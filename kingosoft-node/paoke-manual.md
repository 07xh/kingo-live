# 强智教务选课辅助 — 实测记录与操作手册

全部接口来自真实浏览器流量（HAR）抓取 + 在线回放验证，无一处猜测。
工具：`paoke.mjs`（694 行，零依赖，Node 18+）。

---

## 一、先看结论：现在能不能抢

用真实会话跑 `node paoke.mjs windows` 得到的结果（2026-11-14 14:09 实测）：

| 轮次 | xktype | 状态 | 起 | 止 |
|---|---|---|---|---|
| 预选 | 1 | 关闭 | 2026-11-13 10:00 | 2026-11-13 23:59 |
| 正选 | 2 | 关闭 | 2026-09-18 08:00 | 2026-09-19 18:00 |
| 补选 | 3 | **未定义** | — | — |
| 重修选 | 4 | 关闭 | 2026-10-08 09:00 | 2026-10-09 18:00 |
| 退选 | 5 | **未定义** | — | — |
| 取消正选 | 6 | **未定义** | — | — |
| 正选实验 | 7 | **未定义** | — | — |

三个事实：

1. **预选窗口昨天刚关**（11-13 10:00 → 23:59，只有 14 小时）。
2. **正选早在 9 月就结束**，补选/退选系统还没定义。
3. 所以当前**没有任何一个轮次开放**，脚本此刻抢不了——不是脚本的问题，是没有窗口。

`windows` 每跑一次都是实时数据。窗口一开，这一栏会变 `★开放中` 并给出剩余时间。

---

## 二、完整接口链（真实，可直接复现）

### ① 初始化：拿学号、学期、lcid、窗口起止

```
POST /hsjw/jw/common/getWsxkTimeRange.action?xktype=2
```

返回：

```json
{"status":"400","message":"当前时间不是有效的选课时间区段！",
 "result":"{\"xn\":\"2026\",\"xqM\":\"0\",\"qssj\":\"2026-09-18 08:00\",\"jssj\":\"2026-09-19 18:00\",
            \"xh\":\"202601010101\",\"lcid\":\"50000000000000000000001\",\"xnxqDesc\":\"2026-2027学年第一学期\"}"}
```

**关键坑**：`status` 是 `400`，但 `result` 里照样带全套数据。按 `status!=200` 就报错的写法会直接失效。工具里照收不误。

`lcid` 每次页面加载都变（23 位随机串），不能缓存跨会话用。

### ② 课程列表：拿课号、余量、skbjdm

```
GET  /hsjw/student/wsxk.zx.html?menucode=S2020202          → 页面里取 tableId
POST /hsjw/taglib/DataTable.jsp?tableId=<tid>&clientWidth=1210
```

请求体：

```
initQry=1&xktype=2&xn=2026&xq=0&nj=2026&pycc=05&dwh=05&zydm=0502
&kclb1=&kclb2=&isbyk=&items=&xnxq=2026%2C0&btnSubmit=%CC%E1%BD%BB
&sel_kclb1=&sel_kc=&sel_rkjs=&menucode_current=S2020202
```

返回的表格每行是这样的结构（隐藏列才是关键）：

```html
<tr class='O' id='tr0'>
  <td id='tr0_kc'   name='kc'>[0400111101]工程制图</td>
  <td id='tr0_xf'   name='xf'>3.0</td>
  <td id='tr0_xkrssx' name='xkrssx'>135</td>   <!-- 限选人数 -->
  <td id='tr0_xkrs'   name='xkrs'>123/0</td>   <!-- 已选/免听 -->
  <td id='tr0_skbjdm' name='skbjdm' style='display:none'>0400111101-002</td>
</tr>
```

`skbjdm` = `课号-上课班号`，**这就是提交时要的班级标识，列表页直接给了**，不用再进弹窗页。

余量 = `xkrssx - xkrs`。

### ③ 选课弹窗：拿 ActionForm 全量真实值

```
GET /hsjw/student/report/wsxk.zx_promt.jsp?params=<enc>&token=<md5>&timestamp=<ts>
```

