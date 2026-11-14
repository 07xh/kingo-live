"""Read-only local journal inventory for Windows, kingo-live and shared SQLite.

This is NOT per-course reconciliation: the formats intentionally use different
HMAC salts and fingerprints. No file is created, migrated, deleted, repaired or
marked successful. Never return a path, ID, fingerprint, secret or raw line.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import json
from pathlib import Path
import re
import sqlite3
from typing import Optional

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_SQLITE_BYTES = 32 * 1024 * 1024
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX24 = re.compile(r"^[0-9a-f]{24}$")
_STATES = frozenset({"pending", "rejected", "confirmed"})


@dataclass(frozen=True)
class JournalSummary:
    kind: str
    condition: str
    pending: int = 0
    confirmed: int = 0
    rejected: int = 0
    legacy_unbound: int = 0
    invalid_entries: int = 0
    scope: str = "unknown"

    @property
    def attention(self) -> bool:
        return self.condition != "ok" or bool(
            self.pending or self.legacy_unbound or self.invalid_entries)


def _read_path(path: Optional[Path], limit: int) -> Optional[Path]:
    if path is None:
        return None
    p = Path(path).expanduser()
    # Reject symbolic paths and symbolic parents, even when the destination
    # does not exist. No attempt to open a missing file in write mode.
    if p.is_symlink() or any(parent.is_symlink() for parent in p.parents):
        raise ValueError("symlink")
    if not p.exists():
        return None
    if not p.is_file() or not 0 <= p.stat().st_size <= limit:
        raise ValueError("type_or_size")
    return p


def _counts(values: list[str]) -> dict[str, int]:
    return {state: values.count(state) for state in _STATES}


def windows_summary(path: Optional[Path]) -> JournalSummary:
    kind = "windows_v2"
    if path is None:
        return JournalSummary(kind, "not_configured")
    try:
        p = _read_path(path, MAX_JSON_BYTES)
        if p is None:
            return JournalSummary(kind, "missing")
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not_dict")
        if data.get("version") == 1:
            rows = data.get("attempts")
            if not isinstance(rows, list) or any(
                    not isinstance(x, str) or not _HEX64.fullmatch(x)
                    for x in rows):
                raise ValueError("legacy_corrupt")
            return JournalSummary(kind, "legacy_unbound",
                                  legacy_unbound=len(set(rows)),
                                  scope="unbound")
        if data.get("version") != 2:
            raise ValueError("version")
        raw = data.get("attempts")
        legacy = data.get("legacy_attempts", [])
        if not isinstance(raw, dict) or not isinstance(legacy, list):
            raise ValueError("schema")
        # Validate the salt without ever displaying it.
        salt = base64.urlsafe_b64decode(data["salt"].encode("ascii"))
        if len(salt) != 32 or any(
                not isinstance(x, str) or not _HEX64.fullmatch(x)
                for x in (*raw.keys(), *legacy)):
            raise ValueError("fingerprint")
        if any(v not in _STATES for v in raw.values()):
            raise ValueError("state")
        binding = data.get("scope_binding")
        if binding is not None and (
                not isinstance(binding, str) or not _HEX64.fullmatch(binding)):
            raise ValueError("binding")
        counts = _counts(list(raw.values()))
        unbound = len(set(legacy))
        condition = "ok" if binding or not (raw or legacy) else "unbound_history"
        if unbound:
            condition = "legacy_unbound"
        return JournalSummary(kind, condition, **counts,
                              legacy_unbound=unbound,
                              scope="bound" if binding else "unbound")
    except (OSError, ValueError, TypeError, KeyError, UnicodeError,
            json.JSONDecodeError, base64.binascii.Error):
        return JournalSummary(kind, "invalid", invalid_entries=1)


def live_jsonl_summary(path: Optional[Path]) -> JournalSummary:
    kind = "kingo_live_jsonl"
    if path is None:
        return JournalSummary(kind, "not_configured")
    try:
        p = _read_path(path, MAX_JSON_BYTES)
        if p is None:
            return JournalSummary(kind, "missing")
        latest: dict[str, str] = {}
        bad = 0
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > 10000:
            raise ValueError("too_many_lines")
        for line in lines:
            try:
                row = json.loads(line)
                if (not isinstance(row, dict)
                        or not isinstance(row.get("fp"), str)
                        or not _HEX24.fullmatch(row["fp"])
                        or row.get("state") not in _STATES):
                    raise ValueError("invalid_row")
                latest[row["fp"]] = row["state"]
            except (ValueError, TypeError):
                bad += 1
        key_file = p.with_name(p.name + ".key")
        scope_file = p.with_name(p.name + ".scope")
        # For a nonempty journal, missing or mismatched binding is a manual
        # review condition. This scanner does not create sidecar files.
        key = _read_path(key_file, 64)
        scope = _read_path(scope_file, 128)
        valid_key = key is not None and len(key.read_bytes()) == 32
        valid_scope = False
        if scope is not None:
            text = scope.read_text(encoding="ascii").strip()
            valid_scope = bool(_HEX64.fullmatch(text))
        counts = _counts(list(latest.values()))
        condition = ("invalid" if bad
                     else "unbound_history" if latest and not (valid_key and valid_scope)
                     else "ok")
        return JournalSummary(kind, condition, **counts,
                              invalid_entries=bad,
                              scope="bound" if valid_scope and valid_key else "unbound")
    except (OSError, ValueError, TypeError, UnicodeError, KeyError,
            json.JSONDecodeError):
        return JournalSummary(kind, "invalid", invalid_entries=1)


def shared_sqlite_summary(path: Optional[Path]) -> JournalSummary:
    kind = "shared_sqlite"
    if path is None:
        return JournalSummary(kind, "not_configured")
    try:
        p = _read_path(path, MAX_SQLITE_BYTES)
        if p is None:
            return JournalSummary(kind, "missing")
        # SQLite URI with mode=ro guarantees this inspection cannot create
        # the database. No writable URI, schema migration or integrity fix.
        with sqlite3.connect(p.resolve().as_uri() + "?mode=ro",
                             uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            meta = db.execute("SELECT value FROM metadata WHERE key='salt'").fetchone()
            if (meta is None or not isinstance(meta[0], str)
                    or not _HEX64.fullmatch(meta[0])):
                raise ValueError("salt")
            rows = db.execute(
                "SELECT fingerprint, state FROM attempts LIMIT 10001").fetchall()
            if len(rows) > 10000 or any(
                    not isinstance(fp, str) or not _HEX64.fullmatch(fp)
                    or state not in _STATES for fp, state in rows):
                raise ValueError("row")
            counts = _counts([state for _, state in rows])
        return JournalSummary(kind, "ok", **counts,
                              scope="per_intent_hmac")
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return JournalSummary(kind, "invalid", invalid_entries=1)


def audit_history(*, windows_path: Optional[Path] = None,
                  live_jsonl_path: Optional[Path] = None,
                  shared_sqlite_path: Optional[Path] = None
                  ) -> tuple[JournalSummary, ...]:
    return (
        windows_summary(windows_path),
        live_jsonl_summary(live_jsonl_path),
        shared_sqlite_summary(shared_sqlite_path),
    )


def format_history(summary: tuple[JournalSummary, ...]) -> str:
    """Short GUI-only aggregate report, no account or course identifiers."""
    names = {
        "windows_v2": "Windows 本机 V1/V2",
        "kingo_live_jsonl": "Kingo Live JSONL",
        "shared_sqlite": "共享 SQLite",
    }
    conditions = {
        "ok": "格式可读",
        "missing": "未找到（未创建）",
        "not_configured": "未选择",
        "invalid": "损坏或格式不可信（未修改）",
        "legacy_unbound": "旧记录归属不明（需人工核验）",
        "unbound_history": "缺少已核验的账号绑定（需人工核验）",
    }
    lines = ["历史状态体检（完全只读，未执行官方核验）"]
    for row in summary:
        lines.append(
            f"{names[row.kind]}：{conditions[row.condition]}；"
            f"待核验 {row.pending}、本地确认记录 {row.confirmed}、"
            f"本地拒绝记录 {row.rejected}、"
            f"无账号归属旧记录 {row.legacy_unbound}、"
            f"异常条目 {row.invalid_entries}")
    lines.append(
        "各库使用不同 HMAC 指纹，统计数字不可直接跨库相加或匹配。"
        "本地确认不是当前官方已选证明；任何待核验/损坏记录都不应触发重发。")
    return "\n".join(lines)
