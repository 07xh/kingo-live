#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/recon.py - read-only reconnaissance for a course-selection backend.

Five independent probes, all GET, no write endpoint is ever contacted:

  1. nodes      DNS A/AAAA records -> possible independent backend nodes.
  2. transport  ALPN / HTTP version, Date & custom clock headers, Server-Timing,
                rate-limit headers, Set-Cookie presence.
  3. clock      N samples, RTT/2 midpoint compensation, best-half median offset,
                RTT spread, and the sub-second phase against a 1s Date header.
  4. variants   path variants (single/double slash, wap/web, .action/.do) ->
                status + size + body digest differences.
  5. window     whether a selection-window marker changes when a term/round
                query parameter is added (client-controlled vs server-only gate).

Standard library only, Python 3.10+. Credentials come from an environment
variable; nothing sensitive is written to the report.

Usage:
  set KINGO_COOKIE=<logged-in Cookie header>
  python recon.py --host jwmis.ncwu.edu.cn \
      --path /hsjw//wap/mycourseschedule.action --samples 9 --json recon.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import http.client
import json
import os
import socket
import ssl
import statistics
import sys
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

DEFAULT_VARIANTS = [
    "/hsjw/wap/mycourseschedule.action",
    "/hsjw//wap/mycourseschedule.action",
    "/hsjw/wap/mycourseschedule.do",
    "/hsjw/mycourseschedule.action",
]
WINDOW_MARKERS = (
    "prohibit", "不是有效的选课时间", "未开放", "尚未开始",
    "not in selection time", "window closed",
)
RATE_HEADERS = ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining",
                "x-rate-limit-limit", "x-ratelimit-reset")


# ---------------------------------------------------------------------------
# clock parsing
# ---------------------------------------------------------------------------

def parse_server_time(value: str, header: str) -> Optional[float]:
    """Epoch seconds from a Date header or a custom millisecond/ISO header."""
    s = (value or "").strip()
    if not s:
        return None
    if header.lower() != "date":
        if s.replace(".", "", 1).isdigit():
            v = float(s)
            return v / 1000.0 if v > 1e11 else v
        try:
            d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=_dt.timezone.utc)
            return d.timestamp()
        except ValueError:
            pass
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def parse_server_timing(value: str) -> Dict[str, float]:
    """'queue;dur=8.5, db;dur=22.1' -> {'queue': 8.5, 'db': 22.1}."""
    out: Dict[str, float] = {}
    for part in (value or "").split(","):
        bits = [b.strip() for b in part.split(";")]
        if not bits or not bits[0]:
            continue
        for b in bits[1:]:
            if b.lower().startswith("dur="):
                try:
                    out[bits[0]] = float(b.split("=", 1)[1])
                except ValueError:
                    pass
    return out


def split_host(value: str) -> tuple[str, int]:
    """'host', 'host:8443' or 'http://host:port' -> (host, port)."""
    v = value.strip()
    if "://" in v:
        u = urllib.parse.urlparse(v)
        return u.hostname or "localhost", u.port or (443 if u.scheme == "https" else 80)
    if v.startswith("[") and "]" in v:          # IPv6 literal
        host, _, rest = v[1:].partition("]")
        return host, int(rest.lstrip(":")) if rest.lstrip(":") else 443
    if ":" in v and not v.count(":") > 1:
        host, _, port = v.rpartition(":")
        return host, int(port)
    return v, 443


def resolve_nodes(host: str, port: int = 443) -> List[str]:
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


# ---------------------------------------------------------------------------
# raw GET transport
# ---------------------------------------------------------------------------

@dataclass
class RawGetResult:
    path: str
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    size: int = 0
    sha256: str = ""
    ttfb_ms: float = 0.0
    total_ms: float = 0.0
    alpn: str = ""
    reused: bool = False
    error: str = ""
    body_preview: str = ""

    def public(self) -> dict:
        d = asdict(self)
        d.pop("body_preview", None)
        d.pop("headers", None)
        return d


