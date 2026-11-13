"""SQLite intent fence must protect against restart and simultaneous workers."""
import os
import sys
import tempfile
from contextlib import closing
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
            with closing(sqlite3.connect(store.path)) as db, db:
                db.execute("INSERT INTO attempts (fingerprint, state) VALUES (?, ?)",
                           (legacy_fp, "pending"))
            self.assertFalse(store.reserve(school="https://example.invalid",
                                           account="fictional", term="2026-1",
                                           course={"kch_id": "NCWU-101",
                                                   "xkid": "002"}))

    def test_term_spellings_share_one_intent_across_ncwu_and_qiangke(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "intents.sqlite3"
            first = IntentJournal(db)
            kwargs = {"school": "https://jwmis.ncwu.edu.cn", "account": "fictional",
                      "course": {"kch_id": "COURSE-01", "xkid": "CLASS-01"}}
            self.assertTrue(first.reserve(**kwargs, term="2026-1"))
            self.assertFalse(IntentJournal(db).reserve(**kwargs, term="2026/1"))
            self.assertFalse(IntentJournal(db).reserve(**kwargs, term="2026,1"))
            self.assertTrue(IntentJournal(db).reserve(**kwargs, term="2026-2"))
            self.assertTrue(IntentJournal(db).reserve(**kwargs, term="2027/1"))

    def test_old_term_and_class_fingerprint_blocks_after_upgrade(self):
        import hashlib
        import hmac
        import json
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "intents.sqlite3"
            journal = IntentJournal(db)
            identity = json.dumps(["jwmis.ncwu.edu.cn", "fictional", "2026-1",
                                   "class", "CLASS-01"],
                                  ensure_ascii=False, separators=(",", ":"))
            old_fp = hmac.new(journal.salt, identity.encode("utf-8"),
                              hashlib.sha256).hexdigest()
            with closing(sqlite3.connect(db)) as con, con:
                con.execute("INSERT INTO attempts (fingerprint, state) "
                            "VALUES (?, 'pending')", (old_fp,))
            self.assertFalse(journal.reserve(
                school="https://jwmis.ncwu.edu.cn", account="fictional",
                term="2026/1", course={"kch_id": "COURSE-01", "xkid": "CLASS-01"}))

    def test_cancel_definitely_unsent_only_removes_own_fence(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = IntentJournal(Path(tmp) / "intents.sqlite3")
            args = {"school": "example.invalid", "account": "fictional",
                    "term": "2026-1", "course": TARGET}
            self.assertTrue(journal.reserve(**args))
            self.assertTrue(journal.cancel_definitely_unsent(**args))
            self.assertFalse(journal.cancel_definitely_unsent(**args))
            self.assertTrue(journal.reserve(**{**args, "term": "2026/1"}))
            # An unrelated account can never release a pending intent.
            self.assertFalse(journal.cancel_definitely_unsent(
                **{**args, "account": "someone-else"}))
            self.assertFalse(journal.reserve(**args))

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

    def test_engine_pre_send_failure_is_not_a_physical_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"KINGO_INTENT_DB": str(Path(tmp) / "intents.sqlite3")}):
                cfg = {"system": {"base_url": "https://example.invalid",
                                  "xnxq": "TEST-TERM"}, "policy": {}}
                adapter = Mock()
                adapter.acct = {"user": "fictional", "name": "fictional"}
                adapter.blocked = False
                adapter.logged_in = True
                adapter.grab.return_value = GrabResult(
                    False, "auth", "login failed", write_attempted=False)
                first = Engine(cfg, Mock())
                first._worker(adapter, TARGET)
                self.assertEqual(first.results[0]["attempts"], 0)
                self.assertIs(first.results[0]["write_attempted"], False)
                self.assertEqual(first.results[0]["code"], "auth")
                # A confirmed no-send result cannot permanently fence a course.
                restarted = Engine(cfg, Mock())
                restarted._worker(adapter, TARGET)
                self.assertEqual(adapter.grab.call_count, 2)

    def test_engine_failed_login_before_reservation_is_unsent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"KINGO_INTENT_DB": str(Path(tmp) / "intents.sqlite3")}):
                cfg = {"system": {"base_url": "https://example.invalid",
                                  "xnxq": "TEST-TERM"}, "policy": {}}
                adapter = Mock()
                adapter.acct = {"user": "fictional", "name": "fictional"}
                adapter.blocked = False
                adapter.logged_in = False
                eng = Engine(cfg, Mock())
                eng._worker(adapter, TARGET)
                adapter.grab.assert_not_called()
                self.assertEqual(eng.results[0]["attempts"], 0)
                self.assertFalse((Path(tmp) / "intents.sqlite3").exists())

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
