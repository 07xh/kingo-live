"""Offline Windows-to-legacy shared intent fence; no university HTTP calls."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from core.enrollment import AttemptJournal, EnrollmentEngine, EnrollmentNotReady
from core.course_models import CourseTarget, CourseOffering
from tests.test_enrollment import FakeOfficialGateway

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from qiangke.qk.intent_journal import IntentJournal


TERM = "2026-2027-1"
SCHOOL = "https://example.invalid"
ACCOUNT = "fictional-own-account"


class WindowsSharedFenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.db = self.dir / "intent.sqlite3"
        self.target = CourseTarget("T", "C001", "J01", semester=TERM)

    def journal(self):
        return AttemptJournal(
            self.dir / "windows.json", TERM,
            account_scope="synthetic-account-A",
            shared_intent_db=self.db,
            school_origin=SCHOOL,
            verified_account_id=ACCOUNT,
        )

    def gateway(self):
        gateway = FakeOfficialGateway([CourseOffering("C001", "J01", 1)])
        gateway.verified_school_origin = SCHOOL
        gateway.verified_account_id = ACCOUNT
        return gateway

    def run_with(self, journal, gateway):
        engine = EnrollmentEngine(
            gateway, journal, user_opted_in=True,
            institution_permits_write=True)
        return asyncio.run(engine.run([self.target]))

    def test_existing_qiangke_or_ncwu_intent_blocks_windows_write(self):
        self.assertTrue(IntentJournal(self.db).reserve(
            school=SCHOOL, account=ACCOUNT, term=TERM,
            course={"kch_id": "C001", "xkid": "J02"}))
        gateway = self.gateway()
        journal = self.journal()
        report = self.run_with(journal, gateway)
        self.assertTrue(report.halted)
        self.assertEqual(gateway.writes, [])
        self.assertEqual(journal.status(self.target), None)
        self.assertEqual(report.states["T"], "本机提交记录无法安全写入")

    def test_windows_reserves_shared_guard_before_mock_submission(self):
        journal = self.journal()
        gateway = self.gateway()
        result = self.run_with(journal, gateway)
        self.assertEqual(result.states["T"], "官方记录已确认成功")
        self.assertEqual(len(gateway.writes), 1)
        self.assertEqual(journal.status(self.target), "confirmed")
        self.assertFalse(IntentJournal(self.db).reserve(
            school=SCHOOL, account=ACCOUNT, term=TERM,
            course={"kch_id": "C001", "xkid": "J99"}))

    def test_unverified_account_or_origin_rejected_before_any_write(self):
        for field, value in (("verified_account_id", "other-account"),
                             ("verified_school_origin", "https://other.invalid")):
            with self.subTest(field=field):
                gateway = self.gateway()
                setattr(gateway, field, value)
                with self.assertRaises(EnrollmentNotReady):
                    self.run_with(self.journal(), gateway)
                self.assertEqual(gateway.writes, [])
                self.assertFalse(self.db.exists())

    def test_shared_database_unavailable_leaves_local_unmodified(self):
        self.db.mkdir()
        journal = self.journal()
        gateway = self.gateway()
        result = self.run_with(journal, gateway)
        self.assertTrue(result.halted)
        self.assertEqual(gateway.writes, [])
        self.assertIsNone(journal.status(self.target))

    def test_local_file_write_failure_never_releases_shared_intent(self):
        journal = self.journal()
        with patch.object(journal, "_write", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                journal.reserve_before_write(self.target)
        self.assertFalse(IntentJournal(self.db).reserve(
            school=SCHOOL, account=ACCOUNT, term=TERM,
            course={"kch_id": "C001", "xkid": "J01"}))


if __name__ == "__main__":
    unittest.main()
