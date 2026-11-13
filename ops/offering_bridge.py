"""Read-only adapter for the repository's canonical CourseOffering contract.

No HTTP, no persistence, no write actions. Unknown course/class/seat values
stay unknown; model conversion never silently maps an absent count to zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class ReadOnlyOffering:
    source: str
    course_id: str
    class_id: Optional[str]
    remaining: Optional[int]
    observed_at: Optional[str]

    @property
    def identity_level(self) -> str:
        return "exact" if self.class_id else "course_only"

    def fingerprint(self) -> str:
        if not self.class_id:
            raise ValueError("course-only identity has no teaching-class fingerprint")
        return f"{self.course_id}|{self.class_id}"


def normalize_readonly(record: Any) -> ReadOnlyOffering:
    """Accept CourseOffering, legacy Offering or an equivalent mapping.

    The canonical `remaining` name wins when present, even if its value is
    None. An absent/invalid field cannot be guessed from another field.
    """
    if isinstance(record, Mapping):
        value = record.get
        has = record.__contains__
    else:
        value = lambda k, default=None: getattr(record, k, default)
        has = lambda k: hasattr(record, k)
    cid = value("course_id")
    cls = value("class_id")
    if not isinstance(cid, str) or not cid.strip():
        raise ValueError("missing course identity")
    if cls is not None and not isinstance(cls, str):
        raise ValueError("invalid teaching-class identity")
    amount = value("remaining") if has("remaining") else value("remain")
    if amount is not None and (isinstance(amount, bool)
                               or not isinstance(amount, int) or amount < 0):
        raise ValueError("seat count must be a nonnegative integer or unknown")
    when = value("observed_at")
    if when is not None and not isinstance(when, str):
        raise ValueError("invalid observed_at")
    source = value("source") or "legacy.readonly"
    if not isinstance(source, str):
        raise ValueError("invalid source")
    return ReadOnlyOffering(source, cid.strip(),
                            cls.strip() or None if cls is not None else None,
                            amount, when)
