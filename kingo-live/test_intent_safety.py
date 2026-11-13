"""Crash recovery and single-write invariants, using the real loopback engine."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import live_client as lc
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


class IntentHTTPTests(selftest.LiveHTTPTests):
    def test_confirmed_missing_course_cannot_trigger_fallback(self):
        for class_id in ('T01', ''):
            with self.subTest(class_id=class_id):
                self.state.reset()
                cfg = self.cfg()
                cfg['journal']['path'] += class_id or 'wildcard'
                cfg['targets'][0]['class_id'] = class_id
                cfg['targets'].append({'course_id': 'C002', 'class_id': 'T02'})
                eng = lc.Engine(cfg, self.rec())
                try:
                    eng.enroll(confirm=True)
                    self.state.catalog_rows = [self.state.catalog_rows[1]]
                    self.state.catalog_rows[0]['kyl'] = '1'
                    out = eng.enroll(confirm=True)
                    self.assertEqual(out[0].reason, 'already_confirmed')
                    self.assertEqual(self.state.submit_count, 1)
                finally:
                    eng.close()

    def test_missing_pending_course_cannot_trigger_fallback(self):
        cfg = self.cfg()
        cfg['targets'].append({'course_id': 'C002', 'class_id': 'T02'})
        eng = lc.Engine(cfg, self.rec())
        try:
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
    suite.addTests(IntentHTTPTests(name) for name in IntentHTTPTests.__dict__
                   if name.startswith('test_'))
    return suite


if __name__ == '__main__':
    unittest.main()
