# 跨执行器提交意图保护与华水真实验收门槛

更新日期：2026-11-14。**此文档不代表实际选课成功。**

## 一、统一持久化意图

四组现有代码保持原状：`qiangke/`、`ncwu/`、`kingo-live/`、`clients/windows/` 及 `ops/` 中各自的日志、适配器、FSM、HAR 研究功能保留。本轮增加一个**共同的提交前 SQLite/HMAC 预留**：

- 既有 `qiangke` 和 `ncwu` 已调用 `qiangke.qk.intent_journal.IntentJournal`。
- `kingo-live` 在实际 POST 之前也调用同一 SQLite 预留；原 JSONL 记录继续承担其本地恢复责任。
- Windows 已有 `EnrollmentEngine` 仅在全部真实网关能力门禁开启后，先预留共享 SQLite，再写自身 V2 JSON/HMAC 历史状态并调用 `VerifiedGateway.submit_once`。当前**没有正式华水 VerifiedGateway**。
- `ops.Orchestrator` 在调用自带执行器回调之前，要求显式提供学校域名、官方账号身份、学期和课程/班号并预留共享 SQLite；原 `IntentGate` 仍单独保护进程内重复决策。

默认共用 `~/.kingo-assistant/intent-journal.sqlite3`，环境变量 `KINGO_INTENT_DB` 可以覆盖；`kingo-live.shared_intent_db`、Windows `shared_intent_db` 和 `ops.Mission.shared_intent_db` 用于显式覆盖。**不同路径/电脑不能自动共享；若同一账户跨电脑运行，必须另行实现可靠的中心化服务端互斥。**

SQLite `BEGIN IMMEDIATE` + HMAC 指纹按学校、账号、学期及整个课程锁定；班号提示不同不能当作另一条新提交。保留旧学期写法的指纹兼容及旧历史记录。预留冲突、缺身份、文件异常，均不得执行物理提交。提交后网络异常、进程崩溃、旧日志无法核验时，共享记录保持阻止状态；**不能因为没有在已选列表里立即看到目标，就删除历史记录自动重发**。

### 尚不能据此承诺的情况

1. 仅当每个模块使用**同一真实学校账号身份、相同学期语义、学校域名和 SQLite 路径**，才可证明它们竞争同一个事务锁。`account_scope` 是用户提供的身份声明，不自动证明已登录账号；必须用官方账号信息独立核实。
2. 旧 JSONL、Windows V2 历史日志**没有被自动导入**新的 SQLite。此前用独立日志写过的课程必须先人工核对官方已选状态。代码不会自动清理历史内容。
3. 同一个数据库可以阻止协作代码重复向服务器提交，但**不能保证其他未接入的外部程序**、不同机器或者服务端重复处理不执行。
4. 共享 SQLite 的“pending”是一条**提交意图**，并非已成功选上的证明。只有权威官方已选明细精确匹配课程号＋教学班号才是确认成功。

## 二、华北水利水电大学真实只读核验（仍待现场）

本阶段只允许在本人已登录、本人拥有读取权限的环境中核验，不提交密码、Cookie 或未脱敏 HAR 到 GitHub：

- **官方身份**：账号标识来自服务器授权身份查询而不是配置别名；核对同一账号与学期。
- **窗口**：检查 `/hsjw/jw/common/getWsxkTimeRange.action` 的响应结构和真实状态；HTTP 200 只说明请求成功，不能替代学校窗口状态。
- **课程目录**：只读调用现有 `ncwu/hs_xk.py` 的 `list` 或 `ncwu/hs_kcbcx.py`；核对官方课程号、教学班号、余量字段和 GBK/HTML 行定位。不以 `kcdm`、展示班号推造正式班号。
- **已选明细**：必须拿到官方明确课程号＋教学班号的只读记录；只知道已选门数、学分变化、HTTP 成功文案一律记为 `pending`，不能算作真实成功。
- **实际提交端到端**：需要单独验证学校许可、接口身份、不可重复提交、官方写入窗口与逐班回查。**本次没有对学校发送写请求或运行账号会话**，不能认定端到端成功。

## 三、回归测试范围

`contracts/tests/test_intent_bridge.py`：旧 NCWU 意图与新客户端双向兼容、学期写法兼容、同一课程不同班号、进程内多线程竞争、账号/学校/学期隔离。Windows、`kingo-live`、`ops` 的合成回环测试覆盖各自的真实提交入口对共享 SQLite 的调用；Windows EXE 还需离线打包与 GUI 冒烟。

**最终发布条件：所有 CI Job 通过 + 真实只读样本与官方账号/学期逐字段核验；真实写接口若未验收，必须继续显示“待验收”，而非自动上线。**
