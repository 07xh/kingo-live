"""Read-only cross-project identity audits must never imply selection."""
import unittest
from dataclasses import replace

from contracts.identity_audit import (
    IdentityVerdict, LABELS, compare_offerings,
)
from contracts.offering import from_ncwu_plan, from_qiangke_table


def left(course="000045", teaching="000045-101", **kwargs):
    return from_ncwu_plan({
        "kcdm": course, "skbjdm": teaching, "kcmc": "体育",
        "rkjs": "老师", "left": 4, **kwargs,
    })


def right(course="000045", teaching="000045-101", **kwargs):
    return from_qiangke_table({
        "kcdm": course, "skbjdm": teaching, "kc": "体育", **kwargs,
    })


class IdentityAuditTests(unittest.TestCase):
    def test_two_real_parser_adapters_compare_only_exact_ids(self):
        a = compare_offerings([left()], [right()])
        self.assertEqual(a.rows[0].verdict, IdentityVerdict.EXACT)
        self.assertEqual(a.exact, 1)
        self.assertEqual(a.needs_review, 0)
        self.assertIn("仅快照", a.rows[0].verdict_label)
        self.assertNotIn("已选成功", a.rows[0].verdict_label)

    def test_course_only_cannot_be_called_exact(self):
        a = compare_offerings([left()], [right(teaching="000045-102")])
        self.assertEqual(a.rows[0].verdict, IdentityVerdict.COURSE_ONLY)
        self.assertEqual(a.rows[0].reference_classes, ("000045-102",))
        self.assertEqual(a.exact, 0)

    def test_missing_class_cannot_be_confirmed(self):
        a = compare_offerings([left(teaching=None)], [right()])
        self.assertEqual(a.rows[0].verdict, IdentityVerdict.MISSING_CLASS)

    def test_leading_zeros_are_identity_significant(self):
        a = compare_offerings([left()], [right(course="45", teaching="000045-101")])
        self.assertEqual(a.rows[0].verdict, IdentityVerdict.CLASS_ID_REUSED)
        self.assertNotEqual(a.rows[0].verdict, IdentityVerdict.EXACT)

    def test_missing_entry_is_not_official_absence(self):
        a = compare_offerings([left()], [])
        self.assertEqual(a.rows[0].verdict, IdentityVerdict.NOT_PRESENT)
        self.assertIn("不代表官方未选", a.rows[0].verdict_label)

    def test_duplicates_never_upgrade_to_match(self):
        a = compare_offerings([left()], [right(), right()])
        self.assertEqual(a.rows[0].verdict, IdentityVerdict.AMBIGUOUS)
        b = compare_offerings([left(), left()], [right()])
        self.assertTrue(all(r.verdict == IdentityVerdict.AMBIGUOUS for r in b.rows))

    def test_cosmetic_name_change_does_not_change_id(self):
        a = compare_offerings([left()], [right(kc="另一名称", rkjs="另一教师")])
        self.assertEqual(a.rows[0].verdict, IdentityVerdict.EXACT)
        self.assertIn("课程名称不同", a.rows[0].notes)
        self.assertIn("教师名称不同", a.rows[0].notes)

    def test_invalid_source_and_private_data_rejected(self):
        with self.assertRaises(ValueError):
            compare_offerings([replace(left(), source="unknown")], [right()])
        with self.assertRaises(ValueError):
            compare_offerings([replace(left(), remaining=-1)], [right()])
        with self.assertRaises(ValueError):
            compare_offerings([{"course_id": "fake"}], [right()])

    def test_all_verdicts_are_explicitly_not_success_claims(self):
        self.assertTrue(all("已选成功" not in label
                            for label in LABELS.values()))


if __name__ == "__main__":
    unittest.main()
