# -*- coding: utf-8 -*-
"""抢课引擎：对时等待 → 并发抢 → 退避重试 → 汇总。

并发模型：一个账号内每门课一个线程，但提交动作被账号级锁串行化，
避免同一账号并发打选课接口被风控/会话串号；账号之间真并发。
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import datetime

from .adapters import AUTH, CONFLICT, ENROLLED, ERR, FULL, OK, UNKNOWN, make
from .captcha import CaptchaSolver
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

    def _worker(self, ad, course):
        if getattr(ad, "blocked", False):
            self.log.error(f"[{ad.acct.get('name')}] 凭据熔断，跳过 {course.get('name')}")
            return
        label = f"[{ad.acct.get('name')}/{course.get('name') or course.get('kch_id')}]"
        interval = float(self.policy.get("interval_ms", 300)) / 1000.0
        poll = float(self.policy.get("poll_ms", 1200)) / 1000.0
        jitter = float(self.policy.get("jitter", 0.3))
        backoff_cap = float(self.policy.get("backoff_cap_ms", 3000)) / 1000.0
        deadline = time.time() + float(self.policy.get("max_seconds", 1800))
        attempt = 0
        sleep_s = interval

        while not self.stop.is_set() and time.time() < deadline:
            attempt += 1
            r = ad.grab(course)
            r.attempts = attempt
            with self._lock:
                self.results.append({
                    "account": ad.acct.get("name") or ad.acct.get("user"),
                    "course": course.get("name") or course.get("kch_id"),
                    "code": r.code, "msg": r.msg, "attempts": attempt,
                    "elapsed": round(r.elapsed, 3),
                    "at": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                })

            if r.code in (OK, ENROLLED):
                self.notifier.hit(ad.acct, course, r)
                return
            if r.code == UNKNOWN:
                self.log.warning(f"{label} 结果待核验，停止自动重发；请查官方已选列表")
                return
            if r.code == CONFLICT:
                self.log.warning(f"{label} 冲突/超限，停止重试：{r.msg}")
                self.notifier.fail(ad.acct, course, r)
                return
            if r.code == AUTH:
                self.log.warning(f"{label} 会话失效，重登")
                if not ad.relogin():
                    time.sleep(1.0)
                continue
            if r.code == FULL:
                sleep_s = poll
                if attempt % 10 == 1:
                    self.log.info(f"{label} 满员，继续蹲（第 {attempt} 次）")
            else:
                sleep_s = min(sleep_s * 1.5, backoff_cap)
                if attempt % 5 == 1:
                    self.log.info(f"{label} {r.code} {r.msg[:60]}（第 {attempt} 次）")
            time.sleep(sleep_s * (1 + random.uniform(-jitter, jitter)))

        self.log.warning(f"{label} 结束，共尝试 {attempt} 次未成功")
        self.notifier.fail(ad.acct, course, None)

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

