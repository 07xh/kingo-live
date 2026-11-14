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

    def test_snapshot_order_is_not_assumed(self):
        first = CourseOffering("ncwu.plan", "C1", "01", "Course", None, 0,
                               "2026-11-13T12:00:00+08:00")
        later = CourseOffering("ncwu.plan", "C1", "01", "Course", None, 3,
                               "2026-11-13T12:01:00+08:00")
        backwards = report_snapshot([first], [later])["observed_seat_changes"]
        self.assertEqual(backwards["time_order"], "reversed")
        self.assertEqual(backwards["events"], 0)
        unknown = CourseOffering("ncwu.plan", "C1", "01", "Course", None, 3)
        missing = report_snapshot([unknown], [first])["observed_seat_changes"]
        self.assertEqual(missing["time_order"], "unverified")
        self.assertEqual(missing["events"], 0)
        same = CourseOffering("ncwu.plan", "C1", "01", "Course", None, 3,
                              "2026-11-13T12:00:00+08:00")
        overlap = report_snapshot([same], [first])["observed_seat_changes"]
        self.assertEqual(overlap["time_order"], "overlapping")
        self.assertEqual(overlap["events"], 0)

    def test_duplicate_class_never_generates_delta(self):
        first = CourseOffering("ncwu.plan", "C1", "01", "Course", None, 0,
                               "2026-11-13T12:00:00+08:00")
        later = CourseOffering("ncwu.plan", "C1", "01", "Course", None, 3,
                               "2026-11-13T12:01:00+08:00")
        report = report_snapshot([later, later], [first])
        self.assertEqual(report["observed_seat_changes"]["time_order"], "forward")
        self.assertEqual(report["observed_seat_changes"]
                         ["skipped_ambiguous_class_identities"], 1)
        self.assertEqual(report["observed_seat_changes"]["events"], 0)
        self.assertGreater(report["identity_comparison"]["needs_review"], 0)

    def test_selftest(self):
        selftest()


if __name__ == "__main__":
    unittest.main()
