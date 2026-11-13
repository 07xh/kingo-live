"""Read-only adapters are offline and must never invent seat data."""
import unittest
from types import SimpleNamespace

from contracts.offering import (
    CourseOffering, from_ncwu_plan, from_qiangke_table, from_windows_item,
)


class OfferingContractTests(unittest.TestCase):
    def test_ncwu_prefers_authoritative_remaining_even_if_capacity_disagrees(self):
        item = from_ncwu_plan({
            "kcdm": "00001234", "kcmc": "工程制图", "skbjdm": "05",
            "rkjs": "老师", "cap": 135, "taken": 136, "left": 6,
        }, observed_at="2026-11-13T12:00:00+08:00")
        self.assertEqual(item.remaining, 6)
        self.assertEqual(item.course_id, "00001234")
        self.assertEqual(item.observed_at, "2026-11-13T12:00:00+08:00")

    def test_absent_remaining_stays_unknown(self):
        item = from_ncwu_plan({"kcdm": "1", "kcmc": "高数", "cap": 30, "taken": 29})
        self.assertIsNone(item.remaining)
        self.assertIn("未知", item.availability_label)
        self.assertIsNone(item.observed_at)

    def test_qiangke_bracketed_code_preserved_and_no_status_guess(self):
        item = from_qiangke_table({
            "kc": "[001234]体育1", "skbjdm": "001234-101",
            "rkjs": "老师", "xk_status": "选中",
        })
        self.assertEqual(item.course_id, "001234")
        self.assertEqual(item.course_name, "体育1")
        self.assertEqual(item.class_id, "001234-101")
        self.assertIsNone(item.remaining)

    def test_qiangke_server_qdrs_can_be_read_when_present(self):
        item = from_qiangke_table({"kcdm": "1", "kc": "体育", "qdrs": "4"})
        self.assertEqual(item.remaining, 4)

    def test_windows_readonly_course_item(self):
        item = from_windows_item(SimpleNamespace(
            course_id="00001", class_id=None, course_name="英语",
            teacher="老师", remain=0))
        self.assertEqual(item.remaining, 0)
        self.assertIn("快照", item.availability_label)

    def test_rejects_unreliable_or_negative_remaining(self):
        base = {"kcdm": "1", "kcmc": "课"}
        for value in (-1, "-1", "full", True, 1.2, "NaN"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                from_ncwu_plan({**base, "left": value})

    def test_no_course_identity_or_name_cannot_be_invented(self):
        for row in ({"kcdm": "", "kcmc": "课"}, {"kcdm": "1"}):
            with self.assertRaises(ValueError):
                from_ncwu_plan(row)

    def test_ncwu_to_csv_to_real_windows_viewer(self):
        """Use actual NCWU parser, canonical exporter, Windows CSV importer."""
        import csv
        import json
        from pathlib import Path
        import sys
        import tempfile
        from ncwu.hs_kcbcx import parse_plan, to_csv, to_standard_json

        windows = Path(__file__).resolve().parents[2] / "clients" / "windows" / "source"
        sys.path.insert(0, str(windows))
        from core.plan_csv import load_plan_csv

        html = ("<script>showTotalRecord('5327042','2')</script>"
                "<tr class='O' id='tr1'><td id='tr1_kc'>[001234]工程制图</td>"
                "<td id='tr1_skbjdm'>001234-05</td>"
                "<td id='tr1_rkjs'>老师</td><td id='tr1_xkrssx'>135</td>"
                "<td id='tr1_xkrs'>136/0</td><td id='tr1_qdrs'>6</td></tr>"
                "<tr class='E' id='tr2'><td id='tr2_sksj'>周二</td></tr>")
        rows, _ = parse_plan(html)
        with tempfile.TemporaryDirectory() as td:
            csv_path, json_path = Path(td) / "plan.csv", Path(td) / "normalized.json"
            to_csv(rows, csv_path)
            to_standard_json(rows, json_path, observed_at="2026-11-13T12:00:00+00:00")
            displayed = load_plan_csv(csv_path)
            canonical = json.loads(json_path.read_text(encoding="utf-8"))
            with csv_path.open(encoding="utf-8-sig", newline="") as f:
                headers = next(csv.reader(f))
        self.assertEqual(len(headers), 15)
        self.assertEqual(displayed[0].course_id, "001234")
        self.assertEqual(displayed[0].class_id, "001234-05")
        self.assertEqual(displayed[0].remaining, 6)
        self.assertEqual(canonical[0]["remaining"], 6)
        self.assertEqual(canonical[0]["course_id"], "001234")
        self.assertEqual(canonical[0]["observed_at"], "2026-11-13T12:00:00+00:00")
        self.assertEqual(canonical[0]["source"], "ncwu.plan")

    def test_real_qiangke_table_parser_to_canonical_without_inference(self):
        from qiangke.qk.kingo_xk import parse_table
        html = ("<tr id='tr0'><td id='tr0_kc'>[000045]体育</td>"
                "<td id='tr0_skbjdm'>000045-101</td>"
                "<td id='tr0_rkjs'>老师</td><td id='tr0_xk_status'>已选</td></tr>")
        _, rows = parse_table(html)
        self.assertEqual(len(rows), 1)
        item = from_qiangke_table(rows[0])
        self.assertEqual((item.course_id, item.class_id), ("000045", "000045-101"))
        self.assertIsNone(item.remaining)

    def test_csv_exports_fail_before_overwriting_on_invalid_count(self):
        from pathlib import Path
        import tempfile
        from ncwu.hs_kcbcx import to_csv
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "keep.csv"
            destination.write_text("KEEP", encoding="utf-8")
            with self.assertRaises(ValueError):
                to_csv([{"kcdm": "001", "kcmc": "课程", "left": -1}], destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "KEEP")

    def test_only_public_fields_exported(self):
        item = from_ncwu_plan({
            "kcdm": "1", "kcmc": "课程", "skbjdm": "2",
            "Cookie": "DONT_EXPORT", "password": "DONT_EXPORT",
        })
        blob = item.as_dict()
        self.assertEqual(set(blob), set(CourseOffering.__dataclass_fields__))
        self.assertNotIn("Cookie", blob)
        self.assertNotIn("password", blob)


if __name__ == "__main__":
    unittest.main()
