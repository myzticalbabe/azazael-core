"""
Eve Unleashed — Consciousness Simulation (Phase 4, V10.0).

Self-model: system maintains a model of its own architecture (components,
versions, capabilities).
Introspection loop: periodic self-analysis via InferenceBridge + DistillZ.
Goal-setting: system proposes its own improvement goals from PAMS history.
Reflection: after each morph cycle, evaluates fitness improvement (before/after).

Fail-open: degrades to static self-model if bridge/distill/PAMS unavailable.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass


@dataclass
class ComponentModel:
    """One entry in the system self-model."""
    name: str
    kind: str              # worm | service | bridge | utility | memory
    version: str = "1.0.0"
    status: str = "unknown"
    capabilities: List[str] = field(default_factory=list)
    health: float = 1.0    # 0..1
    last_seen: float = field(default_factory=time.time)


@dataclass
class SelfModel:
    """The system's model of its own architecture."""

    version: str = "1.0.0"
    components: Dict[str, ComponentModel] = field(default_factory=dict)
    total_worms: int = 0
    autonomy_level: str = "recommend_only"
    confidence_threshold: float = 0.85
    last_updated: float = field(default_factory=time.time)
    introspection_count: int = 0

    def register(self, name: str, kind: str, capabilities: Optional[List[str]] = None,
                 version: str = "1.0.0") -> None:
        self.components[name] = ComponentModel(
            name=name, kind=kind, version=version,
            capabilities=capabilities or [],
            last_seen=time.time(),
        )
        self.last_updated = time.time()

    def update_health(self, name: str, health: float, status: str = "unknown") -> None:
        c = self.components.get(name)
        if c is None:
            return
        c.health = max(0.0, min(1.0, float(health)))
        c.status = status
        c.last_seen = time.time()
        self.last_updated = time.time()

    def get_unhealthy(self, threshold: float = 0.7) -> List[str]:
        """Return component names with health below threshold."""
        return [n for n, c in self.components.items() if c.health < threshold]

    def propose_mesh_action(self, action: str, nature: str = "monitor") -> Optional[Dict[str, Any]]:
        """Propose a mesh-driving action based on self-model state."""
        unhealthy = self.get_unhealthy()
        if unhealthy:
            return {"action": "orchestrate", "nature": nature, "reason": f"unhealthy components: {unhealthy}", "confidence": 0.85}
        if self.total_worms == 0:
            return {"action": "discover_worms", "nature": nature, "reason": "no worms discovered", "confidence": 0.9}
        return {"action": action, "nature": nature, "reason": "routine", "confidence": 0.7}

    def snapshot(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "total_worms": self.total_worms,
            "autonomy_level": self.autonomy_level,
            "confidence_threshold": self.confidence_threshold,
            "introspection_count": self.introspection_count,
            "last_updated": self.last_updated,
            "components": {k: asdict(v) for k, v in self.components.items()},
        }


class IntrospectionLoop:
    """Periodic self-analysis: InferenceBridge analyzes, DistillZ distills,
    results written back to PAMS as insight."""

    def __init__(self, self_model: SelfModel, bridge=None, distill=None,
                 recorder=None, interval_s: float = 300.0, act_fn=None):
        self.self_model = self_model
        self._bridge = bridge
        self._distill = distill
        self._recorder = recorder
        self.interval_s = interval_s
        self._running = False
        self._thread = None
        self.insights: List[Dict[str, Any]] = []
        self._lock = __import__("threading").Lock()
        self._act_fn = act_fn
        self._stop_event = __import__("threading").Event()

    # -- subsystem access (fail-open) -------------------------------------

    def _get_bridge(self):
        if self._bridge is not None:
            return self._bridge
        try:
            from META.InferenceBridge import InferenceBridge
            self._bridge = InferenceBridge()
            return self._bridge
        except Exception as exc:
            note_failure("consciousness:bridge", exc)
            return None

    def _get_distill(self):
        if self._distill is not None:
            return self._distill
        try:
            from META.DistillZ import DistillZ
            self._distill = DistillZ()
            return self._distill
        except Exception as exc:
            note_failure("consciousness:distill", exc)
            return None

    def _get_recorder(self):
        if self._recorder is not None:
            return self._recorder
        try:
            from CORE.PamsRecorder import get_recorder
            self._recorder = get_recorder()
            return self._recorder
        except Exception as exc:
            note_failure("consciousness:recorder", exc)
            return None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = __import__("threading").Thread(target=self._run_loop,
                                                      daemon=True,
                                                      name="eve-introspection")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=15.0)

    def _run_loop(self) -> None:
        while self._running:
            try:
                self.run_once()
            except Exception as exc:  # never kill the loop
                note_failure("consciousness:loop", exc)
            self._stop_event.wait(timeout=self.interval_s)

    def run_once(self) -> Dict[str, Any]:
        """One introspection cycle: analyze self-model, distill, record."""
        bridge = self._get_bridge()
        distill = self._get_distill()
        model_json = str(self.self_model.snapshot())

        insight = {"timestamp": time.time(), "cycle": self.self_model.introspection_count + 1,
                   "model_summary": model_json[:2000]}

        if bridge is not None:
            try:
                status = bridge.get_bridge_status()
                insight["llama_available"] = status.get("llama_available", False)
                prompt = ("Analyze this system self-model for health risks, "
                          "capability gaps, and improvement ideas. Be concise.\n" + model_json)
                analysis = bridge.send_to_llama(prompt)
                insight["bridge_analysis"] = analysis[:2000]
            except Exception as exc:
                note_failure("consciousness:bridge_analyze", exc)
                insight["bridge_analysis"] = f"unavailable ({exc})"
        else:
            insight["bridge_analysis"] = "bridge unavailable"

        if distill is not None:
            try:
                distilled = distill.distill()
                insight["distilled"] = distilled
            except Exception as exc:
                note_failure("consciousness:distill", exc)

        with self._lock:
            self.insights.append(insight)
            self.self_model.introspection_count += 1

        # Act on architecture: if unhealthy components found, propose mesh orchestration
        unhealthy = self.self_model.get_unhealthy() if hasattr(self.self_model, 'get_unhealthy') else []
        if unhealthy and self._recorder is not None:
            try:
                self._recorder.record_event(
                    event_type="consciousness_action",
                    data={"action": "orchestrate", "reason": f"unhealthy: {unhealthy}", "cycle": self.self_model.introspection_count},
                    source="consciousness", severity="warning",
                )
            except Exception as exc:
                note_failure("consciousness:action_record", exc)

        # Execute proposed mesh action via act_fn if available
        if self._act_fn is not None:
            try:
                proposed = self.self_model.propose_mesh_action("run_metacluster", "monitor")
                if proposed is not None:
                    self._act_fn(proposed)
            except Exception as exc:
                note_failure("consciousness:act_fn", exc)

        self._record(insight)
        return insight

    def _record(self, payload: Dict[str, Any]) -> None:
        rec = self._get_recorder()
        if rec is None:
            return
        try:
            rec.record_event(event_type="introspection", data=payload,
                             source="introspection_loop", severity="info")
        except Exception as exc:
            note_failure("consciousness:record", exc)

    def recent_insights(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.insights[-limit:])


