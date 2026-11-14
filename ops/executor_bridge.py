"""executor_bridge - wire real executors onto the ops pipeline.

The orchestrator never owns the socket: ``Mission.executor`` is a plain
callback ``target -> receipt``. This module produces such callbacks from
the two executor families that already exist in this repository:

* ``qk.adapters`` (kingo / zfsoft): ``Adapter.grab(course) -> GrabResult``
  with the lifecycle ``open() -> login() -> prepare(courses) -> grab()``.
* ``ncwu/hs_sniper``: ``Sniper.fire_one(kcdm, skbjdm_hint, tj) ->
  (ack, msg, elapsed_ms)``.

Contract rules (enforced, not advisory):

* The bridge performs **one** physical submission per call and never
  retries. Re-fire policy lives entirely in the orchestrator/intent layer.
* Receipts are uniform dicts so verify-phase evidence fusion can consume
  them without knowing which executor fired.
* ``write_attempted`` distinguishes "the HTTP write happened but the
  outcome is unknown" (auth/unknown codes) from "rejected before any
  write" -- the same distinction the qk ``GrabResult`` carries.
* Heavy imports (``qk``, ``hs_sniper``) are lazy: an ``importer``
  callable may be injected for offline tests; without one, the bridge
  imports from ``sys.path`` (repository root expected on it).

Zero third-party dependencies. Python 3.10+.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

try:                                                   # package or direct run
    from .crosscheck import Evidence
except ImportError:                                    # pragma: no cover
    from crosscheck import Evidence

__all__ = ["make_receipt", "QkExecutor", "SniperExecutor",
           "build_executor", "receipt_to_evidence"]

# qk.adapters.base result codes with write semantics
_WRITE_UNKNOWN_CODES = {"success", "enrolled", "unknown", "auth"}
_REJECT_CODES = {"full", "conflict", "error"}
_PRESENT_TRUE = {"success", "enrolled"}
_PRESENT_FALSE = {"full", "conflict", "error"}


def make_receipt(target: Dict[str, Any], *, ok: bool, code: str, msg: str,
                 elapsed_ms: float, write_attempted: Optional[bool],
                 executor: str, status: Optional[int] = None) -> dict:
    """Uniform single-shot receipt for the orchestrator/verify layer."""
    return {"target": dict(target), "ok": bool(ok), "code": str(code),
            "msg": str(msg), "elapsed_ms": round(float(elapsed_ms), 2),
            "write_attempted": write_attempted, "executor": executor,
            "status": status, "at": round(time.time(), 3)}


def receipt_to_evidence(receipt: dict, *, channel: str = "executor",
                        source: str = "") -> Evidence:
    """Translate an executor receipt into a verify-phase Evidence.

    ``success``/``enrolled`` prove presence; ``full``/``conflict``/``error``
    prove absence; ``auth``/``unknown`` leave presence ``None`` (the
    fusion must not treat them as either outcome).
    """
    code = str(receipt.get("code", "unknown"))
    if code in _PRESENT_TRUE:
        present: Optional[bool] = True
    elif code in _PRESENT_FALSE:
        present = False
    else:
        present = None
    tgt = receipt.get("target") or {}
    return Evidence(channel=channel,
                    course_id=str(tgt.get("course_id", "")),
                    class_id=str(tgt.get("class_id", "")),
                    present=present,
                    latency_ms=float(receipt.get("elapsed_ms") or 0.0),
                    stale=False,
                    source=source or f"executor:{receipt.get('executor', '?')}",
                    semester=str(tgt.get("term", "")),
                    account_scope=str(tgt.get("account", "")))


class _LifecycleMixin:
    """Shared no-retry, one-shot call discipline."""

    name = "bridge"

    def __call__(self, target: Dict[str, Any]) -> dict:
        t0 = time.perf_counter()
        try:
            self._ensure_ready()
        except Exception as exc:
            # A preflight error happens before _fire could send any write.
            return make_receipt(target, ok=False, code="error",
                                msg=f"{type(exc).__name__}: {exc}",
                                elapsed_ms=(time.perf_counter() - t0) * 1000,
                                write_attempted=False, executor=self.name)
        try:
            receipt = self._fire(target)
        except Exception as exc:
            # Exceptions from a transport callback do NOT prove no bytes were
            # sent: a server may have committed before the connection failed.
            # Never let a caller mistake this for 'definitely unsent'.
            return make_receipt(target, ok=False, code="unknown",
                                msg=f"{type(exc).__name__}: {exc}",
                                elapsed_ms=(time.perf_counter() - t0) * 1000,
                                write_attempted=None, executor=self.name)
        receipt.setdefault("elapsed_ms", round((time.perf_counter() - t0) * 1000, 2))
        return receipt

    def _ensure_ready(self) -> None:
        raise NotImplementedError

    def _fire(self, target: Dict[str, Any]) -> dict:
        raise NotImplementedError


class QkExecutor(_LifecycleMixin):
    """Bridge to ``qk.adapters`` (kingo/zfsoft) adapters.

    ``adapter`` may be a fully-constructed ``qk`` Adapter, or pass
    ``kind``/``acct``/``opts`` plus an optional ``factory`` (defaults to
    lazily importing ``qk.adapters.make``). ``target`` keys map to the
    qk course dict: ``course_id -> kch_id``, ``class_id -> jxb_ids[0]``.
    """

    def __init__(self, adapter: Any = None, *, kind: str = "kingo",
                 acct: Optional[dict] = None, opts: Optional[dict] = None,
                 factory: Optional[Callable[..., Any]] = None, log: Any = None):
        self.name = f"qk:{kind}"
        self._adapter = adapter
        self._kind = kind
        self._acct = dict(acct or {})
        self._opts = dict(opts or {})
        self._factory = factory
        self._log = log
        self._ready = adapter is not None and bool(
            getattr(adapter, "logged_in", False))

    def _ensure_ready(self) -> None:
        if self._adapter is None:
            if self._factory is None:
                from qk.adapters import make as factory   # lazy, repo root on path
                self._factory = factory
            self._adapter = self._factory(self._kind, self._acct,
                                          self._opts, self._log)
        if not self._ready:
            self._adapter.open()
            if not self._adapter.login():
                raise RuntimeError("qk login failed; no write attempted")
            courses = self._acct.get("courses") or []
            if courses:
                self._adapter.prepare(courses)
            self._ready = True

    def _fire(self, target: Dict[str, Any]) -> dict:
        course = {"kch_id": str(target.get("course_id", "")),
                  "name": str(target.get("name", ""))}
        class_id = str(target.get("class_id", "")).strip()
        if class_id:
            course["jxb_ids"] = [class_id]
        for extra in ("xkid",):
            if target.get(extra):
                course[extra] = target[extra]
        t0 = time.time()
        result = self._adapter.grab(course)
        elapsed = (time.time() - t0) * 1000
        code = str(getattr(result, "code", "unknown"))
        return make_receipt(target, ok=bool(getattr(result, "ok", False)),
                            code=code, msg=str(getattr(result, "msg", "")),
                            elapsed_ms=elapsed,
                            write_attempted=getattr(result, "write_attempted", None),
                            executor=self.name,
                            status=None)


class SniperExecutor(_LifecycleMixin):
    """Bridge to ``ncwu/hs_sniper.Sniper``.

    ``sniper`` may be a constructed Sniper or pass ``cfg`` plus an
    optional ``importer`` returning the module (defaults to lazily
    importing ``hs_sniper``). ``prepare()`` runs once before the first
    fire; ``fire_one(course_id, class_id)`` performs the single write.
    """

    def __init__(self, sniper: Any = None, *, cfg: Optional[dict] = None,
                 importer: Optional[Callable[[], Any]] = None):
        self.name = "hs_sniper"
        self._sniper = sniper
        self._cfg = dict(cfg or {})
        self._importer = importer
        self._ready = False

    def _ensure_ready(self) -> None:
        if self._sniper is None:
            if self._importer is None:
                import hs_sniper as module           # lazy, repo root on path
                module = module
                self._sniper = module.Sniper(self._cfg)
            else:
                self._sniper = self._importer().Sniper(self._cfg)
        if not self._ready:
            if not self._sniper.prepare():
                raise RuntimeError("sniper gate reports window closed; no write")
            self._ready = True

    def _fire(self, target: Dict[str, Any]) -> dict:
        t0 = time.time()
        ack, msg, ms = self._sniper.fire_one(
            str(target.get("course_id", "")),
            str(target.get("class_id", "") or ""))
        # fire_one returns transport evidence only; keep code="unknown"
        # unless the sniper itself claims more -- the verify phase decides.
        return make_receipt(target, ok=bool(ack), code="unknown" if ack else "error",
                            msg=str(msg),
                            elapsed_ms=(time.time() - t0) * 1000,
                            write_attempted=bool(ack) or None,
                            executor=self.name, status=None)


_KINDS = {"qk": QkExecutor, "sniper": SniperExecutor}


def build_executor(spec: dict) -> Callable[[Dict[str, Any]], dict]:
    """Build a Mission.executor callback from a plain config spec.

    ``spec`` requires ``kind``; remaining keys pass through to the
    constructor (``adapter``/``kind``/``acct``/``opts``/``factory`` for
    qk, ``sniper``/``cfg``/``importer`` for sniper). Unknown kinds raise
    -- a typo must never silently degrade to monitor-only.
    """
    kind = str(spec.get("kind", "")).strip().lower()
    if kind not in _KINDS:
        raise ValueError(f"unknown executor kind {kind!r}; "
                         f"expected one of {sorted(_KINDS)}")
    kwargs = {k: v for k, v in spec.items() if k != "kind"}
    return _KINDS[kind](**kwargs)


def demo() -> dict:
    """Offline self-check with a fake qk adapter (no network)."""
    class _FakeResult:
        ok, code, msg = True, "success", "demo"
        attempts, elapsed, write_attempted = 1, 0.01, True

    class _FakeAdapter:
        logged_in = False

        def open(self): self.logged_in = False
        def login(self): self.logged_in = True; return True
        def grab(self, course): return _FakeResult()

    ex = QkExecutor(_FakeAdapter())
    receipt = ex({"course_id": "C1", "class_id": "J1"})
    ev = receipt_to_evidence(receipt)
    return {"receipt": receipt, "evidence": ev.public(),
            "ok": receipt["ok"] and ev.present is True}
