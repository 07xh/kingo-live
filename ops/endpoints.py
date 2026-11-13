#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/endpoints.py - #4 endpoint/channel matrix and #6 window attribution.

Read-only extension of ops/recon.py::probe_variants. recon answers "do these
paths differ?"; this module answers "which channel has its own rate bucket /
window check, and where does the gate actually live?".

  build_matrix        wap <-> web <-> api, .action <-> .do, single/double
                      slash, JSON channel, term/round query variants.
  probe_all           injected fetcher, GET/HEAD/OPTIONS only by default.
  classify            group by (status, content-type, rate headers, window
                      marker) and flag genuinely independent entrances.
  attribute_window    decide whether the gate is client_param, session_state,
                      js_side, server_time or unknown.
  recommend           one explicit endpoint recommendation, never a blind
                      "try them all" - concurrency stays at 1.

Standard library only, Python 3.10+. No write requests are ever emitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
RATE_HEADERS = ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining",
                "x-rate-limit-limit", "server-timing", "x-queue", "x-request-id")
WINDOW_RE = re.compile(
    r'prohibit\s*["\']?\s*[:=]\s*["\']?1|'
    r'不在选课时间|选课时间未到|当前不在选课|未开放|'
    r'window\s*closed|not\s+in\s+selection\s+time', re.I)
OPEN_RE = re.compile(r'prohibit\s*["\']?\s*[:=]\s*["\']?0|选课开放|可提交', re.I)


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Endpoint:
    name: str
    channel: str                       # wap | web | api
    path: str
    method: str = "GET"
    params: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)

    def url(self) -> str:
        if not self.params:
            return self.path
        sep = "&" if "?" in self.path else "?"
        return self.path + sep + urllib.parse.urlencode(self.params)

    def key(self) -> Tuple[str, str, str]:
        return (self.channel, self.path,
                urllib.parse.urlencode(sorted(self.params.items())))

    def public(self) -> dict:
        return {"name": self.name, "channel": self.channel,
                "url": _public_url(self.url()), "method": self.method,
                "header_names": sorted(self.headers)}


@dataclass
class SimpleResponse:
    status: int = 200
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    ttfb_ms: float = 0.0

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


@dataclass
class ProbeResult:
    name: str
    channel: str
    url: str
    status: int = 0
    size: int = 0
    sha8: str = ""
    content_type: str = ""
    rate: Dict[str, str] = field(default_factory=dict)
    window_marker: str = "unknown"     # open | closed | unknown
    ttfb_ms: float = 0.0
    error: str = ""

    def signature(self) -> tuple:
        return (self.status, self.content_type, self.window_marker,
                tuple(sorted(self.rate.items())))

    def public(self) -> dict:
        result = asdict(self)
        result["url"] = _public_url(self.url)
        return result


@dataclass
class WindowObservation:
    label: str
    param: str = ""                    # term / round / batch value sent
    cookie_label: str = "same"         # same | other | none
    marker: str = "unknown"            # open | closed | unknown
    js_decides: bool = False           # response contains a JS-side gate
    note: str = ""


def _public_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    safe = urllib.parse.urlencode([(k, "<redacted>") for k, _ in
                                  urllib.parse.parse_qsl(parts.query,
                                                         keep_blank_values=True)])
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                   safe, ""))


# ---------------------------------------------------------------------------
# matrix construction
# ---------------------------------------------------------------------------

def _split_path(base_path: str) -> Tuple[str, str, str]:
    """Return (prefix, stem, suffix) for /a/b/stem.action(?:?query)."""
    clean = base_path.split("?", 1)[0]
    m = re.match(r"^(.*?)([^/]+?)(\.[a-z0-9]+)$", clean, re.I)
    if not m:
        return (clean, "", "")
    return (m.group(1), m.group(2), m.group(3))


