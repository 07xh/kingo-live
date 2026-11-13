# 提交身份、日志范围与核验证据审计（2026-11-14）

> 静态代码审查 + 本地 / GitHub Actions 离线回归；**没有执行华水真实登录、选课提交或学校端回查**。
> 本次保留 `clients/windows/`、`kingo-live/`、`qiangke/`、`ncwu/`、`ops/` 的现有能力，不删除执行器。

## P0：课程与教学班身份

- `qiangke/qk/kingo_xk.py::class_code` 只接受明确的 `skbjdm`；课程号 `kcdm` 和展示用班号 `skbh` **不再冒充权威教学班 ID**。
- `kingo-live/live_client.py::_readback` 要求课程号、教学班号同时非空且精确匹配；`reconcile()` 跳过缺少任一字段的官方记录。
- `ops/crosscheck.py::fuse` 在具体教学班的确认上，只接受**新鲜、官方、课程+班号精确匹配**的已选明细。学分总数、课表和多次重复读取只能辅助诊断；同一来源重复行最多计一次，相互矛盾的记录转人工复核。
- 现存日志中的 `confirmed` 状态不自动修改或清除。旧日志的来源证据仍需人工检查。

## P1：提交意图与状态的适用范围

| 模块 | 本地存储 | 身份范围 | 本轮处理 | 不应误认为 |
|---|---|---|---|---|
| `qiangke` | SQLite `intent-journal.sqlite3` | 学校域名、账号、学期、课程（有课程号优先锁整门课） | 对 `YYYY/1`、`YYYY-1`、`YYYY,1` 等明确同义写法做新旧 HMAC 指纹查重；保留旧库 | 不同文件名的日志自然互通 |
| `ncwu/hs_xk.py`、`hs_sniper.py` | 同一 SQLite 路径（可用 `KINGO_INTENT_DB` 指定） | 同上 | 使用相同 `IntentJournal`；本轮兼容不同分隔符和旧班号指纹 | 学年编号 `2026` 和 `2026-2027` 一定同义 |
| `kingo-live` | JSONL + .key + 新增 .scope | 原有课程+教学班 HMAC；新增学校、账号、学期的**文件级作用域 HMAC** | 写操作前要求配置 `account_scope`、`xnxq` 和 `base_url`；同路径不同身份拒绝执行；旧非空无作用域日志禁止静默写入 | 与 SQLite 共用数据库或互相自动去重 |
| Windows GUI / EnrollmentEngine | V2 JSON/HMAC Journal | 账号范围、学期、课程号、教学班号 | 保留现有隔离和官方逐课程回查；尚无正式 VerifiedGateway | 已完成真实学校写接口 |

### 旧日志升级边界

- SQLite：既有 `attempts` 不删除、不清空；更换学期分隔符不能绕过旧意图保护。能确定**网络写入前就失败**时，`qiangke` 可撤销刚预留的该条意图；超时、丢响应、异常不可证明未发送时不得撤销。
- `kingo-live`：首次新文件写入前创建 `.scope` 文件，只写密钥 HMAC，不写学号。旧 JSONL 非空且没有 `.scope` 时，要求先人工核验和制定迁移方案；**不能删除旧日志后当成全新提交**。
- 目前不存在横跨 SQLite、JSONL 和 Windows V2 JSON 的**统一跨进程物理写互斥机制**。不能同时开启不同执行器对同一目标进行写提交，也不能把不同日志误认为已全局互通。该边界在接入正式学校适配器前仍需单独验收。

### 回执分类

- `write_attempted = False`：登录失败或本地字段不完整，**明确未调用网络提交**；输出 `attempts = 0`。
- `write_attempted = True`：已调用网络写传输；回执无论“成功”或 429，在缺乏精确官方记录时都属于待核验。
- `write_attempted = None`：旧适配器、意外异常或发送阶段不明；**不应假定安全重发**。
- 账号和学期在提交后不应从 Cookie 内容或课程名称推断。用户配置的 `account_scope` 需要与真实登录身份进行独立核对，配置字符串本身并非官方账号认证。

## P1：CI

- 在 Ubuntu/Windows Python 3.12 的 verify job 中增加编译 `ops/*.py`。
- 同时运行 `ops/test_ops.py`、`ops/test_fingerprint.py`、`ops/test_phase2.py`。
- Windows 的 PyInstaller 命令与本地 `build-windows.bat` 均显式收集共享 `contracts.har_core` 和 `contracts.seat_diff`，保留原生 Tk 冒烟验收。
- 所有测试只针对离线样本或 127.0.0.1 靶标；不得以 CI 绿色宣称学校服务器已验证。

## P2：重复代码

- 两套 `har_to_schema.py` 共同调用 `contracts/har_core.py` 的纯解析与脱敏函数；协议推断、评分与输出模式仍按各自职责保留。
- `ops/seat_events.py` 和 `kingo-live/optimizations.py` 共同调用 `contracts/seat_diff.py`；`None` 不等于 0，禁止凭未知值产生“名额释放”事件；前者仍独有减少事件、动态排序与 EventDriver。
- `ops/IntentGate` 仍是进程内状态机，不替代上述持久化日志；空回查保持 `pending`，完整 `Retry-After` 不因本地轮询上限被截短。

## 当前验收结论

已覆盖的内容为**源码契约与离线回归**，不是实际选课可用性。学校真实接口、账号/学期来源、官方选课窗口、提交请求身份与真实逐班回查仍未完成现场验收；无法据此宣称“抢课成功率”“比人更快”或“全自动上线”。
