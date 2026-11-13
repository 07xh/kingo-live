"""Regression: capacity minus enrolled is NOT a reliable remaining-seat signal."""
from __future__ import annotations

import unittest
from ncwu.hs_kcbcx import parse_plan, _server_remaining


def fixture(*, remaining: str | None = None):
    seat = "" if remaining is None else f"<td id='tr1_qdrs'>{remaining}</td>"
    return ("<script>showTotalRecord('5327042','2')</script>"
            "<tr class='O' id='tr1'>"
            "<td id='tr1_kc'>[00001234]工程制图</td>"
            "<td id='tr1_xkrssx'>135</td>"
            "<td id='tr1_xkrs'>136/0</td>"
            + seat +
            "<td id='tr1_sksj'>周一</td></tr>"
            "<tr class='E' id='tr2'><td id='tr2_sksj'>周三</td></tr>")


class CoursePlanParserTests(unittest.TestCase):
    def test_server_remaining_not_capacity_subtraction(self):
        rows, total = parse_plan(fixture(remaining="6"))
        self.assertEqual(total, 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["left"], 6)
        self.assertEqual(rows[0]["cap"], 135)
        self.assertEqual(rows[0]["taken"], 136)
        self.assertEqual(len(rows[0]["sksj_extra"]), 1)

    def test_missing_remaining_stays_unknown(self):
        rows, _ = parse_plan(fixture())
        self.assertIsNone(rows[0]["left"])
        self.assertEqual(rows[0]["cap"], 135)
        self.assertEqual(rows[0]["taken"], 136)

    def test_missing_or_malformed_server_count_stays_unknown(self):
        for bad in ("-1", "+2", "可选 7", "3/10", "NaN", "满", "１００", "1000001"):
            with self.subTest(value=bad):
                self.assertIsNone(_server_remaining(bad))
                parsed, _ = parse_plan(fixture(remaining=bad))
                self.assertIsNone(parsed[0]["left"])
        self.assertEqual(_server_remaining(" 0 "), 0)
        self.assertEqual(_server_remaining("12"), 12)


if __name__ == "__main__":
    unittest.main()
