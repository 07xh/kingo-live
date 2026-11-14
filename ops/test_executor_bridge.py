"""test_executor_bridge - offline regression for executor wiring.

All tests run against fakes and the loopback; no real network, no real
credentials, no physical submission to any endpoint.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

import executor_bridge as eb  # noqa: E402
from crosscheck import Evidence  # noqa: E402


class FakeResult:
    def __init__(self, ok=True, code="success", msg="faked",
                 write_attempted=True):
        self.ok = ok
        self.code = code
        self.msg = msg
        self.attempts = 1
        self.elapsed = 0.005
        self.raw = ""
        self.write_attempted = write_attempted


class FakeQkAdapter:
    """Records grab calls; lifecycle counters must move exactly once."""

    def __init__(self, result: FakeResult = None):
        self.logged_in = False
        self.calls: List[dict] = []
        self.opens = 0
        self.logins = 0
        self.prepares = 0
        self._result = result or FakeResult()

    def open(self):
        self.opens += 1

    def login(self):
        self.logins += 1
        self.logged_in = True
        return True

    def prepare(self, courses):
        self.prepares += 1

    def grab(self, course):
        self.calls.append(dict(course))
        return self._result


class FakeSniperModule:
    class Sniper:
        def __init__(self, cfg):
            self.cfg = cfg
            self.fires: List[tuple] = []
            self.prepared = 0
            self.gate_open = True

        def prepare(self):
            self.prepared += 1
            return self.gate_open

        def fire_one(self, kcdm, skbjdm_hint="", tj="0", jw=None):
            self.fires.append((kcdm, skbjdm_hint, tj))
            return True, "transport ack only", 12.5


class TestQkExecutor(unittest.TestCase):
    def test_lifecycle_runs_once_and_maps_fields(self):
        ad = FakeQkAdapter()
        ex = eb.QkExecutor(ad, kind="kingo",
                           acct={"courses": [{"kch_id": "X"}]})
        receipt = ex({"course_id": "C1", "class_id": "J1",
                      "term": "2026-1"})
        self.assertEqual(ad.opens, 1)
        self.assertEqual(ad.logins, 1)
        self.assertEqual(ad.prepares, 1)
        # second call must not re-run open/login/prepare
        ex({"course_id": "C2", "class_id": "J2"})
        self.assertEqual(ad.opens, 1)
        self.assertEqual(ad.logins, 1)
        self.assertEqual(ad.prepares, 1)
        self.assertEqual(len(ad.calls), 2)
        c = ad.calls[0]
        self.assertEqual(c["kch_id"], "C1")
        self.assertEqual(c["jxb_ids"], ["J1"])
        self.assertEqual(c["xkid"], "J1")
        # receipt shape
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["code"], "success")
        self.assertIs(receipt["write_attempted"], True)
        self.assertEqual(receipt["executor"], "qk:kingo")
        self.assertEqual(receipt["target"]["course_id"], "C1")

    def test_invalid_or_conflicting_class_is_proven_unsent(self):
        ad = FakeQkAdapter()
        ex = eb.QkExecutor(ad)
        for target in (
            {"course_id": "C1", "class_id": ""},
            {"course_id": "C1", "class_id": ["CLASS-01"]},
            {"course_id": "C1", "class_id": "CLASS-01",
             "xkid": "OTHER-CLASS"},
            {"course_id": "C1", "class_id": "unknown"},
            {"course_id": "", "class_id": "CLASS-01"},
        ):
            with self.subTest(target=target):
                receipt = ex(target)
                self.assertFalse(receipt["ok"])
                self.assertIs(receipt["write_attempted"], False)
        self.assertEqual(ad.calls, [])
        self.assertEqual(ad.opens, 0)

    def test_login_failure_raises_before_write(self):
        ad = FakeQkAdapter()
        ad.login = lambda: False
        ex = eb.QkExecutor(ad)
        receipt = ex({"course_id": "C1", "class_id": "J1"})
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["code"], "error")
        self.assertIs(receipt["write_attempted"], False)
        self.assertEqual(len(ad.calls), 0)

    def test_exec_fault_after_transport_is_unknown_delivery(self):
        class Boom(FakeQkAdapter):
            def grab(self, course):
                raise RuntimeError("socket dead")

        ex = eb.QkExecutor(Boom())
        receipt = ex({"course_id": "C", "class_id": "J"})
        self.assertEqual(receipt["code"], "unknown")
        self.assertIn("RuntimeError", receipt["msg"])
        self.assertIsNone(receipt["write_attempted"])

    def test_lifecycle_login_failure_is_proven_unsent(self):
        ad = FakeQkAdapter()
        ad.login = lambda: False
        receipt = eb.QkExecutor(ad)({"course_id": "C1", "class_id": "J1"})
        self.assertEqual(receipt["code"], "error")
        self.assertIs(receipt["write_attempted"], False)
        self.assertEqual(ad.calls, [])

    def test_lazy_import_via_injected_factory(self):
        made = []

        def factory(kind, acct, opts, log):
            made.append(kind)
            return FakeQkAdapter()

        ex = eb.QkExecutor(kind="zfsoft", acct={"user": "u"},
                           factory=factory)
        receipt = ex({"course_id": "C", "class_id": "J"})
        self.assertEqual(made, ["zfsoft"])
        self.assertEqual(receipt["executor"], "qk:zfsoft")


class TestSniperExecutor(unittest.TestCase):
    def test_class_only_hint_never_authorizes_sniper_write(self):
        module = FakeSniperModule()
        ex = eb.SniperExecutor(importer=lambda: module,
                               cfg={"cookie": "x"})
        receipt = ex({"course_id": "080118", "class_id": "01"})
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["code"], "error")
        self.assertIs(receipt["write_attempted"], False)
        # No login/preparation/network is started for an unsupported write.
        self.assertIsNone(ex._sniper)
        direct = ex._fire({"course_id": "080118", "class_id": "01"})
        self.assertFalse(direct["ok"])
        self.assertEqual(direct["code"], "unsupported_class_write")
        self.assertIs(direct["write_attempted"], False)

    def test_gate_closed_blocks_write(self):
        sniper = FakeSniperModule.Sniper({"cookie": "x"})
        sniper.gate_open = False
        ex = eb.SniperExecutor(sniper=sniper)
        receipt = ex({"course_id": "C", "class_id": "J"})
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["code"], "error")
        self.assertIs(receipt["write_attempted"], False)
        self.assertEqual(ex._sniper.fires, [])


class TestRepositoryImports(unittest.TestCase):
    """Exercise non-injected imports; fake constructors prevent any I/O."""

    def test_real_qk_module_path_with_fake_factory(self):
        from unittest.mock import patch
        eb._enable_repository_imports()
        from qiangke.qk import adapters
        with patch.object(adapters, "make", return_value=FakeQkAdapter()) as make:
            receipt = eb.QkExecutor()({"course_id": "C", "class_id": "J"})
        self.assertEqual(receipt["code"], "success")
        self.assertTrue(make.called)
        self.assertTrue(hasattr(make.call_args.args[3], "ok"))

    def test_real_ncwu_module_path_with_fake_sniper(self):
        from unittest.mock import patch
        eb._enable_repository_imports()
        from ncwu import hs_sniper
        with patch.object(hs_sniper, "Sniper", FakeSniperModule.Sniper):
            receipt = eb.SniperExecutor(cfg={"cookie": "FAKE"})(
                {"course_id": "C", "class_id": "J"})
        self.assertEqual(receipt["code"], "error")
        self.assertIs(receipt["write_attempted"], False)


class TestEvidenceMapping(unittest.TestCase):
    def test_all_executor_codes_leave_official_presence_unknown(self):
        # A transport status is never an official per-class readback.
        for code in ("success", "enrolled", "full", "conflict",
                     "error", "auth", "unknown"):
            with self.subTest(code=code):
                r = eb.make_receipt({"course_id": "C", "class_id": "J"},
                                    ok=True, code=code, msg="",
                                    elapsed_ms=1.0, write_attempted=True,
                                    executor="t")
                ev = eb.receipt_to_evidence(r)
                self.assertIsNone(ev.present)
                self.assertEqual(ev.course_id, "C")
                self.assertEqual(ev.class_id, "J")

    def test_executor_receipt_cannot_impersonate_official_readback(self):
        from crosscheck import fuse
        target = {"course_id": "C", "class_id": "J",
                  "semester": "synthetic-term",
                  "account_scope": "synthetic-account"}
        receipt = eb.make_receipt(
            {**target, "term": "synthetic-term", "account": "synthetic-account"},
            ok=True, code="enrolled", msg="", elapsed_ms=2.0,
            write_attempted=True, executor="qk:kingo")
        evidence = eb.receipt_to_evidence(receipt, channel="official_list")
        self.assertIsNone(evidence.present)
        self.assertNotEqual(fuse(target, [evidence]).verdict, "confirmed")

    def test_evidence_stays_context_only(self):
        """Executor receipts are transport evidence, never enrollment proof.

        The executor channel is deliberately absent from CHANNEL_WEIGHTS:
        an HTTP acknowledgement must not substitute for an official
        selected-course read-back. fuse() must therefore stay "unknown".
        """
        from crosscheck import fuse
        r = eb.make_receipt({"course_id": "C", "class_id": "J"},
                            ok=True, code="success", msg="",
                            elapsed_ms=3.0, write_attempted=True,
                            executor="qk:kingo")
        ev = eb.receipt_to_evidence(r)
        v = fuse({"course_id": "C", "class_id": "J"}, [ev])
        self.assertEqual(v.verdict, "unknown")

    def test_unknown_code_does_not_claim_failure(self):
        from crosscheck import fuse
        r = eb.make_receipt({"course_id": "C", "class_id": "J"},
                            ok=True, code="unknown", msg="ack only",
                            elapsed_ms=3.0, write_attempted=True,
                            executor="hs_sniper")
        ev = eb.receipt_to_evidence(r)
        v = fuse({"course_id": "C", "class_id": "J"}, [ev])
        self.assertNotEqual(v.verdict, "absent")


class TestBuildExecutor(unittest.TestCase):
    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            eb.build_executor({"kind": "typo"})

    def test_kind_passthrough(self):
        ex = eb.build_executor({"kind": "qk", "adapter": FakeQkAdapter()})
        self.assertTrue(callable(ex))
        self.assertEqual(ex.name, "qk:kingo")


class LoopbackHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        body = json.dumps({"status": 200}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class TestOrchestratorEndToEnd(unittest.TestCase):
    """Full loopback: bridge as Mission.executor drives a real submit."""

    def _run(self):
        import tempfile
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), LoopbackHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        try:
            import orchestrator as orch
            from seat_events import Decision, Offering

            ad = FakeQkAdapter()
            executor = eb.QkExecutor(ad, kind="kingo")
            mission = orch.Mission(
                targets=[{"course_id": "C9", "class_id": "J9",
                          "priority": 1}],
                verified_school_origin="https://synthetic.invalid",
                verified_account_id="fictional-bridge-loopback",
                enrollment_semester="2026-2027-1",
                shared_intent_db=str(Path(tempdir.name) / "shared.sqlite3"),
                executor=executor)
            o = orch.Orchestrator(mission)
            o.plan_with()          # recon -> plan
            o.plan_with()          # plan -> arm
            o.arm({})              # arm -> watch
            offer = orch.Offering(course_id="C9", class_id="J9",
                                  capacity=3, remain=1)
            resp = orch.SimpleResponse(status=200, body="x", ttfb_ms=10.0)
            d = o.watch(resp, lambda body: [offer])
            self.assertIn(d.kind, ("submit", "wait"))
            if d.kind == "wait":
                d = Decision(kind="submit", action="fire",
                             target={"course_id": "C9", "class_id": "J9",
                                     "priority": 1})
            order = o.act(d)
            self.assertTrue(order["acted"], order)
            self.assertEqual(len(ad.calls), 1)
            # re-fire must be refused
            again = o.act(d)
            self.assertFalse(again["acted"])
            self.assertEqual(len(ad.calls), 1)
            # executor receipt is context-only: verify must NOT confirm
            receipt = eb.receipt_to_evidence(
                eb.make_receipt({"course_id": "C9", "class_id": "J9"},
                                ok=True, code="success", msg="",
                                elapsed_ms=1.0, write_attempted=True,
                                executor="qk:kingo"))
            verdicts = o.verify([receipt])
            confirmed = [v for v in verdicts if isinstance(v, dict)
                         and v.get("verdict") == "enrolled"]
            self.assertEqual(confirmed, [],
                             "executor ack must never confirm enrollment")
        finally:
            httpd.shutdown()

    def test_loop(self):
        self._run()


class TestDemo(unittest.TestCase):
    def test_demo_selfcheck(self):
        out = eb.demo()
        self.assertTrue(out["ok"])
        self.assertFalse(out["officially_confirmed"])
        self.assertIsNone(out["evidence"]["present"])


if __name__ == "__main__":
    unittest.main()
