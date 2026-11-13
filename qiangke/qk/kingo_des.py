# -*- coding: utf-8 -*-
"""青果（KINGOSOFT）登录参数加密 —— jkingo.des.js 的纯 Python 等价实现。

算法：标准 DES-ECB，64bit 分组，无填充。
  - 明文按 4 个 UTF-16 字符一组 → 64bit（不足补 0）
  - key 同样按 4 字符一组 → 每块 64bit，逐块对数据块迭代加密
  - 输出 16 位大写 hex 字符串
对应 JS：
  strEnc(data, firstKey, null, null) == "".join(des group hex)
"""
from __future__ import annotations

# ---------- 标准 DES 表 ----------
IP = [58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4,
      62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32, 24, 16, 8,
      57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
      61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7]

FP = [40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31,
      38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
      36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27,
      34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25]

E = [32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9, 10, 11, 12, 13,
     12, 13, 14, 15, 16, 17, 16, 17, 18, 19, 20, 21, 20, 21, 22, 23, 24, 25,
     24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1]

P = [16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
     2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25]

# 注意：青果这套 JS 的 PC-1 不是标准 DES 排布（按 key[i*8+j]=keyBit[8*k+i] 生成，
# 即 7 组 × 8 字节列取，丢掉每字节最高位），必须照抄，否则子密钥全错。
PC1 = [8 * (7 - j) + i + 1 for i in range(7) for j in range(8)]

PC2 = [14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10,
       23, 19, 12, 4, 26, 8, 16, 7, 27, 20, 13, 2,
       41, 52, 31, 37, 47, 55, 30, 40, 51, 45, 33, 48,
       44, 49, 39, 56, 34, 53, 46, 42, 50, 36, 29, 32]

SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]

SBOX = [
    [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
     0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
     4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
     15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13],
    [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
     3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
     0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
     13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9],
    [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
     13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
     13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
     1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12],
    [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
     13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
     10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
     3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14],
    [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
     14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
     4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
     11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3],
    [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
     10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
     9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
     4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13],
    [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
     13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
     1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
     6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12],
    [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
     1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
     7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
     2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11],
]


def _bits(v: int, n: int) -> list[int]:
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def _permute(v: int, table: list[int], in_bits: int) -> int:
    b = _bits(v, in_bits)
    out = 0
    for pos in table:
        out = (out << 1) | b[pos - 1]
    return out


def _subkeys(key64: int) -> list[int]:
    k = _permute(key64, PC1, 64)          # 56 bit
    c, d = (k >> 28) & 0xFFFFFFF, k & 0xFFFFFFF
    out = []
    for s in SHIFTS:
        c = ((c << s) | (c >> (28 - s))) & 0xFFFFFFF
        d = ((d << s) | (d >> (28 - s))) & 0xFFFFFFF
        out.append(_permute((c << 28) | d, PC2, 56))
    return out


def _f(r32: int, k48: int) -> int:
    x = _permute(r32, E, 32) ^ k48
    out = 0
    for i in range(8):
        six = (x >> (42 - 6 * i)) & 0x3F
        row = ((six >> 4) & 0x2) | (six & 0x1)
        col = (six >> 1) & 0xF
        out = (out << 4) | SBOX[i][row * 16 + col]
    return _permute(out, P, 32)


_cache: dict[int, list[int]] = {}


def des_encrypt_block(block: int, key: int) -> int:
    ks = _cache.get(key)
    if ks is None:
        ks = _cache[key] = _subkeys(key)
    v = _permute(block, IP, 64)
    left, right = (v >> 32) & 0xFFFFFFFF, v & 0xFFFFFFFF
    for k in ks:
        left, right = right, left ^ _f(right, k)
    return _permute((right << 32) | left, FP, 64)


def des_decrypt_block(block: int, key: int) -> int:
    ks = _cache.get(key)
    if ks is None:
        ks = _cache[key] = _subkeys(key)
    v = _permute(block, IP, 64)
    left, right = (v >> 32) & 0xFFFFFFFF, v & 0xFFFFFFFF
    for k in reversed(ks):
        left, right = right, left ^ _f(right, k)
    return _permute((right << 32) | left, FP, 64)


# ---------- 与 jkingo.des.js 对齐的上层 ----------
def str_to_bt(s: str) -> int:
    """<=4 个 UTF-16 字符 → 64bit（不足补 0）。"""
    v = 0
    for i in range(4):
        ch = ord(s[i]) if i < len(s) else 0
        v = (v << 16) | (ch & 0xFFFF)
    return v


def get_key_blocks(key: str) -> list[int]:
    return [str_to_bt(key[i:i + 4]) for i in range(0, len(key), 4)]


def str_enc(data: str, first_key: str) -> str:
    """等价于 JS strEnc(data, first_key, null, null)，返回大写 hex 串。"""
    if not data:
        return ""
    blocks = get_key_blocks(first_key)
    out = []
    for i in range(0, len(data), 4):
        bt = str_to_bt(data[i:i + 4])
        for kb in blocks:
            bt = des_encrypt_block(bt, kb)
        out.append(f"{bt:016X}")
    return "".join(out)


def str_dec(hex_str: str, first_key: str) -> str:
    blocks = get_key_blocks(first_key)
    out = []
    for i in range(0, len(hex_str), 16):
        bt = int(hex_str[i:i + 16], 16)
        for kb in reversed(blocks):
            bt = des_decrypt_block(bt, kb)
        out.append("".join(chr((bt >> (48 - 16 * j)) & 0xFFFF) for j in range(4)))
    return "".join(out)
