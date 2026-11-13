# -*- coding: utf-8 -*-
"""auto 命令验证：登录 → 自动解析 xkid → 回写配置。

  python tests/test_auto.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mock"))

import kingo_mock  # noqa: E402


def main() -> int:
    srv, port = kingo_mock.serve(0, full_first=0)
    base = f"http://127.0.0.1:{port}"
    cfg = {
        "log_dir": os.path.join(tempfile.gettempdir(), "qk-auto"),
        "system": {"type": "kingo", "base_url": base, "timeout": 3,
                   "captcha": {"mode": "none", "enabled": False}},
        "policy": {"max_seconds": 5},
        "accounts": [{"name": "A", "user": "20230001", "pass": "p@ss",
                      "courses": [{"name": "水工建筑物", "teacher": "张老师"},
                                  {"name": "大学英语"},
                                  {"name": "不存在的课"}]}],
    }
    path = os.path.join(tempfile.gettempdir(), "qk-auto-cfg.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    r = subprocess.run([sys.executable, "run.py", "auto", "-c", path, "--apply"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", timeout=90)
    out = (r.stdout or "") + (r.stderr or "")
    print(out[-1200:])

    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    got = {c["name"]: c.get("xkid") for c in saved["accounts"][0]["courses"]}
    srv.shutdown()

    checks = {
        "水工建筑物 -> XK001": got.get("水工建筑物") == "XK001",
        "大学英语 -> XK003": got.get("大学英语") == "XK003",
        "不存在的课 未误配": not got.get("不存在的课"),
        "命令退出码为1(有未匹配)": r.returncode == 1,
        "输出含解析统计": "解析成功 2/3" in out,
    }
    print("--- 断言 ---")
    fail = 0
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        fail += 0 if v else 1
    print("\nauto", "通过" if fail == 0 else f"失败 {fail} 项")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
