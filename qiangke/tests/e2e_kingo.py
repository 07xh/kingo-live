# -*- coding: utf-8 -*-
"""青果链路端到端：登录加密 → 会话 → 满员重试 → 抢到 → 多账号并发。

  python tests/e2e_kingo.py     退出码 0 = 通过
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mock"))

import kingo_mock                                  # noqa: E402
from qk import log as qlog                         # noqa: E402
from qk.adapters import build_logon_body           # noqa: E402
from qk.engine import Engine                       # noqa: E402

GRAB = {
    "method": "POST",
    "url": "/hsjw/xsxk/xsxk!addXk.action",
    "headers": {"X-Requested-With": "XMLHttpRequest"},
    "form": {"xkid": "{xkid}", "type": "xkxk"},
}


def build_cfg(base: str) -> dict:
    return {
        "log_dir": os.path.join(tempfile.gettempdir(), "qk-kingo-e2e"),
        "system": {
            "type": "kingo",
            "base_url": base,
            "timeout": 3,
            "retries": 1,
            "verify_tls": False,
            "captcha": {"mode": "none", "enabled": False},
            "grab_request": GRAB,
        },
        "policy": {"start_at": "", "interval_ms": 60, "poll_ms": 60, "jitter": 0.0,
                   "max_seconds": 15},
        "accounts": [
            {"name": "号A", "user": "20230001", "pass": "p@ss",
             "courses": [{"name": "水工建筑物", "teacher": "张老师"}]},
            {"name": "号B", "user": "20230002", "pass": "p@ss",
             "courses": [{"name": "结构力学"}]},
        ],
        "notify": {},
    }


def main() -> int:
    srv, port = kingo_mock.serve(0, full_first=2)
    base = f"http://127.0.0.1:{port}"
    log = qlog.build("e2e_kingo", os.path.join(tempfile.gettempdir(), "qk-kingo-e2e"))
    log.info(f"mock 青果启动 {base}")

    eng = Engine(build_cfg(base), log)
    res = eng.run()
    ok = [r for r in res if r["code"] in ("unknown",)]

    # 固定输入下，body 应能通过服务端 token 校验（服务端自己算 md5 组合）
    st = kingo_mock.Handler.state
    body_demo = build_logon_body("20230001", "p@ss", kingo_mock.SESSION,
                                 kingo_mock.SESSION, kingo_mock.DESKEY, "2026-11-13 20:43:22")
    print("\n--- 登录 body 样例 ---")
    print(body_demo[:160] + " ...")
    print(f"\n--- 结果（{len(res)} 条记录）---")
    for r in res:
        print(f"  {r['account']:<6}{str(r['course']):<12}{r['code']:<10}x{r['attempts']}")

    accounts_ok = len({r["account"] for r in ok})
    checks = {
        "登录加密通过服务端校验": st.crypto_ok,
        "两账号均登录": st.logins >= 2,
        "两门均待官方核验": len(ok) == 2,
        "命中满员重试": any(r["attempts"] >= 2 for r in ok),
        "选课接口被打": st.grab_calls >= 4,
        "两账号分别保留待核验": accounts_ok == 2,
        "解密出的params含用户名": "MjAyMz" in st.last_params or "_u" in st.last_params,
    }
    srv.shutdown()
    print("\n--- 断言 ---")
    fail = 0
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        fail += 0 if v else 1
    print("\nE2E(青果)", "通过" if fail == 0 else f"失败 {fail} 项")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

