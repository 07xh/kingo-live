# -*- coding: utf-8 -*-
"""抢课引擎：对时等待 → 并发抢 → 退避重试 → 汇总。

并发模型：一个账号内每门课一个线程，但提交动作被账号级锁串行化，
避免同一账号并发打选课接口被风控/会话串号；账号之间真并发。
"""
from __future__ import annotations

import json
import sqlite3
import os
import random
import threading
import time
from datetime import datetime
from pathlib import Path
import sys

# Legacy CLI runs from qiangke/, while the shared readback contract lives at
# repository root. No network code or credentials are imported.
_root = Path(__file__).resolve().parents[2]
if (_root / "contracts" / "enrollment_evidence.py").is_file() and str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
from contracts.enrollment_evidence import pending_receipt, VerificationState

from .adapters import AUTH, CONFLICT, ENROLLED, ERR, FULL, OK, UNKNOWN, make
from .captcha import CaptchaSolver
from .intent_journal import IntentJournal
from .notify import Notifier


class Engine:
    def __init__(self, cfg: dict, log):
        self.cfg = cfg
        self.log = log
        self.stop = threading.Event()
        self.results: list[dict] = []
        self._lock = threading.Lock()
        self.opts = cfg.get("system") or {}
        self.policy = cfg.get("policy") or {}
        self.solver = CaptchaSolver(self.opts.get("captcha"),
                                    log, img_dir=cfg.get("log_dir", "logs"))
        self.notifier = Notifier(cfg.get("notify") or {}, log)

    # ---------------- 主入口 ----------------
    def run(self) -> list[dict]:
        accounts = self.cfg.get("accounts") or []
        if not accounts:
            raise SystemExit("配置里没有 accounts")

        adapters = []
        kind = self.opts.get("type") or "kingo"
        for acct in accounts:
            courses = acct.get("courses") or self.cfg.get("courses") or []
            acct = dict(acct)
            acct["courses"] = courses
            ad = make(kind, acct, self.opts, self.log, solver=self.solver)
            adapters.append(ad)

        # 登录 + 预热（并行）
        self.log.info("STAGE 登录 / 预热")
        threads = []
        for ad in adapters:
            t = threading.Thread(target=self._boot, args=(ad,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        if all(not ad.logged_in for ad in adapters):
            self.log.error("全部账号登录失败，退出")
            return self._dump()

        self._wait_start(adapters)

        self.log.info("STAGE 开抢")
        jobs = []
        for ad in adapters:
            if not ad.logged_in:
                self.log.error(f"账号 {ad.acct.get('name')} 未登录，跳过")
                continue
            for c in ad.acct["courses"]:
                t = threading.Thread(target=self._worker, args=(ad, c), daemon=True)
                jobs.append(t)
        for t in jobs:
            t.start()
        for t in jobs:
            t.join(timeout=float(self.policy.get("max_seconds", 1800)) + 60)
        return self._dump()

    # ---------------- 内部 ----------------
    def _boot(self, ad):
        try:
            ad.open()
            ad.login()
            if ad.logged_in:
                ad.prepare(ad.acct["courses"])
        except Exception as e:
            self.log.error(f"[{ad.acct.get('name')}] 初始化异常: {e}")

    def _target_ts(self, adapters) -> float | None:
        start_at = self.policy.get("start_at") or self.cfg.get("start_at")
        if not start_at:
            return None
        try:
            dt = datetime.strptime(str(start_at), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            self.log.warning(f"start_at 格式应为 'YYYY-MM-DD HH:MM:SS'，收到 {start_at}，立即开始")
            return None
        base = dt.timestamp()
        # 用各账号服务器偏移的平均值校正
        offs = [a.session.time_offset for a in adapters if a.session]
        off = sum(offs) / len(offs) if offs else 0.0
        return base - off

    def _wait_start(self, adapters):
        target = self._target_ts(adapters)
        if target is None:
            return
        lead = float(self.policy.get("lead_ms", 120)) / 1000.0  # 提前量：抵消首包 RTT
        target -= lead
        self.log.info(f"等待开抢：目标本地时刻 {datetime.fromtimestamp(target):%H:%M:%S.%f}"[:-3]
                      + f"（提前 {lead*1000:.0f}ms 发首包）")
        while True:
            left = target - time.time()
            if left <= 0:
                break
            if left > 1.0:
                time.sleep(min(left - 0.5, 5.0))
            elif left > 0.05:
                time.sleep(left - 0.02)
            else:
                pass  # 最后 20ms 忙等，保证触发精度
        self.log.ok("开抢")

    def _intent_term(self) -> str:
        """Canonicalize semester values before reserving a physical write."""
        value = self.opts.get("xnxq") or self.cfg.get("xnxq") or self.opts.get("semester")
        if isinstance(value, dict):
            year, term = value.get("year"), value.get("term")
            if any(v is None or isinstance(v, bool) for v in (year, term)):
                raise ValueError("学期必须包含学年和学期编号")
            if not str(year).strip() or not str(term).strip():
                raise ValueError("学期编号不能为空")
            return f"{str(year).strip()}/{str(term).strip()}"
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ValueError("缺少明确学期标识")

    def _worker(self, ad, course):
        """At most one real write for a durable account/term/course intent.

        This older CLI does not have a verified official per-course readback.
        Therefore any acknowledgement is UNKNOWN, never a confirmed success.
        Even a FULL or AUTH response cannot authorize an automatic replay.
        """
        label = f"[{ad.acct.get('name')}/{course.get('name') or course.get('kch_id')}]"
        if getattr(ad, "blocked", False):
            self.log.error(f"{label} 登录凭据被锁定，未发送选课提交")
            return
        db_path = (os.environ.get("KINGO_INTENT_DB")
                   or os.path.join(os.path.expanduser("~"),
                                   ".kingo-assistant", "intent-journal.sqlite3"))
        try:
            if not isinstance(course, dict) or not (
                    str(course.get("xkid") or "").strip()
                    or str(course.get("jxb_ids") or "").strip()):
                raise ValueError("qiangke 需要明确教学班号，不能只凭课程名称提交")
            journal = IntentJournal(db_path)
            reserved = journal.reserve(
                school=str(self.opts.get("base_url") or "unknown"),
                account=str(ad.acct.get("user") or ""),
                term=self._intent_term(),
                course=course,
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.log.error(f"{label} 本地意图记录不可用，禁止提交：{type(exc).__name__}")
            with self._lock:
                self.results.append({"account": ad.acct.get("name") or "unknown",
                                     "course": course.get("name") or "",
                                     "code": UNKNOWN, "msg": "本地意图保护不可用",
                                     "attempts": 0, "elapsed": 0.0,
                                     "at": datetime.now().strftime("%H:%M:%S.%f")[:-3]})
            return
        if not reserved:
            self.log.warning(f"{label} 发现历史提交意图，未重发；请先人工核对官方已选课程")
            with self._lock:
                self.results.append({"account": ad.acct.get("name") or "unknown",
                                     "course": course.get("name") or "",
                                     "code": UNKNOWN, "msg": "已有待核验提交意图，跨重启阻止重复写入",
                                     "attempts": 0, "elapsed": 0.0,
                                     "at": datetime.now().strftime("%H:%M:%S.%f")[:-3]})
            return
        started = time.perf_counter()
        try:
            r = ad.grab(course)
        except Exception as exc:
            self.log.error(f"{label} 提交结果不可确认：{type(exc).__name__}")
            r = None
        code = getattr(r, "code", UNKNOWN)
        if code in (OK, ENROLLED, ERR):
            code = UNKNOWN
        # Shared four-client result contract: a transport receipt is pending,
        # never SUCCESS. Preserve named business diagnostics for troubleshooting
        # but never turn them into official enrollment confirmation.
        verdict = pending_receipt()
        if verdict.state is not VerificationState.PENDING:
            raise RuntimeError("统一提交回执状态异常")
        msg = ("请求已发出，待官方逐课程核验；禁止自动重发"
               if code == UNKNOWN else f"收到服务器诊断：{code}；实际选课结果待核验")
        with self._lock:
            self.results.append({
                "account": ad.acct.get("name") or ad.acct.get("user"),
                "course": course.get("name") or course.get("kch_id"),
                "code": code, "msg": msg, "attempts": 1,
                "elapsed": round(time.perf_counter() - started, 3),
                "at": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            })
        self.log.warning(f"{label} 本次物理提交结束（{code}），结果不可自动重发；官方记录待核对")

    # ---------------- 汇总 ----------------
    def _dump(self) -> list[dict]:
        ok = [r for r in self.results if r["code"] in (OK, ENROLLED)]
        self.log.info(f"STAGE 汇总：{len(ok)} 门成功 / 共记录 {len(self.results)} 条")
        path = os.path.join(self.cfg.get("log_dir", "logs"),
                            f"status-{time.strftime('%Y%m%d-%H%M%S')}.json")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ok": ok, "all": self.results}, f, ensure_ascii=False, indent=2)
        self.log.info(f"结果写入 {path}")
        return self.results

