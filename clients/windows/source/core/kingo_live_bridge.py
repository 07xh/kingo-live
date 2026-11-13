"""Private-repository offline bridge to the shared Kingo Live HAR analyzer.

This module never imports the live HTTP client and never sends a request.
Return only numeric aggregate counts: no URL, parameter, body, or credential.
"""
from __future__ import annotations

from functools import lru_cache
import importlib.util
from pathlib import Path
import sys

MAX_HAR_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 3000


@lru_cache(maxsize=1)
def _parser():
    if hasattr(sys, "_MEIPASS"):
        path = Path(sys._MEIPASS) / "har_to_schema.py"
    else:
        path = Path(__file__).resolve().parents[3] / "kingo-live" / "har_to_schema.py"
    if not path.is_file():
        raise ValueError("离线分析组件缺失")
    name = "_kingo_live_shared_har_parser"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("无法加载离线分析组件")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def classify_har(path: str | Path) -> dict[str, int | bool]:
    """Classify local evidence without exporting a schema or any request data."""
    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise ValueError("请选择本机 HAR 普通文件")
    size = file.stat().st_size
    if not 0 < size <= MAX_HAR_BYTES:
        raise ValueError("HAR 文件大小超出本地分析限制")
    parser = _parser()
    try:
        candidates = parser.load_entries(str(file))
        if len(candidates) > MAX_ENTRIES:
            raise ValueError("HAR 请求数量超过上限")
        parser.score_all(candidates)
    except Exception as exc:
        raise ValueError("HAR 结构不符合核验要求") from None
    valid = [c for c in candidates if c.status == 200 and c.method == "GET"]
    def is_catalog(c):
        return (c.scores.get("catalog", 0) >= 7
                and "NOT_CATALOG:timetable_or_schedule" not in c.tags)
    return {
        "api_entries": len(candidates),
        "catalog_candidates": sum(1 for c in valid if is_catalog(c)),
        "enrollment_candidates": sum(
            1 for c in valid if c.scores.get("enrollment", 0) >= 6),
        "excluded_timetables": sum(
            1 for c in candidates
            if "NOT_CATALOG:timetable_or_schedule" in c.tags),
        "closed_window_signals": sum(
            1 for c in candidates if "window_closed_evidence" in c.tags),
        "needs_manual_verification": True,
    }
