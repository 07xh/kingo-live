# kingo-live

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

## Run

```bash
python -m py_compile kingo-live/*.py bench/*.py
cd kingo-live && python selftest.py && python test_optimizations.py && python cli_smoke.py && python har_to_schema.py --self-test
cd ../bench && python academic_latency_sim.py --self-test && python academic_latency_sim.py --all
```

Zero third-party dependencies, Python 3.10+. See `kingo-live/README.md` for the real-system procedure.

## Status

All verification so far runs against loopback HTTP targets and offline simulation. **No request has been sent to a real enrollment system.** The remaining gap is the real schema (`--probe` body or a browser HAR) and a real session. Submission requires an explicit `--confirm` and success is only counted after an official enrollment-list readback.

Cookie is injected through the `KINGO_COOKIE` environment variable; it is never written to disk or to this repository.
