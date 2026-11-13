# -*- coding: utf-8 -*-
"""青果 KINGOSOFT 最小仿真服务 —— 端到端验证登录加密与抢课链路。

服务端行为与真实站点一致：
  - 下发 _sessionid / deskey / timestamp
  - 校验 POST /hsjw/cas/logon.action 的 params（DES 解密）+ token（md5 组合）
  - 登录成功后下发 JSESSIONID
  - 选课接口前 N 次返回「人数已满」，之后返回「选课成功」
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qk.kingo_des import str_dec  # noqa: E402

DESKEY = "96118179457380228129957"
SESSION = "MOCKSESSION0123456789ABCDEF"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 900

PAGE = f"""<html><head><title>KINGOSOFT高校智慧校园教学综合服务平台</title></head><body>
<script type="text/javascript">
var yzmbt = "0"; var _webRootPath = "/hsjw/";
var _sessionid = "{SESSION}"; var _ssessionid = "{SESSION}";
var schoolcode = "10078"; var modename = "kingo";
</script>
<form id="dosub" method="post" action=""><input type="hidden" id="txt_mm_expression" value="">
<input id="username" name="username"><input id="password" name="password" type="password">
<input type="text" name="randnumber" id="randnumber"></form>
<img id="randpic" src="/hsjw/cas/genValidateCode?v=1">
</body></html>"""


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


class State:
    def __init__(self, full_first: int = 2):
        self.full_first = full_first
        self.grab_calls = 0
        self.logins = 0
        self.crypto_ok = False
        self.last_params = ""
        self.pwd_mode_seen = ""
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state = State()

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _text(self, code, s, headers=None):
        self._send(code, s.encode("utf-8"), headers=headers)

    def _authed(self):
        return f"JSESSIONID={SESSION}" in (self.headers.get("Cookie") or "")

    def _raw_body(self) -> str:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode("utf-8", "ignore")

    @staticmethod
    def _parse_raw(body: str) -> dict:
        """严格按 & 与首个 = 切分；不把 + 当空格（浏览器原文即如此）。"""
        out = {}
        for kv in body.split("&"):
            if not kv:
                continue
            k, _, v = kv.partition("=")
            out[urllib.parse.unquote(k)] = urllib.parse.unquote(v)
        return out

    # ---------- GET ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if path in ("/", "/hsjw", "/hsjw/", "/hsjw/cas/login.action"):
            return self._text(200, PAGE)
        if path == "/hsjw/frame/homepage":
            m = (q.get("method") or [""])[0]
            if m == "getTempDeskey":
                return self._text(200, DESKEY)
            if m == "getTempNowtime":
                return self._text(200, "2026-11-13 20:43:22")
            return self._text(200, "")
        if path == "/hsjw/cas/genValidateCode":
            return self._send(200, JPEG, "image/jpeg")
        if path == "/hsjw/frame/homes.action":
            if not self._authed():
                return self._text(200, "<script>alert('温馨提示：凭证已失效，请重新登录!')"
                                       "window.top.location.href='/hsjw/';</script>")
            return self._text(200, """<html><frameset><frame src="/hsjw/frame/menu.jsp">
</frameset><a href="/hsjw/xsxk/xsxk.action">学生选课</a>
<a href="/hsjw/xsxk/xsxk!cxXkList.action">可选课程</a>
<a href="/hsjw/ksgl/ksgl.action">考试管理</a></html>""")
        return self._text(404, "not found")

    # ---------- POST ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/hsjw/cas/logon.action":
            body = self._raw_body()
            f = self._parse_raw(body)
            try:
                plain = str_dec(base64.b64decode(f["params"]).decode(), f["deskey"]).rstrip("\x00")
            except Exception as e:
                return self._text(200, json.dumps({"status": "500", "message": f"解密失败 {e}",
                                                   "result": ""}))
            want = _md5(_md5(plain) + _md5(f.get("timestamp", "")))
            with self.state.lock:
                self.state.crypto_ok = (want == f.get("token"))
                self.state.last_params = plain
                if self.state.crypto_ok:
                    self.state.logins += 1
            if not self.state.crypto_ok:
                return self._text(200, json.dumps({"status": "500", "message": "token 校验失败",
                                                   "result": ""}))
            kv = {}
            for pair in plain.split("&"):
                k, _, v = pair.partition("=")
                kv[k] = v
            ukey = next((k for k in kv if k.startswith("_u")), "")
            pkey = next((k for k in kv if k.startswith("_p")), "")
            ok = False
            if ukey and pkey:
                try:
                    dec = base64.b64decode(kv[ukey]).decode()
                    user, _, sid = dec.partition(";;")
                    pval = kv[pkey]
                    rand = kv.get("randnumber", "")
                    # 兼容两种客户端：明文（旧）/ 双 MD5（真机现行）
                    as_plain = pval == "p@ss"
                    as_md5 = pval == _md5(_md5("p@ss") + _md5(rand.lower()))
                    self.state.pwd_mode_seen = "plain" if as_plain else ("md5" if as_md5 else "?")
                    ok = bool(user) and sid == SESSION and (as_plain or as_md5)
                except Exception:
                    ok = False
            if ok:
                return self._text(200, json.dumps(
                    {"status": "200", "message": "", "result": "/hsjw/frame/homes.action"}),
                    {"Set-Cookie": f"JSESSIONID={SESSION}; Path=/"})
            return self._text(200, json.dumps({"status": "500", "message": "用户名或密码错误",
                                               "result": ""}))

        if path == "/hsjw/xsxk/xsxk!cxXkList.action":
            if not self._authed():
                return self._text(200, "<script>alert('凭证已失效')</script>")
            return self._text(200, """<html><body><table>
<tr><th>课程名称</th><th>教师</th><th>余量</th><th>操作</th></tr>
<tr><td>水工建筑物</td><td>张老师</td><td>余量 3/50</td>
    <td><a href="javascript:addXk('XK001')">选课</a></td></tr>
<tr><td>结构力学</td><td>李老师</td><td>余量 0/40</td>
    <td><a href="javascript:addXk('XK002')">选课</a></td></tr>
<tr><td>大学英语</td><td>王老师</td><td>余量 12/60</td>
    <td><a href="javascript:addXk('XK003')">选课</a></td></tr>
</table></body></html>""")

        if path == "/hsjw/xsxk/xsxk!addXk.action":
            if not self._authed():
                return self._text(200, "<script>alert('凭证已失效')</script>")
            f = self._parse_raw(self._raw_body())
            if not f.get("xkid"):
                return self._text(200, "参数缺失")
            with self.state.lock:
                self.state.grab_calls += 1
                n = self.state.grab_calls
            if n <= self.state.full_first:
                return self._text(200, "该教学班人数已满")
            return self._text(200, "选课成功")

        return self._text(404, "not found")


def serve(port: int = 0, full_first: int = 2):
    Handler.state = State(full_first)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8898
    s, p = serve(port)
    print(f"mock 青果教务已启动 http://127.0.0.1:{p}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
