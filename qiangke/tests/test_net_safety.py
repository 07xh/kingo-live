"""Real loopback regressions: a lost response must never replay a write."""
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qk.net import NetError, Session


class Handler(BaseHTTPRequestHandler):
    writes = 0
    reads = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        type(self).writes += 1
        self.connection.close()  # server accepted the write, response was lost

    def do_GET(self):
        if self.path == '/write-get':
            type(self).writes += 1
            self.connection.close()
            return
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/destination')
            self.end_headers()
            return
        type(self).reads += 1
        if self.path == '/retry' and type(self).reads == 1:
            self.connection.close()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')


class NetSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%s' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        Handler.writes = Handler.reads = 0

    def test_lost_post_response_is_not_retried(self):
        with self.assertRaises(NetError):
            Session(self.base, retries=2).post('/submit', data={'id': 'fictional'})
        self.assertEqual(Handler.writes, 1)

    def test_read_can_retry_after_disconnect(self):
        self.assertEqual(Session(self.base, retries=1).get('/retry').status, 200)
        self.assertEqual(Handler.reads, 2)

    def test_get_enrollment_template_is_never_retried(self):
        from qk.adapters import KingoAdapter
        import logging
        ad = KingoAdapter({'user': 'fictional'}, {
            'base_url': self.base,
            'grab_request': {'method': 'GET', 'url': '/write-get'},
        }, logging.getLogger('test'))
        ad.logged_in = True
        ad.session = Session(self.base, retries=2)
        result = ad.grab({'name': 'fictional', 'jxb_ids': 'T1'})
        self.assertEqual(result.code, 'unknown')
        self.assertEqual(Handler.writes, 1)

    def test_no_redirect_flag_really_prevents_following(self):
        r = Session(self.base).get('/redirect', allow_redirects=False)
        self.assertEqual(r.status, 302)
        self.assertEqual(Handler.reads, 0)

    def test_absolute_url_cannot_bypass_remote_http_policy(self):
        with self.assertRaises(ValueError):
            Session('https://example.invalid').get('http://example.invalid/login')


if __name__ == '__main__':
    unittest.main()
