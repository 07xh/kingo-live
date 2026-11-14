#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hs_xk.py —— 华水青果选课门禁 / 可选课程 / 提交选课（/hsjw 学生端）

接口全是从真包还原的（2026-11-13 HAR，Reqable 3.3.11），不是猜的：

  1) 门禁探测
     POST /hsjw/jw/common/getWsxkTimeRange.action?xktype=<N>
     响应 JSON：status=200 才在窗口内；result 里 qssj/jssj/isValidTimerange/lcid/sfbd/djs/kxkc
     xktype 实测语义：
        1 = 院系选修 / 专业选修预选（第二学期，本次补选 lcmc=2026-2027-2专业选修课补选）
        2 = 体育（第一学期，2026-09-18 ~ 09-19，已过期）
        3 = 补选（未定义）
        4 = 重修（未定义/过期）
        5 = 退选（未定义）
        9 = 外年级/专业 选课申请（未定义）
       88 = 外年级/专业 选课（未定义）
     ★ 判门禁只认这个 status，不要拿首页通知或 issjqd 猜。

  2) 可选课程清单
     POST /hsjw/taglib/DataTable.jsp?tableId=2548
     需要把 initQry=0 + 门禁返回的一整套隐藏域原样回填（xh/xn/xq/nj/zydm/yxbdm/lcid/sfbd/djs/kxkc）
     否则 total=0。响应 GBK HTML，行 <tr class='O'|'E' id='trN'>。

  3) 提交选课（**明文表单，无加密**）
     POST /hsjw/STU_PreElectCourseAction.do?hidOption=saveData
     Body = ActionForm 全部隐藏域 + items
     items 格式： "课程号,任课教师,null|0|1,是否勾选|"  逐条拼接
     即 saveDate() 里 items += kcdm + "," + js + "," + tj + "," + checked + "|"
     ★ 这条不走 DES；只有退选 cancelElectiveCourse.action 走 getEncParams（DES + getTempDeskey）。

用法：
  # 门禁
  python hs_xk.py gate --cookie "JSESSIONID=..." --xktype 1

  # 可选课程（会先探门禁，失败也照样查，只是可能 total=0）
  python hs_xk.py list --cookie "..." --xktype 1 --kcfw zxbnj --nj 2026 --zydm 0502

  # 预演：只打印将要发送的 POST body
  python hs_xk.py submit --cookie "..." --kcdm 0400111101 --skbjdm 002 --dry-run

  # 真提交
  python hs_xk.py submit --cookie "..." --kcdm 0400111101 --skbjdm 002

  课程范围 kcfw：zxbnj=主修(本年级/专业) zxggrx=主修(公共任选) fx=辅修 zxknj=主修(可跨年级/专业)
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import sqlite3
from pathlib import Path

# Share the same durable per-intent guard with the Qiangke CLI.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts.intent_bridge import reserve_shared_intent
from contracts.enrollment_evidence import pending_receipt, VerificationState

BASE = "https://jwmis.ncwu.edu.cn/hsjw/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0")
MENU = "S2020201"          # 预选（院系选修）
TABLE_PRE = "2548"         # 预选可选课程表

_C = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if _C else s


# ─────────────────────────── HTTP 基座 ───────────────────────────

