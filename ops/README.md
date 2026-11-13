# ops/ — 只读侦察 + 名额事件驱动 + 服务端规则判定

三个独立模块，**不改动** `clients/windows/source/`、`kingo-live/`、`qiangke/`、`ncwu/` 四套现有实现。零第三方依赖，Python 3.10+，标准库。

| 文件 | 职责 | 对应上一轮分析的面 |
|---|---|---|
| `recon.py` | 五组只读 GET：后端节点、协议/时钟/排队头、N 次时钟采样与秒边界相位、路径变体、窗口判定归属 | #1 #2 #4 #6 #8 #9 |
| `seat_events.py` | 自适应轮询（静默 60s → 事件窗 2s）、快照差分事件、动态备选重排、单目标一次物理提交闸门 | #3 #12 #13 |
| `p5_probe.py` | 受控观测 → 分配规则 `fcfs/random/priority` 与限流维度 `ip/session/account/node` 判定 → 具体策略值 | #1 #2 |
| `test_ops.py` | 回环 HTTP 靶标上的 65 项离线回归 | 验证 |

## 跑起来

```bash
cd ops
python test_ops.py -v                 # 65/65 OK
python p5_probe.py                    # 打印规则判定示例
python seat_events.py                 # quiet -> submit -> duplicate blocked

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
