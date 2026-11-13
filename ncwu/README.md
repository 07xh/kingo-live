# NCWU · Kingo 选课实测脚本

从华北水利水电大学青果教务（`jwmis.ncwu.edu.cn/hsjw`，Struts2 / GBK / JSESSIONID）真实抓包还原出来的三条接口链。
零第三方依赖，Python 3.10+，三个脚本各自自包含、可独立运行。

| 脚本 | 作用 | 是否需要选课窗口 |
|---|---|---|
| `hs_kcbcx.py` | 开课计划全量查询（课程 / 教学班 / 容量 / 已选 / 可选），CSV 导出、余量盯盘 | 否，常年可读 |
| `hs_xk.py` | 门禁探测 `gate`、可选课程 `list`、提交 `submit`（支持 `--dry-run` 只打印不发包） | `list` / `submit` 需要 |
| `hs_sniper.py` | 抢课引擎：门禁 → 校时 → 盯余量 → 预热 → 并发开火 → 回查验证 | 需要 |

## 凭据

真实 Cookie **不落盘**。仓库里只有 `sniper.example.json`（cookie 为空）。三种注入方式，优先级从高到低：

```bash
export KINGO_COOKIE='JSESSIONID=...'           # 推荐
python hs_sniper.py --cookie 'JSESSIONID=...'
cp sniper.example.json hs_sniper.json          # 本地文件，已被 .gitignore 忽略
```

`JSESSIONID` 从浏览器 F12 → 网络 → 任一 `.action` 请求头里取，`Path=/hsjw`。

## 用法

```bash
# 1) 看有哪些课、还剩多少位（不需要开窗口）
python hs_kcbcx.py --xn 2026 --xq 1 --sort qdrs
python hs_kcbcx.py --xn 2026 --xq 1 --only-open --csv plan.csv
python hs_kcbcx.py --xn 2026 --xq 1 --watch 5        # 每 5 秒刷新余量

# 2) 探门禁 —— 唯一可信的开闸判据
python hs_xk.py gate --xktype 1

# 3) 窗口内的可选课程
python hs_xk.py list --xktype 1 --kcfw zxbnj

# 4) 提交预演（只打印 body，不发包）
python hs_xk.py submit --kcdm 0400111101 --dry-run

# 5) 抢课引擎
python hs_sniper.py --cfg hs_sniper.json --check     # 体检：门禁 / 校时 / 目标余量
python hs_sniper.py --cfg hs_sniper.json --scan      # 只扫一遍
python hs_sniper.py --cfg hs_sniper.json --watch     # 有余量就开火
python hs_sniper.py --cfg hs_sniper.json --watch --at 22:59:58   # 定点开火
```

## 接口

| 用途 | 请求 |
|---|---|
| 门禁 | `POST /hsjw/jw/common/getWsxkTimeRange.action?xktype=N` |
| 已选门数 / 学分 | `POST /hsjw/jw/common/getSelectLessonScoreKcsInfo.action` |
| 可选课程表 | `POST /hsjw/taglib/DataTable.jsp?tableId=2548` |
| 开课计划表 | `POST /hsjw/taglib/DataTable.jsp?tableId=5327042` |
| 提交 | `POST /hsjw/STU_PreElectCourseAction.do?hidOption=saveData` |
| 校时采样 | `GET /hsjw/custom/js/SetRootPath.jsp`（读响应头 `Date`） |

`xktype`：实测 `1` = 专业选修预选。其余取值在通知里没有说明，以 `gate` 返回的 `lcmc`（轮次名）为准。

## 五条设计决定，都是实测打出来的

**1. 响应 200 不等于选上了。**
往 `saveData` 提交一个不存在的课程号，服务端照样回 `{"status":"200","message":"操作成功!"}`。
唯一可信信号是回查 `getSelectLessonScoreKcsInfo` 的 `yxms`（已选门数）是否 +1。
`hs_sniper.py` 每次开火后强制回查，不看响应文本。

**2. 校时不能对 NTP，也不要用名字最像的那个接口。**
开闸判定在教务服务器上，对 `ntp.aliyun.com` 校时等于给自己加偏差（实测 NTP 对齐 −315.7 ms）。
`frame/homepage?method=getTempNowtime` 名字最像校时接口，但服务器内部要等 350 ms，采样间隔直接毁掉边界精度。
用 `custom/js/SetRootPath.jsp`（RTT ≈ 25 ms）配合 Date 翻转法：抓 `Date` 从 T 跳到 T+1 的瞬间定位整秒边界，实测 `offset ≈ +424 ms ± 36 ms`，22 ms 的网络 RTT 就是物理下限。

**3. 余量只认 `qdrs`，不要用 `限选 − 已选` 算。**
高数 A(1)-006 是 `限选 135 / 已选 136 / 可选 6`，相减得 −1 会误判已满。`qdrs` 是服务端算好的真实可选数。

**4. 连接预热必须放在实例级，不能放 thread-local。**
否则开火线程取不到主线程建好的连接，现场重新 TLS 握手 66~72 ms。改实例级共享后降到 45 ms。

**5. `items` 里只有课程号，没有教学班号。**
```
items = 0400111101,null,1,true|
         课程号      是否调剂
```
班次由服务端按「限选人数 + 调剂标记」自行分配，`allow_adjust=1` 成功率更高。配置里的 `skbjdm` 只用于筛选盯哪个班的余量，不进提交参数。

## 六个坑

| 坑 | 现象 | 处理 |
|---|---|---|
| body 中文用 UTF-8 编码 | 服务器**静默**返回空表 `total=0`，不报错 | 每个参数按 GBK 编码 |
| 一个教学班占两行（`O` 主行 + `E` 续行） | 一半行余量是空的 | `kc` 为空的续行合并到上一行 |
| 响应是 chunked | 手写解析把长度前缀留在 body 里 → JSON 失败、连接变脏 `WinError 10038` | 用 `http.client` 标准库 |
| 提交接口需要 DES | 多走一整套密钥流程 | **不需要**，`saveData` 是明文表单；只有退选走 DES |
| GBK 死写 `decode("utf-8","replace")` | 中文全变 `\ufffd`，课程名一律 unknown | 按 `Content-Type` 的 charset 降级解码 |
| 表头包含匹配 `课程` 先命中 `课程号` | 课程名取成课程号 | 先精确匹配再包含匹配 |

## 已知缺口

- 第二学期课表可能 `total=0`（开课计划未发布），此时无可抢项，`--scan` 会如实显示 0。
- 退选未实现（需 DES + `getTempDeskey`），网页手动退。
- 已选课程明细未取，只有门数 / 学分。
- 并发保守 3，不建议超 5。

## 边界

只操作自己的账号、自己的选课数据，按正常单人操作节奏发请求。
不做越权（伪造身份 / 教师端 / 篡改学期门禁），不压测，不打登录接口。
