# Node client integration audit — 2026-11-14

> Scope: `kingosoft-node/` on main as of `bd618ec`. This review is offline and deliberately preserves every line of the original Node source. It does not authorize real enrollment submissions or probe other students' data.

## What exists, and what is **not** unified

| Concern | Current Node implementation | Required before claiming shared execution |
|---|---|---|
| Authenticated identity | `paoke.mjs` imports a local HAR session; course/term identifiers are read from that session | Confirm school, account and term via fresh authenticated official readback; keep opaque identifiers as strings |
| Exact course/class | Both `kcdm` and `skbjdm` appear in selection preparation | Preserve exact course + teaching-class identity across every hop; do not infer from display name or fuzzy prefix |
| Transport reply | `verdict()` maps remote text/status to `OK` and other codes | Treat all submit replies as transport-only; do not mark official enrollment confirmed without exact official selected-course rows |
| Ambiguous transport | `cmdGrab()` has independent concurrency and retry behavior | An uncertain write requires a durable pending record and official readback; no automatic duplicate write |
| Shared persistence | No reference to the existing Python SQLite `IntentJournal` / `intent_bridge` in the new Node client | A single physical cross-process reservation must be verified before any production write; a local session JSON is not a shared journal |
| Readback | `cmdMy()` displays selected-course rows | Must bind account, school, term, course and class to trustworthy fresh response evidence before transition to confirmed |
| Node CI | Main Python workflow did not exercise Node files | Add a separate syntax + pure offline contract job; no live login, enumeration, writes or network probes |

## This change

- Adds `contracts/node_receipt_contract.mjs`, a pure *shape check* for string identities and a pure transport receipt projection. It never labels a response confirmed.
- Adds synthetic Node tests including leading-zero preservation, invalid identity rejection, ambiguous delivery and error/success messages.
- Adds an independent CI workflow, checking the existing Node source syntax **without running its CLI commands**.
- No changes to `kingosoft-node/`, `authserver-node/`, `ops/`, any existing Python executor, the scheduling interval, the Windows app or local journals.
- The new Node contract is **not yet connected** to the live submission path. Passing these tests is not evidence of safe or successful live auto-enrollment.

## Merge boundaries for PR #34 and PR #39

- Keep main's new Node files and the Windows history HMAC scope-binding changes from merged #38.
- PR #39: independently review `prepare_ops_targets`, Windows packaging, and `receipt_to_evidence` with its final CI head. Keep it as preflight, never as an enabled gateway.
- PR #34: avoid merging the old `ops/orchestrator.py` replacement wholesale. Port the minimal durable bridge changes only after comparing against latest main, preserving existing intent rows and pre-callback write fences.
- No forced update of main or either old PR branch. An uncertain result must stay unknown until authorized official readback.

## Additional review warning

`paoke.mjs` has a `probe` command whose endpoint list includes POST requests to enrollment-related write URLs. It must not be classified or run as a harmless read-only regression. The CI above invokes **only** `node --check` on this source. The separate `authserver-node/` investigation commands also must not run as CI tests against university servers.
