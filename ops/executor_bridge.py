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

import sys
import time
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:                                                   # package or direct run
    from .crosscheck import Evidence
except ImportError:                                    # pragma: no cover
    from crosscheck import Evidence

__all__ = ["make_receipt", "QkExecutor", "SniperExecutor",
           "build_executor", "receipt_to_evidence"]

def _enable_repository_imports() -> None:
    """Resolve sibling execution packages from any working directory."""
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)


class _BridgeLog:
    """Minimal logger compatible with qk's custom ok() convention."""
    def __init__(self):
        self._logger = logging.getLogger("ops.executor_bridge")

    def ok(self, message, *args, **kwargs):
        self._logger.info(message, *args, **kwargs)

    def __getattr__(self, method):
        return getattr(self._logger, method)



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
    """Convert executor transport receipts into contextual evidence only.

    A transport acknowledgement never proves that a course was added to
    the official selected-course list. Keep presence unknown for ALL status
    codes, even when a caller labels this channel as an official list.
    """
    tgt = receipt.get("target") or {}
    return Evidence(channel=channel,
                    course_id=str(tgt.get("course_id", "")),
                    class_id=str(tgt.get("class_id", "")),
                    present=None,
                    latency_ms=float(receipt.get("elapsed_ms") or 0.0),
                    stale=False,
                    source=source or f"executor:{receipt.get('executor', '?')}",
                    semester=str(tgt.get("term", "")),
                    account_scope=str(tgt.get("account", "")))

class _LifecycleMixin:
    """Shared no-retry, one-shot call discipline."""

    name = "bridge"

    @staticmethod
    def _exact_target(value: Any, label: str) -> str:
        if type(value) not in (str, int):
            raise ValueError(f"{label} 缺少明确的单值标识")
        result = str(value).strip()
        if (not result or len(result) > 100
                or result.lower() in ("unknown", "null", "none", "-")
                or any(ord(char) < 32 for char in result)):
            raise ValueError(f"{label} 不可信")
        return result

    def _preflight_target(self, target: Dict[str, Any]) -> None:
        if not isinstance(target, dict):
            raise ValueError("选课目标结构错误")
        self._exact_target(target.get("course_id"), "课程号")
        self._exact_target(target.get("class_id"), "教学班号")

    def __call__(self, target: Dict[str, Any]) -> dict:
        t0 = time.perf_counter()
        try:
            self._preflight_target(target)
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
    lazily importing ``qk.adapters.make``). A verified class is mapped
    consistently to scalar ``xkid`` and legacy singleton ``jxb_ids``.
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
                _enable_repository_imports()
                from qiangke.qk.adapters import make as factory
                self._factory = factory
            self._adapter = self._factory(self._kind, self._acct,
                                          self._opts, self._log if self._log is not None else _BridgeLog())
        if not self._ready:
            self._adapter.open()
            if not self._adapter.login():
                raise RuntimeError("qk login failed; no write attempted")
            courses = self._acct.get("courses") or []
            if courses:
                self._adapter.prepare(courses)
            self._ready = True

    def _fire(self, target: Dict[str, Any]) -> dict:
        course_id = self._exact_target(target.get("course_id"), "课程号")
        class_id = self._exact_target(target.get("class_id"), "教学班号")
        if target.get("xkid") not in (None, "", class_id):
            raise ValueError("选课目标教学班字段与权威代码不一致")
        course = {
            "kch_id": course_id,
            "name": str(target.get("name", "")),
            "xkid": class_id,
            "jxb_ids": [class_id],
        }
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

    def _preflight_target(self, target: Dict[str, Any]) -> None:
        super()._preflight_target(target)
        # hs_sniper.fire_one passes only kcdm into its items write body.
        # Its optional skbjdm_hint is NOT serialized. Until school evidence
        # verifies an exact-class payload, selecting a requested class through
        # this bridge would be a false promise and must fail before login.
        raise ValueError("现有 Sniper 提交载荷不包含指定教学班号，禁止精确班级写入")

    def _ensure_ready(self) -> None:
        if self._sniper is None:
            if self._importer is None:
                _enable_repository_imports()
                from ncwu import hs_sniper as module
                self._sniper = module.Sniper(self._cfg)
            else:
                self._sniper = self._importer().Sniper(self._cfg)
        if not self._ready:
            if not self._sniper.prepare():
                raise RuntimeError("sniper gate reports window closed; no write")
            self._ready = True

    def _fire(self, target: Dict[str, Any]) -> dict:
        # Also fail closed for direct invocations that bypass __call__.
        return make_receipt(target, ok=False, code="unsupported_class_write",
                            msg="无法证明 Sniper 写入指定教学班，未发送请求",
                            elapsed_ms=0.0, write_attempted=False,
                            executor=self.name)


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
            "ok": bool(receipt["ok"]) and ev.present is None,
            "officially_confirmed": False}
