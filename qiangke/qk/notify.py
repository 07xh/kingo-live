# -*- coding: utf-8 -*-
"""通知：控制台 + 通用 webhook（钉钉/企业微信/Server酱 均可填其 URL）。"""
from __future__ import annotations

import json
import threading


class Notifier:
    def __init__(self, cfg: dict, log):
        self.cfg = cfg or {}
        self.log = log
        self.url = self.cfg.get("webhook") or ""
        self.kind = (self.cfg.get("kind") or "raw").lower()

    def hit(self, acct, course, res):
        self.log.ok(f"✅ 抢课成功 {acct.get('name','')} / {course.get('name','')} "
                    f"（{res.attempts} 次，{res.elapsed:.2f}s）")
        self._push(f"抢课成功：{acct.get('name','')} - {course.get('name','')} {res.msg}")

    def fail(self, acct, course, res):
        msg = res.msg if res else "超时未成功"
        self.log.warning(f"❌ 未成功 {acct.get('name','')} / {course.get('name','')}：{msg}")
        self._push(f"抢课失败：{acct.get('name','')} - {course.get('name','')} {msg}")

    def _push(self, text: str):
        if not self.url:
            return

        def go():
            from .net import Session
            try:
                s = Session(timeout=8, retries=1)
                if self.kind == "dingtalk":
                    body = {"msgtype": "text", "text": {"content": text}}
                elif self.kind == "wecom":
                    body = {"msgtype": "text", "text": {"content": text}}
                elif self.kind == "serverchan":
                    body = {"title": "抢课", "desp": text}
                else:
                    body = {"text": text}
                s.post(self.url, json_body=body)
            except Exception as e:
                self.log.debug(f"通知失败: {e}")

        threading.Thread(target=go, daemon=True).start()
