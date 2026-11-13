# -*- coding: utf-8 -*-
"""纯标准库 RSA：正方教务登录密码加密（PKCS#1 v1.5）。

正方前端 rsa.js 从登录页正则取 publicKeyExponent / publicKeyModulus，
再把密码按 PKCS#1 v1.5 填充后做模幂。这里用 pow() 直接实现，无需 cryptography。
"""
from __future__ import annotations

import base64
import re
import secrets

_MOD = re.compile(r"var\s+publicKeyModulus\s*=\s*[\"']([0-9a-fA-F]+)[\"']")
_EXP = re.compile(r"var\s+publicKeyExponent\s*=\s*[\"']([0-9a-fA-F]+)[\"']")


def parse_keys(page_html: str):
    """从登录页 HTML 提取 (modulus_hex, exponent_hex)，取不到返回 (None, None)。"""
    m = _MOD.search(page_html)
    e = _EXP.search(page_html)
    return (m.group(1) if m else None, e.group(1) if e else None)


def _pkcs1_v15_pad(msg: bytes, k: int) -> bytes:
    if len(msg) > k - 11:
        raise ValueError("密码过长，超出 RSA 块长度")
    ps = b""
    while len(ps) < k - 3 - len(msg):
        ps += bytes([secrets.randbelow(255) + 1])
    return b"\x00\x02" + ps + b"\x00" + msg


def encrypt(password: str, modulus_hex: str, exponent_hex: str) -> str:
    """返回 base64 密文；调用方把它塞进 mm 字段。"""
    n = int(modulus_hex, 16)
    e = int(exponent_hex, 16)
    k = (n.bit_length() + 7) // 8
    em = _pkcs1_v15_pad(password.encode("utf-8"), k)
    m = int.from_bytes(em, "big")
    c = pow(m, e, n)
    return base64.b64encode(c.to_bytes(k, "big")).decode()
