#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/fingerprint_bridge.py - real-browser channels when alignment is not enough.

Analysis item #14, second half. If assess_gate() says ``use_browser_channel``,
no amount of header reordering will help: the TLS handshake / JS challenge /
device binding lives inside the browser. Two bridges, both zero-dependency at
import time:

  * CDPReplay  - a minimal RFC6455 WebSocket client (stdlib socket only) that
                 evaluates ``fetch()`` inside an already logged-in Chrome tab.
                 Cookies, UA, JA3 and any JS-issued token stay browser-native.
  * FingerprintInjection - a mitmproxy addon (duck-typed, mitmproxy imported
                 only by mitmproxy itself) that restores captured header order
                 and rewrites course params inside a live browser session.

Hard rules:
  * read-only by default; every non-GET/HEAD/OPTIONS call needs allow_write.
  * signed mobile requests (xqerSign / encrptSecretKey) are NEVER rewritten at
    the egress: changing kcid/skbj there invalidates the signature. Hook the
    parameter-construction point in the app instead; this addon only flags it.
  * no credentials are read from disk; the browser session supplies them.

Usage (Chrome started with --remote-debugging-port=9222
       --remote-allow-origins=*, and already logged in):
  python fingerprint_bridge.py --cdp http://127.0.0.1:9222 \
      --url-contains /hsjw//wap/ --url https://TARGET_HOST/hsjw//wap/... --probe
  python fingerprint_bridge.py --selftest
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

try:                                    # package import and direct run
    from .fingerprint import (READ_ONLY_METHODS, SENSITIVE_HEADERS,
                              SENSITIVE_PARAMS, Fingerprint, is_redacted)
except ImportError:                     # pragma: no cover - direct script mode
    from fingerprint import (READ_ONLY_METHODS, SENSITIVE_HEADERS,
                             SENSITIVE_PARAMS, Fingerprint, is_redacted)


# ---------------------------------------------------------------------------
# minimal RFC6455 client (no websockets / websocket-client dependency)
# ---------------------------------------------------------------------------

def encode_frame(payload: bytes, opcode: int = 1, mask: bool = True,
                 key: Optional[bytes] = None) -> bytes:
    """Encode one unfragmented WebSocket frame. Client->server MUST mask."""
    first = 0x80 | (opcode & 0x0F)
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", first, (0x80 if mask else 0x00) | n)
    elif n < 65536:
        head = struct.pack("!BBH", first, (0x80 if mask else 0x00) | 126, n)
    else:
        head = struct.pack("!BBQ", first, (0x80 if mask else 0x00) | 127, n)
    if not mask:
        return head + payload
    k = key or os.urandom(4)
    if len(k) != 4:
        raise ValueError("mask key must be 4 bytes")
    masked = bytes(b ^ k[i % 4] for i, b in enumerate(payload))
    return head + k + masked


def decode_frame(data: bytes) -> Tuple[int, bytes, int]:
    """Return (opcode, payload, bytes_consumed) for one frame."""
    if len(data) < 2:
        raise ValueError("short frame header")
    b1, b2 = data[0], data[1]
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    n = b2 & 0x7F
    off = 2
    if n == 126:
        if len(data) < off + 2:
            raise ValueError("short 16-bit length")
        n = struct.unpack("!H", data[off:off + 2])[0]
        off += 2
    elif n == 127:
        if len(data) < off + 8:
            raise ValueError("short 64-bit length")
        n = struct.unpack("!Q", data[off:off + 8])[0]
        off += 8
    key = b""
    if masked:
        if len(data) < off + 4:
            raise ValueError("short mask key")
        key = data[off:off + 4]
        off += 4
    if len(data) < off + n:
        raise ValueError(f"short payload: need {off + n}, have {len(data)}")
    payload = data[off:off + n]
    if key:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return opcode, payload, off + n


