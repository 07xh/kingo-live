# -*- coding: utf-8 -*-
"""选课列表解析：从青果返回的 HTML/JSON 里抽出 (课程名, 教师, xkid, 余量)。

青果各校列表渲染方式不同，这里按"行"扫描，多套模式并行匹配：
  1. JS 调用：addXk('123') / selectXk("123") / xk(123)
  2. 隐藏域：<input ... name="xkid" value="123">
  3. 链接参数：href="...?xkid=123" / onclick 里 xkid=123
  4. JSON：{"xkid":"123", ...}
"""
from __future__ import annotations

import json
import re

XKID_PATTERNS = [
    r"addXk\s*\(\s*['\"]([0-9A-Za-z_\-]{4,})['\"]",
    r"selectXk\s*\(\s*['\"]([0-9A-Za-z_\-]{4,})['\"]",
    r"xk\s*\(\s*['\"]([0-9A-Za-z_\-]{4,})['\"]",
    r"name\s*=\s*[\"']xkid[\"'][^>]*value\s*=\s*[\"']([0-9A-Za-z_\-]{4,})[\"']",
    r"value\s*=\s*[\"']([0-9A-Za-z_\-]{4,})[\"'][^>]*name\s*=\s*[\"']xkid[\"']",
    r"id\s*=\s*[\"']xkid[_\-]?([0-9A-Za-z_\-]{4,})[\"']",
    r"xkid\s*=\s*([0-9A-Za-z_\-]{4,})",
]

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
SEAT = re.compile(r"(?:余量|剩余|余额)\D{0,4}(\d+)\s*/\s*(\d+)"
                  r"|(\d+)\s*/\s*(\d+)\s*(?:人|已选)")


def strip_tags(html: str) -> str:
    return WS.sub(" ", TAG.sub(" ", html)).strip()


def _first_xkid(chunk: str) -> str:
    for pat in XKID_PATTERNS:
        m = re.search(pat, chunk)
        if m:
            return m.group(1)
    return ""


def parse_html(html: str) -> list[dict]:
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
    out, seen = [], set()
    for row in rows:
        xkid = _first_xkid(row)
        if not xkid or xkid in seen:
            continue
        seen.add(xkid)
        text = strip_tags(row)
        if len(text) < 4:
            continue
        seats = None
        m = SEAT.search(text)
        if m:
            a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            if a and b:
                try:
                    seats = {"left": int(a), "total": int(b)}
                except ValueError:
                    seats = None
        out.append({"xkid": xkid, "text": text, "seats": seats})
    return out


def parse_json(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except Exception:
        return []
    items = None
    for key in ("rows", "items", "list", "data", "result"):
        node = data.get(key) if isinstance(data, dict) else None
        if isinstance(node, list):
            items = node
            break
        if isinstance(node, dict):
            for k2 in ("rows", "items", "list"):
                if isinstance(node.get(k2), list):
                    items = node[k2]
                    break
        if items:
            break
    if not items:
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        xkid = ""
        for k in ("xkid", "XKID", "jxbid", "jxb_id", "id"):
            if it.get(k):
                xkid = str(it[k])
                break
        if not xkid:
            continue
        parts = [str(v) for k, v in it.items() if isinstance(v, (str, int, float))]
        out.append({"xkid": xkid, "text": " ".join(parts), "seats": None})
    return out


def parse(text: str) -> list[dict]:
    rows = parse_json(text)
    return rows or parse_html(text)


def match(rows: list[dict], course: dict) -> dict | None:
    """按课程名 + 教师匹配一行；name 为空则取第一条。"""
    name = (course.get("name") or "").strip()
    teacher = (course.get("teacher") or "").strip()
    kch = (course.get("kch_id") or "").strip()
    if not name and not kch:
        return rows[0] if rows else None
    for r in rows:
        t = r["text"]
        if name and name not in t:
            continue
        if teacher and teacher not in t:
            continue
        if kch and kch not in t and r["xkid"] != kch:
            continue
        return r
    # 名称被截断/空格差异时，退一步做去空格包含
    flat = lambda s: WS.sub("", s)
    for r in rows:
        t = flat(r["text"])
        if name and flat(name) in t and (not teacher or flat(teacher) in t):
            return r
    return None