明文字段：

```
kcdm=0400111101&xh=202601010101&xn=2026&xq=0&checked=false&skbjdm=
&nj=2026&zydm=0502&xsbjdm=&kclb1=&kclb2=&khfs=&xf=3.0&xktype=2
&lcid=50000000000000000000001&xxkckzfs=0&yxsjct=0&yxkzyfxxk=0
&isbuybook=&iscx=&isyxtj=&kcfw=zxbnj&kclb3=&xfsx=&mssx=
```

加密方式与登录完全一致：

```
params = base64( strEnc(明文, tempDeskey, null, null) )
token  = md5( md5(明文) + md5(timestamp) )
```

`tempDeskey` / `timestamp` 来自两个**免鉴权**接口：`/frame/homepage?method=getTempDeskey`、`=getTempNowtime`。

返回页里有一个 `id="ActionForm"` 的表单，字段名都带 `electiveCourseForm.` 前缀：

```
electiveCourseForm.xktype=2       electiveCourseForm.lcid=...
electiveCourseForm.xn=2026        electiveCourseForm.xq=0
electiveCourseForm.xh=202601010101
electiveCourseForm.nj=2026        electiveCourseForm.zydm=0502
electiveCourseForm.kcdm=0400111101
electiveCourseForm.skbjdm=        ← 空，等填
electiveCourseForm.skbzdm=
electiveCourseForm.outnumber=0
electiveCourseForm.zfx_m=0
electiveCourseForm.xf=3.0
electiveCourseForm.is_checkTime=1
electiveCourseForm.kcfw=zxbnj
electiveCourseForm.xk_points=0
```

### ④ 上课班级列表

弹窗页里会带一个第二级 tableId（本次是 6142），拉法：

```
GET /hsjw/taglib/DataTable.jsp?tableId=6142&fre=1
    &xn=2026&xq_m=0&xh=202601010101&kcdm=0400111101
    &skbjdm=&xktype=2&kcfw=zxbnj&isyxkc=false&lcid=...&xsbjdm=
```

列：`skbjdm` / `skbjmc` / `skbzdm` / `xkrssx`（限选）/ `xkrs`（已选）/ `sksj` / `skdd` / `outnumber` / `zfx_m`。

### ⑤ 提交（唯一写操作）

```
POST /hsjw/jw/common/saveElectiveCourse.action
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
```

body：

```
params=<base64(DES(明文))>&token=<md5(md5(明文)+md5(ts))>&timestamp=<ts>
```

明文的构造（照抄页面 `submit` 逻辑）：

1. 取 `#ActionForm` 序列化（jQuery 语义：`encodeURIComponent`，`%20`→`+`）
2. **去掉每项前面的 `electiveCourseForm.`**
3. 覆盖开关：
   - `skbjdm` = 目标班
   - `is_buy_book` = `0`
   - `is_cx` = `0`（非重修）
   - `is_yxtj` = `1`（允许上课班调剂，命中率更高）
   - `is_checkTime` = `1`（不许时间冲突）
   - `xk_points` = `0`

返回 `{"status":"200","message":"选课成功","result":"..."}`。

### ⑥ 退选

```
POST /hsjw/jw/common/cancelElectiveCourse.action
明文: xn&xq&xh&kcdm&skbjdm&skbzdm&lcid&xktype=5
```

### ⑦ 预选是另一套，且不加密

预选页 `/student/wsxk.yx.html?menucode=S2020201` 走的是：

```
POST /hsjw/STU_PreElectCourseAction.do?hidOption=saveData
```

`items` 是 `课号,教师,调剂,勾选|` 拼接的志愿串，**无 DES、无 token**，纯明文表单。
所以预选是**志愿制**，不是先到先得——`grab` 那套并发与时钟校准在预选上毫无意义，用不着跑。

---

## 三、六个坑（都踩过）

**1. 页面必须带 Referer。**
`GET /student/wsxk.zx.html` 不带 Referer 直接返回 1113 字节：

