#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hs_kcbcx.py —— 华水青果「开课计划查询」抓取器（余量快照 / 轮询 / 导出）

为什么用它（而不是去爬预选页面）：
  预选页面（wsxk.yx.html / DataTable.jsp?tableId=2548）只在选课窗口内出数据，
  窗口一关 total=0，而且余量列藏在动态行里，正则一碰就抓错行。
  开课计划查询（taglib/DataTable.jsp?tableId=5327042）是**常年可读**的静态快照，
  一次返回全年所有教学班，字段含：限选人数 / 已选+免听 / 可选人数 / 上课时间地点 / 任课教师。
  → 窗口没开也能知道「有哪些课、哪些快满了」，这是提前排阵容的唯一数据源。

接口事实（2026-11-13 实测，jx.ncu /hsjw）：
  POST  /hsjw/taglib/DataTable.jsp?tableId=5327042&clientWidth=1210
  Body  GBK 编码的 x-www-form-urlencoded（中文值必须 GBK，发 UTF-8 直接 total=0）
        initQry=0&xktype=2&xn=2026&xq=0&nj=2026&pycc=05&dwh=05&zydm=0502
        &kclb1=&kclb2=&isbyk=&items=&xnxq=2026,0
        &btnFilter=类别过滤&btnSubmit=提交
        &sel_pycc=05&sel_nj=2026&sel_yxb=05&sel_zydm=0502
        &kcmk=&sel_schoolarea=&sel_kclb1=&sel_kclb2=&sel_kc=&sel_rkjs=&sel_cddwdm=
        &menucode_current=S2020103
  响应  GBK HTML，行 <tr class='O'|'E' id='trN'>，单元格 id='trN_<字段>'
  分页  无。total 由 parent.showTotalRecord('5327042','68') 给出，68 行 = 34 O + 34 E

字段：kc(含课程号) xqmc xf zongxs kclb cddw curent_skbjdm curent_skbzdmmc skbjmc
      xkrssx(限选) xkrs(已选/免听) qdrs(可选) qsz skfs rkjs sksj skdd skbjdm xqdm

用法：
  # 看本学期全部教学班
  python hs_kcbcx.py --cookie "JSESSIONID=xxx" --xn 2026 --xq 1

  # 只看还有余量的、按可选人数升序（越靠前越危险）
  python hs_kcbcx.py --cookie "..." --xn 2026 --xq 1 --only-open --sort qdrs

  # 导出 CSV（Excel 直接打开不乱码）
  python hs_kcbcx.py --cookie "..." --xn 2026 --xq 1 --csv 开课计划.csv

  # 轮询盯余量，只在变化时打印（抢课前一天挂着跑）
  python hs_kcbcx.py --cookie "..." --xn 2026 --xq 1 --watch 5

Cookie 也可以放文件（一行 JSESSIONID=...）或环境变量 HS_COOKIE。
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://jwmis.ncwu.edu.cn/hsjw/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0")
MENU = "S2020103"
TABLE_ID = "5327042"

_C = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if _C else s


def _wlen(s):
    n = 0
    for ch in re.sub(r"\033\[[0-9;]*m", "", s):
        n += 2 if ord(ch) > 0x2E80 else 1
    return n


def _pad(s, w):
    s = str(s)
    return s + " " * max(0, w - _wlen(s))


# ─────────────────────────── HTTP ───────────────────────────

def _gbk_quote(v):
    """青果要求中文参数用 GBK 编码后再 urlencode，UTF-8 会被当乱码 → total=0"""
    return urllib.parse.quote(str(v), encoding="gbk", errors="replace")


