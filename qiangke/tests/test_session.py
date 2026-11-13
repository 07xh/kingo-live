# -*- coding: utf-8 -*-
"""会话持久化回归：cookie 落盘/恢复、失效判据、免重登、多账号隔离。

  python tests/test_session.py
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from qk.adapters.kingo import KingoAdapter, session_alive  # noqa: E402
from qk import har_cookies  # noqa: E402
from qk.net import Session  # noqa: E402


class _Log:
    def __init__(self):
        self.lines = []

    def _put(self, level, msg):
        self.lines.append((level, msg))

    def info(self, m): self._put("info", m)
    def ok(self, m): self._put("ok", m)
    def warning(self, m): self._put("warn", m)
    def error(self, m): self._put("err", m)


HOME_URL = "https://jwmis.ncwu.edu.cn/hsjw/frame/homes.action"
BIG = "<html><frame>menu</frame>" + ("学生选课系统 " * 120) + "</html>"
LOGIN_URL = "https://jwmis.ncwu.edu.cn/hsjw/cas/login.action"


class _Resp:
    def __init__(self, text=BIG, url=HOME_URL, status=200):
        self.text, self.url, self.status = text, url, status


class _FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.calls = 0

    def get(self, *a, **kw):
        self.calls += 1
        return self.resp


class _Handler(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        if self.path.startswith("/set"):
            self.send_response(200)
            self.send_header("Set-Cookie", "JSESSIONID=REAL123; Path=/; HttpOnly")
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            type(self).seen.append(self.headers.get("Cookie", ""))
            body = b"<html><frame>menu</frame>" + b"x" * 600 + b"</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a):
        pass


def main() -> int:
    checks = {}

    # --- 1. cookie 往返（含两个域、会话 cookie、secure） ---
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "session.cookies")
        s1 = Session(base_url="https://jwmis.ncwu.edu.cn", cookie_file=p)
        s1.set_cookie("JSESSIONID", "ABC123", path="/hsjw", domain="jwmis.ncwu.edu.cn")
        s1.set_cookie("CASTGC", "TGT-xyz", path="/authserver", domain="authserver.ncwu.edu.cn")
        checks["保存会话文件"] = s1.save_cookies() and os.path.isfile(p)

        s2 = Session(base_url="https://jwmis.ncwu.edu.cn", cookie_file=p)
        checks["恢复青果 JSESSIONID"] = s2.cookie("JSESSIONID") == "ABC123"
        checks["恢复 CAS CASTGC"] = s2.cookie("CASTGC") == "TGT-xyz"
        checks["无文件时静默跳过"] = not Session(
            base_url="https://jwmis.ncwu.edu.cn",
            cookie_file=os.path.join(d, "nope.cookies")).load_cookies()

        # --- 2. 失效判据 ---
        checks["有效主页判活"] = session_alive(BIG, HOME_URL, 200)
        checks["凭证失效判死"] = not session_alive("凭证已失效" + BIG, HOME_URL, 200)
        checks["302 到登录页判死"] = not session_alive(BIG, LOGIN_URL, 200)
        checks["authserver 判死"] = not session_alive(
            BIG, "https://authserver.ncwu.edu.cn/authserver/login", 200)
        checks["非 200 判死"] = not session_alive(BIG, HOME_URL, 302)
        checks["短页判死"] = not session_alive("login", HOME_URL, 200)

        # --- 2.5 真实 HTTP 往返：恢复的 cookie 必须真的随请求发出 ---
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{srv.server_port}"
            hp = os.path.join(d, "http.cookies")
            h1 = Session(base_url=base, cookie_file=hp)
            h1.get("/set")
            h1.save_cookies()
            _Handler.seen.clear()
            h2 = Session(base_url=base, cookie_file=hp)
            r = h2.get("/check")
            checks["HTTP 往返带上恢复的 cookie"] = (
                bool(_Handler.seen) and "JSESSIONID=REAL123" in _Handler.seen[-1])
            checks["恢复后主页仍可读"] = len(r.text) > 500 and session_alive(r.text, r.url, r.status)
        finally:
            srv.shutdown()
            srv.server_close()

        # --- 3. 复用会话时一次登录请求都不发 ---
        log = _Log()
        ad = KingoAdapter({"name": "A", "user": "202611014", "pass": ""},
                          {"session_file": p}, log)
        ad.cookie_file = p                    # 上面 s1 已把这个文件写出来
        ad.session = _FakeSession(_Resp())
        sent = {"n": 0}

        def boom(attempt=0):
            sent["n"] += 1
            raise AssertionError("不该发登录请求")

        ad._login_once = boom
        checks["复用会话跳过登录"] = ad.login() is True and sent["n"] == 0
        checks["复用后 logged_in"] = ad.logged_in is True
        checks["复用只探测主页"] = ad.session.calls == 1

        # --- 4. 会话过期 → 回落正常登录 ---
        ad2 = KingoAdapter({"name": "B", "user": "202611014", "pass": ""},
                           {"session_file": p}, log)
        ad2.cookie_file = p
        ad2.session = _FakeSession(_Resp(text="凭证已失效", url=LOGIN_URL))
        ad2._validate_acct = lambda: None
        ad2._login_once = lambda attempt=0: False
        checks["过期会话回落登录"] = ad2.login() is False

        # --- 5. --fresh 强制忽略已存会话 ---
        ad3 = KingoAdapter({"name": "C", "user": "202611014", "pass": ""},
                           {"session_file": p, "session_fresh": True}, log)
        ad3.cookie_file = p
        ad3.session = _FakeSession(_Resp())
        checks["fresh 不复用"] = ad3._reuse_session() is False

        # --- 6. 多账号 cookie 罐隔离 ---
        a = KingoAdapter({"user": "202611014"}, {}, log)
        b = KingoAdapter({"user": "202611102"}, {}, log)
        checks["多账号路径隔离"] = a.cookie_file != b.cookie_file
        checks["路径含学号"] = ("202611014" in os.path.basename(a.cookie_file)
                            and "202611102" in os.path.basename(b.cookie_file))
        checks["可显式关闭"] = KingoAdapter(
            {"user": "x"}, {"session_disabled": True}, log).cookie_file == ""

        # --- 7. 从 HAR / cookies.txt 恢复会话 ---
        mini = {"log": {"entries": [
            {"request": {"url": "https://authserver.ncwu.edu.cn/authserver/login",
                         "method": "POST", "headers": []},
             "response": {"headers": [{"name": "Set-Cookie",
                          "value": "CASTGC=TGT-1; Path=/authserver; Domain=.ncwu.edu.cn; HttpOnly"}]}},
            {"request": {"url": "https://jwmis.ncwu.edu.cn/hsjw/caslogin",
                         "method": "GET", "headers": []},
             "response": {"headers": [{"name": "Set-Cookie",
                          "value": "JSESSIONID=XYZ; Path=/hsjw; HttpOnly"}]}},
            {"request": {"url": "https://jwmis.ncwu.edu.cn/hsjw/frame/homes.action",
                         "method": "GET",
                         "headers": [{"name": "Cookie", "value": "JSESSIONID=XYZ"}]},
             "response": {"headers": []}},
        ]}}
        hpath = os.path.join(d, "mini.har")
        with open(hpath, "w", encoding="utf-8") as f:
            json.dump(mini, f)
        ck = har_cookies.extract(hpath)
        names = {c["name"] for c in ck}
        checks["HAR 提取 CAS + 青果 cookie"] = {"CASTGC", "JSESSIONID"} <= names
        checks["父域 .ncwu.edu.cn 未被过滤"] = any(
            c["name"] == "CASTGC" and c["domain"].startswith(".") for c in ck)
        checks["同名 cookie 不重复"] = len([c for c in ck if c["name"] == "JSESSIONID"]) == 1
        jpath = os.path.join(d, "from-har.cookies")
        har_cookies.write_jar(ck, jpath)
        s3 = Session(base_url="https://jwmis.ncwu.edu.cn", cookie_file=jpath)
        checks["HAR 导入后可直接加载"] = (s3.cookie("JSESSIONID") == "XYZ"
                                   and s3.cookie("CASTGC") == "TGT-1")
        checks["识别 cookies.txt 格式"] = har_cookies.is_netscape(jpath) \
            and not har_cookies.is_netscape(hpath)
        ck2 = har_cookies.extract_netscape(jpath)
        checks["cookies.txt 往返不丢项"] = {c["name"] for c in ck2} == names
        checks["描述不含 cookie 值"] = all("XYZ" not in x and "TGT-1" not in x
                                    for x in har_cookies.describe(ck))

    print("--- 断言 ---")
    fail = 0
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        fail += 0 if v else 1
    print(f"\nSession {'通过' if fail == 0 else f'失败 {fail} 项'}（{len(checks)} 项）")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
