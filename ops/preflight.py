#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/preflight.py - #7 ticket/token prefetch, #8 transport position, #9 jitter.

Everything here happens BEFORE T0. Moving work out of the timing window only
helps if the server is FCFS; ops/p5_probe.py decides that first.

  TicketPool        harvest one-time tokens / hidden fields / CSRF values from
                    read-only pages outside the window; single consumption,
                    TTL guarding, values never appear in any report.
  BodyPrebuilder    serialise the request body before the clock starts.
  ConnectionShard   one keep-alive connection per session plus a spare node;
                    DNS prefetch and TLS session-ticket reuse plan.
  HighResClock      QPC resolution on Windows, CPU affinity / priority hints,
                    sub-millisecond wait. Second-boundary phase is taken from
                    ops/recon.py::probe_clock, not re-derived here.
  PreflightPlan     one explicit checklist + an honest timing budget.

Standard library only, Python 3.10+. No network writes; DNS resolution is
read-only. 0-RTT early data is explicitly forbidden for course submissions.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import socket
import sys
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence

WRITE_UNSAFE = ("0-rtt early data", "tls early data")


# ---------------------------------------------------------------------------
# #7 tickets
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"<input\b[^>]*>", re.I | re.S)
ATTR_RE = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:]*)\s*=\s*"
                     r"(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.I | re.S)
DEFAULT_TICKET_NAMES = ("token", "csrf", "csrfmiddlewaretoken", "__VIEWSTATE",
                        "lt", "execution", "hidOption", "xktype", "lcid")


def extract_hidden(html: str) -> Dict[str, str]:
    """Parse name/value pairs from <input> tags regardless of attribute order."""
    out: Dict[str, str] = {}
    for tag in TAG_RE.findall(html):
        attrs: Dict[str, str] = {}
        for m in ATTR_RE.finditer(tag):
            key = m.group(1).lower()
            val = next((g for g in m.groups()[1:] if g is not None), "")
            attrs[key] = val
        name = attrs.get("name") or attrs.get("id")
        if name and "value" in attrs:
            out[name] = attrs["value"]
    return out


@dataclass
class Ticket:
    name: str
    value: str                       # never serialised by public()
    fetched_at: float
    ttl_s: float = 300.0
    consumed: bool = False

    def expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) - self.fetched_at > self.ttl_s

    def public(self) -> dict:
        return {"name": self.name, "len": len(self.value),
                "sha8": hashlib.sha256(self.value.encode()).hexdigest()[:8],
                "age_s": round(time.time() - self.fetched_at, 3),
                "ttl_s": self.ttl_s, "consumed": self.consumed,
                "expired": self.expired()}


class TicketPool:
    """One-shot token store. A consumed or expired ticket is never reused."""

    def __init__(self, ttl_s: float = 300.0):
        self.ttl_s = ttl_s
        self._items: Dict[str, Ticket] = {}

    def harvest(self, html: str, names: Sequence[str] = DEFAULT_TICKET_NAMES,
                now: Optional[float] = None) -> List[str]:
        found = extract_hidden(html)
        added: List[str] = []
        for name in names:
            if name in found and found[name]:
                self._items[name] = Ticket(name, found[name],
                                           now if now is not None else time.time(),
                                           self.ttl_s)
                added.append(name)
        return added

    def put(self, name: str, value: str, now: Optional[float] = None) -> None:
        self._items[name] = Ticket(name, value,
                                   now if now is not None else time.time(),
                                   self.ttl_s)

    def take(self, name: str) -> Optional[str]:
        t = self._items.get(name)
        if t is None or t.consumed or t.expired():
            return None
        t.consumed = True
        return t.value

    def purge(self) -> int:
        dead = [k for k, v in self._items.items() if v.consumed or v.expired()]
        for k in dead:
            del self._items[k]
        return len(dead)

    def report(self) -> dict:
        return {"count": len(self._items),
                "available": sum(1 for t in self._items.values()
                                 if not t.consumed and not t.expired()),
                "items": [t.public() for t in sorted(
                    self._items.values(), key=lambda x: x.name)]}


class BodyPrebuilder:
    """Serialise before T0 so the timing window contains only the send."""

    def __init__(self, template: Optional[Dict[str, str]] = None):
        self.template = dict(template or {})
        self._sealed: Dict[str, str] = {}

    def prebuild(self, key: str, fields: Dict[str, str]) -> str:
        merged = dict(self.template)
        merged.update(fields)
        body = urllib.parse.urlencode(merged)
        self._sealed[key] = body
        return body

    def sealed(self, key: str) -> Optional[str]:
        return self._sealed.get(key)

    def report(self) -> dict:
        return {"sealed": len(self._sealed),
                "keys": sorted(self._sealed),
                "bytes": {k: len(v) for k, v in self._sealed.items()},
                "values_in_report": False}


