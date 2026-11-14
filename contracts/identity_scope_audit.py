"""Offline, privacy-preserving cross-client enrollment identity preflight.

This contract NEVER validates a live enrollment or constructs/sends requests.
Its verdict is structural only: even ALIGNED is not proof of an official
selected-course listing. Raw accounts and course identifiers are never returned.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping
import re
from urllib.parse import urlsplit


class ScopeVerdict(str, Enum):
    ALIGNED = "structurally_aligned_only"
    MISMATCH = "scope_mismatch"
    INCOMPLETE = "identity_incomplete"


@dataclass(frozen=True)
class ScopeAudit:
    verdict: ScopeVerdict
    checked_clients: int
    mismatched_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()

    # No repr containing school/account/course/class data.


_FIELDS = ("school", "account", "term", "course_id", "class_id")
_PLACEHOLDERS = {"", "-", "none", "null", "unknown", "placeholder"}
_TERM = re.compile(r"^(\d{4}(?:-\d{4})?)[/,-]([0-3])$")


def _field(field: str, value: object) -> str:
    if (isinstance(value, bool) or not isinstance(value, (str, int))
            or len(str(value)) > 256):
        return ""
    result = str(value).strip()
    if (not result or result.lower() in _PLACEHOLDERS
            or any(ord(c) < 32 for c in result)):
        return ""
    if field == "school":
        parsed = urlsplit(result)
        result = (parsed.netloc if parsed.scheme else result).strip().lower()
        if (not result or "/" in result or "@" in result
                or result.lower() in _PLACEHOLDERS):
            return ""
    if field == "term":
        match = _TERM.fullmatch(result)
        if match:
            # Only identical academic-year strings and numeric period codes
            # are normalized. NCWU xqM=0 is NOT silently equated with xq=1.
            result = f"{match.group(1)}/{match.group(2)}"
    return result


def audit_scopes(scopes: Mapping[str, Mapping[str, object]]) -> ScopeAudit:
    """Compare 2-4 executable scope identities; never emit their raw values.

    No value may be inferred from a display name, timetable, seat count or
    a different semester convention. Callers must verify origin and account
    externally before this local structural comparison is meaningful.
    """
    if not isinstance(scopes, Mapping) or not 2 <= len(scopes) <= 4:
        raise ValueError("请提供两至四个旧执行器的身份范围")
    normalized: list[dict[str, str]] = []
    missing: set[str] = set()
    for client, scope in scopes.items():
        if not isinstance(client, str) or not client or not isinstance(scope, Mapping):
            raise ValueError("执行器身份结构错误")
        part: dict[str, str] = {}
        for field in _FIELDS:
            part[field] = _field(field, scope.get(field))
            if not part[field]:
                missing.add(field)
        normalized.append(part)
    if missing:
        return ScopeAudit(ScopeVerdict.INCOMPLETE, len(scopes),
                          missing_fields=tuple(sorted(missing)))
    different = tuple(field for field in _FIELDS
                      if len({scope[field] for scope in normalized}) != 1)
    if different:
        return ScopeAudit(ScopeVerdict.MISMATCH, len(scopes),
                          mismatched_fields=different)
    return ScopeAudit(ScopeVerdict.ALIGNED, len(scopes))
