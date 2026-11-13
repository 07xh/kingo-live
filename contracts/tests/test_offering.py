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
