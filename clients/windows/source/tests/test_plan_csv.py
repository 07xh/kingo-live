"""No HTTP or credentials: the CSV viewer only reads a local snapshot."""
from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest
from core.plan_csv import load_plan_csv, MAX_CSV_BYTES


HEADERS = ("课程号", "课程名称", "教学班号", "可选人数", "任课教师", "上课时间", "上课地点")


class PlanCsvTests(unittest.TestCase):
    def make_file(self, folder: Path, headers=HEADERS, records=None) -> Path:
        path = folder / "plan.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(records if records is not None else [
                ["00001", "工程制图", "05", "6", "老师", "周一", "教室"],
                ["00002", "高等数学", "06", "", "", "", ""],
                ["00003", "电路", "07", "0", "", "", ""],
            ])
        return path

    def test_bom_and_unknown_are_not_converted_to_zero(self):
        with tempfile.TemporaryDirectory() as td:
            rows = load_plan_csv(self.make_file(Path(td)))
        self.assertEqual([x.course_id for x in rows], ["00001", "00002", "00003"])
        self.assertEqual(rows[0].remaining, 6)
        self.assertEqual(rows[1].remaining_label, "未知")
        self.assertEqual(rows[2].remaining_label, "0（快照已满）")

    def test_rejects_invalid_header_and_negative_or_malformed_seat_count(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            with self.assertRaises(ValueError):
                load_plan_csv(self.make_file(folder, headers=["wrong"], records=[["x"]]))
            for bad in ("-1", "NaN", "=1+1"):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    load_plan_csv(self.make_file(folder, records=[
                        ["1", "课", "1", bad, "", "", ""]]))

    def test_rejects_file_size_and_malformed_rows(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            p = self.make_file(folder)
            with p.open("ab") as file:
                file.truncate(MAX_CSV_BYTES + 1)
            with self.assertRaises(ValueError):
                load_plan_csv(p)
            p = self.make_file(folder, records=[["1", "课程", "班", "5", "", "", "", "EXTRA"]])
            with self.assertRaises(ValueError):
                load_plan_csv(p)


if __name__ == "__main__":
    unittest.main()
