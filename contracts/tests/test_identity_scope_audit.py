"""Only synthetic identity values: no real accounts or HAR payloads."""
import unittest

from contracts.identity_scope_audit import ScopeVerdict, audit_scopes


def fictional(term="2026-0", *, course="C-TEST", cls="K-TEST",
              account="anonymous-fixture"):
    return {"school": "https://school.example.invalid/hsjw/",
            "account": account, "term": term, "course_id": course,
            "class_id": cls}


class IdentityScopeAuditTests(unittest.TestCase):
    def test_four_identical_executors_are_structural_only(self):
        case = {
            "qiangke": fictional("2026-0"),
            "ncwu": fictional("2026/0"),
            "kingo-live": fictional("2026,0"),
            "windows": fictional("2026-0"),
        }
        report = audit_scopes(case)
        self.assertEqual(report.verdict, ScopeVerdict.ALIGNED)
        self.assertEqual(report.checked_clients, 4)
        self.assertNotIn("anonymous-fixture", repr(report))

    def test_actual_har_term_zero_never_aliases_cli_term_one(self):
        # NCWU HAR on 2026-11-14: xn=2026, xqM=0, xqName=第一学期.
        # A script's '1' cannot be assumed equivalent without source proof.
        report = audit_scopes({
            "ncwu_har": fictional("2026-0"),
            "cli_default": fictional("2026-1"),
        })
        self.assertEqual(report.verdict, ScopeVerdict.MISMATCH)
        self.assertEqual(report.mismatched_fields, ("term",))

    def test_academic_year_range_not_assumed_from_year_start(self):
        report = audit_scopes({
            "ncwu_har": fictional("2026-0"),
            "other_config": fictional("2026-2027-1"),
        })
        self.assertEqual(report.verdict, ScopeVerdict.MISMATCH)
        self.assertEqual(report.mismatched_fields, ("term",))

    def test_course_and_class_are_independent(self):
        for other in (fictional(course="C-OTHER"),
                      fictional(cls="K-OTHER")):
            with self.subTest(other=other["class_id"] == "K-OTHER"):
                rep = audit_scopes({"a": fictional(), "b": other})
                self.assertEqual(rep.verdict, ScopeVerdict.MISMATCH)

    def test_missing_scope_is_incomplete_without_raw_values(self):
        value = fictional()
        value["account"] = ""
        value["class_id"] = "None"
        rep = audit_scopes({"a": fictional(), "b": value})
        self.assertEqual(rep.verdict, ScopeVerdict.INCOMPLETE)
        self.assertEqual(rep.missing_fields, ("account", "class_id"))
        self.assertNotIn("C-TEST", repr(rep))

    def test_school_paths_match_but_cross_host_or_account_do_not(self):
        x = fictional()
        x["school"] = "SCHOOL.EXAMPLE.INVALID"
        self.assertEqual(
            audit_scopes({"a": fictional(), "b": x}).verdict,
            ScopeVerdict.ALIGNED)
        y = fictional(account="different-account")
        self.assertEqual(
            audit_scopes({"a": fictional(), "b": y}).mismatched_fields,
            ("account",))
        y["school"] = "https://other.example.invalid"
        self.assertEqual(
            audit_scopes({"a": fictional(), "b": y}).mismatched_fields,
            ("school", "account"))

    def test_invalid_count_and_credentials_are_not_echoed(self):
        with self.assertRaises(ValueError):
            audit_scopes({"only": fictional()})
        for key in ("school", "account", "term"):
            bad = fictional()
            bad[key] = "untrusted\nvalue"
            rep = audit_scopes({"a": fictional(), "b": bad})
            self.assertEqual(rep.verdict, ScopeVerdict.INCOMPLETE)
            self.assertNotIn("untrusted", repr(rep))


if __name__ == "__main__":
    unittest.main()
