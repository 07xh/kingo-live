# 华水抢课（华北水利水电大学 · 青果 KINGOSOFT）

目标系统：`https://jwmis.ncwu.edu.cn/hsjw/`（KINGOSOFT 高校智慧校园教学综合服务平台）
零第三方依赖，Python 3.8+ 直接跑。

**连接安全：** 正式教务站点必须使用 HTTPS 且验证服务器证书（system.verify_tls: true）。
本机 mock（localhost / 127.0.0.1 / ::1）可用于离线测试；非本机禁止使用明文 HTTP 或关闭 TLS 验证。
如果原来本地配置为 verify_tls: false，请改为 true；证书异常时修复系统信任链，而不是绕过校验。
测试仅使用虚构账号；不要提交真实 HAR、学号、密码或 Cookie。


## 快速开始

```bash
git clone https://github.com/07xh/kingo-live.git
cd kingo-live/qiangke

cp config.example.json config.ncwu.json      # 填学号密码
# 编辑 config.ncwu.json -> accounts[0].user / .pass

python run.py login      # 真实登录，并把会话存到 logs/session-<学号>.cookies
python run.py window     # 看哪个选课流程开着
python run.py list       # 看有哪些课
python run.py go         # 开抢
```

`config.ncwu.json` 含明文密码，已在 `.gitignore` 里，不会入库。

## 已验证到哪一步

| 环节 | 状态 | 证据 |
|---|---|---|
| 真实登录 | **实测成功** | 真实学号打一次 `cas/logon.action` 返回 `{"status":"200","message":"操作成功"}`，主页 32382 字节 |
| 登录加密 | 逐字节对齐 | 与真机 JS `loginbar.js` 生成的 params **完全一致**（含 `_p` 字段的双 MD5） |
| DES 算法 | 向量全 PASS | 青果 PC-1 非标准排布，已按 `jkingo.des.js` 规则重建；与站点 JS 逐字节一致 |
| 选课链路 | 已定位 | `wsxk.zx.html` → `wsxk.zx_dg.html` → `taglib/DataTable.jsp?tableId=` → `wsxk.zx_lc.jsp?lcid=` |
| 选课时间窗 | 已定位（当前关闭） | `getWsxkTimeRange.action` 返回 `2026-09-18 08:00 ~ 2026-09-19 18:00`，`isValidTimerange=0` |
| 抢课链路 | 仅离线模拟通过 | 两个虚构账号各一次物理提交，结果均待官方核验；模拟服务端不代表真实成功 |

## 登录：`_p` 为什么必须做双 MD5

真机 `loginbar.js` 的 `checkrand()` 里有这段：

```js
if (isXqeScanQrCode) {
    password = hex_md5(hex_md5(password) + hex_md5(randnumber.toLowerCase()));
}
```

服务端**直接拿这个串去比对**，所以传明文一律判「密码错误」（402 / 500「用户名或密码错误」）。
这一步没对齐，前面所有 402 都是白打的。

配置项 `system.auth.pwd_mode`：

| 值 | `_p` 内容 |
|---|---|
| `md5`（默认，现行） | `md5(md5(pwd) + md5(randnumber))`，32 位 hex |
| `plain` | 原密码，老版本页面才用 |

注意 `txt_mm_length` / `txt_mm_expression` 用的仍是**原始密码**长度与字符集，不是 md5 后的。

测试文件已经改用虚构账号与固定向量；不得把真实密码、Cookie 或未脱敏 HAR 提交到仓库。

## 目录

```
qiangke/
├─ run.py                  CLI（probe/login/window/discover/auto/import-curl/check/go）
├─ config.ncwu.json        配置：账号、课程、策略、grab_request
├─ qk/
│  ├─ kingo_des.py         青果 DES（与 jkingo.des.js 逐位等价）
│  ├─ adapters/kingo.py    登录（本地/CAS 双模式）+ 时间窗 + 选课页/列表/提交
│  ├─ kingo_xk.py          选课表格解析（DataTable GBK）+ items 载荷组装
│  ├─ adapters/zfsoft.py   正方适配器（备用，华水不用）
│  ├─ curl2req.py          cURL → 请求模板，自动参数化课程字段
│  ├─ parse.py             选课列表解析 + 课程匹配
│  ├─ engine.py            对时 → 多账号准备 → 持久意图检查 → 单次提交 → 待官方核验汇总
│  ├─ captcha.py           验证码：手工 / OCR 命令 / 打码平台
│  ├─ probe.py、net.py、notify.py、log.py、diag.py、cas.py
├─ mock/kingo_mock.py      青果仿真服务（反向校验 token 与密码算法）
└─ tests/                  e2e_kingo / test_pwd / test_guard / test_auto / e2e
```