def build_matrix(base_path: str,
                 channels: Sequence[str] = ("wap", "web", "api"),
                 suffixes: Sequence[str] = (".action", ".do"),
                 double_slash: bool = True,
                 json_channel: bool = True,
                 param_sets: Sequence[Dict[str, str]] = ()) -> List[Endpoint]:
    """Generate a de-duplicated, read-only endpoint matrix.

    `/hsjw//wap/x.action` (the double slash seen in the real HAR) is kept as
    an explicit variant: gateways frequently route it to a different backend.
    """
    prefix, stem, suffix = _split_path(base_path)
    if not stem:
        raise ValueError("base_path must look like /a/b/stem.action")
    out: List[Endpoint] = []
    seen = set()

    def add(name: str, channel: str, path: str,
            params: Optional[Dict[str, str]] = None,
            headers: Optional[Dict[str, str]] = None) -> None:
        e = Endpoint(name=name, channel=channel, path=path,
                     params=dict(params or {}), headers=dict(headers or {}))
        if e.key() not in seen:
            seen.add(e.key())
            out.append(e)

    for channel in channels:
        for ext in suffixes:
            chan_prefix = prefix
            if channel == "wap":
                chan_prefix = re.sub(r"/web/", "/wap/", chan_prefix)
                if "/wap/" not in chan_prefix:
                    chan_prefix = chan_prefix.rstrip("/") + "/wap/"
            elif channel == "web":
                chan_prefix = re.sub(r"/wap/", "/web/", chan_prefix)
                if "/web/" not in chan_prefix:
                    chan_prefix = chan_prefix.rstrip("/") + "/web/"
            elif channel == "api":
                chan_prefix = re.sub(r"/(wap|web)/", "/", chan_prefix)
                chan_prefix = chan_prefix.rstrip("/") + "/api/"
            add(f"{channel}{ext}", channel, chan_prefix + stem + ext)
            if double_slash:
                doubled = re.sub(r"(/hsjw/)(?![/])", r"\1/", chan_prefix, count=1)
                if doubled != chan_prefix:
                    add(f"{channel}{ext}-dblslash", channel,
                        doubled + stem + ext)
        if json_channel:
            add(f"{channel}-json", channel, prefix + stem + suffix,
                {"format": "json"}, {"Accept": "application/json"})

    for i, params in enumerate(param_sets, 1):
        add(f"paramset-{i}", "web", prefix + stem + suffix, params)

    return out


def matrix_from_config(cfg: dict) -> List[Endpoint]:
    """Accept ops/config.example.json shape; returns the union matrix."""
    target = cfg.get("target", {})
    rc = cfg.get("recon", {})
    base = target.get("path") or "/hsjw//wap/mycourseschedule.action"
    variants = list(rc.get("variants") or [])
    out: List[Endpoint] = []
    seen = set()
    for p in [base] + variants:
        for e in build_matrix(p, param_sets=[
                {rc.get("window_param", "xnxq"): rc.get("window_value", "")}]):
            if e.key() not in seen:
                seen.add(e.key())
                out.append(e)
    return out


# ---------------------------------------------------------------------------
# probing + classification
# ---------------------------------------------------------------------------

def _marker(text: str) -> str:
    if WINDOW_RE.search(text):
        return "closed"
    if OPEN_RE.search(text):
        return "open"
    return "unknown"


def _headers_of(resp: Any) -> Dict[str, str]:
    h = getattr(resp, "headers", None)
    if h is None:
        return {}
    if hasattr(h, "items"):
        return {str(k).lower(): str(v) for k, v in h.items()}
    return {str(k).lower(): str(v) for k, v in dict(h).items()}


def probe_all(fetch: Callable[[Endpoint], Any],
              endpoints: Iterable[Endpoint],
              allow_write: bool = False) -> List[ProbeResult]:
    """Probe every endpoint with an injected fetcher.

    Any method outside GET/HEAD/OPTIONS raises unless allow_write is set.
    Even then nothing is written here - the caller owns the fetcher.
    """
    results: List[ProbeResult] = []
    for e in endpoints:
        r = ProbeResult(name=e.name, channel=e.channel, url=e.url())
        if e.method not in READ_ONLY_METHODS and not allow_write:
            raise ValueError(f"write method blocked: {e.name} {e.method}")
        try:
            resp = fetch(e)
            hs = _headers_of(resp)
            body = getattr(resp, "body", b"")
            if isinstance(body, str):
                body = body.encode("utf-8", "replace")
            r.status = int(getattr(resp, "status", 0))
            r.size = len(body)
            r.sha8 = hashlib.sha256(body).hexdigest()[:8]
            r.content_type = hs.get("content-type", "").split(";")[0].strip()
            r.rate = {k: hs[k] for k in RATE_HEADERS if k in hs}
            r.window_marker = _marker(body.decode("utf-8", "replace"))
            r.ttfb_ms = float(getattr(resp, "ttfb_ms", 0.0))
        except Exception as exc:                       # noqa: BLE001
            r.error = type(exc).__name__
        results.append(r)
    return results


