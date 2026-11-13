"""Shared pure HAR parsing and redaction primitives.

The Windows read-only draft generator and research CLI retain independent
candidate-scoring and output schemas. This file centralizes ONLY equivalent
offline parsing helpers to avoid security drift. No network or credentials.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.parse
from typing import Any, Dict, List, Tuple


SENSITIVE_NAMES = {
    "cookie", "set-cookie", "authorization", "proxy-authorization",
    "token", "access_token", "refresh_token", "jsessionid", "session",
    "sessionid", "sid", "phpsessid", "asp.net_sessionid", "password",
    "passwd", "pwd", "secret", "signature", "sign", "nonce", "ticket",
    "csrf", "csrf_token", "xsrf", "x-xsrf-token", "x-csrf-token",
    "x-api-key", "apikey", "api_key", "auth", "credential", "skey",
    "openid", "unionid", "studentid", "xh", "sno", "idcard", "phone",
}

SENSITIVE_HINTS = ("token", "cookie", "session", "secret", "password", "passwd",
                   "sign", "ticket", "auth", "credential", "key", "pwd", "sid")


def is_sensitive(name: str) -> bool:
    n = name.strip().lower().replace("-", "_")
    if n in {x.replace("-", "_") for x in SENSITIVE_NAMES}:
        return True
    return any(h in n for h in SENSITIVE_HINTS) and n not in ("keys", "keyword")


def redact(name: str, value: Any, strict: bool = False) -> str:
    s = "" if value is None else str(value)
    if is_sensitive(name) or strict:
        return f"<REDACTED:len={len(s)}>"
    return scrub(s[:120])


def scrub(text: str) -> str:
    """Remove phone numbers and ID-card numbers from any emitted text."""
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "<PHONE>", text)
    text = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "<IDCARD>", text)
    return text


# ---------------------------------------------------------------------------
# HAR parsing
# ---------------------------------------------------------------------------

STATIC_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
              ".woff", ".woff2", ".ttf", ".map", ".webp", ".mp4", ".wasm")


def decode_content(entry: dict) -> Tuple[str, str]:
    resp = entry.get("response", {}) or {}
    content = resp.get("content", {}) or {}
    text = content.get("text", "") or ""
    mime = str(content.get("mimeType", "") or "")
    if content.get("encoding") == "base64" and text:
        try:
            text = base64.b64decode(text).decode("utf-8", errors="replace")
        except Exception:
            text = ""
    # Many Kingosoft / mobile endpoints return JSON with literal \uXXXX escapes.
    # Normalize them so Chinese business messages can be matched and previewed.
    text = re.sub(r"\\u([0-9a-fA-F]{4})",
                  lambda m: chr(int(m.group(1), 16)), text)
    return text, mime


def entry_url(entry: dict) -> Tuple[str, str, str, List[Tuple[str, str]]]:
    url = str((entry.get("request", {}) or {}).get("url", "") or "")
    u = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    return u.scheme, u.netloc, u.path or "/", q


def post_params(entry: dict) -> Tuple[Dict[str, str], str]:
    req = entry.get("request", {}) or {}
    pd = req.get("postData", {}) or {}
    params: Dict[str, str] = {}
    for p in pd.get("params", []) or []:
        if isinstance(p, dict) and "name" in p:
            params[str(p["name"])] = str(p.get("value", ""))
    text = str(pd.get("text", "") or "")
    if text and not params:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                params = {str(k): str(v) for k, v in obj.items()}
        except (ValueError, TypeError):
            params = dict(urllib.parse.parse_qsl(text, keep_blank_values=True))
    mime = str(pd.get("mimeType", "") or "")
    return params, mime
