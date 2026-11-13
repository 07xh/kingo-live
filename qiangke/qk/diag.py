# -*- coding: utf-8 -*-
"""只读诊断：把浏览器真实登录请求解开，与本工具生成的参数逐字段比对。

用法（拿到的是浏览器 F12 里 logon.action 那条 Copy as cURL）：
    python run.py diff-login --file login_curl.txt

不登录、不发请求，纯粹本地解密比对，不消耗错误次数。
"""
from __future__ import annotations

import base64
import json
import re
from collections import OrderedDict

from . import curl2req
from .adapters.kingo import build_logon_params
from .kingo_des import str_dec

MASK_KEYS = ("_p", "password", "mm")


def _kv(raw: str) -> OrderedDict:
    out = OrderedDict()
    for pair in (raw or "").split("&"):
        if not pair:
            continue
        k, _, v = pair.partition("=")
        out[k] = v
    return out


def _mask(key: str, val: str) -> str:
    if any(key.startswith(m) for m in MASK_KEYS):
        return val[:2] + "*" * max(0, len(val) - 2) if val else ""
    return val


def diff(curl_text: str, user: str, pwd: str) -> dict:
    req = curl2req.parse(curl_text)
    body = _kv(req.get("body_raw") or "")
    report = {"url": req.get("url"), "ok": False, "rows": [], "notes": []}

    if "params" not in body or "deskey" not in body:
        report["notes"].append("这不是 logon.action 的登录请求（缺 params/deskey 字段）")
        return report

    try:
        raw = base64.b64decode(body["params"]).decode("utf-8", "ignore")
        their = _kv(str_dec(raw, body["deskey"]).rstrip("\x00"))
    except Exception as e:
        report["notes"].append(f"解密失败：{e}（deskey 可能是另一次会话的）")
        return report

    # 用他们的明文反推会话号，再生成我们这边的参数
    sid = ""
    for k, v in their.items():
        if k.startswith("_u"):
            try:
                dec = base64.b64decode(v).decode("utf-8", "ignore")
                sid = dec.partition(";;")[2]
            except Exception:
                pass
            break
    ours = _kv(build_logon_params(user, pwd, sid, their.get("randnumber", ""),
                                  bool(their.get("hid_flag") == "")))

    keys = list(OrderedDict.fromkeys(list(their) + list(ours)))
    same = 0
    for k in keys:
        a, b = their.get(k, "<缺失>"), ours.get(k, "<缺失>")
        ok = a == b
        same += 1 if ok else 0
        report["rows"].append({
            "field": k,
            "browser": _mask(k, str(a)),
            "ours": _mask(k, str(b)),
            "ok": ok,
        })
    report["ok"] = all(r["ok"] for r in report["rows"])
    report["same"] = same
    report["total"] = len(keys)
    report["sessionid"] = sid
    for extra in ("token", "timestamp", "ssessionid"):
        if extra not in body:
            report["notes"].append(f"浏览器 body 里没有 {extra}")
    return report


def render(report: dict) -> str:
    lines = [f"请求：{report.get('url')}", ""]
    if not report.get("rows"):
        lines += ["  无有效字段可比对"] + [f"  ! {n}" for n in report.get("notes", [])]
        return "\n".join(lines)
    w = max(len(r["field"]) for r in report["rows"]) + 2
    lines.append(f"  {'字段':<{w}}{'浏览器':<26}{'本工具':<26}结果")
    lines.append("  " + "-" * (w + 56))
    for r in report["rows"]:
        mark = "ok" if r["ok"] else "差异 <<<"
        lines.append(f"  {r['field']:<{w}}{str(r['browser'])[:24]:<26}"
                     f"{str(r['ours'])[:24]:<26}{mark}")
    lines.append("")
    lines.append(f"  一致 {report['same']}/{report['total']}")
    if report["ok"]:
        lines.append("  结论：参数完全一致 → 差异不在请求构造，问题在账号/密码本身")
    else:
        bad = [r["field"] for r in report["rows"] if not r["ok"]]
        lines.append(f"  结论：以下字段不一致 → {', '.join(bad)}")
    for n in report.get("notes", []):
        lines.append(f"  ! {n}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    txt = open(sys.argv[1], encoding="utf-8").read()
    print(render(diff(txt, sys.argv[2], sys.argv[3])))
