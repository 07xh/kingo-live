# -*- coding: utf-8 -*-
"""日志：控制台（带色）+ 落文件。时间戳到毫秒，格式串避开 strftime 不支持的 %f。"""
from __future__ import annotations

import logging
import os
import sys
import time

logging.addLevelName(25, "OK")

_COLORS = {"DEBUG": "\033[90m", "INFO": "\033[36m", "OK": "\033[32m",
           "WARNING": "\033[33m", "ERROR": "\033[31m", "CRITICAL": "\033[41m"}
_RESET = "\033[0m"


class _Fmt(logging.Formatter):
    def __init__(self, color: bool = False):
        super().__init__("%(asctime)s %(levelname)-5s %(message)s", "%H:%M:%S")
        self.default_msec_format = "%s.%03d"
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        if not self.color:
            return super().format(record)
        lvl = record.levelname
        old = record.levelname
        record.levelname = f"{_COLORS.get(lvl, '')}{lvl}{_RESET}"
        try:
            return super().format(record)
        finally:
            record.levelname = old


def build(name: str = "qk", log_dir: str = "logs", quiet: bool = False) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    fh = logging.FileHandler(
        os.path.join(log_dir, f"run-{time.strftime('%Y%m%d-%H%M%S')}.log"), encoding="utf-8")
    fh.setFormatter(_Fmt(False))
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    if not quiet:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(_Fmt(sys.stdout.isatty()))
        sh.setLevel(logging.DEBUG)
        logger.addHandler(sh)

    def ok(msg, *a, **kw):
        logger.log(25, msg, *a, **kw)

    logger.ok = ok  # type: ignore[attr-defined]
    return logger
