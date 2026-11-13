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

    def test_course_id_fences_all_teaching_class_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IntentJournal(Path(tmp) / "intents.sqlite3")
            args = {"school": "https://example.invalid/hsjw", "account": "fictional",
                    "term": "TEST-TERM"}
            self.assertTrue(store.reserve(**args, course=TARGET))
            renamed = {**TARGET, "name": "Renamed", "teacher": "Different",
                       "xkid": "CLASS-02"}
            self.assertFalse(store.reserve(**args, course=renamed))
            # Different canonical course IDs are not confused even if a class
            # code such as "001" is reused by different classes.
            other_course = {**TARGET, "kch_id": "DIFFERENT"}
            self.assertTrue(store.reserve(**args, course=other_course))
            self.assertTrue(store.reserve(**{**args, "term": "OTHER-TERM"}, course=TARGET))
            self.assertTrue(store.reserve(**{**args, "account": "other-user"}, course=TARGET))
            only_code = {"kch_id": "COURSE-001", "name": "Before"}
            self.assertTrue(store.reserve(**args, course=only_code))
            self.assertFalse(store.reserve(**args, course={**only_code, "name": "After",
                                                           "xkid": "002"}))
            self.assertFalse(store.reserve(**args, course={"kch_id": "COURSE-001",
                                                           "xkid": "None"}))

    def test_missing_class_vs_explicit_class_share_ncwu_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IntentJournal(Path(tmp) / "intents.sqlite3")
            args = {"school": "https://example.invalid/hsjw", "account": "fictional",
                    "term": "2026-1"}
            # hs_xk.py may omit --skbjdm while hs_sniper supplies a hint.
            self.assertTrue(store.reserve(**args, course={"kch_id": "NCWU-101",
                                                           "xkid": "None"}))
            self.assertFalse(store.reserve(**args, course={"kch_id": "NCWU-101",
                                                            "xkid": "002"}))

    def test_older_class_fingerprint_still_prevents_replay(self):
        import hashlib
        import hmac
        import json
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            store = IntentJournal(Path(tmp) / "intents.sqlite3")
            identity = json.dumps(["example.invalid", "fictional", "2026-1",
                                   "class", "002"], ensure_ascii=False,
                                  separators=(",", ":"))
            legacy_fp = hmac.new(store.salt, identity.encode("utf-8"),
                                 hashlib.sha256).hexdigest()
            with sqlite3.connect(store.path) as db:
                db.execute("INSERT INTO attempts (fingerprint, state) VALUES (?, ?)",
                           (legacy_fp, "pending"))
            self.assertFalse(store.reserve(school="https://example.invalid",
                                           account="fictional", term="2026-1",
                                           course={"kch_id": "NCWU-101",
                                                   "xkid": "002"}))

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
