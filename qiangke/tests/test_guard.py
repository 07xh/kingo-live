# -*- coding: utf-8 -*-
"""凭据熔断验证：402/输错次数/锁定 一旦命中，必须一次就停，不能重试。

  python tests/test_guard.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from qk.adapters.kingo import (CredentialBlocked, KingoAdapter, build_logon_body,  # noqa: E402
                               build_logon_params)


class _Log:
    def __init__(self):
        self.lines = []

    def _p(self, lvl, m):
        self.lines.append(f"{lvl} {m}")

    def info(self, m, *a, **k):
        self._p("INFO", m)

    def ok(self, m, *a, **k):
        self._p("OK", m)

    def warning(self, m, *a, **k):
        self._p("WARN", m)

    def error(self, m, *a, **k):
        self._p("ERR", m)

    def debug(self, m, *a, **k):
        pass


def main() -> int:
    log = _Log()
    opts = {"max_fail_tries": 3, "login_retry": 2}
    ad = KingoAdapter({"name": "T", "user": "u", "pass": "p"}, opts, log)

    cases = [
        ("402", "您24小时内已输错1次密码，若超过5次，您的账号将被锁定！|1", True),
        ("402", "您24小时内已输错3次密码，若超过5次，您的账号将被锁定！|3", True),
        ("402", "账号已被锁定", True),
        ("500", "密码错误", True),
        ("500", "用户名或密码不正确", True),
        ("505", "需要输入验证码", False),
        ("200", "", False),
    ]
    checks = {}
    for status, msg, should_block in cases:
        try:
            ad._guard_credential(status, msg)
            blocked = False
        except CredentialBlocked:
            blocked = True
        checks[f"{status} {msg[:18] or '(空)'}"] = (blocked == should_block)

    # login() 遇到熔断后不得再发请求
    ad2 = KingoAdapter({"name": "T", "user": "u", "pass": "p"}, opts, log)
    ad2.session = None
    calls = {"n": 0}

    def fake_once(attempt=0):
        calls["n"] += 1
        raise CredentialBlocked("模拟：输错 4 次")

    ad2._login_once = fake_once
    assert ad2.login() is False
    checks["熔断后只尝试1次"] = (calls["n"] == 1)
    checks["熔断标记置位"] = getattr(ad2, "blocked", False) is True

    # 再次调用直接拒绝，不发请求
    calls["n"] = 0
    ad2.login()
    checks["已熔断不再发请求"] = (calls["n"] == 0)

    # 无验证码时参数名应为 _u / _p（与浏览器一致，不再塞 0000）
    plain = build_logon_params("u1", "p@ss", "S1", "", False)
    body = build_logon_body("u1", "p@ss", "S1", "S2", "12345678901234567890123",
                            "2025-01-01 00:00:00", "", False)
    checks["参数名退化为_u/_p"] = plain.startswith("_u=") and "&_p=" in plain
    checks["_p 为32位md5"] = bool(__import__("re").search(r"&_p=([0-9a-f]{32})&", plain))
    checks["不再出现_u0000"] = ("_u0000" not in plain) and ("_u0000" not in body)

    # 占位符账号：一次请求都不许发
    ad3 = KingoAdapter({"name": "P", "user": "2023xxxxxx", "pass": "教务密码"}, opts, log)
    sent = {"n": 0}

    def boom(attempt=0):
        sent["n"] += 1
        raise AssertionError("不该发请求")

    ad3._login_once = boom
    checks["占位符被拦下"] = (ad3.login() is False) and sent["n"] == 0
    checks["占位符置熔断"] = getattr(ad3, "blocked", False) is True

    # 空密码
    ad4 = KingoAdapter({"name": "E", "user": "20230001", "pass": ""}, opts, log)
    ad4._login_once = boom
    checks["空密码被拦下"] = ad4.login() is False

    print("--- 断言 ---")
    fail = 0
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        fail += 0 if v else 1
    print("\nGuard", "通过" if fail == 0 else f"失败 {fail} 项")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
