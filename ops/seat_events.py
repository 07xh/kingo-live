#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/seat_events.py - event-driven seat monitoring and target re-ranking.

Drop-in extension of tools/kingo-live/optimizations.py::ReleaseMonitor.
Same observe(offers, poll_cost_ms) contract, plus:

  AdaptivePoller   idle 60000 ms -> hot 2000 ms on a release event, bounded
                   hot rounds, cooldown on 429/5xx, backoff on overload.
  ReleaseMonitor   new / reopened / increased / decreased events, empty polls
                   cost nothing.
  TargetRanker     dynamic re-ordering by remaining/capacity, heat and the
                   static priority from config.targets.
  IntentGate       exactly one physical submission per (course_id, class_id),
                   pending results halt until reconcile() hits official readback.
  EventDriver      one poll -> one explicit Decision, never a blind retry.

Standard library only, Python 3.10+.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional


import sys
from pathlib import Path

try:
    from contracts.seat_diff import classify_seat_delta, remaining_count
except ModuleNotFoundError:
    _root = Path(__file__).resolve().parents[1]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from contracts.seat_diff import classify_seat_delta, remaining_count


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

@dataclass
class Offering:
    course_id: str
    class_id: str = ""
    capacity: int = 0
    remain: int = 0
    teacher: str = ""
    heat: float = 0.0            # 0.0 cold .. 1.0 hot, from history

    def fingerprint(self) -> str:
        return f"{self.course_id}|{self.class_id}"


@dataclass
class ReleaseEvent:
    kind: str                    # new | reopened | increased | decreased
    course_id: str
    class_id: str
    remain: int
    previous_remain: Optional[int]
    at_local: float
    poll_cost_ms: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    kind: str                    # submit | wait | switch | halt | cooldown
    action: str                  # fire | sleep | rerank | stop | backoff
    detail: str = ""
    interval_ms: float = 0.0
    target: Optional[dict] = None

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# adaptive polling
# ---------------------------------------------------------------------------

class AdaptivePoller:
    """Quiet at baseline, dense around a release event, backs off on overload."""

    def __init__(self, base_interval_ms: float = 60000.0,
                 hot_interval_ms: float = 2000.0,
                 hot_rounds: int = 10,
                 cooldown_ms: float = 30000.0,
                 backoff_ms: float = 200.0,
                 max_interval_ms: float = 60000.0):
        self.base = base_interval_ms
        self.hot = hot_interval_ms
        self.hot_rounds = max(1, hot_rounds)
        self.cooldown = cooldown_ms
        self.backoff = backoff_ms
        self.maximum = max_interval_ms
        self.state = "idle"
        self.hot_left = 0
        self.penalty_ms = 0.0
        self.polls = 0
        self.transitions: List[dict] = []

    def _move(self, state: str, reason: str) -> None:
        if state != self.state:
            self.transitions.append({"at": round(time.time(), 3),
                                     "from": self.state, "to": state,
                                     "reason": reason})
        self.state = state

    def on_event(self) -> None:
        self.hot_left = self.hot_rounds
        self.penalty_ms = 0.0
        self._move("hot", "release_event")

    def on_rate_limited(self, retry_after_ms: float = 0.0) -> None:
        self.penalty_ms = max(self.penalty_ms, retry_after_ms or self.cooldown)
        self.hot_left = 0
        self._move("cooldown", "http_429")

    def on_overload(self) -> None:
        self.penalty_ms = max(self.penalty_ms, self.backoff)
        self._move("cooldown", "http_5xx")

    def on_auth_failure(self) -> None:
        self.hot_left = 0
        self._move("halt", "auth")

    def next_interval(self) -> float:
        self.polls += 1
        if self.state == "halt":
            return 0.0
        if self.penalty_ms > 0.0:
            # A server Retry-After may exceed the local nominal poll maximum.
            # Never shorten a mandatory cooldown to the display interval cap.
            v = self.penalty_ms
            self.penalty_ms = 0.0
            self._move("idle", "penalty_served")
            return v
        if self.state == "hot" and self.hot_left > 0:
            self.hot_left -= 1
            if self.hot_left == 0:
                self._move("idle", "hot_rounds_exhausted")
            return min(self.hot, self.maximum)
        return min(self.base, self.maximum)

    def report(self) -> dict:
        return {"state": self.state, "polls": self.polls,
                "hot_left": self.hot_left, "penalty_ms": self.penalty_ms,
                "base_ms": self.base, "hot_ms": self.hot,
                "transitions": list(self.transitions)}


