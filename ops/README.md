# ops/ — 只读侦察 + 名额事件驱动 + 服务端规则判定 + 指纹一致性

五个独立模块，**不改动** `clients/windows/source/`、`kingo-live/`、`qiangke/`、`ncwu/` 四套现有实现。零第三方依赖，Python 3.10+，标准库。

| 文件 | 职责 | 对应上一轮分析的面 |
|---|---|---|
| `recon.py` | 五组只读 GET：后端节点、协议/时钟/排队头、N 次时钟采样与秒边界相位、路径变体、窗口判定归属 | #1 #2 #4 #6 #8 #9 |
| `seat_events.py` | 自适应轮询（静默 60s → 事件窗 2s）、快照差分事件、动态备选重排、单目标一次物理提交闸门 | #3 #12 #13 |
| `p5_probe.py` | 受控观测 → 分配规则 `fcfs/random/priority` 与限流维度 `ip/session/account/node` 判定 → 具体策略值 | #1 #2 |
| `fingerprint.py` | HAR 脱敏基线（有序 Header、伪头剥离、URL 令牌清洗）、ClientHello→JA3、有序重放、差异判定、门槛评估 | #14 #4 #8 |
| `fingerprint_bridge.py` | CDP 真实浏览器通道（最小 WebSocket 客户端，无第三方依赖）+ mitmproxy 注入 addon | #14 #5 |
| `test_ops.py` | 回环 HTTP 靶标上的 67 项离线断言（需运行验证） | 验证 |
| `test_fingerprint.py` | 回环靶标上的 113 项指纹回归 | 验证 |

## 跑起来

```bash
cd ops
python test_ops.py -v                 # 67 项离线断言；以实际运行结果为准
python test_fingerprint.py -v         # 113/113 OK
python p5_probe.py                    # 打印规则判定示例
python seat_events.py                 # quiet -> submit -> duplicate blocked
python fingerprint.py --demo          # 差异判定 + 门槛判定示例
python fingerprint_bridge.py --selftest

# 只读侦察（不接触任何写接口）
export KINGO_COOKIE='<登录后 F12 任一请求的 Cookie 头>'
python recon.py --host jwmis.ncwu.edu.cn \
  --path /hsjw//wap/mycourseschedule.action \
  --samples 9 --clock-header '' --json recon.json
```

`recon.py` 只发 GET。`--clock-header X-Server-Time` 可切到毫秒头；`--bind-ip` 配合 `--host` 做后端节点钉住；`--variants` 自定义路径变体；`--window-param/--window-value` 测窗口判定是否吃客户端输入。报告里不含 Cookie 值，只有是否注入的布尔标记。

## 数值

| 参数 | 主值（范围） | 依据 |
|---|---|---|
| `recon.samples` | 9（5–15） | `Date` 仅秒精度，用 RTT 最优半样本中位数拟合秒边界相位；5 次只够中位数 |
| `recon.interval_ms` | 200（0–1000） | 覆盖 RTT 抖动，9 次采样总计约 1.8s，不触发限流 |
| `recon.timeout` | 3.0 s（1–15） | 校园网 RTT 18 ms × 约 150 倍余量 |
| `events.base_interval_ms` | 60000（500–60000） | 沿用 `config.example.json` 的客户端监测下限 |
| `events.hot_interval_ms` | 2000（500–10000） | 退课补位与批量释放常在秒级；事件窗内加密采样才有意义 |
| `events.hot_rounds` | 10（3–30） | 事件后持续 20s 高密度窗口，覆盖一次批处理落地 |
| `events.cooldown_ms` | 30000（1000–120000） | 429 后按 `Retry-After` 或保守冷却 |
| `events.backoff_ms` | 200（50–2000） | 503 退避，不叠加到服务端排队 |
| `ranker.w_remain` | 0.5（0–1） | 余量/容量比是竞争度主因子 |
| `ranker.w_heat` | 0.3（0–1） | 历史热度修正，冷门班优先 |
| `ranker.w_priority` | 0.05（0–0.2） | 静态 priority 只做每级扣分，不否决动态余量 |
| `strategy.concurrency` | 1（1–16） | 默认串行；并发在自建 P5 矩阵所有规则下都是最差列 |
| `strategy.sessions` | 1（1–8） | 先由 `classify_rate_dimension()` 判定限流键再动 |
| `single_physical_submit` | true（固定） | 意图幂等；`pending` 只走官方名单对账，不自动重试 |

