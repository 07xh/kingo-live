"""One read-only official-selection evidence contract for the four legacy clients.

This is NOT a fifth executor or a network client. No receipt, HTTP 200, success
text, seat count, timetable, or catalog can establish an enrollment. The
caller must independently verify the origin, account and semester of the
complete official enrolled-course listing before setting official_verified.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class VerificationState(str, Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class SelectionVerdict:
    state: VerificationState
    reason: str


def pending_receipt() -> SelectionVerdict:
    """Same safe result for HTTP 200, server prose or selected-count changes.

    It denotes an attempted write without exact official enrolled-course
    evidence. It never authorizes a retry, clears a journal, or claims success.
    """
    return SelectionVerdict(VerificationState.PENDING, "official_readback_required")


def _id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("课程身份字段类型错误")
    result = str(value).strip()
    if not result or len(result) > 100 or any(ord(c) < 32 for c in result):
        raise ValueError("课程身份字段为空或不可信")
    return result


def normalized_official_rows(rows: Any, columns: Mapping[str, Any]) -> frozenset[tuple[str, str]]:
    """Parse ONLY a trusted enrolled-course list, never a catalog or timetable.

    Missing class IDs are not silently accepted as positive target evidence.
    Both dict and positional row schemas are supported for existing adapters.
    """
    if not isinstance(rows, (list, tuple)) or len(rows) > 3000:
        raise ValueError("官方记录必须是有界数组")
    if not isinstance(columns, Mapping) or not {"course_id", "class_id"} <= set(columns):
        raise ValueError("官方记录缺少课程号和教学班映射")
    parsed: set[tuple[str, str]] = set()
    for row in rows:
        vals = []
        for key in ("course_id", "class_id"):
            field = columns[key]
            if isinstance(row, dict):
                if not isinstance(field, (str, int)) or isinstance(field, bool):
                    raise ValueError("列映射格式错误")
                val = row.get(str(field))
            elif isinstance(row, (list, tuple)):
                if type(field) is not int or field < 0 or field >= len(row):
                    raise ValueError("数组列映射错误")
                val = row[field]
            else:
                raise ValueError("官方记录行格式错误")
            vals.append(_id(val))
        parsed.add((vals[0], vals[1]))
    return frozenset(parsed)


def verify_selection(*, course_id: Any, class_id: Any,
                     expected_account: str, expected_semester: str,
                     observed_account: str | None, observed_semester: str | None,
                     official_verified: bool,
                     records: Any) -> SelectionVerdict:
    """Bounded exact positive match; absence is NOT rejection or retry authority."""
    if official_verified is not True:
        return SelectionVerdict(VerificationState.UNAVAILABLE, "official_source_not_verified")
    if (not isinstance(expected_account, str) or not expected_account.strip()
            or not isinstance(expected_semester, str) or not expected_semester.strip()
            or observed_account != expected_account
            or observed_semester != expected_semester):
        return SelectionVerdict(VerificationState.UNAVAILABLE, "account_or_semester_unverified")
    try:
        identity = (_id(course_id), _id(class_id))
        if not isinstance(records, (set, frozenset)) or len(records) > 3000:
            raise ValueError("官方记录结构无效")
        for item in records:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("官方课程记录元组格式错误")
            _id(item[0])
            _id(item[1])
    except ValueError:
        return SelectionVerdict(VerificationState.UNAVAILABLE, "identity_or_records_invalid")
    if identity in records:
        return SelectionVerdict(VerificationState.CONFIRMED, "exact_official_course_class")
    return SelectionVerdict(VerificationState.PENDING, "no_exact_official_match")