```html
<h3 style="color:red;">你未授权访问此系统资源，请更换账号登录！</h3>
```

带上 `Referer: https://jwmis.ncwu.edu.cn/hsjw/frame/homes.action` 就是正常的 31129 字节。工具里所有请求默认带这个 Referer。

**2. `getWsxkTimeRange` 的 `status=400` 不是错误。**
窗口关闭时也返回 400，但 `result` 里数据齐全。这是拿 `lcid` 和窗口时间的主要来源。

**3. `tableId` 每次渲染都变。**
不能写死。工具在每次跑之前从对应页面正则抓 `DataTable.jsp?tableId=(\d+)`。

**4. `skbjdm` 有前缀。**
不是 `002`，是 `0400111101-002`（课号-班号）。只传 `002` 会被拒。

**5. 编码混用。**
`.action` / `.do` 返 UTF-8，`.jsp` / `.html` 返 GBK。统一先试严格 UTF-8，抛错再退 GBK。搞错就全是乱码，解析全废。

**6. 教学班已满 ≠ 接口错。**
`showTotalRecord('<tableId>','0')` 就是 0 条，属于正常返回。脚本要把这个当终止条件，不能当失败重试——否则会空转几十轮，白白刷日志。

**7. 有频控，而且按 Referer 触发。**
短时间连续 POST 会返回：

```
错误:请求太过频繁
```

实测：同一秒内换 Referer 连打三次 `saveElectiveCourse.action`，后两次直接被拦。用正确的 `Referer: /student/report/wsxk.zx_promt.jsp` 打则是正常 JSON。工具里 `submitElect` 固定用这个 Referer，并在结果码 `THROTTLE` 上做指数退避（600ms × 次数）。

### 三个错误响应的语义（实测，别搞混）

| 请求体 | 返回 | 含义 |
|---|---|---|
| 真加密 | `{"status":"400","message":"当前时间非有效的网上选课时间区段!"}` | **加密链正确**，卡在窗口校验 |
| 伪加密（随便填 params） | `{"status":"300","message":"无效的访问请求!"}` | token/解密校验失败 |
| 明文直发 | `{"status":"400","message":"当前选课操作的用户不是选课学生本人!"}` | 解不出 xh，被当作非本人 |

**「当前时间非有效的网上选课时间区段」是链路打通的铁证**——服务端完整解密了参数、取到了 xh 和 kcdm，然后才走到时间窗校验这一步。看到这条就说明加密没问题，只差窗口。

---

## 四、怎么用

```bash
cd kingosoft-node

# 有浏览器登录态，直接从 HAR 导入，不用密码
node paoke.mjs import ~/Downloads/xxx.har

node paoke.mjs init        # 拿 xh/xn/xq/lcid/窗口
node paoke.mjs windows     # 七个轮次开放状态（先看这个）
node paoke.mjs calib       # 时钟偏差（毫秒）
node paoke.mjs list        # 课程 + 余量 + skbjdm
node paoke.mjs skb 0400111101          # 某门课的全部上课班
node paoke.mjs grab 0400111101 --dry    # 只打印请求，不发送
node paoke.mjs grab 0400111101 --conc=5 # 真抢
node paoke.mjs grab 0400111101-002      # 直接指定班级
node paoke.mjs watch 0400111101 3       # 轮询捡漏，见空位就抢
node paoke.mjs my                       # 已选课程
```

### 各命令干什么

| 命令 | 作用 | 请求数 |
|---|---|---|
| `import` | 从 HAR 提 cookie，落 `session.json` | 0 |
| `init` | `getWsxkTimeRange` 补全会话 | 1 |
| `windows` | 枚举 xktype=1..7 的窗口 | 7 |
| `calib` | 服务器时间 vs 本机，算偏差 | 1 |
| `list` | 正选页 + DataTable，解析余量 | 2 |
| `skb` | 弹窗页 + 班级表 | 3 |
| `grab` | 弹窗页 → 组装 → 并发提交 | 准备 3~4 + 每轮 3 |
| `watch` | 轮询 list，命中即 grab | 每轮 2 |
| `my` | 退选页 + DataTable(xktype=5) | 3 |

