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
    """Compare exact teaching-class evidence; never infer success from totals.

    Classless or mismatched reports are context, not proof of this class.
    A fresh exact official selected-course record is required for a terminal
    confirmation. Other channels still contribute to contradiction and
    stale-cache diagnostics, but do not substitute for that official record.
    """
    cid = str(target.get("course_id") or "").strip()
    cls = str(target.get("class_id") or "").strip()
    if not cid or not cls:
        return Verdict(cid, cls, "unknown", 0.0, 0.0, 0.0, [], [],
                       "missing exact course or teaching-class identity",
                       "keep_pending")

    rows = [e for e in evidence
            if e.course_id == cid and e.class_id == cls
            and e.channel in CHANNEL_WEIGHTS and e.present is not None]
    # Repeated responses from one channel are NOT independent evidence.
    # A channel contradicting itself also cannot support confirmation.
    by_channel: Dict[str, List[Evidence]] = {}
    for e in rows:
        by_channel.setdefault(e.channel, []).append(e)

    pos = neg = 0.0
    stale: List[str] = []
    conflicts: List[str] = []
    official_positive = False
    official_negative = False
    for channel, reports in by_channel.items():
        fresh = [e for e in reports if not e.stale]
        considered = fresh or reports
        opinions = {e.present for e in considered}
        if len(opinions) > 1:
            conflicts.append(channel)
            continue
        present = considered[0].present
        is_stale = not fresh
        if is_stale:
            stale.append(channel)
        weight = _weight(considered[0])
        if present:
            pos += weight
        else:
            neg += weight
        if channel == "official_list" and not is_stale:
            official_positive = present is True
            official_negative = present is False

    used = sorted(by_channel)
    if conflicts:
        v, conf, reason, action = ("contradicted", 0.2,
            "conflicting observations from the same channel: " + ",".join(sorted(conflicts)),
            "human_check")
    elif official_positive and neg > 0 and any(
            channel != "official_list" and not all(e.stale for e in reports)
            and any(e.present is False for e in reports)
            for channel, reports in by_channel.items()):
        v, conf, reason, action = ("contradicted", 0.5,
            "fresh official selection conflicts with another fresh class-level read",
            "human_check")
    elif official_positive:
        v, conf, reason, action = ("confirmed", 0.85,
            "fresh official selected-course list matches exact course and class",
            "terminal")
    elif official_negative and pos > 0:
        v, conf, reason, action = ("contradicted", 0.5,
            "fresh official list disagrees with class-level evidence",
            "human_check")
    elif not used:
        v, conf, reason, action = ("unknown", 0.0,
            "no exact teaching-class evidence", "keep_pending")
    elif pos > 0:
        v, conf, reason, action = ("pending", 0.5,
            "corroborating reads cannot replace verified official selected-course proof",
            "keep_pending")
    else:
        v, conf, reason, action = ("pending", 0.2,
            "no positive official proof; absence never proves rejection",
            "keep_pending")

    return Verdict(cid, cls, v, conf, round(pos, 3), round(neg, 3),
                   used, sorted(stale), reason, action)


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
