"""NCWU transport must not replay a possibly committed enrollment."""
import sys
import io
from contextlib import redirect_stdout
import os
import tempfile
import ssl
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hs_sniper import Jw, Sniper
import hs_xk


class TransportSafetyTests(unittest.TestCase):
    def test_tls_verification_enabled(self):
        jw = Jw('https://example.invalid/hsjw', 'fictional')
        self.assertEqual(jw._ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(jw._ctx.check_hostname)

    def test_post_disconnect_does_not_replay(self):
        jw = Jw('https://example.invalid/hsjw', 'fictional')
        with patch.object(jw, 'conn') as conn:
            conn.return_value.request.side_effect = TimeoutError('lost response')
            with self.assertRaises(TimeoutError):
                jw.post('/submit', 'id=fictional')
        self.assertEqual(conn.return_value.request.call_count, 1)

    def test_count_increase_is_not_proof_of_target_enrollment(self):
        engine = Sniper({'cookie': 'fictional', 'concurrency': 1})
        engine.xn, engine.xq, engine.xh = 'fictional', '1', 'fictional'
        engine.gate_info = {'status': '200', 'result': {'isValidTimerange': '1'}}
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {'KINGO_INTENT_DB': os.path.join(td, 'intents.sqlite3')}):
                with patch.object(engine.k, 'selected', side_effect=[{'yxms': 1}, {'yxms': 2}]):
                    with patch.object(engine, 'fire_one', return_value=(True, '操作成功', 1)) as fire:
                        with patch('hs_sniper.time.sleep'):
                            self.assertIsNone(engine.fire('fictional-course'))
                            self.assertIsNone(engine.fire('fictional-course'))
                            self.assertEqual(fire.call_count, 1)


    def test_ncw_u_same_course_class_hint_cannot_replay_through_other_cli(self):
        engine = Sniper({'cookie': 'fictional', 'concurrency': 1})
        engine.xn, engine.xq, engine.xh = '2026', '1', 'fictional'
        engine.gate_info = {'status': '200', 'result': {'isValidTimerange': '1'}}
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {'KINGO_INTENT_DB': os.path.join(td, 'intents.sqlite3')}):
                with patch.object(engine.k, 'selected', side_effect=[
                        {'yxms': 1}, {'yxms': 2}]):
                    with patch.object(engine, 'fire_one', return_value=(
                            True, 'unverified', 1)) as sent:
                        with patch('hs_sniper.time.sleep'):
                            self.assertIsNone(engine.fire('COURSE-101', '002'))
                            self.assertIsNone(engine.fire('COURSE-101', '003'))
                            sent.assert_called_once()
                with patch.object(hs_xk, 'get_cookie', return_value='fictional'), \
                     patch.object(hs_xk, 'pre_form', return_value=(
                         {'status': '200', 'result': {'isValidTimerange': '1'}},
                         {'xh': 'fictional', 'xn': '2026', 'xq': '1'})), \
                     patch.object(hs_xk, 'submit') as submit, \
                     patch.object(sys, 'argv', ['hs_xk.py', 'submit',
                                                '--kcdm', 'COURSE-101',
                                                '--skbjdm', '004']):
                    with self.assertRaises(SystemExit):
                        hs_xk.main()
                    submit.assert_not_called()

    def test_debug_receipts_never_log_private_response(self):
        engine = Sniper({'cookie': 'fictional', 'concurrency': 1})
        secret = 'PRIVATE_RESPONSE_SENTINEL'
        with patch.dict(os.environ, {'HS_DEBUG': '1'}), \
             patch.object(engine.k, 'submit',
                          return_value=(200, '{"message":"' + secret + '"}', 3.2)):
            output = io.StringIO()
            with redirect_stdout(output):
                ack, msg, duration = engine.fire_one('TEST-COURSE')
        self.assertTrue(ack)  # Transport receipt only, not confirmed enrollment.
        self.assertNotIn(secret, msg)
        self.assertNotIn(secret, output.getvalue())
        self.assertIn('待核验', msg)


if __name__ == '__main__':
    unittest.main()
