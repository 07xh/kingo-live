#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/fingerprint.py - browser/device fingerprint baselines and exact replay.

Analysis item #14: a bare ``http.client`` caller is rejected outright when the
school gateway applies a JS challenge, device-fingerprint or TLS-fingerprint
check. Every other timing/channel optimisation is worth zero in that case, so
this module makes the gate measurable before anything is tuned.

What it does (standard library only, Python 3.10+):

  1. baseline   Parse a real browser HAR into a redacted Fingerprint: ordered
               headers (name AND casing AND order), cookie-name order, ALPN
               hint, param names. Secret values never leave this process.
  2. ja3        Parse a TLS ClientHello (hex or raw bytes) into a JA3 string
               and MD5, with GREASE filtering, so a real capture can be
               compared against what Python's ssl stack emits.
  3. replay     ``AlignedClient`` emits request headers in the exact baseline
               order using ``putrequest(skip_host, skip_accept_encoding)``.
               Cookie comes only from an environment variable.
  4. compare    Ordered header diff -> critical vs cosmetic differences, plus
               an explicit gate verdict from observed browser/raw statuses.
  5. bridge     See fingerprint_bridge.py: drive the real browser over CDP or
               inject inside a mitmproxy session when the gate is present.

Safety invariants (same as the rest of ops/):
  * default read-only; any non-GET/HEAD/OPTIONS method requires allow_write.
  * no real submission endpoint is guessed or contacted.
  * reports contain no cookie/token/signature values, only names and digests.

Usage:
  python fingerprint.py --har capture.har --url-contains /hsjw// --out fp.json
  python fingerprint.py --baseline fp.json --host jwmis.ncwu.edu.cn \
      --path /hsjw//wap/mycourseschedule.action --probe --json report.json
  python fingerprint.py --ja3-hex 16030300...   # parse a captured ClientHello
  python fingerprint.py --demo
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import http.client
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

SENSITIVE_HEADERS = frozenset({
    "cookie", "set-cookie", "authorization", "proxy-authorization",
    "x-csrf-token", "x-xsrf-token", "x-api-key", "x-auth-token",
    "token", "xqersign", "encrptsecretkey",
})
SENSITIVE_PARAMS = frozenset({
    "token", "xqersign", "encrptsecretkey", "param", "param2",
    "password", "j_password", "verifycode", "captcha",
})
BROWSER_HEADERS = (
    "user-agent", "accept", "accept-language", "accept-encoding",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
    "origin", "referer", "upgrade-insecure-requests",
)
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
BLOCK_STATUSES = frozenset({0, 400, 403, 406, 412, 418, 429, 451, 503})

DEFAULT_BASELINE_HEADERS: List[Tuple[str, str]] = [
    ("Host", "TARGET_HOST"),
    ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    ("Accept", "application/json, text/javascript, */*; q=0.01"),
    ("Accept-Language", "zh-CN,zh;q=0.9"),
    ("Accept-Encoding", "gzip, deflate, br"),
    ("Sec-Fetch-Dest", "empty"),
    ("Sec-Fetch-Mode", "cors"),
    ("Sec-Fetch-Site", "same-origin"),
    ("X-Requested-With", "XMLHttpRequest"),
    ("Connection", "keep-alive"),
]


# ---------------------------------------------------------------------------
# redaction and data model
# ---------------------------------------------------------------------------

def redact(value: str) -> str:
    """One-way marker: length + 8 hex chars, enough to detect value changes."""
    raw = (value or "").encode("utf-8", "replace")
    return "<redacted len=%d sha256=%s>" % (
        len(raw), hashlib.sha256(raw).hexdigest()[:8])


def is_redacted(value: str) -> bool:
    return value.startswith("<redacted len=")