## 核心命令

```bash
python run.py login            # 真实登录
python run.py window           # 遍历 8 个选课流程，看谁开着
python run.py list             # 列出各流程的可选课程（含班号/教师/余量/状态）
python run.py auto --apply     # 拉列表 → 按课程名匹配 → 回写配置
python run.py import-curl --file curl.txt --apply   # 手工抓包模板
python run.py check            # 全链路体检
python run.py go               # 开抢
```

`list` 输出示例：

```
[pre_elect] 2026-2027-2专业选修课补选  ★开放  2026-11-13 10:00 ~ 2026-11-13 23:59
        tableId=2548  总记录=0
WARNING 本轮无可选课程
[by_plan] 2026级新生选课（体育1）  已过期  2026-09-18 08:00 ~ 2026-09-19 18:00
        tableId=2568  总记录=1
        课程              班号      班级名         教师        学分    状态
        体育1             101     气排球         李红霞       1.0   选中
```

`--kind` 可指定单页：`pre_elect` / `by_plan` / `by_volunteer` / `retake`。

`window` 输出示例：

```
流程：2026级新生选课（体育1）   lcid=75035178963157379843299
学期：2026-2027学年第一学期（xn=2026 xqM=0）
时间：2026-09-18 08:00  →  2026-09-19 18:00
范围：主修(本年级/专业)   年级=2026 专业=电气工程及其自动化
WARNING 选课窗口未开放：当前时间不是有效的选课时间区段！
```

窗口没开时，`check` 也会先给出这条告警 —— 此时提交必然被拒，别浪费时间跑 `go`。

## 选课链路（青果 2019 版流程式选课）

```
菜单 PopMenuServlet.jsp (menucode=rongrong&menutype=init)   ← GBK 编码 XML，258 条
  └─ S2020202 选课(按开课计划)  /hsjw/student/wsxk.zx.html
       ├─ getWsxkTimeRange.action?xktype=2      ← 时间窗 + lcid + 年级专业
       └─ wsxk.zx_dg.html?menucode=00000
            ├─ 列表：POST taglib/DataTable.jsp?tableId=5929258
            ├─ 明细：wsxk.zx_lc.jsp?lcid=...&lcmc=...&menucode=...
            └─ 退选：jw/common/cancelElectiveCourse.action
```

其它相关入口：

| menucode | 名称 | 页面 |
|---|---|---|
| S2020201 | 预选课程 | `/student/wsxk.yx.html` |
| S2020202 | 选课(按开课计划) | `/student/wsxk.zx.html` |
| S2020205 | 退选课程 | `/student/wsxk.tx.nopre.html` |
| S2020208 | 选课(按志愿) | `/student/wsxk.zx.byvl.html` |
| S2020302 | 选课结果 | `/student/wsxk.zxjg.jsp` |

## 配置

```json
{
  "system": {
    "type": "kingo",
    "base_url": "https://jwmis.ncwu.edu.cn",
    "auth": {"mode": "local", "pwd_mode": "md5"}
  },
  "accounts": [
    {"name": "本人", "user": "2026xxxxxx", "pass": "你的密码",
     "courses": [{"name": "篮球", "teacher": "李老师"}]}
  ]
}
```

`auth.mode`：

| 值 | 走哪条 | 说明 |
|---|---|---|
| `local`（默认） | `jwmis/hsjw/cas/logon.action` | 青果本地账号，真机验证的正路 |
| `cas` | `authserver.ncwu.edu.cn/authserver/login` | 统一身份认证，RSA-1024 加密，见 `qk/cas.py` |

两套密码**不互通**。手机能登、电脑不能登，通常就是这两套在打架。

## 多学号同时抢

账号之间真并发；同一账号的多门课提交串行（账号级锁），避免会话串号：

```json
"accounts": [
  {"name": "我",   "user": "2026xxxxxx", "pass": "密码1", "courses": [{"name": "篮球"}]},
  {"name": "室友", "user": "2026xxxxxx", "pass": "密码2", "courses": [{"name": "足球"}]}
]
```

## 参数表

