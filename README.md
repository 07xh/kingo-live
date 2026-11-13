# KingoAssistant · private integration

Technical research repository: a real HTTP client for the Kingo/NCWU course-enrollment flow, plus an offline latency simulator used to derive and falsify its conclusions.

**Private repository. No license selected.** Public visibility is not a grant to reuse. No real credentials, cookies, HAR captures, or live configuration are committed.

## Layout

| Path | What it is |
|---|---|
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
