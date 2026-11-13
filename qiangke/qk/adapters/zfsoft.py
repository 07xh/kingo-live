# -*- coding: utf-8 -*-
"""正方教务管理系统（jwglxt）适配器 —— 华北水利水电大学教务系统。

流程对齐正方 2019+ 版本：
  1. GET  login_slogin.html            取 csrftoken（cookie CSRFToken / 页面隐藏域）
  2. GET  login_getPublicKey.html      取 RSA modulus / exponent（base64 或 hex）
  3. GET  kaptcha                      验证码（按需）
  4. POST login_slogin.html            csrftoken + yhm + mm(密文) + yzm
  5. GET  zzxkyzb_cxZzxkYzbIndex.html  进入选课页建立上下文
  6. POST zzxkyzb_cxZzxkYzbDisplay     拉可选课程（可选，解析出 jxb_ids）
  7. POST zzxkyzbjk_xkBcZyZzxkYzb      提交选课 —— 抢课就是打这个包
"""
from __future__ import annotations

import base64
import re
import time
from urllib.parse import urlparse

from ..net import NetError
from ..rsa_crypt import encrypt, parse_keys
from .base import AUTH, CONFLICT, ENROLLED, ERR, FULL, OK, Adapter, GrabResult

DEFAULTS = {
    "login_page": "/jwglxt/xtgl/login_slogin.html",
    "pubkey": "/jwglxt/xtgl/login_getPublicKey.html",
    "captcha": "/jwglxt/kaptcha",
    "course_index": "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html?gnmkdm=N253512&layout=default",
    "course_list": "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbDisplay.html?gnmkdm=N253512",
    "grab": "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html?gnmkdm=N253512",
}

FAIL_WORDS = ("用户名或密码不正确", "密码错误", "用户不存在", "账号不存在", "验证码错误", "验证码不正确")
AUTH_WORDS = ("login_slogin", "会话已失效", "请重新登录", "未登录")
OK_WORDS = ("选课成功", "已选上", "操作成功")
ENROLLED_WORDS = ("已选", "已经选过", "重复选课", "已存在")
FULL_WORDS = ("人数已满", "容量已满", "余量为0", "余量为 0", "已满", "无余量", "选课人数已满")
CONFLICT_WORDS = ("时间冲突", "课程冲突", "冲突", "学分超", "超出学分", "门数超")