class MiniWS:
    """Tiny synchronous WebSocket client with CDP-friendly event skipping."""

    def __init__(self, url: str, timeout: float = 10.0,
                 origin: str = "http://localhost"):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("ws", "wss"):
            raise ValueError("expected ws:// or wss:// URL")
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or (443 if u.scheme == "wss" else 80)
        self.timeout = timeout
        sock = socket.create_connection((self.host, self.port), timeout)
        if u.scheme == "wss":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=self.host)
        self.sock = sock
        self.sock.settimeout(timeout)
        self._buf = b""
        self._id = 0
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        host_hdr = self.host if self.port in (80, 443) else f"{self.host}:{self.port}"
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_hdr}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Origin: {origin}\r\n\r\n"
        ).encode("ascii")
        sock.sendall(handshake)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake closed")
            head += chunk
        first, _, rest = head.partition(b"\r\n\r\n")
        if b" 101 " not in first.split(b"\r\n", 1)[0]:
            raise ConnectionError("WebSocket upgrade rejected: "
                                  + first.split(b"\r\n", 1)[0].decode("ascii", "replace"))
        self._buf = rest

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("WebSocket closed while reading")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _recv_frame(self) -> Tuple[int, bytes]:
        b1, b2 = self._read_exact(2)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read_exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read_exact(8))[0]
        key = self._read_exact(4) if masked else b""
        payload = self._read_exact(n)
        if key:
            payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def send(self, payload: bytes, opcode: int = 1) -> None:
        self.sock.sendall(encode_frame(payload, opcode))

    def call(self, method: str, params: Optional[dict] = None,
             id_only: bool = True) -> dict:
        self._id += 1
        mid = self._id
        self.send(json.dumps({"id": mid, "method": method,
                             "params": params or {}}).encode("utf-8"))
        while True:
            opcode, data = self._recv_frame()
            if opcode == 0x9:                              # ping -> pong
                self.send(data, 0xA)
                continue
            if opcode == 0x8:
                raise ConnectionError("WebSocket closed by peer")
            if opcode != 0x1:
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if id_only and msg.get("id") != mid:
                continue                                   # skip CDP events
            return msg

    def close(self) -> None:
        try:
            self.send(b"", 0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CDP channel
# ---------------------------------------------------------------------------

def list_cdp_targets(cdp_http: str = "http://127.0.0.1:9222",
                     timeout: float = 5.0) -> List[dict]:
    url = cdp_http.rstrip("/") + "/json/list"
    req = urllib.request.Request(url, headers={"User-Agent": "kingo-ops/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_target(targets: List[dict], url_contains: str = "") -> dict:
    pages = [t for t in targets if t.get("type") == "page"
             and t.get("webSocketDebuggerUrl")]
    if not pages:
        raise RuntimeError("no attachable CDP page target")
    if url_contains:
        for t in pages:
            if url_contains in str(t.get("url", "")):
                return t
        raise RuntimeError(f"no page URL contains {url_contains!r}: "
                           + ", ".join(str(t.get("url"))[:80] for t in pages[:5]))
    return pages[0]


def build_fetch_expression(url: str, method: str = "GET",
                           headers: Optional[dict] = None,
                           body: Optional[str] = None,
                           preview_bytes: int = 512) -> str:
    """JS evaluated in the page context. All user data is JSON-escaped."""
    opts: Dict[str, Any] = {"method": method.upper(),
                            "headers": headers or {},
                            "credentials": "include"}
    if body is not None:
        opts["body"] = body
    return (
        "(async () => { const r = await fetch(%s, %s); "
        "const t = await r.text(); "
        "return {status: r.status, "
        "headers: Object.fromEntries(r.headers.entries()), "
        "body: t.slice(0, %d), length: t.length}; })()"
    ) % (json.dumps(url, ensure_ascii=False),
         json.dumps(opts, ensure_ascii=False), int(preview_bytes))


class CDPReplay:
    """Send a request from inside a logged-in browser tab, keeping its TLS/JA3."""

    def __init__(self, ws_url: str, allow_write: bool = False,
                 timeout: float = 10.0):
        self.ws = MiniWS(ws_url, timeout)
        self.allow_write = allow_write
        self.timeout = timeout

    @classmethod
    def from_cdp(cls, cdp_http: str = "http://127.0.0.1:9222",
                 url_contains: str = "", allow_write: bool = False,
                 timeout: float = 10.0) -> "CDPReplay":
        t = pick_target(list_cdp_targets(cdp_http, timeout), url_contains)
        return cls(t["webSocketDebuggerUrl"], allow_write, timeout)

    def fetch(self, url: str, method: str = "GET",
              headers: Optional[dict] = None, body: Optional[str] = None,
              preview_bytes: int = 512) -> dict:
        m = method.upper()
        if m not in READ_ONLY_METHODS and not self.allow_write:
            raise PermissionError(
                f"{m} blocked: set allow_write=True explicitly (ops/ is read-only)")
        expr = build_fetch_expression(url, m, headers, body, preview_bytes)
        msg = self.ws.call("Runtime.evaluate", {
            "expression": expr,
            "awaitPromise": True,
            "returnByValue": True,
            "userGesture": True,
        })
        if "error" in msg:
            raise RuntimeError("CDP error: " + json.dumps(msg["error"])[:300])
        res = msg.get("result", {})
        if res.get("exceptionDetails"):
            raise RuntimeError("page exception: "
                               + json.dumps(res["exceptionDetails"])[:300])
        value = res.get("result", {}).get("value")
        return value if isinstance(value, dict) else {"raw": value}

    def close(self) -> None:
        self.ws.close()


# ---------------------------------------------------------------------------
# mitmproxy addon (duck-typed; mitmproxy is never imported here)
# ---------------------------------------------------------------------------

class FingerprintInjection:
    """Restore captured header order and rewrite course params in a live flow.

    mitmproxy calls ``request(flow)`` with its own HTTPFlow. We use attributes,
    not isinstance checks, so this module can be imported and unit-tested with
    plain fake objects and without mitmproxy installed.
    """

    def __init__(self, baseline_path: str = "", url_contains: str = "",
                 overrides: Optional[dict] = None, allow_write: bool = False,
                 header_order: bool = True):
        self.url_contains = url_contains
        self.overrides = dict(overrides or {})
        self.allow_write = allow_write
        self.header_order = header_order
        self.baseline: Optional[Fingerprint] = None
        self.log: List[str] = []
        if baseline_path and os.path.isfile(baseline_path):
            with open(baseline_path, "r", encoding="utf-8-sig") as fh:
                self.baseline = Fingerprint.from_dict(json.load(fh))

    def load(self, loader: Any) -> None:                # mitmproxy hook, optional
        self.log.append("addon loaded: read_only=%s" % (not self.allow_write))

    def _set_param(self, req: Any, key: str, value: str) -> bool:
        q = getattr(req, "query", None)
        if q is not None and hasattr(q, "__setitem__"):
            q[key] = value
            return True
        form = getattr(req, "urlencoded_form", None)
        if form is not None and key in form and hasattr(form, "__setitem__"):
            form[key] = value
            return True
        return False

    def request(self, flow: Any) -> None:
        req = getattr(flow, "request", None)
        if req is None:
            return
        url = str(getattr(req, "pretty_url", "") or getattr(req, "url", ""))
        if self.url_contains and self.url_contains not in url:
            return
        method = str(getattr(req, "method", "GET")).upper()
        if method not in READ_ONLY_METHODS and not self.allow_write:
            self.log.append("blocked write " + method + " path_sha256="
                        + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12])
            if hasattr(flow, "kill"):
                flow.kill()
            return

        # Signed mobile POSTs must not be rewritten at the egress.
        q = getattr(req, "query", None)
        param_names = set()
        if q is not None and hasattr(q, "keys"):
            param_names |= {str(k).lower() for k in q.keys()}
        form = getattr(req, "urlencoded_form", None)
        if form is not None and hasattr(form, "keys"):
            param_names |= {str(k).lower() for k in form.keys()}
        signed = param_names & SENSITIVE_PARAMS
        if signed and self.overrides:
            self.log.append("signed request detected "
                            + ",".join(sorted(signed))
                            + ": skip egress rewrite, hook the app's "
                              "parameter-construction point instead")
            return

        if self.header_order and self.baseline is not None:
            hdrs = getattr(req, "headers", None)
            if hdrs is not None and hasattr(hdrs, "clear") and hasattr(hdrs, "items"):
                live = {str(k).lower(): str(v) for k, v in hdrs.items()}
                hdrs.clear()
                for k, v in self.baseline.headers:
                    low = k.lower()
                    if low in SENSITIVE_HEADERS and (is_redacted(v) or low in live):
                        if low not in live:
                            continue                    # keep live session value
                        hdrs[k] = live[low]
                    else:
                        hdrs[k] = live.get(low, v)
                self.log.append("restored header order "
                                + str(len(self.baseline.headers)) + " entries")
        for k, v in self.overrides.items():
            if not self._set_param(req, k, str(v)):
                self.log.append(f"param {k} not present in query/form; skipped")


addons = [FingerprintInjection(
    baseline_path=os.environ.get("KINGO_FP_BASELINE", ""),
    url_contains=os.environ.get("KINGO_FP_URL_CONTAINS", ""),
    allow_write=False,
)]


def selftest() -> dict:
    frame = encode_frame(b'{"a":1}', key=b"abcd")
    op, payload, used = decode_frame(frame)
    expr = build_fetch_expression(
        "https://TARGET_HOST/hsjw//wap/mycourseschedule.action?xnxq=2026-2027-1")
    return {
        "websocket_frame_roundtrip": payload == b'{"a":1}' and op == 1 and used == len(frame),
        "cdp_fetch_expression": expr,
        "mitmproxy_installed": _mitmproxy_available(),
        "read_only_default": addons[0].allow_write is False,
        "notes": [
            "Chrome needs --remote-debugging-port=9222 --remote-allow-origins=*",
            "the tab must already be logged in; no credentials are read from disk",
            "signed xqerSign POSTs are never rewritten at the egress",
        ],
    }


def _mitmproxy_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("mitmproxy") is not None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Real-browser fingerprint bridge")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--url-contains", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--method", default="GET")
    ap.add_argument("--body", default="")
    ap.add_argument("--allow-write", action="store_true")
    ap.add_argument("--preview", type=int, default=512)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest or not args.url:
        print(json.dumps(selftest(), ensure_ascii=False, indent=2))
        return 0
    rep = CDPReplay.from_cdp(args.cdp, args.url_contains, args.allow_write)
    try:
        out = rep.fetch(args.url, args.method, body=args.body or None,
                        preview_bytes=args.preview)
    finally:
        rep.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