def fetch_plan(cookie, xn, xq, nj, zydm, yxb, pycc="05",
               kclb1="", kclb2="", kc="", rkjs="", timeout=20):
    pairs = [
        ("initQry", "0"), ("xktype", "2"),
        ("xn", xn), ("xq", xq), ("nj", nj), ("pycc", pycc),
        ("dwh", yxb), ("zydm", zydm),
        ("kclb1", ""), ("kclb2", ""), ("isbyk", ""), ("items", ""),
        ("xnxq", f"{xn},{xq}"),
        ("btnFilter", "类别过滤"), ("btnSubmit", "提交"),
        ("sel_pycc", pycc), ("sel_nj", nj), ("sel_yxb", yxb), ("sel_zydm", zydm),
        ("kcmk", ""), ("sel_schoolarea", ""),
        ("sel_kclb1", kclb1), ("sel_kclb2", kclb2),
        ("sel_kc", kc), ("sel_rkjs", rkjs), ("sel_cddwdm", ""),
        ("menucode_current", MENU),
    ]
    body = "&".join(f"{k}={_gbk_quote(v)}" for k, v in pairs).encode("gbk", "replace")
    url = f"{BASE}taglib/DataTable.jsp?tableId={TABLE_ID}&clientWidth=1210"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Cookie": cookie,
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"{BASE}student/wsxk.kcbcx10319.html?menucode={MENU}",
        "Origin": "https://jwmis.ncwu.edu.cn",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    html = raw.decode("gb18030", "replace")
    if "凭证已失效" in html or "重新登录" in html:
        raise RuntimeError("登录态失效：换 JSESSIONID")
    return html


# ─────────────────────────── 解析 ───────────────────────────

_TR = re.compile(r"<tr\s+class='[OE]'\s+id='tr(\d+)'[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*id='tr\d+_([A-Za-z0-9_]+)'[^>]*>(.*?)</td>", re.S)
_TOTAL = re.compile(r"showTotalRecord\('" + TABLE_ID + r"','(\d+)'\)")


