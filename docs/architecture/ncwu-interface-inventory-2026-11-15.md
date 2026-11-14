# NCWU / KingoAssistant interface inventory — 2026-11-15

> Scope: source inventory for `qiangke/`, `ncwu/`, `kingo-live/`, `kingosoft-node/`, `ops/`, and the existing Windows desktop integration. Read-only documentation audit; this document does not enable or authorize any school write.
>
> Evidence levels: **implemented** = present in code; **observed** = prior authenticated/local evidence recorded in the repository; **verified-production** = a current, identity-scoped, authorized official response reviewed for the exact use case. None of the enrollment submission/readback flows currently has a verified-production *success* record.

## 1. Authentication and official session

| Host/path or integration | Purpose | Implementations | Evidence / limitations |
| --- | --- | --- | --- |
| `jwmis.ncwu.edu.cn/hsjw/cas/login.action` | Local login page | `qiangke/qk/adapters/kingo.py`, `kingosoft-node/kingo.mjs` | Implemented; not a selection endpoint |
| `/hsjw/cas/logon.action` | Local Kingo login | Qiangke + Kingosoft Node | Repository records prior real login success; no claim of a current live session |
| `/hsjw/frame/homepage?method=getTempDeskey` | Login encryption parameter | Qiangke + Kingosoft Node | Implemented |
| `/hsjw/frame/homepage?method=getTempNowtime` | Login time parameter | Qiangke + Kingosoft Node | Implemented |
| `/hsjw/cas/genValidateCode` | Official login verification image | Qiangke | May require human interaction; never bypass verification |
| `/hsjw/frame/homes.action`, `/hsjw/` | Home/session checks | Qiangke | Implemented; HTTP 200 alone is not authenticated identity proof |
| `authserver.ncwu.edu.cn/authserver/login` | Separate campus CAS login surface | `qiangke/qk/cas.py`, `authserver-node/` | Separate from Kingo local login; respect normal verification |
| `/hsjw/xtgl/index_initMenu.action` and `PopMenuServlet.jsp` | Menu/feature discovery | `kingosoft-node/kingo.mjs`, Qiangke | A menu item does not verify course data or a write contract |

## 2. Selection pages, windows and catalog

| Path (under `jwmis.ncwu.edu.cn/hsjw`) | Purpose | Implementations | Evidence / limitations |
| --- | --- | --- | --- |
| `/jw/common/getWsxkTimeRange.action` | Selection rounds, account/term context and open-window marker | Qiangke, NCWU, Node | Prior read-only observation; a returned schedule is not permission to submit |
| `/student/wsxk.yx.html` | Pre-election page | Qiangke, NCWU | Implemented page parsing |
| `/student/wsxk.zx.html`, `/student/wsxk.zx_dg.html` | Plan-based selection pages | Qiangke, Node | Implemented; dynamic window state required |
| `/student/wsxk.zx.byvl.html`, `/student/wsxk.cxxk.jsp` | Volunteer/retake selection pages | Qiangke | Page/flow inventory only |
| `/student/report/wsxk.zx_promt.jsp` | Teaching-class detail/dialog | Node | Implemented; not an independent successful readback |
| `/taglib/DataTable.jsp?tableId=…` | Dynamic list/teaching-class tables | Qiangke, NCWU, Node | Table ID and hidden fields depend on page and active round; verify against official UI |
| `/taglib/DataTable.jsp?tableId=5327042` | Historical/ongoing course-plan query | `ncwu/hs_kcbcx.py` | Prior read-only observations; static plan is **not** live remaining-seat evidence |
| `/taglib/DataTable.jsp?tableId=2548` | Pre-election catalog candidate | `ncwu/hs_xk.py`, `ncwu/hs_sniper.py` | Observed in earlier flow; empty/closed data cannot verify current availability |
| `/jw/common/getSelectLessonScoreKcsInfo.action` | Related selection/score records | NCWU, Node | Implemented; do not infer exact selected teaching class from count-only responses |
| `/student/wsxk.zxjg.jsp` | Selection result page | Qiangke documentation | Located, but no reviewed account+term+course+class selected-list contract |

## 3. Write paths (all remain unverified for successful NCWU enrollment)

| Path | Legacy implementation | State / boundary |
| --- | --- | --- |
| `/STU_PreElectCourseAction.do?hidOption=saveData` | `ncwu/hs_xk.py`, `ncwu/hs_sniper.py`, `qiangke/qk/adapters/kingo.py` | Pre-election submission code exists; no verified official success/readback chain |
| `/jw/common/saveElectiveCourse.action` | `kingosoft-node/paoke.mjs` | Node implements plan-based submission; production Windows remains disabled |
| `/jw/common/cancelElectiveCourse.action` | NCWU/Node inventory | Withdrawal is a separate write, not an enrollment success confirmation |
| `/xsxk/xsxk!addXk.action` | Qiangke legacy default template | Endpoint existence/unauthenticated response does **not** verify current school flow |

