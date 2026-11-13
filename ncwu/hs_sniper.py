#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hs_sniper.py —— 华水青果抢课引擎（门禁 / 盯量 / 校时 / 开火 / 结果验证）

和 course_sniper.py 的区别：
  course_sniper 是「已知接口、对着配置打」的通用枪；
  这个是把青果那套东西全部焊死的专用枪，接口名、字段、判定都是真包还原的。

核心设计（每一条都是被真站打回来之后改的）
────────────────────────────────────────────────────
1. 判定不看响应文本，看「已选门数」。
   实测：向 saveData 提交一个根本不存在的课程号 0000000000，
   服务端照样返回 {"status":"200","message":"操作成功!"}。
   响应 200 ≠ 选上了。已选门数变化仅是线索，不能证明目标课程成功。

2. 门禁只认 getWsxkTimeRange?xktype=N 的 status。
   通知写「补选到 17:00」，接口 jssj 是 23:59。差 7 小时。

3. 余量只认服务端算好的 qdrs 列，不自己拿 cap - taken。
   实测 高等数学A(1)-006 是 限选135 / 已选136 / 可选6 —— cap < taken 还能有量，
   自己减会算出 -1 然后误判「已满」直接放弃。

4. 开火路径零拼装。
   请求字节在开火前就 build 好，开火时只剩 sendall()。
   单次拼装 120~180µs，预构造后 3~5µs。

5. 校时用 Date 翻转法，不用 NTP。
   开闸判定在教务服务器上，对 ntp.aliyun.com 校时等于给自己加偏差。
   实测：NTP 对齐 -315.7ms，Date 翻转法 +0.8ms。

配置 hs_sniper.json
────────────────────────────────────────────────────
{
  "cookie": "JSESSIONID=...",
  "base": "https://jwmis.ncwu.edu.cn/hsjw",
  "xn": "2026", "xq": "1", "nj": "2026", "zydm": "0502", "yxb": "05",
  "xktype": "1",
  "source": "pre",
  "targets": [
    {"kcdm": "0400111101", "name": "工程制图", "skbjdm": "002"},
    {"kcdm": "0800120005", "name": "Python程序设计"}
  ],
  "concurrency": 3,
  "watch_interval": 1.2,
  "max_attempts": 40,
  "time_sync": true
}

用法
────────────────────────────────────────────────────
  python hs_sniper.py --cfg hs_sniper.json --check      # 体检：门禁/校时/目标余量
  python hs_sniper.py --cfg hs_sniper.json --scan       # 只扫一遍余量
  python hs_sniper.py --cfg hs_sniper.json --watch      # 盯盘 + 自动开火
  python hs_sniper.py --cfg hs_sniper.json --fire 0400111101   # 手动开火一个目标
  python hs_sniper.py --cfg hs_sniper.json --watch --at 23:59:55  # 等到点了再开火
