"""Adapt an EXISTING ops executor to the Windows enrollment engine.

No new HTTP or protocol implementation is defined here. The provided source
must independently supply a verified official catalog, selection-window state,
selected-course records and timetable for the SAME authenticated student/term.
An executor receipt is transport context, never official enrollment proof.

This adapter is not registered at GUI startup. Unverified clients remain
disabled, and no real university adapter is bundled.
"""
from __future__ import annotations

import asyncio
from typing import Callable

from core.enrollment import (
    AttemptJournal, EnrollmentEngine, EnrollmentNotReady,
    SubmitReceipt, Target,
)


class ExistingExecutorGateway:
    """Windows VerifiedGateway facade over an already-implemented executor."""

    _CAPABILITIES = (
        "official_catalog_verified", "official_enrollment_verified",
        "official_window_verified", "official_write_verified",
    )
    _READS = ("selection_window", "read_enrollments",
              "list_offerings", "read_schedule")
    _IDENTITY_FIELDS = ("account_scope", "enrollment_semester",
                        "verified_account_id", "verified_school_origin")

    def __init__(self, source, executor: Callable[[dict], dict],
                 journal: AttemptJournal) -> None:
        if not isinstance(journal, AttemptJournal):
            raise TypeError("需要现有 Windows AttemptJournal")
        if not callable(executor):
            raise TypeError("必须复用一个现有可调用的提交执行器")
        if any(not callable(getattr(source, name, None)) for name in self._READS):
            raise EnrollmentNotReady("未提供完整的官方只读核验能力")
        self.source = source
        self.executor = executor
        self.journal = journal

    def __getattr__(self, name: str):
        """Never cache account/permission evidence across session changes.

        A Windows engine can be prepared before the user signs out or changes
        the official account. Read the current source attributes on every gate.
        """
        if name in self._CAPABILITIES:
            return getattr(self.source, name, None) is True
        if name in self._IDENTITY_FIELDS:
            return getattr(self.source, name, None)
        raise AttributeError(name)

    def _check_readback_identity(self) -> None:
        """Readback may continue after write permission expires, not on a new account."""
        if (self.official_enrollment_verified is not True
                or self.journal.shared_intent_db is None
                or self.account_scope != self.journal.account_scope
                or self.enrollment_semester != self.journal.semester
                or self.verified_account_id != self.journal.verified_account_id
                or self.verified_school_origin != self.journal.school_origin):
            raise EnrollmentNotReady("已选数据源与持久化记录的官方账号或学期不一致")

    def _check_identity(self) -> None:
        if (not all(getattr(self, item) is True for item in self._CAPABILITIES)
                or self.journal.shared_intent_db is None
                or self.account_scope != self.journal.account_scope
                or self.enrollment_semester != self.journal.semester
                or self.verified_account_id != self.journal.verified_account_id
                or self.verified_school_origin != self.journal.school_origin):
            raise EnrollmentNotReady("官方验收、共享意图记录或本人学期身份未通过")

    async def selection_window(self):
        return await self.source.selection_window()

    async def read_enrollments(self):
        self._check_readback_identity()
        rows = await self.source.read_enrollments()
        # The source may have switched accounts while the async read awaited.
        self._check_readback_identity()
        return rows

    async def list_offerings(self):
        return await self.source.list_offerings()

    async def read_schedule(self):
        return await self.source.read_schedule()

    async def submit_once(self, target: Target) -> SubmitReceipt:
        self._check_identity()
        if (not isinstance(target, Target)
                or (target.semester and target.semester != self.journal.semester)
                or self.journal.status(target) != "pending"):
            raise EnrollmentNotReady("目标学期或提交意图未经持久化确认")
        # The existing EnrollmentEngine wrote its durable local + shared
        # intent before entering this callback. Call the ORIGINAL executor
        # on a worker thread, never block Tk or start a second HTTP client.
        await asyncio.to_thread(self.executor, {
            "course_id": target.course_id,
            "class_id": target.class_id,
            "term": self.enrollment_semester,
            "account": self.verified_account_id,
            "name": target.course_name,
        })
        # Even a "success" response does not prove a selection. The
        # EnrollmentEngine will separately read official selected courses.
        return SubmitReceipt(None)


def make_existing_executor_engine(*, source, executor, journal: AttemptJournal,
                                  user_opted_in: bool = False,
                                  institution_permits_write: bool = False
                                  ) -> EnrollmentEngine:
    """Create the existing Windows engine without enabling any school writes.

    Real callers must supply independently verified school capability evidence
    and explicit user/institution authorization. Defaults are fail-closed.
    """
    return EnrollmentEngine(
        ExistingExecutorGateway(source, executor, journal), journal,
        user_opted_in=user_opted_in,
        institution_permits_write=institution_permits_write,
    )
