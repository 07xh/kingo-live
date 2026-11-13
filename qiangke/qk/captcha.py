# -*- coding: utf-8 -*-
"""验证码：手工输入 / 外部命令 OCR / 打码平台 HTTP 接口。

config.json -> captcha:
  {"mode": "manual"}
  {"mode": "cmd",  "cmd": "tesseract {img} stdout --psm 7"}
  {"mode": "http", "url": "http://api.ttshitu.com/predict", "method": "POST",
   "image_field": "img", "image_encode": "base64",
   "extra": {"username": "xx", "password": "yy", "typeid": "3"},
   "result_path": "data.result"}
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time


class CaptchaSolver:
    def __init__(self, cfg: dict | None, log, img_dir: str = "logs"):
        self.cfg = cfg or {}
        self.mode = (self.cfg.get("mode") or "none").lower()
        self.log = log
        self.img_dir = img_dir
        os.makedirs(img_dir, exist_ok=True)
        self._sess = None

    # ---------- 对外 ----------
    def solve(self, img: bytes, hint: str = "") -> str | None:
        if not img:
            return None
        if self.mode in ("none", ""):
            return None
        if self.mode == "manual":
            return self._manual(img, hint)
        if self.mode == "cmd":
            return self._cmd(img)
        if self.mode == "http":
            return self._http(img)
        raise ValueError(f"未知验证码模式: {self.mode}")

    # ---------- 各模式 ----------
    def _save(self, img: bytes) -> str:
        path = os.path.abspath(os.path.join(
            self.img_dir, f"captcha-{time.strftime('%H%M%S')}-{int(time.time()*1000)%1000}.png"))
        with open(path, "wb") as f:
            f.write(img)
        return path

    def _manual(self, img: bytes, hint: str) -> str | None:
        path = self._save(img)
        print(f"\n[验证码] 已保存: {path}  {hint}")
        try:
            code = input("  输入验证码（回车跳过）: ").strip()
        except EOFError:
            code = ""
        return code or None

    def _cmd(self, img: bytes) -> str | None:
        path = self._save(img)
        cmd = self.cfg.get("cmd", "").replace("{img}", path)
        try:
            out = subprocess.run(cmd, shell=True, capture_output=True, timeout=20)
            code = (out.stdout or b"").decode("utf-8", "ignore").strip().replace(" ", "")
            code = "".join(ch for ch in code if ch.isalnum())[:8]
            if not code:
                self.log.warning(f"OCR 无输出: {out.stderr[:200]!r}")
            return code or None
        except Exception as e:
            self.log.warning(f"OCR 命令失败: {e}")
            return None

    def _http(self, img: bytes) -> str | None:
        from .net import Session
        if self._sess is None:
            self._sess = Session(timeout=15, retries=2)
        payload = dict(self.cfg.get("extra") or {})
        enc = (self.cfg.get("image_encode") or "base64").lower()
        b64 = base64.b64encode(img).decode()
        if enc == "base64":
            payload[self.cfg.get("image_field", "image")] = b64
        elif enc == "dataurl":
            payload[self.cfg.get("image_field", "image")] = "data:image/png;base64," + b64
        else:
            payload[self.cfg.get("image_field", "image")] = b64

        method = (self.cfg.get("method") or "POST").upper()
        url = self.cfg["url"]
        if method == "POST" and (self.cfg.get("format") or "form") == "json":
            r = self._sess.post(url, json_body=payload)
        elif method == "POST":
            r = self._sess.post(url, data=payload)
        else:
            r = self._sess.get(url, params=payload)

        try:
            data = json.loads(r.text)
        except Exception:
            self.log.warning(f"打码平台返回非 JSON: {r.text[:200]}")
            return None

        node = data
        for key in (self.cfg.get("result_path") or "data.result").split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
                node = node[int(key)]
            else:
                self.log.warning(f"打码结果字段缺失: {self.cfg.get('result_path')}, 原文 {data}")
                return None
        code = str(node).strip()
        self.log.info(f"打码结果: {code}")
        return code or None