class ZfsoftAdapter(Adapter):
    name = "zfsoft"

    def __init__(self, acct: dict, opts: dict, log, solver=None):
        super().__init__(acct, opts, log)
        self.cfg = opts or {}
        self.paths = dict(DEFAULTS)
        self.paths.update(self.cfg.get("paths") or {})
        self.base_url = (self.cfg.get("base_url") or "https://jwgl.ncwu.edu.cn").rstrip("/")
        self.solver = solver
        self.semester = self.cfg.get("semester") or {}
        self.xkxnm = str(self.semester.get("year") or "")
        self.xkxqm = str(self.semester.get("term") or "")
        self.grab_fields = self.cfg.get("grab_fields") or {}
        self.xnm = self.cfg.get("xnm") or "N253512"
        self._pubkey_cache = None

    # ---------------- 基础 ----------------
    def p(self, key: str) -> str:
        return self.paths[key]

    def open(self) -> None:
        self.session = self.make_session(self.base_url)
        t0 = time.time()
        try:
            self.session.get("/", timeout=8)
            self.log.info(f"对时完成：服务器-本地 = {self.session.time_offset:+.3f}s "
                          f"（RTT {time.time()-t0:.2f}s）")
        except NetError as e:
            self.log.warning(f"对时失败（继续）: {e}")

    # ---------------- 登录 ----------------
    def login(self) -> bool:
        with self.lock:
            for attempt in range(int(self.cfg.get("login_retry", 3))):
                try:
                    if self._login_once():
                        self.logged_in = True
                        self.log.ok(f"[{self.acct.get('name','')}] 登录成功")
                        return True
                except NetError as e:
                    self.log.warning(f"登录网络异常(第{attempt+1}次): {e}")
                time.sleep(0.6)
            self.log.error(f"[{self.acct.get('name','')}] 登录失败")
            return False

    def _login_once(self) -> bool:
        s = self.session
        page = s.get(self.p("login_page"))
        html = page.text
        csrf = self._csrftoken(html)

        yzm = ""
        if self._need_captcha(html):
            img = s.get(self.p("captcha"), params={"time": f"{time.time():.6f}"})
            if img.status != 200 or len(img.content) < 100:
                self.log.warning(f"验证码获取异常 status={img.status} len={len(img.content)}")
            yzm = (self.solver.solve(img.content, "正方验证码") if self.solver else "") or ""
            if not yzm:
                self.log.warning("验证码为空，本次跳过")
                return False

        mm = self._password_field(html)
        data = {
            "csrftoken": csrf,
            "yhm": self.acct.get("user", ""),
            "mm": mm,
            "language": "zh_CN",
        }
        if yzm:
            data[self.cfg.get("captcha_field", "yzm")] = yzm

        r = s.post(self.p("login_page"), data=data, referer=self.p("login_page"))
        text = r.text
        if any(w in text for w in FAIL_WORDS):
            hit = next(w for w in FAIL_WORDS if w in text)
            self.log.warning(f"登录被拒: {hit}")
            return False
        if "index_initMenu" in r.url or "index_initMenu" in text or "欢迎" in text:
            return True
        # 有的部署直接 302 到学生首页
        if "xsxk" in r.url or "xtgl/index" in r.url:
            return True
        self.log.warning(f"登录结果不确定，落点 {r.url}，页面长度 {len(text)}")
        return False

    def _csrftoken(self, html: str) -> str:
        m = re.search(r'id="csrftoken"\s+value="([^"]+)"', html) or \
            re.search(r'name="csrftoken"\s+value="([^"]+)"', html)
        if m:
            return m.group(1)
        return self.session.cookie("CSRFToken") or ""

    def _need_captcha(self, html: str) -> bool:
        mode = self.cfg.get("captcha", {}).get("enabled", "auto")
        if mode is True:
            return True
        if mode is False:
            return False
        return ("kaptcha" in html) or ("yzm" in html)

    def _password_field(self, html: str) -> str:
        pwd = self.acct.get("pass", "")
        mode = (self.cfg.get("password_mode") or "auto").lower()
        if mode == "plain":
            return pwd
        keys = self._pubkey(html)
        if keys:
            mod, exp = keys
            try:
                return encrypt(pwd, mod, exp)
            except Exception as e:
                self.log.warning(f"RSA 加密失败，回退明文: {e}")
        return pwd

    def _pubkey(self, html: str):
        if self._pubkey_cache is not None:
            return self._pubkey_cache or None
        mod = exp = None
        try:
            r = self.session.get(self.p("pubkey"), params={"time": int(time.time() * 1000)},
                                 referer=self.p("login_page"))
            if r.status == 200 and r.text.strip().startswith("{"):
                d = r.json()
                mod = d.get("modulus") or d.get("publicKeyModulus")
                exp = d.get("exponent") or d.get("publicKeyExponent")
        except Exception:
            pass
        if not mod:
            hm, he = parse_keys(html)
            mod, exp = hm, he
        if mod and exp:
            self._pubkey_cache = (self._to_hex(mod), self._to_hex(exp))
        else:
            self._pubkey_cache = ()
        return self._pubkey_cache

    @staticmethod
    def _to_hex(v: str) -> str:
        v = (v or "").strip()
        try:
            bytes.fromhex(v)
            return v
        except ValueError:
            pass
        try:
            return base64.b64decode(v + "=" * (-len(v) % 4)).hex()
        except Exception:
            return v

    # ---------------- 预热 ----------------
    def prepare(self, courses: list[dict]) -> None:
        if not self.logged_in:
            self.login()
        # 进入选课页，拿上下文 cookie
        try:
            r = self.session.get(self.p("course_index"), referer="/jwglxt/xtgl/index_initMenu.html")
            self.log.debug(f"选课页 {r.status} len={len(r.text)}")
        except NetError as e:
            self.log.warning(f"选课页打开失败: {e}")
        for c in courses:
            if c.get("jxb_ids"):
                continue
            if (c.get("resolve") or "manual") == "auto":
                self._resolve_course(c)

    def _resolve_course(self, course: dict) -> None:
        """按课程名/教师去可选列表里找 jxb_ids。失败则留给手工填。"""
        name = course.get("name") or ""
        teacher = course.get("teacher") or ""
        data = {
            "xkkh": "", "xqlb": "", "xkzy": "", "txbs": "", "mobile": "",
            "p_address": "", "p_name": "",
            "kch_id": course.get("kch_id") or "",
            "xkxnm": self.xkxnm, "xkxqm": self.xkxqm,
            "kklxdm": course.get("kklxdm") or "",
        }
        try:
            r = self.session.post(self.p("course_list"), data=data,
                                  referer=self.p("course_index"),
                                  headers={"X-Requested-With": "XMLHttpRequest"})
        except NetError as e:
            self.log.warning(f"课程列表请求失败: {e}")
            return
        rows = self._parse_rows(r.text)
        if not rows:
            self.log.warning(f"未能解析课程列表（{name}）——请手工填 jxb_ids，见 README 抓包步骤")
            return
        for row in rows:
            if name and name not in row["text"]:
                continue
            if teacher and teacher not in row["text"]:
                continue
            course["jxb_ids"] = row["jxb_ids"]
            course["kch_id"] = course.get("kch_id") or row["kch_id"]
            self.log.ok(f"命中课程 {name} -> jxb_ids={row['jxb_ids']}")
            return
        self.log.warning(f"列表里没有匹配 {name} {teacher}，{len(rows)} 条候选")

    @staticmethod
    def _parse_rows(text: str) -> list[dict]:
        rows = []
        # JSON 形式
        try:
            import json
            d = json.loads(text)
            items = d.get("items") or d.get("rows") or d.get("data") or []
            for it in items:
                if not isinstance(it, dict):
                    continue
                jxb = str(it.get("jxb_id") or it.get("jxb_ids") or it.get("do_jxb_id") or "")
                if not jxb:
                    continue
                txt = " ".join(str(v) for v in it.values())
                rows.append({"jxb_ids": jxb, "kch_id": str(it.get("kch_id") or ""), "text": txt})
            if rows:
                return rows
        except Exception:
            pass
        # HTML 表格形式：一行里同时有 jxb_id 与课程信息
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
            m = re.search(r'id=["\']jxb_id[^"\']*["\'][^>]*value=["\']([^"\']+)["\']', tr) or \
                re.search(r'value=["\'](\d{6,})["\'][^>]*id=["\']jxb_id', tr)
            if not m:
                continue
            km = re.search(r'id=["\']kch_id[^"\']*["\'][^>]*value=["\']([^"\']+)["\']', tr)
            plain = re.sub(r"<[^>]+>", " ", tr)
            plain = re.sub(r"\s+", " ", plain).strip()
            rows.append({"jxb_ids": m.group(1), "kch_id": km.group(1) if km else "", "text": plain})
        return rows

    # ---------------- 抢 ----------------
    def grab(self, course: dict) -> GrabResult:
        t0 = time.time()
        with self.lock:
            if not self.logged_in and not self.login():
                return GrabResult(False, AUTH, "登录失败", course, 0, time.time() - t0)
            jxb = course.get("jxb_ids") or ""
            if not jxb:
                return GrabResult(False, ERR, "缺少 jxb_ids（未解析到，需手工填）",
                                  course, 0, time.time() - t0)
            data = {
                "jxb_ids": jxb,
                "kch_id": course.get("kch_id") or "",
                "txbs": course.get("txbs") or "",
                "xkxnm": course.get("xkxnm") or self.xkxnm,
                "xkxqm": course.get("xkxqm") or self.xkxqm,
                "xkty": course.get("xkty") or "",
                "rl": "0",
                "bklx_id": "0",
            }
            data.update(self.grab_fields)
            try:
                r = self.session.post(self.p("grab"), data=data,
                                      referer=self.p("course_index"),
                                      headers={"X-Requested-With": "XMLHttpRequest",
                                               "Accept": "application/json, text/javascript, */*; q=0.01"})
            except NetError as e:
                return GrabResult(False, ERR, f"网络异常: {e}", course, 0, time.time() - t0)

            raw = r.text or ""
            flag, msg = self._read_flag(raw)
            code = self._classify(raw, flag, r.url)
            res = GrabResult(code in (OK, ENROLLED), code, msg or raw[:120],
                             course, 0, time.time() - t0, raw[:400])
            if code == OK:
                self.log.ok(f"抢到！{course.get('name','')} {msg}")
            elif code == AUTH:
                self.logged_in = False
            return res

    @staticmethod
    def _read_flag(raw: str):
        m = re.search(r'"flag"\s*:\s*"?([^",}]+)"?', raw)
        flag = m.group(1) if m else ""
        mm = re.search(r'"msg"\s*:\s*"([^"]*)"', raw)
        msg = mm.group(1) if mm else ""
        return flag, msg

    @staticmethod
    def _classify(raw: str, flag: str, url: str = "") -> str:
        if flag == "1" or any(w in raw for w in OK_WORDS):
            return OK
        if any(w in raw for w in ENROLLED_WORDS):
            return ENROLLED
        if any(w in raw for w in FULL_WORDS):
            return FULL
        if any(w in raw for w in CONFLICT_WORDS):
            return CONFLICT
        if "login_slogin" in (url or "") or any(w in raw for w in AUTH_WORDS):
            return AUTH
        return ERR
