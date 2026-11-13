# -*- coding: utf-8 -*-
"""零依赖 HTTP 会话层：cookie 持久化 / gzip / 重试 / 服务器对时。"""
from __future__ import annotations

import gzip
import http.cookiejar
import io
import json as _json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class NetError(Exception):
    pass


@dataclass
class Resp:
    status: int
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    text: str = ""
    elapsed: float = 0.0

    def json(self) -> Any:
        try:
            return _json.loads(self.text)
        except Exception:
            # 正方等系统常返回单引号伪 JSON，做一次宽松修正
            import re
            t = self.text.strip()
            t = re.sub(r"([{,]\s*)'", r'\1"', t)
            t = re.sub(r"'\s*([,}])", r'"\1', t)
            return _json.loads(t)

    def __repr__(self) -> str:
        return f"<Resp {self.status} {len(self.content)}B {self.elapsed:.3f}s>"


class Session:
    """单账号会话。线程内独立使用；跨线程共享需自行加锁（本项目按线程拆分）。"""

    def __init__(self, base_url: str = "", timeout: float = 10.0, retries: int = 2,
                 headers: Optional[Dict[str, str]] = None, proxy: str = "",
                 verify_tls: bool = True, logger=None):
        self.base_url = base_url.rstrip("/")
        target = urllib.parse.urlsplit(self.base_url)
        local_only = target.hostname in ("localhost", "127.0.0.1", "::1")
        if target.scheme == "http" and not local_only:
            raise ValueError("远程教务连接必须使用 HTTPS")
        if not verify_tls and not local_only:
            raise ValueError("关闭 TLS 证书验证仅允许本机测试环境")
        self.timeout = timeout
        self.retries = retries
        self.log = logger
        self.jar = http.cookiejar.CookieJar()
        self.time_offset = 0.0  # 服务器时间 - 本地时间（秒）
        self.default_headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        if headers:
            self.default_headers.update(headers)
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        handlers = [urllib.request.HTTPCookieProcessor(self.jar),
                    urllib.request.HTTPSHandler(context=ctx)]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)
        self._lock = __import__("threading").RLock()

    # ---------- 基础请求 ----------
    def abs_url(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if not url.startswith("/"):
            url = "/" + url
        return self.base_url + url

    def request(self, method: str, url: str, params: Optional[dict] = None,
                data: Optional[Union[dict, bytes, str]] = None, json_body: Any = None,
                headers: Optional[Dict[str, str]] = None, referer: str = "",
                allow_redirects: bool = True, timeout: Optional[float] = None) -> Resp:
        url = self.abs_url(url)
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode(params, doseq=True)

        body: Optional[bytes] = None
        hdrs = dict(self.default_headers)
        if data is not None:
            if isinstance(data, dict):
                body = urllib.parse.urlencode(data, doseq=True).encode()
                hdrs["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            elif isinstance(data, str):
                body = data.encode()
            else:
                body = data
        elif json_body is not None:
            body = _json.dumps(json_body, ensure_ascii=False).encode()
            hdrs["Content-Type"] = "application/json; charset=UTF-8"
        if referer:
            hdrs["Referer"] = self.abs_url(referer)
        if headers:
            hdrs.update(headers)

        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=body, headers=hdrs, method=method.upper())
            t0 = time.perf_counter()
            try:
                resp = self.opener.open(req, timeout=timeout or self.timeout)
                raw = resp.read()
                dt = time.perf_counter() - t0
                self._sync_time(dict(resp.headers))
                raw = self._decompress(raw, resp.headers.get("Content-Encoding", ""))
                return Resp(status=resp.status, url=resp.geturl(),
                            headers={k.lower(): v for k, v in resp.headers.items()},
                            content=raw, text=self._decode(raw, resp.headers), elapsed=dt)
            except urllib.error.HTTPError as e:
                raw = e.read() if hasattr(e, "read") else b""
                dt = time.perf_counter() - t0
                self._sync_time(dict(e.headers or {}))
                raw = self._decompress(raw, (e.headers or {}).get("Content-Encoding", ""))
                return Resp(status=e.code, url=url,
                            headers={k.lower(): v for k, v in (e.headers or {}).items()},
                            content=raw, text=self._decode(raw, e.headers), elapsed=dt)
            except (urllib.error.URLError, socket.timeout, ConnectionError, ssl.SSLError,
                    OSError) as e:
                last_exc = e
                if attempt < self.retries:
                    time.sleep(0.15 * (attempt + 1))
                    continue
        raise NetError(f"{method} {url} 失败: {last_exc}")

    def get(self, url, **kw) -> Resp:
        return self.request("GET", url, **kw)

    def post(self, url, **kw) -> Resp:
        return self.request("POST", url, **kw)

    # ---------- 内部 ----------
    @staticmethod
    def _decompress(raw: bytes, enc: str) -> bytes:
        enc = (enc or "").lower()
        try:
            if "gzip" in enc:
                return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            if "deflate" in enc:
                try:
                    return zlib.decompress(raw)
                except zlib.error:
                    return zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            return raw
        return raw

    @staticmethod
    def _decode(raw: bytes, headers) -> str:
        ctype = ""
        try:
            ctype = (headers or {}).get("Content-Type", "") or ""
        except Exception:
            pass
        charset = "utf-8"
        for part in ctype.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip().strip('"')
        for enc in (charset, "utf-8", "gbk", "gb18030", "latin-1"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "replace")

    def _sync_time(self, headers: Dict[str, str]) -> None:
        date = headers.get("Date") or headers.get("date")
        if not date:
            return
        try:
            from email.utils import parsedate_to_datetime
            srv = parsedate_to_datetime(date).timestamp()
            self.time_offset = srv - time.time()
        except Exception:
            pass

    # ---------- 辅助 ----------
    def server_now(self) -> float:
        return time.time() + self.time_offset

    def cookie(self, name: str) -> Optional[str]:
        for c in self.jar:
            if c.name == name:
                return c.value
        return None

    def set_cookie(self, name: str, value: str, path: str = "/", domain: str = "") -> None:
        """手工注入 cookie（用于复用浏览器/手机抓包拿到的会话）。"""
        import time as _t
        host = domain or (self.base_url.split("://", 1)[-1].split("/")[0])
        c = http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=host, domain_specified=bool(domain), domain_initial_dot=False,
            path=path, path_specified=True, secure=True, expires=int(_t.time()) + 86400,
            discard=False, comment=None, comment_url=None, rest={}, rfc2109=False,
        )
        self.jar.set_cookie(c)

    def dump_cookies(self) -> Dict[str, str]:
        return {c.name: c.value for c in self.jar}
