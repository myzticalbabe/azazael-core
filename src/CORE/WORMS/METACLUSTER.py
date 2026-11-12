# coding: utf-8
"""
METACLUSTER — MetaCluster Orchestrator (Cluster-of-Clusters)

Meta-level orchestrator that manages registry categories as a unified fleet.
U3/D1: the nature taxonomy is gone. A MetaCluster coordinates CATEGORIES —
flat registry dispatch, no cross-category routing, no feeds_into topology.

Architecture:
    MetaCluster (top-level orchestrator)
    ├── Category("monitor")     → monitor worms (from registry)
    ├── Category("diagnostic")  → diagnostic worms
    └── ... (all registry categories, flat)

    MetaClusterState (shared state across all categories)
    ├── Results aggregation
    ├── Insight generation
    └── PAMS recording of every action

Public surface is unchanged (orchestrate/discover_all/get_status/mermaid/
hooks/history); the `categories` parameters now take category names. Result and
state field names keep their keys so chain/state readers do not break.

Usage:
    >>> from CORE.WORMS.METACLUSTER import MetaCluster
    >>> mc = MetaCluster()
    >>> mc.discover_all()              # Auto-discover all categories
    >>> results = mc.orchestrate()     # Run priority categories end-to-end
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

#: Fail-open swallows recorded when the runtime recorder is unavailable. A
#: ledger or reflection failure must never break orchestration, but it must not
#: be indistinguishable from success either.
_SWALLOWED: List[str] = []


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # runtime absent: stay importable
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        _SWALLOWED.append("%s:no-recorder" % where)


#: Canonical project root for this module's default paths. The fallback used to be
#: `Path.home() / "azazael"`, which names a DIFFERENT, stale checkout from this repo
#: (`C:/Azazael/azazael`), so `export_state()` with no filepath wrote the state file
#: into a tree nobody reads. CORE.paths owns that resolution (AZAZAEL_ROOT first,
#: never raises), so the decision is made once instead of re-derived per module.
from CORE.paths import get_project_root_safe as _get_project_root_safe
import argparse


def _note_failure(where: str, exc: BaseException | None = None) -> None:
    """Record a fail-open swallow. Never raises."""
    try:
        note_failure(where, exc)
    except Exception as _e:
        _SWALLOWED.append("%s:%s" % (where, type(_e).__name__))

# ── Flat registry dispatch (U3/D1) ─────────────────────────────────
# No ClusterManager, no CrossNatureRoutingEngine: both were deleted
# with the taxonomy. Categories come straight from the worm registry.
try:
    from CORE.worm_registry import (
        register_worm, WORM_REGISTRY, discover_worms,
        get_all_categories, get_worms_by_category, mark_spawned,
    )
    _REGISTRY_OK = True
except ImportError as exc:
    note_failure("METACLUSTER:L72", exc)
    _REGISTRY_OK = False
    WORM_REGISTRY = {}
    def discover_worms(worms_dir: Optional[str] = None, progress_callback: Optional[Callable] = None) -> int: return 0
    def get_all_categories() -> Set[str]: return set()
    def get_worms_by_category(category: str) -> Dict[str, Dict]: return {}
    def mark_spawned(worm_type: str) -> None: return None
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags: Optional[Set[str]] = None):
        def _identity(cls):
            return cls
        return _identity
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
                    from CORE.PamsRecorder import get_recorder
                    _pams = get_recorder()
                except ImportError as exc:
                    note_failure("METACLUSTER:L92", exc)
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
                except ImportError as exc:
                    note_failure("METACLUSTER:L109", exc)
                    _mem_bus = False
    return _mem_bus if _mem_bus else None

# ── Free-Threading ─────────────────────────────────────────────────
try:
    from CORE.freethreading import is_free_threaded
    _FREETHREAD_OK = True
except ImportError as exc:
    note_failure("METACLUSTER:L117", exc)
    _FREETHREAD_OK = False
    def is_free_threaded() -> bool:
        try:
            return not sys._is_gil_enabled()
        except Exception as exc:
            note_failure("METACLUSTER:L122", exc)
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
                except ImportError as exc:
                    note_failure("METACLUSTER:L137", exc)
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
                except ImportError as exc:
                    note_failure("METACLUSTER:L154", exc)
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
                except ImportError as exc:
                    note_failure("METACLUSTER:L171", exc)
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
    categories_executed: int = 0
    total_worms_ok: int = 0
    total_worms_fail: int = 0
    category_results: Dict[str, Any] = field(default_factory=dict)
    routed_data: Dict[str, Any] = field(default_factory=dict)
    insights: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    cross_category_paths_used: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class MetaClusterState:
    """Shared state across all clusters in a MetaCluster run."""
    run_id: str = ""
    started_at: str = ""
    phase: str = "idle"  # idle, discovering, executing, routing, analyzing, complete
    active_categories: Set[str] = field(default_factory=set)
    completed_categories: Set[str] = field(default_factory=set)
    failed_categories: Set[str] = field(default_factory=set)
    worm_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # {category: {worm: result}}
    routed_messages: List[Dict] = field(default_factory=list)
    events: List[Dict] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)


# NOTE: CrossNatureRoutingEngine lived in CORE.WORMS.CROSS_NATURE_ROUTER,
# deleted with the taxonomy (U3/D7). Nothing routes between categories.


# ═══════════════════════════════════════════════════════════════════════
# META CLUSTER — The Top-Level Orchestrator
# ═══════════════════════════════════════════════════════════════════════

@register_worm(category="orchestration", priority=10, cooldown_seconds=5.0,
    description="MetaCluster — cluster-of-clusters orchestrator coordinating all registry categories with flat dispatch and PAMS recording")
class MetaCluster:
    """
    MetaCluster — the top-level orchestrator that manages clusters of clusters.
    
    Coordinates registry categories with flat dispatch, generates insights,
    and records every action to PAMS memory.
    
    Usage:
        mc = MetaCluster()
        mc.discover_all()
        
        # Run a single category end-to-end
        result = mc.orchestrate_category("security")
        
        # Run multiple categories
        result = mc.orchestrate(categories=["security", "defense", "monitor"])
        
        # Run with auto-prioritization
        result = mc.orchestrate_adaptive(max_categories=5)
        
        # Visualize
        print(mc.to_mermaid())
        print(mc.to_mermaid_execution_flow())
    """
    
    VERSION = "2.0.0"
    
    def __init__(self):
        self.categories: Dict[str, List[str]] = {}
        self.state: Optional[MetaClusterState] = None
        self._history: List[MetaClusterResult] = []
        #: Worm subset per fissioned atom (EP374). Empty unless a caller passed
        #: a `capability_split` hook; a plain category name is never a key, so a
        #: later run without the hook cannot pick up a previous run's atoms.
        self._atom_worms: Dict[str, List[str]] = {}
        self._lock = threading.RLock()
        self._hooks: Dict[str, List[Callable]] = {
            "pre_category": [],
            "post_category": [],
            "on_insight": [],
            "on_error": [],
        }
    
    # ── Discovery ──────────────────────────────────────────────────
    
    def discover_all(self) -> Dict[str, int]:
        """Discover all registry categories from the worm registry."""
        try:
            discover_worms()
        except Exception as exc:
            note_failure("METACLUSTER:L269", exc)
        counts = {c: len(get_worms_by_category(c))
                  for c in sorted(get_all_categories())}
        self.categories = {c: sorted(get_worms_by_category(c))
                           for c in counts}
        _record_event("metacluster_discovery", {
            "categories": len(counts),
            "total_worms": sum(counts.values()),
            "per_category": counts,
        })
        return counts
    
    # ── Hooks ──────────────────────────────────────────────────────
    
    def on(self, hook_name: str, callback: Callable):
        """Register a hook callback. Supports: pre_category, post_category, on_insight, on_error."""
        if hook_name in self._hooks:
            self._hooks[hook_name].append(callback)

    def _run_hooks(self, hook_name: str, **kwargs):
        """Run all registered hooks for a given hook point."""
        for cb in self._hooks.get(hook_name, []):
            try:
                cb(**kwargs)
            except Exception as exc:
                note_failure("METACLUSTER:L294", exc)
    
    # ── Orientation ────────────────────────────────────────────────
    
    # ── Plan model (Helium EP130/EP171): order + levels for a dispatch ──
    #: Above this many categories a plan is not worth building (quadratic
    #: dependency scan); the caller's order is used unchanged instead.
    _PLAN_MAX_CATEGORIES = 64

    def _plan_execution_order(self, order: List[str], *,
                              granularity: Optional[Any] = None,
                              capability_split: Optional[Callable] = None,
                              plan_out: Optional[Dict[str, Any]] = None) -> tuple:
        """Derive category execution order and levels from a dispatch plan.

        `order` is the caller's requested order, already filtered to known,
        non-circular categories. Returns (ordered_names, level_of, note).

        The plan is REAL, not decorative. U3/D1 deleted the old taxonomy
        and with it every declared dependency edge, so the edges are derived
        from the data instead: two categories that share a worm are dependent
        (re-running the same worm is exactly the duplication Helium's
        common-subgraph elimination exists to expose). Categories sharing
        nothing land at the same level and are safe to run concurrently.

        The caller's order stays authoritative WITHIN a level -- the result is
        sorted by (level, requested index), so it can reorder only where a
        dependency actually requires it.

        Any failure returns the input order with an empty level map: planning
        must never be able to break orchestration.

        Three optional hooks carry the caller's knowledge without this module
        importing a sibling: `capability_split` (EP374) fans a category into one
        atom per capability group, `granularity` (EP197) supplies the
        within-level complexity order, and `plan_out` (EP186) receives the built
        plan, assignment and rehearsal. With no hook supplied the plan is
        exactly the one this method has always built.
        """
        names = [str(n) for n in (order or [])]
        # Cleared before ANY return below: a run without the hook must not pick
        # up a previous run's atoms (a plain category name is never a key).
        self._atom_worms = {}
        # The single-name exit applies only when there is no split to make. One
        # category is EXACTLY the shape a capability split changes the count of
        # (EP374: a lone category that is really two dispatch units), so testing
        # the count first returns `plan-skipped:single` and skips the case the
        # row exists for. With no hook this is the old early return unchanged,
        # and still ahead of the dispatch_plan import.
        if capability_split is None and len(names) < 2:
            return names, {}, "plan-skipped:single"
        if len(names) > self._PLAN_MAX_CATEGORIES:
            return names, {}, "plan-skipped:too-many"
        try:
            from CORE.dispatch_plan import (
                assign_roles, eliminate_common_subgraphs, plan_dag,
                record_dispatch_plan, rehearse_dispatch, simulate_vs_spawn,
            )
        except Exception as exc:  # noqa: BLE001 — planning is optional
            note_failure("METACLUSTER:L333", exc)
            return names, {}, "plan-fallback:%s" % type(exc).__name__
        try:
            idx = {n: i for i, n in enumerate(names)}
            worms: Dict[str, Set[str]] = {n: set(self.categories.get(n) or []) for n in names}
            # EP374 (TopoAgent): adaptive atomic fission. A category is ONE
            # dispatch unit for the whole run, so two of its worm groups that
            # share nothing still execute one after the other. The capability
            # boundary is caller knowledge -- a sibling owns the module that
            # would hold it -- so it arrives as a callable, and with none the
            # plan below is the plan built before this branch existed.
            if capability_split is not None:
                atoms, atom_worms = self._split_into_atoms(
                    names, worms, capability_split)
                if atoms != names:
                    names = atoms
                    idx = {n: i for i, n in enumerate(names)}
                    worms = {n: set(atom_worms.get(n) or ()) for n in names}
                    self._atom_worms = atom_worms
                # Post-split, not pre-split: a lone category that did NOT split
                # (one group, or a hook that raised and left it intact) is still
                # the single-category plan -- no plan built, empty level map,
                # and no atoms registered.
                if len(names) < 2:
                    return names, {}, "plan-skipped:single"
            # EP197: the caller's complexity policy is translated into the
            # per-task "complexity" that `plan_dag` ALREADY accepts, so the
            # within-tier order comes from the planner's own `ready_order`
            # instead of a second ordering rule re-derived here. With no policy
            # every weight is 0.0, which leaves `order`/`levels` and the sort
            # below exactly as they were.
            weights = self._granularity_weights(names, worms, granularity)
            tasks = [{"id": n,
                      "deps": [m for m in names
                               if idx[m] < idx[n] and (worms[m] & worms[n])],
                      "complexity": weights.get(n, 0.0)}
                     for n in names]
            plan = plan_dag(tasks)
            # Rehearse-before-execute: dry-run the plan AND its assignment and
            # predict failures before anything is dispatched. Roles/pools come
            # from data that exists -- U3/D1 deleted the old taxonomy, so
            # there is no per-category role left to read; every category in
            # this run honestly belongs to the one pool this method orchestrates.
            # Inventing a role per category would be fabricated input, and the
            # single real pool still yields the rehearsal's oversubscription
            # signal (pool_load = categories this run will execute in that pool).
            assignment = assign_roles(
                plan.get("order") or names,
                {n: "category" for n in names},
                {"category": "metacluster"})
            rehearsal = rehearse_dispatch(plan, assignment)
            predicted = list(rehearsal.get("predicted_failures") or [])
            # EP110 (simulate-vs-spawn): price the rehearsal against the
            # spawn it replaces and ride the verdict on the plan note and
            # plan_out, so the costing reaches the live dispatch seam. Pure
            # and non-raising by contract; the outer guard stays backstop.
            costing = simulate_vs_spawn(plan, assignment,
                                        rehearsal=rehearsal)
            if not plan.get("valid"):
                # The rehearsal's predicted failures (plan-gap:*/assign-gap:*)
                # EXPLAIN the unusable plan, so they are what the fallback note
                # reports; the raw gap list is only the fallback for a rehearsal
                # that produced nothing to say.
                gaps = ";".join(
                    str(g) for g in (predicted or (plan.get("gaps") or [])))
                self._publish_plan(plan_out, plan=plan, ordered=names,
                                   levels={}, assignment=assignment,
                                   rehearsal=rehearsal, costing=costing,
                                   atoms=dict(self._atom_worms),
                                   note="plan-fallback:invalid(%s)" % gaps[:120])
                return names, {}, "plan-fallback:invalid(%s)" % gaps[:120]
            levels = plan.get("levels") or {}
            # EP197: the level comes from the dependency DAG, and nodes inside a
            # level are independent by construction -- so their relative order
            # is a policy choice, not a correctness one. The policy rides the
            # per-task `complexity` handed to `plan_dag` above, and the order
            # read back is the planner's own `ready_order` (descending priority
            # then complexity within a tier), so this host does not re-derive an
            # ordering rule a sibling already owns. With no policy `rank` is
            # empty and the key collapses to (level, requested index): the
            # previous sort, exactly.
            rank = ({n: i for i, n in enumerate(plan.get("ready_order") or [])}
                    if weights else {})
            ordered = sorted(
                plan.get("order") or names,
                key=lambda n: (levels.get(n, 0), rank.get(n, idx.get(n, 0)),
                               idx.get(n, 0)))
            sharing = eliminate_common_subgraphs(
                {n: sorted(worms[n]) for n in names})
            note = "planned:levels=%d,shared_segments=%d,dedup_savings=%d" % (
                (max(levels.values()) + 1) if levels else 0,
                len(sharing.get("shared") or []),
                int(sharing.get("savings") or 0))
            if sharing.get("savings"):
                # Shared worms are work this run can do once. Recorded, not
                # silently acted on: the report must stay readable.
                note += ",shared_worms=%d" % int(sharing.get("savings") or 0)
            if self._atom_worms:
                # A split plan has to SAY it split: an atom is a dispatch unit,
                # and a reader counting categories would otherwise read the
                # fan-out as a plan that changed nothing.
                note += ",fissioned=%d atoms" % len(self._atom_worms)
            # The rehearsal's verdict rides the plan note that orchestration
            # already reports, so a held plan and the busiest pool are visible
            # without a second telemetry channel.
            note += ",rehearsed=%s,predicted_failures=%d" % (
                "go" if rehearsal.get("go") else "hold", len(predicted))
            note += ",cost=%s" % costing.get("decision", "spawn")
            pool_load = rehearsal.get("pool_load") or {}
            if pool_load:
                busiest = max(pool_load, key=lambda p: pool_load[p])
                note += ",busiest_pool=%s(%d)" % (busiest, int(pool_load[busiest]))
            try:
                record_dispatch_plan("metacluster", plan)
            except Exception as exc:  # noqa: BLE001 — ledger is best-effort
                _note_failure("metacluster:dispatch-plan-ledger", exc)
            try:
                _record_event("metacluster_plan", {
                    "note": note, "order": ordered, "levels": levels})
            except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
                _note_failure("metacluster:plan-event", exc)
            self._publish_plan(plan_out, plan=plan, ordered=ordered,
                               levels=levels, assignment=assignment,
                               rehearsal=rehearsal, costing=costing,
                               atoms=dict(self._atom_worms),
                               note=note)
            return ordered, levels, note
        except Exception as exc:  # noqa: BLE001 — never break orchestration
            note_failure("METACLUSTER:L369", exc)
            return names, {}, "plan-fallback:%s" % type(exc).__name__

    def _split_into_atoms(self, names: List[str], worms: Dict[str, Set[str]],
                          capability_split: Callable) -> tuple:
        """Fan a category out into one atom per capability group (EP374).

        `capability_split` is called as `(category, sorted_worms)` and returns a
        mapping of group label -> the worms in that group. A category yielding
        fewer than two NON-EMPTY groups is not split: a single group IS the
        category, and renaming a node for no gain would only churn the plan.
        Any failure leaves the caller's names untouched, because fission is an
        optimisation and a category that cannot be grouped must still run.

        Returns (atom_names, {atom: its worms}); an atom is named
        `<category>#<label>` -- the same `#` convention `plan_dag` uses for its
        capability-level fission, so a reader of the plan sees one shape. The
        two are complements, not duplicates: `plan_dag(split_on_capability=True)`
        splits a TASK that declares capability names, while this host's unit is a
        category whose members are WORMS, so the boundary here groups worms.
        """
        atoms: List[str] = []
        groups: Dict[str, List[str]] = {}
        for n in names:
            try:
                split = capability_split(n, sorted(worms.get(n) or ()))
            except Exception as exc:        # noqa: BLE001 — fission is optional
                _note_failure("metacluster:capability-split", exc)
                split = None
            per: List[str] = []
            if isinstance(split, dict) and len(split) > 1:
                for label, members in split.items():
                    if not members:
                        continue
                    atom = "%s#%s" % (n, str(label))
                    per.append(atom)
                    groups[atom] = [str(m) for m in members]
            if len(per) < 2:
                for atom in per:
                    groups.pop(atom, None)
                atoms.append(n)
                continue
            atoms.extend(per)
        return atoms, groups

    def _granularity_weights(self, names: List[str],
                             worms: Dict[str, Set[str]],
                             granularity: Any) -> Dict[str, float]:
        """Per-node complexity weight for the within-level order (EP197).

        `granularity` is either a callable `(name, sorted_worms) -> weight` or a
        mapping of name -> weight. No policy means no weights, which is what
        keeps the default order identical. A callable that raises for one node
        is skipped FOR THAT NODE: a scheduling policy is advice, and advice must
        never be able to fail a plan.
        """
        if granularity is None:
            return {}
        weights: Dict[str, float] = {}
        for n in names:
            try:
                if callable(granularity):
                    raw_weight: Any = granularity(
                        n, sorted(worms.get(n) or ()))
                    weights[n] = float(raw_weight)
                else:
                    weights[n] = float(granularity.get(n, 0.0))
            except Exception as exc:        # noqa: BLE001 — policy is advisory
                _note_failure("metacluster:granularity-weight", exc)
                continue
        return weights

    @staticmethod
    def _publish_plan(plan_out: Optional[Dict[str, Any]],
                      **fields: Any) -> None:
        """Hand the built plan to a caller that passed an out-dict (EP186).

        SGDe's half of that row is PLAN CONSTRUCTION, and until now the plan was
        visible only as a formatted note string -- a caller wanting the artefacts
        had to import the dispatch module itself. A dict passed in is that
        exposure without this module importing a sibling.
        """
        if isinstance(plan_out, dict):
            plan_out.update(fields)

    def orchestrate(self, categories: Optional[List[str]] = None,
                    parallel: bool = False, max_categories: int = 0, *,
                    granularity: Optional[Any] = None,
                    capability_split: Optional[Callable] = None,
                    plan_out: Optional[Dict[str, Any]] = None) -> MetaClusterResult:
        """
        Orchestrate a set of registry categories end-to-end (U3: `categories`
        takes category names; kept for caller compatibility).
        
        Args:
            categories: Specific categories to execute (None = auto-discover all)
            parallel: Execute categories in parallel where possible
            max_categories: Limit to top-N categories by priority weight
            granularity: Optional complexity policy for the within-level order.
            capability_split: Optional `(category, worms) -> {label: worms}`
                that splits a category into atoms at its capability boundaries.
            plan_out: Optional dict receiving the built plan, assignment and
                rehearsal, for a caller that needs the plan itself.
        
        Returns:
            MetaClusterResult with full execution details and insights.
        """
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        # Initialize state
        orchestration_id = f"meta_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:6]}"
        self.state = MetaClusterState(
            run_id=orchestration_id,
            started_at=datetime.now().isoformat(),
            phase="discovering",
        )
        
        start = time.perf_counter()
        
        # Phase 1: Discover
        if not self.categories:
            self.discover_all()
        
        # Determine categories to execute
        if categories is None:
            categories = sorted(self.categories.keys())
        
        if max_categories > 0:
            ranked = self._rank_categories(categories)
            # `max_categories` must cap what can actually RUN. A circular
            # category (the orchestration infrastructure itself) is dropped by
            # the filter below, so letting it consume the budget made a
            # request for one category execute ZERO -- and the run still
            # reported `complete`. Same guarantee as the old filter.
            eligible = [n for n in ranked if not self._is_circular_category(n)]
            # If EVERY requested category is circular, keep the old selection
            # so the filter below still reports and marks them: an empty
            # request must stay visible as a no-op, never silently replaced.
            categories = (eligible or ranked)[:max_categories]
        
        # Requested order is execution order (no routing topology to sort by).
        execution_order = [n for n in categories if n in self.categories]
        
        # ── Drop circular categories ──────────────────────────────
        # A category holding the orchestration infrastructure would re-enter
        # this method or deadlock. Mark its worms spawned to keep
        # "never spawned" alerts honest.
        circular_categories = [n for n in execution_order if self._is_circular_category(n)]
        if circular_categories:
            execution_order = [n for n in execution_order if n not in circular_categories]
            for category in circular_categories:
                for worm_name in (self.categories.get(category) or []):
                    try:
                        mark_spawned(worm_name)
                    except Exception as exc:  # noqa: BLE001 — bookkeeping is best-effort
                        note_failure("METACLUSTER:L432", exc)

        # ── Plan the dispatch (Helium) ──────────────────────────────
        # Derive the execution order and its levels from a task DAG. Planning
        # is fail-open: on any failure `level_of` is empty and `execution_order`
        # is exactly what it was above, so the executor below is the same
        # sequential loop this method always ran.
        # The hooks are passed ONLY when a caller supplied one, so the default
        # call keeps its single-positional-argument shape: an existing wrapper
        # of this method (and the default plan) is untouched by the new rows.
        if granularity is None and capability_split is None and plan_out is None:
            execution_order, level_of, plan_note = self._plan_execution_order(
                execution_order)
        else:
            execution_order, level_of, plan_note = self._plan_execution_order(
                execution_order, granularity=granularity,
                capability_split=capability_split, plan_out=plan_out)

        self.state.phase = "executing"
        self.state.active_categories = set(execution_order)
        
        # Phase 2: Execute categories in order
        cfg = _get_config()
        cfg.clusters.default_timeout if cfg else 60
        cfg.clusters.max_parallel_worms if cfg else 8
        cfg.clusters.max_retries if cfg else 3
        
        category_results: Dict[str, Any] = {}
        routed_data: Dict[str, Any] = {}
        total_ok = 0
        total_fail = 0
        errors = []

        # A run that executes nothing must SAY so. Everything below is a loop
        # over `execution_order`, so an empty order produced a result object with
        # phase "complete", zero errors and zero categories -- indistinguishable
        # from a clean full pass. `active_categories` is already empty here; this
        # makes it countable by a caller that only reads `errors`.
        if not execution_order:
            errors.append(
                "no category was eligible to execute: the requested set collapsed "
                "to nothing after the orchestration-infrastructure filter "
                "(circular=%s)" % (sorted(circular_categories) or "none"))
        cross_category_paths = []
        
        log = _get_log()
        _get_breakers()
        
        # Shared-accumulator lock: `parallel` runs same-level categories on
        # threads, and free-threaded 3.14t gives no GIL safety net for a
        # read-modify-write on an int. Held only around the accumulator writes.
        _acc_lock = threading.Lock()

        def _execute_category(category: str) -> None:
            """Execute one category end-to-end.

            Extracted from the sequential loop verbatim so a level's independent
            categories can run concurrently; only the accumulator writes are
            locked, so the work itself stays parallel.
            """
            nonlocal total_ok, total_fail
            # Bind the state once: this closure runs on a worker thread, so the
            # type checker cannot narrow `self.state` here even though
            # `orchestrate` has already constructed it before dispatch.
            state = self.state
            if state is None:  # defensive: refused rather than crashing later
                raise RuntimeError("orchestrate: state not initialised")
            state.phase = f"executing_{category}"
            
            # Pre-hook
            self._run_hooks("pre_category", category=category, state=self.state)
            
            # Execute the category cluster with config-driven timeout
            try:
                # A fissioned atom (EP374) carries its own worm subset -- the
                # atom IS the unit -- so the cluster is built from the group,
                # not from the whole category. Every name that was not split
                # falls back to its category unchanged.
                atom_worms = self._atom_worms.get(category)
                cluster = ClusterWORMZ.build_from_registry(
                    sorted(atom_worms if atom_worms is not None
                           else (self.categories.get(category) or [])))
                cluster.set_parallel(parallel)
                cluster.execute_all()
                agg = cluster.aggregate_results()
                results = agg.worm_results or {}
                errs = [str((v or {}).get("error", ""))[:100]
                        for v in results.values()
                        if isinstance(v, dict) and not v.get("success")
                        and (v or {}).get("error")]
                with _acc_lock:
                    category_results[category] = {
                        "success_count": agg.success_count,
                        "failure_count": agg.failure_count,
                        "duration_ms": agg.duration_ms,
                        "worm_results": results,
                        "errors": errs,
                    }
                    total_ok += agg.success_count
                    total_fail += agg.failure_count
                    if errs:
                        errors.extend(errs)
                
                with state.lock:
                    state.completed_categories.add(category)
                    state.worm_results[category] = results
                
                if log:
                    log.info("category_executed", {
                        "category": category,
                        "ok": agg.success_count,
                        "fail": agg.failure_count,
                        "duration_ms": agg.duration_ms,
                    })
            except Exception as e:
                with state.lock:
                    state.failed_categories.add(category)
                err_msg = f"Category '{category}' failed: {str(e)}"
                with _acc_lock:
                    errors.append(err_msg)
                self._run_hooks("on_error", category=category, error=str(e), state=self.state)
                if log:
                    log.error("category_failed", {"category": category, "error": str(e)})

                # EP113 Reflexion: a failed category leaves a REUSABLE lesson,
                # not only an error string in a log. Before this call
                # `reflect_on_failure` had zero production callers -- the buffer
                # was complete and nothing ever wrote to it.
                #
                # Deliberately last in the handler and fully fail-open: the
                # bookkeeping above (failed_categories, errors, hooks, log) has
                # already happened, so a reflection can neither change control
                # flow nor lose a failure record. `what_next` is derived from the
                # nature and the phase so the lesson is actionable rather than
                # filler.
                try:
                    from CORE.technique_runtime import reflect_on_failure
                    reflect_on_failure(
                        question=f"category:{category}",
                        what_failed=err_msg[:300],
                        what_next=(
                            f"re-run category '{category}' alone to isolate the "
                            f"fault; it failed during phase "
                            f"'{state.phase}'. Check its category's worm "
                            f"dependencies before re-running the full set."),
                        decision_id=f"metacluster:{category}")
                except Exception as exc:              # noqa: BLE001 — reflection best-effort
                    _note_failure("metacluster:reflect", exc)
            
            # Post-hook
            self._run_hooks("post_category", category=category, result=category_results.get(category), state=self.state)

        # ── Execute in plan order, fanning out each independent level ──
        # `level_of` comes from the plan; empty means no plan was built, which
        # makes this the sequential loop unchanged. Concurrency needs BOTH a
        # plan and the caller's `parallel` opt-in, so the default path is
        # byte-for-byte the old behaviour.
        if level_of and parallel and len(execution_order) > 1:
            budget = max(1, int(cfg.clusters.max_parallel_worms if cfg else 8))
            for level in sorted(set(level_of.values())):
                group = [n for n in execution_order
                         if level_of.get(n, 0) == level]
                for i in range(0, len(group), budget):
                    batch = group[i:i + budget]
                    if len(batch) == 1:
                        _execute_category(batch[0])
                        continue
                    threads = [threading.Thread(target=_execute_category,
                                                args=(n,), daemon=True)
                               for n in batch]
                    for t in threads:
                        t.start()
                    for t in threads:
                        t.join()
        else:
            for category in execution_order:
                _execute_category(category)
        
        # Phase 4: Generate insights
        self.state.phase = "analyzing"
        insights = self._generate_insights(category_results, self.state)
        recommendations = self._generate_recommendations(category_results, self.state)
        
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
            metrics_collector.record("metacluster.orchestrate.categories", len(category_results))
        except Exception as exc:
            note_failure("METACLUSTER:L617", exc)
        
        # Build result
        result = MetaClusterResult(
            orchestration_id=orchestration_id,
            started_at=self.state.started_at,
            completed_at=datetime.now().isoformat(),
            duration_ms=result_duration_ms,
            categories_executed=len(category_results),
            total_worms_ok=total_ok,
            total_worms_fail=total_fail,
            category_results=category_results,
            routed_data=routed_data,
            insights=insights,
            recommendations=recommendations,
            cross_category_paths_used=cross_category_paths,
            errors=errors,
        )
        
        with self._lock:
            self._history.append(result)
            if len(self._history) > 100:
                self._history = self._history[-50:]
        
        _record_event("metacluster_orchestrated", {
            "orchestration_id": orchestration_id,
            "categories": len(category_results),
            "ok": total_ok, "fail": total_fail,
            "duration_ms": result.duration_ms,
            "insights": len(insights),
            "plan": plan_note,
        })
        
        return result

    def _rank_categories(self, categories: List[str]) -> List[str]:
        """Priority-weight ranking over registry categories (was _score_categories)."""
        weights: Dict[str, float] = {}
        for category in categories:
            try:
                worms = get_worms_by_category(category)
            except Exception as exc:
                note_failure("METACLUSTER:L659", exc)
                worms = {}
            weights[category] = sum(
                float((info or {}).get("priority", 0)) for info in worms.values())
        return sorted(categories, key=lambda c: weights.get(c, 0.0), reverse=True)

    @staticmethod
    def _reaches_cycle(nature: str, feeds: Dict[str, List[str]]) -> bool:
        """Iterative DFS: does any path out of ``nature`` revisit a node on it?"""
        on_path: Set[str] = set()
        # (node, is_exit) — children are pushed ahead of the exit marker, so a
        # node only leaves the path once its whole subtree has been walked.
        stack: List[tuple] = [(nature, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                on_path.discard(node)
                continue
            if node in on_path:
                return True
            on_path.add(node)
            stack.append((node, True))
            for downstream in feeds.get(node) or []:
                stack.append((downstream, False))
        return False

    def _is_circular_category(self, category: str, feeds: Optional[Dict[str, List[str]]] = None) -> bool:
        """True when the category holds the orchestration classes themselves.

        Executing those recurses or deadlocks. Matched by module path rather
        than identity so METACLUSTER does not import the orchestrators back,
        which would close an import cycle. (Was _contains_orchestration_
        infrastructure over NCM clusters; now over the flat registry.)

        With `feeds`, reachability over that graph (DFS cycle detection).
        """
        if feeds is not None:
            return self._reaches_cycle(category, feeds)
        try:
            worms = get_worms_by_category(category)
        except Exception as exc:
            note_failure("METACLUSTER:L706", exc)
            return False
        orchestrators = {
            "MetaCluster": "METACLUSTER",
            "ClusterWORMZ": "CLUSTERWORMS",
            "ErrorRecoveryOrchestrator": "ERROR_RECOVERY",
        }
        for name, info in worms.items():
            module = str((info or {}).get("module", ""))
            for cls_name, owner in orchestrators.items():
                if name == cls_name or cls_name in module:
                    if owner in module.upper():
                        return True
        return "orchestration" in str(category).lower() and any(
            n in str(worms) for n in ("MetaCluster", "ClusterWORMZ"))

    def orchestrate_category(self, category: str, parallel: bool = False) -> Optional[MetaClusterResult]:
        """Orchestrate a single Nature (convenience method)."""
        return self.orchestrate(categories=[category], parallel=parallel)

    def orchestrate_adaptive(self, max_categories: int = 5,
                             parallel: bool = False) -> MetaClusterResult:
        """
        Adaptive orchestration: automatically select and prioritize Natures
        based on system state, recent execution history, and diversity engine.
        """
        return self.orchestrate(max_categories=max_categories, parallel=parallel)

    def orchestrate_full_pipeline(self, start_category: str,
                                  parallel: bool = False) -> MetaClusterResult:
        """
        Execute one category end-to-end (U3: was follow-the-routing-graph
        from a start Nature; no graph remains, so this runs the single
        requested category like orchestrate (legacy name)).
        """
        return self.orchestrate(categories=[start_category], parallel=parallel)
    
    # ── Insights & Recommendations ─────────────────────────────────
    
    def _generate_insights(self, category_results: Dict[str, Any],
                           state: MetaClusterState) -> List[str]:
        """Generate human-readable insights from execution results."""
        insights = []
        
        total_ok = sum(r.get("success_count", 0) for r in category_results.values())
        total_fail = sum(r.get("failure_count", 0) for r in category_results.values())
        total_worms = total_ok + total_fail
        success_rate = (total_ok / max(total_worms, 1)) * 100
        
        if total_worms > 0:
            insights.append(f"Overall success rate: {success_rate:.0f}% ({total_ok}/{total_worms} worms)")
        
        # Per-category insights
        for category, result in sorted(category_results.items()):
            n_ok = result.get("success_count", 0)
            n_fail = result.get("failure_count", 0)
            n_total = n_ok + n_fail
            
            if n_fail > 0:
                insights.append(f"⚠️ {category}: {n_fail}/{n_total} worms failed")
            elif n_total > 0:
                insights.append(f"OK {category}: all {n_total} worms succeeded")
        
        return insights

    def _generate_recommendations(self, category_results: Dict[str, Any],
                                  state: MetaClusterState) -> List[str]:
        """Generate actionable recommendations from results."""
        recommendations = []
        
        # Failed categories
        for category, result in category_results.items():
            if result.get("failure_count", 0) > 0:
                recommendations.append(f"Investigate failures in '{category}' category cluster")
        
        # Unused categories
        try:
            all_categories = set(get_all_categories())
        except Exception as exc:
            note_failure("METACLUSTER:L784", exc)
            all_categories = set()
        executed = set(category_results.keys())
        unused = all_categories - executed
        if unused:
            recommendations.append(f"Consider running unused categories: {', '.join(sorted(unused)[:5])}")
        
        return recommendations
    
    # ── Status ─────────────────────────────────────────────────────
    
    def get_status(self) -> Dict[str, Any]:
        """Get full MetaCluster status."""
        try:
            categories = sorted(get_all_categories())
        except Exception as exc:
            note_failure("METACLUSTER:L799", exc)
            categories = []
        return {
            "version": self.VERSION,
            "categories": len(categories),
            "ncm": {"categories": len(categories)},
            "routing_paths": 0,
            "cross_category_routes": 0,
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
        all registry categories and their worms (U3: no routing overlay).
        """
        try:
            categories = sorted(get_all_categories())
        except Exception as exc:
            note_failure("METACLUSTER:L827", exc)
            return "graph TB\n    error[\"registry unavailable\"]"
        lines = ["graph TB"]
        for category in categories:
            try:
                worms = sorted(get_worms_by_category(category))
            except Exception as exc:
                note_failure("METACLUSTER:L833", exc)
                worms = []
            lines.append(f'    {category}["{category}<br/>{len(worms)} worms"]')
            for worm in worms[:20]:
                lines.append(f'    {category} --> {worm}')
        return "\n".join(lines)

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
        
        # Nodes for each category
        for category, n_result in result.category_results.items():
            ok = n_result.get("success_count", 0)
            fail = n_result.get("failure_count", 0)
            status = "ok" if fail == 0 else "fail"
            color = "#00ff88" if status == "ok" else "#ff4444"
            
            duration = n_result.get('duration_ms', 0)
            lines.append(f'    {category}["{category}<br/>{ok}OK {fail}FAIL ({duration:.0f}ms)"]')
            lines.append(f"    style {category} fill:#16213e,stroke:{color},color:{color}")
        
        return "\n".join(lines)

    def to_mermaid_routing_topology(self) -> str:
        """
        Routing topology (U3: flat dispatch declares no routes).
        Kept so dashboard callers keep working; reports the fact.
        """
        return ("graph TB\n"
                '    title["Flat dispatch — no routing topology"]\n'
                "    style title fill:#1a1a2e,stroke:#00d9ff,color:#00d9ff")

    def to_mermaid_all_functions(self) -> str:
        """
        Generate Mermaid class diagram showing all worm classes and
        their registry categories (U3: flat, ex taxonomy).
        """
        try:
            categories = sorted(get_all_categories())
        except Exception as exc:
            note_failure("METACLUSTER:L883", exc)
            return "classDiagram\n    %% No data"
        lines = ["classDiagram"]
        for category in categories:
            try:
                worms = sorted(get_worms_by_category(category))
            except Exception as exc:
                note_failure("METACLUSTER:L889", exc)
                worms = []
            for worm in worms[:30]:
                lines.append(f"    {category} --> {worm}")
        return "\n".join(lines)
    
    # ── Persistence ────────────────────────────────────────────────
    
    def export_state(self, filepath: str = "") -> str:
        """Export full MetaCluster state to JSON."""
        if not filepath:
            filepath = str(_get_project_root_safe() / "data" / "metacluster_state.json")
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        export = {
            "version": self.VERSION,
            "exported_at": datetime.now().isoformat(),
            "free_threaded": is_free_threaded(),
            "categories": sorted(get_all_categories()) if _REGISTRY_OK else [],
            "routes": [],
            "orchestration_history": [
                {
                    "id": r.orchestration_id,
                    "categories": r.categories_executed,
                    "ok": r.total_worms_ok,
                    "fail": r.total_worms_fail,
                    "duration_ms": r.duration_ms,
                    "insights": r.insights,
                    "recommendations": r.recommendations,
                }
                for r in self._history[-20:]
            ],
        }
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(export, f, indent=2, default=str)
        
        _record_event("metacluster_exported", {"filepath": filepath})
        return filepath

    def __repr__(self):
        n_categories = len(self.categories)
        history = len(self._history)
        return f"MetaCluster(v{self.VERSION}, {n_categories} categories, flat, {history} runs)"


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
        except Exception as exc:
            note_failure("METACLUSTER:L950", exc)


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    """CLI entry point."""
    
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
        categories_arg = args.orchestrate if args.orchestrate else None
        result = mc.orchestrate(categories=categories_arg, parallel=args.parallel)
        print(f"Orchestrated {result.categories_executed} Categories: {result.total_worms_ok}OK/{result.total_worms_fail}FAIL ({result.duration_ms:.0f}ms)")
        for insight in result.insights:
            print(f"  {insight}")
        for rec in result.recommendations:
            print(f"  {rec}")
    
    elif args.adaptive > 0:
        result = mc.orchestrate_adaptive(max_categories=args.adaptive, parallel=args.parallel)
        print(f"Adaptive: {result.categories_executed} Categories, {result.total_worms_ok}OK/{result.total_worms_fail}FAIL ({result.duration_ms:.0f}ms)")
    
    elif args.pipeline:
        result = mc.orchestrate_full_pipeline(args.pipeline, parallel=args.parallel)
        print(f"Pipeline [{args.pipeline}]: {result.categories_executed} Categories, {result.total_worms_ok}OK/{result.total_worms_fail}FAIL")
        for path in result.cross_category_paths_used:
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