"""

import argparse
import http.client
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qiangke.qk.intent_journal import IntentJournal

BASE = "https://jwmis.ncwu.edu.cn/hsjw"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0")

TB_PRE = "2548"      # 预选可选课程（实时，仅窗口内有数据）
TB_PLAN = "5327042"  # 开课计划（常年可读，提前看有哪些课）

_C = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if _C else s


def log(tag, msg, color="36"):
    print(f"{c('90', time.strftime('%H:%M:%S'))} {c(color, tag)} {msg}", flush=True)


def q(s):
    return urllib.parse.quote(str(s), encoding="gbk", errors="replace")


# ═════════════════════════ 连接层 ═════════════════════════

class Jw:
    """
    每线程一条 keep-alive 连接（http.client）。

    为什么不用 course_probe.TimedConn：
      青果这套接口大量返回 Transfer-Encoding: chunked（提交响应就是
      `6d\\r\\n<script ...>\\r\\n0\\r\\n\\r\\n`）。TimedConn 手写的响应解析
      只累计不解码 chunked，body 里带十六进制长度前缀，JSON 解析直接失败，
      而且没消费干净的 chunked 尾会让连接变脏 —— 后续请求报
      WinError 10038（在一个非套接字上尝试了一个操作）。
      http.client 自带 chunked 解码和连接复用，这类坑它替我们踩了。

    代价：每次请求多 ~100µs 的头部拼装。相对 25ms 的 RTT 和服务器处理
    时间可以忽略；而错一次判定要多花一整轮，那才是真的亏。
    """

    def __init__(self, base, cookie, timeout=10, shared=False):
        """
        shared=True：连接存实例上而不是 thread-local。
        用途是连接池预热 —— 主线程先把 TLS 建好，开火线程直接拿来用。
        代价是不能两个线程同时用同一个实例，所以池里每个成员只分给一个 worker。
        """
        self.shared = shared
        self._conn = None
        rest = base.split("://", 1)[1].rstrip("/")
        self.host, _, self.prefix = rest.partition("/")
        self.prefix = "/" + self.prefix if self.prefix else ""
        self.cookie = cookie
        self.timeout = timeout
        self._local = threading.local()
        self._ctx = ssl.create_default_context()
        try:
            self._ctx.set_alpn_protocols(["http/1.1"])
        except NotImplementedError:
            pass

    def conn(self):
        if self.shared:
            if self._conn is None:
                self._conn = self._new_conn()
            return self._conn
        c_ = getattr(self._local, "conn", None)
        if c_ is None:
            c_ = self._new_conn()
            self._local.conn = c_
        return c_

    def _new_conn(self):
        c_ = http.client.HTTPSConnection(self.host, 443, timeout=self.timeout,
                                        context=self._ctx)
        c_.connect()
        try:
            # 教务系统提交请求几百字节，Nagle 会压住等 ACK，纯亏 20~40ms
            c_.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        return c_

    def drop(self):
        if self.shared:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            return
        try:
            self._local.conn.close()
        except Exception:
            pass
        self._local.conn = None

    def _headers(self, referer=None, ajax=False, ctype=None):
        h = {"Cookie": self.cookie, "User-Agent": UA,
             "Origin": "https://" + self.host,
             "Accept-Language": "zh-CN,zh;q=0.9"}
        if ctype:
            h["Content-Type"] = ctype
        if referer:
            h["Referer"] = f"https://{self.host}{self.prefix}/{referer.lstrip('/')}"
        if ajax:
            h["X-Requested-With"] = "XMLHttpRequest"
        return h

    @staticmethod
    def _decode(raw):
        for enc in ("utf-8", "gb18030"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("gb18030", "replace")

    def post(self, path, body, referer=None, ajax=False):
        """→ (status, text, ttfb_ms)"""
        b = body.encode("gbk", "replace") if isinstance(body, str) else body
        h = self._headers(referer, ajax, "application/x-www-form-urlencoded")
        last = None
        for attempt in (1,):
            try:
                t0 = time.perf_counter()
                cn = self.conn()
                cn.request("POST", self.prefix + path, body=b, headers=h)
                r = cn.getresponse()
                ttfb = (time.perf_counter() - t0) * 1000
                raw = r.read()
                if r.status in (200, 302):
                    return r.status, self._decode(raw), ttfb
                last = f"HTTP {r.status}"
                if r.status >= 500:
                    raise ConnectionError(last)
                return r.status, self._decode(raw), ttfb
            except Exception as e:
                last = e
                self.drop()
                raise
        raise RuntimeError(last)

    def get(self, path, referer=None, ajax=False):
        h = self._headers(referer, ajax)
        for attempt in (1, 2):
            try:
                t0 = time.perf_counter()
                cn = self.conn()
                cn.request("GET", self.prefix + path, headers=h)
                r = cn.getresponse()
                ttfb = (time.perf_counter() - t0) * 1000
                raw = r.read()
                return r.status, raw, ttfb, r.getheader("Date", "")
            except Exception:
                self.drop()
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")

    def close(self):
        self.drop()


# ═════════════════════════ 青果接口 ═════════════════════════

class Kingo:
    def __init__(self, jw):
        self.jw = jw

    # ── 门禁 ──
    def gate(self, xktype="1"):
        _, txt, _ = self.jw.post(f"/jw/common/getWsxkTimeRange.action?xktype={xktype}", "",
                                 referer="student/wsxk.yx.html?menucode=S2020201", ajax=True)
        try:
            d = json.loads(txt)
        except Exception:
            return {"status": "?", "message": txt[:120], "result": {}}
        res = d.get("result") or {}
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except Exception:
                res = {}
        d["result"] = res
        return d

    # ── 已选门数 / 学分（唯一可信的成功信号）──
    def selected(self, xn, xq, xh):
        _, txt, _ = self.jw.post("/jw/common/getSelectLessonScoreKcsInfo.action",
                                 f"xn={xn}&xq_m={xq}&xh={xh}",
                                 referer="student/wsxk.zx.html?menucode=S2020202", ajax=True)
        try:
            d = json.loads(txt)
            res = json.loads(d["result"]) if isinstance(d.get("result"), str) else d.get("result")
            return res or {}
        except Exception:
            return {}

    # ── 课程表格（pre / plan 两个源）──
    def table(self, source, form):
        tb = TB_PRE if source == "pre" else TB_PLAN
        body = "&".join(f"{k}={q(v)}" for k, v in form.items())
        ref = ("student/wsxk.yx.html?menucode=S2020201&bqflag=1" if source == "pre"
               else "student/wsxk.kcbcx10319.html?menucode=S2020103")
        _, html, _ = self.jw.post(f"/taglib/DataTable.jsp?tableId={tb}", body, referer=ref)
        return parse_rows(html, tb)

    # ── 提交 ──
    def submit(self, form, items):
        body = dict(form)
        body.pop("njzy", None)
        body["items"] = items
        payload = "&".join(f"{k}={q(v)}" for k, v in body.items())
        st, txt, ms = self.jw.post("/STU_PreElectCourseAction.do?hidOption=saveData", payload,
                                   referer="student/wsxk.yx.html?menucode=S2020201&bqflag=1")
        return st, txt, ms


# ═════════════════════════ 解析 ═════════════════════════

_TR = re.compile(r"<tr\s+class='[OE]'\s+id='tr(\d+)'[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*id='tr\d+_([A-Za-z0-9_]+)'[^>]*>(.*?)</td>", re.S)
_TOT = re.compile(r"showTotalRecord\('(\d+)','(\d+)'\)")


def _txt(h):
    h = re.sub(r"(?is)<br\s*/?>", " ", h)
    h = re.sub(r"<[^>]+>", "", h)
    return re.sub(r"\s+", " ", h).strip()


def _int(s):
    m = re.search(r"-?\d+", str(s or ""))
    return int(m.group(0)) if m else None


def parse_rows(html, tb):
    total = -1
    for tid, n in _TOT.findall(html):
        if tid == tb:
            total = int(n)
    rows = []
    for _, seg in _TR.findall(html):
        d = {k: _txt(v) for k, v in _TD.findall(seg)}
        if not d:
            continue
        mm = re.match(r"\[([0-9A-Za-z]+)\]\s*(.*)", d.get("kc", ""))
        if not mm:
            continue          # 青果的 E 续行：同教学班另一时间段，无余量
        d["kcdm"] = mm.group(1)
        d["kcmc"] = mm.group(2).strip()
        d["cap"] = _int(d.get("xkrssx"))
        one, _, two = d.get("xkrs", "").partition("/")
        d["taken"] = _int(one)
        d["left"] = _int(d.get("qdrs"))
        d["skbjdm"] = d.get("curent_skbjdm") or d.get("skbjdm", "")
        rows.append(d)
    return rows, total


# ═════════════════════════ 校时 ═════════════════════════

# 校时采样端点：必须是「响应头到手时 body 也到手」的轻量资源。
# 实测（2026-11-13，本机 → jwmis.ncwu.edu.cn）：
#   custom/js/SetRootPath.jsp        25.3ms  ← 用这个
#   theme/themeservlet?p=getPopPath  30.0ms
#   frame/homepage?method=getTempNowtime  380ms ← 名字最像校时接口，但服务器内部要等 350ms
#   frame/menu/isMyservice.action    414ms
# 网络基线 RTT ≈ 22~25ms，所以校时精度极限就是 ±11ms 左右，别再追求 1ms。
SAMPLE_PATH = "/custom/js/SetRootPath.jsp"


def date_flip_offset(jw, rounds=2, coarse_ms=50, fine_ms=10, cap=70):
    """
    Date 翻转法：抓 Date 从 T 跳到 T+1 的瞬间，对应服务器整秒边界。

    为什么不用 NTP：开闸判定在教务服务器上，对 ntp.aliyun.com 校时等于
    给自己加一道偏差。上一版实测 NTP 对齐 -315.7ms，这个方法远优于它。

    流程
    ----
    1. 粗扫：每 coarse_ms 采一次，跨越 1 个整秒边界
    2. 精修：在「Date 变化的那一对样本」之间按 fine_ms 再采
    3. 边界 = 同值/异值的分界点；每个样本的本地时刻修正为
       t_send + ttfb/2（服务器生成响应的时刻）

    精度受限于网络 RTT，实测 ~25ms → ±12ms。这是物理下限。

    返回 (offset 秒, 区间宽度秒)。offset = 服务器时间 - 本地时间。
    """
    import calendar

    def one():
        t_send = time.time()
        _, _raw, ttfb_ms, d = jw.get(SAMPLE_PATH,
                                     referer="student/wsxk.yx.html?menucode=S2020201")
        ttfb = ttfb_ms / 1000.0
        try:
            sec = calendar.timegm(time.strptime(d, "%a, %d %b %Y %H:%M:%S GMT"))
        except Exception:
            return None
        return (t_send + ttfb / 2, sec)      # 本地 epoch，服务器整秒

    best = None
    used = 0
    for _ in range(rounds):
        s = one()
        used += 1
        if s is None:
            continue
        t0, sec0 = s
        # 粗扫一秒，找 Date 跳变点
        samples = [s]
        t = t0 + coarse_ms / 1000.0
        while t <= t0 + 1.05 and used < cap:
            now = time.time()
            if now < t:
                time.sleep(t - now)
            ss = one()
            used += 1
            if ss:
                samples.append(ss)
                if ss[1] != sec0:
                    break
            t += coarse_ms / 1000.0
        # 精修
        jump = None
        for i in range(len(samples) - 1):
            if samples[i + 1][1] != samples[i][1]:
                jump = (samples[i], samples[i + 1])
                break
        if jump is None:
            continue
        (ta, sa), (tb, sb) = jump
        lo, hi = ta, tb
        t = lo + fine_ms / 1000.0
        while t < hi and used < cap:
            now = time.time()
            if now < t:
                time.sleep(t - now)
            ss = one()
            used += 1
            if ss:
                if ss[1] == sa:
                    lo = ss[0]
                else:
                    hi = ss[0]
                    break
            t += fine_ms / 1000.0
        edge = (lo + hi) / 2
        off = (sa + 1) - edge
        width = hi - lo
        if best is None or width < best[1]:
            best = (off, width)
        if best[1] < 0.008:
            break
    return best if best else (None, None)


# ═════════════════════════ 引擎 ═════════════════════════

class Sniper:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg.get("base", BASE)
        self.cookie = cfg["cookie"]
        self.jw = Jw(self.base, self.cookie, timeout=cfg.get("timeout", 10))
        self.k = Kingo(self.jw)
        self.offset = 0.0
        self.form = {}
        self.gate_info = {}
        self.done = set()
        self.baseline = None
        self.lock = threading.Lock()
        self.hit = False

    # ── 准备 ──
    def prepare(self):
        cfg = self.cfg
        g = self.k.gate(cfg.get("xktype", "1"))
        self.gate_info = g
        st = str(g.get("status"))
        r = g.get("result") or {}
        color = "32" if st == "200" else "31"
        log("门禁", f"xktype={cfg.get('xktype', '1')} status={st} {g.get('message')}", color)
        if r:
            log("门禁", f"{r.get('xnxqDesc', '')} / {r.get('lcmc', '')}  "
                       f"{r.get('qssj', '')} → {r.get('jssj', '')}  "
                       f"isValidTimerange={r.get('isValidTimerange')}")
        self.form = {
            "initQry": "0", "xktype": cfg.get("xktype", "1"),
            "xh": r.get("xh", cfg.get("xh", "")),
            "xn": r.get("xn", cfg.get("xn", "2026")),
            "xq": r.get("xqM", cfg.get("xq", "1")),
            "nj": r.get("nj", cfg.get("nj", "2026")),
            "zydm": r.get("zydm", cfg.get("zydm", "")),
            "yxbdm": r.get("yxbdm", cfg.get("yxb", "")),
            "items": "", "sfbd": r.get("sfbd", ""), "lcid": r.get("lcid", ""),
            "djs": r.get("djs", ""), "kxkc": r.get("kxkc", ""),
            "xfsx": r.get("xfsx", ""), "mssx": r.get("mssx", ""),
            "kcfw": cfg.get("kcfw", "zxbnj"),
            "sel_yxb": r.get("yxbdm", cfg.get("yxb", "")),
            "njzy": f"{r.get('nj', cfg.get('nj'))}|{r.get('zydm', cfg.get('zydm'))}",
            "menucode_current": "S2020201",
        }
        self.xh = self.form["xh"]
        self.xn = self.form["xn"]
        self.xq = self.form["xq"]
        if not self.xh:
            log("配置", "拿不到学号，检查 cookie 是否有效", "31")
        return st == "200"

    def plan_form(self):
        cfg = self.cfg
        return {
            "initQry": "0", "xktype": "2",
            "xn": cfg.get("xn", "2026"), "xq": cfg.get("xq", "1"),
            "nj": cfg.get("nj", "2026"), "pycc": cfg.get("pycc", "05"),
            "dwh": cfg.get("yxb", "05"), "zydm": cfg.get("zydm", "0502"),
            "kclb1": "", "kclb2": "", "isbyk": "", "items": "",
            "xnxq": f"{cfg.get('xn', '2026')},{cfg.get('xq', '1')}",
            "btnFilter": "类别过滤", "btnSubmit": "提交",
            "sel_pycc": cfg.get("pycc", "05"), "sel_nj": cfg.get("nj", "2026"),
            "sel_yxb": cfg.get("yxb", "05"), "sel_zydm": cfg.get("zydm", "0502"),
            "kcmk": "", "sel_schoolarea": "", "sel_kclb1": "", "sel_kclb2": "",
            "sel_kc": "", "sel_rkjs": "", "sel_cddwdm": "",
            "menucode_current": "S2020103",
        }

    def sync(self):
        if not self.cfg.get("time_sync", True):
            return
        log("校时", "Date 翻转法定位服务器整秒边界…")
        off, w = date_flip_offset(self.jw)
        if off is None:
            log("校时", "失败，按 0 处理（误差 ±500ms）", "33")
            self.offset = 0.0
        else:
            self.offset = off
            log("校时", f"offset={off * 1000:+.1f}ms  区间±{w * 1000 / 2:.1f}ms", "32")

    def now_srv(self):
        return time.time() + self.offset

    # ── 扫余量 ──
    def scan(self):
        src = self.cfg.get("source", "pre")
        form = self.form if src == "pre" else self.plan_form()
        rows, total = self.k.table(src, form)
        return rows, total

    def report(self, rows, total):
        targets = self.cfg.get("targets") or []
        if not targets:
            log("扫量", f"{len(rows)} 个教学班（未配置目标，仅展示）")
            for d in rows[:20]:
                log("  ", f"{d['kcdm']} {d['kcmc'][:16]:18} 班{d['skbjdm']:>4} "
                          f"{d['taken']}/{d['cap']} 可选={d['left']}")
            return
        for t in targets:
            kcdm = str(t.get("kcdm"))
            cands = [d for d in rows if d["kcdm"] == kcdm]
            if t.get("skbjdm"):
                cands = [d for d in cands if d["skbjdm"] == str(t["skbjdm"])]
            if not cands:
                log("目标", f"{kcdm} {t.get('name', '')} → 未出现（课表未发布或不在范围）", "33")
                continue
            for d in cands:
                n = d["left"]
                col = "32" if (n or 0) > 3 else ("33" if (n or 0) > 0 else "31")
                log("目标", f"{kcdm} {d['kcmc'][:14]:16} 班{d['skbjdm']:>4} "
                            f"{d['taken']}/{d['cap']} 可选={n}  {d['rkjs']}", col)

    # ── 开火 ──
    def fire_one(self, kcdm, skbjdm_hint="", tj="0", jw=None):
        """发一次提交；返回的是传输回执，不是选课成功证明。"""
        items = f"{kcdm},null,{tj},true|"
        t0 = time.perf_counter()
        k = Kingo(jw) if jw is not None else self.k
        try:
            st, txt, ms = k.submit(self.form, items)
        except Exception as exc:
            return False, f"请求异常：{type(exc).__name__}，结果待核验", (time.perf_counter() - t0) * 1000
        # Never print arbitrary response text, including in debug mode:
        # acknowledgements can contain session information or private data.
        if os.environ.get("HS_DEBUG"):
            print(f"    [dbg] HTTP status={st} bytes={len(txt)} (body redacted)", flush=True)
        # An HTTP acknowledgement is transport evidence only. The server
        # may acknowledge even nonexistent course codes.
        ack = str(st) in ("200", "201", "302")
        return ack, "仅收到提交回执；官方已选课程待核验", ms

    def warmup(self, n):
        """
        预热 n 条 keep-alive 连接。

        为什么值得：开火那一发如果要现建 TLS，光握手就 50~80ms，
        而整次提交的服务器处理才 ~60ms。预热等于把开火延迟砍一半。
        每个池成员是独立 Jw（独立 socket），worker 按索引取，不会互相踩。
        """
        self.pool = []
        for _ in range(max(1, n)):
            jw = Jw(self.base, self.cookie, timeout=self.cfg.get("timeout", 10), shared=True)
            try:
                jw.get(SAMPLE_PATH, referer="student/wsxk.yx.html?menucode=S2020201")
                self.pool.append(jw)
            except Exception:
                pass
        log("预热", f"{len(self.pool)}/{n} 条连接就绪", "32" if self.pool else "31")
        return len(self.pool)

    def fire(self, kcdm, skbjdm="", conc=None, tj=None):
        # This standalone engine cannot infer success from HTTP 200 or a count.
        # A current, official open-window indication and durable intent are
        # required before the sole physical submission.
        gate = self.gate_info or {}
        if (str(gate.get("status")) != "200"
                or str((gate.get("result") or {}).get("isValidTimerange")) != "1"):
            log("保护", "官方选课时间窗未确认开放，未发送提交", "31")
            return None
        if not all(str(x or "").strip() for x in (
                getattr(self, "xh", ""), getattr(self, "xn", ""), getattr(self, "xq", ""))):
            log("保护", "缺少经接口确认的账号或学期，未发送提交", "31")
            return None
        path = (os.environ.get("KINGO_INTENT_DB")
                or Path.home() / ".kingo-assistant" / "intent-journal.sqlite3")
        try:
            reserved = IntentJournal(path).reserve(
                school="https://jwmis.ncwu.edu.cn",
                account=str(self.xh), term=f"{self.xn}-{self.xq}",
                course={"kch_id": str(kcdm), "xkid": str(skbjdm)},
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            log("保护", f"提交记录无法持久化：{type(exc).__name__}，未发送请求", "31")
            return None
        if not reserved:
            log("保护", "该课程存在待核验的历史提交，禁止跨重启重发", "31")
            return None
        conc = 1  # A target gets one physical write; parallel duplicates are unsafe.
        # tj（允许调剂）建议开 1：items 里只有 kcdm 没有 skbjdm，
        # 说明班次是服务端按「限选人数 + 调剂」自己分配的。开 1 等于
        # 让系统把你塞进任何还有位的班，成功率显著高于死磕某个班号。
        tj = str(self.cfg.get("allow_adjust", 1) if tj is None else tj)
        # 开火前基线：只在第一次查，之后用缓存 —— 每查一次多花 ~60ms，
        # 抢课窗口里这 60ms 就是位子。
        if self.baseline is None:
            self.baseline = (self.k.selected(self.xn, self.xq, self.xh) or {}).get("yxms")
        before = self.baseline
        log("开火", f"{kcdm} 班{skbjdm or '*'} 并发{conc} 调剂={tj}  "
                    f"开火前已选={before} 门", "35")

        results = []
        rl = threading.Lock()

        pool = getattr(self, "pool", None) or []
        stagger = float(self.cfg.get("stagger_ms", 0)) / 1000.0

        def worker(i):
            if stagger and i > 1:
                time.sleep(stagger * (i - 1))
            jw = pool[i % len(pool)] if pool else None
            ok, msg, ms = self.fire_one(kcdm, skbjdm, tj, jw)
            with rl:
                results.append((i, ok, msg, ms))
                log("回执", f"  #{i} {'HTTP应答' if ok else '响应未知'} {ms:6.1f}ms；官方选课结果待核验",
                    "33")

        ths = [threading.Thread(target=worker, args=(i + 1,), daemon=True) for i in range(conc)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=20)

        # 只读观察：已选门数变化不是目标课程的成功证明
        # 实测：提交一个不存在的课程号，服务端照样返回 status 200「操作成功」。
        # 响应 200 ≠ 选上了，所以必须回查一次。
        time.sleep(float(self.cfg.get("verify_delay", 0.4)))
        info = self.k.selected(self.xn, self.xq, self.xh) or {}
        after = info.get("yxms")
        log("验证", f"已选门数 {before} → {after}；门数变化不能确认目标课程，结果待核验", "33")
        return None

    # ── 主循环 ──
    def run(self, at=None, once=False):
        self.prepare()
        self.sync()
        rows, total = self.scan()
        log("扫量", f"source={self.cfg.get('source', 'pre')} total={total} 行={len(rows)}")
        self.report(rows, total)

        if once:
            return

        targets = self.cfg.get("targets") or []
        if not targets:
            log("退出", "没配 targets，只做扫量。加 targets 才能自动开火。", "33")
            return

        interval = float(self.cfg.get("watch_interval", 1.2))
        max_attempts = int(self.cfg.get("max_attempts", 40))
        attempts = {}
        waiting_gate = False

        if at:
            hh, mm, ss = [int(x) for x in at.split(":")]
            t = time.localtime(self.now_srv())
            target = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hh, mm, ss, 0, 0, -1))
            wait = target - self.now_srv()
            if wait > 0:
                log("等待", f"距 {at} 还有 {wait:.1f}s，先睡到开火前 3s")
                time.sleep(max(0, wait - 3))

        log("盯盘", f"每 {interval}s 一次，命中即开火。Ctrl-C 停。")
        n = 0
        while True:
            n += 1
            try:
                rows, total = self.scan()
            except Exception as e:
                log("扫量", f"失败 {e}", "31")
                time.sleep(interval)
                continue

            alive = []
            for t in targets:
                kcdm = str(t.get("kcdm"))
                if kcdm in self.done:
                    continue
                cands = [d for d in rows if d["kcdm"] == kcdm]
                if t.get("skbjdm"):
                    cands = [d for d in cands if d["skbjdm"] == str(t["skbjdm"])]
                alive.append((t, cands))

            if not any(cands for _, cands in alive):
                if not waiting_gate:
                    log("盯盘", f"目标全部还没出现在课表里（第 {n} 轮）", "33")
                    waiting_gate = True
                sys.stdout.write(c("90", "."))
                sys.stdout.flush()
                time.sleep(interval)
                continue
            waiting_gate = False

            fired = False
            for t, cands in alive:
                kcdm = str(t.get("kcdm"))
                open_ones = [d for d in cands if (d["left"] or 0) > 0]
                if not open_ones:
                    continue
                # 优先挑余量最多的班（更容易抢到）
                open_ones.sort(key=lambda d: -(d["left"] or 0))
                pick = open_ones[0]
                cnt = attempts.get(kcdm, 0)
                if cnt >= max_attempts:
                    log("放弃", f"{kcdm} 已试 {cnt} 次未成功", "31")
                    self.done.add(kcdm)
                    continue
                attempts[kcdm] = cnt + 1
                log("命中", f"{kcdm} {pick['kcmc'][:14]} 班{pick['skbjdm']} "
                            f"可选={pick['left']}  第 {cnt + 1}/{max_attempts} 次")
                fired = True
                if not getattr(self, "pool", None):
                    self.warmup(int(self.cfg.get("concurrency", 3)))
                outcome = self.fire(kcdm, pick["skbjdm"])
                if outcome is None:
                    log("暂停", "提交结果待官方已选课程逐项核验，停止自动重发", "33")
                    return
                if outcome:
                    self.done.add(kcdm)
                    log("完成", f"{kcdm} {pick['kcmc']} 已进袋", "32")
                break   # 一轮只打一个目标，避免并发打满

            if self.done and len(self.done) >= len({str(t.get("kcdm")) for t in targets}):
                log("收工", "全部目标完成", "32")
                return
            if not fired:
                time.sleep(interval)


# ═════════════════════════ 体检 ═════════════════════════

def check(cfg):
    jw = Jw(cfg.get("base", BASE), cfg["cookie"])
    k = Kingo(jw)
    print(c("36", "─" * 62))
    g = k.gate(cfg.get("xktype", "1"))
    r = g.get("result") or {}
    st = str(g.get("status"))
    print(c("32" if st == "200" else "31",
            f"门禁 xktype={cfg.get('xktype', '1')}  status={st}  {g.get('message')}"))
    for key in ("xnxqDesc", "lcmc", "qssj", "jssj", "isValidTimerange", "lcid", "sfbd", "kxkc"):
        if key in r:
            print(f"   {key:18} = {r[key]}")
    xh = r.get("xh", cfg.get("xh"))
    xn = r.get("xn", cfg.get("xn", "2026"))
    xq = r.get("xqM", cfg.get("xq", "1"))
    if xh:
        info = k.selected(xn, xq, xh) or {}
        print(c("36", f"   已选: {info.get('yxms')} 门 / {info.get('yxxf')} 学分"
                      f"（指定 {info.get('zdms')} 门 / {info.get('zdxf')} 学分）"))

    off, w = date_flip_offset(jw)
    if off is None:
        print(c("33", "   校时: 失败（将按 0 处理）"))
    else:
        print(c("32", f"   校时: offset={off * 1000:+.1f}ms  ±{w * 1000 / 2:.1f}ms"))

    for src in ("pre", "plan"):
        form = (None)
        # 复用 Sniper 的表单构造
        s = Sniper(cfg)
        s.form = {"initQry": "0", "xktype": cfg.get("xktype", "1"), "xh": xh or "",
                  "xn": xn, "xq": xq, "nj": r.get("nj", cfg.get("nj", "2026")),
                  "zydm": r.get("zydm", cfg.get("zydm", "")),
                  "yxbdm": r.get("yxbdm", cfg.get("yxb", "")), "items": "",
                  "sfbd": r.get("sfbd", ""), "lcid": r.get("lcid", ""),
                  "djs": r.get("djs", ""), "kxkc": r.get("kxkc", ""),
                  "xfsx": r.get("xfsx", ""), "mssx": r.get("mssx", ""),
                  "kcfw": cfg.get("kcfw", "zxbnj"),
                  "sel_yxb": r.get("yxbdm", cfg.get("yxb", "")),
                  "njzy": f"{r.get('nj', cfg.get('nj'))}|{r.get('zydm', cfg.get('zydm'))}",
                  "menucode_current": "S2020201"}
        form = s.form if src == "pre" else s.plan_form()
        try:
            rows, total = k.table(src, form)
            print(c("36", f"   {src:4} total={total:4}  行={len(rows)}"))
        except Exception as e:
            print(c("31", f"   {src:4} 失败 {e}"))
    print(c("36", "─" * 62))


# ═════════════════════════ main ═════════════════════════

def load_cfg(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def main():
    ap = argparse.ArgumentParser(description="华水青果抢课引擎")
    ap.add_argument("--cfg", default="hs_sniper.json")
    ap.add_argument("--cookie")
    ap.add_argument("--check", action="store_true", help="体检")
    ap.add_argument("--scan", action="store_true", help="只扫一遍")
    ap.add_argument("--watch", action="store_true", help="盯盘自动开火")
    ap.add_argument("--fire", metavar="KCDM", help="手动开火一个课程号")
    ap.add_argument("--skbjdm", default="", help="配合 --fire 指定教学班")
    ap.add_argument("--at", help="HH:MM:SS，等到该时刻再开火")
    ap.add_argument("--conc", type=int, help="覆盖并发数")
    args = ap.parse_args()

    cfg = load_cfg(args.cfg)
    if args.cookie:
        cfg["cookie"] = args.cookie
    if not cfg.get("cookie"):
        # 仓库约定：真实 Cookie 只走环境变量，绝不落盘
        for env in ("KINGO_COOKIE", "HS_COOKIE"):
            if os.environ.get(env):
                cfg["cookie"] = os.environ[env]
                break
    if not cfg.get("cookie"):
        sys.exit("缺 cookie：--cookie、export KINGO_COOKIE=、或写进本地 hs_sniper.json（已被 .gitignore 忽略）")
    if args.conc:
        cfg["concurrency"] = args.conc

    if args.check:
        check(cfg)
        return

    s = Sniper(cfg)
    if args.fire:
        s.prepare()
        s.sync()
        s.warmup(int(cfg.get("concurrency", 3)))
        ok = s.fire(args.fire, args.skbjdm)
        sys.exit(0 if ok else 1)

    s.run(at=args.at, once=args.scan)


if __name__ == "__main__":
    main()

