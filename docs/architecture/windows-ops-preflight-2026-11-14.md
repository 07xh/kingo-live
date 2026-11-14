# Windows ↔ ops target preflight — 2026-11-14

## Current integrated behavior

- The existing Windows course-plan preflight now calls the existing `ops.adapter.normalize_target` implementation (no cloned adapter).
- Offline mapping retains exact course ID, teaching-class ID, semester, preference order and alternative group.
- The same path is included in the PyInstaller bundle and checked with the packaged Tk smoke test.
- The existing `ops.executor_bridge.receipt_to_evidence` now treats every executor receipt as transport context only; no return code can become official enrollment proof.
- No HTTP request, browser click, automatic school login or school submission occurs as a result of this new preflight.

## Remaining work before automatic execution can be enabled

1. Rebase/resolve the separate PR #34 unified persistent intent changes against current `main`, retaining the later Windows history scope-binding fixes.
2. Implement and independently verify a school-specific `VerifiedGateway` feeding the existing `core.enrollment.EnrollmentEngine`; do not treat generic `qk` or `ncwu` response text as official success.
3. Obtain verified account and semester scope from an authenticated official read-only endpoint and compare it with the shared journal.
4. Confirm real catalog/teaching-class identities, official selection window and exact selected-course readback.
5. Wire only the reviewed existing gateway through `Application.install_verified_engine_factory`; never set `_engine_factory` from a local plan or simulation.
6. Run the whole Windows packaged-EXE test on a real Windows machine and separately validate the school integration in a permitted selection window.

## Result semantics

- `unsent`: no physical write was attempted.
- `unknown`: request may have been sent; stop and retain pending journal for official readback.
- `confirmed`: only fresh, exact official selected-course evidence for the verified account/semester can establish this.

This is incremental integration, **not** a claim that the Windows GUI can submit live enrollments.
