"""Offline integration tests: Windows engine -> original executor callback."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from core.course_models import CourseOffering, CourseTarget, ScheduleSnapshot
from core.enrollment import (AttemptJournal, EnrollmentNotReady, WindowState,
                             VerificationPolicy)
from core.existing_executor_gateway import make_existing_executor_engine


class SyntheticOfficialSource:
    official_catalog_verified = True
    official_enrollment_verified = True
    official_window_verified = True
    official_write_verified = True
    account_scope = "fictional-account-scope"
    enrollment_semester = "2026-2027-1"
    verified_account_id = "fictional-student"
    verified_school_origin = "https://synthetic.invalid"

    def __init__(self):
        self.selected = set()
        self.reads = 0
        self.writes = []
        self.window = WindowState.OPEN

    async def selection_window(self):
        return self.window

    async def read_enrollments(self):
        self.reads += 1
        return set(self.selected)

    async def list_offerings(self):
        return [CourseOffering("COURSE-A", "CLASS-01", 1,
                semester=self.enrollment_semester, time_slots=())]

    async def read_schedule(self):
        return ScheduleSnapshot(self.enrollment_semester, (), True)


class ExistingExecutorGatewayTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        root = Path(self.td.name)
        self.source = SyntheticOfficialSource()
        self.journal = AttemptJournal(
            root / "windows.json", "2026-2027-1",
            account_scope=self.source.account_scope,
            shared_intent_db=root / "shared.sqlite3",
            school_origin=self.source.verified_school_origin,
            verified_account_id=self.source.verified_account_id)
        self.target = CourseTarget("A", "COURSE-A", "CLASS-01",
                                   semester="2026-2027-1")

    def make(self, callback, *, opted=True, permitted=True):
        engine = make_existing_executor_engine(
            source=self.source, executor=callback, journal=self.journal,
            user_opted_in=opted, institution_permits_write=permitted)
        engine.verification_policy = VerificationPolicy(
            max_reads=1, interval_seconds=0)
        return engine

    def test_existing_executor_single_call_and_official_readback(self):
        def existing_executor(target):
            self.source.writes.append(target)
            self.source.selected.add((target["course_id"], target["class_id"]))
            return {"ok": True, "code": "success", "write_attempted": True}
        engine = self.make(existing_executor)
        result = asyncio.run(engine.run([self.target]))
        self.assertFalse(result.halted)
        self.assertEqual(len(self.source.writes), 1)
        self.assertEqual(self.source.writes[0]["account"],
                         "fictional-student")
        self.assertEqual(result.delivery["A"], "unknown")
        self.assertEqual(result.states["A"], "官方记录已确认成功")
        again = asyncio.run(engine.run([self.target]))
        self.assertFalse(again.halted)
        self.assertEqual(len(self.source.writes), 1)

    def test_executor_ack_without_official_readback_stays_pending(self):
        called = []
        engine = self.make(lambda target: called.append(target) or
                           {"ok": True, "code": "enrolled"})
        result = asyncio.run(engine.run([self.target]))
        self.assertTrue(result.halted)
        self.assertEqual(len(called), 1)
        self.assertEqual(self.journal.status(self.target), "pending")
        again = asyncio.run(engine.run([self.target]))
        self.assertTrue(again.halted)
        self.assertEqual(len(called), 1)

    def test_missing_consent_or_permission_never_calls_executor(self):
        called = []
        for opted, permitted in ((False, True), (True, False)):
            with self.subTest(opted=opted, permitted=permitted):
                with self.assertRaises(EnrollmentNotReady):
                    self.make(called.append, opted=opted,
                              permitted=permitted).validate_ready()
        self.assertEqual(called, [])

    def test_scope_mismatch_blocks_before_executor(self):
        self.source.verified_account_id = "other-student"
        with self.assertRaises(EnrollmentNotReady):
            self.make(lambda target: None).validate_ready()

    def test_absent_official_readback_capability_blocks(self):
        self.source.official_enrollment_verified = False
        with self.assertRaises(EnrollmentNotReady):
            self.make(lambda target: None).validate_ready()

    def test_direct_submit_without_prewrite_journal_is_forbidden(self):
        called = []
        gateway = self.make(called.append).gateway
        with self.assertRaises(EnrollmentNotReady):
            asyncio.run(gateway.submit_once(self.target))
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
