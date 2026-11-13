"""Canonical *read-only* course offering view shared by research clients.

Pure, deterministic adapters; never perform HTTP, authentication, enrollment,
credential persistence, availability inference, or write-back.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping


@dataclass(frozen=True)
class CourseOffering:
    source: str
    course_id: str
    class_id: str | None
    course_name: str
    teacher: str | None
    remaining: int | None
    observed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def availability_label(self) -> str:
        if self.remaining is None:
            return "未知（未提供可信余量）"
        if self.remaining == 0:
            return "快照余量为 0"
        return f"快照余量 {self.remaining}（需官网复核）"


def _text(value: Any, *, required: bool = False) -> str | None:
    if value is None:
        value = ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("课程字段必须是普通文本")
    result = str(value).strip()
    if len(result) > 200 or any(ord(char) < 32 for char in result):
        raise ValueError("课程字段包含非法字符或过长")
    if required and not result:
        raise ValueError("课程代码与名称不能为空")
    return result or None


def _remaining(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("不能将布尔值当成余量")
    if isinstance(value, int):
        count = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        count = int(value)
    else:
        raise ValueError("可信余量只能为非负整数或未知")
    if count < 0 or count > 1000000:
        raise ValueError("余量超出有效范围")
    return count


def _offering(*, source: str, course_id: Any, class_id: Any,
              course_name: Any, teacher: Any, remaining: Any,
              observed_at: str | None) -> CourseOffering:
    if observed_at is not None:
        observed_at = _text(observed_at, required=True)
    return CourseOffering(
        source=source,
        course_id=_text(course_id, required=True),
        class_id=_text(class_id),
        course_name=_text(course_name, required=True),
        teacher=_text(teacher),
        remaining=_remaining(remaining),
        observed_at=observed_at,
    )


def from_ncwu_plan(row: Mapping[str, Any], *, observed_at: str | None = None) -> CourseOffering:
    """Adapt ncwu/hs_kcbcx.parse_plan output.

    Only `left` decoded from server `qdrs` is considered a seat observation.
    Never substitute capacity minus enrollment or assume a current snapshot.
    """
    return _offering(
        source="ncwu.plan", course_id=row.get("kcdm"),
        class_id=row.get("skbjdm"), course_name=row.get("kcmc"),
        teacher=row.get("rkjs"), remaining=row.get("left"),
        observed_at=observed_at,
    )


def from_qiangke_table(row: Mapping[str, Any], *, observed_at: str | None = None) -> CourseOffering:
    """Adapt qiangke/qk/kingo_xk.parse_table output, without guessing seats."""
    import re

    code = row.get("kcdm")
    name = row.get("kc") or ""
    if not code:
        match = re.match(r"^\[([^\]]+)\]", name)
        code = match.group(1) if match else None
    name = re.sub(r"^\[[^\]]+\]\s*", "", name).strip()
    return _offering(
        source="qiangke.table", course_id=code,
        class_id=row.get("skbjdm"), course_name=name,
        teacher=row.get("rkjs"), remaining=row.get("qdrs"),
        observed_at=observed_at,
    )


def from_windows_item(item: Any, *, observed_at: str | None = None) -> CourseOffering:
    """Adapt the legacy Windows read-only CourseItem; no importing GUI deps."""
    return _offering(
        source="windows.readonly", course_id=getattr(item, "course_id", None),
        class_id=getattr(item, "class_id", None),
        course_name=getattr(item, "course_name", None),
        teacher=getattr(item, "teacher", None),
        remaining=getattr(item, "remain", None),
        observed_at=observed_at,
    )