## 判据

| 观测 | 结论 | 下一步 |
|---|---|---|
| `nodes.multi_node=true` | 后端多 A 记录 | 每个 Transport 钉住一个解析 IP，`Host` 头保持原值，逐节点测限流 |
| `transport.queue_ms` 有值 | 服务端暴露排队耗时 | 用 `Server-Timing` 分离 queue/tx，定位真正瓶颈 |
| `clock.precision_ms=1000` | 只有 `Date` 秒头 | 用 `second_phase_ms` 对齐秒边界；不编造毫秒精度 |
| `clock.precision_ms=1.0` | 有毫秒时间头 | 把 `config.clock.header` 改成该头 |
| `variants.divergent=true` | 路径变体状态/响应体不同 | 对每个变体单独测窗口与限流，可能对应独立入口 |
| `window.client_input_affects_gate=true` | 窗口判定吃查询参数 | 把完整参数集映射出来，逐项确认服务端是否真校验 |
| `classify_allocation=hint=fcfs` | 相对位次是唯一杠杆 | 开 `prebuild`、对齐服务器秒边界、`concurrency` 保持 1 |
| `hint=random/priority` | 提速是噪声 | 把力气放在目标竞争度与名额释放瞬时，不调并发 |
| `classify_rate_dimension=hint=session/ip/node` | 限流按该维度分桶 | 按维度扩 `sessions` 或多出口；`account` 则无收益 |

## 与现有模块的对接点

- `seat_events.ReleaseMonitor.observe(offers, poll_cost_ms)` 与 `optimizations.py::ReleaseMonitor` 同签名，可直接替换；多出 `decreased` 事件（只记录、不触发提交）。
- `Offering` 带 `capacity` 与 `heat`；`optimizations.py::Offering` 没有这两个字段时，`TargetRanker` 会退化成只用余量（capacity=0 → ratio=0），不会抛异常。
- `IntentGate` 对齐 `core/enrollment.py` 的「提交前持久化意图 + 官方已选名单回读」：`issue()` → `mark_pending()` → `reconcile(official_hit)`，失败只允许走 `FailureClassifier` 的 `next`，不允许同一目标二次 `issue()`。
- `p5_probe.recommend_strategy()` 的输出直接对应 `config.example.json` 的 `strategy.*` 键，可 `--set` 覆盖。
- `recon.py` 的输出是 `har_to_schema.py` 之外的第二条取数路径：先确认端点与响应头，再填 `catalog/submit/enrollment` 三块契约。

## 证据链（来自可访问仓库 `07xh/kingo-assistant`）

- `tools/kingo-live/README.md`：P5 矩阵（`fcfs` +1.00 / `random` +0.07 / `priority` +0.00；并发 0.09–0.12）、`Date` 秒精度、四段延迟定义、策略开关真实生效位置。该矩阵是**自建仿真**，本模块把它变成可证伪的实测。
- `tools/kingo-live/config.example.json`：`strategy` 的保守默认值与全部占位符。
- `docs/acceptance/ncwu-xiqueer-har-2026-11-11.md`：`jwmis.ncwu.edu.cn` 的 `/hsjw//wap/mycourseschedule.action`（双斜杠 wap 通道，课表不是目录）、`api.xiqueer.com` `/manager//wap/wapController.jsp` 签名 POST（`xqerSign`/`encrptSecretKey`/`param`/`param2`/`token`）、`prohibit:"1"` + 「当前时间不是有效的选课时间区段!」。
- `docs/acceptance/official-selection-window-evidence-2026-11.md`：2026-11-09~13 专业选修预选、2026-12-21~25 公选/体育选课；日期区间不等于个人账户窗口状态。

## 边界

`recon.py` 不写任何提交端点；`seat_events.py` 不发起网络请求，只输出 `Decision`，真正的 POST 仍由现有执行器负责。三接口契约（`catalog/submit/enrollment`）在当前仓库仍是 `TARGET_*` 占位符，窗口外抓不到提交端点时不得猜测写接口。

---

## #14 指纹一致性（本轮新增）

**为什么先做这条**：网关若有 JS 挑战 / 设备指纹 / TLS 指纹校验，裸 `http.client` 在第一道就被拒，前面 13 条时序与并发优化全部归零。先证伪这道门，再谈提速。

