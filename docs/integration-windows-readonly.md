# Windows + Kingo Live private integration

## Provenance

- Historical Windows source: `07xh/kingo-assistant` main at `2ab31adb212d134be0ec050d9404d2a7a4dac4d7`.
- Research tools: `07xh/kingo-live` main at `604794482890a92d396d76431ee3c611adfd62eb`.
- Destination is **private**. The old public repository is unchanged.

## What is actually wired together

The Windows Tk application in `clients/windows/source/windows_gui.py`
has an offline HAR cross-check action. It invokes
`core/kingo_live_bridge.py`, which lazily loads the **existing**
`kingo-live/har_to_schema.py` as a shared parser. PyInstaller bundles
that file for the Windows standalone EXE; there is no duplicate parser
code. No school network requests are created.

The GUI shows only aggregate numeric evidence: number of usable HAR
entries, potential course catalog and enrolled-list responses,
excluded timetable responses and closed-window evidence. It neither
exports raw HAR nor writes an inferred protocol config.

The legacy Windows desktop monitor and school submission adapter gates
remain unchanged. The live HTTP research CLI is **not** made accessible
from the GUI; it is not verified for the institution and still contains
risks with pending-journal lifetime, HTTP retries and response semantics.

## Verification gates

1. Existing `kingo-live` loopback/academic-simulation tests (Ubuntu + Windows).
2. Integrated Windows desktop unit tests, including offline HAR privacy,
   oversize rejection and malformed-file handling.
3. Windows source and packaged Tk GUI smoke tests.
4. Windows PyInstaller bundle includes the shared parser as a data file.
5. Real personal university login, seat data, server limits and actual
   submission: **not tested, not supported in the GUI**.

The old public repository is preserved for provenance and rollback. Do
not publish secrets, cookies, signed HARs, enrollment records or private
study details in either repository.
