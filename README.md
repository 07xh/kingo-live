# KingoAssistant · private integration

Technical research repository: a real HTTP client for the Kingo/NCWU course-enrollment flow, plus an offline latency simulator used to derive and falsify its conclusions.

**Private repository. No license selected.** Public visibility is not a grant to reuse. No real credentials, cookies, HAR captures, or live configuration are committed.

## Layout

| Path | What it is |
|---|---|
| `qiangke/` | 独立 Python 实现（零依赖）：青果本地登录 + 8 个选课流程时间窗 + DataTable 选课链路，与 `ncwu/`、`kingo-live/` 并存不互相依赖。见 `qiangke/README.md` |
| `kingo-live/README.md` | Full engineering doc: parameter ranges, placeholder discovery, verification criteria, CLI reference |
| `kingo-live/live_client.py` | Real `http.client` transport, request budget, submission, official enrollment-list readback, CLI |
| `kingo-live/optimizations.py` | Clock sync, session prewarm, release-event monitor, failure classifier, failover planner |
| `kingo-live/har_to_schema.py` | Browser HAR -> config field mapping (with credential redaction) |
| `kingo-live/config.example.json`, `capture.example.har` | Templates and synthetic fixtures only |
| `kingo-live/selftest.py`, `test_optimizations.py`, `cli_smoke.py` | Real loopback HTTP/1.1 end-to-end tests |
| `bench/academic_latency_sim.py` | Offline four-stage latency simulation, allocation rules, lock modes, P5 sensitivity matrix |
| `bench/p5-results.json`, `sim-results.json` | Recorded measurement output |
| `ncwu/README.md` | Live-endpoint notes: the six API paths, clock sync, five design decisions, six pitfalls |
| `ncwu/hs_kcbcx.py` | Offline-capable course-plan query (`tableId=5327042`) with CSV export and remainder watch |
| `ncwu/hs_xk.py` | Gate probe (`getWsxkTimeRange`), available courses (`tableId=2548`), submission (`--dry-run` supported) |
| `ncwu/hs_sniper.py` | Enrollment engine: gate → clock sync → remainder watch → prewarm → concurrent submit → readback verify |
| `ncwu/sniper.example.json` | Template only; cookie is empty and injected via `KINGO_COOKIE` |

## 本轮审查与执行语义

见 [2026-11-13 审查报告](docs/audit-2026-11-13.md)。提交回执不等于选中；`qiangke`/`ncwu` 缺少逐课程官方核验时显示待核验并停止重发。通用客户端每意图只写一次、发送前持久落盘、重启保留 pending；同目标并发/重复策略不再复制 POST。Windows 自动提交尚未接通，真实华水端到端仍待验收。

## Run

```bash
python -m py_compile kingo-live/*.py bench/*.py ncwu/*.py
cd kingo-live && python selftest.py && python test_optimizations.py && python cli_smoke.py && python har_to_schema.py --self-test
cd ../bench && python academic_latency_sim.py --self-test && python academic_latency_sim.py --all
```

Zero third-party dependencies, Python 3.10+. See `kingo-live/README.md` for the real-system procedure.

## Status

All verification so far runs against loopback HTTP targets and offline simulation. **No request has been sent to a real enrollment system.** The remaining gap is the real schema (`--probe` body or a browser HAR) and a real session. Submission requires an explicit `--confirm` and success is only counted after an official enrollment-list readback.

Cookie is injected through the `KINGO_COOKIE` environment variable; it is never written to disk or to this repository.

## Consolidated Windows client (offline/readonly integration)

The historical Windows Tk desktop source and tests from
[07xh/kingo-assistant](https://github.com/07xh/kingo-assistant)
at commit `2ab31adb212d134be0ec050d9404d2a7a4dac4d7`
are preserved in `clients/windows/source/` in this **private** repository.

- Start: `cd clients/windows/source && python windows_gui.py`
- Main UI: plans, read-only monitor, offline demo, official-site appointment, tray.
- **New integration:** Tools → "Kingo Live HAR 证据交叉核验（离线）".
  This invokes the same repository's `kingo-live/har_to_schema.py` through
  `core/kingo_live_bridge.py` and displays numeric evidence counts only.
- No cookies, HAR contents, response bodies, query strings, course IDs or
  student identities are displayed by the integrated tool.
- The live HTTP submission module is **not imported** into the Windows GUI.
  The existing school-specific write adapter is intentionally absent and
  the GUI's automatic-submit start remains disabled.
- Source CI additionally runs native Windows Tk tests and bundles the shared
  offline analyzer as a PyInstaller data file. Only passing CI plus an
  actual Windows test can establish whether the executable launches.
- Original CLI research code and its loopback tests remain in `kingo-live/`.
  Real NCWU permissions, endpoints, and live submissions are not verified.

See `docs/integration-windows-readonly.md` for scope and verification.

## Legacy Windows HAR draft support (synchronized 2026-11-13)

The Windows client also retains `clients/windows/source/core/har_to_schema.py` from the old `kingo-assistant` repository. It creates an **offline, unverified** protocol draft for the Windows read-only client; it is distinct from the research `kingo-live/har_to_schema.py` schema. The restored `core/har_schema.py` provides fixed-label read-only JSON shape signals without replay or raw request export. Both have dedicated unit tests, and no automatic enrollment submission is enabled in the GUI. See `docs/legacy-windows-sync-2026-11-13.md`.

## Windows course-plan CSV snapshot viewer

From the Windows desktop use **离线工具 → 查看开课计划 CSV（非实时）** to inspect a local CSV exported by `ncwu/hs_kcbcx.py --csv`. The viewer uses only the user-selected UTF-8/UTF-8-BOM file; it never connects to the school, writes enrollment requests, changes the user's plans, or persists session credentials. An absent `可选人数` stays **未知**, never inferred from capacity minus enrollment. The viewer must not be interpreted as live availability; comparing with the official page is still required.

## Legacy Windows documentation and read-only diagnostics

The existing Windows UI now displays per-cycle query duration, HTTP count, last
response status/size, and a transport-error category without logging URLs,
cookies or response contents. No extra polling request is added.
Historical design and acceptance material from the public Windows predecessor
is archived in [docs/legacy-kingo-assistant](docs/legacy-kingo-assistant/README.md).
Those files are **past-state evidence**, not current school acceptance.

## Unified read-only course data contract (v1)

`contracts/offering.py` provides pure, non-network normalization of course
records from `ncwu/`, `qiangke/` and the Windows read-only client. The
contract never infers missing seat counts or claims current availability;
existing adapters remain unchanged until compatible consumers are verified.
See [contract and rollout](docs/architecture/course-offering-contract-v1.md).

## NCWU shared-contract read-only export

`ncwu/hs_kcbcx.py --csv` now uses the shared offering model while preserving
the existing Windows-compatible CSV columns. Optional `--standard-json path`
produces normalized course-only snapshots with a client observation timestamp.
The old `--json` format stays available.
