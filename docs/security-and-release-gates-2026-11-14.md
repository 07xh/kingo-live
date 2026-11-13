# Credential exposure response and release gates — 2026-11-14

## Scope and evidence

The 2026-11-13 audit records that an earlier credential-based fixture in
`qiangke/tests/test_pwd.py` was replaced and the sanitized main-history
snapshot was rebuilt. That change **does not** prove the credential is absent
from every branch, tag, fork, clone, Actions artifact, cache, or old repository.
Do not reproduce any historical password, Cookie, session token, account ID, or
captured HAR in a GitHub issue, CI log, chat message or new commit.

## Required credential response — account owner

1. Change any potentially exposed school-account password using the official
   account-recovery flow, then invalidate active sessions if supported.
2. Rotate affected third-party notification/API tokens and revoke any exported
   session Cookies. A GitHub history rewrite **cannot** invalidate a live
   credential.
3. Audit the full history of both repositories, all refs, PR refs, forks
   and accessible clones with an approved secret-scanning tool. Only capture
   file/commit identifiers and redacted detector metadata.
4. If historical removal is necessary, agree on a coordinated history rewrite,
   communicate new clone requirements, revoke affected tokens first, and
   separately review GitHub caches and release assets. Never force-push
   rewritten history as a routine CI fix.
5. Keep `*.cookies`, local identity/session configurations, raw HAR and
   SQLite intent databases out of Git and generated release artifacts.

**Status:** Current snapshot and prior audit reviewed; comprehensive history,
remote caches and credential invalidation are *not verified* by this PR.

## Runtime safety gate

- Windows application enrollment remains disabled until a reviewed official
  school adapter, verified account/semester, authoritative catalog, selection
  window, and enrolled-list readback are available.
- The `kingo-live/` transport cannot opt out of server Retry-After.
- The older `qiangke` CLI now uses a per-account/term/target durable SQLite
  intent fence before one physical attempt. Without official per-course
  readback, even 'success'/'already selected' response text is UNKNOWN.
- Standalone NCWU write tools share the same journal *path* by default and
  refuse duplicate exact intent keys. Their account, semester and teaching
  class identifiers must still be checked for consistency before claiming
  cross-tool deduplication. Direct low-level HTTP helpers are not a public
  execution entry point and do not independently provide the full guard.
- A pending intent is **not** automatically deleted after restart, a timeout,
  a business response or an absent course. It requires official examination
  and a separately designed/manual-audited reconciliation procedure.

## What CI does and does not prove

Ubuntu and Windows workflows compile the Python files, run offline/loopback
regressions including the journal and cooldown tests, and attempt the Windows
PyInstaller smoke test. A successful run means only these checks passed. The
actual Windows executable, real NCWU catalog, account/session permissions,
authoritative course enrollment and school-enforced frequency limits are
separate acceptance gates. No live enrollment POST is a CI test.

Do not merge this PR unless the latest head's checks pass and the changes
receive manual review. Do not treat a missing status response as green.
