#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/crosscheck.py - #13 multi-channel official readback fusion.

The selected-course list is often cached: an HTTP 200 right after a submit can
still say "not selected". Treating that single stale read as truth turns a real
success into an endless pending intent. This module fuses independent, all
read-only channels and emits one explicit verdict.

  CHANNELS         official_list / wap_schedule / credit_summary / zxxq
  Evidence         channel, target identity, present, latency, stale hint
  CrossChecker     weighted fusion; stale reads are down-weighted, not ignored
  reconcile        pending intents -> one Decision each, never a re-submit

Invariants: HTTP 200 != success; an unknown result stays pending; a confirmed
intent is terminal. Nothing here opens a socket or emits a write request.

Standard library only, Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:                                                   # package or direct run
    from .seat_events import Decision
except ImportError:                                    # pragma: no cover
    from seat_events import Decision

CHANNEL_WEIGHTS: Dict[str, float] = {
    "official_list": 1.0,        # authoritative but frequently cached
    "wap_schedule": 0.8,         # personal timetable on the wap channel
    "credit_summary": 0.6,       # total credits / selected count
    "zxxq": 0.5,                 # structured selection payload
}
STALE_PENALTY = 0.30             # a stale read keeps 30% of its weight
CONFIRM_THRESHOLD = 1.20         # >= 2 independent channels, or 1 strong + 1 weak


@dataclass
class Evidence:
    channel: str
    course_id: str
    class_id: str = ""
    present: Optional[bool] = None     # None = channel did not answer
    latency_ms: float = 0.0
    stale: bool = False                # cached / older than the submit
    source: str = ""
    at: float = field(default_factory=time.time)

    def public(self) -> dict:
        d = asdict(self)
        d["at"] = round(self.at, 3)
        return d


@dataclass
class Verdict:
    course_id: str
    class_id: str
    verdict: str                       # confirmed | pending | contradicted | unknown
    confidence: float
    positive: float
    negative: float
    channels: List[str]
    stale_channels: List[str]
    reason: str
    action: str                        # terminal | keep_pending | human_check

    def public(self) -> dict:
        return asdict(self)


def _weight(e: Evidence) -> float:
    base = CHANNEL_WEIGHTS.get(e.channel, 0.3)
    if e.stale:
        base *= STALE_PENALTY
    return base


def fuse(target: Dict[str, str], evidence: Iterable[Evidence]) -> Verdict:
    """Fuse all channels that answer for this exact (course_id, class_id)."""
    cid = str(target.get("course_id", ""))
    cls = str(target.get("class_id", ""))
    rows = [e for e in evidence
            if e.course_id == cid and (not cls or e.class_id in ("", cls))]
    pos = neg = 0.0
    used: List[str] = []
    stale: List[str] = []
    authoritative_negative = False
    for e in rows:
        if e.present is None:
            continue
        used.append(e.channel)
        if e.stale:
            stale.append(e.channel)
        w = _weight(e)
        if e.present:
            pos += w
        else:
            neg += w
            if e.channel == "official_list" and not e.stale:
                authoritative_negative = True

    independent_pos = {e.channel for e in rows
                       if e.present and not e.stale}
    if not rows or not used:
        v, conf, reason, action = "unknown", 0.0, \
            "no channel answered for this target", "keep_pending"
    elif authoritative_negative and pos > 0:
        v, conf, reason, action = "contradicted", 0.5, \
            "fresh official list says absent while another channel says present", \
            "human_check"
    elif pos >= CONFIRM_THRESHOLD and len(independent_pos) >= 2:
        v, conf, reason, action = "confirmed", min(0.99, 0.6 + 0.1 * pos), \
            "two or more independent non-stale channels agree on presence", \
            "terminal"
    elif pos >= 1.0 and "official_list" in independent_pos:
        v, conf, reason, action = "confirmed", 0.85, \
            "fresh official list confirms the exact course identity", \
            "terminal"
    elif neg > 0 and all(e.stale for e in rows if e.present is False) and pos == 0:
        v, conf, reason, action = "pending", 0.3, \
            "only stale negative reads; a cached list cannot falsify a submit", \
            "keep_pending"
    elif pos > 0 and neg > 0:
        v, conf, reason, action = "pending", 0.4, \
            "channels disagree without a fresh authoritative negative", \
            "keep_pending"
    elif pos > 0:
        v, conf, reason, action = "pending", 0.5, \
            "weak/partial positive evidence; keep the intent pending", \
            "keep_pending"
    else:
        v, conf, reason, action = "pending", 0.2, \
            "all answers negative; never promote HTTP 200 to success", \
            "keep_pending"

    return Verdict(cid, cls, v, round(conf, 3), round(pos, 3), round(neg, 3),
                   sorted(set(used)), sorted(set(stale)), reason, action)


def reconcile(pending_intents: Sequence[Dict[str, str]],
              evidence: Iterable[Evidence]) -> List[Decision]:
    """One Decision per pending intent. Never emits a second submission."""
    rows = list(evidence)
    out: List[Decision] = []
    for intent in pending_intents:
        v = fuse(intent, rows)
        if v.action == "terminal":
            out.append(Decision(kind="halt", action="stop",
                                detail=f"confirmed via {','.join(v.channels)}",
                                target=dict(intent)))
        elif v.action == "human_check":
            out.append(Decision(kind="halt", action="stop",
                                detail="contradicted evidence; manual check",
                                target=dict(intent)))
        else:
            out.append(Decision(kind="wait", action="sleep",
                                detail=f"{v.verdict}: {v.reason}",
                                interval_ms=2000.0, target=dict(intent)))
    return out


def channel_plan() -> dict:
    return {"channels": {k: {"weight": w} for k, w in CHANNEL_WEIGHTS.items()},
            "stale_penalty": STALE_PENALTY,
            "confirm_threshold": CONFIRM_THRESHOLD,
            "all_read_only": True,
            "note": "parallel GETs only; a lagging list is down-weighted, "
                    "never treated as proof of failure"}


def demo() -> dict:
    t1 = {"course_id": "C1", "class_id": "01"}
    t2 = {"course_id": "C2", "class_id": "02"}
    t3 = {"course_id": "C3", "class_id": "03"}
    base = time.time()
    ev = [
        Evidence("official_list", "C1", "01", False, 40.0, True, "cached", base),
        Evidence("wap_schedule", "C1", "01", True, 55.0, False, "wap", base),
        Evidence("credit_summary", "C1", "01", True, 61.0, False, "credit", base),
        Evidence("official_list", "C2", "02", False, 38.0, False, "fresh", base),
        Evidence("wap_schedule", "C2", "02", True, 52.0, False, "wap", base),
        Evidence("official_list", "C3", "03", False, 41.0, True, "cached", base),
    ]
    verdicts = [fuse(t, ev).public() for t in (t1, t2, t3)]
    decisions = [d.as_dict() for d in reconcile([t1, t2, t3], ev)]
    return {"evidence": [e.public() for e in ev],
            "verdicts": verdicts, "decisions": decisions,
            "plan": channel_plan()}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = demo()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for v in report["verdicts"]:
            print(f"{v['course_id']}/{v['class_id']}: {v['verdict']:12s} "
                  f"pos={v['positive']} neg={v['negative']} "
                  f"action={v['action']} channels={v['channels']}")
        print("decisions=", [d["action"] for d in report["decisions"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
