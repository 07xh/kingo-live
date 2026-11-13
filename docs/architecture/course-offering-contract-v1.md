# Unified read-only course offering contract v1

**Status:** pure normalization adapters and offline contract tests are implemented.
Production callers still use their existing parsers and session managers; replacing
those interfaces is a separate gated change. No part of this contract sends HTTP
requests or performs school enrollment.

| Canonical property | NCWU plan parser | Qiangke DataTable | Windows read-only |
|---|---|---|---|
| course_id | `kcdm` | `kcdm` or bracketed `kc` | `course_id` |
| class_id | `skbjdm` | `skbjdm` | `class_id` |
| course_name | `kcmc` | `kc` without prefix | `course_name` |
| teacher | `rkjs` | `rkjs` | `teacher` |
| remaining | `left` from server `qdrs` | only explicit `qdrs` | `remain` |
| observed_at | explicit caller timestamp only | explicit caller timestamp only | explicit caller timestamp only |

## Fail-closed rules

- Missing trustworthy remaining count remains **unknown** (`None`), not zero
  and not inferred from seat capacity, number enrolled, or a success message.
- The course code is always a string (including leading zeros); the teaching
  class is distinct from the course. Missing class IDs are not fabricated.
- Invalid counts, fields and absent course identity fail closed.
- The record never contains student names, passwords, request bodies, cookies,
  raw HAR evidence or login state.
- Local or historical snapshots must never be marketed as live availability.
- A course-offering record is not proof of enrollment or an authorization to submit.

## Integration order

1. Pin these mappings against already-tested local fixtures from each parser.
2. Update read-only GUI/CLI presentation one consumer at a time, preserving tests.
3. Replace redundant parsing ONLY after data and error-state contracts match.
4. Keep submission and official enrollment readback entirely separate.
5. Do not delete old adapters until Windows packaged and Linux/Windows CI pass.

## First active consumer: NCWU plan exporter

The existing `ncwu/hs_kcbcx.py --csv` now normalizes parsed rows through
`from_ncwu_plan` before exporting its **unchanged 15-column** CSV. The
optional `--standard-json path` writes strictly the canonical public fields,
with the timestamp of this *client's observation* (not a server-time claim).
The legacy `--json` output remains unchanged.

Real parser-to-consumer offline tests verify NCWU HTML → CSV → Windows loader
and Qiangke DataTable HTML → canonical model. No school requests are made in
those tests, and no automatic submissions or real-time claims are introduced.
