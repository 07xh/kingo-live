"""NCWU transport must not replay a possibly committed enrollment."""
import sys
import os
import tempfile
import ssl
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hs_sniper import Jw, Sniper


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


if __name__ == '__main__':
    unittest.main()
