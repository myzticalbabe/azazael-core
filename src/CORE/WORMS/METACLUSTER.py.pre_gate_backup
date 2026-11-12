# coding: utf-8
"""
METACLUSTER — MetaCluster Orchestrator (Cluster-of-Clusters)

Meta-level orchestrator that manages multiple NatureClusters as a unified
fleet. A MetaCluster is "meta" in Nature — it doesn't run individual worms;
it coordinates clusters of worms across multiple Natures.

Architecture:
    MetaCluster (top-level orchestrator)
    ├── NatureCluster("security")     → 5 security worms
    ├── NatureCluster("profiling")    → 5 profiling worms
    ├── NatureCluster("monitor")      → 8 monitor worms
    ├── NatureCluster("defense")      → 5 defense worms
    ├── NatureCluster("telemetry")    → 5 telemetry worms
    └── ... (all 17 Natures)

    MetaClusterState (shared state across all clusters)
    ├── Results aggregation
    ├── Cross-Nature routing
    ├── Insight generation
    └── PAMS recording of every action

Features:
- Multi-Nature orchestration: execute multiple Natures in priority order
- MetaClusterState: shared state, routing, insights across clusters
- Adaptive scheduling: system-state-driven Nature prioritization
- Pre/Post Nature hooks: inject behavior before/after each Nature executes
- PAMS memory recording: every spawn, execute, route, insight stored
- Mermaid graph generation: full cluster-of-clusters visualization
- Thread-safe: all state modifications use RLock
- Free-threading aware: auto-detects Python 3.14t for true parallelism

Usage:
    >>> from CORE.WORMS.METACLUSTER import MetaCluster
    >>> mc = MetaCluster()
    >>> mc.discover_all()              # Auto-discover all Nature clusters
    >>> results = mc.orchestrate()     # Run priority Natures end-to-end
    >>> insights = mc.generate_insights(results)
    >>> print(mc.to_mermaid())         # Visualize the meta-cluster
"""

import os
import sys
import json
import time
import hashlib
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Optional, Any, Callable
from dataclasses import dataclass, field

# ── Import NatureClusters ──────────────────────────────────────────
try:
    from CORE.WORMS.NATURE_CLUSTERS import (
        NatureClusterManager, NatureResult, NATURE_DEFINITIONS,
    )
    _NATURE_OK = True
except ImportError:
    _NATURE_OK = False
    NatureClusterManager = None
    NatureResult = None
    NATURE_DEFINITIONS: Dict[str, Any] = {}

# ── Import CrossNatureRoutingEngine from extracted module ──────────
try:
    from CORE.WORMS.CROSS_NATURE_ROUTER import CrossNatureRoutingEngine, CrossNatureRoute
    _ROUTER_OK = True
except ImportError:
    _ROUTER_OK = False
    CrossNatureRoutingEngine = None
    from dataclasses import dataclass as _dc
    @_dc
    class _CrossNatureRoute:
        from_nature: str = ""
        to_nature: str = ""
        data_mapping: dict = None
        description: str = ""
        def __post_init__(self):
            if self.data_mapping is None:
                self.data_mapping = {}
    CrossNatureRoute = _CrossNatureRoute

# ── Worm Registry ──────────────────────────────────────────────────
try:
    from CORE.worm_registry import (
        register_worm, WORM_REGISTRY, discover_worms,
        get_all_categories, mark_spawned,
    )
    _REGISTRY_OK = True
except ImportError:
    _REGISTRY_OK = False
    WORM_REGISTRY = {}
    def discover_worms(worms_dir: Optional[str] = None, progress_callback: Optional[Callable] = None) -> int: return 0
    def get_all_categories() -> Set[str]: return set()
    def mark_spawned(name: str) -> Any: pass
    def register_worm(category: str = "", priority: int = 0, cooldown_seconds: float = 0, description: str = "", tags: Optional[set[str]] = None, **kwargs: Any) -> Callable[..., Any]: return lambda f: f
# ── PAMS Memory Recording (lazy, thread-safe) ─────────────────
_pams: Any = None
_pams_lock = threading.Lock()
def _get_pams():
    """Lazy PAMS recorder — double-checked locking for Python 3.14t free-threading."""
    global _pams
    if _pams is None:
        with _pams_lock:
            if _pams is None:
                try:
                    from CORE.PamsRecorder import PamsRecorder
                    _pams = PamsRecorder()
                except ImportError:
                    _pams = False
    return _pams if _pams else None


