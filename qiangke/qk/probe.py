# -*- coding: utf-8 -*-
"""入口探测：确认教务地址与系统类型（华水 = 青果 KINGOSOFT）。

  python run.py probe
  python run.py probe --url https://jwmis.ncwu.edu.cn/hsjw/
  python run.py probe --write
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re

from .net import NetError, Session

CANDIDATES = [
    "https://jwmis.ncwu.edu.cn/hsjw/",
    "https://jwmis.ncwu.edu.cn/",
    "http://jwmis.ncwu.edu.cn/hsjw/",
]

SIGN = [
    ("kingo", ("kingosoft", "kingo", "/hsjw/", "cas/logon.action", "青果")),
    ("zfsoft", ("jwglxt", "正方", "csrftoken", "zzxkyzb", "login_slogin")),
    ("qzsoft", ("强智", "qzdatasoft", "logon.do", "xkbmservlet")),
    ("urp", ("urp", "jwweb")),
]


def _fingerprint(html: str, url: str = "") -> list[str]:
    low = (html or "").lower() + " " + (url or "").lower()
    return [n for n, ws in SIGN if any(w in low for w in ws)]


def probe_one(url: str, timeout: float = 6.0) -> dict:
    s = Session(base_url=url, timeout=timeout, retries=0, verify_tls=True)
    out = {"url": url, "ok": False, "status": 0, "final": "", "title": "",
           "sign": [], "note": ""}
    try:
        r = s.get("/", timeout=timeout)
    except NetError as e:
        out["note"] = str(e)[:90]
        return out
    out["status"] = r.status
    out["final"] = r.url
    out["ok"] = r.status in (200, 301, 302)
    m = re.search(r"<title>(.*?)</title>", r.text, re.S | re.I)
    out["title"] = (m.group(1).strip()[:50] if m else "")
    out["sign"] = _fingerprint(r.text, r.url)
    return out


def run(log, urls: list[str] | None = None) -> dict:
    targets = urls or CANDIDATES
    log.info(f"探测 {len(targets)} 个入口 …")
    rows = []
    with cf.ThreadPoolExecutor(max_workers=min(6, len(targets))) as ex:
        for res in ex.map(probe_one, targets):
            rows.append(res)
            log.info(f"{'OK ' if res['ok'] else '-- '} {res['url']:<44}{res['status']:<4}"
                     f"{(','.join(res['sign']) or '-'):<10}{res['title'][:26]} {res['note']}")

    live = [r for r in rows if r["ok"]]
    pick = None
    for want in ("kingo", "zfsoft", "qzsoft", "urp"):
        hit = next((r for r in live if want in r["sign"]), None)
        if hit:
            base = re.sub(r"^(https?://[^/]+).*$", r"\1", hit["final"])
            pick = {"base_url": base, "system": want, "entry": hit["final"],
                    "title": hit["title"]}
            break
    if pick:
        log.ok(f"选定：{pick['base_url']}  类型={pick['system']}  入口={pick['entry']}")
    else:
        log.warning("没探到可用入口，检查网络/VPN，或用 --url 指定浏览器里的真实地址")
    return {"targets": rows, "pick": pick}


def write_config(report: dict, path: str = "config.ncwu.json") -> bool:
    pick = report.get("pick")
    if not pick:
        return False
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    sysobj = cfg.setdefault("system", {})
    sysobj["type"] = pick["system"]
    sysobj["base_url"] = pick["base_url"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return True
