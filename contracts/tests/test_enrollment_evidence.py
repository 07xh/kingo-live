"""No network: four legacy clients share the same official readback verdict."""
import unittest
from contracts.enrollment_evidence import (
    VerificationState, normalized_official_rows, verify_selection,
)


class EvidenceTests(unittest.TestCase):
    def verdict(self, records, **kw):
        args = dict(course_id="C01", class_id="T01", expected_account="acct-hash",
                    expected_semester="2026-1", observed_account="acct-hash",
                    observed_semester="2026-1", official_verified=True,
                    records=records)
        args.update(kw)
        return verify_selection(**args).state

    def test_only_exact_course_class_confirms(self):
        rows = normalized_official_rows(
            [{"kc": "C01", "sk": "T02"}, {"kc": "C02", "sk": "T01"},
             {"kc": "C01", "sk": "T01"}],
            {"course_id": "kc", "class_id": "sk"})
        self.assertEqual(self.verdict(rows), VerificationState.CONFIRMED)
        self.assertEqual(self.verdict(rows, class_id="T03"), VerificationState.PENDING)
        self.assertEqual(self.verdict(rows, course_id="C03"), VerificationState.PENDING)

    def test_empty_authoritative_list_is_pending_not_rejected(self):
        self.assertEqual(self.verdict(frozenset()), VerificationState.PENDING)

    def test_unverified_readback_never_confirms(self):
        record = frozenset({("C01", "T01")})
        for k in (dict(official_verified=False),
                  dict(observed_account="different"),
                  dict(observed_semester="different"),
                  dict(expected_account="")):
            with self.subTest(k=k):
                self.assertEqual(self.verdict(record, **k), VerificationState.UNAVAILABLE)
        self.assertEqual(self.verdict([("C01", "T01")]), VerificationState.UNAVAILABLE)

    def test_count_message_and_catalog_never_count_as_evidence(self):
        for raw in ({"yxms": 1}, "操作成功", [("C01", "T01")], None):
            self.assertEqual(self.verdict(raw), VerificationState.UNAVAILABLE)

    def test_incomplete_row_never_confirms(self):
        for rows in ([{"kc": "C01"}], [{"kc": "C01", "sk": ""}],
                     [{"kc": "C01", "sk": None}]):
            with self.assertRaises(ValueError):
                normalized_official_rows(rows, {"course_id": "kc", "class_id": "sk"})

    def test_ncw_u_2026_11_14_empty_or_partial_tables_never_confirm(self):
        # Sanitized structural facts from the user's second HAR:
        # DataTable 2549 had zero rows; 5036 had zero rows;
        # neither supplied a verified teaching-class identifier.
        self.assertEqual(self.verdict(frozenset()), VerificationState.PENDING)
        observed_2549 = [{"kcdm": "FICTIONAL-CODE", "kc": "Sample"}]
        observed_5036 = [{"kcdm": "FICTIONAL-CODE", "status": "selected"}]
        for rows in (observed_2549, observed_5036):
            with self.subTest(columns=list(rows[0])):
                with self.assertRaises(ValueError):
                    normalized_official_rows(
                        rows, {"course_id": "kcdm", "class_id": "skbjdm"})
        # The count/credit endpoint includes yxms but no course or class.
        self.assertEqual(self.verdict({"yxms": 2.0}),
                         VerificationState.UNAVAILABLE)

    def test_positional_schema_and_no_identity_leaks(self):
        ids = normalized_official_rows([["C01", "T01"]],
                                        {"course_id": 0, "class_id": 1})
        verdict = verify_selection(course_id="C01", class_id="T01",
            expected_account="private-account", expected_semester="2026-1",
            observed_account="private-account", observed_semester="2026-1",
            official_verified=True, records=ids)
        self.assertEqual(verdict.state, VerificationState.CONFIRMED)
        self.assertNotIn("private-account", repr(verdict))


if __name__ == "__main__":
    unittest.main()
