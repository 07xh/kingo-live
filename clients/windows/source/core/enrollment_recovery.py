"""Read-only reconciliation of durable enrollment intent records.

No school write, network endpoint, login, request retries or scheduler exists
here. A trusted *read-only* adapter may be injected independently of the
official selection-window and write capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    _root = Path(__file__).resolve().parents[4]
    if (_root / "contracts" / "enrollment_evidence.py").is_file() and str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
from contracts.enrollment_evidence import verify_selection, VerificationState

from core.enrollment import (
    AttemptJournal, EnrollmentNotReady, Target, _read_records, _validate_targets,
)


class ReadbackGateway(Protocol):
    """Independently verified official readback for one account and semester."""
    official_enrollment_verified: bool
    account_scope: str
    enrollment_semester: str

    async def read_enrollments(self) -> set[tuple[str, str]]: ...


class RecoveryStatus(str, Enum):
    """No state implies authorization to resend an enrollment request."""
    CONFIRMED = "confirmed"
    ALREADY_SELECTED = "already_selected"
    PENDING = "pending"
    REJECTED = "rejected"
    CONFIRMED_MISMATCH = "confirmed_mismatch"
    LEGACY_SCOPE_UNVERIFIED = "legacy_scope_unverified"
    NOT_ATTEMPTED = "not_attempted"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RecoveryEntry:
    """Only a local target key and status; no personal or session data."""
    key: str
    status: RecoveryStatus


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """One official read attempt and its durable local reconciliation."""
    entries: tuple[RecoveryEntry, ...]
    official_read_ok: bool
    unlisted_pending: int

    @property
    def states(self) -> dict[str, RecoveryStatus]:
        return {entry.key: entry.status for entry in self.entries}

    @property
    def unresolved_keys(self) -> tuple[str, ...]:
        return tuple(
            entry.key for entry in self.entries
            if entry.status in (RecoveryStatus.PENDING,
                                RecoveryStatus.CONFIRMED_MISMATCH,
                                RecoveryStatus.LEGACY_SCOPE_UNVERIFIED,
                                RecoveryStatus.UNAVAILABLE)
        )

    @property
    def needs_attention(self) -> bool:
        return not self.official_read_ok or bool(
            self.unlisted_pending or self.unresolved_keys)


def _check_readback_scope(gateway: ReadbackGateway, journal: AttemptJournal) -> None:
    """Independent read gate. Write opt-in or open enrollment window not needed."""
    if getattr(gateway, "official_enrollment_verified", False) is not True:
        raise EnrollmentNotReady("官方已选记录只读接口未验收，拒绝核验")
    if (getattr(gateway, "account_scope", None) != journal.account_scope
            or getattr(gateway, "enrollment_semester", None) != journal.semester):
        raise EnrollmentNotReady("官方账号或学期与本地提交记录不匹配")
    # Read-only recovery needs the SAME verified school identity as the
    # write gate; a matching display alias alone cannot authorize recovery.
    if journal.shared_intent_db is not None:
        if (getattr(gateway, "verified_account_id", None)
                != journal.verified_account_id
                or getattr(gateway, "verified_school_origin", None)
                != journal.school_origin):
            raise EnrollmentNotReady("只读恢复的真实账号或学校来源与共享记录不一致")


async def reconcile_pending(gateway: ReadbackGateway, journal: AttemptJournal,
                            targets: Sequence[Target]) -> RecoveryReport:
    """One bounded official read of the submitted course identities.

    - Works even when the enrollment window is closed.
    - Never calls `submit_once`, `selection_window`, or `list_offerings`.
    - Does not clear pending records on absent, stale or unreadable readbacks.
    - May mark a scope-bound pending/rejected attempt as confirmed only
      after an authoritative positive match. V1 account-unbound intents stay pending. The HMAC journal remains account scoped.
    - Reports unresolved *unlisted* intents without trying to guess their
      original identifiers.
    """
    ordered = list(targets)
    _validate_targets(ordered, journal.semester)
    _check_readback_scope(gateway, journal)

    with journal.exclusive():
        data = journal._read()
        pending = ({fingerprint for fingerprint, status
                    in data["attempts"].items() if status == "pending"}
                   | set(data["legacy_attempts"]))
        present_hashes = set()
        for target in ordered:
            present_hashes.add(journal._fingerprint(target, data))
            present_hashes.add(journal._legacy_fingerprint(target))
        unlisted = len(pending - present_hashes)

        # Status reads occur under the same OS lock. A corrupt journal or
        # filesystem error raises before any official request or mutation.
        journal_states = {target.key: journal.status(target) for target in ordered}
        try:
            records = _read_records(await gateway.read_enrollments())
        except Exception:
            # No assumption of failure. Leave the journal entirely unchanged.
            return RecoveryReport(
                tuple(RecoveryEntry(t.key, RecoveryStatus.UNAVAILABLE)
                      for t in ordered),
                official_read_ok=False,
                unlisted_pending=unlisted,
            )

        results: list[RecoveryEntry] = []
        for target in ordered:
            state = journal_states[target.key]
            verdict = verify_selection(
                course_id=target.course_id, class_id=target.class_id,
                expected_account=journal.account_scope,
                expected_semester=journal.semester,
                observed_account=getattr(gateway, "account_scope", None),
                observed_semester=getattr(gateway, "enrollment_semester", None),
                official_verified=getattr(gateway, "official_enrollment_verified", False),
                records=records,
            )
            selected = verdict.state is VerificationState.CONFIRMED

            if state == "legacy_pending":
                # V1 fingerprints have no account scope. A positive official
                # selection does not establish ownership of the old attempt.
                # Preserve the legacy fence until an audited manual migration.
                status = RecoveryStatus.LEGACY_SCOPE_UNVERIFIED
            elif selected and state in ("pending", "rejected"):
                # Never mark confirmed based on transport acknowledgement.
                # Journal will reject unexpected state transitions.
                journal.record_confirmed(target)
                status = RecoveryStatus.CONFIRMED
            elif selected and state == "confirmed":
                status = RecoveryStatus.CONFIRMED
            elif selected:
                status = RecoveryStatus.ALREADY_SELECTED
            elif state in ("pending", "legacy_pending"):
                status = RecoveryStatus.PENDING
            elif state == "confirmed":
                status = RecoveryStatus.CONFIRMED_MISMATCH
            elif state == "rejected":
                status = RecoveryStatus.REJECTED
            else:
                status = RecoveryStatus.NOT_ATTEMPTED
            results.append(RecoveryEntry(target.key, status))

        # Do not claim an unlisted pending entry was reconciled. All journal
        # fences not backed by a positive official record remain in place.
        return RecoveryReport(
            tuple(results), official_read_ok=True, unlisted_pending=unlisted)
