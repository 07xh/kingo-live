"""Pure seat-count normalization and catalog-delta classification.

Shared by the research and Windows-independent monitor implementations.
Unknown is distinct from zero. No networking or submission.
"""
from __future__ import annotations

from typing import Any, Optional


def remaining_count(offering: Any) -> Optional[int]:
    # Canonical CourseOffering.remaining takes precedence over legacy remain.
    value = (getattr(offering, "remaining") if hasattr(offering, "remaining")
             else getattr(offering, "remain", None))
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        value = value.strip()
        if value.isascii() and value.isdecimal():
            return int(value)
    return None


def classify_seat_delta(current: Any, previous: Any = None, *,
                        first_observation: bool = False,
                        include_decrease: bool = False) -> Optional[str]:
    """Return a release-event kind only when both compared counts are known.

    The first appearance of an offering with positive seats is a 'new' event.
    For an already tracked offering, an unknown count cannot demonstrate a
    capacity change, even if the following observation is positive.
    """
    after = remaining_count(current)
    if after is None:
        return None
    if first_observation:
        return "new" if after > 0 else None
    before = remaining_count(previous)
    if before is None:
        return None
    if before <= 0 < after:
        return "reopened"
    if after > before:
        return "increased"
    if include_decrease and after < before:
        return "decreased"
    return None
