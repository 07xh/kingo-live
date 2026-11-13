"""An acknowledgement or a dropped response cannot prove enrollment."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qk.adapters import KingoAdapter, ZfsoftAdapter, GrabResult
from qk.engine import Engine
from qk.net import NetError, Resp


class Log:
    def __getattr__(self, _):
        return lambda *a, **kw: None


class SubmissionTests(unittest.TestCase):
    def adapter(self, cls):
        ad = cls({'user': 'fictional', 'courses': []}, {}, Log())
        ad.logged_in = True
        ad.session = ad.make_session('http://127.0.0.1')
        return ad

    def test_both_adapters_treat_dropped_response_as_unknown(self):
        for cls in (KingoAdapter, ZfsoftAdapter):
            with self.subTest(adapter=cls.name):
                ad = self.adapter(cls)
                with patch.object(ad.session, 'post', side_effect=NetError('lost response')):
                    r = ad.grab({'name': 'fictional', 'jxb_ids': 'T1'})
                self.assertEqual(r.code, 'unknown')
                self.assertFalse(r.ok)

    def test_success_ack_without_official_readback_is_unknown(self):
        for cls in (KingoAdapter, ZfsoftAdapter):
            with self.subTest(adapter=cls.name):
                ad = self.adapter(cls)
                with patch.object(ad.session, 'post', return_value=Resp(
                        status=200, url='http://127.0.0.1/submit', text='操作成功')):
                    r = ad.grab({'name': 'fictional', 'jxb_ids': 'T1'})
                self.assertEqual(r.code, 'unknown')
                self.assertFalse(r.ok)

    def test_unknown_stops_worker_without_retry_or_success_notification(self):
        ad = self.adapter(KingoAdapter)
        eng = Engine({'policy': {'max_seconds': .02, 'interval_ms': 1,
                                'backoff_cap_ms': 1, 'jitter': 0}}, Log())
        with patch.object(ad, 'grab', return_value=GrabResult(False, 'unknown', 'uncertain')) as grab:
            with patch.object(eng.notifier, 'hit') as hit:
                eng._worker(ad, {'name': 'fictional'})
        self.assertEqual(grab.call_count, 1)
        hit.assert_not_called()
        self.assertEqual(len(eng.results), 1)


if __name__ == '__main__':
    unittest.main()
