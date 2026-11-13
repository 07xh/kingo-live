"""Offline integration and privacy gates for the shared HAR classifier."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.kingo_live_bridge import MAX_HAR_BYTES, classify_har


class KingoLiveBridgeTests(unittest.TestCase):
    def test_synthetic_capture_produces_only_aggregate_counts(self):
        fixture = (Path(__file__).resolve().parents[4]
                   / "kingo-live" / "capture.example.har")
        result = classify_har(fixture)
        self.assertGreater(result["api_entries"], 0)
        self.assertTrue(result["needs_manual_verification"])
        self.assertEqual(set(result), {
            "api_entries", "catalog_candidates", "enrollment_candidates",
            "excluded_timetables", "closed_window_signals",
            "needs_manual_verification",
        })
        self.assertTrue(all(type(v) in (int, bool) for v in result.values()))
        self.assertNotIn("SECRET123", json.dumps(result))
        self.assertNotIn("cookie", json.dumps(result).lower())

    def test_malformed_file_is_rejected_without_echoing_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.har"
            path.write_text('SECRET123 NOT VALID JSON', encoding="utf-8")
            with self.assertRaises(ValueError) as err:
                classify_har(path)
            self.assertNotIn("SECRET123", str(err.exception))

    def test_oversize_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "large.har"
            with path.open("wb") as fh:
                fh.truncate(MAX_HAR_BYTES + 1)
            with self.assertRaises(ValueError):
                classify_har(path)


if __name__ == "__main__":
    unittest.main()
