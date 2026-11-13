"""Offline identity comparison of two *course-offering* snapshots.

This module cannot prove an enrollment, account ownership, semester, or
authoritative source. It only compares public course/class codes already
validated by contracts.offering. In particular, it never interprets
DataTable xk_status, counts, response messages, or filenames as evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .offering import CourseOffering, from_standard_record


class IdentityVerdict(str, Enum):
    EXACT = "exact_ids"
    COURSE_ONLY = "course_only"
    MISSING_CLASS = "missing_class"
    CLASS_ID_REUSED = "class_id_reused"
    AMBIGUOUS = "ambiguous"
    NOT_PRESENT = "not_in_snapshot"


LABELS = {
    IdentityVerdict.EXACT: "课程号和教学班号完全一致（仅快照）",
    IdentityVerdict.COURSE_ONLY: "仅课程号相同；教学班不一致或缺失",
    IdentityVerdict.MISSING_CLASS: "原记录缺少教学班号，无法逐班匹配",
    IdentityVerdict.CLASS_ID_REUSED: "教学班号相同但课程号不同，需人工核对",
    IdentityVerdict.AMBIGUOUS: "课程身份重复或存在歧义，需人工核对",
    IdentityVerdict.NOT_PRESENT: "另一快照未找到；不代表官方未选",
}


@dataclass(frozen=True)
class IdentityRow:
    course_id: str
    class_id: str | None
    course_name: str
    verdict: IdentityVerdict
    reference_classes: tuple[str, ...]
    notes: str = ""

    @property
    def verdict_label(self) -> str:
        return LABELS[self.verdict]


@dataclass(frozen=True)
class IdentityAudit:
    rows: tuple[IdentityRow, ...]
    left_count: int
    reference_count: int

    @property
    def exact(self) -> int:
        return sum(row.verdict is IdentityVerdict.EXACT for row in self.rows)

    @property
    def needs_review(self) -> int:
        return len(self.rows) - self.exact


def compare_offerings(
    left: Sequence[CourseOffering], reference: Sequence[CourseOffering],
) -> IdentityAudit:
    """Strict public, local-only ID comparison. Never changes any journal."""
    if not isinstance(left, (tuple, list)) or not isinstance(reference, (tuple, list)):
        raise ValueError("课程快照必须是数组")
    if len(left) > 3000 or len(reference) > 3000:
        raise ValueError("课程快照超过核对上限")

    def checked(items):
        if any(not isinstance(x, CourseOffering) for x in items):
            raise ValueError("课程数据未通过统一契约")
        # Revalidate even dataclass instances constructed outside parsers.
        return [from_standard_record(x.as_dict()) for x in items]

    source = checked(left)
    other = checked(reference)
    key_count = Counter((x.course_id, x.class_id) for x in source)
    course_map: dict[str, list[CourseOffering]] = defaultdict(list)
    class_map: dict[str, list[CourseOffering]] = defaultdict(list)
    for x in other:
        course_map[x.course_id].append(x)
        if x.class_id is not None:
            class_map[x.class_id].append(x)

    results = []
    for x in source:
        same_course = course_map.get(x.course_id, [])
        exact = [y for y in same_course if x.class_id is not None
                 and y.class_id == x.class_id]
        other_classes = tuple(sorted({y.class_id for y in same_course
                                      if y.class_id is not None}))
        notes = ""
        if key_count[(x.course_id, x.class_id)] > 1 or len(exact) > 1:
            verdict = IdentityVerdict.AMBIGUOUS
        elif x.class_id is None:
            verdict = IdentityVerdict.MISSING_CLASS
        elif len(exact) == 1:
            verdict = IdentityVerdict.EXACT
            changed = []
            if x.course_name != exact[0].course_name:
                changed.append("课程名称不同")
            if x.teacher and exact[0].teacher and x.teacher != exact[0].teacher:
                changed.append("教师名称不同")
            notes = "、".join(changed)
        elif any(y.course_id != x.course_id for y in class_map.get(x.class_id, [])):
            verdict = IdentityVerdict.CLASS_ID_REUSED
        elif same_course:
            verdict = IdentityVerdict.COURSE_ONLY
        else:
            verdict = IdentityVerdict.NOT_PRESENT
        results.append(IdentityRow(
            x.course_id, x.class_id, x.course_name,
            verdict, other_classes, notes,
        ))
    return IdentityAudit(tuple(results), len(source), len(other))