| 参数 | 主值 | 范围 | 依据 |
|---|---|---|---|
| `policy.interval_ms` | 300ms | 100–2000 | 低于 100ms 青果常回「操作过于频繁」 |
| `policy.poll_ms` | 1200ms | 500–3000 | 满员蹲守间隔，降频避风控 |
| `policy.jitter` | 0.3 | 0–0.5 | sleep 抖 ±30%，打散固定节奏 |
| `policy.backoff_cap_ms` | 3000ms | 1000–5000 | 异常链路退避上限 |
| `policy.lead_ms` | 120ms | 60–300 | 提前量 ≈ 首包 RTT×1.5 |
| `policy.max_seconds` | 1800s | 60–7200 | 单门课总时长 |
| `system.timeout` | 8s | 3–15 | 高峰期教务响应慢 |
| `system.login_retry` | 2 | 1–3 | **别超过 3**：青果 24h 内错 5 次锁号 |

`start_at` 填 `"2026-09-18 07:59:58"` 则按**服务器时间**（Date 头校正）准点发首包。

## 熔断（防锁号）

| 场景 | 行为 |
|---|---|
| 账号/密码仍是占位符 | 直接拒绝，0 次请求 |
| 返回 402 / 「输错 N 次」/ 含「锁定」 | 抛 `CredentialBlocked`，一次即停 |
| `max_fail_tries`（默认 3） | 服务端记录达到该次数也熔断 |

## 验证码

首次登录通常不要（页面 `yzmbt="0"`），连续失败后才弹。配置：

```json
"captcha": {"mode": "manual"}
"captcha": {"mode": "cmd",  "cmd": "tesseract {img} stdout --psm 7"}
"captcha": {"mode": "http", "url": "https://your-ocr/api"}
```

**CAS 统一身份认证（`auth.mode = "cas"`）正常账密登录不弹验证码**：华水 `authserver`
主表单 `fm1` 只有 username / password(RSA) / execution / encrypted / _eventId / loginType，
没有 captcha 字段。页面里那些「验证码登录」「滑动登录」是被注释掉的模板，
只有连续失败或风控才渲染 `captcha2` 弹层 —— 撞上了工具不硬闯，直接报提示，
改用下面的会话复用。

## 保存 / 复用登录状态

登录成功后 cookie 罐（Netscape 格式）自动落盘，之后所有命令先探测主页，
会话还活着就**直接进系统，0 次密码请求**：

```json
"system": {
  "session_file": "logs/session-{user}.cookies",
  "session_disabled": false
}
```

```bash
python run.py login              # 首次：真实登录 → 保存会话
python run.py window             # 之后：自动复用，不再打密码
python run.py go                 # 开抢：同样复用
python run.py login --fresh      # 强制重新登录并覆盖会话文件

# 不想再打密码（比如已经输错过几次）：用浏览器登录态直接导入
python run.py import-cookies --file 已登录.har --verify
python run.py import-cookies --file cookies.txt --verify   # 插件导出的 Netscape 格式也行
```

`--verify` 只 GET 一次主页判断会话是否还活着，**不发送任何登录请求，不消耗错误次数**；
日志只打印 cookie 的名字，不打印值。HAR 里会同时还原 `jwmis` 的 `JSESSIONID`
和 `authserver` 的 `CASTGC`（含 `.ncwu.edu.cn` 父域）。

| 细节 | 说明 |
|---|---|
| 存储内容 | `jwmis` 的 `JSESSIONID` + `authserver` 的 CAS cookie，两个域一起存 |
| 有效性判据 | 主页 HTTP 200、长度 ≥ 500、URL 不含 login/authserver、正文不含「凭证已失效」 |
| 失效处理 | 自动回落正常登录；抢课中途掉线也走 `relogin()` 重新保存 |
| 多账号 | `{user}` 自动替换成学号，模板没带 `{user}` 时自动追加，绝不串号 |
| 安全 | `logs/` 已在 `.gitignore`；cookie 罐等同账号凭据，别外传、别提交 |
| 直接导入浏览器会话 | 用浏览器插件导出 Netscape `cookies.txt` 放到 `session_file` 指定路径即可，无需跑登录 |

**为什么这件事重要**：青果本地登录 24h 内错 5 次锁号，CAS 连续失败会弹风控滑块。
会话复用把每个窗口需要的密码请求从「每次运行 1~2 次」压到「只在会话过期时 1 次」。

## 返回码分类

当前正式提交没有官方逐课程回查，任何写入回执都不能归类为选课成功；每个目标只允许一次物理写入，后续应人工核对官方记录。

