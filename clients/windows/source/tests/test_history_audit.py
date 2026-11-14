"""Synthetic local-history inventory. Never contact a school or write to user files."""
import base64
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from core.history_audit import (
    audit_history, windows_summary, live_jsonl_summary, shared_sqlite_summary,
    format_history,
)


class HistoryAuditTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_missing_paths_do_not_create_or_migrate_state(self):
        paths = [self.root / "windows.json", self.root / "live.jsonl",
                 self.root / "common.sqlite3"]
        result = audit_history(
            windows_path=paths[0], live_jsonl_path=paths[1],
            shared_sqlite_path=paths[2])
        self.assertEqual([x.condition for x in result], ["missing"] * 3)
        self.assertFalse(any(path.exists() for path in paths))
        self.assertNotIn(str(self.root), format_history(result))

    def test_windows_v1_history_never_auto_migrates(self):
        p = self.root / "old.json"
        raw = json.dumps({"version": 1, "attempts": ["a" * 64]})
        p.write_text(raw, encoding="utf-8")
        result = windows_summary(p)
        self.assertEqual(result.legacy_unbound, 1)
        self.assertEqual(result.condition, "legacy_unbound")
        self.assertEqual(p.read_text(encoding="utf-8"), raw)

    def test_windows_v2_bound_unbound_and_pending_counts(self):
        p = self.root / "v2.json"
        data = {"version": 2,
                "salt": base64.urlsafe_b64encode(b"x" * 32).decode("ascii"),
                "attempts": {"a" * 64: "pending", "b" * 64: "confirmed"},
                "legacy_attempts": []}
        p.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(windows_summary(p).condition, "unbound_history")
        data["scope_binding"] = "d" * 64
        p.write_text(json.dumps(data), encoding="utf-8")
        result = windows_summary(p)
        self.assertEqual(result.condition, "ok")
        self.assertEqual(result.pending, 1)
        self.assertEqual(result.confirmed, 1)
        self.assertEqual(result.scope, "bound")

    def test_jsonl_different_states_are_last_write_wins(self):
        p = self.root / "intent.jsonl"
        lines = [
            {"state": "pending", "fp": "a" * 24},
            {"state": "confirmed", "fp": "a" * 24},
            {"state": "pending", "fp": "b" * 24},
        ]
        p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
        p.with_name(p.name + ".key").write_bytes(b"k" * 32)
        p.with_name(p.name + ".scope").write_text("c" * 64 + "\n", encoding="ascii")
        before = p.read_bytes()
        result = live_jsonl_summary(p)
        self.assertEqual((result.pending, result.confirmed), (1, 1))
        self.assertEqual(result.condition, "ok")
        self.assertEqual(p.read_bytes(), before)

    def test_unbound_and_corrupt_jsonl_are_visible_not_discarded(self):
        p = self.root / "intent.jsonl"
        p.write_text(json.dumps({"fp": "a" * 24, "state": "pending"}) + "\n",
                     encoding="utf-8")
        self.assertEqual(live_jsonl_summary(p).condition, "unbound_history")
        with p.open("a", encoding="utf-8") as handle:
            handle.write("not-json\n")
        result = live_jsonl_summary(p)
        self.assertEqual(result.condition, "invalid")
        self.assertEqual(result.invalid_entries, 1)
        self.assertTrue(result.attention)

    def test_sqlite_read_only_and_missing_file_not_created(self):
        p = self.root / "fence.sqlite3"
        self.assertEqual(shared_sqlite_summary(p).condition, "missing")
        self.assertFalse(p.exists())
        with sqlite3.connect(p) as db:
            db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE attempts(fingerprint TEXT PRIMARY KEY, state TEXT NOT NULL, at TEXT)")
            db.execute("INSERT INTO metadata VALUES ('salt', ?)", ("e" * 64,))
            db.execute("INSERT INTO attempts VALUES (?, 'pending', '')", ("f" * 64,))
        before = p.read_bytes()
        status = shared_sqlite_summary(p)
        self.assertEqual(status.pending, 1)
        self.assertEqual(status.condition, "ok")
        self.assertEqual(p.read_bytes(), before)

    def test_corruption_and_symlink_are_not_followed(self):
        p = self.root / "corrupt.sqlite3"
        p.write_bytes(b"not a sqlite database")
        self.assertEqual(shared_sqlite_summary(p).condition, "invalid")
        q = self.root / "broken.json"
        q.write_text("{", encoding="utf-8")
        self.assertEqual(windows_summary(q).condition, "invalid")
        alias = self.root / "alias.json"
        try:
            alias.symlink_to(q)
        except (OSError, NotImplementedError):
            return
        self.assertEqual(windows_summary(alias).condition, "invalid")

    def test_readonly_summary_never_includes_ids_fingerprints_or_paths(self):
        p = self.root / "history.jsonl"
        p.write_text(json.dumps({
            "fp": "a" * 24, "state": "pending", "account": "SECRET_ACCOUNT",
            "course_id": "SECRET_COURSE", "note": "SECRET_COOKIE"}) + "\n",
            encoding="utf-8")
        text = format_history(audit_history(live_jsonl_path=p))
        for secret in ("SECRET_ACCOUNT", "SECRET_COURSE", "SECRET_COOKIE",
                       str(self.root), "a" * 24):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
