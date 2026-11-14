#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/test_ops.py - offline regression for recon / seat_events / p5_probe.

Everything runs against a local loopback HTTP target. No school host, no
credentials, no write request. Python 3.10+ / standard library only.

    python test_ops.py -v
"""
from __future__ import annotations

import argparse
import http.server
import json
import sys
import threading
import time
import urllib.parse
from email.utils import formatdate

import p5_probe
import recon
import seat_events


class Target(http.server.BaseHTTPRequestHandler):
    hits = 0
    lock = threading.Lock()

    def log_message(self, *_args):
        pass

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        path, query = u.path, urllib.parse.parse_qs(u.query)
        body = b"{}"
        status = 200
        if path.startswith("/limited"):
            with Target.lock:
                Target.hits += 1
                n = Target.hits
            if n > 2:
                status = 429
                self.send_header("Retry-After", "1")
                body = b'{"error":"slow down"}'
            else:
                body = b'{"ok":true}'
        elif path.startswith("/window") or path.endswith("mycourseschedule.action"):
            if "xnxq" in query:
                body = b'{"open":true,"rows":[]}'
            else:
                body = b'{"prohibit":"1","msg":"not in selection time"}'
        elif path.endswith(".do"):
            status = 404
            body = b'{"error":"not found"}'
        elif path.startswith("/hsjw"):
            body = json.dumps({"variant": path, "rows": []}).encode()
        elif path == "/probe":
            body = b'{"ok":true,"rows":[]}'
        else:
            status = 404
            body = b'{"error":"not found"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Date", formatdate(usegmt=True))
        self.send_header("X-Server-Time", str(int(time.time() * 1000)))
        self.send_header("Server-Timing", "queue;dur=8.5, db;dur=22.1")
        self.send_header("Set-Cookie", "recon=1; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Case:
    def __init__(self):
        self.total = 0
        self.failed = []
        self.verbose = "-v" in sys.argv

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        self.total += 1
        if cond:
            if self.verbose:
                print(f"  OK  {name}")
        else:
            self.failed.append((name, detail))
            print(f"  FAIL  {name}  {detail}")


def main() -> int:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    c = Case()
    try:
        host = f"127.0.0.1:{port}"

        # --- helpers -----------------------------------------------------
        c.check("resolve_nodes", recon.resolve_nodes("127.0.0.1", port) == ["127.0.0.1"])
        c.check("split_host plain", recon.split_host("a.example") == ("a.example", 443))
        c.check("split_host port", recon.split_host("a.example:8443") == ("a.example", 8443))
        c.check("split_host url", recon.split_host("http://127.0.0.1:9") == ("127.0.0.1", 9))
        c.check("parse_server_time Date",
                recon.parse_server_time("Fri, 13 Nov 2026 02:48:00 GMT", "Date") is not None)
        c.check("parse_server_time ms",
                recon.parse_server_time("1799808000123", "X-Server-Time") == 1799808000.123)
        c.check("parse_server_timing",
                recon.parse_server_timing("queue;dur=8.5, db;dur=22.1")
                == {"queue": 8.5, "db": 22.1})

        # --- transport ---------------------------------------------------
        h, p = recon.split_host(host)
        client = recon.RawGet(h, p, "http", timeout=3.0, cookie="recon=1")
        r1 = client.get("/probe", preview_bytes=64)
        r2 = client.get("/probe", preview_bytes=64)
        c.check("raw_get status", r1.status == 200, str(r1.status))
        c.check("raw_get headers", "date" in r1.headers and "server-timing" in r1.headers)
        c.check("raw_get digest", len(r1.sha256) == 16)
        c.check("keep_alive reused", r2.reused is True, str(r2.reused))
        tr = recon.probe_transport(r1)
        c.check("transport queue_ms", tr["queue_ms"] == 8.5, str(tr["queue_ms"]))
        c.check("transport tx_ms", tr["tx_ms"] == 22.1, str(tr["tx_ms"]))
        c.check("transport set_cookie", tr["set_cookie"] is True)
        client.close()

        # --- clock -------------------------------------------------------
        c2 = recon.RawGet(h, p, "http", timeout=3.0)
        ms = recon.probe_clock(c2, "/probe", "Date", 6, 0, "X-Server-Time")
        c.check("clock ms synced", ms["synced"] is True)
        c.check("clock ms precision", ms["precision_ms"] == 1.0, str(ms["precision_ms"]))
        c.check("clock ms offset finite", abs(ms["offset_ms"]) < 60000, str(ms["offset_ms"]))
        sec = recon.probe_clock(c2, "/probe", "Date", 6, 0)
        c.check("clock Date precision", sec["precision_ms"] == 1000.0,
                str(sec["precision_ms"]))
        c.check("clock Date phase bound", 0.0 <= sec["second_phase_ms"] < 1000.0,
                str(sec["second_phase_ms"]))
        c2.close()

        # --- variants / window -------------------------------------------
        c3 = recon.RawGet(h, p, "http", timeout=3.0)
        var = recon.probe_variants(c3, [
            "/hsjw/wap/mycourseschedule.action",
            "/hsjw//wap/mycourseschedule.action",
            "/hsjw/wap/mycourseschedule.do",
        ])
        c.check("variants ok_count", var["ok_count"] == 2, str(var["ok_count"]))
        c.check("variants divergent", var["divergent"] is True)
        win = recon.probe_window(c3, "/hsjw//wap/mycourseschedule.action",
                                 "xnxq", "2026-2027-1")
        c.check("window input affects gate", win["client_input_affects_gate"] is True,
                json.dumps(win, ensure_ascii=False)[:200])
        c3.close()

        # --- full recon.run ----------------------------------------------
        args = argparse.Namespace(
            host=host, port=0, scheme="http",
            path="/hsjw//wap/mycourseschedule.action",
            cookie_env="", insecure=False, bind_ip="", timeout=3.0,
            samples=5, interval_ms=0.0, clock_header="",
            variants=recon.DEFAULT_VARIANTS[:3],
            window_param="xnxq", window_value="2026-2027-1",
        )
        report = recon.run(args)
        c.check("run read_only", report["read_only"] is True)
        c.check("run nodes", report["nodes"]["count"] >= 1)
        c.check("run transport", report["transport"]["status"] == 200)
        c.check("run clock", report["clock"]["synced"] is True)
        c.check("run findings", len(report["findings"]["hints"]) >= 2,
                str(report["findings"]["hints"]))
        c.check("run json serialisable", len(json.dumps(report)) > 100)

        # --- adaptive poller ---------------------------------------------
        pol = seat_events.AdaptivePoller(base_interval_ms=60000.0,
                                         hot_interval_ms=2000.0, hot_rounds=2)
        c.check("poller idle", pol.next_interval() == 60000.0)
        pol.on_event()
        c.check("poller hot 1", pol.next_interval() == 2000.0)
        c.check("poller hot 2", pol.next_interval() == 2000.0)
        c.check("poller back to idle", pol.next_interval() == 60000.0)
        pol.on_rate_limited(30000.0)
        c.check("poller cooldown", pol.next_interval() == 30000.0)
        pol.on_rate_limited(120000.0)
        c.check("Retry-After survives local max interval",
                pol.next_interval() == 120000.0)
        pol.on_auth_failure()
        c.check("poller halt", pol.next_interval() == 0.0)

        # --- release monitor ---------------------------------------------
        mon = seat_events.ReleaseMonitor()
        O = seat_events.Offering
        e0 = mon.observe([O("C1", "01", 60, 0)], 12.0)
        c.check("monitor quiet first read", e0 == [], str(e0))
        e1 = mon.observe([O("C1", "01", 60, 2)], 13.0)
        c.check("monitor reopened", [e.kind for e in e1] == ["reopened"], str(e1))
        e2 = mon.observe([O("C1", "01", 60, 5)], 14.0)
        c.check("monitor increased", [e.kind for e in e2] == ["increased"], str(e2))
        e3 = mon.observe([O("C1", "01", 60, 1)], 15.0)
        c.check("monitor decreased", [e.kind for e in e3] == ["decreased"], str(e3))
        c.check("monitor trigger", seat_events.ReleaseMonitor.trigger(e1 + e2) is True)
        c.check("monitor no trigger on decrease",
                seat_events.ReleaseMonitor.trigger(e3) is False)
        c.check("monitor report", mon.report()["polls"] == 4)
        # A missing seat count is not a known zero; it cannot prove reopening.
        unknown_mon = seat_events.ReleaseMonitor()
        unknown_mon.observe([O("UNKNOWN", "01", 10, None)])
        unknown_events = unknown_mon.observe([O("UNKNOWN", "01", 10, 3)])
        c.check("monitor unknown->positive is not reopening",
                unknown_events == [], str(unknown_events))

        # --- ranker -------------------------------------------------------
        ranker = seat_events.TargetRanker()
        targets = [
            {"course_id": "C1", "class_id": "01", "priority": 1, "enabled": True},
            {"course_id": "C2", "class_id": "01", "priority": 2, "enabled": True},
        ]
        offers = [O("C1", "01", 60, 1, heat=0.9),
                  O("C2", "01", 60, 30, heat=0.1)]
        ordered = ranker.reorder(targets, offers)
        c.check("ranker dynamic reorder", ordered[0]["course_id"] == "C2",
                str(ordered))
        c.check("ranker skips disabled",
                ranker.reorder([{"course_id": "C3", "enabled": False}], offers) == [])

        # --- intent gate --------------------------------------------------
        gate = seat_events.IntentGate()
        c.check("gate allow first", gate.allow(targets[0]) is True)
        c.check("gate issue first", gate.issue(targets[0]) is True)
        c.check("gate block duplicate", gate.issue(targets[0]) is False)
        gate.mark_pending(targets[0], "readback_503")
        c.check("gate pending", gate.states["C1|01"] == "pending")
        c.check("gate reconcile hit",
                gate.reconcile(targets[0], True) == "confirmed")
        c.check("gate still blocked after confirmed", gate.issue(targets[0]) is False)
        pending_gate = seat_events.IntentGate()
        pending_gate.issue(targets[0])
        c.check("absent readback leaves pending",
                pending_gate.reconcile(targets[0], False) == "pending")
        c.check("pending is not reissued", pending_gate.issue(targets[0]) is False)

        # --- driver -------------------------------------------------------
        drv = seat_events.EventDriver(targets)
        d0 = drv.poll([O("C1", "01", 60, 0)])
        c.check("driver quiet", d0.kind == "quiet", d0.kind)
        d1 = drv.poll([O("C1", "01", 60, 3)])
        c.check("driver submit", d1.kind == "submit", d1.kind)
        c.check("driver hot interval", d1.interval_ms == 2000.0, str(d1.interval_ms))
        d2 = drv.poll([O("C1", "01", 60, 4)])
        c.check("driver duplicate blocked", d2.kind == "duplicate", d2.kind)
        d3 = drv.poll([], status=429)
        c.check("driver cooldown", d3.kind == "rate_limited", d3.kind)
        d4 = drv.poll([], status=401)
        c.check("driver auth halt", d4.kind == "auth", d4.kind)

        # A non-200 read must not discard the previous official snapshot.
        # Otherwise the next unchanged positive row is a fake 'new' event.
        protected = seat_events.EventDriver(targets)
        positive = [O("C1", "01", 60, 3)]
        first = protected.poll(positive)
        c.check("first observation with known seats", first.kind == "submit")
        redirected = protected.poll([], status=302)
        c.check("redirect blocks catalog use",
                redirected.kind == "read_unavailable", redirected.kind)
        c.check("redirect preserves last snapshot",
                protected.monitor.snapshot["C1|01"].remain == 3)
        returned = protected.poll(positive)
        c.check("unchanged catalog does not create phantom new event",
                returned.kind == "quiet", returned.kind)
        missing = protected.poll([], status=404)
        c.check("404 does not reset baseline",
                missing.kind == "read_unavailable"
                and protected.monitor.snapshot["C1|01"].remain == 3)
        cached = protected.poll([], status=304)
        c.check("304 does not masquerade as a fresh catalog",
                cached.kind == "read_unavailable"
                and protected.monitor.snapshot["C1|01"].remain == 3)


        # --- p5_probe -----------------------------------------------------
        c.check("spearman perfect negative",
                abs(p5_probe.spearman([1, 2, 3, 4], [4, 3, 2, 1]) + 1.0) < 1e-9,
                str(p5_probe.spearman([1, 2, 3, 4], [4, 3, 2, 1])))
        c.check("spearman ties handled",
                0.8 < abs(p5_probe.spearman([1, 2, 3, 4], [1, 1, 0, 0])) <= 1.0,
                str(p5_probe.spearman([1, 2, 3, 4], [1, 1, 0, 0])))
        fcfs = p5_probe.classify_allocation([(1, 1), (2, 1), (3, 1),
                                             (4, 0), (5, 0), (6, 0)])
        c.check("allocation fcfs", fcfs["hint"] == "fcfs", str(fcfs))
        rnd = p5_probe.classify_allocation([(1, 0), (2, 1), (3, 0),
                                            (4, 1), (5, 0), (6, 1)])
        c.check("allocation random", rnd["hint"] == "random", str(rnd))
        prio = p5_probe.classify_allocation(
            [(1, 0), (2, 1), (3, 0), (4, 1), (5, 1), (6, 0)],
            priorities=[1, 9, 2, 8, 7, 3])
        c.check("allocation priority", prio["hint"] == "priority", str(prio))
        rate = p5_probe.classify_rate_dimension([
            {"dimension": "ip", "key": "A", "requests": 20, "limited": 18},
            {"dimension": "ip", "key": "B", "requests": 20, "limited": 2},
            {"dimension": "session", "key": "S1", "requests": 20, "limited": 10},
            {"dimension": "session", "key": "S2", "requests": 20, "limited": 10},
        ])
        c.check("rate dimension ip", rate["hint"] == "ip", str(rate))
        rec = p5_probe.recommend_strategy(fcfs, rate)
        c.check("strategy fcfs prebuild", rec["strategy"]["prebuild"] is True)
        c.check("strategy sessions", rec["strategy"]["sessions"] == 2,
                str(rec["strategy"]["sessions"]))
        c.check("strategy single submit", rec["strategy"]["single_physical_submit"] is True)
        c.check("demo serialisable", len(json.dumps(p5_probe.demo())) > 100)
    finally:
        srv.shutdown()
        srv.server_close()

    passed = c.total - len(c.failed)
    print(f"{passed}/{c.total} OK")
    for name, detail in c.failed:
        print(f"  FAILED: {name} :: {detail}")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