Every physical write must have an authorized window, reviewed request policy, exact official account/term/target scope, a durable pre-write fence and authoritative post-write readback. Transport 200 or success text cannot confirm enrollment.

## 4. App/mobile and schema-configured channels

| Host/path | Source | Known evidence |
| --- | --- | --- |
| `jwmis.ncwu.edu.cn/hsjw//wap/mycourseschedule.action` | 2026-11-11 HAR audit, `core/ncwu_timetable_schema.py` | Personal timetable JSON structure observed; **not elective catalog/remaining seats** |
| `jwmis.ncwu.edu.cn/hsjw//wap/kb_kbdetail_bz.action` | 2026-11-11 HAR audit | Timetable auxiliary data only |
| `api.xiqueer.com/manager//wap/wapController.jsp` | 2026-11-11 HAR audit | Signed/encoded mobile API; selection metadata is not a verified live selection endpoint |
| `kingo-live/config.example.json` `catalog.path`, `submit.path`, `enrollment.path` | `kingo-live/live_client.py` | Configurable adapters with placeholders; not independently verified institutional endpoints |
| `clients/windows/source/core/client.py` `protocol.*` | Windows read-only monitor | Requires explicit verified source contract; local HAR-derived drafts force `verified: false` |

Some Node exploratory routes (`xsxxxggl`, `xscj`, `xskb`, `kwgl`, `pyfa`, `xjgl`) are identity, grades, timetable, exams, curriculum and student-information surfaces, **not** alternate enrollment endpoints. Do not try to promote them to write or availability evidence.

## 5. Internal code contracts — reuse, do not rewrite

| Module | Interface / responsibility | Verified scope |
| --- | --- | --- |
| `qiangke/qk/adapters/kingo.py` | Login, read-only selection list, legacy submit | Login observed, submit not verified |
| `ncwu/hs_xk.py` / `ncwu/hs_sniper.py` | Time window, GBK table parsing, pre-election and scheduling | Prior read-only observations + offline tests |
| `kingosoft-node/paoke.mjs` | Plan-based Node CLI, windows/list/class/grab/my | Selected-course provenance and shared persistence incomplete |
| `kingo-live/live_client.py` | Configured transport, read budget, journal, reconciliation | Loopback/offline end-to-end |
| `ops/adapter.py` / `ops/orchestrator.py` / `ops/executor_bridge.py` | Target normalization, state flow and existing executor callbacks | Offline; no fifth HTTP client |
| `contracts/intent_bridge.py` | Shared SQLite pre-write reservation, fail closed on insufficient identity | Implemented in Python/Windows/ops; Node not yet under the same shared fence |
| `contracts/identity_scope_audit.py` / `contracts/cross_client_identity.mjs` | Offline exact school/account/term/course/class reconciliation | Structural comparison, not authenticated proof |
| `clients/windows/source/core/existing_executor_gateway.py` | Windows → original executor, guarded by official read provider | No production official read provider or enabled engine factory |
| `clients/windows/source/core/enrollment.py` | Fail-closed FSM, local HMAC history, optional shared SQLite intent | Synthetic tests; official readback pending |

### Integration risk register

1. **P0:** The Node submission path is not yet gated by the same durable cross-process SQLite intent, unlike the Python and Windows integration. Preserve original source; do not enable Node writes through Windows while this gap exists.
2. **P0:** The Windows gateway has no independently verified live NCWU read-only source for official account, term, selection window, catalog and exact selected-course list. Keep production auto-submit disabled.
3. **P0:** Confirm that session/account identity and write permission are still valid immediately before a write and after an asynchronous selected-list fetch. A source can change after Windows engine construction. This guard is addressed by the scope revalidation patch paired with this inventory.
4. **P0:** `xqM=0`, `xq=1`, `2026-2027/1` are *not* safely interchangeable absent verified official mapping. Do not normalize by guess or cross-account alias.
5. **P1:** Keep old journal salt/schema, and verify cross-process restarts and both legacy course-/class-level reservation keys with synthetic tests.
6. **P1:** CI and EXE smoke success are offline evidence only; official login, enrollment and real Windows lifecycle remain separate acceptance gates.

### Acceptance sequence

A. Confirm shared pre-write fence and live-scope revalidation (synthetic tests; no network) → B. verified official **read-only** identity/catalog/selected-list adapter → C. reviewed Windows gateway factory under explicit permission and school policy → D. packaged Windows lifecycle verification → E. permitted official end-to-end test during the correct selection window. Until E passes, no production success claim.