# ---------------------------------------------------------------------------
# snapshot diff
# ---------------------------------------------------------------------------

class ReleaseMonitor:
    """Diff consecutive real catalog reads. Empty polls produce no events."""

    def __init__(self):
        self.snapshot: Dict[str, Offering] = {}
        self.polls = 0
        self.events: List[ReleaseEvent] = []

    def observe(self, offers: Iterable[Any],
                poll_cost_ms: float = 0.0) -> List[ReleaseEvent]:
        self.polls += 1
        now = time.time()
        current = {self._fp(o): o for o in offers}
        out: List[ReleaseEvent] = []
        for fp, o in current.items():
            prev = self.snapshot.get(fp)
            kind = classify_seat_delta(
                o, prev, first_observation=prev is None,
                include_decrease=True)
            if kind is None:
                continue
            cur = remaining_count(o)
            prv = remaining_count(prev)
            # A classified event always has a known current count.
            out.append(self._event(kind, o, cur, prv, now, poll_cost_ms))
        self.snapshot = current
        self.events.extend(out)
        return out

    @staticmethod
    def _fp(o: Any) -> str:
        if hasattr(o, "fingerprint"):
            return o.fingerprint()
        return f"{getattr(o, 'course_id', '')}|{getattr(o, 'class_id', '')}"

    @staticmethod
    def _event(kind: str, o: Any, cur: int, prv: Optional[int],
               now: float, cost: float) -> ReleaseEvent:
        return ReleaseEvent(kind=kind,
                            course_id=str(getattr(o, "course_id", "")),
                            class_id=str(getattr(o, "class_id", "")),
                            remain=cur, previous_remain=prv,
                            at_local=round(now, 3),
                            poll_cost_ms=round(cost, 3))

    @staticmethod
    def trigger(events: List[ReleaseEvent]) -> bool:
        return any(e.kind in ("new", "reopened", "increased") for e in events)

    def report(self) -> dict:
        kinds = {k: sum(1 for e in self.events if e.kind == k)
                 for k in ("new", "reopened", "increased", "decreased")}
        return {"polls": self.polls, "tracked": len(self.snapshot),
                "events": len(self.events), "kinds": kinds}


# ---------------------------------------------------------------------------
# dynamic target ranking
# ---------------------------------------------------------------------------

class TargetRanker:
    """Score = w_remain*seat_ratio + w_heat*(1-heat) - w_priority*(rank-1).

    Static priority from config.targets stays as a tie-breaker, not a veto.
    """

    def __init__(self, w_remain: float = 0.5, w_heat: float = 0.3,
                 w_priority: float = 0.05):
        self.w_remain, self.w_heat = w_remain, w_heat
        self.w_priority = w_priority

    @staticmethod
    def _match(offers: List[Offering], target: dict) -> Optional[Offering]:
        want = str(target.get("class_id") or "").strip()
        for o in offers:
            if str(o.course_id) != str(target["course_id"]):
                continue
            if want and str(o.class_id) != want:
                continue
            return o
        return None

    def score(self, offer: Offering, priority: int = 1) -> float:
        cap = max(1, int(offer.capacity or 0))
        ratio = min(1.0, max(0.0, int(offer.remain or 0) / cap))
        heat = min(1.0, max(0.0, float(offer.heat or 0.0)))
        return round(self.w_remain * ratio
                     + self.w_heat * (1.0 - heat)
                     - self.w_priority * max(0, priority - 1), 6)

    def reorder(self, targets: List[dict],
                offers: List[Offering]) -> List[dict]:
        scored = []
        for i, t in enumerate(targets):
            if not t.get("enabled", True):
                continue
            o = self._match(offers, t)
            if o is None:
                continue
            prio = int(t.get("priority", i + 1))
            scored.append((self.score(o, prio), prio, dict(t)))
        scored.sort(key=lambda x: (-x[0], x[1], x[2].get("course_id", "")))
        return [x[2] for x in scored]


