"""Crash recovery and single-write invariants, using the real loopback engine."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import live_client as lc
from qiangke.qk.intent_journal import IntentJournal
import selftest


class JournalTests(unittest.TestCase):
    def test_pending_survives_restart_without_plaintext_ids(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / 'journal.jsonl')
            offer = lc.Offering('fictional-course', 'fictional-class', '', '', 1)
            lc.Journal(path).write('pending', offer)
            self.assertTrue(lc.Journal(path).is_pending(offer))
            self.assertNotIn('fictional-course', Path(path).read_text())

    def test_new_pending_after_resolution_is_pending_again(self):
        with tempfile.TemporaryDirectory() as d:
            j = lc.Journal(str(Path(d) / 'journal.jsonl'))
            offer = lc.Offering('C', 'T', '', '', 1)
            for state in ('pending', 'rejected', 'pending'):
                j.write(state, offer)
            self.assertTrue(j.is_pending(offer))

    def test_legacy_journal_without_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'journal.jsonl'
            path.write_text('{"state":"pending","fp":"old-key"}\n')
            with self.assertRaises(RuntimeError):
                lc.Journal(str(path))


class ScopeTests(unittest.TestCase):
    def test_account_and_term_must_match_journal_scope(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "intent.jsonl")
            journal = lc.Journal(path)
            scope = {"account": "fictional-user", "term": "2026-1",
                     "school": "https://school.example.invalid"}
            journal.bind_scope(**scope)
            lc.Journal(path).bind_scope(**scope)
            with self.assertRaises(RuntimeError):
                journal.bind_scope(**{**scope, "account": "other-user"})
            with self.assertRaises(RuntimeError):
                journal.bind_scope(**{**scope, "term": "2026-2"})
            with self.assertRaises(RuntimeError):
                journal.bind_scope(**{**scope, "school": "https://another.example.invalid"})

    def test_existing_unscoped_journal_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            journal = lc.Journal(str(Path(d) / "legacy.jsonl"))
            journal.write("pending", lc.Offering("C1", "T01", 1))
            with self.assertRaises(RuntimeError):
                journal.bind_scope(account="fictional", term="2026-1",
                                   school="https://school.example.invalid")


class IntentHTTPTests(selftest.LiveHTTPTests):
    def test_confirmed_missing_course_cannot_trigger_fallback(self):
        for class_id in ('T01', ''):
            with self.subTest(class_id=class_id):
                self.state.reset()
                cfg = self.cfg()
                cfg['journal']['path'] += class_id or 'wildcard'
                cfg["journal"]["shared_intent_db"] = cfg["journal"]["path"] + ".sqlite3"
                cfg['targets'][0]['class_id'] = class_id
                cfg['targets'].append({'course_id': 'C002', 'class_id': 'T02'})
                eng = lc.Engine(cfg, self.rec())
                try:
                    eng.enroll(confirm=True)
                    self.state.catalog_rows = [self.state.catalog_rows[1]]
                    self.state.catalog_rows[0]['kyl'] = '1'
                    out = eng.enroll(confirm=True)
                    self.assertEqual(out[0].reason, 'already_enrolled_in_official_records')
                    self.assertEqual(self.state.submit_count, 1)
                finally:
                    eng.close()

    def test_missing_pending_course_cannot_trigger_fallback(self):
        cfg = self.cfg()
        cfg['targets'].append({'course_id': 'C002', 'class_id': 'T02'})
        eng = lc.Engine(cfg, self.rec())
        try:
            eng.journal.bind_scope(
                account=eng.cfg["account_scope"], term=eng.cfg["xnxq"],
                school=eng.cfg["base_url"])
            eng.journal.write('pending', lc.Offering('C001', 'T01', '', '', 1))
            self.state.catalog_rows = [self.state.catalog_rows[1]]
            self.state.catalog_rows[0]['kyl'] = '1'
            eng.enroll(confirm=True)
            self.assertEqual(self.state.submit_count, 0)
        finally:
            eng.close()

    def test_pending_full_course_still_blocks_fallback(self):
        cfg = self.cfg()
        cfg['targets'].append({'course_id': 'C002', 'class_id': 'T02'})
        eng = lc.Engine(cfg, self.rec())
        try:
            eng.journal.bind_scope(
                account=eng.cfg["account_scope"], term=eng.cfg["xnxq"],
                school=eng.cfg["base_url"])
            eng.journal.write('pending', lc.Offering('C001', 'T01', '', '', 1))
            self.state.catalog_rows[0]['kyl'] = '0'
            self.state.catalog_rows[1]['kyl'] = '1'
            out = eng.enroll(confirm=True)
            self.assertEqual(out[0].reason, 'pending_intent_needs_reconciliation')
            self.assertEqual(self.state.submit_count, 0)
        finally:
            eng.close()

    def test_confirmed_intent_cannot_be_sent_again(self):
        eng = lc.Engine(self.cfg(), self.rec())
        try:
            eng.enroll(confirm=True)
            restarted = lc.Engine(self.cfg(), self.rec('_again'))
            try:
                restarted.enroll(confirm=True)
                self.assertEqual(self.state.submit_count, 1)
            finally:
                restarted.close()
        finally:
            eng.close()

    def test_parallel_strategy_never_duplicates_same_intent(self):
        eng = lc.Engine(self.cfg(concurrency=3, race_repeat=2, burst=2), self.rec())
        try:
            eng.enroll(confirm=True)
            self.assertEqual(self.state.submit_count, 1)
        finally:
            eng.close()

    def test_pending_is_durable_before_network_send(self):
        eng = lc.Engine(self.cfg(), self.rec())
        original = eng._budgeted

        def request(tr, method, path, **kw):
            if path == '/submit':
                self.assertEqual(len(lc.Journal(str(eng.journal.path)).pending_fingerprints()), 1)
                return lc.RawResponse(status=0, headers={}, body=b'', error='lost response')
            return original(tr, method, path, **kw)

        try:
            with patch.object(eng, '_budgeted', side_effect=request):
                eng.enroll(confirm=True)
            again = lc.Engine(self.cfg(), self.rec('_restart'))
            try:
                out = again.enroll(confirm=True)
                self.assertEqual(out[0].reason, 'pending_intent_needs_reconciliation')
                self.assertEqual(self.state.submit_count, 0)
            finally:
                again.close()
        finally:
            eng.close()

    def test_missing_account_scope_blocks_write_before_transport(self):
        cfg = self.cfg()
        cfg["account_scope"] = ""
        eng = lc.Engine(cfg, self.rec("_no_account"))
        try:
            with self.assertRaises(RuntimeError):
                eng.enroll(confirm=True)
            self.assertEqual(self.state.submit_count, 0)
        finally:
            eng.close()

    def test_reconciliation_ignores_classless_official_records(self):
        eng = lc.Engine(self.cfg(), self.rec("_classless_reconcile"))
        try:
            target = lc.Offering("C001", "", 1)
            eng.journal.write("pending", target)
            self.state.enrolled = [{"kcid": "C001", "skbj": ""}]
            report = eng.reconcile()
            self.assertEqual(report["resolved"], 0)
            self.assertEqual(report["remaining_pending"], 1)
        finally:
            eng.close()

    def test_exact_readback_does_not_accept_course_only_or_other_class(self):
        eng = lc.Engine(self.cfg(), self.rec('_identity'))
        try:
            expected = lc.Offering('C001', 'T01', 1)
            self.state.enrolled = [{'kcid': 'C001', 'skbj': ''}]
            matched, _ = eng._readback(eng.transports[0], expected)
            self.assertFalse(matched)
            self.state.enrolled = [{'kcid': 'C001', 'skbj': 'T02'}]
            matched, _ = eng._readback(eng.transports[0], expected)
            self.assertFalse(matched)
            self.state.enrolled = [{'kcid': 'C001', 'skbj': 'T01'}]
            matched, _ = eng._readback(eng.transports[0], expected)
            self.assertTrue(matched)
            matched, result = eng._readback(
                eng.transports[0], lc.Offering('C001', '', 1))
            self.assertFalse(matched)
            self.assertIn('missing_teaching_class', result.error)
        finally:
            eng.close()

    def test_no_physical_submit_when_catalog_class_missing(self):
        cfg = self.cfg()
        cfg['targets'][0]['class_id'] = ''
        self.state.catalog_rows[0]['skbj'] = ''
        eng = lc.Engine(cfg, self.rec('_classless'))
        try:
            out = eng.enroll(confirm=True)
            self.assertEqual(self.state.submit_count, 0)
            self.assertFalse(any(x.success for x in out))
            self.assertFalse(eng.journal.pending_fingerprints())
        finally:
            eng.close()

    def test_empty_readback_does_not_release_pending_intent(self):
        self.state.empty_readback = True
        eng = lc.Engine(self.cfg(), self.rec())
        try:
            eng.enroll(confirm=True)
            self.assertEqual(len(eng.journal.pending_fingerprints()), 1)
            report = eng.reconcile()
            self.assertEqual(report['remaining_pending'], 1)
        finally:
            eng.close()


    def test_qiangke_ncwu_shared_intent_prevents_kingo_live_post(self):
        """The existing qiangke/ and ncwu/ SQLite gate is reused, not cloned."""
        cfg = self.cfg()
        self.assertTrue(IntentJournal(cfg["journal"]["shared_intent_db"]).reserve(
            school=cfg["base_url"], account=cfg["account_scope"],
            term=cfg["xnxq"], course={"kch_id": "C001", "xkid": "T02"}))
        engine = lc.Engine(cfg, self.rec("_other_engine"))
        try:
            result = engine.enroll(confirm=True)
            self.assertEqual(result[0].reason, "shared_intent_already_reserved")
            self.assertFalse(result[0].success)
            self.assertEqual(self.state.submit_count, 0)
            self.assertEqual(engine.journal.pending_fingerprints(), set())
        finally:
            engine.close()

    def test_shared_intent_db_failure_is_fail_closed(self):
        cfg = self.cfg()
        engine = lc.Engine(cfg, self.rec("_failed_db"))
        try:
            with patch("live_client.reserve_shared_intent",
                       side_effect=OSError("synthetic disk failure")):
                result = engine.enroll(confirm=True)
            self.assertEqual(result[0].reason, "shared_intent_journal_unavailable")
            self.assertFalse(result[0].success)
            self.assertEqual(self.state.submit_count, 0)
            self.assertEqual(engine.journal.pending_fingerprints(), set())
        finally:
            engine.close()

    def test_shared_intent_reservation_occurs_before_real_http(self):
        cfg = self.cfg()
        engine = lc.Engine(cfg, self.rec("_shared_before_send"))
        original = engine._budgeted
        fired = []
        def inspect_send(tr, method, path, **kwargs):
            if path == "/submit":
                fired.append(True)
                self.assertFalse(IntentJournal(
                    engine.shared_intent_db).reserve(
                        school=cfg["base_url"], account=cfg["account_scope"],
                        term=cfg["xnxq"],
                        course={"kch_id": "C001", "xkid": "T01"}))
                return lc.RawResponse(status=0, headers={}, body=b"",
                                      error="lost response")
            return original(tr, method, path, **kwargs)
        try:
            with patch.object(engine, "_budgeted", side_effect=inspect_send):
                result = engine.enroll(confirm=True)
            self.assertEqual(len(fired), 1)
            self.assertEqual(self.state.submit_count, 0)
            self.assertEqual(result[0].reason, "submission_unknown")
            restarted = lc.Engine(cfg, self.rec("_shared_restart"))
            try:
                again = restarted.enroll(confirm=True)
                self.assertEqual(again[0].reason,
                                 "pending_intent_needs_reconciliation")
                self.assertEqual(self.state.submit_count, 0)
            finally:
                restarted.close()
        finally:
            engine.close()

    def test_stale_confirmed_journal_never_claims_success(self):
        cfg = self.cfg()
        engine = lc.Engine(cfg, self.rec("_stale"))
        try:
            engine.journal.bind_scope(
                account=cfg["account_scope"], term=cfg["xnxq"],
                school=cfg["base_url"])
            target = lc.Offering("C001", "T01", "", "", 1)
            engine.journal.write("confirmed", target, "old_approval")
            result = engine.enroll(confirm=True)
            self.assertEqual(result[0].reason,
                             "historical_confirmation_not_in_official_records")
            self.assertFalse(result[0].success)
            self.assertEqual(self.state.submit_count, 0)
        finally:
            engine.close()

    def test_stale_confirmed_journal_cannot_bypass_scope_gate(self):
        cfg = self.cfg()
        cfg["enrollment"]["official_verified"] = False
        engine = lc.Engine(cfg, self.rec("_stale_scope"))
        try:
            engine.journal.bind_scope(
                account=cfg["account_scope"], term=cfg["xnxq"],
                school=cfg["base_url"])
            engine.journal.write(
                "confirmed", lc.Offering("C001", "T01", "", "", 1),
                "old_approval")
            result = engine.enroll(confirm=True)
            self.assertEqual(result[0].reason,
                             "official_readback_contract_not_verified")
            self.assertFalse(result[0].success)
            self.assertEqual(self.state.submit_count, 0)
        finally:
            engine.close()

    def test_unverified_enrollment_contract_never_sends_write(self):
        cfg = self.cfg()
        cfg["enrollment"]["official_verified"] = False
        engine = lc.Engine(cfg, self.rec("_unverified"))
        try:
            result = engine.enroll(confirm=True)
            self.assertEqual(self.state.submit_count, 0)
            self.assertEqual(result[0].reason, "official_readback_contract_not_verified")
        finally:
            engine.close()

    def test_cross_account_or_semester_readback_blocks_write(self):
        for delta in ({"account_scope": "different"},
                      {"semester": "different"}):
            with self.subTest(delta=delta):
                self.state.reset()
                cfg = self.cfg()
                cfg["enrollment"].update(delta)
                engine = lc.Engine(cfg, self.rec("_wrong_scope"))
                try:
                    result = engine.enroll(confirm=True)
                    self.assertEqual(self.state.submit_count, 0)
                    self.assertEqual(result[0].reason, "official_readback_contract_not_verified")
                finally:
                    engine.close()

    def test_incomplete_official_rows_remain_pending_not_confirmed(self):
        # The mock acknowledges the write but returns an incomplete row.
        cfg = self.cfg()
        engine = lc.Engine(cfg, self.rec("_incomplete"))
        original = engine._official_records
        reads = 0
        def incomplete(response):
            nonlocal reads
            reads += 1
            # Account/semester preflight is intact. Corrupt only the official
            # readback AFTER the single physical submission.
            if reads == 1:
                return original(response)
            return lc.normalized_official_rows([{"kcid": "C001"}],
                {"course_id": "kcid", "class_id": "skbj"})
        engine._official_records = incomplete
        try:
            out = engine.enroll(confirm=True)
            self.assertFalse(out[0].success)
            self.assertEqual(out[0].reason, "readback_unavailable")
            self.assertEqual(self.state.submit_count, 1)
            self.assertTrue(engine.journal.pending_fingerprints())
        finally:
            engine._official_records = original
            engine.close()


def load_tests(loader, tests, pattern):
    suite = loader.loadTestsFromTestCase(JournalTests)
    suite.addTests(loader.loadTestsFromTestCase(ScopeTests))
    suite.addTests(IntentHTTPTests(name) for name in IntentHTTPTests.__dict__
                   if name.startswith('test_'))
    return suite


if __name__ == '__main__':
    unittest.main()
