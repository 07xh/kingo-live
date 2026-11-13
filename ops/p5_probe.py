#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/p5_probe.py - decide which server-side rules actually apply.

The offline benchmark in tools/bench/academic_latency_sim.py says client speed
only matters under FCFS, and that concurrency is the worst column. Those numbers
come from a self-authored model, not from the live system. This module turns
that open question into two measurable decisions:

  classify_allocation()   arrival rank vs success -> fcfs | random | priority
  classify_rate_dimension()  429 rate grouped by ip/session/account/node ->
                             which key owns the rate bucket
  recommend_strategy()    the two hints -> concrete strategy values

Pure functions, standard library only, Python 3.10+. No network access.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

STRATEGY_BASELINE = {
    "concurrency": 1,
    "sessions": 1,
    "burst": 0,
    "poll_interval_ms": 60000.0,
    "hot_interval_ms": 2000.0,
    "respect_retry_after": True,
    "prebuild": False,
    "direct_api": False,
    "timeout": 3.0,
    "single_physical_submit": True,
}


def _ranks(values: Sequence[float]) -> List[float]:
    """Average ranks, ties shared."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    return pearson(_ranks(xs), _ranks(ys))


def classify_allocation(observations: Sequence[Tuple[int, int]],
                        priorities: Optional[Sequence[float]] = None) -> dict:
    """observations: (arrival_rank, success) pairs from one controlled round.

    arrival_rank 1 = earliest request. success 0/1.
    priorities optional per-observation priority score for the priority rule.
    """
    if len(observations) < 4:
        return {"hint": "inconclusive", "rho_rank": 0.0, "n": len(observations),
                "confidence": 0.0, "basis": "need >= 4 observations"}
    ranks = [float(a) for a, _ in observations]
    wins = [float(b) for _, b in observations]
    rho = round(spearman(ranks, wins), 4)
    rho_prio = 0.0
    if priorities is not None and len(priorities) == len(observations):
        rho_prio = round(spearman(list(priorities), wins), 4)
    n = len(observations)
    if rho <= -0.6:
        hint, conf = "fcfs", min(0.95, 0.5 + abs(rho) / 2.0)
        basis = "earlier arrival strongly predicts success"
    elif rho >= 0.6:
        hint, conf = "inverted", min(0.9, 0.4 + abs(rho) / 2.0)
        basis = "later arrival wins: likely batch/queue reset, re-measure"
    elif abs(rho) < 0.35 and rho_prio >= 0.6:
        hint, conf = "priority", min(0.9, 0.4 + rho_prio / 2.0)
        basis = "success tracks the priority score, not arrival rank"
    elif abs(rho) < 0.35:
        hint, conf = "random", min(0.85, 0.5 + (0.35 - abs(rho)))
        basis = "no rank/priority correlation; speedups are noise"
    else:
        hint, conf = "inconclusive", 0.35
        basis = "weak correlation, collect another round"
    return {"hint": hint, "rho_rank": rho, "rho_priority": rho_prio,
            "n": n, "confidence": round(conf, 3), "basis": basis}


def classify_rate_dimension(samples: Sequence[Dict[str, Any]]) -> dict:
    """samples: {'dimension': str, 'key': str, 'requests': int, 'limited': int}.

    A dimension is the bucket key when different keys under it show clearly
    different 429 rates (i.e. the limiter separates them).
    """
    groups: Dict[str, Dict[str, List[int]]] = {}
    for s in samples:
        dim, key = str(s["dimension"]), str(s["key"])
        req = int(s.get("requests", 0))
        lim = int(s.get("limited", 0))
        cur = groups.setdefault(dim, {}).setdefault(key, [0, 0])
        cur[0] += req
        cur[1] += lim
    per_dimension = {}
    for dim, keys in groups.items():
        rates = {k: (v[1] / v[0] if v[0] else 0.0) for k, v in keys.items()}
        vals = list(rates.values())
        spread = round(max(vals) - min(vals), 4) if len(vals) >= 2 else 0.0
        per_dimension[dim] = {
            "keys": len(keys), "rates": {k: round(v, 4) for k, v in rates.items()},
            "spread": spread,
        }
    if not per_dimension:
        return {"hint": "inconclusive", "per_dimension": {}, "confidence": 0.0,
                "basis": "no samples"}
    ranked = sorted(per_dimension.items(), key=lambda kv: -kv[1]["spread"])
    dim, info = ranked[0]
    if info["spread"] < 0.3:
        return {"hint": "inconclusive", "per_dimension": per_dimension,
                "confidence": 0.2,
                "basis": "no dimension separates the 429 rates"}
    return {"hint": dim, "per_dimension": per_dimension,
            "confidence": round(min(0.9, 0.4 + info["spread"] / 2.0), 3),
            "basis": f"{dim} keys show the largest 429-rate spread"}


def recommend_strategy(allocation: dict, rate: dict) -> dict:
    out = dict(STRATEGY_BASELINE)
    notes: List[str] = []
    hint = allocation.get("hint")
    if hint == "fcfs":
        out.update({"prebuild": True, "hot_interval_ms": 1000.0,
                    "poll_interval_ms": 5000.0, "timeout": 2.0})
        notes.append("FCFS: relative arrival rank is the only lever; align the "
                     "server second boundary and keep one prebuilt request")
    elif hint in ("random", "priority"):
        out.update({"poll_interval_ms": 2000.0, "hot_interval_ms": 1000.0})
        notes.append(f"{hint}: timing is noise; spend effort on target selection "
                     "and the exact seat-release instant")
    else:
        notes.append("allocation unknown: keep baseline, do not raise concurrency")
    dim = rate.get("hint")
    if dim == "session":
        out.update({"sessions": 3})
        notes.append("limiter keys on session: independent Cookie jars give "
                     "independent buckets, still one physical submit per target")
    elif dim == "ip":
        out.update({"sessions": 2})
        notes.append("limiter keys on source IP: pin distinct backend A records "
                     "or distinct egress IPs; verify each bucket separately")
    elif dim == "account":
        notes.append("limiter keys on account: extra sessions share one bucket, "
                     "no concurrency gain available")
    elif dim == "node":
        notes.append("limiter keys on backend node: bind each Transport to a "
                     "different resolved A record with the Host header unchanged")
    else:
        notes.append("rate dimension unknown: keep concurrency=1")
    if out["sessions"] > 1 and hint == "fcfs":
        out["concurrency"] = 1
        notes.append("concurrency stays 1: parallel POSTs lose rank in FCFS")
    return {"strategy": out, "notes": notes,
            "allocation": allocation, "rate_dimension": rate}


def demo() -> dict:
    fcfs = [(1, 1), (2, 1), (3, 1), (4, 0), (5, 0), (6, 0)]
    random_round = [(1, 0), (2, 1), (3, 0), (4, 1), (5, 0), (6, 1)]
    samples = [
        {"dimension": "ip", "key": "A", "requests": 20, "limited": 18},
        {"dimension": "ip", "key": "B", "requests": 20, "limited": 2},
        {"dimension": "session", "key": "S1", "requests": 20, "limited": 10},
        {"dimension": "session", "key": "S2", "requests": 20, "limited": 10},
    ]
    a = classify_allocation(fcfs)
    r = classify_rate_dimension(samples)
    return {"fcfs": a, "random": classify_allocation(random_round),
            "rate": r, "recommendation": recommend_strategy(a, r)}


if __name__ == "__main__":
    import json
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
