# -*- coding: utf-8 -*-
from .base import (AUTH, CONFLICT, ENROLLED, ERR, FULL, OK, UNKNOWN, Adapter, GrabResult)
from .kingo import KingoAdapter, build_logon_body
from .zfsoft import ZfsoftAdapter

ADAPTERS = {"kingo": KingoAdapter, "zfsoft": ZfsoftAdapter}


def make(kind: str, acct: dict, opts: dict, log, solver=None) -> Adapter:
    cls = ADAPTERS.get((kind or "").lower())
    if cls is None:
        raise ValueError(f"未知系统类型 {kind!r}，可选：{', '.join(ADAPTERS)}")
    return cls(acct, opts, log, solver=solver)


__all__ = ["Adapter", "GrabResult", "KingoAdapter", "ZfsoftAdapter", "build_logon_body",
           "make", "ADAPTERS", "OK", "ENROLLED", "FULL", "CONFLICT", "AUTH", "ERR", "UNKNOWN"]