### 结果码

| 码 | 含义 | 脚本动作 |
|---|---|---|
| `OK` | 200 选课成功 | 停 |
| `FULL` | 名额满 | 120~300ms 后重试 |
| `THROTTLE` | 请求太过频繁 | 指数退避重试（600ms × n） |
| `CONFLICT` | 时间冲突 | **停**（继续打没意义，还可能触发风控） |
| `LIMIT` | 超学分/门数 | **停** |
| `DUP` | 已选过 | **停** |
| `AUTH` | 会话失效 / 无效访问请求 | **停**，重新 import |
| `NOTSTU` | 不是选课学生本人 | **停**，账号角色不对 |
| `CLOSED` | 非有效选课时间段 | **停**，等窗口开 |
| `NOCLASS` | 该课 0 个可选班 | **停** |
| `OTHER` | 其它消息 | 停并打印原文 |

把 `CONFLICT` / `LIMIT` / `DUP` 当可重试是最常见的错误写法——会在已经确定失败的状态下刷几千次请求。

---

## 五、速度对比

| 环节 | 浏览器点击 | 脚本 |
|---|---|---|
| 加载完整页面 | 30~200 KB | 不发 |
| 渲染解析 | 50~300 ms | 不发 |
| 二次跳转再加载 | 300~1500 ms | 不发 |
| 提交本身 | 1 次 POST | 1 次 POST |
| **合计** | **800~3000 ms** | **80~300 ms** |

脚本快的地方不是"点得更快"，是**根本没有点**——省掉页面渲染和二次跳转，只留那一个 POST。

`grab` 的准备阶段（弹窗页 + 班级表）会花 2~4 秒，但只做一次；之后每轮重试只有 `seal`(2 GET) + `submit`(1 POST)。

---

## 六、并发怎么定

| 场景 | 并发 | 依据 |
|---|---|---|
| 单人正选 | 3 | 默认值，够用且不显眼 |
| 开盘瞬间 | 5 | 系统本身会慢，靠冗余命中；**再高会撞频控** |
| 补选捡漏 | 1~2 | 名额随机释放，堆请求没收益 |
| 上限 | 8 | 实测连打就会出「请求太过频繁」，超过只会更快被拦 |

名额在服务端，客户端堆请求不会让名额变多。这里的瓶颈不是网速，是**服务端频控**——并发开大反而更慢。

如果一定要提速，正确的做法不是加并发，而是：

1. `grab` 的准备阶段只做一次（工具已经这么做了）
2. 开盘前把 `--dry` 跑一遍，确认 `skbjdm` 正确
3. 用 `calib` 校时，提前 200~400ms 发起第一发

---

## 七、边界

| 学校开了什么 | 这套还行不行 |
|---|---|
| 无额外防护（当前部署） | 直接可用 |
| 选课验证码 | 失效，得先过验证码 |
| 排队令牌 / Waiting Room | 失效，请求会被排队页拦 |
| 同账号频控 | 降并发、拉长间隔 |
| IP 频控 | 需要出口轮换 |
| 志愿抽签（预选就是） | **速度无意义**，别跑 |

判断当前是哪种：跑 `probe` 和 `list`。

- `list` 直接返回课程行 → 无排队
- 返回"系统繁忙"或空骨架 → 有排队或限流
- 提交时要求额外 token / 图片码 → 有验证码

---

## 八、安全提醒

- `grab` / `drop` 是**写操作**。跑之前先 `--dry`，看清楚目标 `skbjdm` 和 `kcdm` 再动手。
- 退选（`drop`）会掉课，不可撤销。参数传错就是把自己已选的课退了。
- 会话 cookie 落在 `session.json`，已在 `.gitignore` 里，别提交。
- 强智登录**错 5 次锁号 24 小时**。本工具不复用密码登录，只吃 HAR 里的会话，不会触发计数。
