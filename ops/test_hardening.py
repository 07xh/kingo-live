"""Offline security and execution-path regressions for ops/. No school hosts."""
import argparse
import http.server
import json
import threading
import unittest

import endpoints
import fingerprint
import recon
import seat_events
import crosscheck
import offering_bridge
from types import SimpleNamespace


class Handler(http.server.BaseHTTPRequestHandler):
    seen = []
    def log_message(self, *args):
        pass
    def do_GET(self):
        Handler.seen.append((self.path, self.headers.get("Accept", "")))
        body = b'{"prohibit":"1"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HardeningTests(unittest.TestCase):
    def test_endpoints_real_readonly_path_and_custom_headers(self):
        Handler.seen = []
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            args = argparse.Namespace(
                demo_fetch=False, path="/hsjw/wap/mycourseschedule.action",
                window_param="xnxq", window_value="2026-2027-1",
                host="127.0.0.1", port=srv.server_port, scheme="http",
                cookie_env="", timeout=2.0)
            report = endpoints.run(args)
            self.assertGreater(report["classification"]["probed"], 0)
            self.assertEqual(report["classification"]["errors"], [])
            self.assertTrue(any("format=json" in path and accept ==
                                "application/json" for path, accept in Handler.seen))
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=2)

    def test_rawget_connection_failure_is_structured(self):
        # Invalid hostname avoids reliance on a closed live network port.
        client = recon.RawGet("127.0.0.1", 1, "http", timeout=0.25)
        try:
            result = client.get("/probe")
            self.assertEqual(result.status, 0)
            self.assertTrue(result.error)
        finally:
            client.close()

    def test_response_values_and_request_path_not_exported(self):
        item = fingerprint.SentRequest(
            method="GET",
            path="/student/123456?studentNo=123456&token=SECRET_QUERY",
            response_headers={
                "Set-Cookie": "SESSION=SECRET_COOKIE",
                "Location": "https://host/?token=SECRET_REDIRECT",
            },
            headers_sent=[["Cookie", "<injected>"]])
        report = item.public()
        data = json.dumps(report)
        for marker in ("123456", "SECRET_COOKIE", "SECRET_QUERY",
                       "SECRET_REDIRECT", "SESSION="):
            self.assertNotIn(marker, data)
        self.assertIn("response_header_names", report)
        self.assertNotIn("response_headers", report)

    def test_unknown_har_fields_redacted(self):
        har = {"log": {"entries": [{"request": {
            "url": "https://example.test/student?studentNo=SECRET_ID",
            "method": "GET",
            "headers": [{"name": "X-Campus-Identity", "value": "SECRET_HEADER"},
                        {"name": "Referer",
                         "value": "https://example.test/?student=SECRET_REF"}]
        }}]}}
        baseline = fingerprint.fingerprint_from_har(har)
        encoded = baseline.to_json()
        for secret in ("SECRET_ID", "SECRET_HEADER", "SECRET_REF"):
            self.assertNotIn(secret, encoded)

    def test_exact_scope_required_for_confirmation(self):
        target = {"course_id": "C1", "class_id": "01",
                  "semester": "term-A", "account_scope": "account-A"}
        Evidence = crosscheck.Evidence
        base = dict(semester="term-A", account_scope="account-A")
        no_class = Evidence("official_list", "C1", "", True, **base)
        self.assertNotEqual(crosscheck.fuse(target, [no_class]).verdict,
                            "confirmed")
        wrong_class = Evidence("official_list", "C1", "02", True, **base)
        self.assertNotEqual(crosscheck.fuse(target, [wrong_class]).verdict,
                            "confirmed")
        wrong_account = Evidence("official_list", "C1", "01", True,
                                 semester="term-A", account_scope="account-B")
        self.assertNotEqual(crosscheck.fuse(target, [wrong_account]).verdict,
                            "confirmed")
        wrong_term = Evidence("official_list", "C1", "01", True,
                              semester="term-B", account_scope="account-A")
        self.assertNotEqual(crosscheck.fuse(target, [wrong_term]).verdict,
                            "confirmed")
        exact = Evidence("official_list", "C1", "01", True, **base)
        self.assertEqual(crosscheck.fuse(target, [exact]).verdict, "confirmed")
        unscoped = {"course_id": "C1", "class_id": "01"}
        self.assertNotEqual(crosscheck.fuse(unscoped, [exact]).verdict,
                            "confirmed")
        self.assertNotIn("account-A", json.dumps(exact.public()))

    def test_independent_nonofficial_channels_not_authoritative(self):
        target = {"course_id": "C1", "class_id": "01",
                  "semester": "term-A", "account_scope": "account-A"}
        fields = dict(semester="term-A", account_scope="account-A")
        evidence = [
            crosscheck.Evidence("wap_schedule", "C1", "01", True, **fields),
            crosscheck.Evidence("credit_summary", "C1", "01", True, **fields),
            crosscheck.Evidence("zxxq", "C1", "01", True, **fields),
        ]
        self.assertEqual(crosscheck.fuse(target, evidence).verdict, "pending")
        repeated = evidence + [crosscheck.Evidence(
            "wap_schedule", "C1", "01", True, **fields)]
        self.assertEqual(crosscheck.fuse(target, repeated).positive,
                         crosscheck.fuse(target, evidence).positive)

    def test_canonical_snapshot_preserves_unknowns(self):
        record = SimpleNamespace(source="ncwu.plan", course_id="C1",
                                 class_id="01", course_name="Course",
                                 teacher=None, remaining=None,
                                 observed_at=None)
        normalized = offering_bridge.normalize_readonly(record)
        self.assertIsNone(normalized.remaining)
        self.assertEqual(normalized.identity_level, "exact")
        monitor = seat_events.ReleaseMonitor()
        self.assertEqual(monitor.observe([normalized]), [])
        valid = SimpleNamespace(source="ncwu.plan", course_id="C1",
                                class_id="01", course_name="Course",
                                remaining=3, observed_at=None)
        self.assertEqual([x.kind for x in monitor.observe([valid])], ["new"])
        self.assertEqual(monitor.observe([]), [])
        self.assertEqual(monitor.observe([normalized]), [])
        self.assertEqual(monitor.observe([valid]), [])
        self.assertEqual(len(monitor.snapshot), 1)
        self.assertIsNone(offering_bridge.normalize_readonly(
            SimpleNamespace(course_id="C1", class_id=None,
                            remaining=None)).class_id)

    def test_event_readback_and_unexpected_response(self):
        gate = seat_events.IntentGate()
        target = {"course_id": "C1", "class_id": "01"}
        gate.issue(target)
        self.assertEqual(gate.reconcile(target, False), "pending")
        self.assertFalse(gate.allow(target))
        driver = seat_events.EventDriver([target])
        self.assertEqual(driver.poll([], status=302).kind, "invalid_snapshot")
        self.assertEqual(driver.poll([], status=429, retry_after_ms=120000).interval_ms,
                         120000.0)
        ranker = seat_events.TargetRanker()
        self.assertEqual(ranker.reorder(
            [target], [seat_events.Offering("C1", "01", 60, 0)]), [])

    def test_query_values_redacted_in_readonly_diagnostics(self):
        path = "/hsjw/wap/course.action?studentNo=SECRET_STUDENT&token=SECRET_TOKEN"
        self.assertNotIn("SECRET_", recon.public_path(path))
        endpoint = endpoints.Endpoint("test", "wap",
                                     "/hsjw/wap/course.action",
                                     params={"studentNo": "SECRET_STUDENT"})
        self.assertNotIn("SECRET_STUDENT", json.dumps(endpoint.public()))
        result = endpoints.ProbeResult("test", "wap", endpoint.url(),
                                       status=200, window_marker="open")
        self.assertNotIn("SECRET_STUDENT", json.dumps(result.public()))
        classified = endpoints.classify([result])
        self.assertNotIn("SECRET_STUDENT", json.dumps(classified))

    def test_demo_distinguishes_real_confirmation_from_clues(self):
        report = crosscheck.demo()
        statuses = [v["verdict"] for v in report["verdicts"]]
        self.assertEqual(statuses, ["confirmed", "contradicted", "pending"])
        self.assertTrue(report["plan"]["confirm_threshold_is_advisory_only"])


if __name__ == "__main__":
    unittest.main()
