"""Crash-safe cross-process intent fence for a single enrollment target.

Records salted HMAC fingerprints only; no credentials, course names or
identifiers are written. A reserved intent is never implicitly cleared after
a timeout, rejection, process crash or application restart. The official
enrollment record must be inspected before any later enrollment decision.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import urlsplit


class IntentJournal:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS attempts (fingerprint TEXT PRIMARY KEY, state TEXT NOT NULL, at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("INSERT OR IGNORE INTO metadata (key, value) VALUES ('salt', ?)", (secrets.token_hex(32),))
            self.salt = bytes.fromhex(db.execute("SELECT value FROM metadata WHERE key='salt'").fetchone()[0])
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    def _fingerprint(self, *, school: str, account: str, term: str, course: dict) -> str:
        if not account.strip():
            raise ValueError("账户标识为空，拒绝创建提交意图")
        course_identity = str(course.get("kch_id") or course.get("name") or "").strip()
        class_identity = str(course.get("xkid") or course.get("jxb_ids") or "").strip()
        if not (course_identity or class_identity):
            raise ValueError("目标课程标识为空，拒绝提交")
        # Teacher labels may change without changing the registered course.
        # Do not let a cosmetic edit create a new submission identity.
        school_id = urlsplit(school).netloc or school
        identity = json.dumps([school_id.strip().lower(), account.strip(), term.strip(),
                               course_identity, class_identity],
                              ensure_ascii=False, separators=(",", ":"))
        return hmac.new(self.salt, identity.encode("utf-8"), hashlib.sha256).hexdigest()

    def reserve(self, *, school: str, account: str, term: str, course: dict) -> bool:
        """Atomically reserve BEFORE HTTP write; False includes prior crashes.

        BEGIN IMMEDIATE + UNIQUE fingerprint makes competing processes and
        threads unable to acquire the same enrollment intent twice.
        """
        fp = self._fingerprint(school=school, account=account, term=term, course=course)
        with closing(sqlite3.connect(self.path, timeout=15, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                cursor = db.execute(
                    "INSERT OR IGNORE INTO attempts (fingerprint, state) VALUES (?, 'pending')",
                    (fp,))
                db.commit()
                return cursor.rowcount == 1
            except BaseException:
                db.rollback()
                raise