# ---------------------------------------------------------------------------
# #8 transport position
# ---------------------------------------------------------------------------

@dataclass
class Shard:
    session_id: int
    node: str
    conn_id: int
    keep_alive: bool = True
    tls_session_reuse: bool = True
    alpn: str = "h2,http/1.1"

    def public(self) -> dict:
        return asdict(self)


class ConnectionShard:
    """Assign one persistent connection per session; keep a spare node.

    This is a plan, not a socket pool: the existing executors own their
    transport. Nothing here opens a connection to the school.
    """

    def __init__(self, nodes: Sequence[str], sessions: int = 1,
                 spare: int = 1):
        self.nodes = list(nodes) or ["TARGET_HOST"]
        self.sessions = max(1, int(sessions))
        self.spare = max(0, int(spare))
        self._shards: List[Shard] = []
        for i in range(self.sessions):
            self._shards.append(Shard(i, self.nodes[i % len(self.nodes)], i))
        for j in range(self.spare):
            self._shards.append(Shard(self.sessions + j,
                                      self.nodes[(self.sessions + j) % len(self.nodes)],
                                      self.sessions + j, tls_session_reuse=False))

    def resolve(self, host: str, port: int = 443) -> List[str]:
        """Read-only DNS prefetch; returns unique IPv4/IPv6 addresses."""
        try:
            infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
            return sorted({i[4][0] for i in infos})
        except OSError as exc:
            return [f"<resolve failed: {type(exc).__name__}>"]

    def tls_plan(self) -> dict:
        return {"session_tickets": True, "session_reuse": True,
                "alpn": ["h2", "http/1.1"],
                "early_data_for_write": False,
                "note": "0-RTT is replayable and forbidden for submissions; "
                        "use it for read-only catalog polling at most",
                "tcp_fast_open": "read-only only",
                "nagle": "disabled on the timing connection",
                "quickack": "best effort"}

    def report(self) -> dict:
        return {"sessions": self.sessions, "nodes": len(self.nodes),
                "shards": [s.public() for s in self._shards],
                "early_data_blocked": True}


# ---------------------------------------------------------------------------
# #9 local scheduling jitter
# ---------------------------------------------------------------------------

