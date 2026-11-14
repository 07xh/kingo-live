# Exact teaching-class identity: fail-closed bridge — 2026-11-15

Scope: **existing** Qiangke, NCWU Sniper and ops executor bridge. No new
network client, no new submit interface, no production school writes, and no
change to the established shared SQLite journal or official-result evidence.

## Source-backed findings

1. `ops/executor_bridge.py:QkExecutor` previously put the requested class in
   `jxb_ids=[id]` while `qiangke/qk/adapters/kingo.py:_render_tpl` fell back
   from `xkid` to this *list*. Generic string interpolation could turn a
   scalar class identifier into literal `['CLASS-01']` in the wire form.
2. A caller could also supply a conflicting `xkid` and `class_id`; the older
   bridge copied the extra field without comparing it to the target.
3. `ncwu/hs_sniper.py:fire_one(kcdm, skbjdm_hint)` currently creates
   `items` from `kcdm,tj` only. The requested `skbjdm_hint` never appears
   in the pre-election write body. It **cannot guarantee that the specified
   teaching class is selected**; previous bridge receipts implied that it
   could submit for a requested class. The official semantics still require
   authorized real-world verification.

## Current behavior

- Qiangke keeps the old one-element `jxb_ids` input interface, but the
  template resolves every teaching-class alias to the **same scalar** code,
  rejecting zero/multiple list items, invalid type, placeholders, alias
  conflicts and disagreement with an explicit `xkid`.
- Its write path returns `write_attempted=False` before any HTTP request
  when class identity is absent or ambiguous. Read-only catalog queries still
  work without a class ID.
- `ops.QkExecutor` supplies `xkid` (scalar) and `jxb_ids` (singleton)
  consistently; malformed or contradictory targets are rejected **before**
  initializing a login or executor transport.
- `ops.SniperExecutor` refuses a class-specific write before the first
  network operation because no confirmed class-aware protocol exists in the
  current Sniper implementation. Direct `_fire` also returns a proven-unsent
  receipt. This is a deliberate safety gate, **not** completed live support.
- Standalone Sniper `fire` and `fire_one` now refuse a requested class when
  the existing payload cannot represent it; course-only behavior remains
  under its previous journal and unknown-result restrictions.
- All executor transport receipts remain context-only. A 200/OK, local
  counter increment, course-only match, or fake fixture cannot become an
  official selected-class confirmation.

## Synthetic acceptance tests

- Different course codes, exact class codes and display labels stay distinct.
- Singleton legacy `jxb_ids` renders a plain code, not a Python list string.
- Multiple/conflicting/empty class values are rejected with **zero** sends.
- Existing read-only course list interpolation continues working.
- Sniper's class hint cannot reach its course-only write path; no DB intent is
  reserved on this definitely-unsent branch.
- An existing course-only Sniper reservation still blocks a second attempt.
- No change to official selected-course readback; Issue #21 remains open.

Issue #37 should remain open until real official teaching-class identity and
the exact write/readback contract are independently verified. This change
eliminates an unsafe mapping path; it does **not** establish which live
endpoint or field actually selects a teaching class.