def _req(cookie, url, data=None, referer=None, ajax=True, timeout=15):
    body = data.encode("gbk", "replace") if isinstance(data, str) else data
    h = {
        "Cookie": cookie,
        "User-Agent": UA,
        "Origin": "https://jwmis.ncwu.edu.cn",
        "Referer": referer or f"{BASE}student/wsxk.yx.html?menucode={MENU}&bqflag=1",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if data is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if ajax:
        h["X-Requested-With"] = "XMLHttpRequest"
    r = urllib.request.Request(url, data=body, method="POST" if data is not None else "GET",
                               headers=h)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.read()


def _q(s):
    return urllib.parse.quote(str(s), encoding="gbk", errors="replace")


# ─────────────────────────── 门禁 ───────────────────────────

GATE_MEAN = {1: "院系/专业选修预选", 2: "体育选课", 3: "补选", 4: "重修", 5: "退选",
             9: "外年级/专业 选课申请", 88: "外年级/专业 选课"}


def gate(cookie, xktype, timeout=15):
    raw = _req(cookie, f"{BASE}jw/common/getWsxkTimeRange.action?xktype={xktype}",
               data="", timeout=timeout)
    txt = raw.decode("utf-8", "replace")
    try:
        d = json.loads(txt)
    except Exception:
        return {"status": "?", "message": txt[:200], "result": {}}
    res = {}
    if isinstance(d.get("result"), str):
        try:
            res = json.loads(d["result"])
        except Exception:
            res = {}
    elif isinstance(d.get("result"), dict):
        res = d["result"]
    d["result"] = res
    if "zysx" in res:
        res["zysx_plain"] = re.sub(r"<[^>]+>", "", str(res["zysx"]))
    return d


def show_gate(d, xktype):
    st = str(d.get("status"))
    color = "32" if st == "200" else "31"
    print(c(color, f"xktype={xktype} ({GATE_MEAN.get(int(xktype), '未知')})  status={st}  {d.get('message')}"))
    r = d.get("result") or {}
    if not r:
        return
    for k in ("xnxqDesc", "xqName", "lcmc", "qssj", "jssj", "isValidTimerange",
              "lcid", "sfbd", "djs", "kxkc", "qyPjxz", "sfklbq", "yqxszj", "yqxszx"):
        if k in r:
            print(f"   {k:18} = {r[k]}")
    if r.get("zysx_plain"):
        print(c("90", f"   注意事项: {r['zysx_plain'][:160]}"))
    # 窗口倒计时
    try:
        import datetime
        js = datetime.datetime.strptime(r["jssj"], "%Y-%m-%d %H:%M")
        left = (js - datetime.datetime.now()).total_seconds()
        if left > 0:
            print(c("33", f"   → 窗口还剩 {int(left // 3600)}h{int(left % 3600 // 60)}m"))
        else:
            print(c("31", "   → 窗口已结束"))
    except Exception:
        pass


# ─────────────────────────── 可选课程 ───────────────────────────

_TR = re.compile(r"<tr\s+class='[OE]'\s+id='tr(\d+)'[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*id='tr\d+_([A-Za-z0-9_]+)'[^>]*>(.*?)</td>", re.S)
_INPUT = re.compile(r"<input[^>]*name='([^']*)'[^>]*value='([^']*)'", re.I)


def _txt(h):
    h = re.sub(r"(?is)<br\s*/?>", " ", h)
    h = re.sub(r"<[^>]+>", "", h)
    return re.sub(r"\s+", " ", h).strip()


def pre_form(cookie, xktype, kcfw, nj, zydm, yxb, xn, xq, timeout=15):
    """拉门禁 → 组装预选表格的隐藏域（必须整组回填，缺一个 total=0）"""
    g = gate(cookie, xktype, timeout)
    r = g.get("result") or {}
    form = {
        "initQry": "0", "xktype": xktype,
        "xh": r.get("xh", ""), "xn": r.get("xn", xn), "xq": r.get("xqM", xq),
        "nj": r.get("nj", nj), "zydm": r.get("zydm", zydm), "yxbdm": r.get("yxbdm", yxb),
        "items": "", "sfbd": r.get("sfbd", ""), "lcid": r.get("lcid", ""),
        "djs": r.get("djs", ""), "kxkc": r.get("kxkc", ""),
        "xfsx": r.get("xfsx", ""), "mssx": r.get("mssx", ""),
        "kcfw": kcfw, "sel_yxb": r.get("yxbdm", yxb),
        "njzy": f"{r.get('nj', nj)}|{r.get('zydm', zydm)}",
        "menucode_current": MENU,
    }
    return g, form


def query_pre(cookie, form, timeout=20):
    body = "&".join(f"{k}={_q(v)}" for k, v in form.items())
    html = _req(cookie, f"{BASE}taglib/DataTable.jsp?tableId={TABLE_PRE}",
                data=body, timeout=timeout).decode("gb18030", "replace")
    if "凭证已失效" in html:
        raise RuntimeError("登录态失效")
    m = re.search(r"showTotalRecord\('" + TABLE_PRE + r"','(\d+)'\)", html)
    total = int(m.group(1)) if m else -1
    rows = []
    for _, seg in _TR.findall(html):
        d = {k: _txt(v) for k, v in _TD.findall(seg)}
        if not d:
            continue
        mm = re.match(r"\[([0-9A-Za-z]+)\]\s*(.*)", d.get("kc", ""))
        d["kcdm"] = mm.group(1) if mm else ""
        d["kcmc"] = (mm.group(2) if mm else d.get("kc", "")).strip()
        one, _, two = d.get("xkrs", "").partition("/")
        d["taken"] = int(one) if one.strip().isdigit() else None
        d["mian"] = int(two) if two.strip().isdigit() else None
        d["cap"] = int(d["xkrssx"]) if d.get("xkrssx", "").isdigit() else None
        d["left"] = int(d["qdrs"]) if d.get("qdrs", "").isdigit() else None
        d["skbjdm"] = d.get("curent_skbjdm") or d.get("skbjdm", "")
        # 行内 input（saveDate 里读取的 rkjs 就来自这里）
        d["_inputs"] = _INPUT.findall(seg)
        rows.append(d)
    return rows, total, html


COLS = [("kcdm", "课程号", 11), ("kcmc", "课程名称", 24), ("xf", "学分", 4),
        ("kclb", "类别", 16), ("skbjdm", "班号", 5),
        ("cap", "限选", 4), ("taken", "已选", 4), ("left", "可选", 4),
        ("rkjs", "教师", 10), ("sksj", "上课时间", 20)]


def _wlen(s):
    n = 0
    for ch in re.sub(r"\033\[[0-9;]*m", "", s):
        n += 2 if ord(ch) > 0x2E80 else 1
    return n


def _pad(s, w):
    s = str(s)
    return s + " " * max(0, w - _wlen(s))


def print_rows(rows):
    if not rows:
        print(c("33", "  0 行 —— 该轮次没有可选课程（课表未发布 / 不满足条件 / 已选满）"))
        return
    h = "  ".join(_pad(n, w) for _, n, w in COLS)
    print(c("90", h))
    print(c("90", "─" * _wlen(h)))
    for d in rows:
        cells = []
        for k, _, w in COLS:
            v = d.get(k)
            if k == "left":
                n = d.get("left")
                if n is None:
                    cells.append(_pad("?", w))
                elif n <= 0:
                    cells.append(c("31", _pad("满", w)))
                elif n <= 3:
                    cells.append(c("33", _pad(n, w)))
                else:
                    cells.append(c("32", _pad(n, w)))
                continue
            cells.append(_pad("" if v is None else v, w))
        print("  ".join(cells))


# ─────────────────────────── 提交选课 ───────────────────────────

def submit(cookie, form, items, dry_run=False, timeout=20):
    body = dict(form)
    body["items"] = items
    # saveDate() 提交时 target=hidFrm，但表单域不变
    body.pop("njzy", None)          # 提交页 ActionForm 没有 njzy
    body["menucode_current"] = MENU
    payload = "&".join(f"{k}={_q(v)}" for k, v in body.items())
    url = f"{BASE}STU_PreElectCourseAction.do?hidOption=saveData"
    if dry_run:
        print(c("36", "[dry-run] POST " + url))
        print(c("36", "[dry-run] body:"))
        for k, v in body.items():
            print(f"    {k} = {v}")
        return None
    raw = _req(cookie, url, data=payload, timeout=timeout)
    return raw


def judge(raw):
    if raw is None:
        return
    txt = raw.decode("gb18030", "replace")
    plain = _txt(txt)
    verdict = pending_receipt()
    assert verdict.state is VerificationState.PENDING
    print(c("90", f"已接收接口响应 {len(raw)}B；响应正文已隐藏；当前结果={verdict.state.value}，待官方逐课程核验"))
    ok = ["选课成功", "成功", "保存成功"]
    full = ["人数已满", "容量已满", "已满", "超过限选"]
    dup = ["已经选", "已选过", "重复", "不能重复"]
    closed = ["不是有效的选课时间", "时间区段", "未到", "已结束"]
    for kw in ok:
        if kw in plain:
            print(c("33", "→ 仅收到成功文案；尚未按官方已选课程逐项核验，不能确认选中"))
            return
    for kw in full:
        if kw in plain:
            print(c("31", f"→ 判定：已满（命中「{kw}」）"))
            return
    for kw in dup:
        if kw in plain:
            print(c("33", "→ 接口提示可能已选过；须以官方已选课程明细为准"))
            return
    for kw in closed:
        if kw in plain:
            print(c("31", f"→ 判定：门外（命中「{kw}」）"))
            return
    print(c("33", "→ 结果未知，请在官方已选课程列表核对；不回显服务器响应正文"))


# ─────────────────────────── CLI ───────────────────────────

def get_cookie(args):
    if args.cookie:
        return args.cookie
    if args.cookie_file and os.path.exists(args.cookie_file):
        s = open(args.cookie_file, encoding="utf-8").read()
        m = re.search(r"JSESSIONID=[0-9A-Fa-f]+", s)
        if m:
            return m.group(0)
    for env in ("KINGO_COOKIE", "HS_COOKIE"):
        if os.environ.get(env):
            return os.environ[env]
    sys.exit('缺 Cookie：--cookie "JSESSIONID=..." / --cookie-file / export KINGO_COOKIE=')


def main():
    ap = argparse.ArgumentParser(description="华水青果选课门禁 / 课程 / 提交")
    ap.add_argument("mode", choices=["gate", "list", "submit"])
    ap.add_argument("--cookie")
    ap.add_argument("--cookie-file", default="hs_cookie.txt")
    ap.add_argument("--xktype", default="1")
    ap.add_argument("--kcfw", default="zxbnj")
    ap.add_argument("--xn", default="2026")
    ap.add_argument("--xq", default="1")
    ap.add_argument("--nj", default="2026")
    ap.add_argument("--zydm", default="0502")
    ap.add_argument("--yxb", default="05")
    ap.add_argument("--kcdm", help="submit: 课程号")
    ap.add_argument("--skbjdm", help="submit: 教学班号（用于核对，服务端只认 kcdm+items）")
    ap.add_argument("--js", default="null", help="submit: 该项的任课教师值，默认 null")
    ap.add_argument("--tj", default="0", help="submit: 允许调剂 0/1")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ck = get_cookie(args)

    if args.mode == "gate":
        show_gate(gate(ck, args.xktype), args.xktype)
        return

    if args.mode == "list":
        g, form = pre_form(ck, args.xktype, args.kcfw, args.nj, args.zydm, args.yxb,
                           args.xn, args.xq)
        show_gate(g, args.xktype)
        print()
        rows, total, html = query_pre(ck, form)
        print(c("36", f"[可选课程] kcfw={args.kcfw} tableId={TABLE_PRE} total={total} 行={len(rows)}"))
        print_rows(rows)
        if not rows and total == 0:
            print(c("90", "  提示：换个 kcfw 试（zxggrx / fx / zxknj），或该学期课表还没发布"))
        return

    if args.mode == "submit":
        if not args.kcdm:
            sys.exit("submit 需要 --kcdm")
        g, form = pre_form(ck, args.xktype, args.kcfw, args.nj, args.zydm, args.yxb,
                           args.xn, args.xq)
        st = str(g.get("status"))
        window = (g.get("result") or {})
        if st != "200" or str(window.get("isValidTimerange")) != "1":
            if not args.dry_run:
                sys.exit("官方选课窗口未明确开放；本次未发送选课提交。")
            print(c("33", "选课窗口未开放；仅执行本地 dry-run，不发送提交。"))
        # items 格式：kcdm,js,tj,checked|
        items = f"{args.kcdm},{args.js},{args.tj},true|"
        print(c("36", f"[提交] kcdm={args.kcdm} skbjdm={args.skbjdm or '-'} items={items}"))
        # Reserve in the same crash-safe journal used by the Qiangke CLI.
        # Dry runs cannot claim an intent because no school write is sent.
        if not args.dry_run:
            if not all(str(form.get(key) or "").strip() for key in ("xh", "xn", "xq")):
                sys.exit("缺少官方账号或学期字段，禁止发送选课提交。")
            try:
                reserved = reserve_shared_intent(
                    school="https://jwmis.ncwu.edu.cn",
                    account=str(form["xh"]),
                    term=f"{form['xn']}-{form['xq']}",
                    course_id=str(args.kcdm),
                    class_id=str(args.skbjdm or ""),
                    # This legacy action submits a course code; the class
                    # hint is optional. Preserve that interface while using
                    # the same durable course-level reservation.
                    require_class=False,
                )
            except (OSError, ValueError, sqlite3.Error) as exc:
                sys.exit(f"持久化提交保护失败，未发送请求：{type(exc).__name__}")
            if not reserved:
                sys.exit("相同账户、学期和目标存在历史提交意图，已停止重发；请核对官方已选记录。")
        # The intent is persisted BEFORE the only physical write; lost responses
        # remain unknown and are never automatically replayed.
        raw = submit(ck, form, items, dry_run=args.dry_run)
        judge(raw)
        return


if __name__ == "__main__":
    main()