@dataclass
class Fingerprint:
    name: str = "baseline"
    method: str = "GET"
    url: str = ""
    path: str = ""
    http_version: str = "HTTP/1.1"
    headers: List[Tuple[str, str]] = field(default_factory=list)
    cookie_names: List[str] = field(default_factory=list)
    param_names: List[str] = field(default_factory=list)
    sensitive_params: List[str] = field(default_factory=list)
    alpn: List[str] = field(default_factory=lambda: ["http/1.1"])
    ja3: str = ""
    ja3_hash: str = ""
    source: str = "manual"
    redacted: bool = True
    notes: List[str] = field(default_factory=list)

    def header_names(self) -> List[str]:
        return [k.lower() for k, _ in self.headers]

    def header_value(self, name: str) -> str:
        low = name.lower()
        for k, v in self.headers:
            if k.lower() == low:
                return v
        return ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(d: dict) -> "Fingerprint":
        return Fingerprint(
            name=str(d.get("name", "baseline")),
            method=str(d.get("method", "GET")),
            url=str(d.get("url", "")),
            path=str(d.get("path", "")),
            http_version=str(d.get("http_version", "HTTP/1.1")),
            headers=[(str(k), str(v)) for k, v in d.get("headers", [])],
            cookie_names=[str(x) for x in d.get("cookie_names", [])],
            param_names=[str(x) for x in d.get("param_names", [])],
            sensitive_params=[str(x) for x in d.get("sensitive_params", [])],
            alpn=[str(x) for x in d.get("alpn", ["http/1.1"])],
            ja3=str(d.get("ja3", "")),
            ja3_hash=str(d.get("ja3_hash", "")),
            source=str(d.get("source", "dict")),
            redacted=bool(d.get("redacted", True)),
            notes=[str(x) for x in d.get("notes", [])],
        )


@dataclass
class TlsProfile:
    ciphers: List[str] = field(default_factory=list)
    alpn: List[str] = field(default_factory=lambda: ["h2", "http/1.1"])
    min_version: str = "TLSv1_2"
    max_version: str = ""
    session_tickets: bool = True
    session_reuse: bool = True
    verify: bool = True


# ---------------------------------------------------------------------------
# HAR -> baseline
# ---------------------------------------------------------------------------

