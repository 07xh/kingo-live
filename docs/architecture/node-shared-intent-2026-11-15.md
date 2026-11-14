# Node -> original SQLite intent fence (2026-11-15)

**Scope:** Reuse the existing Python/Qiangke journal with the original
salt, `metadata` and `attempts` schema, and legacy fingerprint lookup.
Do **not** add a fifth HTTP executor, a new Node-only database, or a new
school endpoint. This is a local persistence integration, **not** a
verified live NCWU enrollment submission or result readback.

## Connection

- `kingosoft-node/paoke.mjs` imports
  `contracts/node_shared_intent.mjs`.
- That module invokes `python -m contracts.intent_bridge_cli` as a fresh
  local child process, with JSON fields supplied over stdin (not argv).
- The Python module calls existing `contracts.intent_bridge.reserve_shared_intent`
  which uses `qiangke.qk.intent_journal.IntentJournal`.
- `KINGO_INTENT_DB` specifies the same SQLite path for all clients.
  If unset, everyone uses the existing
  `~/.kingo-assistant/intent-journal.sqlite3` default. No migration,
  rehashing, salt reset or deletions occur.
- `KINGO_PYTHON` may point to a trusted Python 3.10+ executable, for
  example the same Python runtime used by the desktop app. A missing
  interpreter, invalid output, SQLite errors or a lock time-out block writes.
- The Node bridge accepts an exact school/account/term/course/class scope.
  The legacy term spelling aliases are handled by the original Python
  journal, **not** reimplemented in JavaScript.

## Gate order (original Node enrollment HTTP code retained)

1. Normal run is disabled unless the person explicitly opts in and a valid
   school window and authenticated identity were just obtained.
2. The fresh official window response must provide an exact account ID,
   academic-year field, raw term field, and an open window.
3. The user-confirmed `KINGO_SOURCE_TERM` must exactly equal the returned
   raw `xn,xqM` pair. `KINGO_CANONICAL_TERM` must be the separately
   confirmed cross-client academic term, identical to Windows/Python scope.
   **Do not** automatically map `xqM=0` to `xq=1` or use a cosmetic display
   name to establish identity. These checks are *structural*; independently
   verifying official provenance is still required for production release.
4. After course/class preparation, fetch fresh identity/window status again.
   Account/term changes block the operation.
5. Build the existing encrypted body locally, then atomically reserve the
   course-level shared SQLite intent **before** the only possible POST.
6. A one-use in-process permit gates the existing Node `post()` helper.
   Legacy multi-worker/retry arguments no longer produce repeated physical
   submissions. `watch` ends on pending/blocked outcomes.
7. Whether the transport returns 200, a success message, a rejection, or
   throws an exception, the Node client never labels the target confirmed.
   It remains `PENDING` in the existing durable journal until a trusted,
   fresh official selected-course list establishes an exact match.
8. `raw` is limited to listed read-only diagnostic endpoints, and `probe`
   does not send empty bodies to enrollment or withdrawal write paths.

**Note:** Withdrawal (`drop`) is a separate, user-initiated action with
different business semantics. The *enrollment-intent* journal must not be
silently applied to withdrawal as though it were a second enrollment attempt.
The original withdrawal implementation is preserved, not exposed through
the Windows selection gateway.

## Verification performed in CI

The isolated Node contract workflow runs on Windows and Ubuntu with Node 22
and Python 3.12. Synthetic tests use temporary SQLite databases and
fictional identifiers to cover:

- Node first -> Python duplicate, and Python first -> Node duplicate;
- exact same course with a different class, historical term spellings,
  independent account/school/term scopes;
- simultaneous independent Python processes competing for one reservation;
- session/account/term/window mismatch, nonexistent interpreter and bad DB;
- redacted failure output without identifiers.

The existing Python journal tests, Windows integration suite, and all
other offline CI continue to run. **No school requests** are made in tests.

## Remaining production acceptance gates

The current NCWU official authenticated selected-course detail endpoint,
live field semantics, exact class identifier, authorization and school
request policy remain unverified. These are tracked in Issues #21 and #37.
Keep the Windows production engine factory disabled until those gates pass.
A green CI proves the local fence, **not** that enrollment succeeded.
