# -*- coding: utf-8 -*-
"""适配器基类与结果结构。"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from ..net import Session

# 结果码
OK = "success"        # 本次提交成功
ENROLLED = "enrolled"  # 之前已选上
FULL = "full"         # 容量满 / 无余量
CONFLICT = "conflict" # 时间冲突 / 学分超限
AUTH = "auth"         # 登录态失效，需要重登
UNKNOWN = "unknown"  # 提交结果不确定；核验前禁止自动重发
ERR = "error"         # 其他失败（网络、参数）


@dataclass
class GrabResult:
    ok: bool
    code: str
    msg: str
    course: dict = field(default_factory=dict)
    attempts: int = 0
    elapsed: float = 0.0
    raw: str = ""

    def __repr__(self) -> str:
        return f"<{self.code} {self.course.get('name','')} {self.msg[:40]!r} x{self.attempts}>"


class Adapter:
    """一个适配器实例 = 一个学生账号。非线程安全，同账号内用 self.lock 串行。"""

    name = "base"

    def __init__(self, acct: dict, opts: dict, log):
        self.acct = acct or {}
        self.opts = opts or {}
        self.log = log
        self.session = None
        self.lock = threading.RLock()
        self.logged_in = False

    def make_session(self, base_url: str) -> Session:
        """统一两个教务适配器的连接策略；远程站点默认校验证书。"""
        return Session(
            base_url=base_url,
            timeout=float(self.opts.get("timeout", 8)),
            retries=int(self.opts.get("retries", 1)),
            headers=self.opts.get("headers") or {},
            verify_tls=self.opts.get("verify_tls", True),
            logger=self.log,
        )

    # --- 生命周期 ---
    def open(self) -> None:
        raise NotImplementedError

    def login(self) -> bool:
        raise NotImplementedError

    def prepare(self, courses: list[dict]) -> None:
        """开抢前预热：拉课程列表、解析 jxb_ids 等。默认无动作。"""

    def grab(self, course: dict) -> GrabResult:
        raise NotImplementedError

    def relogin(self) -> bool:
        with self.lock:
            self.logged_in = False
            try:
                return self.login()
            except Exception as e:
                self.log.warning(f"重登失败: {e}")
                return False