# ---------------------------------------------------------------------------
# one physical submission per target
# ---------------------------------------------------------------------------

class IntentGate:
    """idle -> issued -> pending -> confirmed | rejected. No second 'issued'."""

    def __init__(self):
        self.states: Dict[str, str] = {}
        self.history: List[dict] = []

    @staticmethod
    def key(target: dict) -> str:
        return f"{target.get('course_id', '')}|{target.get('class_id', '')}"

    def allow(self, target: dict) -> bool:
        return self.states.get(self.key(target), "idle") == "idle"

    def issue(self, target: dict) -> bool:
        k = self.key(target)
        if not self.allow(target):
            self.history.append({"key": k, "event": "blocked_duplicate"})
            return False
        self.states[k] = "issued"
        self.history.append({"key": k, "event": "issued", "at": time.time()})
        return True

    def mark_pending(self, target: dict, reason: str = "readback_unavailable") -> None:
        k = self.key(target)
        self.states[k] = "pending"
        self.history.append({"key": k, "event": "pending", "reason": reason})

    def reconcile(self, target: dict, official_hit: bool) -> str:
        k = self.key(target)
        # An absent or delayed readback is NOT proof that a write failed.
        # Retain pending unless an explicit exact official positive is found.
        state = "confirmed" if official_hit else "pending"
        self.states[k] = state
        self.history.append({"key": k, "event": state, "at": time.time()})
        return state

    def report(self) -> dict:
        return {"states": dict(self.states), "history": list(self.history)}


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

class EventDriver:
    def __init__(self, targets: List[dict],
                 poller: Optional[AdaptivePoller] = None,
                 monitor: Optional[ReleaseMonitor] = None,
                 ranker: Optional[TargetRanker] = None,
                 gate: Optional[IntentGate] = None):
        self.targets = [dict(t) for t in targets]
        self.poller = poller or AdaptivePoller()
        self.monitor = monitor or ReleaseMonitor()
        self.ranker = ranker or TargetRanker()
        self.gate = gate or IntentGate()
        self.decisions: List[Decision] = []

    def poll(self, offers: List[Offering], poll_cost_ms: float = 0.0,
             status: int = 200) -> Decision:
        if status in (401, 403):
            self.poller.on_auth_failure()
            d = Decision("auth", "stop", f"http={status}")
        elif status == 429:
            self.poller.on_rate_limited()
            d = Decision("rate_limited", "backoff", "http=429",
                         self.poller.next_interval())
        elif status in (500, 502, 503, 504):
            self.poller.on_overload()
            d = Decision("overload", "backoff", f"http={status}",
                         self.poller.next_interval())
        else:
            events = self.monitor.observe(offers, poll_cost_ms)
            if ReleaseMonitor.trigger(events):
                self.poller.on_event()
                ordered = self.ranker.reorder(self.targets, offers)
                if not ordered:
                    d = Decision("no_match", "sleep", "no target matched catalog",
                                 self.poller.next_interval())
                else:
                    target = ordered[0]
                    if not self.gate.allow(target):
                        d = Decision("duplicate", "stop",
                                     "one physical submission already issued",
                                     self.poller.next_interval(), target)
                    elif self.gate.issue(target):
                        d = Decision("submit", "fire",
                                     f"events={[e.kind for e in events]}",
                                     self.poller.next_interval(), target)
            else:
                d = Decision("quiet", "sleep", "no release event",
                             self.poller.next_interval())
        self.decisions.append(d)
        return d

    def report(self) -> dict:
        return {"poller": self.poller.report(),
                "monitor": self.monitor.report(),
                "gate": self.gate.report(),
                "decisions": [d.as_dict() for d in self.decisions]}


if __name__ == "__main__":
    demo = EventDriver([
        {"course_id": "C1", "class_id": "01", "priority": 1, "enabled": True},
        {"course_id": "C2", "class_id": "01", "priority": 2, "enabled": True},
    ])
    print(demo.poll([Offering("C1", "01", 60, 0)]).as_dict())   # quiet
    print(demo.poll([Offering("C1", "01", 60, 2)]).as_dict())   # submit
    print(demo.poll([Offering("C1", "01", 60, 3)]).as_dict())   # duplicate blocked
