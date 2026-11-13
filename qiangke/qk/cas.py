# -*- coding: utf-8 -*-
"""统一身份认证（金智 LTAP / authserver）CAS 登录。

华水真实登录链路（HAR 实证）：
  1. GET  https://authserver.ncwu.edu.cn/authserver/login?service=<jwmis caslogin>
  2. 页面里取 hidden execution
  3. POST 同一 URL：username / password(RSA-1024 hex) / execution / encrypted=true
     / _eventId=submit / loginType=1
  4. 302 → https://jwmis.ncwu.edu.cn/hsjw/caslogin?ticket=ST-xxx
  5. GET ticket → 青果会话建立

RSA 是 ohdave/RSAUtils 那套：零填充、128 字节分块、小端、输出小写 hex 左补零到 256。
已用 HAR 里真实密文验证：encrypt("Lsh20070113@") 与浏览器产生的值逐字节一致。
"""
from __future__ import annotations

import re

from .net import NetError, Session

DEFAULT_EXP = "010001"
DEFAULT_MOD = (
    "008aed7e057fe8f14c73550b0e6467b023616ddc8fa91846d2613cdb7f7621e3cada4cd5d812d627af6b87727ade4e26"
    "d26208b7326815941492b2204c3167ab2d53df1e3a2c9153bdb7c8c2e968df97a5e7e01cc410f92c4c2c2fba529b3ee98"
    "8ebc1fca99ff5119e036d732c368acf8beba01aa2fdafa45b21e4de4928d0d403"
)

FAIL_WORDS = ("用户名或密码", "密码错误", "用户名不存在", "账号不存在", "您提供的用户名",
              "密码有误", "认证失败", "登录失败")


def rsa_encrypt(pwd: str, exp_hex: str = DEFAULT_EXP, mod_hex: str = DEFAULT_MOD,
                chunk: int = 128) -> str:
    n = int(mod_hex, 16)
    e = int(exp_hex, 16)
    b = pwd.encode("utf-8")
    if len(b) > chunk:
        raise ValueError("密码过长，超出单块")
    b = b + b"\x00" * ((-len(b)) % chunk)
    out = []
    for i in range(0, len(b), chunk):
        block = int.from_bytes(b[i:i + chunk], "little")
        out.append(f"{pow(block, e, n):0256x}")
    return "".join(out)


class CasAuth:
    def __init__(self, session: Session, cfg: dict, log):
        c = (cfg or {}).get("auth") or {}
        self.s = session
        self.log = log
        self.base = (c.get("base") or "https://authserver.ncwu.edu.cn").rstrip("/")
        self.service = c.get("service") or "https://jwmis.ncwu.edu.cn/hsjw/caslogin"
        self.login_path = c.get("login_page") or "/authserver/login"
        self.js_path = c.get("login_js") or "/authserver/themes/sudy_ncwu/js/login.js?t=1"
        self.exp = c.get("rsa_exponent") or ""
        self.mod = c.get("rsa_modulus") or ""
        self.last_error = ""

    @property
    def login_url(self) -> str:
        from urllib.parse import quote
        return f"{self.base}{self.login_path}?service={quote(self.service, safe='')}"

    def _load_key(self) -> None:
        if self.exp and self.mod:
            return
        try:
            js = self.s.get(self.js_path).text
            m = re.search(r'getKeyPair\("([0-9a-fA-F]+)",\s*[\'"]{2},\s*"([0-9a-fA-F]+)"', js)
            if m:
                self.exp, self.mod = m.group(1), m.group(2)
                self.log.info(f"CA 公钥已加载：e={self.exp} n={len(self.mod)}hex")
                return
        except NetError as e:
            self.log.warning(f"取 CA 公钥失败，用内置值: {e}")
        self.exp, self.mod = DEFAULT_EXP, DEFAULT_MOD

    def login(self, user: str, pwd: str, captcha: str = "") -> bool:
        self._load_key()
        try:
            page = self.s.get(self.login_url, referer=self.base + "/")
        except NetError as e:
            self.last_error = f"登录页打不开: {e}"
            return False
        html = page.text or ""
        m = re.search(r'name="execution"\s+value="([^"]+)"', html)
        if not m:
            self.last_error = "登录页里没找到 execution，页面结构可能变了"
            return False
        execution = m.group(1)
        need_code = 'name="captcha"' in html or 'id="captcha"' in html

        data = {
            "username": user,
            "password": rsa_encrypt(pwd, self.exp, self.mod),
            "execution": execution,
            "encrypted": "true",
            "_eventId": "submit",
            "loginType": "1",
        }
        if captcha:
            data["captcha"] = captcha
        try:
            r = self.s.post(self.login_url, data=data, referer=self.login_url)
        except NetError as e:
            self.last_error = f"提交失败: {e}"
            return False

        final = r.url or ""
        body = r.text or ""
        # 成功只有一个判据：拿到了 ticket 或落到 jwmis；停在 authserver 一律算失败
        if "ticket=ST-" in final and "jwmis" in final:
            self.log.ok(f"CAS 认证通过，拿到 ticket，落地 {final[:90]}")
            return True
        if "jwmis" in final and "authserver" not in final:
            self.log.ok(f"CAS 回跳成功 → {final[:90]}")
            return True
        self.last_error = self._pick_error(body) or f"仍停在认证页（status={r.status}）"
        return False

    @staticmethod
    def _pick_error(html: str) -> str:
        for pat in (r'id="msg"[^>]*>([^<]{2,80})<', r'class="auth_error"[^>]*>([^<]{2,80})<',
                    r'<div[^>]*error[^>]*>([^<]{2,80})<', r'"message"\s*:\s*"([^"]{2,80})"'):
            m = re.search(pat, html, re.I)
            if m and m.group(1).strip():
                return m.group(1).strip()
        for w in FAIL_WORDS:
            if w in html:
                return w
        return ""
