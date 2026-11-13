# -*- coding: utf-8 -*-
"""cURL → 请求模板。从浏览器 F12「Copy as cURL」直接生成选课请求模板。

用法：
    python run.py import-curl --file curl.txt            # 写入 config 的 grab_request
    python run.py import-curl --file curl.txt --dry      # 只看解析结果
"""
from __future__ import annotations

import json
import re
import shlex

# 会被自动参数化的课程标识字段
COURSE_KEYS = ("xkid", "jxb_id", "jxb_ids", "jxbid", "kch_id", "kchid", "kcid",
               "classid", "class_id", "bjid", "id", "kcbm", "kcdm")

SKIP_HEADERS = {"content-length", "host", "connection", "accept-encoding", "cookie",
                "origin", "referer", "user-agent"}


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def parse(curl: str) -> dict:
    """解析单条 cURL（支持 -H / --data / --data-raw / --data-binary / -X / --compressed）。"""
    curl = curl.replace("\\\r\n", " ").replace("\\\n", " ").strip()
    curl = re.sub(r"^\s*curl\s+", "", curl, flags=re.I)
    try:
        parts = shlex.split(curl)
    except ValueError:
        parts = curl.split()

    url = ""
    method = ""
    headers: dict[str, str] = {}
    body = ""
    i = 0
    while i < len(parts):
        p = parts[i]
        if p in ("-H", "--header"):
            i += 1
            raw = _strip_quotes(parts[i])
            if ":" in raw:
                k, v = raw.split(":", 1)
                headers[k.strip()] = v.strip()
        elif p in ("--data", "--data-raw", "--data-binary", "--data-ascii", "-d"):
            i += 1
            body = _strip_quotes(parts[i])
        elif p in ("-X", "--request"):
            i += 1
            method = _strip_quotes(parts[i]).upper()
        elif p in ("--compressed", "-s", "-k", "-i", "--insecure"):
            pass
        elif p.startswith("http://") or p.startswith("https://") or p.startswith("/"):
            if not url:
                url = _strip_quotes(p)
        i += 1

    if not method:
        method = "POST" if body else "GET"

    form: dict[str, str] = {}
    json_body = None
    if body:
        if body.lstrip().startswith("{"):
            try:
                json_body = json.loads(body)
            except json.JSONDecodeError:
                json_body = None
        if json_body is None:
            for kv in body.split("&"):
                if not kv:
                    continue
                k, _, v = kv.partition("=")
                from urllib.parse import unquote_plus
                form[unquote_plus(k)] = unquote_plus(v)

    return {"method": method, "url": url, "headers": headers,
            "form": form, "json": json_body, "body_raw": body}


def to_template(req: dict) -> dict:
    """把请求里的课程标识值替换成占位符，得到可复用的抢课模板。"""
    out = {"method": req["method"], "url": req["url"], "headers": {}, "form": {}, "json": None}

    for k, v in (req.get("headers") or {}).items():
        if k.lower() in SKIP_HEADERS:
            continue
        out["headers"][k] = v

    marks = []
    for k, v in (req.get("form") or {}).items():
        lk = k.lower()
        if lk in COURSE_KEYS or (any(t in lk for t in ("xkid", "jxb", "kch", "kcid", "classid"))
                                 and str(v).isdigit()):
            out["form"][k] = "{" + k + "}"
            marks.append(f"{k}={v}")
        else:
            out["form"][k] = v

    if req.get("json"):
        def walk(node):
            if isinstance(node, dict):
                return {k: ("{" + k + "}" if k.lower() in COURSE_KEYS else walk(v))
                        for k, v in node.items()}
            if isinstance(node, list):
                return [walk(x) for x in node]
            return node
        out["json"] = walk(req["json"])
    return out, marks


def summarize(req: dict) -> str:
    u = req["url"]
    host = re.sub(r"^https?://[^/]+", "", u)
    return f"{req['method']} {host}  form={len(req.get('form') or {})}项"


if __name__ == "__main__":
    import sys
    print(json.dumps(to_template(parse(open(sys.argv[1], encoding="utf-8").read())),
                     ensure_ascii=False, indent=2))