class HighResClock:
    """QPC resolution report + CPU affinity hints + precise waiting.

    The absolute epoch used by wait_until is time.perf_counter_ns() so it is
    comparable with Python's monotonic clock on every platform.
    """

    def __init__(self):
        self.platform = sys.platform
        self.qpc_freq = 0
        if sys.platform == "win32":
            try:
                freq = ctypes.c_longlong()
                ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
                self.qpc_freq = int(freq.value)
            except Exception:                          # noqa: BLE001
                self.qpc_freq = 0

    def now_ns(self) -> int:
        return time.perf_counter_ns()

    def resolution_ns(self) -> int:
        if self.qpc_freq:
            return max(1, round(1e9 / self.qpc_freq))
        return 1000  # perf_counter_ns nominal resolution

    def pin(self, cpu_index: int = 0) -> dict:
        """Best-effort affinity + priority; never raises."""
        applied: List[str] = []
        notes: List[str] = []
        if sys.platform == "win32":
            try:
                mask = 1 << max(0, int(cpu_index))
                k = ctypes.windll.kernel32
                if k.SetProcessAffinityMask(k.GetCurrentProcess(), mask):
                    applied.append("SetProcessAffinityMask")
                if k.SetPriorityClass(k.GetCurrentProcess(), 0x00000080):
                    applied.append("HIGH_PRIORITY_CLASS")
            except Exception as exc:                  # noqa: BLE001
                notes.append(type(exc).__name__)
        elif hasattr(os, "sched_setaffinity"):
            try:
                os.sched_setaffinity(0, {max(0, int(cpu_index))})
                applied.append("sched_setaffinity")
            except OSError as exc:
                notes.append(type(exc).__name__)
        else:
            notes.append("no affinity API on this platform")
        return {"applied": applied, "notes": notes,
                "warning": "high priority can starve the GUI thread; use "
                           "only in the dedicated worker process"}

    def phase_from_recon(self, clock_report: dict) -> dict:
        """Reuse ops/recon.py::probe_clock instead of re-deriving the phase."""
        return {"offset_ms": clock_report.get("offset_ms"),
                "second_phase_ms": clock_report.get("second_phase_ms"),
                "rtt_p50_ms": clock_report.get("rtt_p50_ms"),
                "date_precision_ms": 1000,
                "source": "recon.probe_clock"}

    def wait_until(self, target_perf_ns: int, lead_ms: float = 30.0,
                   spin_ms: float = 20.0, max_wait_s: float = 30.0) -> dict:
        """Sleep until (target - lead), then yield-spin. Returns jitter."""
        start = self.now_ns()
        deadline = target_perf_ns - int(lead_ms * 1_000_000)
        limit = start + int(max_wait_s * 1e9)
        while self.now_ns() < deadline and self.now_ns() < limit:
            remaining_ms = (deadline - self.now_ns()) / 1e6
            if remaining_ms > spin_ms + 1.0:
                time.sleep(min((remaining_ms - spin_ms) / 1000.0, 0.25))
            else:
                time.sleep(0)                        # yield, no busy burn
        reached = self.now_ns()
        return {"target_ns": target_perf_ns,
                "lead_ms": lead_ms,
                "reached_ns": reached,
                "overshoot_ms": round((reached - start) / 1e6, 3),
                "remaining_ms": round((target_perf_ns - reached) / 1e6, 3),
                "timed_out": reached >= limit}


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def assemble(pool: TicketPool, bodies: BodyPrebuilder,
             shards: ConnectionShard, clock: HighResClock,
             clock_report: Optional[dict] = None,
             lead_ms: float = 30.0) -> dict:
    t = pool.report()
    b = bodies.report()
    s = shards.report()
    phase = clock.phase_from_recon(clock_report or {})
    checks = {
        "tickets_available": t["available"] > 0,
        "body_preserialised": b["sealed"] > 0,
        "connections_planned": len(s["shards"]) >= shards.sessions,
        "clock_resolution_ns": clock.resolution_ns(),
        "phase_known": phase["second_phase_ms"] is not None,
        "early_data_for_write": False,
    }
    warnings = [
        "one-time tickets may be bound to the submission instant; verify on a "
        "test round before relying on prefetch",
        "CAS ticket refresh must finish before T0 or a 302 lands inside the window",
        "0-RTT early data is replayable: never use it for the write request",
        "all positioning gains are noise when the allocation rule is random "
        "or priority based (see ops/p5_probe.py)",
    ]
    return {"checks": checks, "tickets": t, "bodies": b, "shards": s,
            "phase": phase, "lead_ms": lead_ms,
            "budget_ms": {"saved_rtt": 1, "saved_redirect": 1,
                          "note": "upper bound only; measure with recon"},
            "warnings": warnings,
            "invariants": {"single_physical_submit": True,
                           "http_200_is_not_success": True,
                           "unknown_result_writes_pending": True}}


def demo() -> dict:
    html = ('<form><input type="hidden" name="token" value="T0k3n-abc" />'
            '<input value="lt-123" name="lt" />'
            '<input id="xktype" value="N" />'
            '<input name="csrf" value="c5rf" /></form>')
    pool = TicketPool(ttl_s=300.0)
    added = pool.harvest(html, ("token", "lt", "csrf", "xktype"))
    bodies = BodyPrebuilder({"xktype": "N", "sfbd": "0"})
    body = bodies.prebuild("target-1", {"kcdm": "TARGET_COURSE_ID",
                                         "token": pool.take("token") or ""})
    shards = ConnectionShard(["10.0.0.1", "10.0.0.2"], sessions=3, spare=1)
    clock = HighResClock()
    target = clock.now_ns() + 5_000_000
    wait = clock.wait_until(target, lead_ms=2.0, spin_ms=1.0, max_wait_s=1.0)
    plan = assemble(pool, bodies, shards, clock,
                    {"offset_ms": 12.5, "second_phase_ms": 37.0,
                     "rtt_p50_ms": 18.0})
    return {"harvested": added, "body_len": len(body),
            "body_contains_kcdm": "kcdm=" in body,
            "dns_nodes": shards.resolve("localhost", 80)[:2],
            "tls_plan": shards.tls_plan(), "wait": wait,
            "plan": plan}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pin", type=int, default=-1,
                    help="best-effort CPU affinity (dedicated worker only)")
    args = ap.parse_args(argv)
    if args.demo:
        report = demo()
    else:
        report = demo()
        report["plan_only"] = True
    if args.pin >= 0:
        report["pin"] = HighResClock().pin(args.pin)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        p = report["plan"]
        print("tickets=", p["tickets"]["available"],
              "bodies=", p["bodies"]["sealed"],
              "shards=", len(p["shards"]["shards"]),
              "clock_res_ns=", p["checks"]["clock_resolution_ns"])
        print("phase=", p["phase"]["second_phase_ms"],
              "wait_remaining_ms=", report["wait"]["remaining_ms"])
        print("early_data_for_write=", p["checks"]["early_data_for_write"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
