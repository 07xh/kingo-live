# Legacy Windows synchronization audit — 2026-11-13

## Source and destination

- Source: `07xh/kingo-assistant` main, after commit `a6a1a6a`.
- Destination: `07xh/kingo-live`, based on `d64a236479c2a671a76e100156c540c126bbaf53`.
- Earlier import: `kingo-live#1` captured `kingo-assistant@2ab31ad`.

## Identified drift

The old repository's `clients/windows/source/` contains 104 tracked files. On the audited destination baseline, 89 have identical Git blob hashes, 6 are missing, and 9 have different content.

The six absent paths include two historical local configuration/workflow files (not ported because the destination owns its CI and ignore rules) and four HAR modules/tests restored in this change:

- `core/har_schema.py`: privacy-preserving, fixed-label JSON shape signals.
- `core/har_to_schema.py`: **offline-only draft** mapping to the Windows read-only client's protocol config. All outputs remain unverified.
- `tests/test_har_schema_integration.py`: safe shape coverage and redaction checks.
- `tests/test_har_to_schema.py`: draft-generation regression tests.

`core/har_audit.py` is refreshed to include the legacy read-only shape classifications, without replacing the destination's Windows GUI or independent `core/kingo_live_bridge.py`.

The other differences are in the destination-specific Windows GUI and synthetic HTML fixtures. They are intentionally **not blindly overwritten**; the destination contains a shared HAR bridge and its own offline packaged-GUI tests. Existing identical Windows source files and tests are preserved.

## Validation and release boundaries

- GitHub Actions `Windows integrated GUI` runs `unittest discover`, packaged GUI smoke checks and PyInstaller verification. Restored tests are included automatically.
- Offline Linux/Windows research tests remain separate from Windows GUI tests.
- `ncwu/*.py` is added to the syntax-compilation gate; it is **not** a real school acceptance test.
- The desktop application still has no enabled, verified university-specific enrollment submission adapter.
- Live credentials, student identifiers and raw HAR captures must never be committed.
- Keep `kingo-assistant` as a source/rollback archive until final manual review and Windows real-device acceptance. Do not archive or delete it in this change.

## Remaining integration items

- Document and review the old repository's historical acceptance and architecture notes if needed; they are not runtime code.
- Validate Windows GUI on an actual user computer and compare its read-only output with the official university UI.
- Verify school authorization, login/session safety and applicable rate limits prior to any enrollment feature.
- Make an explicit separate archival decision only after the new repository is accepted.
