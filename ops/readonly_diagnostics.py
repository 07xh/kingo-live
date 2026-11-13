"""Local-only reporting for canonical NCWU/Qiangke course snapshot exports.

Consumes the existing contracts.offering schema; reuses the existing
contracts.identity_audit logic and ops.seat_events monitor. It is NOT an
enrollment validator, credential manager, polling client, or submission tool.
Only anonymous aggregate numbers are printed; no raw course/identity rows.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
from typing import Sequence

from contracts.offering import CourseOffering, from_standard_record
from contracts.identity_audit import compare_offerings
from .offering_bridge import normalize_readonly
from .seat_events import ReleaseMonitor

MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 3000


def read_snapshot(path: str) -> list[CourseOffering]:
    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise ValueError("expected an ordinary local JSON file (no symlinks)")
    if not 0 < file.stat().st_size <= MAX_BYTES:
        raise ValueError("snapshot exceeds local diagnostics size limits")
    raw = json.loads(file.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list) or len(raw) > MAX_RECORDS:
        raise ValueError("expected a canonical snapshot array of up to 3000 records")
    return [from_standard_record(row) for row in raw]


def summarize(records: Sequence[CourseOffering]) -> dict:
    normalized = [normalize_readonly(x) for x in records]
    return {
        "count": len(normalized),
        "sources": dict(sorted(Counter(x.source for x in normalized).items())),
        "exact_class_identity": sum(x.identity_level == "exact" for x in normalized),
        "missing_class_identity": sum(x.class_id is None for x in normalized),
        "known_seat_count": sum(x.remaining is not None for x in normalized),
        "unknown_seat_count": sum(x.remaining is None for x in normalized),
    }


def report_snapshot(current: Sequence[CourseOffering],
                    before: Sequence[CourseOffering] | None = None) -> dict:
    current = [from_standard_record(x.as_dict()) for x in current]
    before = ([from_standard_record(x.as_dict()) for x in before]
              if before is not None else None)
    result = {
        "mode": "local_readonly",
        "network_requests": 0,
        "submissions": 0,
        "official_enrollment_verified": False,
        "current": summarize(current),
    }
    if before is not None:
        audit = compare_offerings(current, before)
        counts = Counter(x.verdict.value for x in audit.rows)
        monitor = ReleaseMonitor()
        monitor.observe([normalize_readonly(x) for x in before])
        events = monitor.observe([normalize_readonly(x) for x in current])
        result["previous"] = summarize(before)
        result["identity_comparison"] = {
            "exact": audit.exact,
            "needs_review": audit.needs_review,
            "verdict_counts": dict(sorted(counts.items())),
            "does_not_prove_enrollment": True,
        }
        result["observed_seat_changes"] = {
            "events": len(events),
            "counts": dict(sorted(Counter(e.kind for e in events).items())),
            "partial_snapshots_never_infer_disappearance": True,
        }
    return result


def selftest() -> None:
    a = CourseOffering("ncwu.plan", "C1", "01", "Example",
                       None, 0, "2026-11-13T12:00:00+08:00")
    b = CourseOffering("ncwu.plan", "C1", "01", "Example",
                       None, 2, "2026-11-13T12:01:00+08:00")
    c = CourseOffering("ncwu.plan", "C2", None, "Example",
                       None, None, "2026-11-13T12:01:00+08:00")
    result = report_snapshot([b, c], [a])
    assert result["observed_seat_changes"]["counts"]["reopened"] == 1
    assert result["current"]["unknown_seat_count"] == 1
    assert result["identity_comparison"]["needs_review"] == 1
    encoded = json.dumps(result, ensure_ascii=False)
    assert "C1" not in encoded and "C2" not in encoded
    print("ops readonly diagnostics self-test OK")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local readonly course diagnostics")
    parser.add_argument("--snapshot", help="current canonical JSON list")
    parser.add_argument("--compare", help="optional prior canonical JSON list")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        selftest()
        return 0
    if not args.snapshot:
        parser.error("--snapshot is required unless --self-test is set")
    result = report_snapshot(read_snapshot(args.snapshot),
                             read_snapshot(args.compare) if args.compare else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
