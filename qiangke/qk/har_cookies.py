# -*- coding: utf-8 -*-
"""从浏览器 HAR 抓包还原登录会话 cookie，写成 Netscape cookie 罐。

用途：账号密码已经打过好几次、不想再冒锁号风险时，直接把浏览器里
**已登录**的那次抓包（HAR）里的 JSESSIONID / CASTGC 喂给工具，
之后所有命令都走会话复用，0 次密码请求。

只认目标域，只取每个 cookie 最后一次出现的值（后出现的覆盖前面的）。
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import time
from urllib.parse import urlparse

DEFAULT_DOMAINS = ("jwmis.ncwu.edu.cn", "authserver.ncwu.edu.cn")


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _allowed(host: str, domains) -> bool:
    """允许目标域及其父域（CASTGC 常写在 .ncwu.edu.cn 上）。"""
    for d in domains:
        d = d.lstrip(".")
        if host == d or host.endswith("." + d) or d.endswith("." + host):
            return True
    return False


def _parse_set_cookie(raw: str, host: str):
    """Set-Cookie -> (name, value, domain, domain_specified, path, secure)。"""
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    if not parts or "=" not in parts[0]:
        return None
    name, value = parts[0].split("=", 1)
    domain, path, secure, specified = host, "/", False, False
    for p in parts[1:]:
        k, _, v = p.partition("=")
        k = k.strip().lower()
        if k == "domain" and v.strip():
            domain = v.strip().lstrip(".")
            domain = "." + domain if "." in domain else domain
            specified = True
        elif k == "path" and v.strip():
            path = v.strip()
        elif k == "secure":
            secure = True
    return name.strip(), value.strip(), domain, specified, path, secure


def extract(har_path: str, domains=DEFAULT_DOMAINS) -> list[dict]:
    """按抓包时序回放 cookie，返回最终生效的一组。"""
    with open(har_path, "r", encoding="utf-8-sig") as f:
        har = json.load(f)
    entries = har.get("log", {}).get("entries", []) or []
    state: dict[tuple, dict] = {}

    def put(name, value, domain, specified, path, secure):
        if not name or not _allowed(domain.lstrip("."), domains):
            return
        # 同名 cookie 只留最终值：避免 request 头与 Set-Cookie 各存一条，
        # 请求时拼出重复的同名 Cookie。
        state[(name,)] = {
            "name": name, "value": value, "domain": domain,
            "domain_specified": specified, "path": path, "secure": secure,
        }

    for e in entries:
        req = e.get("request", {}) or {}
        rsp = e.get("response", {}) or {}
        # 响应先于下一次请求：同一条 entry 里 request 是「之前的」，response 是「之后的」
        rh = _host(req.get("url", ""))
        for h in req.get("headers", []) or []:
            if (h.get("name") or "").lower() != "cookie":
                continue
            for kv in (h.get("value") or "").split(";"):
                if "=" in kv:
                    n, v = kv.strip().split("=", 1)
                    put(n, v, rh, False, "/", True)
        for h in rsp.get("headers", []) or []:
            if (h.get("name") or "").lower() != "set-cookie":
                continue
            parsed = _parse_set_cookie(h.get("value") or "", rh)
            if parsed:
                put(*parsed)
    return list(state.values())


def write_jar(cookies: list[dict], out_path: str) -> int:
    """写成 MozillaCookieJar 能直接 load 的 Netscape 格式。"""
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    jar = http.cookiejar.MozillaCookieJar(out_path)
    expires = int(time.time()) + 30 * 86400
    for c in cookies:
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=c["name"], value=c["value"], port=None,
            port_specified=False, domain=c["domain"],
            domain_specified=c["domain_specified"],
            domain_initial_dot=c["domain"].startswith("."),
            path=c["path"], path_specified=True, secure=c["secure"],
            expires=expires, discard=False, comment=None, comment_url=None,
            rest={}, rfc2109=False,
        ))
    jar.save(ignore_discard=True, ignore_expires=True)
    return len(cookies)


def is_netscape(path: str) -> bool:
    """区分浏览器导出的 cookies.txt 与 JSON 格式的 HAR。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(512)
    except OSError:
        return False
    return "\t" in head and not head.lstrip().startswith("{")


def extract_netscape(path: str, domains=DEFAULT_DOMAINS) -> list[dict]:
    """读浏览器插件导出的 Netscape cookies.txt，过滤到目标域。"""
    jar = http.cookiejar.MozillaCookieJar(path)
    jar.load(ignore_discard=True, ignore_expires=True)
    out = []
    for c in jar:
        if not _allowed(c.domain.lstrip("."), domains):
            continue
        out.append({
            "name": c.name, "value": c.value, "domain": c.domain,
            "domain_specified": c.domain_specified, "path": c.path or "/",
            "secure": c.secure,
        })
    return out


def describe(cookies: list[dict]) -> list[str]:
    """只列域/路径/名字，不吐 cookie 值。"""
    return [f"{c['domain']}{c['path']} {c['name']}" for c in cookies]
