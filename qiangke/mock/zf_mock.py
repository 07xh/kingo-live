# -*- coding: utf-8 -*-
"""正方 jwglxt 最小仿真服务，用于端到端验证引擎逻辑（不依赖真实教务系统）。

行为：
  - 登录必须带 yhm/mm，成功后下发 JSESSIONID
  - 选课前 N 次返回「人数已满」，之后返回 flag=1「选课成功」
  - 无 cookie 访问选课接口会 302 回登录页（触发 AUTH 分支）
"""
from __future__ import annotations

import base64
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CSRF = "m0ck-csrftoken-9f2a"
MODULUS = base64.b64encode(bytes([0xC0]) * 256).decode()
EXPONENT = base64.b64encode(bytes([0x01, 0x00, 0x01])).decode()
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 900

LOGIN_HTML = f"""<html><head><title>统一身份认证</title></head><body>
<form id="frm"><input id="csrftoken" name="csrftoken" value="{CSRF}">
<input id="yhm" name="yhm"><input id="mm" name="mm" type="password">
<img id="captchaImg" src="/jwglxt/kaptcha?time=1">
<script>var publicKeyModulus="";</script></form></body></html>"""


class State:
    def __init__(self, full_first: int = 2):
        self.full_first = full_first
        self.grab_calls = 0
        self.logins = 0
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state = State()

    def log_message(self, *a):
        pass

    # ---------- helpers ----------
    def _send(self, code: int, body: bytes = b"", ctype: str = "text/html; charset=utf-8",
              headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _text(self, code: int, html: str, headers: dict | None = None):
        self._send(code, html.encode("utf-8"), "text/html; charset=utf-8", headers)

    def _authed(self) -> bool:
        return "JSESSIONID=live" in (self.headers.get("Cookie") or "")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "ignore")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    # ---------- routes ----------
    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._text(200, LOGIN_HTML)
        if p == "/jwglxt/xtgl/login_slogin.html":
            return self._text(200, LOGIN_HTML)
        if p == "/jwglxt/xtgl/login_getPublicKey.html":
            return self._send(200, json.dumps({"modulus": MODULUS, "exponent": EXPONENT}).encode(),
                              "application/json")
        if p == "/jwglxt/kaptcha":
            return self._send(200, JPEG, "image/jpeg")
        if p == "/jwglxt/xtgl/index_initMenu.html":
            return self._text(200, "<html><body>index_initMenu 欢迎 同学</body></html>")
        if p == "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html":
            if not self._authed():
                return self._text(302, "", {"Location": "/jwglxt/xtgl/login_slogin.html"})
            return self._text(200, "<html><body>选课页</body></html>")
        return self._text(404, "not found")

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/jwglxt/xtgl/login_slogin.html":
            f = self._body()
            if f.get("yhm") and f.get("mm"):
                with self.state.lock:
                    self.state.logins += 1
                return self._text(302, "", {
                    "Location": "/jwglxt/xtgl/index_initMenu.html",
                    "Set-Cookie": "JSESSIONID=live; Path=/",
                })
            return self._text(200, "<html><body>用户名或密码不正确</body></html>")

        if p == "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html":
            if not self._authed():
                return self._text(302, "", {"Location": "/jwglxt/xtgl/login_slogin.html"})
            f = self._body()
            if not f.get("jxb_ids") or not f.get("kch_id"):
                return self._send(200, json.dumps({"flag": "0", "msg": "参数缺失"}).encode(),
                                  "application/json")
            with self.state.lock:
                self.state.grab_calls += 1
                n = self.state.grab_calls
            if n <= self.state.full_first:
                out = {"flag": "0", "msg": "该教学班人数已满"}
            else:
                out = {"flag": "1", "msg": "选课成功"}
            return self._send(200, json.dumps(out, ensure_ascii=False).encode(),
                              "application/json; charset=utf-8")

        return self._text(404, "not found")


def serve(port: int = 0, full_first: int = 2):
    Handler.state = State(full_first)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


if __name__ == "__main__":
    s, port = serve(int(__import__("sys").argv[1]) if len(__import__("sys").argv) > 1 else 8899)
    print(f"mock 正方教务已启动 http://127.0.0.1:{port}   Ctrl+C 退出")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
