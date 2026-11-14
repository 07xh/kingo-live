"""An acknowledgement or a dropped response cannot prove enrollment."""
import os
import tempfile
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


    def test_already_enrolled_message_cannot_be_success_without_readback(self):
        for cls in (KingoAdapter, ZfsoftAdapter):
            with self.subTest(adapter=cls.name):
                ad = self.adapter(cls)
                for reply in ("已选", "已经选", "重复选课", "操作成功", "选课成功"):
                    with self.subTest(reply=reply), patch.object(
                        ad.session, 'post',
                        return_value=Resp(
                            status=200, url='http://127.0.0.1/submit',
                            text=reply + " PRIVATE_RESPONSE_SENTINEL"
                        )
                    ):
                        r = ad.grab({'name': 'fictional', 'jxb_ids': 'T1'})
                        self.assertEqual(r.code, 'unknown')
                        self.assertFalse(r.ok)
                        self.assertEqual(r.raw, '')
                        self.assertNotIn('PRIVATE_RESPONSE_SENTINEL', r.msg)

    def test_rate_limit_response_stops_and_does_not_show_raw_body(self):
        for cls in (KingoAdapter, ZfsoftAdapter):
            with self.subTest(adapter=cls.name):
                ad = self.adapter(cls)
                with patch.object(ad.session, 'post', return_value=Resp(
                    status=429, url='http://127.0.0.1/submit',
                    text='PRIVATE_RESPONSE_SENTINEL 操作过于频繁'
                )):
                    r = ad.grab({'name': 'fictional', 'jxb_ids': 'T1'})
                self.assertEqual(r.code, 'unknown')
                self.assertFalse(r.ok)
                self.assertIn('429', r.msg)
                self.assertEqual(r.raw, '')
                self.assertNotIn('PRIVATE_RESPONSE_SENTINEL', r.msg)

    def test_legacy_class_list_serializes_one_scalar_in_every_alias(self):
        ad = self.adapter(KingoAdapter)
        _, fields, body, _ = ad._render_tpl(
            {"url": "/synthetic", "form": {
                "xkid": "{xkid}", "legacy": "{jxb_ids}",
                "alias": "{class_id}", "course": "{kch_id}",
            }},
            {"kch_id": "COURSE-01", "jxb_ids": ["COURSE-01-009"]},
        )
        self.assertIsNone(body)
        self.assertEqual(fields, {
            "xkid": "COURSE-01-009", "legacy": "COURSE-01-009",
            "alias": "COURSE-01-009", "course": "COURSE-01",
        })
        self.assertNotIn("[", repr(fields["xkid"]))

    def test_conflicting_or_ambiguous_class_refuses_http_write(self):
        ad = self.adapter(KingoAdapter)
        invalid = (
            {"xkid": "CLASS-01", "jxb_ids": ["CLASS-02"]},
            {"jxb_ids": ["CLASS-01", "CLASS-02"]},
            {"jxb_ids": []},
            {"xkid": ["CLASS-01"]},
            {"xkid": "unknown"},
            {},
        )
        for bad in invalid:
            with self.subTest(bad=bad), patch.object(ad, "_send") as sending:
                result = ad.grab({"kch_id": "COURSE-01", **bad})
                self.assertFalse(result.ok)
                self.assertIs(result.write_attempted, False)
                self.assertEqual(result.attempts, 0)
                sending.assert_not_called()

    def test_readonly_template_remains_usable_without_class_id(self):
        ad = self.adapter(KingoAdapter)
        url, form, body, headers = ad._render_tpl(
            {"url": "/synthetic/list", "form": {"q": "all"}}, {})
        self.assertEqual(url, "/synthetic/list")
        self.assertEqual(form, {"q": "all"})
        self.assertIsNone(body)
        self.assertEqual(headers, {})

    def test_unknown_stops_worker_without_retry_or_success_notification(self):
        ad = self.adapter(KingoAdapter)
        eng = Engine({'system': {'base_url': 'https://example.invalid',
                                 'xnxq': 'TEST-TERM'},
                      'policy': {'max_seconds': .02, 'interval_ms': 1,
                                 'backoff_cap_ms': 1, 'jitter': 0}}, Log())
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {'KINGO_INTENT_DB': os.path.join(td, 'intents.sqlite3')}):
                with patch.object(ad, 'grab', return_value=GrabResult(False, 'unknown', 'uncertain')) as grab:
                    with patch.object(eng.notifier, 'hit') as hit:
                        eng._worker(ad, {'name': 'fictional', 'xkid': 'CLASS-01'})
        self.assertEqual(grab.call_count, 1)
        hit.assert_not_called()
        self.assertEqual(len(eng.results), 1)


if __name__ == '__main__':
    unittest.main()