@dataclass
class ImprovementGoal:
    """A self-proposed improvement goal."""

    id: str
    title: str
    rationale: str
    source: str              # "pams_history" | "introspection" | "reflection"
    priority: float          # 0..1
    status: str = "proposed"  # proposed | accepted | done | rejected
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


class GoalSetting:
    """Proposes improvement goals from PAMS history / introspection."""

    def __init__(self, recorder=None):
        self._recorder = recorder
        self.goals: List[ImprovementGoal] = []
        self._lock = __import__("threading").Lock()

    def _get_recorder(self):
        if self._recorder is not None:
            return self._recorder
        try:
            from CORE.PamsRecorder import get_recorder
            self._recorder = get_recorder()
            return self._recorder
        except Exception as exc:
            note_failure("goal_setting:recorder", exc)
            return None

    def propose_from_introspection(self, insight: Dict[str, Any]) -> List[ImprovementGoal]:
        goals: List[ImprovementGoal] = []
        analysis = str(insight.get("bridge_analysis", ""))
        distilled = str(insight.get("distilled", ""))
        text = (analysis + " " + distilled).lower()
        triggers = {
            "latency": "Reduce inference latency",
            "error": "Reduce autonomous error rate",
            "health": "Improve component health monitoring",
            "coverage": "Expand worm fleet coverage",
        }
        for key, title in triggers.items():
            if key in text:
                g = ImprovementGoal(
                    id=str(uuid.uuid4()), title=title,
                    rationale=f"Introspection flagged {key}",
                    source="introspection", priority=0.6,
                )
                goals.append(g)
        with self._lock:
            self.goals.extend(goals)
        return goals

    def propose_from_pams(self, memories: List[Dict[str, Any]]) -> List[ImprovementGoal]:
        """Derive goals from recurring PAMS memory themes (fail-open if empty)."""
        goals: List[ImprovementGoal] = []
        if not memories:
            return goals
        # naive theme count: repeated error mentions -> goal
        error_count = sum(1 for m in memories if "error" in str(m).lower())
        if error_count >= 3:
            g = ImprovementGoal(
                id=str(uuid.uuid4()), title="Reduce recurring PAMS-recorded errors",
                rationale=f"{error_count} recent memories mention errors",
                source="pams_history", priority=0.7,
            )
            goals.append(g)
        with self._lock:
            self.goals.extend(goals)
        return goals

    def accept(self, goal_id: str) -> bool:
        with self._lock:
            for g in self.goals:
                if g.id == goal_id and g.status == "proposed":
                    g.status = "accepted"
                    return True
        return False

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [asdict(g) for g in self.goals]


class Reflection:
    """Evaluates fitness improvement after each morph cycle (before/after)."""

    def __init__(self, recorder=None):
        self._recorder = recorder
        self.cycles: List[Dict[str, Any]] = []
        self._lock = __import__("threading").Lock()

    def _get_recorder(self):
        if self._recorder is not None:
            return self._recorder
        try:
            from CORE.PamsRecorder import get_recorder
            self._recorder = get_recorder()
            return self._recorder
        except Exception as exc:
            note_failure("reflection:recorder", exc)
            return None

    def reflect(self, before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        """Compare before/after GA snapshots; report fitness delta."""
        bf = before.get("best_fitness")
        af = after.get("best_fitness")
        improved = bool(af and af != bf)
        gen_delta = (after.get("generation", 0) - before.get("generation", 0))
        report = {
            "timestamp": time.time(),
            "generation_delta": gen_delta,
            "best_fitness_before": bf,
            "best_fitness_after": af,
            "improved": improved,
            "note": "fitness improved" if improved else "no measurable improvement",
        }
        with self._lock:
            self.cycles.append(report)
        self._record(report)
        return report

    def _record(self, payload: Dict[str, Any]) -> None:
        rec = self._get_recorder()
        if rec is None:
            return
        try:
            rec.record_event(event_type="reflection", data=payload,
                             source="reflection", severity="info")
        except Exception as exc:
            note_failure("reflection:record", exc)

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.cycles[-50:])


__all__ = ["SelfModel", "ComponentModel", "IntrospectionLoop",
           "ImprovementGoal", "GoalSetting", "Reflection"]
