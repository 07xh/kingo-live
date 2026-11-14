"""Shared SQLite intent contract with all legacy NCWU/Qiangke writers.

Offline-only: synthetic school, student, term and class identifiers.
"""
import tempfile
import threading
import unittest
from pathlib import Path

from contracts.intent_bridge import reserve_shared_intent, shared_intent_path
from qiangke.qk.intent_journal import IntentJournal

SCOPE = {"school": "https://synthetic.invalid/hsjw",
         "account": "fictional-student", "term": "2026-2027-1"}
COURSE = {"kch_id": "COURSE-01", "xkid": "CLASS-01"}


class SharedIntentBridgeTests(unittest.TestCase):
    def test_bridge_blocks_previous_ncwu_reservation_in_same_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shared.sqlite3"
            old = IntentJournal(path)
            self.assertTrue(old.reserve(**SCOPE, course=COURSE))
            self.assertFalse(reserve_shared_intent(
                **SCOPE, course_id="COURSE-01", class_id="CLASS-01", path=path))
            # NCWU write action is course-level, so switching class cannot
            # re-authorize another write under the same course code.
            self.assertFalse(reserve_shared_intent(
                **SCOPE, course_id="COURSE-01", class_id="CLASS-02", path=path))

    def test_bridge_reservation_blocks_old_qiangke_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shared.sqlite3"
            self.assertTrue(reserve_shared_intent(
                **SCOPE, course_id="COURSE-01", class_id="CLASS-01", path=path))
            self.assertFalse(IntentJournal(path).reserve(
                **{**SCOPE, "term": "2026-2027/1"}, course=COURSE))

    def test_different_account_semester_and_school_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shared.sqlite3"
            args = {**SCOPE, "course_id": "COURSE-01",
                    "class_id": "CLASS-01", "path": path}
            self.assertTrue(reserve_shared_intent(**args))
            for change in (
                {"account": "other-fictional-student"},
                {"term": "2026-2027-2"},
                {"school": "https://other.synthetic.invalid"},
            ):
                self.assertTrue(reserve_shared_intent(**{**args, **change}))

    def test_concurrent_registrations_choose_only_one_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shared.sqlite3"
            barrier = threading.Barrier(5)
            outcomes, failures = [], []
            lock = threading.Lock()

            def contender():
                try:
                    barrier.wait(timeout=10)
                    outcome = reserve_shared_intent(
                        **SCOPE, course_id="COURSE-01",
                        class_id="CLASS-01", path=path)
                    with lock:
                        outcomes.append(outcome)
                except Exception as exc:
                    with lock:
                        failures.append(type(exc).__name__)

            threads = [threading.Thread(target=contender) for _ in range(5)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertEqual(failures, [])
            self.assertEqual(outcomes.count(True), 1)
            self.assertEqual(outcomes.count(False), 4)

    def test_no_identity_or_class_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shared.sqlite3"
            for broken in (
                {"account": ""},
                {"term": "unknown"},
                {"course_id": "none"},
                {"class_id": ""},
                {"class_id": None},
            ):
                with self.subTest(broken=broken), self.assertRaises(ValueError):
                    reserve_shared_intent(
                        **{**SCOPE, "course_id": "COURSE-01",
                           "class_id": "CLASS-01", "path": path, **broken})
            self.assertFalse(path.exists())

    def test_shared_path_override_and_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                shared_intent_path(Path(tmp) / "local.db"),
                (Path(tmp) / "local.db").resolve())


if __name__ == "__main__":
    unittest.main()
