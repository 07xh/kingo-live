# -*- coding: utf-8 -*-
"""青果 KINGOSOFT 教务适配器 —— 华北水利水电大学 jwmis.ncwu.edu.cn/hsjw。

登录链路（全部来自站点真实前端 loginbar.js / SetKingoEncypt.jsp）：
  1. GET  /hsjw/                       → 302 到 /hsjw/cas/login.action，取 _sessionid/_ssessionid/yzmbt
  2. GET  /hsjw/frame/homepage?method=getTempDeskey    → DES 密钥（纯文本）
  3. GET  /hsjw/frame/homepage?method=getTempNowtime   → 服务器时间戳字符串
  4. GET  /hsjw/cas/genValidateCode?v=xxx              → 验证码图（yzmbt=="1" 时必须）
  5. params 明文拼接 → DES 加密 → base64 → 与 token/timestamp/deskey/ssessionid 一起
     POST /hsjw/cas/logon.action，返回 JSON {"status":"200","result":"<跳转URL>"}

抢课：青果各校选课 action 不统一，用「抓包模板」驱动（config.system.grab_request），
模板里的 {xxx} 从 course 字典取值；URL 里的课程标识自动参数化。
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import time
from urllib.parse import quote

from ..kingo_des import str_enc
from ..net import NetError, Session
from .base import AUTH, CONFLICT, ENROLLED, ERR, FULL, OK, Adapter, GrabResult

DEFAULTS = {
    "entry": "/hsjw/",
    "logon": "/hsjw/cas/logon.action",
    "deskey": "/hsjw/frame/homepage?method=getTempDeskey",
    "nowtime": "/hsjw/frame/homepage?method=getTempNowtime",
    "captcha": "/hsjw/cas/genValidateCode",
    "home": "/hsjw/frame/homes.action",
    "xsxk": "/hsjw/xsxk/xsxk.action",
}

# 已探测确认真实存在（未登录返回「凭证已失效」而非 404）的两个业务 action
DEFAULT_LIST = {
    "method": "POST",
    "url": "/hsjw/xsxk/xsxk!cxXkList.action",
    "headers": {"X-Requested-With": "XMLHttpRequest"},
    "form": {},
}
DEFAULT_GRAB = {
    "method": "POST",
    "url": "/hsjw/xsxk/xsxk!addXk.action",
    "headers": {"X-Requested-With": "XMLHttpRequest"},
    "form": {"xkid": "{xkid}"},
}

FAIL_WORDS = ("密码错误", "用户名或密码", "账号或密码", "不存在", "验证码错误", "验证码不正确",
              "已锁定", "密码不正确", "输错", "锁定")
AUTH_WORDS = ("凭证已失效", "请重新登录", "重新登录", "login.action")
OK_WORDS = ("成功", "选课成功", "操作成功", "\"status\":\"200\"")
ENROLLED_WORDS = ("已选", "已经选", "重复", "已存在")
FULL_WORDS = ("已满", "人数已满", "余量不足", "无余量", "容量")
CONFLICT_WORDS = ("冲突", "学分", "门数", "时间不", "先修")


class CredentialBlocked(Exception):
    """账号/密码类错误 —— 绝不重试，重试等于把账号往锁里推。"""


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode()


def _expr_of(pwd: str) -> int:
    r = 0
    for ch in pwd:
        o = ord(ch)
        if 48 <= o <= 57:
            r |= 8
        elif 97 <= o <= 122:
            r |= 4
        elif 65 <= o <= 90:
            r |= 2
        else:
            r |= 1
    return r


def pwd_field(pwd: str, randnumber: str = "", mode: str = "md5") -> str:
    """`_p` 的取值。

    手机端 loginbar.js 走 isXqeScanQrCode 分支：
        password = hex_md5(hex_md5(password) + hex_md5(randnumber.toLowerCase()))
    服务端直接拿这个串去比对，所以发明文一律判「密码错误」。
    """
    if (mode or "md5").lower() == "plain":
        return pwd
    return _md5(_md5(pwd) + _md5((randnumber or "").lower()))


def build_logon_params(user: str, pwd: str, sessionid: str,
                       randnumber: str = "", need_captcha: bool = False,
                       pwd_mode: str = "md5") -> str:
    """loginbar.js 里的 params 明文串（DES 前）。"""
    u = _b64(f"{user};;{sessionid}")
    pol = "1" if (len(pwd) >= 6 and pwd != user) else "0"
    uzh = "1" if (user.lower().strip() and user.lower().strip() in pwd.lower()) else "0"
    hid_flag = "" if need_captcha else "1"
    # 浏览器里 randnumber 为空就是空串，参数名随之退化成 _u / _p，不能硬塞 0000
    p_rand = randnumber or ""
    # 注意：txt_mm_length / txt_mm_expression 用的是原始密码，不是 md5 后的
    return (f"_u{p_rand}={u}&_p{p_rand}={pwd_field(pwd, p_rand, pwd_mode)}"
            f"&randnumber={p_rand}"
            f"&isPasswordPolicy={pol}&txt_mm_expression={_expr_of(pwd)}"
            f"&txt_mm_length={len(pwd)}&txt_mm_userzh={uzh}"
            f"&hid_flag={hid_flag}&hidlag=1&hid_dxyzm=")


def build_logon_body(user: str, pwd: str, sessionid: str, ssessionid: str,
                     deskey: str, timestamp: str, randnumber: str = "",
                     need_captcha: bool = False, pwd_mode: str = "md5") -> str:
    """完全对齐 loginbar.js 的 checkrand()，返回 POST body 原文。"""
    params = build_logon_params(user, pwd, sessionid, randnumber, need_captcha, pwd_mode)
    token = _md5(_md5(params) + _md5(timestamp))
    enc = _b64(str_enc(params, deskey))
    return (f"params={enc}&token={token}&timestamp={timestamp}"
            f"&deskey={deskey}&ssessionid={ssessionid}")


class KingoAdapter(Adapter):
    name = "kingo"

    def __init__(self, acct: dict, opts: dict, log, solver=None):
        super().__init__(acct, opts, log)
        self.cfg = opts or {}
        self.paths = dict(DEFAULTS)
        self.paths.update(self.cfg.get("paths") or {})
        self.base_url = (self.cfg.get("base_url") or "https://jwmis.ncwu.edu.cn").rstrip("/")
        self.grab_request = self.cfg.get("grab_request") or {}
        self.list_request = self.cfg.get("list_request") or {}
        self.solver = solver
        self.vars: dict[str, str] = {}

    # ---------- 基础 ----------
    def p(self, key: str) -> str:
        return self.paths[key]

    @property
    def prefix(self) -> str:
        """站点根路径，如 /hsjw。由 entry 反推，避免到处硬编码。"""
        entry = self.paths.get("entry") or "/hsjw/"
        first = entry.strip("/").split("/")[0]
        return "/" + first

    def open(self) -> None:
        self.session = Session(
            base_url=self.base_url,
            timeout=float(self.cfg.get("timeout", 8)),
            retries=int(self.cfg.get("retries", 1)),
            headers=self.cfg.get("headers") or {},
            verify_tls=bool(self.cfg.get("verify_tls", False)),
            logger=self.log,
        )
        try:
            t0 = time.time()
            self.session.get(self.p("entry"), timeout=8)
            self.log.info(f"对时：服务器-本地 = {self.session.time_offset:+.3f}s（本地 RTT {time.time()-t0:.2f}s）")
        except NetError as e:
            self.log.warning(f"入口不可达：{e}")

    # ---------- 登录 ----------
    def _validate_acct(self) -> None:
        """占位符/空值直接拦下 —— 一次请求都不发，别白送错误次数。"""
        u = str(self.acct.get("user") or "").strip()
        p = str(self.acct.get("pass") or "")
        if not u or not p:
            raise CredentialBlocked("账号或密码为空，先改 config.ncwu.json")
        placeholders = ("xxxxxx", "xxxx", "学号", "教务密码", "密码", "password", "你的")
        if any(t in u for t in placeholders) or p in placeholders or any(t in p for t in ("xxxx", "教务密码", "你的")):
            raise CredentialBlocked(
                f"配置里还是占位符（user={u!r}）—— 填真实学号和密码后再跑，"
                "青果错 5 次锁号，占位符请求一条都别发")

    def login(self) -> bool:
        with self.lock:
            if getattr(self, "blocked", False):
                self.log.error("凭据已熔断，拒绝再试（防止锁号）")
                return False
            try:
                self._validate_acct()
            except CredentialBlocked as e:
                self.blocked = True
                self.log.error(str(e))
                return False
            # 默认走青果本地登录（手机真机验证过的正路）；CAS 需显式配置
            if (self.cfg.get("auth") or {}).get("mode", "local") == "cas":
                return self._login_cas()
            tries = int(self.cfg.get("login_retry", 2))
            for attempt in range(tries):
                try:
                    if self._login_once(attempt):
                        self.logged_in = True
                        self.log.ok(f"[{self.acct.get('name','')}] 登录成功")
                        return True
                except CredentialBlocked as e:
                    self.blocked = True
                    self.log.error("=" * 62)
                    self.log.error(f"停止尝试：{e}")
                    self.log.error("去浏览器（无痕窗口）确认学号/密码能否登录。")
                    self.log.error("能登 → 问题在参数（密码含特殊字符等）；登不上 → 密码不对。")
                    self.log.error("青果 24h 内错 5 次锁号，别再跑本工具。")
                    self.log.error("=" * 62)
                    return False
                except NetError as e:
                    self.log.warning(f"登录网络异常(第{attempt+1}次): {e}")
                time.sleep(0.5)
            self.log.error(f"[{self.acct.get('name','')}] 登录失败")
            return False

    def _pwd_mode(self) -> str:
        """_p 传什么：md5 = hex_md5(hex_md5(pwd)+hex_md5(rand))；plain = 明文。"""
        auth = self.cfg.get("auth") or {}
        return (auth.get("pwd_mode") or self.cfg.get("pwd_mode") or "md5").lower()

    def _login_cas(self) -> bool:
        """走统一身份认证（authserver CAS），成功后青果会话自动建立。"""
        from ..cas import CasAuth
        cas = CasAuth(self.session, self.cfg, self.log)
        self.log.info(f"CAS 登录 → {cas.service}")
        try:
            ok = cas.login(self.acct.get("user", ""), self.acct.get("pass", ""))
        except Exception as e:  # 网络/解析异常不该算凭据错误
            self.log.error(f"CAS 登录异常: {e}")
            return False
        if not ok:
            msg = cas.last_error or "未知原因"
            self.log.error(f"CAS 认证失败：{msg}")
            if any(w in msg for w in FAIL_WORDS) or "有误" in msg:
                self.blocked = True
                self.log.error("判为凭据错误 → 熔断，不再重试（避免锁号）")
            return False
        try:
            r = self.session.get(self.p("home"), referer="/hsjw/")
        except NetError as e:
            self.log.error(f"CAS 通过但青果主页打不开: {e}")
            return False
        if "凭证已失效" in (r.text or ""):
            self.log.error("CAS 通过，但青果未接受会话（ticket 未生效）")
            return False
        self.logged_in = True
        self.log.ok(f"[{self.acct.get('name','')}] 登录成功（CAS）")
        return True

    def _login_once(self, attempt: int = 0) -> bool:
        s = self.session
        page = s.get(self.p("entry"), referer="/hsjw/")
        html = page.text
        if "凭证已失效" in html or len(html) < 500:
            html = s.get("/hsjw/cas/login.action").text

        sid = self._var(html, "_sessionid")
        ssid = self._var(html, "_ssessionid") or sid
        yzmbt = self._var(html, "yzmbt")
        self.vars.update(_sessionid=sid, _ssessionid=ssid, yzmbt=yzmbt)
        if not sid:
            self.log.warning("没取到 _sessionid，页面结构可能变了")

        need_code = (yzmbt == "1") or bool(self.cfg.get("captcha", {}).get("force")) \
            or self.cfg.get("captcha", {}).get("enabled") is True

        rnd = ""
        if need_code:
            img = s.get(self.p("captcha"), params={"v": random.randint(1, 9999)},
                        referer="/hsjw/")
            if img.status == 200 and len(img.content) > 200:
                rnd = (self.solver.solve(img.content, "青果验证码") if self.solver else "") or ""
            if not rnd:
                self.log.warning("验证码为空，本次跳过")
                return False

        try:
            deskey = s.get(self.p("deskey"), referer="/hsjw/").text.strip()
            timestamp = s.get(self.p("nowtime"), referer="/hsjw/").text.strip()
        except NetError as e:
            self.log.warning(f"取加密密钥失败: {e}")
            return False
        if not deskey or not timestamp:
            self.log.warning(f"密钥/时间戳异常 deskey={deskey!r} ts={timestamp!r}")
            return False
        self.vars.update(deskey=deskey, timestamp=timestamp)

        # 浏览器在 checkrand 里先执行 _sessionid = _ssessionid，再拼 _u，故两处都用 _ssessionid
        body = build_logon_body(self.acct.get("user", ""), self.acct.get("pass", ""),
                                ssid, ssid, deskey, timestamp, rnd, need_code,
                                pwd_mode=self._pwd_mode())
        r = s.post(self.p("logon"), data=body, referer="/hsjw/",
                   headers={"X-Requested-With": "XMLHttpRequest",
                            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "Accept": "application/json, text/javascript, */*; q=0.01"})
        raw = (r.text or "").strip()
        status, msg, target = self._parse_logon(raw)
        if status == "200":
            if target:
                g = s.get(target, referer="/hsjw/")
                if "凭证已失效" in g.text:
                    self.log.warning("登录返回 200 但后续仍是未登录态")
                    return False
            return True
        # 凭据类失败一律熔断，不进入下一轮
        self._guard_credential(status, msg)
        if any(w in msg for w in FAIL_WORDS):
            self.log.warning(f"登录被拒：{msg}（第 {attempt+1} 次）")
        else:
            self.log.warning(f"登录失败 status={status} msg={msg[:80]} raw={raw[:120]}")
        return False

    def _guard_credential(self, status: str, msg: str) -> None:
        """识别「密码错/接近锁定」，抛 CredentialBlocked 让上层立即收手。"""
        msg = msg or ""
        m = re.search(r"\|\s*(\d+)\s*$", msg)
        tries = int(m.group(1)) if m else 0
        limit = int(self.cfg.get("max_fail_tries", 3))
        if "锁定" in msg or tries >= limit:
            raise CredentialBlocked(f"服务端已记录输错 {tries or '多'} 次 → {msg.strip()[:60]}")
        if status in ("401", "402") or "输错" in msg or any(w in msg for w in FAIL_WORDS):
            raise CredentialBlocked(f"账号/密码被判定错误 → {msg.strip()[:60] or status}")

    @staticmethod
    def _parse_logon(raw: str):
        try:
            d = json.loads(raw)
            return str(d.get("status", "")), str(d.get("message", "")), str(d.get("result", ""))
        except Exception:
            m = re.search(r'"status"\s*:\s*"?(\d+)"?', raw)
            mm = re.search(r'"message"\s*:\s*"([^"]*)"', raw)
            rr = re.search(r'"result"\s*:\s*"([^"]*)"', raw)
            return (m.group(1) if m else "", mm.group(1) if mm else raw[:80],
                    rr.group(1) if rr else "")

    @staticmethod
    def _var(html: str, name: str) -> str:
        m = re.search(rf"var\s+{name}\s*=\s*[\"']([^\"']*)[\"']", html)
        return m.group(1) if m else ""

    # ---------- 预热 / 发现 ----------
    # 青果选课流程类型：1 预选/补选 2 正选 3 补选 4 重修 5 退选 6 取消正选 7/8 实验
    XKTYPES = (1, 2, 3, 4, 5, 6, 7, 8)

    def check_all_windows(self) -> list[dict]:
        """遍历所有选课流程类型。只查一个 xktype 会漏掉正在开放的窗口。"""
        out = []
        for t in self.XKTYPES:
            info = self.check_xk_window(t)
            info["xktype"] = t
            out.append(info)
        return out

    # ---------- 选课页 / 列表 / 提交（青果 DataTable 模式） ----------
    # 已知选课页与其提交接口
    XK_PAGES = {
        "pre_elect": ("/student/wsxk.yx.html", "STU_PreElectCourseAction.do?hidOption=saveData"),
        "by_plan": ("/student/wsxk.zx.html", ""),           # 正选走流程页，提交在 wsxk.zx_lc.jsp
        "by_volunteer": ("/student/wsxk.zx.byvl.html", ""),
        "retake": ("/student/wsxk.cxxk.jsp", ""),
    }

    def xk_page(self, kind: str = "pre_elect") -> dict:
        """打开选课页，抽出 hidden 字段初值、tableId、提交地址。"""
        from .. import kingo_xk as X

        page, action = self.XK_PAGES.get(kind, self.XK_PAGES["pre_elect"])
        r = self.session.get(f"{self.prefix}{page}", referer=f"{self.prefix}/frame/homes.action")
        html = r.text or ""

        fields: dict[str, str] = {}
        for m in re.finditer(r"<input\b([^>]*)>", html, re.I):
            a = dict(re.findall(r"([\w:-]+)\s*=\s*[\"']([^\"']*)[\"']", m.group(1)))
            name = a.get("name") or a.get("id") or ""
            if name and (a.get("type") or "").lower() == "hidden":
                fields[name] = a.get("value", "")

        tb = re.search(r'tableId=(\d+)', html)
        if not action:
            m = re.search(r'ActionForm"\)\.action\s*=\s*[^;]*?"([^"]*\.do[^"]*)"', html)
            action = m.group(1) if m else ""

        return {
            "kind": kind, "page": page, "action": action,
            "table_id": tb.group(1) if tb else "",
            "fields": fields, "html": html,
        }

    def list_courses(self, kind: str = "pre_elect", **over) -> dict:
        """拉可选课程列表。返回 {total, rows, page, action, table_id, xktype, window}。

        页面的 hidden 字段（xn/xq/lcid/nj/zydm…）是 JS 打开时从
        `getWsxkTimeRange` 灌进去的，静态 HTML 里全是空串，必须自己补。
        """
        from .. import kingo_xk as X

        info = self.xk_page(kind)
        xktype = int(info["fields"].get("xktype") or 0)
        win = self.check_xk_window(xktype) if xktype else {}
        info["xktype"] = xktype
        info["window"] = win
        if not info["table_id"]:
            info.update(total=None, rows=[])
            return info

        form = {
            "xktype": str(xktype) if xktype else "",
            "xh": win.get("xh", ""),
            "xn": win.get("xn", ""),
            "xq": win.get("xqM", ""),
            "nj": win.get("nj", ""),
            "zydm": win.get("zydm", ""),
            "yxbdm": win.get("yxbdm", ""),
            "lcid": win.get("lcid", ""),
            "sfbd": win.get("sfbd", "0") or "0",
            "djs": win.get("djs", "0") or "0",
            "kxkc": win.get("kxkc", "1") or "1",
            "xfsx": win.get("xfsx", "") or "",
            "mssx": win.get("mssx", "") or "",
            "kcfw": "zxbnj",
            "items": "",
            "initQry": "0",
        }
        form.update({k: str(v) for k, v in over.items() if v is not None})
        r = self.session.post(
            f"{self.prefix}/taglib/DataTable.jsp?tableId={info['table_id']}",
            data=form, referer=f"{self.prefix}{info['page']}")
        raw = r.content
        try:
            html = raw.decode("gbk")
        except UnicodeDecodeError:
            html = raw.decode("utf-8", "ignore")
        _, rows = X.parse_table(html)
        info.update(total=X.total_records(html), rows=rows, form=form, html=html)
        return info

    def submit_pre_elect(self, rows: list, action: str = "", page: str = "",
                         tj: str = "0") -> dict:
        """提交预选。rows 取自 list_courses()，只提交勾选的行。"""
        from .. import kingo_xk as X

        action = action or "STU_PreElectCourseAction.do?hidOption=saveData"
        items = X.build_items(rows, tj=tj)
        if not items:
            return {"ok": False, "message": "没有可提交的课程（items 为空）", "raw": ""}
        form = {"items": items}
        r = self.session.post(f"{self.prefix}/{action.lstrip('/')}", data=form,
                              referer=f"{self.prefix}{page or '/student/wsxk.yx.html'}",
                              headers={"X-Requested-With": "XMLHttpRequest"})
        raw = (r.text or "").strip()
        try:
            data = json.loads(raw)
        except ValueError:
            return {"ok": False, "message": raw[:200] or "响应非 JSON", "raw": raw}
        # 101 = 有错但部分成功，100 = 失败，其余按成功看
        status = str(data.get("status", ""))
        return {"ok": status not in ("100",), "status": status,
                "message": data.get("message", ""), "raw": raw, "items": items}

    def check_xk_window(self, xktype: int = 2) -> dict:
        """查选课时间窗。

        服务端返回 status=400 表示「当前时间不是有效的选课时间区段」，
        result 里仍带完整参数（qssj/jssj/lcid/nj/zy…），可用来判断何时开抢。
        """
        path = f"{self.prefix}/jw/common/getWsxkTimeRange.action?xktype={xktype}"
        info: dict = {"open": False, "raw_status": "", "message": ""}
        if self.session is None:
            self.open()
        try:
            r = self.session.get(path, referer=f"{self.prefix}/student/wsxk.zx.html")
            data = json.loads(r.text or "{}")
        except (NetError, ValueError) as e:
            info["message"] = f"取选课时间窗失败: {e}"
            return info
        info["raw_status"] = data.get("status", "")
        info["message"] = data.get("message", "")
        try:
            inner = json.loads(data.get("result") or "{}")
        except ValueError:
            inner = {}
        info.update(inner)
        info["open"] = data.get("status") == "200" and inner.get("isValidTimerange") == "1"
        return info

    def prepare(self, courses: list[dict]) -> None:
        if not self.logged_in and not self.login():
            return
        try:
            r = self.session.get(self.p("home"), referer="/hsjw/")
            if "凭证已失效" in r.text:
                self.log.warning("预热发现会话失效")
                self.logged_in = False
                return
            acts = self.discover(r.text)
            self.log.info(f"发现 {len(acts)} 个 action 入口，选课相关：")
            for a in acts:
                if any(k in a.lower() for k in ("xk", "xuan", "select", "kb")):
                    self.log.info(f"    {a}")
        except NetError as e:
            self.log.warning(f"主页抓取失败: {e}")

        need = [c for c in courses if not (c.get("xkid") or c.get("jxb_ids"))]
        if need:
            self._resolve_all(need)

    def _resolve_all(self, courses: list[dict]) -> None:
        """拉一次选课列表，把课程名匹配成 xkid。"""
        rows = self.fetch_course_list()
        if not rows:
            self.log.warning("课程列表为空/解析不到 —— 需要手工填 xkid，或先跑 import-curl 配 list_request")
            return
        self.log.info(f"课程列表 {len(rows)} 条，开始匹配 {len(courses)} 门目标课")
        for c in courses:
            from ..parse import match
            hit = match(rows, c)
            if not hit:
                self.log.warning(f"  未匹配：{c.get('name')} {c.get('teacher') or ''}")
                continue
            c["xkid"] = c.get("xkid") or hit["xkid"]
            left = (hit.get("seats") or {}).get("left")
            tail = f" 余量 {left}" if left is not None else ""
            self.log.ok(f"  命中：{c.get('name')} -> xkid={hit['xkid']}{tail}")

    def _send(self, url: str, form: dict, json_body, headers: dict, method: str = "POST"):
        if json_body is not None:
            return self.session.post(url, json_body=json_body, headers=headers)
        if (method or "").upper() == "GET":
            params = form or None
            return self.session.get(url, params=params, headers=headers)
        return self.session.post(url, data=form or {}, headers=headers)

    def fetch_course_list(self) -> list[dict]:
        """拉课程列表，统一成 engine/parse 认的格式。

        优先走青果 DataTable（各选课页真实列表）；拿不到再退回老模板。
        每项含：name / teacher / skbh / skbjmc / xf / xkid(=上课班级代码) / kch_id / status
        """
        from .. import kingo_xk as X
        from ..parse import parse as parse_rows

        kinds = [self.cfg.get("xk_kind") or "pre_elect"]
        if not self.cfg.get("xk_kind"):
            kinds += ["by_plan"]

        best: list[dict] = []
        for kind in kinds:
            try:
                info = self.list_courses(kind)
            except (NetError, ValueError) as e:
                self.log.warning(f"拉列表失败({kind}): {e}")
                continue
            rows = info.get("rows") or []
            if not rows:
                continue
            win = info.get("window") or {}
            self.log.info(f"列表来源 {kind}：{win.get('lcmc','')} 共 {len(rows)} 条")
            for r in rows:
                best.append({
                    "name": X.course_name(r),
                    "teacher": r.get("rkjs", ""),
                    "skbh": r.get("skbh", ""),
                    "skbjmc": r.get("skbjmc", ""),
                    "xf": r.get("xf", ""),
                    "xkid": X.class_code(r),
                    "kch_id": X.course_code(r),
                    "status": r.get("xk_status", ""),
                    "raw": r,
                })
            break

        if best:
            return best

        # 回退：老模板路径
        tpl = self.list_request or DEFAULT_LIST
        url, form, json_body, headers = self._render_tpl(tpl, {})
        headers.setdefault("Referer", self.base_url + self.p("xsxk"))
        try:
            r = self._send(url, form, json_body, headers, tpl.get("method", "POST"))
        except NetError as e:
            self.log.warning(f"拉课程列表失败: {e}")
            return []
        if "凭证已失效" in (r.text or ""):
            self.logged_in = False
            return []
        return parse_rows(r.text or "")

    def discover(self, html: str | None = None) -> list[str]:
        """从主页/框架页里提取所有 .action / .jsp 入口，供人工确认选课地址。"""
        if html is None:
            html = self.session.get(self.p("home")).text
        found = re.findall(r"[\"'](/hsjw/[A-Za-z0-9_./\-]+\.(?:action|jsp))[^\"']*[\"']", html)
        seen, out = set(), []
        for a in found:
            if a not in seen:
                seen.add(a)
                out.append(a)
        return out

    # ---------- 抢 ----------
    def grab(self, course: dict) -> GrabResult:
        t0 = time.time()
        with self.lock:
            if not self.logged_in and not self.login():
                return GrabResult(False, AUTH, "登录失败", course, 0, time.time() - t0)
            if not self.grab_request:
                if not getattr(self, "_tpl_hint", False):
                    self._tpl_hint = True
                    self.log.info("未配 grab_request，用内置默认模板（该 action 已探测存在）；"
                                  "若返回参数错误，跑 import-curl 覆盖")
            url, form, json_body, headers = self._render_tpl(
                self.grab_request or DEFAULT_GRAB, course)
            hd = dict(headers)
            hd.setdefault("X-Requested-With", "XMLHttpRequest")
            hd.setdefault("Referer", self.base_url + "/hsjw/")
            try:
                r = self._send(url, form, json_body, hd,
                               (self.grab_request or DEFAULT_GRAB).get("method", "POST"))
            except NetError as e:
                return GrabResult(False, ERR, f"网络异常: {e}", course, 0, time.time() - t0)

            raw = r.text or ""
            code = self._classify(raw, r.url)
            res = GrabResult(code in (OK, ENROLLED), code, raw.strip()[:200] or f"HTTP {r.status}",
                             course, 0, time.time() - t0, raw[:600])
            if code == OK:
                self.log.ok(f"抢到！{course.get('name','')} {raw.strip()[:60]}")
            elif code == AUTH:
                self.logged_in = False
            return res

    def _render_tpl(self, tpl: dict, course: dict):
        pool = dict(course)
        pool.setdefault("user", self.acct.get("user", ""))
        pool.setdefault("name", course.get("name", ""))
        pool.setdefault("kch_id", course.get("kch_id", ""))
        pool.setdefault("jxb_ids", course.get("jxb_ids", ""))
        pool.setdefault("xkid", course.get("xkid") or course.get("jxb_ids") or "")
        # 常见同义字段名，避免模板里 {jxbid}/{jxb_id}/{classid} 取不到值
        for alias in ("jxb_id", "jxbid", "classid", "class_id", "bjid", "kcid"):
            pool.setdefault(alias, pool["xkid"])

        def sub(v):
            if not isinstance(v, str):
                return v
            return re.sub(r"\{(\w+)\}", lambda m: str(pool.get(m.group(1), m.group(0))), v)

        url = sub(tpl.get("url") or "")
        form = {k: sub(v) for k, v in (tpl.get("form") or {}).items()}
        json_body = None
        if tpl.get("json"):
            def walk(node):
                if isinstance(node, dict):
                    return {k: walk(v) for k, v in node.items()}
                if isinstance(node, list):
                    return [walk(x) for x in node]
                return sub(node)
            json_body = walk(tpl["json"])
        headers = {k: sub(v) for k, v in (tpl.get("headers") or {}).items()}
        return url, form, json_body, headers

    def _classify(self, raw: str, url: str = "") -> str:
        cfg = self.cfg.get("words") or {}
        def has(key, default):
            return tuple(cfg.get(key) or default)
        if any(w in raw for w in has("ok", OK_WORDS)) and not any(
                w in raw for w in has("full", FULL_WORDS)):
            return OK
        if any(w in raw for w in has("enrolled", ENROLLED_WORDS)):
            return ENROLLED
        if any(w in raw for w in has("full", FULL_WORDS)):
            return FULL
        if any(w in raw for w in has("conflict", CONFLICT_WORDS)):
            return CONFLICT
        if "login.action" in (url or "") or any(w in raw for w in has("auth", AUTH_WORDS)):
            return AUTH
        return ERR