def classify(results: Sequence[ProbeResult]) -> dict:
    """Group identical response shapes and flag independent entrances."""
    groups: Dict[tuple, List[str]] = {}
    for r in results:
        groups.setdefault(r.signature(), []).append(r.name)

    rate_buckets = {tuple(sorted(r.rate.items())) for r in results if r.rate}
    window_markers = {r.window_marker for r in results if r.status == 200}
    channels = {r.channel for r in results if r.status == 200}

    group_list = [{"signature": {
        "status": sig[0], "content_type": sig[1],
        "window_marker": sig[2], "rate": dict(sig[3])},
        "members": members, "count": len(members),
        "channels": sorted({r.channel for r in results
                            if r.signature() == sig})}
        for sig, members in sorted(groups.items(), key=lambda x: -len(x[1]))]

    independent = len({g["signature"]["window_marker"] for g in group_list}) > 1 \
        or len(rate_buckets) > 1
    return {
        "probed": len(results),
        "ok": sum(1 for r in results if r.status == 200),
        "errors": [r.name for r in results if r.error],
        "groups": group_list,
        "open_paths": [_public_url(r.url) for r in results
                       if r.status == 200 and r.window_marker == "open"],
        "divergent": len(groups) > 1,
        "independent_entrance": independent,
        "rate_buckets": len(rate_buckets),
        "window_markers": sorted(window_markers),
        "live_channels": sorted(channels),
        "judgement": (
            "independent gate/limit found: probe that channel separately"
            if independent else
            "all variants share one backend: no bypass from path switching"),
    }


# ---------------------------------------------------------------------------
# #6 window attribution
# ---------------------------------------------------------------------------

def attribute_window(observations: Sequence[WindowObservation]) -> dict:
    """Locate the selection-window gate from controlled read-only samples."""
    if not observations:
        return {"attribution": "unknown", "confidence": 0.0, "evidence": []}
    evidence: List[str] = []
    if any(o.js_decides for o in observations):
        evidence.append("response contains a client-side JS gate")
        return {"attribution": "js_side", "confidence": 0.6,
                "evidence": evidence,
                "next": "read the JS branch; server may still re-check"}

    # Same session, different param -> marker changes  => client_param.
    # Same param, different session -> marker changes  => session_state.
    by_cookie: Dict[str, set] = {}
    by_param: Dict[str, set] = {}
    for o in observations:
        by_cookie.setdefault(o.cookie_label, set()).add(o.marker)
        by_param.setdefault(o.param, set()).add(o.marker)
    cookies = sorted(by_cookie)
    param_sensitive = len(by_param) > 1 and any(
        len(v) > 1 for v in by_cookie.values())
    session_sensitive = any(len(v) > 1 for v in by_param.values())

    if param_sensitive:
        a, conf = "client_param", 0.85
        evidence.append("same session, different term/round param -> marker changes")
        nxt = "the gate reads a client-controlled field; verify server re-check"
    elif session_sensitive:
        a, conf = "session_state", 0.75
        evidence.append("same param, different session -> marker changes")
        nxt = "refresh CAS ticket / session before T0"
    elif all(o.marker == "closed" for o in observations):
        a, conf = "server_time", 0.7
        evidence.append("marker stays closed across params and sessions")
        nxt = "only the server clock matters; timing work is the whole game"
    else:
        a, conf = "unknown", 0.2
        evidence.append("insufficient contrast between samples")
        nxt = "collect param + session + no-cookie samples"

    evidence.append(f"samples={len(observations)} cookies={cookies} "
                    f"param_groups={len(by_param)}")
    return {"attribution": a, "confidence": conf, "evidence": evidence,
            "next": nxt}


def recommend(classification: dict, attribution: dict) -> dict:
    """One recommendation. Never enables parallel submissions."""
    independent = classification.get("independent_entrance", False)
    live = classification.get("live_channels", [])
    gate = attribution.get("attribution")
    if not live:
        return {"channel": "", "reason": "no 2xx channel observed",
                "window_gate": gate, "concurrency": 1, "sessions": 1,
                "prebuild": False, "single_physical_submit": True,
                "note": "no entrance verified; do not submit"}
    open_channels = sorted({g["channels"][0] for g in
                            classification.get("groups", [])
                            if g["signature"]["window_marker"] == "open"
                            and g["channels"] and g["signature"]["status"] == 200})
    if independent and open_channels:
        channel = open_channels[0]
        reason = "this channel has an open marker / distinct limit; verify it " \
                 "before treating it as the single entrance"
    elif independent:
        channel = sorted(live, key=lambda c: (c != "web", c))[0]
        reason = "distinct rate/window behaviour; keep as the single entrance"
    else:
        channel = "web" if "web" in live else live[0]
        reason = "all channels share one backend; pick the most verified path"
    return {"channel": channel, "reason": reason,
            "candidate_paths": classification.get("open_paths", [])[:5],
            "window_gate": gate,
            "concurrency": 1, "sessions": 1,
            "prebuild": gate in ("server_time", "session_state"),
            "single_physical_submit": True,
            "note": "do not fan out; rate dimension must be measured first (#2)"}