## 测试

```bash
python tests/test_xk.py        # 23 项：DataTable 解析 / 空表 / 退化字段 / items 格式
python tests/test_pwd.py       # 8 项：双 MD5 与虚构黄金向量一致
python tests/e2e_kingo.py      # 7 项：双账号登录 + 单目标单次提交 + 结果待核验
python tests/test_guard.py     # 16 项：占位符拦截 / 402 熔断
python tests/test_auto.py      # 5 项：课程匹配 + 回写 + 不误配
python tests/test_session.py   # 27 项：cookie 落盘/恢复 + 真实 HTTP 往返 + HAR 导入 + 免重登
python tests/e2e.py            # 7 项：正方适配器没退化
python tests/test_tls.py       # 10 项：TLS 校验 / 禁用远程明文 / 本机 mock
```

## 已知坑

1. **`_p` 必须双 MD5** —— 传明文一律 402，之前所有失败都源于此。
2. **`_sessionid` 要用 `_ssessionid`** —— 浏览器在 `checkrand()` 里先赋值再拼 `_u`。
3. **青果 PC-1 非标准 DES** —— `key[i*8+j] = keyBit[8k+i]`，按标准 DES 写子密钥全错。
4. **菜单是 GBK XML** —— `PopMenuServlet.jsp` 要 `decode("gbk")`，且必须 POST `menucode=rongrong&menutype=init`。
5. **`log/setCookie` 存的是账号名不是密码** —— 别指望从 cookie 还原凭据。

## 只读标准课程 JSON（跨 Windows 客户端）

`python run.py list --kind by_plan --standard-json courses.json`
将本次实际只读列表的课程代码、教学班、名称、教师、可信余量与
**本机观察时间**写入本机 JSON（只记录公开课程字段，不导出会话和账号）。
Windows 客户端可通过“离线工具 → 查看规范化课程 JSON（非实时）”打开。
任何缺字段或含混余量都不会被当作可信名额；异常/空数据不会覆盖已存在的快照。
这只是历史快照，不是实时官方状态，也不证明课程已选成功。

## 2026-11-14 结果核验保护（仅本目录）

青果和正方适配器的 `grab()` **不再**把“已选”“重复选课”
或“操作成功”等写入回执当作课程选中。所有已实际发送的提交
在缺少可信的官方逐课程回查前只返回 `unknown`，
`GrabResult.ok` 始终为 `False`；网络/会话异常及 429 都不会被
显示为成功。日志只保存固定分类说明，不保存原始回执正文。

此更改不实现、猜测或调用新的官方已选清单接口：当前系统缺少
已验证的只读结果接口与身份对照证据。应与跨重启意图持久化
保护一起使用，完成官方回查验证前不得宣称已选成功。


## 2026-11-14：跨重启防重复提交与结果待核验

正式命令行执行入口 `go` 现在在发送前创建持久化提交意图：
默认使用用户目录 `~/.kingo-assistant/intent-journal.sqlite3`
（Windows 为对应用户主目录）；`KINGO_INTENT_DB` 可供隔离的测试环境
指定专用路径。SQLite 原子唯一约束用于处理跨线程/进程的同目标竞态。
只存加盐 HMAC 指纹和状态，不存学号、课程名、密码或 Cookie。

如果该账号、学期和课程已经留下提交意图，程序将保护性停止，
**不会因重启、掉线、响应文本或名额重新出现就重发**。
本版不自动删除 pending，也不声称具备已验收的官方课程回查。
跨模块课程号/教学班身份未统一前，不得将不同代码入口的状态
视为同一条已核实结果。不要通过删除数据库绕过待核验状态。

旧配置中的 `interval_ms` / `poll_ms` / `jitter` 仅为历史兼容字段，
不再使本命令行执行器进行重复写入；登录/只读查询与真实写入是不同操作。


### 2026-11-14 提交意图安全约束

本地提交执行器要求在 `system.xnxq` 中设置经学校确认的准确学期标识。
示例默认留空：留空、没有稳定 `xkid`/`jxb_ids` 教学班号、或两者冲突时
**禁止写入**。仅凭课程名称、教师或含糊回执不能构造一个可提交目标。
SQLite 意图指纹仅包括教务站点、账号、学期和教学班号，课程名称等
显示字段变化不得绕过跨重启去重保护。需要由本人另行检查官方已选
结果；此变更不会自动确认选中，也不会解锁重复提交。
