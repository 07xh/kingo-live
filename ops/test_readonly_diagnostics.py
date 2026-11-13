"""Fully local tests for ops readonly diagnostics."""
import json
from pathlib import Path
import tempfile
import unittest

from contracts.offering import CourseOffering
from .readonly_diagnostics import read_snapshot, report_snapshot, selftest


class ReadonlyDiagnosticTests(unittest.TestCase):
    def test_valid_canonical_diff_and_no_identity_output(self):
        old = CourseOffering("ncwu.plan", "C1", "01", "Course", None, 0,
                             "2026-11-13T12:00:00+08:00")
        new = CourseOffering("ncwu.plan", "C1", "01", "Course", None, 3,
                             "2026-11-13T12:01:00+08:00")
        missing = CourseOffering("ncwu.plan", "C2", None, "Course", None, None,
                                 "2026-11-13T12:01:00+08:00")
        report = report_snapshot([new, missing], [old])
        self.assertEqual(report["current"]["unknown_seat_count"], 1)
        self.assertEqual(report["observed_seat_changes"]["counts"]["reopened"], 1)
        self.assertEqual(report["identity_comparison"]["exact"], 1)
        self.assertFalse(report["official_enrollment_verified"])
        serialized = json.dumps(report)
        self.assertNotIn("C1", serialized)
        self.assertNotIn("C2", serialized)

    def test_file_validation_and_unknown_values(self):
        entry = CourseOffering("qiangke.table", "C3", None, "Course", None,
                               None, "2026-11-13T12:01:00+08:00")
        with tempfile.TemporaryDirectory() as td:
            file = Path(td) / "courses.json"
            file.write_text(json.dumps([entry.as_dict()]), encoding="utf-8")
            self.assertEqual(len(read_snapshot(str(file))), 1)
            file.write_text(json.dumps([{**entry.as_dict(), "remaining": -1}]),
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                read_snapshot(str(file))
            file.write_text(json.dumps([entry.as_dict()]), encoding="utf-8")
            link = Path(td) / "shortcut.json"
            try:
                link.symlink_to(file)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaises(ValueError):
                    read_snapshot(str(link))

    def test_selftest(self):
        selftest()


if __name__ == "__main__":
    unittest.main()
