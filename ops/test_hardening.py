"""Offline security and execution-path regressions for ops/. No school hosts."""
import argparse
import http.server
import json
import threading
import unittest

import endpoints
import fingerprint
import recon


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


if __name__ == "__main__":
    unittest.main()
