# ops/ integration audit — 2026-11-14

Scope: `ops/`, `kingo-live/`, `qiangke/`, `ncwu/`, and `clients/windows/source/` at main baseline `4172a4d`. Source review only; no live school requests, no release build certification.

## Decision: no fifth enrollment executor

- **Single write state authority (Windows):** `core/enrollment.py::AttemptJournal` and `core/enrollment_flow.py::run_enrollment`. The journal stores an account/semester-scoped HMAC fingerprint, persists a `pending` reservation **before** the physical write, and holds an OS process lock. `core/enrollment_recovery.py::reconcile_pending` confirms only after matching an independently verified official enrollment record. Never infer success from transport acknowledgements.
- **Not an alternate authority:** `ops/seat_events.py::IntentGate` stores plain course/class keys in memory only. It does not persist across restarts, does not scope by account and semester, and previously `reconcile(False)` labeled `rejected` even when a missing readback was inconclusive (corrected on this branch to remain `pending`). Never wire this gate's `issue`, `reconcile` or `EventDriver` to `submit_once`.
- `qiangke/qk/engine.py` uses its own retry loop/status output and `ncwu/hs_xk.py` includes a repeat option and response-string classifier. These legacy paths are not equivalent to the Windows durable journal; do not silently route production enrollment through them.
- `kingo-live/optimizations.py::FailureClassifier` may remain an offline classification utility; it cannot override Windows journal/reconciliation evidence.

## Read-only data boundary

- `contracts/offering.py::CourseOffering` is the canonical cross-client **read-only** snapshot contract. It keeps `remaining=None` as unknown, keeps course and teaching-class IDs separate, and records an explicitly supplied observation timestamp.
- `ops/seat_events.py::Offering` is a separate experimental shape (capacity, heat, `remain=0` default). It loses the unknown/zero distinction. Do not pass canonical rows to `EventDriver` by filling unknown fields with zero; do not let its emitted `submit/fire` decision authorize any POST.
- For any future adapter, accept the canonical contract, reject missing course/class identity, preserve `None`, and limit output to *read-only informational* release events. Tests must cover malformed, absent, ambiguous and stale snapshots before enabling a consumer.

## Two-second monitoring policy

- `ops/config.example.json` has `hot_interval_ms=2000` for the offline event model.
- Windows `core/monitor_policy.py` now accepts `interval_range: [2, 2]` **only** when a source-bearing `official_frequency_policy` is explicitly confirmed. Unconfirmed runs retain the 60 s default floor.
- All real requests still pass through the independent `core/request_budget.py::GlobalRequestBudget` (shared, persistent, sliding window); a 2 s local interval setting does not certify permission or guarantee an actual 2 s network cadence. A 429 or other server cooling signal takes precedence.
- Do **not** lower or bypass the shared request budget or fabricate a published permission source.

## Bounded acceptance plan

1. Offline: run the existing Windows monitor-policy, request-budget, enrollment journal/recovery, canonical snapshot and `ops/` test suites. Confirm 2 s selected policy passes only with explicit verification, 1.9 s rejects, unverified 2 s rejects.
2. Integration: compare the existing read-only parser outputs to `contracts/offering.py`; reconcile exact course ID, class ID, nonnegative/unknown remaining values and observation timestamps. Do not install a new crawler, new session manager or submission engine.
3. On institutionally authorized access: verify the real **read-only** catalog and server-exposed request policy at the official source using the user's own account. Redact credentials, cookies, tokens, full HAR and personal enrollment details. Never claim current availability from an offline fixture.
4. Only after offline and live read-only evidence is collected, review whether a read-only event adapter is useful; continue using the Windows journal as the only submission authority.

## Open blockers

- No live NCWU course catalog acceptance in this audit.
- No confirmed independent evidence of the institution's request-rate limits supplied in the repository.
- No full Windows EXE runtime or packaged GUI acceptance in this audit.
- `ops/IntentGate` remains an offline experiment and must not be treated as durable state.