# ---------------------------------------------------------------------------
# CLI / demo
# ---------------------------------------------------------------------------

def demo() -> dict:
    base = "/hsjw/wap/mycourseschedule.action"
    eps = build_matrix(base, param_sets=[{"xnxq": "2026-2027-1"}])
    body_closed = b'{"prohibit":"1","msg":"not in selection time"}'
    body_open = b'{"prohibit":"0","rows":[]}'

    def fetch(e: Endpoint) -> SimpleResponse:
        if e.channel == "api" or "json" in e.name:
            return SimpleResponse(200, {"Content-Type": "application/json",
                                       "X-RateLimit-Limit": "60"},
                                  body_closed, 8.0)
        if "dblslash" in e.name:
            return SimpleResponse(200, {"Content-Type": "application/json",
                                       "X-RateLimit-Limit": "600"},
                                  body_open, 12.0)
        return SimpleResponse(200, {"Content-Type": "text/html"},
                              b"<html>not in selection time</html>", 15.0)

    results = probe_all(fetch, eps)
    cls = classify(results)
    obs = [WindowObservation("term-A", "2026-2027-1", "same", "closed"),
           WindowObservation("term-B", "2025-2026-2", "same", "open"),
           WindowObservation("session-B", "2026-2027-1", "other", "closed")]
    att = attribute_window(obs)
    return {"endpoints": [e.public() for e in eps],
            "results": [r.public() for r in results],
            "classification": cls, "window": att,
            "recommendation": recommend(cls, att)}


def run(args: argparse.Namespace) -> dict:
    eps = build_matrix(args.path, param_sets=[
        {args.window_param: args.window_value}] if args.window_value else [])
    if args.demo_fetch:
        return demo()
    # Live probing is deliberately opt-in and read-only.
    if not args.host:
        raise SystemExit("--host is required unless --demo-fetch is set")
    try:
        from . import recon
    except ImportError:
        import recon
    import os
    client = recon.RawGet(args.host, args.port, args.scheme,
                          timeout=args.timeout,
                          cookie=os.environ.get(args.cookie_env, ""))

    def fetch(e: Endpoint) -> SimpleResponse:
        # recon returns a bounded preview, not the full body: enough for the
        # window marker and content-type, and it keeps the probe read-only.
        r = client.get(e.url(), preview_bytes=4096,
                       extra_headers=e.headers)
        if r.error:
            raise OSError(r.error)
        body = (r.body_preview or "").encode("utf-8", "replace")
        return SimpleResponse(r.status, dict(r.headers), body, r.ttfb_ms)

    try:
        results = probe_all(fetch, eps)
    finally:
        client.close()
    cls = classify(results)
    return {"endpoints": [e.public() for e in eps],
            "results": [r.public() for r in results],
            "classification": cls,
            "recommendation": recommend(cls, {"attribution": "unknown"})}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--demo", action="store_true", help="offline demo + judgement")
    ap.add_argument("--demo-fetch", action="store_true")
    ap.add_argument("--host", default="")
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--scheme", default="https")
    ap.add_argument("--path", default="/hsjw/wap/mycourseschedule.action")
    ap.add_argument("--window-param", default="xnxq")
    ap.add_argument("--window-value", default="2026-2027-1")
    ap.add_argument("--cookie-env", default="KINGO_COOKIE")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = demo() if args.demo else run(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        c = report["classification"]
        print(f"endpoints={len(report['endpoints'])} probed={c['probed']} "
              f"ok={c['ok']} groups={len(c['groups'])}")
        print(f"divergent={c['divergent']} independent={c['independent_entrance']} "
              f"rate_buckets={c['rate_buckets']} markers={c['window_markers']}")
        if "window" in report:
            print(f"window_gate={report['window']['attribution']} "
                  f"confidence={report['window']['confidence']}")
        print("recommend=", report["recommendation"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