# ── MemoryBus (lazy, thread-safe) ────────────────────────────────────
_mem_bus: Any = None
_mem_bus_lock = threading.Lock()
def _get_mem_bus():
    """Lazy MemoryBus — double-checked locking for Python 3.14t free-threading."""
    global _mem_bus
    if _mem_bus is None:
        with _mem_bus_lock:
            if _mem_bus is None:
                try:
                    from CORE.MemoryBus import MemoryBus
                    _mem_bus = MemoryBus()
                except ImportError:
                    _mem_bus = False
    return _mem_bus if _mem_bus else None

# ── Free-Threading ─────────────────────────────────────────────────
try:
    from CORE.freethreading import is_free_threaded
    _FREETHREAD_OK = True
except ImportError:
    _FREETHREAD_OK = False
    def is_free_threaded() -> bool:
        try:
            return not sys._is_gil_enabled()
        except Exception:
            return False

# ── Config (lazy, thread-safe) ─────────────────────────────────
_cfg: Any = None
_cfg_lock = threading.Lock()
def _get_config():
    """Lazy config — double-checked locking for Python 3.14t free-threading."""
    global _cfg
    if _cfg is None:
        with _cfg_lock:
            if _cfg is None:
                try:
                    from CORE.config import get_config
                    _cfg = get_config()
                except ImportError:
                    _cfg = False
    return _cfg if _cfg else None


# ── Circuit Breaker (lazy, thread-safe) ────────────────────────────
_breaker_registry: Any = None
_breaker_lock = threading.Lock()
def _get_breakers():
    """Lazy BreakerRegistry — double-checked locking for Python 3.14t free-threading."""
    global _breaker_registry
    if _breaker_registry is None:
        with _breaker_lock:
            if _breaker_registry is None:
                try:
                    from CORE.circuit_breaker import BreakerRegistry
                    _breaker_registry = BreakerRegistry()
                except ImportError:
                    _breaker_registry = False
    return _breaker_registry if _breaker_registry else None


# ── Structured Logger (lazy, thread-safe) ────────────────────────
_logger: Any = None
_logger_lock = threading.Lock()
def _get_log():
    """Lazy structured logger — double-checked locking for Python 3.14t free-threading."""
    global _logger
    if _logger is None:
        with _logger_lock:
            if _logger is None:
                try:
                    from CORE.azazael_logging import get_logger
                    _logger = get_logger("MetaCluster")
                except ImportError:
                    _logger = False
    return _logger if _logger else None


# ═══════════════════════════════════════════════════════════════════════
# DATA TYPES
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class MetaClusterResult:
    """Aggregate result from a MetaCluster orchestration run."""
    orchestration_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_ms: float = 0.0
    natures_executed: int = 0
    total_worms_ok: int = 0
    total_worms_fail: int = 0
    nature_results: Dict[str, Any] = field(default_factory=dict)
    routed_data: Dict[str, Any] = field(default_factory=dict)
    insights: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    cross_nature_paths_used: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class MetaClusterState:
    """Shared state across all clusters in a MetaCluster run."""
    run_id: str = ""
    started_at: str = ""
    phase: str = "idle"  # idle, discovering, executing, routing, analyzing, complete
    active_natures: Set[str] = field(default_factory=set)
    completed_natures: Set[str] = field(default_factory=set)
    failed_natures: Set[str] = field(default_factory=set)
    worm_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # {nature: {worm: result}}
    routed_messages: List[Dict] = field(default_factory=list)
    events: List[Dict] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)


# NOTE: CrossNatureRoutingEngine is now in CORE.WORMS.CROSS_NATURE_ROUTER


# ═══════════════════════════════════════════════════════════════════════
# META CLUSTER — The Top-Level Orchestrator
# ═══════════════════════════════════════════════════════════════════════

@register_worm(category="orchestration", priority=10, cooldown_seconds=5.0,
    description="MetaCluster — cluster-of-clusters orchestrator coordinating all Natures with cross-Nature routing and PAMS recording")