class RawGet:
    """Persistent http.client GET with per-request timing. Read-only."""

    def __init__(self, host: str, port: int, scheme: str = "https",
                 timeout: float = 3.0, verify_tls: bool = True,
                 cookie: str = "", extra_headers: Optional[Dict[str, str]] = None,
                 bind_ip: str = ""):
        self.host, self.port, self.scheme = host, port, scheme
        self.timeout, self.verify_tls = timeout, verify_tls
        self.cookie = cookie
        self.extra_headers = extra_headers or {}
        self.bind_ip = bind_ip
        self._conn: Optional[Any] = None
        self._alpn = ""

    def _open(self) -> Any:
        if self._conn is not None:
            return self._conn
        kw: Dict[str, Any] = {"host": self.host, "port": self.port,
                              "timeout": self.timeout}
        if self.scheme == "https":
            ctx = ssl.create_default_context()
            if not self.verify_tls:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            ctx.set_alpn_protocols(["h2", "http/1.1"])
            kw["context"] = ctx
            conn = http.client.HTTPSConnection(**kw)
        else:
            conn = http.client.HTTPConnection(**kw)
        if self.bind_ip:
            conn.source_address = (self.bind_ip, 0)
        conn.connect()
        sock = getattr(conn, "sock", None)
        proto = getattr(sock, "selected_alpn_protocol", lambda: None)()
        self._alpn = proto or ""
        self._conn = conn
        return conn

    def get(self, path: str, preview_bytes: int = 0) -> RawGetResult:
        conn = self._open()
        reused = getattr(conn, "_recon_used", False)
        headers = {"Accept": "*/*", "User-Agent": "kingo-ops-recon/1.0"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        headers.update(self.extra_headers)
        t0 = time.perf_counter()
        t_send = time.time()
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            ttfb = (time.perf_counter() - t0) * 1000.0
            body = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            self._conn = None
            return RawGetResult(path=path, status=0, error=str(exc),
                                alpn=self._alpn, reused=reused)
        t_recv = time.time()
        total = (time.perf_counter() - t0) * 1000.0
        conn._recon_used = True
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        r = RawGetResult(
            path=path, status=resp.status, headers=hdrs,
            size=len(body), sha256=hashlib.sha256(body).hexdigest()[:16],
            ttfb_ms=round(ttfb, 3), total_ms=round(total, 3),
            alpn=self._alpn, reused=reused,
            body_preview=body[:preview_bytes].decode("utf-8", "replace"),
        )
        r.midpoint_epoch = (t_send + t_recv) / 2.0          # type: ignore[attr-defined]
        r.rtt_ms = (t_recv - t_send) * 1000.0               # type: ignore[attr-defined]
        return r

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------

def probe_nodes(host: str, port: int) -> dict:
    try:
        nodes = resolve_nodes(host, port)
    except OSError as exc:
        return {"host": host, "port": port, "nodes": [], "error": str(exc),
                "multi_node": False}
    return {"host": host, "port": port, "nodes": nodes,
            "count": len(nodes), "multi_node": len(nodes) >= 2}


def probe_transport(r: RawGetResult) -> dict:
    clock_headers = {}
    for k, v in r.headers.items():
        if k == "date" or "time" in k or "timestamp" in k:
            clock_headers[k] = v
    rate = {k: v for k, v in r.headers.items() if k in RATE_HEADERS}
    timing = parse_server_timing(r.headers.get("server-timing", ""))
    return {
        "status": r.status, "size": r.size, "sha256": r.sha256,
        "ttfb_ms": r.ttfb_ms, "total_ms": r.total_ms,
        "alpn": r.alpn, "http2": r.alpn == "h2",
        "connection_reused": r.reused,
        "set_cookie": "set-cookie" in r.headers,
        "clock_headers": clock_headers,
        "server_timing": timing,
        "queue_ms": timing.get("queue"),
        "tx_ms": timing.get("db"),
        "rate_headers": rate,
    }


def probe_clock(client: RawGet, path: str, header: str, samples: int,
                interval_ms: float, custom_header: str = "") -> dict:
    hdr = custom_header or header
    rows: List[dict] = []
    for _ in range(max(1, samples)):
        r = client.get(path, preview_bytes=0)
        if r.error or r.status == 0:
            rows.append({"error": r.error or "no_response"})
            continue
        epoch = parse_server_time(r.headers.get(hdr.lower(), ""), hdr)
        if epoch is None:
            rows.append({"error": "missing_or_unparsable_clock_header",
                         "header": hdr, "rtt_ms": round(r.rtt_ms, 3)})
            continue
        mid = r.midpoint_epoch
        offset = (epoch - mid) * 1000.0
        rows.append({
            "server_epoch": round(epoch, 6),
            "rtt_ms": round(r.rtt_ms, 3),
            "offset_ms": round(offset, 3),
            "phase_ms": round(((mid - epoch) % 1.0) * 1000.0, 3),
            "ttfb_ms": r.ttfb_ms,
        })
        if interval_ms > 0:
            time.sleep(interval_ms / 1000.0)
    good = [x for x in rows if "offset_ms" in x]
    if not good:
        return {"header": hdr, "samples": len(rows), "usable": 0,
                "rows": rows, "synced": False,
                "reason": "no usable clock samples"}
    good.sort(key=lambda x: x["rtt_ms"])
    best = good[:max(1, len(good) // 2)]
    offsets = [x["offset_ms"] for x in best]
    phases = [x["phase_ms"] for x in best]
    precision = 1000.0 if hdr.lower() == "date" else 1.0
    return {
        "header": hdr, "samples": len(rows), "usable": len(good),
        "precision_ms": precision,
        "offset_ms": round(statistics.median(offsets), 3),
        "spread_ms": round(max(x["offset_ms"] for x in good)
                           - min(x["offset_ms"] for x in good), 3),
        "rtt_p50_ms": round(statistics.median(x["rtt_ms"] for x in good), 3),
        "second_phase_ms": round(statistics.median(phases), 3),
        "synced": True,
        "best_half": len(best),
        "rows": rows,
    }


def probe_variants(client: RawGet, paths: List[str]) -> dict:
    out = []
    for p in paths:
        r = client.get(p, preview_bytes=256)
        out.append({
            "path": p, "status": r.status, "size": r.size,
            "sha256": r.sha256, "ttfb_ms": r.ttfb_ms,
            "rate_limited": r.status == 429 or "retry-after" in r.headers,
            "error": r.error,
            "preview": r.body_preview[:120],
        })
    ok = [x for x in out if x["status"] == 200]
    sigs = {(x["size"], x["sha256"]) for x in ok}
    return {"paths": out, "ok_count": len(ok),
            "distinct_bodies": len(sigs),
            "divergent": len(sigs) > 1 or len({x["status"] for x in out}) > 1}


def probe_window(client: RawGet, path: str, param_name: str,
                 param_value: str) -> dict:
    sep = "&" if "?" in path else "?"
    with_param = f"{path}{sep}{urllib.parse.urlencode({param_name: param_value})}"
    a = client.get(path, preview_bytes=512)
    b = client.get(with_param, preview_bytes=512)

    def markers(r: RawGetResult) -> List[str]:
        text = (r.body_preview + " " + " ".join(
            f"{k}={v}" for k, v in r.headers.items())).lower()
        return [m for m in WINDOW_MARKERS if m in text]

    ma, mb = markers(a), markers(b)
    return {
        "base_path": path, "with_param": with_param,
        "base_status": a.status, "param_status": b.status,
        "base_sha256": a.sha256, "param_sha256": b.sha256,
        "base_markers": ma, "param_markers": mb,
        "marker_changed": ma != mb,
        "body_changed": a.sha256 != b.sha256,
        "client_input_affects_gate": (ma != mb) or (a.sha256 != b.sha256),
    }


def build_findings(nodes: dict, transport: dict, clock: dict,
                   variants: dict, window: dict) -> dict:
    hints: List[str] = []
    if nodes.get("multi_node"):
        hints.append(f"multi_node={nodes['count']}: pin each backend IP with an "
                     "unchanged Host header to test per-node rate buckets")
    if transport.get("queue_ms") is not None:
        hints.append("Server-Timing exposes queue;dur -> server-side queue is "
                     "measurable, use it to locate the P5 bottleneck")
    if transport.get("http2"):
        hints.append("ALPN h2: multiplex concurrent reads on one connection")
    if transport.get("rate_headers"):
        hints.append("rate-limit headers present -> vary IP/session/account/node "
                     "one dimension at a time to identify the bucket key")
    if clock.get("synced") and clock.get("precision_ms") == 1000.0:
        hints.append(f"Date is 1s resolution; second_phase_ms="
                     f"{clock.get('second_phase_ms')} usable for boundary alignment")
    if clock.get("synced") and clock.get("precision_ms") == 1.0:
        hints.append("millisecond clock header available -> set clock.header in "
                     "config to this header, precision_ms=1.0")
    if variants.get("divergent"):
        hints.append("path variants diverge: at least one variant has a distinct "
                     "status/body -> probe its own window and rate behaviour")
    if window.get("client_input_affects_gate"):
        hints.append("window marker changes with a query parameter -> the gate "
                     "consumes client-controlled input; map the full parameter set")
    return {
        "hints": hints,
        "p5_measured": False,
        "rate_dimension_measured": False,
        "next_step": "run ops/p5_probe.py controlled probes during an open "
                     "round before changing concurrency/sessions",
    }


def run(args: argparse.Namespace) -> dict:
    host, port = split_host(args.host)
    if args.port:
        port = args.port
    cookie = os.environ.get(args.cookie_env, "") if args.cookie_env else ""
    client = RawGet(host, port, args.scheme, args.timeout, not args.insecure,
                    cookie, bind_ip=args.bind_ip)
    try:
        nodes = probe_nodes(host, port)
        base = client.get(args.path, preview_bytes=512)
        transport = probe_transport(base)
        clock = probe_clock(client, args.path, "date", args.samples,
                            args.interval_ms, args.clock_header)
        variants = probe_variants(client, args.variants)
        window = probe_window(client, args.path, args.window_param,
                              args.window_value)
    finally:
        client.close()
    report = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "target": {"host": host, "port": port, "scheme": args.scheme,
                   "path": args.path, "cookie_env": args.cookie_env,
                   "cookie_present": bool(cookie)},
        "read_only": True,
        "nodes": nodes,
        "transport": transport,
        "clock": clock,
        "variants": variants,
        "window": window,
        "findings": build_findings(nodes, transport, clock, variants, window),
    }
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only course-selection recon")
    ap.add_argument("--host", required=True, help="host, host:port or full URL")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--scheme", default="https", choices=("https", "http"))
    ap.add_argument("--path", default="/hsjw//wap/mycourseschedule.action")
    ap.add_argument("--cookie-env", default="KINGO_COOKIE")
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--bind-ip", default="", help="local source IP for node pinning")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--samples", type=int, default=9)
    ap.add_argument("--interval-ms", type=float, default=200.0)
    ap.add_argument("--clock-header", default="", help="custom ms header, e.g. X-Server-Time")
    ap.add_argument("--variants", nargs="*", default=DEFAULT_VARIANTS)
    ap.add_argument("--window-param", default="xnxq")
    ap.add_argument("--window-value", default="2026-2027-1")
    ap.add_argument("--json", default="", help="write the full JSON report here")
    args = ap.parse_args(argv)

    report = run(args)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        print(f"wrote {args.json}")
    t = report["transport"]
    c = report["clock"]
    print(f"status={t['status']} alpn={t['alpn'] or 'n/a'} "
          f"nodes={report['nodes']['count']} ttfb={t['ttfb_ms']}ms")
    print(f"clock: synced={c.get('synced')} header={c.get('header')} "
          f"offset={c.get('offset_ms', 'n/a')}ms spread={c.get('spread_ms', 'n/a')}ms "
          f"rtt_p50={c.get('rtt_p50_ms', 'n/a')}ms")
    print(f"variants: ok={report['variants']['ok_count']} "
          f"divergent={report['variants']['divergent']} "
          f"window_input_affects_gate={report['window']['client_input_affects_gate']}")
    for h in report["findings"]["hints"]:
        print(f"  - {h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
