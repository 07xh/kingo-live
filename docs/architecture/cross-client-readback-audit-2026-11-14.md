# Cross-client identity and official readback audit (offline)

This PR adds only pure, synthetic reconciliation tests and **does not change** the existing Node, Windows, Python or ops execution paths.

- `reconcileIdentity` checks exact school, authenticated-account identifier, term, course ID and teaching-class ID across Node and Windows scope representations. It cannot authenticate either representation.
- `inspectOfficialSelectedReadback` requires explicit official/authenticated/fresh provenance flags, matching source scope and exact course/class rows. These flags are inputs to a **pure offline validator**, not proof that a real endpoint is trustworthy.
- Missing selected rows, count-only summaries, transport-success text, mismatched class or account, and stale evidence are not success.
- No real server calls, credential collection, HAR imports, write submissions, retry behavior, persistence modifications or execution toggles.
- Future integration must identify actual authenticated official read-only endpoint provenance, normalize real response fields with evidence, and verify the persistent SQLite fence across separate processes. Until then, these utilities are not production enrollment verification.
