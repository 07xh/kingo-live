"""SQLite intent fence must protect against restart and simultaneous workers."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qk.intent_journal import IntentJournal
from qk.adapters import GrabResult
from qk.engine import Engine


TARGET = {"name": "Synthetic Safety Course", "kch_id": "TEST-101", "xkid": "CLASS-01"}


class JournalTests(unittest.TestCase):
    def test_reopen_and_cross_process_style_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "intents.sqlite3"
            first = IntentJournal(db)
            args = {"school": "example.invalid", "account": "fictional-user",
                    "term": "TEST-TERM", "course": TARGET}
            self.assertTrue(first.reserve(**args))
            self.assertFalse(IntentJournal(db).reserve(**args))
            data = db.read_bytes()
            for secret in (b"fictional-user", b"TEST-101", b"CLASS-01"):
                self.assertNotIn(secret, data)

    def test_competing_threads_one_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "intents.sqlite3"
            outcomes = []
            lock = threading.Lock()

            def try_reserve():
                value = IntentJournal(db).reserve(school="example.invalid",
                           account="fictional", term="2026-1", course=TARGET)
                with lock:
                    outcomes.append(value)

            threads = [threading.Thread(target=try_reserve) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(outcomes.count(True), 1)
            self.assertEqual(outcomes.count(False), 5)

    def test_class_identity_ignores_display_names_and_other_course_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IntentJournal(Path(tmp) / "intents.sqlite3")
            args = {"school": "https://example.invalid/hsjw", "account": "fictional",
                    "term": "TEST-TERM"}
            self.assertTrue(store.reserve(**args, course=TARGET))
            renamed = {**TARGET, "name": "Renamed", "teacher": "Different",
                       "kch_id": "DIFFERENT"}
            self.assertFalse(store.reserve(**args, course=renamed))
            self.assertFalse(store.reserve(**args, course={"jxb_ids": "CLASS-01"}))
            self.assertTrue(store.reserve(**{**args, "term": "OTHER-TERM"}, course=TARGET))

    def test_missing_term_or_class_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IntentJournal(Path(tmp) / "intents.sqlite3")
            args = {"school": "https://example.invalid", "account": "fictional",
                    "term": "TEST-TERM", "course": TARGET}
            for delta in ({"school": ""}, {"school": "unknown"}, {"account": ""},
                          {"term": ""}, {"term": "unknown"},
                          {"course": {"name": "Display only"}},
                          {"course": {"xkid": "A", "jxb_ids": "B"}}):
                with self.subTest(delta=delta), self.assertRaises(ValueError):
                    store.reserve(**{**args, **delta})

    def test_engine_missing_term_or_class_sends_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"KINGO_INTENT_DB": str(Path(tmp) / "intents.sqlite3")}):
                adapter = Mock()
                adapter.acct = {"user": "fictional", "name": "fictional"}
                adapter.blocked = False
                cfg = {"system": {"base_url": "https://example.invalid"}, "policy": {}}
                Engine(cfg, Mock())._worker(adapter, TARGET)
                cfg["system"]["xnxq"] = "TEST-TERM"
                Engine(cfg, Mock())._worker(adapter, {"name": "Display only"})
                adapter.grab.assert_not_called()

    def test_engine_restart_must_not_send_second_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"KINGO_INTENT_DB": str(Path(tmp) / "intents.sqlite3")}):
                cfg = {"system": {"base_url": "https://example.invalid", "xnxq": "TEST-TERM"}, "policy": {}}
                adapter = Mock()
                adapter.acct = {"user": "fictional", "name": "fictional"}
                adapter.blocked = False
                adapter.grab.return_value = GrabResult(False, "unknown", "unverified")
                first = Engine(cfg, Mock())
                first._worker(adapter, TARGET)
                restarted = Engine(cfg, Mock())
                restarted._worker(adapter, TARGET)
                self.assertEqual(adapter.grab.call_count, 1)
                self.assertEqual(first.results[0]["code"], "unknown")
                self.assertEqual(restarted.results[0]["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
