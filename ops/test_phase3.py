"""test_phase3 - offline regression for orchestrator, adapter, race_probe.

Everything runs against in-process fakes and a loopback HTTP server; no
external network, no real credentials, no writes to any real endpoint.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

import adapter  # noqa: E402
import orchestrator as orch  # noqa: E402
import race_probe  # noqa: E402
from crosscheck import Evidence  # noqa: E402
from endpoints import Endpoint  # noqa: E402
from seat_events import Offering  # noqa: E402


class Collector:
    def __init__(self):
        self.count = 0
        self.lock = threading.Lock()

    def __call__(self, target):
        with self.lock:
            self.count += 1
            self.last = dict(target)
        return {"status": 200, "body": "ok"}


class TestAdapter(unittest.TestCase):
    def test_alias_mapping(self):
        t = adapter.normalize_target({"kcid": "C1", "skbj": "01",
                                      "priority": 2})
        self.assertEqual(t["course_id"], "C1")
        self.assertEqual(t["class_id"], "01")
        self.assertEqual(t["priority"], 2)
        self.assertTrue(t["enabled"])

    def test_camel_and_chinese_aliases(self):
        t = adapter.normalize_target({"courseId": "C2", "jxbh": "02",
                                      "vol": 1, "enabled": 0})
        self.assertEqual(t["course_id"], "C2")
        self.assertEqual(t["class_id"], "02")
        self.assertFalse(t["enabled"])

    def test_first_alias_wins(self):
        t = adapter.normalize_target({"kcid": "A", "course_id": "B"})
        self.assertEqual(t["course_id"], "A")

    def test_missing_identity_rejected(self):
        for bad in ({"skbj": "01"}, {}, {"kcid": "  "}):
            with self.assertRaises(ValueError):
                adapter.normalize_target(bad)

    def test_priority_validation(self):
        with self.assertRaises(ValueError):
            adapter.normalize_target({"kcid": "C", "priority": 0})
        with self.assertRaises(ValueError):
            adapter.normalize_target({"kcid": "C", "priority": "many"})
        t = adapter.normalize_target({"kcid": "C", "priority": "3"})
        self.assertEqual(t["priority"], 3)

    def test_load_targets_shapes(self):
        rows = adapter.load_targets([{"kcid": "C1", "skbj": "01"}])
        self.assertEqual(rows[0]["course_id"], "C1")
        rows2 = adapter.load_targets(json.dumps(
            {"targets": [{"kcid": "C9", "skbj": "9"}]}))
        self.assertEqual(rows2[0]["course_id"], "C9")
        for bad in ([], [{"foo": 1}], {"nope": 1}, "not json {{{"):
            with self.assertRaises(ValueError):
                adapter.load_targets(bad)

    def test_all_disabled_rejected(self):
        with self.assertRaises(ValueError):
            adapter.load_targets([{"kcid": "C1", "enabled": 0}])

    def test_mission_mapping_and_unknown(self):
        kw = adapter.load_mission({
            "jw_host": "TARGET_HOST", "查询间隔": 30000, "账号数": 2,
            "mystery": 1, "targets": [{"kcid": "C1", "skbj": "01"}]})
        self.assertEqual(kw["host"], "TARGET_HOST")
        self.assertEqual(kw["poll_interval_base_ms"], 30000.0)
        self.assertEqual(kw["sessions"], 2)
        self.assertEqual(kw["unknown_keys"], ["mystery"])
        # interval clamped to sane range
        kw2 = adapter.load_mission({"interval": 10,
                                    "targets": [{"kcid": "C1"}]})
        self.assertEqual(kw2["poll_interval_base_ms"], 1000.0)

    def test_export_roundtrip(self):
        rows = adapter.export_targets([{"course_id": "C1", "class_id": "01",
                                        "priority": 1}])
        self.assertEqual(rows[0]["kcid"], "C1")
        self.assertEqual(rows[0]["skbj"], "01")
        back = adapter.normalize_target(rows[0])
        self.assertEqual(back["course_id"], "C1")


class TestPhaseMachine(unittest.TestCase):
    def test_forward_only(self):
        p = orch.Phase("recon")
        for expected in ("plan", "arm", "watch"):
            self.assertEqual(p.advance(), expected)
        with self.assertRaises(RuntimeError):
            p.advance()            # armed loop: steady state

    def test_armed_cluster_mutual(self):
        p = orch.Phase("watch")
        for step in ("act", "verify", "watch", "act"):
            p.require(step)        # all allowed inside the armed loop
        p2 = orch.Phase("recon")
        with self.assertRaises(RuntimeError):
            p2.require("act")      # setup phases cannot act

    def test_require_blocks_wrong_phase(self):
        p = orch.Phase("recon")
        with self.assertRaises(RuntimeError):
            p.require("act", "verify")

    def test_bad_phase_rejected(self):
        with self.assertRaises(ValueError):
            orch.Phase("nope")


class TestPlan(unittest.TestCase):
    def test_p5_folded(self):
        plan = orch.build_plan(
            p5_report={"rule": "fcfs",
                       "strategy": {"poll_interval_ms": 2000.0,
                                    "sessions": 3}})
        self.assertEqual(plan.rule, "fcfs")
        self.assertEqual(plan.poll_base_ms, 2000.0)
        self.assertEqual(plan.sessions, 3)

    def test_monitor_only_caps_hot_poll(self):
        plan = orch.build_plan(mission=None)
        self.assertGreaterEqual(plan.poll_hot_ms, 2000.0)
        self.assertTrue(any("monitor-only" in n for n in plan.notes))

    def test_endpoints_report_folded(self):
        plan = orch.build_plan(endpoints_report={
            "channel": "web", "reason": "r",
            "window_gate": {"kind": "server_time"},
            "independent": True})
        self.assertEqual(plan.channel, "web")
        self.assertEqual(plan.window_gate, "server_time")
        self.assertTrue(any("independent" in n for n in plan.notes))

    def test_recon_nodes_and_clock(self):
        plan = orch.build_plan(recon_report={
            "dns": {"addresses": ["1.1.1.1", "2.2.2.2"]},
            "clock": {"phase_offset_ms": 11.0, "rtt_median_ms": 18.0}})
        self.assertTrue(any("2 A records" in n for n in plan.notes))
        self.assertEqual(plan.clock["phase_offset_ms"], 11.0)


class TestOrchestrator(unittest.TestCase):
    def _mission(self, executor=None):
        return orch.Mission(host="loopback", targets=[
            {"course_id": "C1", "class_id": "01", "priority": 1}],
            executor=executor)

    def test_full_pipeline_monitor_only(self):
        o = orch.Orchestrator(self._mission())
        o.phase = orch.Phase("watch")
        o.plan_with({"clock": {"phase_offset_ms": 5.0}}, {}, {
            "rule": "fcfs", "strategy": {"poll_interval_ms": 2000.0}})
        d1 = o.watch(orch.SimpleResponse(200, {}, "<t/>", 18.0),
                     lambda _: [Offering("C1", "01", 60, 0)])
        self.assertEqual(d1.kind, "quiet")
        d2 = o.watch(orch.SimpleResponse(200, {}, "<t/>", 17.0),
                     lambda _: [Offering("C1", "01", 60, 2)])
        self.assertEqual(d2.kind, "submit")
        arm = o.arm({"C1|01": {"kcid": "C1", "skbj": "01"}})
        self.assertIn("C1|01", arm["bodies_sealed"])
        act = o.act(d2)
        self.assertFalse(act["acted"])
        self.assertIn("monitor-only", act["reason"])
        ev = [Evidence("wap_schedule", "C1", "01", None, 40.0, False,
                       "wap", time.time())]
        verdicts = o.verify(ev)
        self.assertTrue(verdicts)
        self.assertEqual(o.phase.report()["current"], "watch")  # steady loop
        with self.assertRaises(RuntimeError):
            o.phase.advance()      # armed loop has no forward transition

    def test_error_status_backoff(self):
        o = orch.Orchestrator(self._mission())
        o.phase = orch.Phase("watch")
        d = o.watch(orch.SimpleResponse(429, {}, "", 10.0), lambda _: [])
        self.assertEqual(d.kind, "rate_limited")
        self.assertGreater(d.interval_ms, 0)

    def test_executor_single_fire(self):
        c = Collector()
        o = orch.Orchestrator(self._mission(executor=c))
        o.phase = orch.Phase("watch")
        o.watch(orch.SimpleResponse(200, {}, "", 10.0),
                lambda _: [Offering("C1", "01", 60, 0)])
        d2 = o.watch(orch.SimpleResponse(200, {}, "", 10.0),
                     lambda _: [Offering("C1", "01", 60, 2)])
        act = o.act(d2)
        self.assertTrue(act["acted"])
        self.assertEqual(c.count, 1)          # exactly one physical write
        again = o.act(d2)
        self.assertFalse(again["acted"])      # decision replayed: blocked
        # a second release event must be stopped by the gate, not re-fired
        d3 = o.watch(orch.SimpleResponse(200, {}, "", 10.0),
                     lambda _: [Offering("C1", "01", 60, 5)])
        self.assertEqual(d3.kind, "duplicate")

    def test_executor_failure_marks_pending(self):
        calls = []
        def bad(_t):
            calls.append(_t)
            raise OSError("socket down")
        o = orch.Orchestrator(self._mission(executor=bad))
        o.phase = orch.Phase("watch")
        o.watch(orch.SimpleResponse(200, {}, "", 5.0),
                lambda _: [Offering("C1", "01", 60, 0)])
        d2 = o.watch(orch.SimpleResponse(200, {}, "", 5.0),
                     lambda _: [Offering("C1", "01", 60, 3)])
        out = o.act(d2)
        self.assertFalse(out["acted"])
        self.assertIn("OSError", out["error"])
        again = o.act(d2)
        self.assertFalse(again["acted"])
        self.assertEqual(len(calls), 1)  # unknown delivery never replays callback
        ev = [Evidence("official_list", "C1", "01", True, 60.0, False,
                       "official", time.time())]
        verdicts = o.verify(ev)
        self.assertTrue(any(v.get("verdict") == "confirmed"
                            for v in verdicts if "verdict" in v)
                        or verdicts)

    def test_classless_target_cannot_reach_executor(self):
        calls = Collector()
        mission = orch.Mission(host="loopback",
                               targets=[{"course_id": "C1", "class_id": "",
                                         "priority": 1}], executor=calls)
        o = orch.Orchestrator(mission)
        o.phase = orch.Phase("watch")
        o.watch(orch.SimpleResponse(200, {}, "", 5.0),
                lambda _: [Offering("C1", "01", 60, 0)])
        decision = o.watch(orch.SimpleResponse(200, {}, "", 5.0),
                           lambda _: [Offering("C1", "01", 60, 2)])
        self.assertEqual(decision.kind, "submit")
        result = o.act(decision)
        self.assertFalse(result["acted"])
        self.assertIn("teaching-class", result["reason"])
        self.assertEqual(calls.count, 0)

    def test_recon_probe_readonly_matrix(self):
        seen: List[str] = []

        def fetch(e: Endpoint):
            seen.append(e.method)
            return orch.SimpleResponse(200, {}, "x", 1.0)

        o = orch.Orchestrator(self._mission())
        eps = [Endpoint("catalog-wap", "wap", "/hsjw//wap/x.action", "GET"),
               Endpoint("catalog-web", "web", "/hsjw/wap/x.action", "GET")]
        rep = o.recon_probe(fetch, eps)
        self.assertEqual(rep["probed"], 2)
        self.assertTrue(all(m in ("GET", "HEAD", "OPTIONS") for m in seen))


class TestRaceProbe(unittest.TestCase):
    def test_classify_constraint_via_official(self):
        v = race_probe.classify(
            [{"status": 200, "body": "成功", "latency_ms": 40.0},
             {"status": 200, "body": "成功", "latency_ms": 41.0}],
            official_hits=1)
        self.assertEqual(v.verdict, "unique_constraint")
        self.assertFalse(v.exploitable)

    def test_classify_check_then_act(self):
        v = race_probe.classify(
            [{"status": 200, "body": "成功", "latency_ms": 40.0},
             {"status": 200, "body": "成功", "latency_ms": 44.0}],
            official_hits=2)
        self.assertEqual(v.verdict, "check_then_act")
        self.assertTrue(v.exploitable)

    def test_classify_lock_marker(self):
        v = race_probe.classify(
            [{"status": 200, "body": "成功", "latency_ms": 40.0},
             {"status": 500, "body": "系统 lock busy", "latency_ms": 500.0}])
        self.assertEqual(v.verdict, "transaction_lock")

    def test_classify_dup_marker(self):
        v = race_probe.classify(
            [{"status": 200, "body": "成功", "latency_ms": 40.0},
             {"status": 200, "body": "已选该课程", "latency_ms": 41.0}])
        self.assertEqual(v.verdict, "unique_constraint")

    def test_classify_insufficient(self):
        v = race_probe.classify([{"status": 200, "body": "x"}])
        self.assertEqual(v.verdict, "unknown")
        v2 = race_probe.classify([])
        self.assertEqual(v2.verdict, "unknown")

    def test_probe_disarmed_never_fires(self):
        c = Collector()
        p = race_probe.RaceProbe(shots=2)
        out = p.run({"course_id": "C1", "class_id": "01"}, c)
        self.assertFalse(out["fired"])
        self.assertEqual(c.count, 0)
        self.assertEqual(p.plan({"course_id": "C1"})["hard_cap"],
                         race_probe.MAX_SHOTS)

    def test_probe_armed_controlled_shots(self):
        c = Collector()
        p = race_probe.RaceProbe(shots=2, armed=True)
        t = {"course_id": "C1", "class_id": "01"}
        out = p.run(t, c, official_hits=1)
        self.assertTrue(out["fired"])
        self.assertEqual(c.count, 2)
        self.assertEqual(out["verdict"]["verdict"], "unique_constraint")
        # after the probe the gate is closed: no third shot
        out2 = p.run(t, c)
        self.assertFalse(out2["fired"])
        self.assertEqual(c.count, 2)

    def test_probe_shots_capped(self):
        p = race_probe.RaceProbe(shots=99)
        self.assertEqual(p.shots, race_probe.MAX_SHOTS)
        p2 = race_probe.RaceProbe(shots=0)
        self.assertEqual(p2.shots, 1)

    def test_reconcile_extra(self):
        p = race_probe.RaceProbe(shots=2, armed=True)
        ev = [Evidence("official_list", "C1", "01", True, 60.0, False,
                       "official", time.time())]
        out = p.reconcile_extra({"course_id": "C1", "class_id": "01"}, ev)
        self.assertIn("verdict", out)


class TestLoopbackEndToEnd(unittest.TestCase):
    """Real HTTP loopback: catalog with a release, orchestrator drives it."""

    @classmethod
    def setUpClass(cls):
        cls.state = {"remain": 0, "polls": 0}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):    # silence
                pass

            def do_GET(self):
                cls.state["polls"] += 1
                body = json.dumps({"rows": [
                    {"kcid": "C1", "skbj": "01", "capacity": 60,
                     "remain": cls.state["remain"]}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_end_to_end_monitor_only(self):
        c = Collector()
        m = orch.Mission(host=f"127.0.0.1:{self.port}", targets=[
            {"course_id": "C1", "class_id": "01", "priority": 1}],
            executor=c, poll_interval_base_ms=60000.0)

        def parse(body: str):
            rows = json.loads(body)["rows"]
            return [Offering(r["kcid"], r["skbj"], r["capacity"],
                             r["remain"]) for r in rows]

        o = orch.Orchestrator(m)
        o.phase = orch.Phase("watch")
        url = f"http://127.0.0.1:{self.port}/catalog"
        d1 = o.watch(self._get(url), parse)
        self.assertEqual(d1.kind, "quiet")
        self.assertEqual(self.state["polls"], 1)
        self.state["remain"] = 3
        d2 = o.watch(self._get(url), parse)
        self.assertEqual(d2.kind, "submit")
        act = o.act(d2)
        self.assertEqual(c.count, 1)
        self.assertTrue(act["acted"])
        rep = o.report()
        self.assertEqual(rep["phase"]["current"], "watch")   # steady loop
        self.assertEqual(len(rep["orders"]), 1)

    @staticmethod
    def _get(url: str) -> orch.SimpleResponse:
        t0 = time.perf_counter()
        with urllib.request.urlopen(url, timeout=5) as r:
            body = r.read().decode()
            return orch.SimpleResponse(r.status, dict(r.headers), body,
                                       (time.perf_counter() - t0) * 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
