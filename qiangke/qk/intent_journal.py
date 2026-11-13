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
        """Stable per-teaching-class deduplication; labels never authorize replay."""
        if not all(isinstance(v, str) for v in (school, account, term)):
            raise ValueError("提交意图身份格式错误")
        school_id = (urlsplit(school).netloc or school).strip().lower()
        account_id, term_id = account.strip(), term.strip()
        if school_id in ("", "unknown", "none", "-"):
            raise ValueError("缺少可信教务系统标识")
        if not account_id:
            raise ValueError("缺少账户标识")
        if term_id.lower() in ("", "unknown", "none", "-"):
            raise ValueError("缺少明确学期标识")
        if not isinstance(course, dict):
            raise ValueError("目标课程必须是字典")
        class_a, class_b = course.get("xkid"), course.get("jxb_ids")
        if any(v is not None and (isinstance(v, bool) or not isinstance(v, (str, int)))
               for v in (class_a, class_b)):
            raise ValueError("教学班号格式不可信")
        class_a, class_b = str(class_a or "").strip(), str(class_b or "").strip()
        if class_a and class_b and class_a != class_b:
            raise ValueError("教学班号字段冲突，禁止提交")
        class_id = class_a or class_b
        if class_id.lower() in ("", "unknown", "none", "-"):
            raise ValueError("缺少稳定教学班号")
        # Course name, teacher and optionally changing course code are ignored:
        # blocking an ambiguous duplicate is safer than risking a second POST.
        identity = json.dumps([school_id, account_id, term_id, class_id],
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
