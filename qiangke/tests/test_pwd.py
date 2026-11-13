# -*- coding: utf-8 -*-
"""`_p` 字段算法回归：用手机真机 HAR 里的成功样本做基准。

真机样本（jwmis.ncwu.edu.cn_2026_11_13_21_40_28.har，请求 #22 POST /hsjw/cas/logon.action，
响应 status=200 操作成功）：
    user=202611014  pwd=Lsh20070113@  sessionid=B7E1B5AF5B811575F4AD99B05ACD1A6C
    明文 params 里的 _p = 75a0d9af468ea40a5df81044e7957c9a
"""
import hashlib
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qk.adapters.kingo import build_logon_params, pwd_field  # noqa: E402

HAR_PLAIN = (
    "_u=MjAyNjExMDE0OztCN0UxQjVBRjVCODExNTc1RjRBRDk5QjA1QUNEMUE2Qw==&"
    "_p=75a0d9af468ea40a5df81044e7957c9a&randnumber=&isPasswordPolicy=1&"
    "txt_mm_expression=15&txt_mm_length=12&txt_mm_userzh=0&hid_flag=1&hidlag=1&hid_dxyzm="
)
USER = "202611014"
PWD = "Lsh20070113@"
SID = "B7E1B5AF5B811575F4AD99B05ACD1A6C"


def main() -> int:
    checks = {}

    # 1. 双 MD5：md5(md5(pwd) + md5(rand.lower()))
    checks["双MD5 == HAR密文"] = pwd_field(PWD, "", "md5") == "75a0d9af468ea40a5df81044e7957c9a"
    checks["md5 长度 32"] = len(pwd_field(PWD, "", "md5")) == 32

    # 2. 有验证码时 rand 参与运算
    r = pwd_field(PWD, "1234", "md5")
    want = hashlib.md5(
        (hashlib.md5(PWD.encode()).hexdigest()
         + hashlib.md5("1234".encode()).hexdigest()).encode()
    ).hexdigest()
    checks["rand 参与双MD5"] = r == want
    checks["rand 大小写归一"] = pwd_field(PWD, "AB12", "md5") == pwd_field(PWD, "ab12", "md5")

    # 3. plain 模式仍是明文（兼容旧路径）
    checks["plain 模式发明文"] = pwd_field(PWD, "", "plain") == PWD

    # 4. 完整明文与真机逐字节一致
    got = build_logon_params(USER, PWD, SID, "", False, "md5")
    checks["完整params逐字节一致"] = got == HAR_PLAIN

    # 5. 长度/复杂度字段用的是原密码，不是 md5
    checks["txt_mm_length 用原密码长度"] = "txt_mm_length=12&" in got
    checks["expression=15"] = "txt_mm_expression=15&" in got

    ok = True
    for name, res in checks.items():
        ok &= bool(res)
        print(f"[{'PASS' if res else 'FAIL'}] {name}")
    print("\n" + ("全部通过" if ok else "有失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
