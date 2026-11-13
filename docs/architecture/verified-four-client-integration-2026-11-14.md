# 四套旧执行器统一官方结果核验（2026-11-14）

> 状态：统一证据判定契约已接入；**真实华水已选课程接口尚未验收**，不得标记“真实抢课全自动可用”。这是复用旧代码的阶段性工程集成，而不是新建第五套提交内核。

## 已有模块的唯一职责

| 旧模块 | 保留职责 | 统一结果入口 | 尚未完成 |
|---|---|---|---|
| `clients/windows/source/` | 唯一桌面入口、既有 `enrollment_flow.py` 状态机、持久状态恢复 | `contracts.enrollment_evidence.verify_selection()` | 校方验证后的真实适配器；Windows 自动提交保持关闭 |
| `kingo-live/` | 通用真实 HTTP 客户端、已配置的课程目录与已选课程回查 | `normalized_official_rows()` + `verify_selection()` | 实际学校响应字段、账号/学期来源验证 |
| `qiangke/` | 青果登录、既有课程解析与会话适配 | `pending_receipt()` | 可信官方逐课程只读回查适配 |
| `ncwu/` | 华水门禁、预选目录与已选门数等旧实现 | `pending_receipt()` | **门数不等于明细**；必须得到逐课程官方记录 |

## 单一判定流程

```text
既有适配器正常获取官方只读记录
    -> 验证完整数据来源、登录账号、学期及记录结构
    -> contracts/enrollment_evidence.py：精确课程号 + 教学班号
       -> confirmed: 仅存在完全匹配的官方已选记录时
       -> pending: 已查询完整可信列表但尚无匹配；不代表失败
       -> unavailable: 没有可信接口、账号/学期不符或数据结构异常
    -> Windows 既有状态机和 journal；不额外发送提交请求
```

提交回执、HTTP 200、提示“成功”、课程名、名额、已选门数、课表均不是 `confirmed` 证据。
共享契约是无网络、无账号、无提交动作的纯函数，不是第五套执行器，也不会自动改写旧提交策略。

## 真实 HAR 状态

用户提供的 `jwmis.ncwu.edu.cn_2026_11_14_01_04_37.har` 经本地检查包含 59 条请求，
主要为登录页面和静态资源，**未含已选课程明细响应**。
此文件不在 Git 中。不能据此硬编码官方 `enrollment` 路径。
对于未知接口，客户端拒绝成功判定，不得生成模拟学校数据充当真实数据。

### 第二份 HAR 的真实只读证据（2026-11-14）

`jwmis.ncwu.edu.cn_2026_11_14_01_15_06.har` 含 **112 条请求**，只将脱敏后的结构结论写入本文件，不上传 HAR/Cookie。

- `POST /hsjw/jw/common/getSelectLessonScoreKcsInfo.action`：返回 `status/message/result`，内部包含 `yxms/yxxf` 等已选门数和学分；不含“课程号＋教学班号”明细。
- `POST /hsjw/jw/common/getWsxkTimeRange.action`：内部包含账号、学年、学期与时间窗字段；可辅助确认会话范围，但不是已选列表。两次实际响应业务状态均为 `400`，`isValidTimerange=0`，说明所采集选课类型的时间窗未开放。
- `POST /hsjw/taglib/DataTable.jsp?tableId=2549`：本次表头包括课程代码、课程、教师及人数；`showTotalRecord('2549','0')`，没有记录或教学班号证据。
- `POST /hsjw/taglib/DataTable.jsp?tableId=5036`：本次表头包含课程代码、状态和课程名称等；`showTotalRecord('5036','0')`，没有课程明细行或教学班号证据。
- `POST /hsjw/student/wsxk.yxjg_report.jsp`：统计报表，不包含可核实的目标课程＋教学班记录。
- `POST /hsjw/jw/common/showYearTerm.action`：包含学期字段，不是官方逐课程选课记录。

**安全边界：**有 107 条请求携带 Cookie 请求头，并捕获过 `/hsjw/cas/logon.action` 登录接口；这份 HAR 属于敏感会话数据，禁止公开上传、加入 PR、CI 或版本历史。

**结论：**第二份 HAR 提供了真实菜单、门禁和汇总结构，但采集时选课时间窗未开放；表格零记录不能解释为“学生未选课”，更不能证明具体教学班选中或失败。仍缺少经过业务语义确认的完整、绑定账号和学期的官方已选课程列表。任何真实接口适配器必须继续保持未验证状态，不能硬编码一个“成功”判据。

## kingo-live 闸门

示例 `kingo-live/config.example.json` 的
`enrollment.official_verified` 默认为 `false`，`account_scope`、`semester`、
`response_account_path`、`response_semester_path` 均为空。
启用前必须分别确认官方完整已选课程接口、登录账号字段和学期字段。
旧通用引擎在**任何实际提交前**执行一次只读已选课程查询，
从响应体提取账号与学期，并与预期值完全比较；缺失、解码失败、不同账号或不同学期均拒绝提交。
本阶段只支持可直接核实作用域字段的 JSON 响应，未知 HTML 响应不推断成功。
**在配置里填写 true 并不是官方接口验收证明**：仍需实际核对服务器返回的是本人账号、
当前学期且完整的已选课程列表，而非课表、目录或登录成功页面。
回环测试独立使用虚构范围，不证明生产系统可用。

## 保留、未完成和禁止误报的边界

1. Windows 仍保留 `VerifiedGateway`、`AttemptJournal`、`run_enrollment`、
   `reconcile_pending`；桌面 GUI 尚未启用真实写入。
2. `qiangke` 和 `ncwu` 保留原有文件与功能，没有替换成新执行器。
3. 四套代码此前使用不同的意图记录格式；本轮**尚未统一为一份跨所有引擎的原子日记账**，
   不能声称全局防重复已完成。禁止同时运行多套写入客户端。
4. `kingo-live` 仅在已核验完整官方记录的条件下可进入 `confirmed`；
   不完整、延迟或无法读取的结果保留待核验。
5. 新证据契约不能自行证实服务器内容的真实性；验证责任在具体只读适配器。
6. 尚需完成：学校真实 read-only 响应模式测试（脱敏）、统一课程身份/学期标准、
   跨执行器 journal 迁移与恢复、Windows 正式适配、真实只读联调验收。

## 验收方法

- `python -m unittest discover -s contracts/tests -q`
- `cd kingo-live && python -m unittest test_intent_safety test_request_policy -q && python selftest.py`
- `cd clients/windows/source && python -m unittest discover -s tests -q`
- GitHub Actions 的 Windows EXE 只是离线打包/冒烟，不是教务真实提交验证。

**隐私要求**：HAR 中的 Cookie、Authorization、Token、学生身份、完整响应和请求体不得进 Git、PR、Issue 或 CI 日志。