class MetaCluster:
    """
    MetaCluster — the top-level orchestrator that manages clusters of clusters.
    
    "Meta" in Nature: doesn't execute individual worms, but coordinates
    multiple NatureClusters, routes data between them, generates insights,
    and records every action to PAMS memory.
    
    Usage:
        mc = MetaCluster()
        mc.discover_all()
        
        # Run a single Nature end-to-end
        result = mc.orchestrate_nature("security")
        
        # Run multiple Natures with cross-routing
        result = mc.orchestrate(natures=["security", "defense", "monitor"])
        
        # Run with auto-prioritization
        result = mc.orchestrate_adaptive(max_natures=5)
        
        # Visualize
        print(mc.to_mermaid())
        print(mc.to_mermaid_execution_flow())
    """
    
    VERSION = "1.0.0"
    
    def __init__(self):
        self.ncm: Optional[NatureClusterManager] = (
            NatureClusterManager() if NatureClusterManager else None        )
        self.router = CrossNatureRoutingEngine()
        self.state: Optional[MetaClusterState] = None
        self._history: List[MetaClusterResult] = []
        self._lock = threading.RLock()
        self._hooks: Dict[str, List[Callable]] = {
            "pre_nature": [],
            "post_nature": [],
            "on_insight": [],
            "on_error": [],
        }
    
    # ── Discovery ──────────────────────────────────────────────────
    
    def discover_all(self) -> Dict[str, int]:
        """Discover all Nature clusters from the worm registry."""
        if not self.ncm:
            return {}
        
        nature_counts = self.ncm.discover_all_natures()
        _record_event("metacluster_discovery", {
            "natures": len(nature_counts),
            "total_worms": sum(nature_counts.values()),
            "per_nature": nature_counts,
        })
        return nature_counts
    
    # ── Hooks ──────────────────────────────────────────────────────
    
    def on(self, hook_name: str, callback: Callable):
        """Register a hook callback. Supports: pre_nature, post_nature, on_insight, on_error."""
        if hook_name in self._hooks:
            self._hooks[hook_name].append(callback)

    def _run_hooks(self, hook_name: str, **kwargs):
        """Run all registered hooks for a given hook point."""
        for cb in self._hooks.get(hook_name, []):
            try:
                cb(**kwargs)
            except Exception:
                pass
    
    # ── Orientation ────────────────────────────────────────────────
    
    def orchestrate(self, natures: Optional[List[str]] = None,
                    parallel: bool = False, max_natures: int = 0) -> MetaClusterResult:
        """
        Orchestrate a set of Natures end-to-end with cross-Nature routing.
        
        Args:
            natures: Specific Natures to execute (None = auto-discover all)
            parallel: Execute Natures in parallel where possible
            max_natures: Limit to top-N Natures by priority
        
        Returns:
            MetaClusterResult with full execution details and insights.
        """
        if not self.ncm:
            return MetaClusterResult(errors=["NatureClusterManager not available"])
        
        # Initialize state
        orchestration_id = f"meta_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:6]}"
        self.state = MetaClusterState(
            run_id=orchestration_id,
            started_at=datetime.now().isoformat(),
            phase="discovering",
        )
        
        start = time.perf_counter()
        
        # Phase 1: Discover
        if not self.ncm.clusters:
            self.discover_all()
        
        # Determine Natures to execute
        if natures is None:
            natures = list(self.ncm.clusters.keys())
        
        if max_natures > 0:
            scores = self.ncm._score_natures()
            sorted_natures = sorted(scores, key=scores.get, reverse=True)
            natures = [n for n in sorted_natures if n in natures][:max_natures]
        
        # Compute routing order
        execution_order = self.router.compute_routing_order(natures)
        
        self.state.phase = "executing"
        self.state.active_natures = set(execution_order)
        
        # Phase 2: Execute Natures in order
        cfg = _get_config()
        cfg.clusters.default_timeout if cfg else 60
        cfg.clusters.max_parallel_worms if cfg else 8
        cfg.clusters.max_retries if cfg else 3
        
        nature_results: Dict[str, Any] = {}
        routed_data: Dict[str, Any] = {}
        total_ok = 0
        total_fail = 0
        errors = []
        cross_nature_paths = []
        
        log = _get_log()
        _get_breakers()
        
        for nature in execution_order:
            self.state.phase = f"executing_{nature}"
            
            # Pre-hook
            self._run_hooks("pre_nature", nature=nature, state=self.state)
            
            # Inject routed data from upstream
            upstream_data = {}
            upstream_routes = self.router.get_routes_to(nature)
            for route in upstream_routes:
                if route.from_nature in nature_results:
                    upstream_data[route.from_nature] = nature_results[route.from_nature]
            
            # Execute the Nature cluster with config-driven timeout
            try:
                ncm_result = self.ncm.execute_nature(nature, parallel=parallel)
                if ncm_result:
                    nature_results[nature] = {
                        "success_count": ncm_result.success_count,
                        "failure_count": ncm_result.failure_count,
                        "duration_ms": ncm_result.duration_ms,
                        "worm_results": ncm_result.worm_results,
                        "errors": ncm_result.errors,
                    }
                    total_ok += ncm_result.success_count
                    total_fail += ncm_result.failure_count
                    if ncm_result.errors:
                        errors.extend(ncm_result.errors)
                    
                    with self.state.lock:
                        self.state.completed_natures.add(nature)
                        self.state.worm_results[nature] = ncm_result.worm_results
                    
                    if log:
                        log.info("nature_executed", {
                            "nature": nature,
                            "ok": ncm_result.success_count,
                            "fail": ncm_result.failure_count,
                            "duration_ms": ncm_result.duration_ms,
                        })
                else:
                    with self.state.lock:
                        self.state.failed_natures.add(nature)
                    err_msg = f"Nature '{nature}' returned no result"
                    errors.append(err_msg)
                    if log:
                        log.warning("nature_no_result", {"nature": nature})
            except Exception as e:
                with self.state.lock:
                    self.state.failed_natures.add(nature)
                err_msg = f"Nature '{nature}' failed: {str(e)}"
                errors.append(err_msg)
                self._run_hooks("on_error", nature=nature, error=str(e), state=self.state)
                if log:
                    log.error("nature_failed", {"nature": nature, "error": str(e)})
            
            # Post-hook
            self._run_hooks("post_nature", nature=nature, result=nature_results.get(nature), state=self.state)
            
            # Phase 3: Route downstream
            if nature in nature_results:
                route_count = self.router.route_nature_output(
                    nature, nature_results[nature], self.state
                )
                if route_count > 0:
                    cross_nature_paths.append(f"{nature} -> {route_count} downstream")
        
        # Phase 4: Generate insights
        self.state.phase = "analyzing"
        insights = self._generate_insights(nature_results, self.state)
        recommendations = self._generate_recommendations(nature_results, self.state)
        
        for insight in insights:
            self._run_hooks("on_insight", insight=insight, state=self.state)
        
        self.state.phase = "complete"
        
        # Compute duration before any try blocks
        result_duration_ms = round((time.perf_counter() - start) * 1000, 2)
        
        # Record metrics
        try:
            from CORE.metrics import metrics_collector
            metrics_collector.record("metacluster.orchestrate.duration_ms", result_duration_ms)
            metrics_collector.record("metacluster.orchestrate.worms_ok", total_ok)
            metrics_collector.record("metacluster.orchestrate.worms_fail", total_fail)
            metrics_collector.record("metacluster.orchestrate.natures", len(nature_results))
        except Exception:
            pass
        
        # Build result
        result = MetaClusterResult(
            orchestration_id=orchestration_id,
            started_at=self.state.started_at,
            completed_at=datetime.now().isoformat(),
            duration_ms=result_duration_ms,
            natures_executed=len(nature_results),
            total_worms_ok=total_ok,
            total_worms_fail=total_fail,
            nature_results=nature_results,
            routed_data=routed_data,
            insights=insights,
            recommendations=recommendations,
            cross_nature_paths_used=cross_nature_paths,
            errors=errors,
        )
        
        with self._lock:
            self._history.append(result)
            if len(self._history) > 100:
                self._history = self._history[-50:]
        
        _record_event("metacluster_orchestrated", {
            "orchestration_id": orchestration_id,
            "natures": len(nature_results),
            "ok": total_ok, "fail": total_fail,
            "duration_ms": result.duration_ms,
            "insights": len(insights),
        })
        
        return result

    def orchestrate_nature(self, nature: str, parallel: bool = False) -> Optional[MetaClusterResult]:
        """Orchestrate a single Nature (convenience method)."""
        return self.orchestrate(natures=[nature], parallel=parallel)

    def orchestrate_adaptive(self, max_natures: int = 5,
                             parallel: bool = False) -> MetaClusterResult:
        """
        Adaptive orchestration: automatically select and prioritize Natures
        based on system state, recent execution history, and diversity engine.
        """
        return self.orchestrate(max_natures=max_natures, parallel=parallel)

    def orchestrate_full_pipeline(self, start_nature: str,
                                  parallel: bool = False) -> MetaClusterResult:
        """
        Execute a full routing pipeline starting from one Nature,
        following all downstream Natures through the routing graph.
        
        Example: orchestrate_full_pipeline("security")
        → security → defense → repair → orchestration
        """
        order = self.router.compute_routing_order([start_nature])
        return self.orchestrate(natures=order, parallel=parallel)
    
    # ── Insights & Recommendations ─────────────────────────────────
    
    def _generate_insights(self, nature_results: Dict[str, Any],
                           state: MetaClusterState) -> List[str]:
        """Generate human-readable insights from execution results."""
        insights = []
        
        total_ok = sum(r.get("success_count", 0) for r in nature_results.values())
        total_fail = sum(r.get("failure_count", 0) for r in nature_results.values())
        total_worms = total_ok + total_fail
        success_rate = (total_ok / max(total_worms, 1)) * 100
        
        if total_worms > 0:
            insights.append(f"Overall success rate: {success_rate:.0f}% ({total_ok}/{total_worms} worms)")
        
        # Per-Nature insights
        for nature, result in sorted(nature_results.items()):
            n_ok = result.get("success_count", 0)
            n_fail = result.get("failure_count", 0)
            n_total = n_ok + n_fail
            
            if n_fail > 0:
                insights.append(f"⚠️ {nature}: {n_fail}/{n_total} worms failed")
            elif n_total > 0:
                insights.append(f"OK {nature}: all {n_total} worms succeeded")
        
        # Cross-Nature insights
        if state.routed_messages:
            route_count = len(state.routed_messages)
            insights.append(f"Cross-Nature routing: {route_count} routes activated")
        
        # Staleness insight
        if self.ncm is not None and hasattr(self.ncm, '_diversity') and self.ncm._diversity:
            staleness = self.ncm._diversity.get_staleness_report()
            never_spawned = [s for s in staleness if s["spawn_count"] == 0]
            if never_spawned:
                insights.append(f"[!] {len(never_spawned)} worm types have never been spawned")
        
        return insights

    def _generate_recommendations(self, nature_results: Dict[str, Any],
                                  state: MetaClusterState) -> List[str]:
        """Generate actionable recommendations from results."""
        recommendations = []
        
        # Failed Natures
        for nature, result in nature_results.items():
            if result.get("failure_count", 0) > 0:
                recommendations.append(f"Investigate failures in '{nature}' Nature cluster")
        
        # Unused Natures
        all_natures = set(self.ncm.clusters.keys()) if self.ncm else set()
        executed = set(nature_results.keys())
        unused = all_natures - executed
        if unused:
            recommendations.append(f"Consider running unused Natures: {', '.join(sorted(unused)[:5])}")
        
        # Routing gaps
        routing_graph = self.router.get_all_paths()
        if len(routing_graph) < 3:
            recommendations.append("Few cross-Nature routing paths found — add more routes between Natures")
        
        return recommendations
    
    # ── Status ─────────────────────────────────────────────────────
    
    def get_status(self) -> Dict[str, Any]:
        """Get full MetaCluster status."""
        ncm_status = self.ncm.get_status() if self.ncm else {}
        return {
            "version": self.VERSION,
            "ncm": ncm_status,
            "routing_paths": len(self.router.get_all_paths()),
            "cross_nature_routes": len(self.router.routes),
            "orchestration_history": len(self._history),
            "last_orchestration": self._history[-1].orchestration_id if self._history else None,
            "free_threaded": is_free_threaded(),
            "hooks_registered": {k: len(v) for k, v in self._hooks.items()},
            "state_phase": self.state.phase if self.state else "idle",
        }
    
    def get_last_result(self) -> Optional[MetaClusterResult]:
        """Get the most recent orchestration result."""
        return self._history[-1] if self._history else None
    
    # ── Mermaid Graphs ─────────────────────────────────────────────
    
    def to_mermaid(self) -> str:
        """
        Generate complete Mermaid graph of the MetaCluster:
        all Natures, their worms, and cross-Nature routes.
        """
        if not self.ncm:
            return "graph TB\n    error[\"NatureClusterManager not available\"]"
        
        return self.ncm.to_mermaid(include_routes=True)

    def to_mermaid_execution_flow(self, result: Optional[MetaClusterResult] = None) -> str:
        """
        Generate Mermaid flowchart showing execution order and results
        from the most recent (or specified) orchestration.
        """
        result = result or self.get_last_result()
        if not result:
            return "graph LR\n    empty[\"No orchestration results yet\"]"
        
        lines = ["graph LR"]
        lines.append(f'    title["* MetaCluster Orchestration<br/>{result.orchestration_id}<br/>{result.duration_ms:.0f}ms, {result.total_worms_ok}OK/{result.total_worms_fail}FAIL"]')
        lines.append("    style title fill:#1a1a2e,stroke:#00d9ff,color:#00d9ff")
        lines.append("")
        
        # Nodes for each Nature
        for nature, n_result in result.nature_results.items():
            ok = n_result.get("success_count", 0)
            fail = n_result.get("failure_count", 0)
            status = "ok" if fail == 0 else "fail"
            color = "#00ff88" if status == "ok" else "#ff4444"
            
            definition = NATURE_DEFINITIONS.get(nature, {})
            icon = definition.get("icon", "")
            
            duration = n_result.get('duration_ms', 0)
            lines.append(f'    {nature}["{icon} {nature}<br/>{ok}OK {fail}FAIL ({duration:.0f}ms)"]')
            lines.append(f"    style {nature} fill:#16213e,stroke:{color},color:{color}")
        
        # Routing edges
        seen_edges = set()
        for path in result.cross_nature_paths_used:
            parts = path.split(" → ")
            for i in range(len(parts) - 1):
                edge = (parts[i], parts[i+1])
                if edge not in seen_edges:
                    seen_edges.add(edge)
                    lines.append(f"    {edge[0]} --> {edge[1]}")
        
        return "\n".join(lines)

    def to_mermaid_routing_topology(self) -> str:
        """
        Generate Mermaid graph of the complete cross-Nature routing topology.
        Shows all possible data flow paths through the ecosystem.
        """
        lines = ["graph TB"]
        lines.append('    title["Cross-Nature Routing Topology"]')
        lines.append("    style title fill:#1a1a2e,stroke:#00d9ff,color:#00d9ff")
        lines.append("")
        
        for route in self.router.routes:
            lines.append(f'    {route.from_nature}["{route.from_nature}"] -->|feeds| {route.to_nature}["{route.to_nature}"]')
        
        return "\n".join(lines)

    def to_mermaid_all_functions(self) -> str:
        """
        Generate Mermaid class diagram showing all worm classes,
        their methods, and which Nature they belong to.
        """
        if not self.ncm:
            return "classDiagram\n    %% No data"
        
        return self.ncm.to_mermaid_functions()
    
    # ── Persistence ────────────────────────────────────────────────
    
    def export_state(self, filepath: str = "") -> str:
        """Export full MetaCluster state to JSON."""
        if not filepath:
            filepath = str(Path.home() / "azazael" / "data" / "metacluster_state.json")
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        export = {
            "version": self.VERSION,
            "exported_at": datetime.now().isoformat(),
            "free_threaded": is_free_threaded(),
            "ncm_status": self.ncm.get_status() if self.ncm else {},
            "routes": [{"from": r.from_nature, "to": r.to_nature} for r in self.router.routes],
            "orchestration_history": [
                {
                    "id": r.orchestration_id,
                    "natures": r.natures_executed,
                    "ok": r.total_worms_ok,
                    "fail": r.total_worms_fail,
                    "duration_ms": r.duration_ms,
                    "insights": r.insights,
                    "recommendations": r.recommendations,
                }
                for r in self._history[-20:]
            ],
        }
        
        with open(filepath, "w") as f:
            json.dump(export, f, indent=2, default=str)
        
        _record_event("metacluster_exported", {"filepath": filepath})
        return filepath

    def __repr__(self):
        natures = len(self.ncm.clusters) if self.ncm else 0
        routes = len(self.router.routes)
        history = len(self._history)
        return f"MetaCluster(v{self.VERSION}, {natures} natures, {routes} routes, {history} runs)"


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _record_event(event_type: str, data: Dict[str, Any]):
    """Record event to PAMS memory."""
    pams = _get_pams()
    if pams:
        try:
            pams.record_event(
                event_type, data,
                source="MetaCluster",
                severity="info",
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="METACLUSTER — MetaCluster Orchestrator"
    )
    parser.add_argument("--discover", action="store_true", help="Discover all Natures")
    parser.add_argument("--orchestrate", type=str, nargs="*", help="Orchestrate specific Natures")
    parser.add_argument("--adaptive", type=int, default=0, help="Adaptive orchestration with max N Natures")
    parser.add_argument("--pipeline", type=str, help="Run full pipeline from a start Nature")
    parser.add_argument("--parallel", action="store_true", help="Parallel execution")
    parser.add_argument("--mermaid", action="store_true", help="Full Mermaid graph")
    parser.add_argument("--mermaid-flow", action="store_true", help="Execution flow Mermaid graph")
    parser.add_argument("--mermaid-routing", action="store_true", help="Routing topology Mermaid graph")
    parser.add_argument("--mermaid-functions", action="store_true", help="Class diagram Mermaid graph")
    parser.add_argument("--export", type=str, help="Export state to JSON file")
    parser.add_argument("--status", action="store_true", help="Show MetaCluster status")
    
    args = parser.parse_args()
    mc = MetaCluster()
    
    if args.discover:
        counts = mc.discover_all()
        print(f"Discovered {len(counts)} Natures, {sum(counts.values())} worms")
    
    elif args.orchestrate is not None:
        natures = args.orchestrate if args.orchestrate else None
        result = mc.orchestrate(natures=natures, parallel=args.parallel)
        print(f"Orchestrated {result.natures_executed} Natures: {result.total_worms_ok}OK/{result.total_worms_fail}FAIL ({result.duration_ms:.0f}ms)")
        for insight in result.insights:
            print(f"  {insight}")
        for rec in result.recommendations:
            print(f"  💡 {rec}")
    
    elif args.adaptive > 0:
        result = mc.orchestrate_adaptive(max_natures=args.adaptive, parallel=args.parallel)
        print(f"Adaptive: {result.natures_executed} Natures, {result.total_worms_ok}OK/{result.total_worms_fail}FAIL ({result.duration_ms:.0f}ms)")
    
    elif args.pipeline:
        result = mc.orchestrate_full_pipeline(args.pipeline, parallel=args.parallel)
        print(f"Pipeline [{args.pipeline}]: {result.natures_executed} Natures, {result.total_worms_ok}OK/{result.total_worms_fail}FAIL")
        for path in result.cross_nature_paths_used:
            print(f"  {path}")
    
    elif args.mermaid:
        print(mc.to_mermaid())
    
    elif args.mermaid_flow:
        print(mc.to_mermaid_execution_flow())
    
    elif args.mermaid_routing:
        print(mc.to_mermaid_routing_topology())
    
    elif args.mermaid_functions:
        print(mc.to_mermaid_all_functions())
    
    elif args.export:
        path = mc.export_state(args.export)
        print(f"Exported to: {path}")
    
    elif args.status:
        import json
        print(json.dumps(mc.get_status(), indent=2, default=str))
    
    else:
        print("METACLUSTER v" + MetaCluster.VERSION)
        print()
        print("Usage:")
        print("  --discover                   Discover all Natures")
        print("  --orchestrate [NATURES...]   Orchestrate specific Natures")
        print("  --adaptive N                 Adaptive orchestration (max N Natures)")
        print("  --pipeline NATURE            Execute full routing pipeline")
        print("  --parallel                   Use parallel execution")
        print("  --mermaid                    Full Mermaid graph")
        print("  --mermaid-flow               Execution flow graph")
        print("  --mermaid-routing            Routing topology")
        print("  --mermaid-functions          Class diagram")
        print("  --export PATH                Export state to JSON")
        print("  --status                     Show status")


if __name__ == "__main__":
    main()
