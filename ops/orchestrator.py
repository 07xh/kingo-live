"""orchestrator - six-phase pipeline gluing all ops modules together.

Phases: recon -> plan -> arm -> watch -> act -> verify.

Design invariants (enforced, not advisory):

* ``recon`` / ``plan`` / ``watch`` / ``verify`` are read-only; the only phase
  that may produce a submission decision is ``act``, and even then the
  orchestrator never performs the write itself -- it hands an explicit,
  single-fire order to a caller-supplied executor callback.
* One physical submission per target for the whole process lifetime
  (delegated to :class:`seat_events.IntentGate`).
* Missing real-interface evidence keeps every write-related field disabled:
  without an executor callback the pipeline degrades to a monitoring loop.

Zero third-party dependencies. Python 3.10+.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:                                                   # package or direct run
    from .endpoints import Endpoint, probe_all
    from .preflight import TicketPool, BodyPrebuilder, ConnectionShard, \
        HighResClock, assemble
    from .seat_events import AdaptivePoller, ReleaseMonitor, TargetRanker, \
        IntentGate, EventDriver, Offering, Decision, parse_retry_after_ms
    from .crosscheck import Evidence, fuse, reconcile
except ImportError:                                    # pragma: no cover
    from endpoints import Endpoint, probe_all
    from preflight import TicketPool, BodyPrebuilder, ConnectionShard, \
        HighResClock, assemble
    from seat_events import AdaptivePoller, ReleaseMonitor, TargetRanker, \
        IntentGate, EventDriver, Offering, Decision, parse_retry_after_ms
    from crosscheck import Evidence, fuse, reconcile

__all__ = ["Phase", "Mission", "Plan", "Orchestrator", "SimpleResponse",
           "PHASES"]

PHASES = ("recon", "plan", "arm", "watch", "act", "verify")


class Phase:
    """Phase machine: recon -> plan -> arm strictly forward; once armed the
    pipeline runs a steady-state watch/act/verify loop (a real monitor keeps
    polling after issuing an order)."""

    ALLOWED = {"recon": "plan", "plan": "arm", "arm": "watch"}
    POST_ARM = ("watch", "act", "verify")

    def __init__(self, current: str = "recon"):
        if current not in PHASES:
            raise ValueError(f"unknown phase {current!r}")
        self.current = current
        self.history: List[Tuple[str, str]] = [(current, "init")]

    def advance(self, reason: str = "") -> str:
        nxt = Phase.ALLOWED.get(self.current)
        if nxt is None:
            raise RuntimeError(
                "armed loop is steady-state; no forward transition left")
        self.current = nxt
        self.history.append((nxt, reason))
        return nxt

    def require(self, *allowed: str) -> None:
        if self.current in Phase.POST_ARM and \
                any(a in Phase.POST_ARM for a in allowed):
            return                     # armed cluster: watch/act/verify
        if self.current not in allowed:
            raise RuntimeError(
                f"phase={self.current}; this step needs one of {allowed}")

    def report(self) -> dict:
        return {"current": self.current, "history": self.history,
                "setup_forward_only": True,
                "armed_loop": self.current in Phase.POST_ARM}


@dataclass
class SimpleResponse:
    """Minimal response view the orchestrator needs from the caller."""
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: str = ""
    ttfb_ms: float = 0.0


@dataclass
class Mission:
    """Everything the caller declared up front. No secrets live here."""
    host: str = "TARGET_HOST"
    targets: List[Dict[str, Any]] = field(default_factory=list)
    poll_interval_base_ms: float = 60000.0
    poll_interval_hot_ms: float = 2000.0
    hot_rounds: int = 10
    sessions: int = 1
    respect_retry_after: bool = True
    executor: Optional[Callable[[Dict[str, Any]], Any]] = None
    # Explicit verified scope for the shared, persistent intent fence.
    # A human-readable account nickname alone is not authoritative.
    verified_school_origin: str = ""
    verified_account_id: str = ""
    enrollment_semester: str = ""
    shared_intent_db: Optional[str] = None
    catalog_path: str = "/hsjw//wap/mycourseschedule.action"
    ticket_names: Tuple[str, ...] = ("token", "csrf", "_csrf")

    def public(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()
             if k not in ("executor", "targets", "verified_account_id",
                          "shared_intent_db")}
        d["verified_account_id"] = "<configured>" if self.verified_account_id else "<missing>"
        d["shared_intent_db"] = "<configured>" if self.shared_intent_db else "<default>"
        d["targets"] = [
            {k: ("<redacted>" if any(h in k.lower() for h in
                ("cookie", "token", "password", "secret", "authorization",
                 "credential", "session", "key"))
                 else v) for k, v in t.items()}
            for t in self.targets]
        d["executor"] = "attached" if self.executor else "monitor-only"
        return d


@dataclass
class Plan:
    """Concrete, actionable output of the ``plan`` phase."""
    channel: str = ""
    channel_reason: str = ""
    window_gate: str = "unknown"
    sessions: int = 1
    poll_base_ms: float = 60000.0
    poll_hot_ms: float = 2000.0
    hot_rounds: int = 10
    respect_retry_after: bool = True
    prebuild: bool = False
    ticket_ttl_s: float = 300.0
    transport: dict = field(default_factory=dict)
    clock: dict = field(default_factory=dict)
    rule: str = "unknown"
    notes: List[str] = field(default_factory=list)

    def public(self) -> dict:
        return dict(self.__dict__)


def build_plan(recon_report: Optional[dict] = None,
               endpoints_report: Optional[dict] = None,
               p5_report: Optional[dict] = None,
               mission: Optional[Mission] = None) -> Plan:
    """Pure function: evidence in, concrete plan out. No I/O."""
    plan = Plan(sessions=max(1, mission.sessions) if mission else 1,
                poll_base_ms=(mission.poll_interval_base_ms if mission else 60000.0),
                poll_hot_ms=(mission.poll_interval_hot_ms if mission else 2000.0),
                hot_rounds=(mission.hot_rounds if mission else 10),
                respect_retry_after=(mission.respect_retry_after
                                     if mission else True))
    er = endpoints_report or {}
    if er:
        plan.channel = str(er.get("channel", ""))
        plan.channel_reason = str(er.get("reason", ""))
        gate = er.get("window_gate") or er.get("gate")
        if isinstance(gate, dict):
            gate = gate.get("kind") or gate.get("verdict")
        plan.window_gate = str(gate or "unknown")
        if er.get("independent") is True:
            plan.notes.append("independent channel detected: keep one "
                              "entrance, probe variants only read-only")
    if recon_report:
        dns = recon_report.get("dns")
        if isinstance(dns, dict):
            nodes = dns.get("addresses") or dns.get("a_records")
            if isinstance(nodes, list) and len(nodes) > 1:
                plan.notes.append(f"{len(nodes)} A records: shard across "
                                  "nodes")
        clock = recon_report.get("clock")
        if isinstance(clock, dict):
            plan.clock = {"phase_offset_ms": clock.get("phase_offset_ms"),
                          "rtt_median_ms": clock.get("rtt_median_ms")}
    if isinstance(p5_report, dict):
        rule = str(p5_report.get("rule", "unknown"))
        plan.rule = rule
        strat = p5_report.get("strategy") or {}
        if isinstance(strat, dict):
            if strat.get("poll_interval_ms"):
                plan.poll_base_ms = float(strat["poll_interval_ms"])
            if strat.get("sessions"):
                plan.sessions = max(1, int(strat["sessions"]))
        plan.notes.append(f"rule={rule}: strategy folded into plan")
    else:
        plan.notes.append("p5 evidence absent: rule stays unknown, "
                          "poll stays conservative")
    # Monitor-only missions must never inherit aggressive polling.
    if mission is None or mission.executor is None:
        plan.poll_hot_ms = max(plan.poll_hot_ms, 2000.0)
        plan.notes.append("monitor-only: no executor attached, act phase "
                          "will emit nothing but observations")
    return plan


class Orchestrator:
    """Forward-only pipeline. ``fetch_catalog`` is caller-supplied I/O;
    everything else stays inside ops."""

    def __init__(self, mission: Mission, plan: Optional[Plan] = None):
        self.mission = mission
        self.plan = plan or build_plan(mission=mission)
        self.phase = Phase("recon")
        self.driver = EventDriver(
            [dict(t) for t in mission.targets],
            poller=AdaptivePoller(mission.poll_interval_base_ms,
                                  mission.poll_interval_hot_ms,
                                  mission.hot_rounds),
            monitor=ReleaseMonitor(),
            ranker=TargetRanker(),
            gate=IntentGate())
        self.pool = TicketPool(ttl_s=self.plan.ticket_ttl_s)
        self.bodies = BodyPrebuilder()
        self.shard = ConnectionShard([mission.host], sessions=self.plan.sessions)
        self._apply_plan_runtime()
        self.clock = HighResClock()
        self.verdicts: List[dict] = []
        self.observations: List[dict] = []
        self.orders: List[dict] = []
        self._executed: set = set()

    def _apply_plan_runtime(self) -> None:
        """Apply the selected Plan without resetting live monitor/intent state."""
        poller = self.driver.poller
        poller.base = float(self.plan.poll_base_ms)
        poller.hot = float(self.plan.poll_hot_ms)
        poller.hot_rounds = max(1, int(self.plan.hot_rounds))
        self.pool.ttl_s = float(self.plan.ticket_ttl_s)
        self.shard = ConnectionShard([self.mission.host],
                                     sessions=max(1, int(self.plan.sessions)))

    # -- phase: recon ---------------------------------------------------
    def recon_probe(self, fetch: Callable[[Endpoint], SimpleResponse],
                    endpoints: Sequence[Endpoint]) -> dict:
        """Read-only matrix probe; delegates to endpoints.probe_all."""
        self.phase.require("recon")
        results = probe_all(fetch, endpoints)
        rep = {"probed": len(results),
               "rows": [{"name": r.name, "channel": r.channel,
                         "status": getattr(r, "status", None)}
                        for r in results]}
        self.phase.advance("recon matrix captured")
        return rep

    # -- phase: plan ----------------------------------------------------
    def plan_with(self, recon_report: Optional[dict] = None,
                  endpoints_report: Optional[dict] = None,
                  p5_report: Optional[dict] = None) -> Plan:
        # Planning is a pure function; allow it from watch too so a running
        # monitor can fold in fresh evidence without leaving its loop.
        self.phase.require("recon", "plan", "watch")
        self.plan = build_plan(recon_report, endpoints_report, p5_report,
                               self.mission)
        self._apply_plan_runtime()
        if self.phase.current in ("recon", "plan"):
            self.phase.advance("plan built")
        return self.plan

    # -- phase: arm -----------------------------------------------------
    def arm(self, prebuilt: Optional[Dict[str, Dict[str, str]]] = None) -> dict:
        """Prepare everything that can be prepared before the window.

        ``prebuilt`` maps intent-key -> {field: value}; bodies are serialised
        here so the timed path only does a socket write. No network happens.
        """
        self.phase.require("arm", "watch")
        prep = assemble(self.pool, self.bodies, self.shard, self.clock)
        if prebuilt:
            for key, fields in prebuilt.items():
                self.bodies.prebuild(key, fields)
        prep["bodies_sealed"] = self.bodies.report()["keys"]
        prep["clock_resolution_ns"] = self.clock.resolution_ns()
        if self.phase.current == "arm":
            self.phase.advance("armed")
        return prep

    # -- phase: watch ---------------------------------------------------
    def watch(self, response: SimpleResponse,
              parse_offers: Callable[[str], List[Offering]]) -> Decision:
        """One read-only catalog poll through the event pipeline."""
        self.phase.require("watch", "arm")
        offers = parse_offers(response.body) if response.status == 200 else []
        retry_ms = (parse_retry_after_ms(response.headers)
                    if response.status in (429, 503) else 0.0)
        d = self.driver.poll(offers, response.ttfb_ms, response.status,
                             retry_after_ms=retry_ms)
        self.observations.append({
            "t": round(time.time(), 3), "status": response.status,
            "offers": len(offers), "decision": d.kind})
        return d

    # -- phase: act -----------------------------------------------------
    def act(self, decision: Decision) -> dict:
        """Translate a fire decision into a single, explicit order.

        The orchestrator does not own the socket: the executor callback from
        ``Mission`` performs the physical write. Without one, act is a no-op
        and the target is marked pending for the verify phase.
        """
        self.phase.require("watch", "act")
        if decision.kind != "submit" or decision.action != "fire":
            return {"acted": False, "reason": f"decision={decision.kind}"}
        target = dict(decision.target or {})
        if not target:
            return {"acted": False, "reason": "empty target"}
        key = IntentGate.key(target)
        if key in self._executed:
            return {"acted": False,
                    "reason": "order already executed for this target"}
        if not (str(target.get("course_id") or "").strip()
                and str(target.get("class_id") or "").strip()):
            self.driver.gate.mark_pending(target, "missing exact teaching-class identity")
            self.orders.append({"target": target, "acted": False,
                                "reason": "missing exact teaching-class identity"})
            return {"acted": False, "reason": "missing exact teaching-class identity"}
        if self.mission.executor is None:
            self.driver.gate.mark_pending(target, "monitor-only: no executor")
            self.orders.append({"target": target, "acted": False,
                                "reason": "monitor-only"})
            return {"acted": False, "reason": "monitor-only, intent pending"}
        body = self.bodies.sealed(IntentGate.key(target)) \
            if self.plan.prebuild else None
        order = {"target": target, "body_prebuilt": body is not None,
                 "channel": self.plan.channel}
        # A process-only gate cannot survive restart or protect other
        # executables. Fence locally before attempting the shared reservation.
        self._executed.add(key)
        try:
            origin = self.mission.verified_school_origin
            account = self.mission.verified_account_id
            term = self.mission.enrollment_semester
            parsed = urlsplit(origin)
            if (parsed.scheme not in ("https", "http") or not parsed.netloc
                    or not account.strip() or not term.strip()):
                raise ValueError("school, account or term identity not verified")
            repo_root = str(Path(__file__).resolve().parents[1])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from contracts.intent_bridge import reserve_shared_intent
            database = (os.environ.get("KINGO_INTENT_DB")
                        if "KINGO_INTENT_DB" in os.environ
                        else self.mission.shared_intent_db)
            reserved = reserve_shared_intent(
                school=origin, account=account, term=term,
                course_id=str(target["course_id"]),
                class_id=str(target["class_id"]), path=database)
        except Exception as exc:
            order["acted"] = False
            order["reason"] = f"shared_intent_unavailable:{type(exc).__name__}"
            self.driver.gate.mark_pending(target, order["reason"])
            self.orders.append(order)
            return order
        if not reserved:
            order["acted"] = False
            order["reason"] = "shared_intent_pending_needs_official_reconciliation"
            self.driver.gate.mark_pending(target, order["reason"])
            self.orders.append(order)
            return order
        # SQLite reservation is durable before any possible socket write.
        try:
            result = self.mission.executor(target)
            order["callback_returned"] = True
            order["result_type"] = type(result).__name__
        except Exception as exc:  # executor faults must not loop re-fires
            self.driver.gate.mark_pending(target, f"executor_error:{type(exc).__name__}")
            order["acted"] = False
            order["error"] = type(exc).__name__
            self.orders.append(order)
            return order
        order["acted"] = True
        self.orders.append(order)
        return order

    # -- phase: verify --------------------------------------------------
    def verify(self, evidence: Sequence[Evidence]) -> List[dict]:
        """Fuse all channels for every pending/issued intent.

        Read-only: lagging lists are down-weighted, never treated as proof
        of failure. Terminates the pipeline.
        """
        self.phase.require("act", "verify")
        pending = [o["target"] for o in self.orders if not o.get("acted")]
        out = [d.as_dict() for d in reconcile(pending, evidence)]
        for o in self.orders:
            if o.get("acted"):
                v = fuse(o["target"], evidence)
                out.append({"target": o["target"], "verdict": v.verdict,
                            "action": v.action, "channels": v.channels,
                            "reason": v.reason})
        self.verdicts = out
        return out

    def report(self) -> dict:
        return {"phase": self.phase.report(), "plan": self.plan.public(),
                "mission": self.mission.public(),
                "driver": self.driver.report(),
                "observations": self.observations[-20:],
                "orders": [{k: v for k, v in o.items()
                            if k not in ("target", "result")}
                           for o in self.orders],
                "verdicts": self.verdicts}


def demo() -> dict:
    """End-to-end offline walkthrough: monitor-only mission, one release."""
    mission = Mission(host="loopback", targets=[
        {"course_id": "C1", "class_id": "01", "priority": 1}])
    orch = Orchestrator(mission)
    orch.phase = Phase("watch")  # demo starts mid-pipeline; recon skipped

    p5 = {"rule": "fcfs", "strategy": {"poll_interval_ms": 2000.0,
                                       "sessions": 2}}
    orch.plan_with({"dns": {"addresses": ["10.0.0.1", "10.0.0.2"]},
                    "clock": {"phase_offset_ms": 12.0,
                              "rtt_median_ms": 18.0}},
                   {"channel": "web", "reason": "single live channel",
                    "window_gate": {"kind": "server_time"}},
                   p5)

    def parse(_html: str) -> List[Offering]:
        return [Offering("C1", "01", 60, 0)]

    d1 = orch.watch(SimpleResponse(200, {}, "<table/>", 18.0), parse)
    d2 = orch.watch(SimpleResponse(200, {}, "<table/>", 17.0),
                    lambda _: [Offering("C1", "01", 60, 2)])
    arm = orch.arm({"C1|01": {"kcid": "C1", "skbj": "01"}})
    act = orch.act(d2)
    ev = [Evidence("wap_schedule", "C1", "01", None, 40.0, False, "wap",
                   time.time())]
    verify = orch.verify(ev)
    return {"poll1": d1.as_dict(), "poll2": d2.as_dict(), "arm": arm,
            "act": act, "verify": verify,
            "phase": orch.phase.report()["current"]}


if __name__ == "__main__":
    import json
    print(json.dumps(demo(), ensure_ascii=False, indent=1, default=str))