def load_har(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def har_entries(har: dict, url_contains: str = "", method: str = "") -> List[dict]:
    out = []
    for e in har.get("log", {}).get("entries", []):
        req = e.get("request", {}) or {}
        url = str(req.get("url", ""))
        if url_contains and url_contains not in url:
            continue
        if method and str(req.get("method", "")).upper() != method.upper():
            continue
        out.append(e)
    return out


def fingerprint_from_har(path_or_har: Any, url_contains: str = "",
                         index: int = 0, name: str = "har") -> Fingerprint:
    har = load_har(path_or_har) if isinstance(path_or_har, str) else path_or_har
    entries = har_entries(har, url_contains)
    if not entries:
        raise ValueError(f"no HAR entry matches url_contains={url_contains!r}")
    if index >= len(entries):
        raise IndexError(f"HAR index {index} out of range ({len(entries)} entries)")
    e = entries[index]
    req = e.get("request", {}) or {}
    resp = e.get("response", {}) or {}
    url = str(req.get("url", ""))
    u = urllib.parse.urlsplit(url)
    # The URL itself may carry tokens/signatures; scrub those values too.
    safe_query = urllib.parse.urlencode([
        (k, "<redacted>" if k.lower() in SENSITIVE_PARAMS else v)
        for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    ])
    safe_url = urllib.parse.urlunsplit(
        (u.scheme, u.netloc, u.path, safe_query, u.fragment))
    path = u.path + (("?" + safe_query) if safe_query else "")

    headers: List[Tuple[str, str]] = []
    cookie_names: List[str] = []
    pseudo: List[str] = []
    for h in req.get("headers", []) or []:
        k, v = str(h.get("name", "")), str(h.get("value", ""))
        if not k:
            continue
        low = k.lower()
        if low.startswith(":"):          # HTTP/2 pseudo-header, not replayable
            pseudo.append(k)
            continue
        if low == "cookie":
            cookie_names = [p.split("=", 1)[0].strip()
                            for p in v.split(";") if p.strip()]
        headers.append((k, redact(v) if low in SENSITIVE_HEADERS else v))

    param_names: List[str] = []
    sensitive_params: List[str] = []
    for p in req.get("queryString", []) or []:
        n = str(p.get("name", ""))
        if not n:
            continue
        param_names.append(n)
        if n.lower() in SENSITIVE_PARAMS:
            sensitive_params.append(n)
    for p in ((req.get("postData", {}) or {}).get("params", []) or []):
        n = str(p.get("name", ""))
        if n and n not in param_names:
            param_names.append(n)
            if n.lower() in SENSITIVE_PARAMS:
                sensitive_params.append(n)

    http_version = str(resp.get("httpVersion", "") or req.get("httpVersion", "")
                       or "HTTP/1.1")
    alpn = ["h2", "http/1.1"] if http_version.upper() in (
        "HTTP/2.0", "HTTP/2", "H2", "H2C") else ["http/1.1"]
    notes = ["ALPN inferred from HAR response.httpVersion; not a TLS capture"]
    if pseudo:
        notes.append("HTTP/2 pseudo-headers stripped for HTTP/1.1 replay: "
                     + ",".join(pseudo))
    if not any(k.lower() == "host" for k, _ in headers):
        headers.insert(0, ("Host", u.netloc))
        notes.append("Host header absent in HAR; synthesised from request URL")
    if not cookie_names:
        for c in req.get("cookies", []) or []:
            n = str(c.get("name", ""))
            if n:
                cookie_names.append(n)
    return Fingerprint(
        name=name, method=str(req.get("method", "GET")).upper(),
        url=safe_url, path=path,
        http_version=http_version, headers=headers,
        cookie_names=cookie_names, param_names=param_names,
        sensitive_params=sensitive_params, alpn=alpn,
        source="har", redacted=True, notes=notes,
    )


def fingerprint_from_echo(echo: dict, name: str = "observed") -> Fingerprint:
    """Build a fingerprint from a loopback target's request echo."""
    rows = echo.get("headers", []) or []
    headers = [(str(k), redact(str(v)) if str(k).lower() in SENSITIVE_HEADERS
                else str(v)) for k, v in rows]
    cookie_names = []
    for k, v in rows:
        if str(k).lower() == "cookie":
            cookie_names = [p.split("=", 1)[0].strip()
                            for p in str(v).split(";") if p.strip()]
    return Fingerprint(
        name=name, method=str(echo.get("method", "GET")).upper(),
        path=str(echo.get("path", "")),
        http_version=str(echo.get("http_version", "HTTP/1.1")),
        headers=headers, cookie_names=cookie_names,
        alpn=[], source="echo", redacted=True,
    )


# ---------------------------------------------------------------------------
# TLS ClientHello -> JA3
# ---------------------------------------------------------------------------

def _is_grease(v: int) -> bool:
    return (v & 0x0F0F) == 0x0A0A and ((v >> 8) & 0xFF) == (v & 0xFF)


def parse_client_hello(data: Any) -> dict:
    """Parse one TLS ClientHello record (hex string or bytes) into JA3 fields.

    GREASE values are dropped, matching the JA3 convention. TLS 1.3 ciphers
    are present in the handshake but are not configurable via set_ciphers().
    """
    if isinstance(data, str):
        data = bytes.fromhex("".join(data.split()))
    raw = bytes(data)
    if not raw:
        raise ValueError("empty ClientHello")
    if raw[0] == 0x16:                                   # TLS record layer
        rec_len = struct.unpack("!H", raw[3:5])[0]
        raw = raw[5:5 + rec_len]
    if not raw or raw[0] != 0x01:
        raise ValueError("not a ClientHello handshake message")
    hs_len = int.from_bytes(raw[1:4], "big")
    b = raw[4:4 + hs_len]
    if len(b) < 38:
        raise ValueError("ClientHello too short")

    version = struct.unpack("!H", b[0:2])[0]
    off = 2 + 32                                          # version + random
    sid_len = b[off]
    off += 1 + sid_len
    cs_len = struct.unpack("!H", b[off:off + 2])[0]
    off += 2
    ciphers = [struct.unpack("!H", b[off + i * 2:off + i * 2 + 2])[0]
               for i in range(cs_len // 2)]
    off += cs_len
    comp_len = b[off]
    off += 1 + comp_len

    ext_all: List[int] = []
    ext_types: List[int] = []
    curves: List[int] = []
    points: List[int] = []
    sni: List[str] = []
    if off + 2 <= len(b):
        ext_total = struct.unpack("!H", b[off:off + 2])[0]
        off += 2
        end = min(off + ext_total, len(b))
        while off + 4 <= end:
            etype, elen = struct.unpack("!HH", b[off:off + 4])
            edata = b[off + 4:off + 4 + elen]
            off += 4 + elen
            ext_all.append(etype)
            if not _is_grease(etype):
                ext_types.append(etype)
            if etype == 0x0000 and len(edata) > 2:       # server_name
                p = 2
                while p + 3 <= len(edata):
                    nlen = struct.unpack("!H", edata[p + 1:p + 3])[0]
                    sni.append(edata[p + 3:p + 3 + nlen].decode("utf-8", "replace"))
                    p += 3 + nlen
            elif etype == 0x000A and len(edata) >= 2:    # supported_groups
                for i in range(2, min(len(edata), 2 + struct.unpack(
                        "!H", edata[0:2])[0]) - 1, 2):
                    g = struct.unpack("!H", edata[i:i + 2])[0]
                    if not _is_grease(g):
                        curves.append(g)
            elif etype == 0x000B and edata:               # ec_point_formats
                for v in edata[1:1 + edata[0]]:
                    points.append(v)

    keep_ciphers = [c for c in ciphers if not _is_grease(c)]
    ja3 = ",".join([
        str(version),
        "-".join(str(c) for c in keep_ciphers),
        "-".join(str(e) for e in ext_types),
        "-".join(str(g) for g in curves),
        "-".join(str(p) for p in points),
    ])
    return {
        "ssl_version": version,
        "session_id_len": sid_len,
        "ciphers": keep_ciphers,
        "ciphers_raw": ciphers,
        "extensions": ext_types,
        "curves": curves,
        "point_formats": points,
        "server_name": sni,
        "grease_present": (len(ciphers) != len(keep_ciphers)
                           or any(_is_grease(e) for e in ext_all)),
        "ja3": ja3,
        "ja3_hash": hashlib.md5(ja3.encode("ascii")).hexdigest(),
    }


def build_ssl_context(profile: TlsProfile) -> ssl.SSLContext:
    """Apply the observable parts of a TLS profile to a stdlib SSLContext."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if profile.verify:
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_default_certs()
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if profile.min_version:
        ctx.minimum_version = getattr(ssl.TLSVersion, profile.min_version,
                                      ssl.TLSVersion.TLSv1_2)
    if profile.max_version:
        ctx.maximum_version = getattr(ssl.TLSVersion, profile.max_version,
                                      ssl.TLSVersion.TLSv1_3)
    if profile.ciphers:
        ctx.set_ciphers(":".join(profile.ciphers))
    if profile.alpn:
        ctx.set_alpn_protocols(profile.alpn)
    if not profile.session_tickets:
        ctx.options |= ssl.OP_NO_TICKET
    return ctx


class SessionHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that reuses the negotiated TLS session across connects.

    Python's stdlib does not pass ``session=`` to wrap_socket, so we override
    connect(). TLS 1.3 tickets may arrive after the handshake; a second connect
    reuses whatever the socket exposed. Requires a real TLS peer to validate.
    """

    def __init__(self, *args: Any, session: Any = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.session = session

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self.host, self.port), self.timeout,
            getattr(self, "source_address", None))
        if getattr(self, "_tunnel_host", None):
            self._tunnel()
        ctx = getattr(self, "_context", None) or getattr(self, "context", None)
        hostname = self.host if getattr(ctx, "check_hostname", False) else None
        self.sock = ctx.wrap_socket(self.sock, server_hostname=hostname,
                                    session=self.session)
        self.session = getattr(self.sock, "session", None)


# ---------------------------------------------------------------------------
# exact ordered-header replay
# ---------------------------------------------------------------------------

@dataclass
class SentRequest:
    method: str = ""
    path: str = ""
    status: int = 0
    headers_sent: List[List[str]] = field(default_factory=list)
    response_headers: Dict[str, str] = field(default_factory=dict)
    size: int = 0
    sha256: str = ""
    ttfb_ms: float = 0.0
    reused: bool = False
    alpn: str = ""
    error: str = ""
    echo: Dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict:
        d = asdict(self)
        d.pop("echo", None)
        return d


class AlignedClient:
    """Emit a request whose header order/casing matches a captured baseline.

    ``putrequest(skip_host=True, skip_accept_encoding=True)`` is the key: the
    stdlib normally injects Host and ``Accept-Encoding: identity`` itself, both
    of which are loud fingerprint differences against a real browser.
    """

    def __init__(self, host: str, port: int = 443, scheme: str = "https",
                 baseline: Optional[Fingerprint] = None,
                 tls_profile: Optional[TlsProfile] = None,
                 timeout: float = 3.0, cookie_env: str = "KINGO_COOKIE",
                 cookie: str = "", bind_ip: str = "",
                 allow_write: bool = False):
        self.host, self.port, self.scheme = host, port, scheme
        self.baseline = baseline
        self.tls_profile = tls_profile or TlsProfile()
        self.timeout = timeout
        self.cookie = cookie or (os.environ.get(cookie_env, "") if cookie_env else "")
        self.bind_ip = bind_ip
        self.allow_write = allow_write
        self._conn: Any = None
        self._tls_session: Any = None
        self._alpn = ""

    def _open(self) -> Any:
        if self._conn is not None:
            return self._conn
        kw: Dict[str, Any] = {"host": self.host, "port": self.port,
                              "timeout": self.timeout}
        if self.scheme == "https":
            ctx = build_ssl_context(self.tls_profile)
            conn = SessionHTTPSConnection(context=ctx,
                                          session=self._tls_session, **kw)
        else:
            conn = http.client.HTTPConnection(**kw)
        if self.bind_ip:
            conn.source_address = (self.bind_ip, 0)
        conn.connect()
        sock = getattr(conn, "sock", None)
        self._alpn = (getattr(sock, "selected_alpn_protocol", lambda: None)()
                      or "")
        self._tls_session = getattr(conn, "session", None)
        self._conn = conn
        return conn

    def _ordered_headers(self, extra: Optional[Dict[str, str]]) -> List[Tuple[str, str]]:
        rows = list(self.baseline.headers) if self.baseline else list(
            DEFAULT_BASELINE_HEADERS)
        if not any(k.lower() == "host" for k, _ in rows):
            host = self.host if self.port in (80, 443) else f"{self.host}:{self.port}"
            rows.insert(0, ("Host", host))
        out: List[Tuple[str, str]] = []
        for k, v in rows:
            low = k.lower()
            if low == "cookie":
                if is_redacted(v) or v in ("TARGET_COOKIE", "<cookie>"):
                    if not self.cookie:
                        continue                       # never send a placeholder
                    v = self.cookie
                elif self.cookie:
                    v = self.cookie
            if k == "Host" and v == "TARGET_HOST":
                v = self.host if self.port in (80, 443) else f"{self.host}:{self.port}"
            out.append((k, v))
        for k, v in (extra or {}).items():
            low = k.lower()
            hit = next((i for i, (ek, _) in enumerate(out) if ek.lower() == low), -1)
            if hit >= 0:
                out[hit] = (out[hit][0], v)            # keep baseline position
            else:
                out.append((k, v))
        return out

    def request(self, method: str, path: str, body: Any = None,
                extra_headers: Optional[Dict[str, str]] = None) -> SentRequest:
        m = method.upper()
        if m not in READ_ONLY_METHODS and not self.allow_write:
            raise PermissionError(
                f"{m} blocked: set allow_write=True explicitly (ops/ is read-only)")
        headers = self._ordered_headers(extra_headers)
        conn = self._open()
        reused = getattr(conn, "_fp_used", False)
        t0 = time.perf_counter()
        try:
            conn.putrequest(m, path, skip_host=True, skip_accept_encoding=True)
            for k, v in headers:
                conn.putheader(k, v)
            raw = None
            if body is not None:
                raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
            conn.endheaders(raw)
            resp = conn.getresponse()
            ttfb = (time.perf_counter() - t0) * 1000.0
            data = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            self._conn = None
            return SentRequest(method=m, path=path, error=str(exc),
                               headers_sent=self._public_headers(headers),
                               alpn=self._alpn, reused=reused)
        conn._fp_used = True
        try:
            parsed = json.loads(data.decode("utf-8", "replace"))
            echo = parsed if isinstance(parsed, dict) else {}
        except (ValueError, UnicodeDecodeError):
            echo = {}
        return SentRequest(
            method=m, path=path, status=resp.status,
            headers_sent=self._public_headers(headers),
            response_headers={k.lower(): v for k, v in resp.getheaders()},
            size=len(data), sha256=hashlib.sha256(data).hexdigest()[:16],
            ttfb_ms=round(ttfb, 3), reused=reused, alpn=self._alpn,
            echo=echo,
        )

    @staticmethod
    def _public_headers(rows: Sequence[Tuple[str, str]]) -> List[List[str]]:
        out = []
        for k, v in rows:
            if k.lower() in SENSITIVE_HEADERS:
                v = "<injected>" if v and not is_redacted(v) else "<redacted>"
            out.append([k, v])
        return out

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None


# ---------------------------------------------------------------------------
# diff and gate verdict
# ---------------------------------------------------------------------------

def compare(baseline: Fingerprint, observed: Fingerprint,
            order_sensitive: bool = True) -> dict:
    """Ordered header diff. Secret values are compared by presence only."""
    bn, on = baseline.header_names(), observed.header_names()
    bmap = {k.lower(): v for k, v in baseline.headers}
    omap = {k.lower(): v for k, v in observed.headers}
    missing = [k for k in bn if k not in omap]
    extra = [k for k in on if k not in bmap]
    value_mismatch = []
    for k in bmap:
        if k in SENSITIVE_HEADERS or k not in omap:
            continue
        if bmap[k] != omap[k] and not is_redacted(bmap[k]) and not is_redacted(omap[k]):
            value_mismatch.append(k)
    order_match = bn == on
    cookie_match = baseline.cookie_names == observed.cookie_names
    alpn_match = (not baseline.alpn or not observed.alpn or
                  baseline.alpn[0] == observed.alpn[0])
    ja3_match = (not baseline.ja3_hash or not observed.ja3_hash or
                 baseline.ja3_hash == observed.ja3_hash)

    critical: List[str] = []
    cosmetic: List[str] = []
    if order_sensitive and not order_match:
        critical.append("header_order")
    for k in missing:
        (critical if k in BROWSER_HEADERS or k in ("host", "cookie",
                                                  "content-type", "x-requested-with")
         else cosmetic).append("missing:" + k)
    for k in extra:
        cosmetic.append("extra:" + k)
    for k in value_mismatch:
        (critical if k in BROWSER_HEADERS else cosmetic).append("value:" + k)
    if not cookie_match:
        critical.append("cookie_name_order")
    if not alpn_match:
        critical.append("alpn")
    if not ja3_match:
        critical.append("ja3")
    score = round(max(0.0, 1.0 - 0.20 * len(critical) - 0.05 * len(cosmetic)), 3)
    return {
        "baseline": baseline.name, "observed": observed.name,
        "header_order_match": order_match,
        "baseline_order": bn, "observed_order": on,
        "missing": missing, "extra": extra,
        "value_mismatch": value_mismatch,
        "cookie_order_match": cookie_match,
        "alpn_match": alpn_match, "ja3_match": ja3_match,
        "critical": critical, "cosmetic": cosmetic,
        "alignment_score": score,
    }


def assess_gate(observations: dict, diff: Optional[dict] = None) -> dict:
    """Decide whether a fingerprint/JS gate exists and which channel to use.

    observations keys: raw_status, browser_status, js_challenge,
    tls_challenge, header_order_check, captcha.
    """
    raw = observations.get("raw_status")
    browser = observations.get("browser_status")
    js = bool(observations.get("js_challenge"))
    tls_check = bool(observations.get("tls_challenge"))
    header_check = bool(observations.get("header_order_check"))
    captcha = bool(observations.get("captcha"))
    blocked = raw in BLOCK_STATUSES
    divergent = blocked and browser == 200
    critical = (diff or {}).get("critical", [])
    evidence = []
    if divergent:
        evidence.append(f"raw={raw} browser={browser}: same URL, different verdict")
    if js:
        evidence.append("JS challenge observed in raw response")
    if tls_check:
        evidence.append("TLS fingerprint challenge observed")
    if header_check:
        evidence.append("server rejects reordered headers")
    if captcha:
        evidence.append("captcha present: no purely technical path")

    gate = bool(divergent or js or tls_check or header_check or captcha)
    if captcha:
        verdict, channel = "human_required", "none"
    elif gate and (js or tls_check or divergent):
        verdict, channel = "use_browser_channel", "cdp"
    elif critical:
        verdict, channel = "align_required", "raw"
    elif browser is None and raw is None:
        verdict, channel = "unknown", "raw"
    else:
        verdict, channel = "pass", "raw"
    actions = []
    if channel == "cdp":
        actions = [
            "run fingerprint_bridge.CDPReplay against the logged-in browser tab",
            "keep cookies/UA/TLS inside the browser; do not replay with http.client",
            "optionally route through the mitmproxy addon to rewrite kcid/skbj",
        ]
    elif verdict == "align_required":
        actions = ["fix: " + x for x in critical[:8]]
    elif verdict == "human_required":
        actions = ["captcha cannot be bypassed by alignment; stop automation here"]
    elif verdict == "unknown":
        actions = ["capture raw_status + browser_status for the same read-only URL"]
    return {
        "gate_present": gate,
        "evidence": evidence,
        "verdict": verdict,
        "channel": channel,
        "critical_differences": critical,
        "actions": actions,
        "raw_status": raw, "browser_status": browser,
        "fingerprinting_worth_doing": gate or bool(critical),
    }


def probe_alignment(client: AlignedClient, path: str) -> dict:
    """One read-only GET; if the peer echoes the request, diff against baseline."""
    r = client.request("GET", path)
    out: Dict[str, Any] = {"sent": r.public(), "status": r.status}
    if r.echo.get("headers") and client.baseline is not None:
        observed = fingerprint_from_echo(r.echo)
        diff = compare(client.baseline, observed)
        out["observed"] = observed.to_dict()
        out["diff"] = diff
        out["aligned"] = not diff["critical"]
    else:
        out["aligned"] = None
        out["reason"] = ("peer did not echo request headers; use a loopback "
                         "target or a HAR-captured baseline for the diff")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def demo() -> dict:
    fp = Fingerprint(name="demo", headers=list(DEFAULT_BASELINE_HEADERS))
    echo = {"method": "GET", "path": "/hsjw//wap/mycourseschedule.action",
            "headers": [["Host", "jwmis.ncwu.edu.cn"],
                        ["User-Agent", "kingo-ops-recon/1.0"],
                        ["Accept", "*/*"]]}
    obs = fingerprint_from_echo(echo)
    diff = compare(fp, obs)
    return {
        "baseline": fp.to_dict(),
        "diff": diff,
        "gate_with_challenge": assess_gate(
            {"raw_status": 403, "browser_status": 200, "js_challenge": True}, diff),
        "gate_clean": assess_gate(
            {"raw_status": 200, "browser_status": 200}, compare(fp, fp)),
        "ja3_fields": ["ssl_version", "ciphers", "extensions", "curves",
                       "point_formats", "ja3", "ja3_hash"],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Fingerprint baseline / gate probe")
    ap.add_argument("--har", default="", help="browser HAR to build a baseline")
    ap.add_argument("--url-contains", default="")
    ap.add_argument("--har-index", type=int, default=0)
    ap.add_argument("--baseline", default="", help="existing baseline JSON")
    ap.add_argument("--out", default="", help="write baseline JSON here")
    ap.add_argument("--host", default="")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--scheme", default="https", choices=("http", "https"))
    ap.add_argument("--path", default="/hsjw//wap/mycourseschedule.action")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--bind-ip", default="")
    ap.add_argument("--cookie-env", default="KINGO_COOKIE")
    ap.add_argument("--probe", action="store_true", help="one read-only GET")
    ap.add_argument("--ja3-hex", default="", help="parse a captured ClientHello")
    ap.add_argument("--raw-status", type=int, default=-1)
    ap.add_argument("--browser-status", type=int, default=-1)
    ap.add_argument("--js-challenge", action="store_true")
    ap.add_argument("--tls-challenge", action="store_true")
    ap.add_argument("--json", default="")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args(argv)

    if args.demo:
        print(json.dumps(demo(), ensure_ascii=False, indent=2))
        return 0
    if args.ja3_hex:
        print(json.dumps(parse_client_hello(args.ja3_hex), ensure_ascii=False,
                         indent=2))
        return 0

    report: Dict[str, Any] = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "read_only": True,
    }
    fp: Optional[Fingerprint] = None
    if args.har:
        fp = fingerprint_from_har(args.har, args.url_contains, args.har_index)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(fp.to_json() + "\n")
            print(f"wrote baseline {args.out}")
    elif args.baseline:
        with open(args.baseline, "r", encoding="utf-8-sig") as fh:
            fp = Fingerprint.from_dict(json.load(fh))
    if fp is not None:
        report["baseline"] = fp.to_dict()
        print(f"baseline={fp.name} method={fp.method} path={fp.path} "
              f"headers={len(fp.headers)} cookie_names={len(fp.cookie_names)} "
              f"alpn={fp.alpn} redacted={fp.redacted}")

    if args.probe and args.host:
        host = args.host.split("://")[-1].split("/")[0]
        h, _, p = host.partition(":")
        port = args.port or (int(p) if p else (443 if args.scheme == "https" else 80))
        client = AlignedClient(h, port, args.scheme, fp, timeout=args.timeout,
                               cookie_env=args.cookie_env, bind_ip=args.bind_ip)
        try:
            align = probe_alignment(client, args.path)
        finally:
            client.close()
        report["probe"] = align
        report["cookie_present"] = bool(client.cookie)
        r = align["sent"]
        print(f"probe status={r['status']} ttfb={r['ttfb_ms']}ms "
              f"alpn={r['alpn'] or 'n/a'} headers_sent={len(r['headers_sent'])} "
              f"aligned={align['aligned']}")
        if align.get("diff"):
            print("critical=" + (",".join(align["diff"]["critical"]) or "none"))

    obs = {k: v for k, v in {
        "raw_status": args.raw_status if args.raw_status >= 0 else None,
        "browser_status": (args.browser_status
                           if args.browser_status >= 0 else None),
        "js_challenge": args.js_challenge,
        "tls_challenge": args.tls_challenge,
    }.items()}
    if any(v is not None for v in obs.values()):
        report["gate"] = assess_gate(obs, report.get("probe", {}).get("diff"))
        print(f"gate={report['gate']['verdict']} channel={report['gate']['channel']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
