# -*- coding: utf-8 -*-
"""青果选课表格解析与提交载荷组装。

数据来源：`POST /hsjw/taglib/DataTable.jsp?tableId=NNNN`（GBK 编码 HTML）。
每行 `<tr id='trN'>`，每个单元格 `<td id='trN_<字段>' name='<字段>'>`。

关键字段：
  kc         课程名，形如 `[1500120001]体育1`
  kcdm       课程代码 `1500120001`
  skbjdm     上课班级代码 `1500120001-101`   ← 选课/退选的唯一标识
  skbh       上课班号 `101`
  skbjmc     上课班级名称 `气排球`
  rkjs       任课教师
  xk_status  选课状态：选中 / 未选 / 已满
  is_yxtj    允许调剂 1/0
  lb / kcsx  课程类别 / 课程属性
  xf / zxs   学分 / 总学时

提交载荷（`saveDate()` 组装）：
  items = kcdm,rkjs,tj,true|kcdm,rkjs,tj,true|...
  其中 tj = 允许调剂 '1'/'0'
"""
from __future__ import annotations

import re
from html import unescape
from typing import Optional

RE_TR = re.compile(r"<tr\b([^>]*)>(.*?)</tr>", re.S | re.I)
RE_TD = re.compile(r"<td\b([^>]*)>(.*?)</td>", re.S | re.I)
RE_ATTR = re.compile(r"([\w:-]+)\s*=\s*['\"]([^'\"]*)['\"]")
RE_TAG = re.compile(r"<[^>]+>")
RE_TOTAL = re.compile(r"showTotalRecord\(\s*'?(\d+)'?\s*,\s*'?(\d+)'?\s*\)")


def _text(s: str) -> str:
    """去标签、反转义、压缩空白。"""
    s = RE_TAG.sub(" ", s or "")
    s = unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def total_records(html: str) -> Optional[int]:
    """从 `parent.showTotalRecord('2548','12')` 里取总记录数。"""
    m = RE_TOTAL.search(html or "")
    return int(m.group(2)) if m else None


def parse_table(html: str) -> tuple[list[str], list[dict]]:
    """解析 DataTable 页面，返回 (表头字段序列, 数据行列表)。

    表头行带 `id='head_<字段>'`；数据行带 `id='trN'`，单元格 `id='trN_<字段>'`。
    """
    html = html or ""
    headers: list[str] = []
    rows: list[dict] = []

    for attrs, inner in RE_TR.findall(html):
        a = dict(RE_ATTR.findall(attrs or ""))
        tr_id = a.get("id", "")
        cells = RE_TD.findall(inner)
        if not cells:
            continue

        # 表头行
        if any("head_" in dict(RE_ATTR.findall(a2 or "")).get("id", "") for a2, _ in cells):
            for a2, _ in cells:
                hid = dict(RE_ATTR.findall(a2 or "")).get("id", "")
                if hid.startswith("head_"):
                    headers.append(hid[5:])
            continue

        # 数据行：只认 trN 这种 id
        if not re.fullmatch(r"tr\d+", tr_id or ""):
            continue
        row: dict = {"_tr": tr_id}
        for a2, inner2 in cells:
            ca = dict(RE_ATTR.findall(a2 or ""))
            name = ca.get("name", "")
            if not name:
                cid = ca.get("id", "")
                name = cid[len(tr_id) + 1:] if cid.startswith(tr_id + "_") else ""
            if not name:
                continue
            row[name] = _text(inner2)
            # 操作列里的退选/查看链接带额外属性
            if name == "operation":
                for href in re.findall(r"<a\b([^>]*)>", inner2, re.I):
                    row.setdefault("_links", []).append(dict(RE_ATTR.findall(href)))
        rows.append(row)

    return headers, rows


def course_code(row: dict) -> str:
    """课程代码。优先隐藏字段 kcdm，回退到课程名里的方括号前缀。"""
    kcdm = (row.get("kcdm") or "").strip()
    if kcdm:
        return kcdm
    m = re.match(r"\[([^\]]+)\]", row.get("kc", ""))
    return m.group(1) if m else ""


def class_code(row: dict) -> str:
    """Authoritative teaching-class code, or empty when not provided."""
    skbjdm = (row.get("skbjdm") or "").strip()
    if skbjdm:
        return skbjdm
    # A course code or a display class number is not an authoritative
    # teaching-class identity. Never fabricate skbjdm for enrollment.
    return ""


def course_name(row: dict) -> str:
    """纯课程名，剥掉 `[1500120001]` 前缀。"""
    return re.sub(r"^\[[^\]]*\]\s*", "", row.get("kc", "")).strip()


def is_selected(row: dict) -> bool:
    """该教学班是否已选中。"""
    st = row.get("xk_status", "")
    return ("选中" in st) or ("已选" in st)


def build_items(rows: list[dict], tj: str = "0") -> str:
    """组装 `items` 字段：kcdm,rkjs,tj,true|...

    与页面 saveDate() 完全一致：任课教师取单元格文本（不是教师 ID）。
    """
    parts = []
    for row in rows:
        kcdm = course_code(row)
        if not kcdm:
            continue
        rkjs = (row.get("rkjs") or "").strip()
        allow = "1" if (tj == "1" or row.get("is_yxtj") == "1") else "0"
        parts.append(f"{kcdm},{rkjs},{allow},true")
    return "|".join(parts)


def match(rows: list[dict], name: str = "", teacher: str = "",
          code: str = "") -> list[dict]:
    """按课程名 / 教师 / 课程代码筛行。空条件不参与。"""
    def norm(s: str) -> str:
        return re.sub(r"\s+", "", s or "")

    out = []
    for row in rows:
        if code and course_code(row) != code:
            continue
        if name and norm(name) not in norm(course_name(row)):
            continue
        if teacher and norm(teacher) not in norm(row.get("rkjs", "")):
            continue
        out.append(row)
    return out
