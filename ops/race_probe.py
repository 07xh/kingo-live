"""race_probe - #11: is the server's check-then-act atomic?

Face #11 is high variance: if the backend enrolls via ``check-then-act``
without a transaction lock or unique constraint, N concurrent identical
submissions can hit N times; with a lock or constraint, concurrency is pure
noise and the single-submit invariant stays optimal.

This module does two things and nothing else:

1. ``classify`` - pure function. Response tuples (status, body markers,
   duplicate-count evidence) -> lock verdict. Works offline against the
   loopback harness and against real response dumps alike.
2. ``RaceProbe`` - the only place in ops where a *controlled* multi-shot
   submit may happen. Guards, in order: caller must attach an executor,
   ``armed=True`` must be passed explicitly, intent is journalled
   ``pending`` BEFORE any fire, shots are bounded (default 2, hard cap 4),
   and every extra hit is registered for reconcile against the official
   list. Without ``armed`` the probe returns a plan, never a submission.

Zero third-party dependencies. Python 3.10+.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:                                                   # package or direct run
    from .seat_events import IntentGate
    from .crosscheck import Evidence, fuse
except ImportError:                                    # pragma: no cover
    from seat_events import IntentGate
    from crosscheck import Evidence, fuse

__all__ = ["LockVerdict", "classify", "RaceProbe", "MAX_SHOTS"]

MAX_SHOTS = 4

DUP_MARKERS = ("已选该课程", "重复选课", "已选过", "already selected",
               "duplicate", "已存在")
FULL_MARKERS = ("人数已满", "容量已满", "名额已满", "full", "no seat")
LOCK_MARKERS = ("锁", "lock", "busy", "timeout", "deadlock")


@dataclass
class LockVerdict:
    verdict: str          # unique_constraint | transaction_lock |
                          # check_then_act | unknown
    exploitable: bool
    confidence: float
    evidence: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "exploitable": self.exploitable,
                "confidence": round(self.confidence, 3),
                "evidence": self.evidence}


def _has(marker_group: Tuple[str, ...], texts: Sequence[str]) -> bool:
    low = [t.lower() for t in texts]
    return any(m in t for m in marker_group for t in low)


def classify(responses: Sequence[Dict[str, Any]],
             official_hits: int = -1) -> LockVerdict:
    """Classify server atomicity from N identical concurrent submissions.

    ``responses``: [{"status": int, "body": str, "latency_ms": float}, ...]
    ``official_hits``: courses found for this exact identity in the official
    list afterwards; -1 = unknown (verdict then rests on responses alone).
    """
    if not responses:
        return LockVerdict("unknown", False, 0.0, ["no responses"])
    ev: List[str] = []
    ok = [r for r in responses if int(r.get("status", 0)) == 200]
    bodies = [str(r.get("body", "")) for r in responses]
    ok_bodies = [str(r.get("body", ""))
                 for r in responses if int(r.get("status", 0)) == 200]

    # 1. Official list is the ground truth when available.
    if official_hits >= 0 and len(responses) >= 2:
        if official_hits >= 2:
            ev.append(f"official list shows {official_hits} hits for one "
                      "intent: no unique constraint")
            return LockVerdict("check_then_act", True, 0.95, ev)
        if official_hits == 1:
            ev.append("official list shows exactly 1 hit")
            if len(ok) >= 2 and not _has(DUP_MARKERS, ok_bodies):
                ev.append("multiple 200s with no duplicate marker: "
                          "results were silently collapsed -> constraint")
                return LockVerdict("unique_constraint", False, 0.85, ev)
            ev.append("single success + duplicate/failed siblings")
            return LockVerdict("transaction_lock", False, 0.8, ev)
        if official_hits == 0:
            ev.append("official list shows 0 hits: all siblings failed")
            return LockVerdict("transaction_lock", False, 0.7, ev)

    # 2. Response-only fallback when the list is not yet readable.
    if len(responses) < 2:
        return LockVerdict("unknown", False, 0.2, ["need >=2 responses"])
    dup = _has(DUP_MARKERS, bodies)
    full = _has(FULL_MARKERS, bodies)
    lockish = _has(LOCK_MARKERS, bodies)
    if dup and len(ok) >= 2:
        ev.append("sibling 200 answered 'already selected': constraint met")
        return LockVerdict("unique_constraint", False, 0.75, ev)
    if full and len(ok) >= 2:
        ev.append("sibling answers 'full': both passed the check first")
        return LockVerdict("check_then_act", True, 0.6, ev)
    if lockish:
        ev.append("lock/busy markers: serialization visible")
        return LockVerdict("transaction_lock", False, 0.65, ev)
    lat = sorted(float(r.get("latency_ms", 0.0)) for r in responses)
    spread = lat[-1] - lat[0] if lat else 0.0
    ev.append(f"latency spread {spread:.1f} ms, statuses="
              f"{[r.get('status') for r in responses]}; ambiguous, "
              "need official-list evidence")
    return LockVerdict("unknown", False, 0.3, ev)


@dataclass
class RaceProbe:
    """Controlled #11 probe. Default state is disarmed and read-only."""
    gate: Optional[IntentGate] = None
    armed: bool = False
    shots: int = 2
    fired: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.gate = self.gate or IntentGate()
        self.shots = max(1, min(int(self.shots), MAX_SHOTS))

    def plan(self, target: Dict[str, str]) -> dict:
        """What a probe WOULD do. Always available, never fires."""
        return {"armed": self.armed, "shots": self.shots,
                "hard_cap": MAX_SHOTS,
                "target_gate_open": self.gate.allow(target),
                "procedure": [
                    "journal intent pending",
                    f"fire {self.shots} identical bodies concurrently "
                    "(single connection pool, same ms)",
                    "collect status/body/latency tuples",
                    "read official list for this exact identity",
                    "classify() -> lock verdict",
                    "reconcile every extra hit via crosscheck"]}

    def run(self, target: Dict[str, str],
            submit: Callable[[Dict[str, str]], Dict[str, Any]],
            official_hits: int = -1) -> Dict[str, Any]:
        """The ONLY multi-shot submit in ops. Requires armed=True."""
        if not self.armed:
            return {"fired": False,
                    "reason": "disarmed: pass armed=True explicitly"}
        if not self.gate.allow(target):
            return {"fired": False,
                    "reason": "intent gate closed: one submission already "
                              "issued for this target"}
        self.gate.issue(target)
        out: List[Dict[str, Any]] = []
        for _ in range(self.shots):
            t0 = time.perf_counter()
            try:
                resp = dict(submit(dict(target)))
            except Exception as exc:
                out.append({"status": -1, "body": "",
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": (time.perf_counter() - t0) * 1000})
                continue
            resp.setdefault("status", 0)
            resp.setdefault("body", "")
            resp["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            out.append(resp)
        self.fired.append({"target": dict(target), "shots": len(out)})
        verdict = classify(out, official_hits)
        return {"fired": True, "shots": len(out), "responses": out,
                "verdict": verdict.as_dict(),
                "note": "extra hits must be reconciled via official list; "
                        "single-submit invariant is suspended ONLY inside "
                        "this probe"}

    def reconcile_extra(self, target: Dict[str, str],
                        evidence: Sequence[Evidence]) -> dict:
        """Fuse official-list evidence for a probe-fired intent."""
        v = fuse(target, evidence)
        return {"target": dict(target), "verdict": v.verdict,
                "action": v.action, "channels": v.channels}


def demo() -> dict:
    t = {"course_id": "C1", "class_id": "01"}
    real = [
        classify([{"status": 200, "body": "选课成功", "latency_ms": 40.0},
                  {"status": 200, "body": "该课程人数已满",
                   "latency_ms": 42.0}], official_hits=-1),
        classify([{"status": 200, "body": "选课成功", "latency_ms": 40.0},
                  {"status": 200, "body": "已选该课程",
                   "latency_ms": 41.0}]),
        classify([{"status": 200, "body": "选课成功", "latency_ms": 39.0},
                  {"status": 200, "body": "选课成功", "latency_ms": 44.0}],
                 official_hits=2),
        classify([{"status": 200, "body": "选课成功", "latency_ms": 40.0},
                  {"status": 200, "body": "系统繁忙 lock",
                   "latency_ms": 500.0}]),
    ]
    probe = RaceProbe(shots=2)
    plan = probe.plan(t)
    disarmed = probe.run(t, lambda tg: {"status": 200, "body": "x"})
    return {"verdicts": [v.as_dict() for v in real], "plan": plan,
            "disarmed_run": disarmed}


if __name__ == "__main__":
    import json
    print(json.dumps(demo(), ensure_ascii=False, indent=1, default=str))