```bash
# 1) 真实浏览器 HAR -> 脱敏基线（Cookie/Authorization/URL token 全部只留长度+摘要）
python fingerprint.py --har capture.har --url-contains /hsjw// --out fp.json

# 2) 对齐客户端发一次只读 GET，回环靶标回显时自动比对 Header 顺序
python fingerprint.py --baseline fp.json --host jwmis.ncwu.edu.cn --probe \
  --json fp-report.json

# 3) 从抓包拿 ClientHello 十六进制 -> JA3 字符串与 MD5
python fingerprint.py --ja3-hex 16030100...

# 4) 实机观测：同一 URL 裸客户端 403 / 浏览器 200 -> 判定必须走浏览器通道
python fingerprint.py --baseline fp.json --raw-status 403 \
  --browser-status 200 --js-challenge --json gate.json

# 5) 门存在时改走真实浏览器（Chrome 需 --remote-debugging-port=9222
#    --remote-allow-origins=*，标签页已登录）
python fingerprint_bridge.py --cdp http://127.0.0.1:9222 \
  --url-contains /hsjw//wap/ --url https://TARGET_HOST/hsjw//wap/... --probe
```

### 实现要点

| 点 | 做法 |
|---|---|
| Header 顺序 / 大小写 | `putrequest(skip_host=True, skip_accept_encoding=True)` + 逐条 `putheader`，避免 stdlib 注入 `Host` 与 `Accept-Encoding: identity` |
| HTTP/2 HAR | 剥离 `:method` / `:authority` 等伪头，由 URL 合成 `Host` 插到首位；ALPN 由 `response.httpVersion` 推断并标注为推断值 |
| 凭据 | 基线里 Cookie/Token 只存 `<redacted len=N sha256=8hex>`；发送时从 `KINGO_COOKIE` 注入，报告里只留 `<injected>` |
| TLS | `TlsProfile` 设 cipher 顺序 / ALPN / ticket；`SessionHTTPSConnection` 覆写 `connect()` 把 `session=` 传给 `wrap_socket` 做会话复用（需真 TLS 对端验证）；JA3 从真实 ClientHello 解析并过滤 GREASE，**不从 `get_ciphers()` 猜** |
| CDP 通道 | 自写最小 RFC6455 客户端（`socket` + `struct` + `base64`），`Runtime.evaluate` 在页面上下文 `fetch(..., credentials:'include')`，Cookie/UA/JA3/JS 令牌全部浏览器原生 |
| mitmproxy addon | duck typing，导入时不依赖 mitmproxy；恢复基线 Header 顺序、保留实时 Cookie、改写 `kcid`/`skbj` |
| 签名 POST | 含 `xqerSign`/`encrptSecretKey` 的请求**在出口改写会让签名失效**，addon 只记录并跳过，必须回到 App 的参数构造点 hook |

### 判据

| 观测 | 结论 | 动作 |
|---|---|---|
| `raw_status∈{400,403,406,412,418,429,451,503}` 且 `browser_status=200` | 指纹/JS 门存在 | `verdict=use_browser_channel` → CDP |
| `diff.critical` 含 `header_order` / `missing:user-agent` / `value:user-agent` / `cookie_name_order` / `alpn` / `ja3` | 对齐不足 | `verdict=align_required`，逐项修 |
| `probe_alignment.aligned=true` | 有序复刻通过（回环实测） | 才可继续用裸客户端做 #1–#13 |
| `captcha=true` | 无纯技术路径 | `verdict=human_required`，停机 |
| 无 `browser_status` | 证据不足 | `verdict=unknown`，不猜 |

### 边界与实测缺口

- 两个模块默认只读；任何非 `GET/HEAD/OPTIONS` 需显式 `allow_write=True`，CDP 与 addon 同样执行。
- `test_fingerprint.py` 的 113 项全部在**本机回环靶标**上跑：验证 Header 顺序、脱敏、JA3 解析、门槛判定、写保护、addon 逻辑。**没有**连学校主机、没有真 TLS 握手、没有真 Chrome 会话、没有签名 POST。
- 真实 HAR、真实 ClientHello、真实 `raw_status/browser_status` 仍是 `TARGET_*` 占位符；拿到后按上面 4 步回填，判据不变。
- `#5` 的 Frida 钩点 / jadx 签名函数定位**本轮未做**：签名通道在出口改不动，必须先定位参数构造点，属下一轮独立工作。
