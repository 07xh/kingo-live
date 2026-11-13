"""Strictly local viewer for an exported NCWU course-plan CSV snapshot.

No school requests, session credentials, enrollment writes, or configuration
changes. All data originates from the user-selected file, NOT live availability.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 3000
REQUIRED_HEADERS = ("课程号", "课程名称", "教学班号", "可选人数")
OPTIONAL_HEADERS = ("任课教师", "上课时间", "上课地点")


@dataclass(frozen=True)
class PlanRow:
    course_id: str
    course_name: str
    class_id: str
    remaining: int | None
    teacher: str
    schedule: str
    room: str

    @property
    def remaining_label(self) -> str:
        if self.remaining is None:
            return "未知"
        if self.remaining == 0:
            return "0（快照已满）"
        return str(self.remaining)


def _text(value: str | None, *, max_chars: int = 240) -> str:
    text = (value or "").strip()
    if len(text) > max_chars or any(ord(c) < 32 for c in text):
        raise ValueError("CSV 单元格包含非法控制字符或过长")
    return text


def _remaining(value: str | None) -> int | None:
    s = _text(value, max_chars=20)
    if not s:
        return None
    if not s.isascii() or not s.isdecimal():
        raise ValueError("可选人数必须为非负整数或留空")
    n = int(s)
    if n > 1000000:
        raise ValueError("可选人数超出允许范围")
    return n


def load_plan_csv(path: Path) -> list[PlanRow]:
    """Read a bounded UTF-8 BOM-compatible CSV without modifying any file."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("请选择本机普通 CSV 文件")
    size = path.stat().st_size
    if not 0 < size <= MAX_CSV_BYTES:
        raise ValueError("CSV 文件为空或超出容量限制")

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            headers = reader.fieldnames or []
            if (len(headers) != len(set(headers))
                    or not all(header in headers for header in REQUIRED_HEADERS)):
                raise ValueError("CSV 表头不符合开课计划导出格式")
            rows: list[PlanRow] = []
            for record in reader:
                if len(rows) >= MAX_CSV_ROWS:
                    raise ValueError("CSV 行数超过显示上限")
                if None in record:
                    raise ValueError("CSV 数据列数与表头不一致")
                course_id = _text(record.get("课程号"), max_chars=80)
                course_name = _text(record.get("课程名称"))
                if not course_id or not course_name:
                    raise ValueError("CSV 课程代码或名称缺失")
                rows.append(PlanRow(
                    course_id=course_id,
                    course_name=course_name,
                    class_id=_text(record.get("教学班号"), max_chars=80),
                    remaining=_remaining(record.get("可选人数")),
                    teacher=_text(record.get("任课教师")),
                    schedule=_text(record.get("上课时间")),
                    room=_text(record.get("上课地点")),
                ))
    except (UnicodeError, csv.Error) as error:
        raise ValueError("CSV 编码或内容格式不正确") from error
    return rows
