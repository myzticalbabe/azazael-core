"""AOA Cross-Arm Router -- DAG-based workflow orchestration across Arms.

Extended 2026-09-14 (audit AOA_ARCHITECTURE_AUDIT_2026-09-14.md section 5,
steps 1-4). Three changes, all additive:

1. PROVENANCE ENVELOPE. Every result leaving this router now carries
   ``produced_at`` / ``stale_after_s`` / ``age_s`` / ``is_stale`` (see
   ``core.contracts``). Validation is warn-only, so no existing consumer
   changes behaviour and no Arm is forced to declare anything yet.

2. ELASTIC SELECTION. ``route_task`` still takes an explicit ``arm_id`` and
   behaves exactly as before. It may now be called with ``arm_id=None``, in
   which case the capability alone is routed: candidate Arms that DECLARE
   that capability are ranked by learned Q from the spine ledger and the
   best is chosen.

   Why Q is not enough on its own: ``get_q_weight`` returns 0.0 both for an
   Arm that has never run and for one that has settled neutral, so ranking
   purely on Q starves untried Arms permanently -- they score 0, are never
   chosen, and therefore never get scored. Four live inference providers
   independently flagged this cold-start collapse as the single highest risk
   in elastic selection. The guard is a minimum-pull quota: Arms below
   ``min_pulls`` are exercised FIRST (fewest pulls win), and Q-ranking only
   applies among Arms that have a measured record. That guarantees coverage
   rather than merely hoping epsilon-greedy supplies it, and epsilon remains
   available for ongoing exploration among proven Arms.

3. DECISION RECORDING. Every selection, its evidence, and its outcome are
   kept in a bounded replay log and published on the bus, so a run can be
   explained after the fact instead of reconstructed from inference.

Feedback-loop guard. This router both READS the Q store to select and WRITES
outcomes back to it, which is exactly the self-reinforcement loop the reviews
warned about. Three properties keep it bounded:

  * a write only ever happens AFTER execution completes, never at selection
    time, so a decision cannot influence the evidence used to score it;
  * ``spine_registry`` reinforces by EMA with alpha clamped to [0.05, 0.5],
    so a single outcome can never dominate;
  * writes are additionally capped per Arm per hour (``max_writes_per_arm_per_hour``),
    so one hot path cannot load the table.

Known remaining risk, deliberately NOT addressed here: ``EventBus.publish``
is synchronous and inline, so publishing routing events blocks the routing
thread. NVIDIA NIM flagged this. Moving the bus to a bounded async queue is
Plane 2 of the audit and touches every subscriber, so it is not folded into
this change.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from enum import Enum
from datetime import datetime
from collections import deque
import random
import threading
import time
import uuid

from .arm_registry import ArmRegistry, BaseArm
from .event_bus import EventBus, AOEvent, AOEventType, EventPriority, get_event_bus
from .contracts import (
    ArmResult,
    validate_result,
    get_contract_report,
    now_iso,
)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class StepStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# Nature used for arm-level Q in the spine ledger. Mirrors the existing
# "route" nature that MeshRouterWorm uses for env routing, but is kept
# SEPARATE on purpose: env-route reliability and arm capability reliability
# are different questions, and sharing one namespace would let a healthy
# network path promote an Arm that has never executed anything.
ARM_ROUTE_NATURE = "aoa_route"


@dataclass
class RoutingPolicy:
    """Tunables for elastic selection.

    ``min_pulls`` is the cold-start guard: below this many observed
    outcomes an Arm is "unproven" and is tried before any Q-ranking applies.
    ``max_writes_per_arm_per_hour`` bounds how much one Arm can influence the
    very table that selects it.
    """

    min_pulls: int = 30
    epsilon: float = 0.1
    max_writes_per_arm_per_hour: int = 60
    write_back: bool = True
    default_stale_after_s: Optional[float] = None
    replay_log_size: int = 500


@dataclass
class WorkflowStep:
    step_id: str
    arm_id: str
    capability: str
    payload: dict = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Optional[dict] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class WorkflowDAG:
    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    steps: Dict[str, WorkflowStep] = field(default_factory=dict)
    edges: Dict[str, List[str]] = field(default_factory=dict)

    def add_step(self, step: WorkflowStep):
        self.steps[step.step_id] = step
        self.edges[step.step_id] = list(step.depends_on)

    def get_ready_steps(self) -> List[WorkflowStep]:
        ready = []
        for step in self.steps.values():
            if step.status != StepStatus.PENDING:
                continue
            deps_satisfied = all(
                self.steps[dep].status == StepStatus.COMPLETED
                for dep in step.depends_on if dep in self.steps)
            if deps_satisfied:
                ready.append(step)
        return ready

    def get_topological_order(self) -> List[str]:
        in_degree = {sid: 0 for sid in self.steps}
        for sid, deps in self.edges.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[sid] += 1
        queue = deque([sid for sid, deg in in_degree.items() if deg == 0])
        order = []
        while queue:
            sid = queue.popleft()
            order.append(sid)
            for other_sid, deps in self.edges.items():
                if sid in deps:
                    in_degree[other_sid] -= 1
                    if in_degree[other_sid] == 0:
                        queue.append(other_sid)
        return order if len(order) == len(self.steps) else list(self.steps.keys())

    def is_complete(self) -> bool:
        return all(s.status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED)
                   for s in self.steps.values())

    def get_status_summary(self) -> dict:
        counts = {s: 0 for s in StepStatus}
        for step in self.steps.values():
            counts[step.status] += 1
        return {
            "workflow_id": self.workflow_id, "name": self.name,
            "total_steps": len(self.steps), "completed": counts[StepStatus.COMPLETED],
            "failed": counts[StepStatus.FAILED], "pending": counts[StepStatus.PENDING],
            "running": counts[StepStatus.RUNNING], "skipped": counts[StepStatus.SKIPPED],
        }


def _spine():
    """Import the spine ledger lazily; None when unavailable.

    Lazy on purpose: importing spine_registry at module scope would make the
    whole AOA router fail to load whenever the ledger cannot be imported,
    turning an optional learning signal into a hard dependency.
    """
    try:
        from CORE import spine_registry as spine
        return spine
    except Exception as exc:
        note_failure("router:L187", exc)
        return None


class CrossArmRouter:
    def __init__(self, registry: Optional[ArmRegistry] = None, bus: Optional[EventBus] = None,
                 policy: Optional[RoutingPolicy] = None):
        self.registry = registry or ArmRegistry()
        self.bus = bus or get_event_bus()
        self.policy = policy or RoutingPolicy()
        self._workflows: Dict[str, WorkflowDAG] = {}
        self._execution_history: List[dict] = []
        # Replay log: why each route was chosen, and how it turned out.
        self._routing_log: deque = deque(maxlen=self.policy.replay_log_size)
        self._log_lock = threading.Lock()
        # capability -> [arm_id], invalidated when the registry membership
        # changes. Keyed on content rather than latched forever: a cached
        # capability index that never re-derives is the same defect already
        # found and fixed in the live monitors.
        self._cap_index: Optional[Dict[str, List[str]]] = None
        self._cap_index_key: Optional[tuple] = None
        # Per-arm write timestamps for the hourly contribution cap.
        self._write_window: Dict[str, deque] = {}
        self._write_lock = threading.Lock()
        self._stats: Dict[str, int] = {
            "routed": 0, "pinned": 0, "elastic": 0,
            "cold_start": 0, "exploit": 0, "epsilon": 0,
            "degraded_reads": 0, "write_backs": 0, "write_backs_suppressed": 0,
            "no_candidate": 0, "arm_missing": 0,
        }
        self._stats_lock = threading.Lock()

    # ── capability discovery ───────────────────────────────────────────

    def _registry_key(self) -> tuple:
        try:
            return tuple(sorted(self.registry.arms.keys()))
        except Exception as exc:
            note_failure("router:L224", exc)
            return ("<unavailable>",)

    def capability_index(self) -> Dict[str, List[str]]:
        """Map capability name -> Arm ids that declare it.

        Every Arm is asked directly via ``get_capabilities()``. Reading the
        registry's summary instead would only give a COUNT of capabilities,
        not their names, and would make routing depend on a display field.
        """
        key = self._registry_key()
        if self._cap_index is not None and self._cap_index_key == key:
            return self._cap_index
        index: Dict[str, List[str]] = {}
        try:
            items = list(self.registry.arms.items())
        except Exception as exc:
            note_failure("router:L240", exc)
            items = []
        for arm_id, arm in items:
            try:
                caps = arm.get_capabilities() or []
            except Exception as exc:
                # An Arm whose capability listing raises is skipped for
                # ROUTING only; it stays registered and explicitly pinned
                # calls to it still work.
                # An Arm whose capability listing raises is skipped for
                # ROUTING only; it stays registered and explicitly pinned
                # calls to it still work.
                note_failure("router:L245", exc)
                continue
            for cap in caps:
                name = getattr(cap, "name", None)
                if name is None and isinstance(cap, dict):
                    name = cap.get("name")
                if name:
                    index.setdefault(str(name), []).append(arm_id)
        for names in index.values():
            names.sort()
        self._cap_index, self._cap_index_key = index, key
        return index

    def _arm_priority(self, arm_id: str) -> int:
        try:
            arm = self.registry.get_arm(arm_id)
            meta = arm.metadata if arm else None
            return int(getattr(meta, "priority", 0) or 0)
        except Exception as exc:
            note_failure("router:L266", exc)
            return 0

    # ── learned-Q evidence ─────────────────────────────────────────────

    def _read_evidence(self, capability: str, arm_ids: List[str]) -> tuple:
        """(evidence_by_arm, degraded, read_ms) for this capability.

        Reads are best-effort. On any failure the caller falls back to static
        registry priority and marks the decision degraded, so a missing or
        damaged ledger can never stop the router from routing -- it only
        stops it from *learning*.
        """
        started = time.perf_counter()
        evidence: Dict[str, Dict[str, Any]] = {}
        # ``_spine()`` guards its own import, but a read failure must never be
        # able to propagate out of selection: the documented contract is that
        # a damaged ledger stops the router LEARNING, never stops it ROUTING.
        # Caught here rather than assumed, because assuming it is exactly how
        # the degraded path becomes unreachable in practice.
        try:
            spine = _spine()
        except Exception as exc:
            note_failure("router:L288", exc)
            spine = None
        if spine is None:
            for arm_id in arm_ids:
                evidence[arm_id] = {"q": 0.0, "pulls": 0, "known": False}
            return evidence, True, (time.perf_counter() - started) * 1000.0
        for arm_id in arm_ids:
            worm = "%s>%s" % (capability, arm_id)
            try:
                q = float(spine.get_q_weight(ARM_ROUTE_NATURE, worm))
                pulls = int(spine.get_q_updates(ARM_ROUTE_NATURE, worm))
                evidence[arm_id] = {"q": q, "pulls": pulls, "known": pulls > 0}
            except Exception as exc:
                note_failure("router:L300", exc)
                evidence[arm_id] = {"q": 0.0, "pulls": 0, "known": False}
        return evidence, False, (time.perf_counter() - started) * 1000.0

    def select_arm(self, capability: str, candidates: Optional[List[str]] = None,
                   policy: Optional[RoutingPolicy] = None) -> dict:
        """Choose an Arm for a capability and explain the choice.

        Deterministic given the same evidence EXCEPT when exploration fires,
        in which case ``reason`` says so and the log records it -- an
        unexplainable route is indistinguishable from a bug.

        Returns a decision dict (never raises) with keys: ``arm_id``,
        ``reason``, ``candidates``, ``evidence``, ``read_ms``, ``degraded``.
        """
        pol = policy or self.policy
        if candidates is None:
            candidates = self.capability_index().get(str(capability), [])
        candidates = [c for c in candidates if c]
        decision = {
            "capability": capability,
            "candidates": list(candidates),
            "arm_id": None,
            "reason": None,
            "evidence": {},
            "read_ms": 0.0,
            "degraded": False,
        }
        if not candidates:
            decision["reason"] = "no_candidate"
            return decision

        evidence, degraded, read_ms = self._read_evidence(capability, candidates)
        decision["evidence"] = evidence
        decision["degraded"] = degraded
        decision["read_ms"] = round(read_ms, 3)

        # Static order for ties and for the degraded fallback: higher
        # registry priority first, then name, so the choice is stable and
        # reproducible rather than dependent on dict iteration order.
        static_order = sorted(candidates, key=lambda a: (-self._arm_priority(a), a))

        if degraded:
            decision["arm_id"] = static_order[0]
            decision["reason"] = "degraded_static_priority"
            return decision

        unproven = [a for a in candidates if evidence[a]["pulls"] < pol.min_pulls]
        if unproven:
            # Exercise the least-observed Arm first. This is the cold-start
            # guard: coverage is guaranteed rather than left to chance.
            unproven.sort(key=lambda a: (evidence[a]["pulls"],
                                         -self._arm_priority(a), a))
            decision["arm_id"] = unproven[0]
            decision["reason"] = "cold_start"
            return decision

        proven = sorted(candidates, key=lambda a: (-evidence[a]["q"], a))
        if len(proven) > 1 and pol.epsilon > 0 and random.random() < pol.epsilon:
            decision["arm_id"] = random.choice(proven)
            decision["reason"] = "epsilon"
            return decision
        decision["arm_id"] = proven[0]
        decision["reason"] = "exploit"
        return decision

    # ── outcome write-back ─────────────────────────────────────────────

    @staticmethod
    def _is_infra_error(exc: BaseException) -> bool:
        """True when a failure says nothing about the Arm's own reliability.

        An OSError-family fault (missing binary, refused connection, timed-out
        upstream) is INFRASTRUCTURE: it must be counted but never learned, or
        an Arm is demoted for a fault that was never its own. Everything else
        -- ValueError, TypeError, RuntimeError, ... -- is execution evidence
        about the Arm and is learned as before. Mirrors the mesh router's
        env_error censoring so both surfaces agree on what "not the Arm's
        fault" means.
        """
        return isinstance(exc, OSError)

    def _write_back(self, capability: str, arm_id: str, success: bool,
                    duration_ms: float, censored: bool = False) -> bool:
        """Feed one outcome into the ledger. Returns True if it was stored.

        Called only AFTER execution. Suppressed when this Arm has already
        contributed ``max_writes_per_arm_per_hour`` times in the last hour,
        so a hot capability cannot dominate the table that selects it.

        ``censored`` marks infrastructure noise (see ``_is_infra_error``):
        the outcome is recorded and counted but the spine must not learn from
        it, so an Arm is never demoted for a fault that was not its own.
        """
        if not self.policy.write_back:
            return False
        arm_key = "%s>%s" % (capability, arm_id)
        now = time.time()
        cutoff = now - 3600.0
        with self._write_lock:
            window = self._write_window.setdefault(arm_key, deque())
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= self.policy.max_writes_per_arm_per_hour:
                with self._stats_lock:
                    self._stats["write_backs_suppressed"] += 1
                return False
            window.append(now)
        spine = _spine()
        if spine is None:
            with self._stats_lock:
                self._stats["write_backs_suppressed"] += 1
            return False
        try:
            spine.record_outcome(ARM_ROUTE_NATURE, arm_key,
                                 bool(success), float(duration_ms),
                                 censored=bool(censored))
        except Exception as exc:
            note_failure("router:L417", exc)
            with self._stats_lock:
                self._stats["write_backs_suppressed"] += 1
            return False
        with self._stats_lock:
            self._stats["write_backs"] += 1
        return True

    def write_backs_last_hour(self, capability: str, arm_id: str) -> int:
        """Writes this Arm has contributed in the last hour (observability)."""
        arm_key = "%s>%s" % (capability, arm_id)
        cutoff = time.time() - 3600.0
        with self._write_lock:
            window = self._write_window.get(arm_key)
            if not window:
                return 0
            return sum(1 for t in window if t >= cutoff)

    # ── routing ────────────────────────────────────────────────────────

    def route_task(self, arm_id: Optional[str] = None, capability: Optional[str] = None,
                   payload: Optional[dict] = None, policy: Optional[RoutingPolicy] = None) -> dict:
        """Route a capability to an Arm.

        ``arm_id`` may be None (or omitted) to route elastically by
        capability: ``route_task(None, "get_status")``. Passing an explicit
        ``arm_id`` behaves exactly as it always has -- the Arm is used
        as-is, no selection, no learning applied to the choice.

        The returned dict keeps the original shape (``status``, ``data``,
        ``error``, ``arm_id``) and adds provenance keys, so existing
        consumers need no change.
        """
        pol = policy or self.policy
        payload = payload or {}
        if not capability:
            return {"status": "error", "error": "capability is required",
                    "arm_id": arm_id or ""}

        selected = arm_id
        decision = None
        if selected:
            # Explicitly pinned: no selection happens, so no exploration
            # policy is consulted and epsilon cannot divert the call.
            with self._stats_lock:
                self._stats["pinned"] += 1
        else:
            decision = self.select_arm(capability, policy=pol)
            selected = decision.get("arm_id")
            if not selected:
                with self._stats_lock:
                    self._stats["no_candidate"] += 1
                return {
                    "status": "error",
                    "error": "no Arm declares capability '%s'" % capability,
                    "arm_id": "", "capability": capability,
                    "reason": decision.get("reason", "no_candidate"),
                    "candidates": decision.get("candidates", []),
                }
            with self._stats_lock:
                self._stats["elastic"] += 1
                reason_key = decision.get("reason") or ""
                if reason_key in self._stats:
                    self._stats[reason_key] += 1
                if decision.get("degraded"):
                    self._stats["degraded_reads"] += 1

        with self._stats_lock:
            self._stats["routed"] += 1

        arm = self.registry.get_arm(selected)
        if arm is None:
            with self._stats_lock:
                self._stats["arm_missing"] += 1
            return {"status": "error", "error": "Arm '%s' not found" % selected}

        started_at = now_iso()
        started_perf = time.perf_counter()
        self.bus.publish(AOEvent(
            event_type="%s.execute.started" % selected,
            source_arm="aoa.router", target_arm=selected,
            payload={"capability": capability, "payload": payload,
                     "selected_by": (decision or {}).get("reason", "pinned")}))

        result: Optional[ArmResult] = None
        # Delegation spec (4-tuple contract): the routing decision crosses
        # the executor boundary as instruction/context/tools/model instead
        # of dissolving into a bare payload. Wire-compatible: arms that
        # ignore the envelope behave exactly as before.
        _spec = None
        _exec_payload = payload
        try:
            from .delegation_spec import from_route
            _spec = from_route(capability, payload, decision, selected)
            _exec_payload = _spec.to_payload(payload)
        except Exception as exc:
            note_failure("router:L512", exc)
            _spec = None
            _exec_payload = payload
        try:
            if not arm.is_initialized:
                arm.initialize()
            raw = arm.execute(capability, _exec_payload)
            duration_ms = (time.perf_counter() - started_perf) * 1000.0
            result = ArmResult.from_arm_output(
                raw, selected, capability,
                stale_after_s=pol.default_stale_after_s)
            result.metadata.setdefault("duration_ms", round(duration_ms, 3))
            if decision:
                result.metadata["selected_by"] = decision.get("reason")
            violations = validate_result(result, report=get_contract_report())
            if violations:
                result.metadata["contract_violations"] = violations
            flat = result.to_dict()
            flat["routed_by"] = "aoa.router"
            flat["arm_id"] = selected
            # Outcome: was this delegation the right choice? Recorded here so a
            # tracer can answer "was this arm the right choice for this capability"
            # from the ledger instead of from memory. Computed after success is
            # determined (below) and attached to flat before return.
            if _spec is not None:
                try:
                    _spec.attach(flat)
                except Exception as exc:
                    note_failure("router:L535", exc)
            if decision:
                flat["routing"] = {
                    "reason": decision.get("reason"),
                    "candidates": decision.get("candidates", []),
                    "read_ms": decision.get("read_ms"),
                    "degraded": decision.get("degraded"),
                    "evidence": decision.get("evidence", {}),
                }
            self.bus.publish(AOEvent(
                event_type="%s.execute.complete" % selected, source_arm=selected,
                payload={"capability": capability,
                         "status": flat.get("status"),
                         "is_stale": flat.get("is_stale")}))
            success = flat.get("status") == "ok"
            # Attach outcome now that success is known.
            _outcome = "accepted" if success else "rejected"
            if success and flat.get("is_stale") is True:
                _outcome = "unverified"
            flat["outcome"] = {
                "verdict": _outcome,
                "judged_by": "aoa.router",
                "success": bool(success),
                "duration_ms": round(duration_ms, 3),
                "contract_violations": list(violations),
            }
            # Record the outcome for PINNED calls too. The Q table models
            # (capability, Arm) reliability, and a pinned execution is a real
            # observation of that pair -- discarding it is the same silent
            # telemetry loss that left `record_route_result()` with zero
            # callers for this table's entire history. It cannot create a
            # selection loop: a pinned call never reads Q to choose, and the
            # write still happens only after execution completes.
            stored = self._write_back(capability, selected, success, duration_ms)
            self._record_decision(capability, selected, decision, success,
                                  duration_ms, stored, violations)
            # Durable outcome: the verdict above otherwise lives only in a bounded
            # deque and dies with the process, so "was this arm the right choice"
            # is unanswerable from a ledger. Written after the answer exists and
            # never allowed to fail the route.
            _trace_ref = None
            try:
                from .delegation_tracer import emit_delegation
                _trace_ref = emit_delegation(
                    flat, capability=capability, arm_id=selected,
                    spec=_spec, duration_ms=duration_ms)
            except Exception as exc:
                note_failure("router:L-outcome-trace", exc)
            if _trace_ref:
                flat["trace"] = dict(_trace_ref)
            return flat
        except Exception as e:
            note_failure("router:L562", e)
            duration_ms = (time.perf_counter() - started_perf) * 1000.0
            self.bus.publish(AOEvent(
                event_type=AOEventType.ARM_ERROR, source_arm=selected,
                payload={"capability": capability, "error": str(e)},
                priority=EventPriority.HIGH))
            # A failed execution is still evidence: record it, so a
            # consistently failing Arm is demoted by the same mechanism that
            # promotes a working one.
            censored = self._is_infra_error(e)
            stored = self._write_back(capability, selected, False, duration_ms,
                                      censored=censored)
            self._record_decision(capability, selected, decision, False,
                                  duration_ms, stored,
                                  ["exception"] if not censored
                                  else ["exception", "censored:infra"])
            _failed = {"status": "error", "error": str(e), "arm_id": selected,
                       "capability": capability, "produced_at": now_iso(),
                       "outcome": {"verdict": "rejected", "judged_by": "aoa.router",
                                   "success": False, "duration_ms": round(duration_ms, 3),
                                   "contract_violations": ["exception"] if not censored
                                   else ["exception", "censored:infra"]}}
            # A failed execution is evidence too: record it, or the trace holds
            # only the calls that worked and an arm that always throws looks
            # untried.
            try:
                from .delegation_tracer import emit_delegation
                _trace_ref = emit_delegation(
                    _failed, capability=capability, arm_id=selected,
                    spec=_spec, duration_ms=duration_ms)
                if _trace_ref:
                    _failed["trace"] = dict(_trace_ref)
            except Exception as exc:
                note_failure("router:L-error-trace", exc)
            return _failed

    def _record_decision(self, capability: str, arm_id: str,
                         decision: Optional[dict], success: bool,
                         duration_ms: float, stored: bool,
                         violations: List[str]) -> None:
        """Append a replayable routing record. Bounded by the deque."""
        entry = {
            "at": now_iso(),
            "capability": capability,
            "arm_id": arm_id,
            "selected_by": (decision or {}).get("reason", "pinned"),
            "candidates": (decision or {}).get("candidates", []),
            "read_ms": (decision or {}).get("read_ms"),
            "degraded": (decision or {}).get("degraded"),
            "success": bool(success),
            "outcome": "accepted" if success else "rejected",
            "duration_ms": round(duration_ms, 3),
            "outcome_recorded": bool(stored),
            "contract_violations": list(violations),
        }
        with self._log_lock:
            self._routing_log.append(entry)

    def get_routing_log(self, limit: int = 50) -> List[dict]:
        """Most recent routing decisions, newest first."""
        with self._log_lock:
            items = list(self._routing_log)
        items.reverse()
        return items[:limit]

    def get_selection_stats(self) -> dict:
        """Counters for monitors: how routing is actually behaving."""
        with self._stats_lock:
            stats = dict(self._stats)
        stats["routing_log_size"] = len(self._routing_log)
        stats["contract"] = get_contract_report().snapshot()  # pyright: ignore[reportArgumentType]
        stats["candidate_capabilities"] = len(self.capability_index())
        return stats

    def reset_telemetry(self) -> None:
        """Clear counters and the replay log (tests, and monitor resets)."""
        with self._stats_lock:
            for k in self._stats:
                self._stats[k] = 0
        with self._log_lock:
            self._routing_log.clear()
        with self._write_lock:
            self._write_window.clear()
        get_contract_report().reset()

    # ── workflows ──────────────────────────────────────────────────────

    def create_workflow(self, name: str, description: str = "") -> WorkflowDAG:
        workflow = WorkflowDAG(name=name, description=description)
        self._workflows[workflow.workflow_id] = workflow
        return workflow

    def execute_workflow(self, workflow_id: str) -> List[dict]:
        workflow = self._workflows.get(workflow_id)
        if workflow is None:
            return [{"error": "Workflow '%s' not found" % workflow_id}]
        results = []
        self.bus.publish(AOEvent(
            event_type=AOEventType.WORKFLOW_STARTED, source_arm="aoa.router",
            payload={"workflow_id": workflow_id, "name": workflow.name},
            correlation_id=workflow_id))
        while not workflow.is_complete():
            ready = workflow.get_ready_steps()
            if not ready:
                break
            for step in ready:
                step.status = StepStatus.RUNNING
                step.started_at = datetime.now().isoformat()
                # An empty arm_id is how a DAG step asks for elastic
                # routing: the step names a capability, the router picks.
                result = self.route_task(step.arm_id or None, step.capability,
                                         step.payload)
                step.result = result
                step.completed_at = datetime.now().isoformat()
                if result.get("status") == "ok":
                    step.status = StepStatus.COMPLETED
                else:
                    step.status = StepStatus.FAILED
                    step.error = result.get("error", "Unknown error")
                # Timestamps are already recorded on the step; returning them
                # costs nothing and is the only way a caller can report a real
                # duration. Without them `SessionManager` had to log 0.0 ms for
                # every step, which reads as "instant" rather than "unmeasured".
                results.append({
                    "step_id": step.step_id, "arm_id": step.arm_id,
                    "resolved_arm_id": result.get("arm_id", step.arm_id),
                    "capability": step.capability, "status": step.status.value,
                    "started_at": step.started_at,
                    "completed_at": step.completed_at,
                    "result": result})
                self.bus.publish(AOEvent(
                    event_type=AOEventType.WORKFLOW_STEP_COMPLETE,
                    source_arm="aoa.router",
                    payload={"step_id": step.step_id, "status": step.status.value},
                    correlation_id=workflow_id))
        self.bus.publish(AOEvent(
            event_type=AOEventType.WORKFLOW_COMPLETE, source_arm="aoa.router",
            payload=workflow.get_status_summary(), correlation_id=workflow_id))
        return results

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowDAG]:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[dict]:
        return [w.get_status_summary() for w in self._workflows.values()]

    def get_cross_arm_routes(self) -> List[dict]:
        routes = []
        arms = self.registry.list_arms()
        for source in arms:
            for target in arms:
                if source["arm_id"] != target["arm_id"]:
                    routes.append({
                        "source": source["arm_id"], "target": target["arm_id"],
                        "source_category": source["category"],
                        "target_category": target["category"]})
        return routes
