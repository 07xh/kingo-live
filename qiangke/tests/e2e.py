# -*- coding: utf-8 -*-
"""正方只读安全回归：mock 登录 → 单次物理提交 → 待官方核验。

  python tests/e2e.py
退出码 0 = 通过。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mock"))

import zf_mock                       # noqa: E402
from qk import log as qlog           # noqa: E402
from qk.engine import Engine         # noqa: E402


def build_cfg(base: str) -> dict:
    return {
        "log_dir": os.path.join(tempfile.gettempdir(), "qk-e2e-logs"),
        "system": {
            "type": "zfsoft",
            "base_url": base,
            "timeout": 3,
            "retries": 1,
            "verify_tls": False,
            "password_mode": "rsa",
            "captcha": {"mode": "none", "enabled": False},
            "semester": {"year": "2025", "term": "3"},
        },
        "policy": {
            "start_at": "",
            "interval_ms": 100,
            "poll_ms": 100,
            "jitter": 0.0,
            "max_seconds": 20,
        },
        "accounts": [{
            "name": "测试生",
            "user": "20230001",
            "pass": "p@ss",
            "courses": [{"name": "水工建筑物", "teacher": "张老师",
                         "kch_id": "B0123", "jxb_ids": "202500123456", "resolve": "manual"}],
        }],
        "notify": {},
    }


def main() -> int:
    srv, port = zf_mock.serve(0, full_first=2)
    base = f"http://127.0.0.1:{port}"
    log = qlog.build("e2e", os.path.join(tempfile.gettempdir(), "qk-e2e-logs"))
    log.info(f"mock 启动于 {base}")

    eng = Engine(build_cfg(base), log)
    res = eng.run()
    main_submission_count = zf_mock.Handler.state.grab_calls

    ok = [r for r in res if r["code"] in ("unknown",)]
    print("\n--- 结果 ---")
    for r in res:
        print(f"  {r['course']:<14}{r['code']:<10}x{r['attempts']:<3}{r['msg'][:30]}")

    # 分支二：即使服务端报告 AUTH，已发出提交的结果仍未知，且应标记会话失效
    from qk.adapters import ZfsoftAdapter
    from qk.captcha import CaptchaSolver
    import qk.probe as probe_mod

    cfg = build_cfg(base)
    ad = ZfsoftAdapter(dict(cfg["accounts"][0]), cfg["system"], log,
                       solver=CaptchaSolver({"mode": "none"}, log))
    ad.open()
    ad.login()
    ad.session.jar.clear()
    r_auth = ad.grab(cfg["accounts"][0]["courses"][0])
    print(f"  会话失效分支 -> {r_auth.code} {r_auth.msg[:30]}")

    # 分支三：入口探测
    rep = probe_mod.run(log, [base])
    pick = rep.get("pick") or {}

    checks = {
        "收到回执仍为待核验": bool(ok),
        "主流程每目标只提交一次": len(ok) == 1 and ok[0]["attempts"] == 1,
        "登录成功": zf_mock.Handler.state.logins >= 1,
        "主流程没有重复提交": main_submission_count == 1,
        "会话失效的提交待核验": r_auth.code == "unknown" and not ad.logged_in,
        "探测识别正方": pick.get("system") == "zfsoft",
        "状态文件落盘": os.path.isdir(os.path.join(tempfile.gettempdir(), "qk-e2e-logs")),
    }
    srv.shutdown()
    print("\n--- 断言 ---")
    fail = 0
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        fail += 0 if v else 1
    print("\nE2E", "通过" if fail == 0 else f"失败 {fail} 项")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

