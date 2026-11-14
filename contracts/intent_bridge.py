"""Shared, durable cross-entrypoint intent reservation.

The four existing execution families MUST keep their native journals for
their own recovery contracts. This bridge adds a common SQLite reservation
*before* a transport write, using the already deployed Qiangke/NCWU schema.

All affected entrypoints must use the same path and the same authenticated
school, student, term and course identity to share one fence. Neither a
user-provided account alias nor a successful HTTP response proves identity.

A reserved or ambiguous intent remains fenced pending authoritative review.
Nothing here performs HTTP requests or deletes historical journal records.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def shared_intent_path(path: Optional[str | os.PathLike[str]] = None) -> Path:
    if path is not None:
        value = str(path).strip()
        if not value:
            raise ValueError("Explicit shared intent path must not be empty")
        return Path(value).expanduser().resolve()
    override = os.environ.get("KINGO_INTENT_DB")
    if override is not None:
        if not override.strip():
            raise ValueError("KINGO_INTENT_DB must not be empty")
        return Path(override).expanduser().resolve()
    return (Path.home() / ".kingo-assistant" / "intent-journal.sqlite3").resolve()


def reserve_shared_intent(*, school: str, account: str, term: str,
                          course_id: str, class_id: str = "",
                          path: Optional[str | os.PathLike[str]] = None,
                          require_class: bool = True) -> bool:
    """Return True only when the single physical-write slot was reserved.

    Fail closed on missing or placeholder identity. A course-level lock
    blocks all teaching-class variants, mirroring the NCWU write payload.
    Existing HMACs and old SQLite rows are reused in-place, not migrated
    or silently discarded. The INSERT is SQLite BEGIN IMMEDIATE atomic.
    """
    if not all(isinstance(x, str) and x.strip()
               for x in (school, account, term, course_id)):
        raise ValueError("school, account, term and course_id are required")
    if not isinstance(class_id, str):
        raise ValueError("class_id must be a string")
    if require_class and not class_id.strip():
        raise ValueError("exact teaching-class identity required")
    invalid = {"none", "null", "unknown", "-"}
    if (account.strip().lower() in invalid or term.strip().lower() in invalid
            or course_id.strip().lower() in invalid):
        raise ValueError("placeholder identity cannot authorize a write")

    from qiangke.qk.intent_journal import IntentJournal
    return IntentJournal(shared_intent_path(path)).reserve(
        school=school, account=account, term=term,
        course={"kch_id": course_id.strip(), "xkid": class_id.strip()},
    )
