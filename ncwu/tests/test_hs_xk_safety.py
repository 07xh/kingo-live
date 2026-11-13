"""Single-shot and truthful receipt safety for the standalone NCWU client."""
import contextlib
import io
import os
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hs_xk


class HsXkSafetyTests(unittest.TestCase):
    def test_closed_window_never_sends_submit(self):
        with patch.object(hs_xk, "get_cookie", return_value="fictional"), \
             patch.object(hs_xk, "pre_form", return_value=(
                 {"status": "400", "result": {"isValidTimerange": "0"}}, {})), \
             patch.object(hs_xk, "submit") as send, \
             patch.object(sys, "argv", ["hs_xk.py", "submit", "--kcdm", "TEST1"]):
            with self.assertRaises(SystemExit):
                hs_xk.main()
            send.assert_not_called()

    def test_explicitly_open_window_sends_once(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"KINGO_INTENT_DB": os.path.join(td, "intents.sqlite3")}), \
                 patch.object(hs_xk, "get_cookie", return_value="fictional"), \
                 patch.object(hs_xk, "pre_form", return_value=(
                     {"status": "200", "result": {"isValidTimerange": "1"}},
                     {"xh": "fictional", "xn": "2026", "xq": "1"})), \
                 patch.object(hs_xk, "submit", return_value=b"{}") as send, \
                 patch.object(hs_xk, "judge"), \
                 patch.object(sys, "argv", ["hs_xk.py", "submit", "--kcdm", "TEST1"]):
                hs_xk.main()
                with self.assertRaises(SystemExit):
                    hs_xk.main()
                send.assert_called_once()

    def test_success_words_are_not_enrollment_confirmation(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hs_xk.judge('{"message":"选课成功"}'.encode("gb18030"))
        self.assertIn("尚未按官方已选课程逐项核验", out.getvalue())
        self.assertNotIn("判定：成功", out.getvalue())


if __name__ == "__main__":
    unittest.main()