def _txt(h):
    h = re.sub(r"(?is)<br\s*/?>", " ", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = h.replace("&nbsp;", " ").replace("&ensp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", h).strip()


def parse_plan(html):
    """→ (rows, total)  rows 按表格顺序

    坑：青果一个教学班可能占多行（多个上课时间段），
        第二行起 kc 为空、只有 sksj/skdd/rkjs，且没有限选/已选/可选。
        直接逐行输出会得到一半是「?」的废行 —— 这里把续行合并进主行。
    """
    m = _TOTAL.search(html)
    total = int(m.group(1)) if m else -1
    rows = []
    for _, seg in _TR.findall(html):
        d = {}
        for k, v in _TD.findall(seg):
            d[k] = _txt(v)
        if not d:
            continue

        # kc 形如 "[0400111101]工程制图"
        mm = re.match(r"\[([0-9A-Za-z]+)\]\s*(.*)", d.get("kc", ""))
        if not mm:
            # 续行：同教学班的另一个时间段
            if rows:
                t, p = d.get("sksj", ""), d.get("skdd", "")
                extra = f"{t} {p}".strip()
                if extra and extra not in rows[-1].get("sksj_extra", []):
                    rows[-1].setdefault("sksj_extra", []).append(extra)
                tea = d.get("rkjs", "")
                if tea and tea not in rows[-1].get("rkjs_all", []):
                    rows[-1].setdefault("rkjs_all", []).append(tea)
            continue

        d["kcdm"] = mm.group(1)
        d["kcmc"] = mm.group(2).strip()

        # xkrs 形如 "123/0"（已选 / 免听）
        one, _, two = d.get("xkrs", "").partition("/")
        d["taken"] = _int(one)
        d["mian"] = _int(two)

        d["cap"] = _int(d.get("xkrssx", ""))      # 限选人数
        d["left"] = _int(d.get("qdrs", ""))       # 可选人数（服务端算好的）
        if d["left"] is None and d["cap"] is not None and d["taken"] is not None:
            d["left"] = d["cap"] - d["taken"]
        d["skbjdm"] = d.get("curent_skbjdm") or d.get("skbjdm", "")
        rows.append(d)
    return rows, total


def _int(s):
    s = str(s).strip()
    m = re.search(r"-?\d+", s)
    return int(m.group(0)) if m else None


# ─────────────────────────── 输出 ───────────────────────────

COLS = [("kcdm", "课程号", 11), ("kcmc", "课程名称", 24), ("xf", "学分", 4),
        ("kclb", "课程类别", 18), ("skbjdm", "教学班", 6),
        ("cap", "限选", 4), ("taken", "已选", 4), ("left", "可选", 4),
        ("rkjs", "任课教师", 12), ("sksj", "上课时间", 26), ("skdd", "上课地点", 16)]


def print_table(rows):
    if not rows:
        print(c("33", "  （0 行 —— 该学期开课计划未发布，或筛选条件没匹配）"))
        return
    head = "  ".join(_pad(n, w) for _, n, w in COLS)
    print(c("90", head))
    print(c("90", "─" * _wlen(head)))
    for d in rows:
        cells = []
        for k, _, w in COLS:
            v = d.get(k)
            v = "" if v is None else str(v)
            if k == "sksj" and d.get("sksj_extra"):
                v = f"{v} +{len(d['sksj_extra'])}段"
            if k == "rkjs" and len(d.get("rkjs_all", [])) > 1:
                v = "/".join(d["rkjs_all"])[:w]
            if k == "left":
                n = d.get("left")
                if n is None:
                    v = "?"
                elif n <= 0:
                    v = c("31", _pad("满", w)).ljust(w)
                elif n <= 3:
                    v = c("33", _pad(str(n), w))
                else:
                    v = c("32", _pad(str(n), w))
                cells.append(v)
                continue
            cells.append(_pad(v, w))
        print("  ".join(cells))


def to_csv(rows, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["课程号", "课程名称", "学分", "总学时", "课程类别", "承担单位",
                    "教学班号", "限选人数", "已选", "免听", "可选人数",
                    "任课教师", "上课时间", "上课地点", "校区"])
        for d in rows:
            w.writerow([d.get("kcdm"), d.get("kcmc"), d.get("xf"), d.get("zongxs"),
                        d.get("kclb"), d.get("cddw"), d.get("skbjdm"),
                        d.get("cap"), d.get("taken"), d.get("mian"), d.get("left"),
                        d.get("rkjs"), d.get("sksj"), d.get("skdd"), d.get("xqmc")])
    print(c("32", f"已导出 {len(rows)} 行 → {path}"))


def key_of(d):
    return (d.get("kcdm", ""), d.get("skbjdm", ""), d.get("rkjs", ""))


def snapshot(rows):
    return {key_of(d): (d.get("taken"), d.get("left")) for d in rows}


# ─────────────────────────── 主流程 ───────────────────────────

def get_cookie(args):
    if args.cookie:
        return args.cookie
    if args.cookie_file and os.path.exists(args.cookie_file):
        s = open(args.cookie_file, encoding="utf-8").read().strip()
        # 允许放整段 Cookie 头，或一行 JSESSIONID=...
        m = re.search(r"JSESSIONID=[0-9A-Fa-f]+", s)
        if m:
            return m.group(0)
    for env in ("KINGO_COOKIE", "HS_COOKIE"):
        if os.environ.get(env):
            return os.environ[env]
    sys.exit("缺 Cookie：--cookie \"JSESSIONID=...\" 或 --cookie-file 或 export KINGO_COOKIE=")


def main():
    ap = argparse.ArgumentParser(description="华水青果开课计划查询 + 余量盯盘")
    ap.add_argument("--cookie")
    ap.add_argument("--cookie-file", default="hs_cookie.txt")
    ap.add_argument("--xn", default="2026", help="学年，如 2026")
    ap.add_argument("--xq", default="0", help="学期：0=第一学期 1=第二学期（青果 0 基）")
    ap.add_argument("--nj", default="2026", help="年级")
    ap.add_argument("--zydm", default="0502", help="专业代码")
    ap.add_argument("--yxb", default="05", help="院系部代码")
    ap.add_argument("--pycc", default="05", help="培养层次代码")
    ap.add_argument("--kclb1", default="", help="课程类别一级过滤，如 01=必修")
    ap.add_argument("--kc", default="", help="课程名称模糊")
    ap.add_argument("--rkjs", default="", help="任课教师模糊")
    ap.add_argument("--only-open", action="store_true", help="只显示还有余量的")
    ap.add_argument("--sort", default="", choices=["", "qdrs", "taken", "kcdm"],
                    help="排序：qdrs=可选人数升序（越紧越前）")
    ap.add_argument("--csv")
    ap.add_argument("--json")
    ap.add_argument("--watch", type=float, default=0, metavar="秒",
                    help="轮询间隔，只在余量变化时打印")
    ap.add_argument("--watch-max", type=int, default=0, help="最多轮询次数，0=不限")
    ap.add_argument("--timeout", type=float, default=20)
    args = ap.parse_args()

    cookie = get_cookie(args)

    def query():
        html = fetch_plan(cookie, args.xn, args.xq, args.nj, args.zydm, args.yxb,
                          args.pycc, args.kclb1, "", args.kc, args.rkjs,
                          timeout=args.timeout)
        return parse_plan(html)

    rows, total = query()
    print(c("36", f"[开课计划] {args.xn} 学年 第{'一' if args.xq == '0' else '二'}学期 "
                  f"| 年级 {args.nj} 专业 {args.zydm} 院系 {args.yxb} | "
                  f"服务器 total={total} 解析行数={len(rows)}"))
    if args.kclb1:
        print(c("90", f"  类别过滤 kclb1={args.kclb1}"))
    print()

    view = rows
    if args.only_open:
        view = [d for d in view if (d.get("left") or 0) > 0]
    if args.sort == "qdrs":
        view = sorted(view, key=lambda d: (d.get("left") is None, d.get("left") or 0))
    elif args.sort == "taken":
        view = sorted(view, key=lambda d: -(d.get("taken") or 0))
    elif args.sort == "kcdm":
        view = sorted(view, key=lambda d: d.get("kcdm", ""))

    print_table(view)
    nopen = sum(1 for d in rows if (d.get("left") or 0) > 0)
    nfull = sum(1 for d in rows if d.get("left") is not None and d["left"] <= 0)
    print()
    print(c("90", f"  合计 {len(rows)} 个教学班：有余量 {nopen} / 已满 {nfull} / "
                  f"未知 {len(rows) - nopen - nfull}"))

    if args.csv:
        to_csv(view, args.csv)
    if args.json:
        json.dump(view, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(c("32", f"已导出 JSON → {args.json}"))

    if not args.watch:
        return

    # ── 轮询盯盘 ──
    print()
    print(c("36", f"[盯盘] 每 {args.watch}s 一次，只在余量变化时打印。Ctrl-C 退出。"))
    prev = snapshot(rows)
    n = 0
    try:
        while True:
            time.sleep(args.watch)
            n += 1
            if args.watch_max and n > args.watch_max:
                break
            try:
                rows, _ = query()
            except Exception as e:
                print(c("31", f"  [{time.strftime('%H:%M:%S')}] 查询失败: {e}"))
                continue
            cur = snapshot(rows)
            changed = 0
            for k in cur:
                if k not in prev:
                    print(c("32", f"  [{time.strftime('%H:%M:%S')}] + 新增教学班 "
                                  f"{k[0]} 班{k[1]} {k[2]} 可选={cur[k][1]}"))
                    changed += 1
                    continue
                if cur[k] != prev[k]:
                    old_t, old_l = prev[k]
                    new_t, new_l = cur[k]
                    d = (new_l or 0) - (old_l or 0)
                    tag = c("32", f"↑+{d}") if d > 0 else (c("31", f"↓{d}") if d < 0 else "·")
                    print(f"  [{time.strftime('%H:%M:%S')}] {tag} {k[0]} 班{k[1]} "
                          f"{k[2]}  已选 {old_t}→{new_t}  可选 {old_l}→{new_l}")
                    changed += 1
            for k in prev:
                if k not in cur:
                    print(c("33", f"  [{time.strftime('%H:%M:%S')}] - 教学班消失 {k[0]} 班{k[1]}"))
                    changed += 1
            prev = cur
            if not changed:
                sys.stdout.write(c("90", "."))
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n已退出盯盘。")


if __name__ == "__main__":
    main()
