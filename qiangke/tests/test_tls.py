# -*- coding: utf-8 -*-
"""离线回归：远程站点必须使用已验证的 HTTPS，Mock 本机 HTTP 保持可用。"""
import os
import ssl
import sys
from urllib.request import HTTPSHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qk.net import Session  # noqa: E402
from qk.adapters import KingoAdapter, ZfsoftAdapter  # noqa: E402


class Log:
    pass


def tls_context(session):
    return next(h._context for h in session.opener.handlers if isinstance(h, HTTPSHandler))


def rejects(func):
    try:
        func()
    except ValueError:
        return True
    return False


def main() -> int:
    default = Session(base_url="https://example.invalid")
    ctx = tls_context(default)
    mock = Session(base_url="http://127.0.0.1:8080", verify_tls=False)
    local_https = Session(base_url="https://localhost:8443", verify_tls=False)
    kingo = KingoAdapter({"name": "test"}, {}, Log())
    zfsoft = ZfsoftAdapter({"name": "test"}, {}, Log())
    checks = {
        "默认校验证书": ctx.verify_mode == ssl.CERT_REQUIRED,
        "默认校验主机名": ctx.check_hostname,
        "禁止远程 HTTPS 忽略证书": rejects(
            lambda: Session(base_url="https://example.invalid", verify_tls=False)),
        "禁止远程明文 HTTP": rejects(
            lambda: Session(base_url="http://example.invalid", verify_tls=True)),
        "本机 mock HTTP 可用": mock.base_url.startswith("http://127.0.0.1"),
        "不允许绝对 URL 绕过": rejects(
            lambda: mock.get("http://example.invalid")),
        "不允许不安全重定向": rejects(
            lambda: next(h for h in mock.opener.handlers
                         if type(h).__name__ == "_SecureRedirectHandler"
                        ).redirect_request(None, None, 302, "Found", {},
                                           "http://example.invalid")),
        "本机 mock HTTPS 可显式关闭验证": not tls_context(local_https).check_hostname,
        "青果适配器使用安全默认": tls_context(kingo.make_session(
            "https://example.invalid")).check_hostname,
        "正方适配器使用安全默认": tls_context(zfsoft.make_session(
            "https://example.invalid")).check_hostname,
    }
    for name, passed in checks.items():
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
