# -*- coding: utf-8 -*-
"""登录参数生成的脱敏黄金向量。

只使用虚构账号、密码和会话 ID。原始真实 HAR 凭据不得提交到仓库。
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qk.adapters.kingo import build_logon_params, pwd_field  # noqa: E402

USER = "demo_student_01"
PWD = "DemoPass123!"
SID = "MOCK_SESSION_ABC123"
EXPECTED_PASSWORD_HASH = "ac84e6b657ded275d2e516e2e113c18d"
EXPECTED_PARAMS = (
    "_u=ZGVtb19zdHVkZW50XzAxOztNT0NLX1NFU1NJT05fQUJDMTIz&"
    "_p=ac84e6b657ded275d2e516e2e113c18d&randnumber=&isPasswordPolicy=1&"
    "txt_mm_expression=15&txt_mm_length=12&txt_mm_userzh=0&hid_flag=1&hidlag=1&hid_dxyzm="
)


def main() -> int:
    checks = {}

    checks["双MD5 == 固定虚构向量"] = pwd_field(PWD, "", "md5") == EXPECTED_PASSWORD_HASH
    checks["md5 长度 32"] = len(pwd_field(PWD, "", "md5")) == 32

    r = pwd_field(PWD, "1234", "md5")
    want = hashlib.md5(
        (hashlib.md5(PWD.encode()).hexdigest()
         + hashlib.md5("1234".encode()).hexdigest()).encode()
    ).hexdigest()
    checks["rand 参与双MD5"] = r == want
    checks["rand 大小写归一"] = pwd_field(PWD, "AB12", "md5") == pwd_field(PWD, "ab12", "md5")
    checks["plain 模式返回输入"] = pwd_field(PWD, "", "plain") == PWD

    got = build_logon_params(USER, PWD, SID, "", False, "md5")
    checks["完整params固定向量"] = got == EXPECTED_PARAMS
    checks["txt_mm_length 用原密码长度"] = "txt_mm_length=12&" in got
    checks["expression=15"] = "txt_mm_expression=15&" in got

    ok = True
    for name, passed in checks.items():
        ok &= bool(passed)
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    print("\n" + ("全部通过" if ok else "有失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
