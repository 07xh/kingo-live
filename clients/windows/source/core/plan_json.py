"""Validated, offline Windows import of the shared course JSON contract."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

# Standalone source runs from clients/windows/source. In the packaged app
# the shared contract is embedded by PyInstaller --paths ../../...
if not getattr(sys, "frozen", False):
    root = Path(__file__).resolve().parents[4]
    if (root / "contracts" / "offering.py").is_file() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
from contracts.offering import from_standard_record
from .plan_csv import PlanRow, MAX_CSV_BYTES, MAX_CSV_ROWS


@dataclass(frozen=True)
class ImportedSnapshot:
    rows: tuple[PlanRow, ...]
    sources: tuple[str, ...]
    observed_at: tuple[str, ...]


def _unique_keys(pairs):
    item = {}
    for key, value in pairs:
        if key in item:
            raise ValueError("JSON 中字段重复")
        item[key] = value
    return item


def _bad_constant(value):
    raise ValueError("JSON 中存在非有限数字")


def load_plan_json(path: Path) -> ImportedSnapshot:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("请选择本机普通 JSON 快照文件")
    size = path.stat().st_size
    if not 0 < size <= MAX_CSV_BYTES:
        raise ValueError("JSON 文件为空或过大")
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f, object_pairs_hook=_unique_keys,
                             parse_constant=_bad_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("JSON 无法解析") from exc
    if not isinstance(data, list) or len(data) > MAX_CSV_ROWS:
        raise ValueError("只接受标准化课程 JSON 数组，最多 3000 条")
    parsed = [from_standard_record(row) for row in data]
    return ImportedSnapshot(
        rows=tuple(PlanRow(
            course_id=x.course_id, course_name=x.course_name,
            class_id=x.class_id or "", remaining=x.remaining,
            teacher=x.teacher or "", schedule="", room="",
        ) for x in parsed),
        sources=tuple(sorted({x.source for x in parsed})),
        observed_at=tuple(sorted({x.observed_at for x in parsed
                                  if x.observed_at is not None})),
    )
