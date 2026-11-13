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
import re
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import urlsplit


def term_spellings(term: str) -> tuple[str, ...]:
    """Equivalent explicit academic year/term spellings, including legacy keys.

    Only numeric year/term forms are normalized. Arbitrary term identifiers
    remain exact; a course year like 2026-2027 is NOT conflated with 2026.
    The additional variants are checked for historic pre-migration intents.
    """
    if not isinstance(term, str) or not term.strip():
        raise ValueError("缺少明确学期标识")
    raw = term.strip()
    match = re.fullmatch(r"(\\d{4}(?:-\\d{4})?)[/,-]([1-3])", raw)
    if not match:
        return (raw,)
    year, part = match.groups()
    return (f"{year}/{part}", f"{year}-{part}", f"{year},{part}")



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
        account_id, term_id = account.strip(), term_spellings(term)[0]
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
        # Legacy clients stringify a missing optional CLI value into "None".
        # Treat such placeholders as absent, NEVER as a stable class identifier.
        if class_a.lower() in ("none", "null", "unknown", "-"):
            class_a = ""
        if class_b.lower() in ("none", "null", "unknown", "-"):
            class_b = ""
        if class_a and class_b and class_a != class_b:
            raise ValueError("教学班号字段冲突，禁止提交")
        class_id = class_a or class_b
        raw_course = course.get("kch_id") or course.get("kcdm")
        if raw_course is not None and (isinstance(raw_course, bool)
                                       or not isinstance(raw_course, (str, int))):
            raise ValueError("课程代码格式不可信")
        course_code = str(raw_course or "").strip()
        # Some legacy read-only NCWU adapters cannot determine a teaching
        # class. Conservatively fence the *whole course* in that case.
        # Qiangke's write engine additionally requires an explicit class ID.
        # When an authoritative course code exists, reserve the whole course.
        # NCWU's standalone submission payload chooses by course code, not by
        # its optional teaching-class hint; a different hint must never
        # authorize a second physical write.
        target = ("course", course_code) if course_code else ("class", class_id)
        if not target[1] or target[1].lower() in ("unknown", "none", "-"):
            raise ValueError("缺少稳定的课程代码或教学班号")
        # Do not include cosmetic display name / teacher in this identity.
        identity = json.dumps([school_id, account_id, term_id, *target],
                              ensure_ascii=False, separators=(",", ":"))
        return hmac.new(self.salt, identity.encode("utf-8"), hashlib.sha256).hexdigest()

    def reserve(self, *, school: str, account: str, term: str,
                course: dict) -> bool:
        """Atomically fence a course intent across normalized/legacy term keys.

        The existing SQLite file is retained. A historical course-key or
        teaching-class-key reservation under any recognized term spelling
        blocks a new physical write, even after an app upgrade.
        """
        fp = self._fingerprint(school=school, account=account,
                               term=term, course=course)
        school_id = (urlsplit(school).netloc or school).strip().lower()
        account_id = account.strip()
        code = str(course.get("kch_id") or course.get("kcdm") or "").strip()
        class_id = str(course.get("xkid") or course.get("jxb_ids") or "").strip()
        if class_id.lower() in ("none", "null", "unknown", "-"):
            class_id = ""
        targets = ([("course", code)] if code else []) + (
            [("class", class_id)] if class_id else [])
        fingerprints = {fp}
        for spelling in term_spellings(term):
            for target_type, identity_code in targets:
                payload = json.dumps([
                    school_id, account_id, spelling, target_type, identity_code,
                ], ensure_ascii=False, separators=(",", ":"))
                fingerprints.add(hmac.new(
                    self.salt, payload.encode("utf-8"),
                    hashlib.sha256).hexdigest())

        with closing(sqlite3.connect(self.path, timeout=15,
                                     isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                placeholders = ",".join("?" for _ in fingerprints)
                row = db.execute(
                    "SELECT 1 FROM attempts WHERE fingerprint IN ("
                    + placeholders + ") LIMIT 1",
                    tuple(fingerprints)).fetchone()
                if row:
                    db.commit()
                    return False
                cursor = db.execute(
                    "INSERT INTO attempts (fingerprint, state) "
                    "VALUES (?, 'pending')", (fp,))
                db.commit()
                return cursor.rowcount == 1
            except BaseException:
                db.rollback()
                raise

    def cancel_definitely_unsent(self, *, school: str, account: str,
                                 term: str, course: dict) -> bool:
        """Release ONLY the newly reserved key after proven pre-send failure.

        Never call for a timeout, network error, response ambiguity, or any
        case where the transport could already have sent a request. Legacy
        keys are never deleted through this method.
        """
        fp = self._fingerprint(school=school, account=account,
                               term=term, course=course)
        with closing(sqlite3.connect(self.path, timeout=15,
                                     isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                result = db.execute(
                    "DELETE FROM attempts WHERE fingerprint = ? "
                    "AND state = 'pending'", (fp,))
                db.commit()
                return result.rowcount == 1
            except BaseException:
                db.rollback()
                raise
