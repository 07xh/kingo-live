# 华水抢课（华北水利水电大学 · 青果 KINGOSOFT）

> 本目录是 `kingo-live` 仓库下的独立 Python 实现，与 `ncwu/`、`kingo-live/` 两套并存，互不依赖。
> 目标系统：`jwmis.ncwu.edu.cn/hsjw`（青果 KINGOSOFT 2019 版）。

零第三方依赖，Python 3.8+ 直接跑。

## 快速开始

```bash
git clone https://github.com/07xh/kingo-live.git
cd kingo-live/qiangke

cp config.example.json config.ncwu.json      # 填学号密码
# 编辑 config.ncwu.json -> accounts[0].user / .pass

python run.py login      # 真实登录
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
| 抢课链路 | 仿真端到端 7/7 PASS | 两账号并发 → 满员重试 → 全部抢到，服务端反向校验 token |

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

验证方式不靠猜：`tests/test_pwd.py` 拿真机 HAR 里那次**成功**登录的明文做基准，逐字节比对。

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
│  ├─ engine.py            对时 → 多账号并发 → 退避重试 → 汇总
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
  {"name": "室友", "user": "202611015", "pass": "密码2", "courses": [{"name": "足球"}]}
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

## 返回码分类

`success` 选上 / `enrolled` 已在 / `full` 满员重试 / `conflict` 冲突 / `auth` 会话失效自动重登 / `error` 其它

## 测试

```bash
python tests/test_xk.py        # 23 项：DataTable 解析 / 空表 / 退化字段 / items 格式
python tests/test_pwd.py       # 8 项：双 MD5 与真机 HAR 逐字节一致
python tests/e2e_kingo.py      # 7 项：双账号并发 → 满员重试 → 全抢到
python tests/test_guard.py     # 16 项：占位符拦截 / 402 熔断
python tests/test_auto.py      # 5 项：课程匹配 + 回写 + 不误配
python tests/e2e.py            # 7 项：正方适配器没退化
```

## 已知坑

1. **`_p` 必须双 MD5** —— 传明文一律 402，之前所有失败都源于此。
2. **`_sessionid` 要用 `_ssessionid`** —— 浏览器在 `checkrand()` 里先赋值再拼 `_u`。
3. **青果 PC-1 非标准 DES** —— `key[i*8+j] = keyBit[8k+i]`，按标准 DES 写子密钥全错。
4. **菜单是 GBK XML** —— `PopMenuServlet.jsp` 要 `decode("gbk")`，且必须 POST `menucode=rongrong&menutype=init`。
5. **`log/setCookie` 存的是账号名不是密码** —— 别指望从 cookie 还原凭据。
