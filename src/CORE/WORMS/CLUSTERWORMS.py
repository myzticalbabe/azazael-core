# coding: utf-8
"""
CLUSTERWORMS - Coordinated Worm Collectives

Tier 2: Coordinated groups of worms working in unison.

Features:
- Worm coordination with shared state
- Inter-worm messaging
- Cluster lifecycle management
- Result aggregation
- Cluster persistence
- Dynamic worm resolution from WORM_REGISTRY (replaces hardcoded if/elif)
- PAMS memory recording of every cluster action
- MemoryBus publishing for inter-agent communication
- Mermaid graph generation for cluster visualization
- Category-based cluster auto-building from registry categories

Usage:
    >>> from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
    >>> cluster = ClusterWORMZ("system")
    >>> cluster.add_worm("SYSTEMZ", config={})
    >>> cluster.add_worm("PROCESSZ", config={})
    >>> results = cluster.execute_all()
    >>> aggregated = cluster.aggregate_results()

    # Dynamic category-based clusters:
    >>> cluster = ClusterWORMZ.build_from_category("security")
    >>> print(cluster.to_mermaid())
"""

import sys
import time
import inspect
import threading
import warnings
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable, Set
from dataclasses import dataclass, field
from collections import defaultdict
import argparse
import time as _t

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).
_SWALLOWED: List[str] = []

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        _SWALLOWED.append("%s:no-recorder" % where)


def _note_failure(where: str, exc: BaseException | None = None) -> None:
    """Record a fail-open swallow. Never raises."""
    try:
        note_failure(where, exc)
    except Exception as _e:
        _SWALLOWED.append("%s:%s" % (where, type(_e).__name__))


#: DirectMemory hook for cluster execution boundaries (2026-09-28).
#: Tests replace `_dm` with a stub; production wiring may set a recorder.
#: None by default: boundary events are opt-in, never import-time I/O.
#:
#: RLock, not Lock: `execute_all()` takes this lock to snapshot `_dm`, and
#: callers are ALLOWED to already hold it -- tests/test_cluster_integration_p1.py
#: swaps the stub under `with _dm_lock:` and then calls `execute_all()`. With a
#: plain Lock that re-acquisition is a same-thread self-deadlock that never
#: returns, which is what made suite chunk 2 run to its wall-clock cap.
#: RLock preserves cross-thread exclusion (the property the lock exists for)
#: while making the documented re-entrant call legal.
_dm = None
_dm_lock = threading.RLock()


#: Mid-flight clarification channel (2026-09-28). The executor must NORMALISE a
#: worm's request for owner input into an outcome row, never let it escape as an
#: exception: a raised NeedsClarification caught by the generic handler would be
#: reported as a plain worm crash, losing both the question and the decision_id
#: that ties it to the choice log. Guarded import so a missing delegation module
#: degrades to "no channel" rather than breaking every cluster run.
try:
    from CORE.WORMS.expansion.delegation import (
        NeedsClarification as _NeedsClarification,  # pyright: ignore[reportAssignmentType] -- except-path defines a structurally distinct local Exception subclass behind the same name; selected by import success
        ClarificationTimeout as _ClarificationTimeout,  # pyright: ignore[reportAssignmentType] -- same shape as _NeedsClarification above
    )
except Exception as exc:  # noqa: BLE001 -- optional channel
    _note_failure("clusterworms:delegation-import", exc)
    class _NeedsClarification(Exception):  # type: ignore[no-redef]
        """Fallback so the `except` clauses below stay valid when absent."""

        decision_id = None
        question = ""

    class _ClarificationTimeout(Exception):  # type: ignore[no-redef]
        """Fallback so the `except` clauses below stay valid when absent."""

        decision_id = None
        question = ""
        waited_s = 0.0


class _WatchdogThread:
    """Timeout watchdog for hung worm futures (2026-09-28).

    Tracks (future, name, deadline) entries on a poll loop; a future past
    its deadline gets ``cancel()`` called and its name reported. Completion
    is ``done() is True`` (identity, not truthiness) so foreign future
    stand-ins are never mistaken for finished work. Never raises out of
    the poll loop; ``stop()`` returns timed-out names.
    """

    def __init__(self, poll_interval: float = 1.0):
        self._poll_interval = max(0.01, float(poll_interval))
        self._futures: dict = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._timeouts: list = []

    def track(self, future, name: str, timeout: float) -> None:
        with self._lock:
            self._futures[future] = (str(name), _t.monotonic() + float(timeout))

    def untrack(self, future) -> None:
        with self._lock:
            self._futures.pop(future, None)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self) -> list:
        self._running = False
        th, self._thread = self._thread, None
        if th is not None and th is not threading.current_thread():
            th.join(timeout=self._poll_interval * 4)
        with self._lock:
            out = list(self._timeouts)
        return out

    def _poll(self) -> None:
        while self._running:
            try:
                now = _t.monotonic()
                with self._lock:
                    items = list(self._futures.items())
                for fut, (name, deadline) in items:
                    try:
                        done = fut.done()
                    except Exception as exc:              # noqa: BLE001 - was silent
                        _note_failure("clusterworms:future-done-probe", exc)
                        done = False
                        if done is True:
                            with self._lock:
                                self._futures.pop(fut, None)
                            continue
                    if now >= deadline:
                        try:
                            fut.cancel()
                        except Exception as exc:              # noqa: BLE001 - was silent
                            _note_failure("clusterworms:future-cancel", exc)
                            pass
                        with self._lock:
                            self._futures.pop(fut, None)
                            if name not in self._timeouts:
                                self._timeouts.append(name)
            except Exception as exc:                      # noqa: BLE001 - was silent
                _note_failure("clusterworms:poll-loop", exc)
                pass
            _t.sleep(self._poll_interval)

# ── Worm Registry ─────────────────────────────────────────────────
try:
    from CORE.worm_registry import (
        register_worm, WORM_REGISTRY, get_worms_by_category,  # pyright: ignore[reportAssignmentType] -- except-path fabricates a decorator fallback with an unrelated signature; selected by import success
        get_all_categories, discover_worms, mark_spawned,  # pyright: ignore[reportAssignmentType] -- fallback mark_spawned uses param name and return type differing from the real one; selected by import success
    )
    _REGISTRY_OK = True
except ImportError as exc:                            # noqa: BLE001 - was silent
    _note_failure("clusterworms:registry-import", exc)
    _REGISTRY_OK = False
    WORM_REGISTRY = {}
    def get_worms_by_category(category: str) -> Dict[str, Dict[Any, Any]]: return {}
    def get_all_categories() -> Set[str]: return set()
    def discover_worms(worms_dir: Optional[str] = None, progress_callback: Optional[Callable] = None) -> int: return 0
    def mark_spawned(name: str) -> Any: pass
    def register_worm(category: str = "", priority: int = 0, cooldown_seconds: float = 0, description: str = "", tags: Optional[set[str]] = None, **kwargs: Any) -> Callable[..., Any]: return lambda f: f


# ── PAMS Memory Recording (module-level, thread-safe) ────────────
# NOTE: Module-level imports (not lazy in function) to avoid thread-safety
# issues under Python 3.14t free-threading (no GIL). Importing modules
# from background threads can deadlock when the import system isn't
# re-entrant. These module-level imports happen at import time on the
# main thread, so _get_pams() / _get_mem_bus() are simple lookups.
try:
    from CORE.PamsRecorder import get_recorder as _get_recorder  # singleton accessor (PamsRecorder.py:112-125)
    _PAMS_OK = True
except ImportError as exc:                            # noqa: BLE001 - was silent
    _note_failure("clusterworms:pams-import", exc)
    _PAMS_OK = False
    _get_recorder = None

try:
    from CORE.MemoryBus import MemoryBus as _MemoryBusCls
    _MEMBUS_OK = True
except ImportError as exc:                            # noqa: BLE001 - was silent
    _note_failure("clusterworms:bus-import", exc)
    _MEMBUS_OK = False
    _MemoryBusCls = None

_pams: Any = None
_pams_lock = threading.Lock()
_mem_bus: Any = None
_mem_bus_lock = threading.Lock()


def _get_pams():
    """Lazy PAMS recorder — double-checked locking for Python 3.14t free-threading."""
    global _pams
    if _pams is None:
        if not _PAMS_OK:
            _pams = False
            return None
        with _pams_lock:
            if _pams is None:
                try:
                    _pams = _get_recorder()  # pyright: ignore[reportOptionalCall] -- reachable only when _PAMS_OK proved the import succeeded; the None fallback limb never reaches here
                except Exception as exc:  # noqa: BLE001 — optional recorder; fall back to None
                    _note_failure("clusterworms:pams-construct", exc)
                    _pams = False
    return _pams if _pams else None
def _get_mem_bus():
    """Lazy MemoryBus — double-checked locking for Python 3.14t free-threading."""
    global _mem_bus
    if _mem_bus is None:
        if not _MEMBUS_OK:
            _mem_bus = False
            return None
        with _mem_bus_lock:
            if _mem_bus is None:
                try:
                    _mem_bus = _MemoryBusCls()  # pyright: ignore[reportOptionalCall] -- reachable only when _MEMBUS_OK proved the import succeeded; None limb unreachable
                except Exception as exc:  # noqa: BLE001 — optional bus; fall back to None
                    _note_failure("clusterworms:bus-construct", exc)
                    _mem_bus = False
    return _mem_bus if _mem_bus else None


# ── Free-Threading ────────────────────────────────────────────────
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, CancellationToken, TaskCancelledError  # pyright: ignore[reportAssignmentType] -- optional import; except-path binds None / a local Exception subclass behind the same names; selected by import success
    from concurrent.futures import as_completed
    _FREETHREAD_OK = True
except ImportError as exc:                            # noqa: BLE001 - was silent
    _note_failure("clusterworms:freethreading-import", exc)
    _FREETHREAD_OK = False
    FreeThreadPool = None
    CancellationToken = None
    class TaskCancelledError(Exception): pass

    def is_free_threaded() -> bool:
        try:
            return not sys._is_gil_enabled()
        except Exception as exc:  # noqa: BLE001 — 314t detection best-effort
            _note_failure("clusterworms:gil-check", exc)
            return False

    def as_completed(fs, timeout=None):
        from concurrent.futures import as_completed as _ac
        return _ac(fs, timeout=timeout)

# ── Circuit Breaker ────────────────────────────────────────────────
# NOTE: Module-level imports (not lazy in function) to avoid thread-safety
# issues under Python 3.14t free-threading (no GIL). Importing modules
# from background threads can deadlock when the import system isn't
# re-entrant. These module-level imports happen at import time on the
# main thread, so _get_breaker_registry() is a simple lookup.
_cfg = None
try:
    from CORE.circuit_breaker import BreakerRegistry as _BreakerRegistryCls
    from CORE.config import get_config as _get_config
except ImportError as exc:                            # noqa: BLE001 - was silent
    _note_failure("clusterworms:breaker-import", exc)
    _BreakerRegistryCls = None
    _get_config = None

_breaker_registry: Any = None
_breaker_lock = threading.Lock()
def _get_breaker_registry():
    """Get or create the circuit breaker registry — double-checked locking for Python 3.14t."""
    global _breaker_registry, _cfg
    if _breaker_registry is None:
        if _BreakerRegistryCls is None:
            _breaker_registry = False
            return None
        with _breaker_lock:
            if _breaker_registry is None:
                try:
                    _cfg = _get_config()  # pyright: ignore[reportOptionalCall] -- reachable only when the breaker import succeeded (registry cls non-None); None limb unreachable
                    _breaker_registry = _BreakerRegistryCls()
                except Exception as exc:  # noqa: BLE001 — optional breaker; fall back to None
                    _note_failure("clusterworms:breaker-construct", exc)
                    _breaker_registry = False
    return _breaker_registry if _breaker_registry else None

# ── Legacy Worm Module Pre-imports (lazy, deferred) ──────────────
# NOTE: These imports are DEFERRED to avoid circular import deadlock
# under Python 3.14t free-threading. CORE/WORMS/__init__.py imports
# CLUSTERWORMS at package load time. If we pre-import legacy worm
# modules at module level, we are trying to __import__() submodules
# of CORE.WORMS while CORE.WORMS is still partially initialized.
# The import system handles this on CPython 3.12, but under 3.14t
# free-threading it can deadlock the import lock.
#
# _ensure_legacy_modules() is called lazily on first worm execution,
# by which time CORE.WORMS is fully loaded.
_LEGACY_MODULE_NAMES = [
    "SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ", "CLOCKZ", "WORMZ",
    "TRIMZ", "HEALTHZ", "OPTIMIZ", "ERRORZ", "METRICZ", "METAZ",
    "DEPENDZ", "VALIDZ", "DEADZ", "DOCKERZ", "LOGZ", "CHANGEZ", "TELEMETRYZ",
]
_LEGACY_MODULES_LOADED = False
def _ensure_legacy_modules():
    """Lazily pre-import legacy worm modules on first use.

    Called from _legacy_execute() before resolution to ensure modules
    are in sys.modules before worker threads access them. This is safe
    because it runs on the main thread before entering the thread pool.
    """
    global _LEGACY_MODULES_LOADED, _LEGACY_DISPATCH
    if _LEGACY_MODULES_LOADED:
        return

    for _mod_name in _LEGACY_MODULE_NAMES:
        try:
            __import__(_mod_name)  # bare import (works for src/ root modules)
        except ImportError as exc:                    # noqa: BLE001 - was silent
            _note_failure("clusterworms:legacy-module-import", exc)
            try:
                __import__(f"CORE.WORMS.{_mod_name}", fromlist=[_mod_name])  # full path
            except ImportError as exc2:               # noqa: BLE001 - was silent
                _note_failure("clusterworms:legacy-module-import-full", exc2)
                pass  # not available -- will fall through to registry

    # Rebuild _LEGACY_DISPATCH now that modules are imported
    # Use .clear() + .update() to mutate in-place — preserves references
    # held by test modules that import _LEGACY_DISPATCH directly.
    _LEGACY_DISPATCH.clear()
    _LEGACY_DISPATCH.update({
        name: (lambda n=name: _legacy_execute(n))
        for name in _LEGACY_MODULE_NAMES
    })
    _LEGACY_MODULES_LOADED = True
def _legacy_execute(name: str):
    """Execute a worm via legacy dispatch using thread-safe sys.modules lookup.

    Ensures legacy modules are pre-imported before resolution (lazy init
    to avoid circular import deadlock under Python 3.14t free-threading).

    Unlike the old __import__()-based lambdas, this function reads from
    sys.modules (populated by _ensure_legacy_modules() or by
    discover_worms() on the main thread). This is safe to call from
    background threads under Python 3.14t free-threading.
    """
    _ensure_legacy_modules()
    call_map = {
        "SYSTEMZ": lambda m: m.SystemZ().get_status().__dict__,
        "PROCESSZ": lambda m: m.PROCESSZ().get_summary(),
        "DISKZ": lambda m: m.DISKZ().get_summary(),
        "NETWORXZ": lambda m: m.NETWORXZ().get_summary(),
        "CLOCKZ": lambda m: m.CLOCKZ().get_uptime(),
        "WORMZ": lambda m: m.WORMZ().run().__dict__,
        "TRIMZ": lambda m: (lambda w: (w.scan_all(show_progress=False), {"files_scanned": len(w.results)})[1])(m.TRIMZ()),
        "HEALTHZ": lambda m: m.HEALTHZ().check().__dict__,
        "OPTIMIZ": lambda m: m.OPTIMIZ().analyze().__dict__,
        "ERRORZ": lambda m: m.ERRORZ().run().__dict__,
        "METRICZ": lambda m: m.METRICZ().run().__dict__,
        "METAZ": lambda m: m.METAZ().run().__dict__,
        "DEPENDZ": lambda m: m.DEPENDZ().run().__dict__,
        "VALIDZ": lambda m: m.VALIDZ().run().__dict__,
        "DEADZ": lambda m: m.DEADZ().run().__dict__,
        "DOCKERZ": lambda m: m.DOCKERZ().get_status().__dict__,
        "LOGZ": lambda m: m.LOGZ().run().__dict__,
        "CHANGEZ": lambda m: m.CHANGEZ().run().__dict__,
        "TELEMETRYZ": lambda m: m.TELEMETRYZ().get_status().__dict__,
    }
    if name not in call_map:
        return None
    # Check both bare name and full path in sys.modules
    for mod_name in [name, f"CORE.WORMS.{name}"]:
        mod = sys.modules.get(mod_name)
        if mod:
            return call_map[name](mod)
    return None

# _LEGACY_DISPATCH is rebuilt inside _ensure_legacy_modules() on first
# call to avoid circular import deadlock under Python 3.14t free-threading.

# Note: tkinter imported lazily inside run_popup()
def _discover_progress(processed: int, total: int, module_name: str):
    """Progress callback for discover_worms() — prints to stderr."""
    print(f"\r  [discover] {processed}/{total} - {module_name}", file=sys.stderr, end="", flush=True)


# ═══════════════════════════════════════════════════════════════════════
# DATA TYPES
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ClusterResult:
    """Result from cluster execution."""

    cluster_id: str
    worm_results: Dict[str, Any]
    aggregated_metrics: Dict[str, float]
    duration_ms: float
    success_count: int
    failure_count: int
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    partial_failure_threshold: float = 1.0
    retired_skipped: List[str] = field(default_factory=list)
    quarantined_skipped: List[str] = field(default_factory=list)

    @property
    def overall_status(self) -> str:
        """success / partial_success / failure from the counts.

        A run where everything succeeded is success; anything above the
        partial-failure threshold with some success is partial_success;
        otherwise failure (including the empty run: nothing succeeded).
        """
        total = (self.success_count or 0) + (self.failure_count or 0)
        if total <= 0:
            return "failure"
        try:
            thr = float(self.partial_failure_threshold)
        except (TypeError, ValueError) as exc:         # noqa: BLE001 - was silent
            _note_failure("clusterworms:result-threshold-parse", exc)
            thr = 1.0
        rate = self.success_count / total
        if rate >= thr:
            return "success"
        if self.success_count > 0:
            return "partial_success"
        return "failure"


@dataclass
class WormConfig:
    """Configuration for a worm in cluster."""

    name: str
    module: str
    enabled: bool = True
    priority: int = 0
    timeout: int = 30
    retry: int = 1
    params: Dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
# CLUSTER REGISTRY + DYNAMIC RESOLUTION
# ═══════════════════════════════════════════════════════════════════════

CLUSTER_REGISTRY = {
    "bibl": {
        "description": "BIBL crawling and trimming",
        "worms": ["WORMZ", "TRIMZ"],
    },
    "system": {
        "description": "Full system monitoring",
        "worms": ["SYSTEMZ", "PROCESSZ", "DISKZ"],
    },
    "network": {
        "description": "Network and time management",
        "worms": ["NETWORXZ", "CLOCKZ", "DISKZ"],
    },
    "security": {
        "description": "Security auditing",
        "worms": ["PROCESSZ", "NETWORXZ", "SYSTEMZ"],
    },
    "analysis": {
        "description": "Code analysis and metrics",
        "worms": ["METAZ", "METRICZ", "DEPENDZ"],
    },
    "validation": {
        "description": "Code validation and error checking",
        "worms": ["VALIDZ", "DEADZ", "ERRORZ"],
    },
    "health": {
        "description": "System health monitoring",
        "worms": ["HEALTHZ", "OPTIMIZ", "SYSTEMZ"],
    },
    "full": {
        "description": "Complete ecosystem scan",
        "worms": ["SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ", "WORMZ"],
    },
}

# Known worm-to-module mapping for backward-compatible dynamic resolution
_WORM_MODULE_MAP = {
    "SYSTEMZ": ("SYSTEMZ", "SystemZ"),
    "PROCESSZ": ("PROCESSZ", "PROCESSZ"),
    "DISKZ": ("DISKZ", "DISKZ"),
    "NETWORXZ": ("NETWORXZ", "NETWORXZ"),
    "CLOCKZ": ("CLOCKZ", "CLOCKZ"),
    "WORMZ": ("WORMZ", "WORMZ"),
    "METAZ": ("METAZ", "METAZ"),
    "METRICZ": ("METRICZ", "METRICZ"),
    "DEPENDZ": ("DEPENDZ", "DEPENDZ"),
    "VALIDZ": ("VALIDZ", "VALIDZ"),
    "DEADZ": ("DEADZ", "DEADZ"),
    "ERRORZ": ("ERRORZ", "ERRORZ"),
    "TRIMZ": ("TRIMZ", "TRIMZ"),
    "OPTIMIZ": ("OPTIMIZ", "OPTIMIZ"),
    "HEALTHZ": ("HEALTHZ", "HEALTHZ"),
    "LOGZ": ("LOGZ", "LOGZ"),
    "CHANGEZ": ("CHANGEZ", "CHANGEZ"),
    "DOCKERZ": ("DOCKERZ", "DOCKERZ"),
    "TELEMETRYZ": ("TELEMETRYZ", "TELEMETRYZ"),
}

# Standard method names to try on worm instances, in priority order
_STANDARD_METHODS = [
    "get_summary", "get_status", "run", "execute", "scan",
    "check", "analyze", "audit", "collect", "probe", "profile",
    "inspect", "query", "fetch", "start", "monitor",
    "report", "validate", "detect", "measure", "track", "fix",
    "heal", "test", "verify", "publish", "subscribe", "deliver",
    "negotiate", "orchestrate", "handle_request", "register_model",
    "register_schema", "register_peer", "register_method",
    "check_latest", "check_usage", "check_outdated", "check_package",
    "compare_versions", "compile_html", "crawl_all", "predictive_crawl",
    "train_model", "train_episode", "train_from_memory",
    "get_uptime", "get_metrics", "get_health", "get_report",
    "get_stats", "get_info", "get_data", "get_config",
]

# Legacy hardcoded dispatch — used as fallback when dynamic resolution fails
# NOTE: Uses _legacy_execute() helper which does sys.modules lookup instead of
# __import__() — this is thread-safe under Python 3.14t free-threading.
# The actual lambdas are rebuilt below after _legacy_execute() is defined.
_LEGACY_DISPATCH: Dict[str, Callable] = {}  # populated below
def _resolve_worm_dynamic(module_name: str) -> Optional[Dict[str, Any]]:
    """
    Resolve and execute a worm dynamically using the WORM_REGISTRY.
    
    Strategy (in priority order):
    1. Look up the class from WORM_REGISTRY and instantiate it
    2. Try to find and call a standard method
    3. Fall back to legacy hardcoded dispatch
    
    Args:
        module_name: The worm class name (e.g., "SYSTEMZ", "PortAuditor")
    
    Returns:
        Dict with result data, or None if resolution failed
    """
    # Strategy 1: Registry-based resolution
    if _REGISTRY_OK:
        # Pre-discovery is done on the main thread in execute_all()
        # before entering the thread pool. Calling discover_worms() here
        # is a fast no-op if the registry is already populated.
        discover_worms()
        
        # Direct lookup in registry
        reg_info = WORM_REGISTRY.get(module_name)
        if reg_info:
            cls = reg_info.get("class")
            if cls and callable(cls):
                return _execute_worm_class(module_name, cls)
        
        # Search all categories for the class name
        for category in get_all_categories():
            cat_worms = get_worms_by_category(category)
            cat_info = cat_worms.get(module_name)
            if cat_info:
                cls = cat_info.get("class")
                if cls and callable(cls):
                    return _execute_worm_class(module_name, cls)
    
    # Strategy 2: Legacy hardcoded dispatch (backward compatibility)
    if module_name in _LEGACY_DISPATCH:
        try:
            result = _LEGACY_DISPATCH[module_name]()
            return {"success": True, "method": "legacy_dispatch", "result": result}
        except Exception as e:  # noqa: BLE001 — dispatch failure reports, never raises
            _note_failure("clusterworms:legacy-dispatch", e)
            return {"success": False, "error": str(e), "method": "legacy_dispatch"}
    
    # Strategy 3: Try to import and find the class by convention
    # Uses sys.modules first (thread-safe, populated by _ensure_legacy_modules()
    # on main thread before pool). Falls back to __import__ with fromlist
    # for correct leaf-module resolution on dotted names like
    # ("CORE.WORMS.SYSTEMZ", "SystemZ").
    if module_name in _WORM_MODULE_MAP:
        mod_name, cls_name = _WORM_MODULE_MAP[module_name]
        try:
            mod = sys.modules.get(mod_name) or __import__(mod_name, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            return _execute_worm_class(module_name, cls)
        except Exception as exc:  # noqa: BLE001 — fall through to next resolution strategy
            _note_failure("clusterworms:module-map-resolution", exc)
            pass
    
    return None
def _clarification_outcome(exc: BaseException, method_name: str) -> Optional[Dict[str, Any]]:
    """needs_input outcome for a raised clarification, else None."""
    if type(exc).__name__ != "NeedsClarification":
        return None
    return {
        "success": False,
        "needs_input": str(getattr(exc, "question", exc))[:800],
        "decision_id": getattr(exc, "decision_id", None),
        "method": method_name,
    }


def _execute_worm_class(name: str, cls: Callable[..., Any]) -> Dict[str, Any]:
    """
    Instantiate a worm class and call its primary method.

    Tries standard methods in priority order, skipping those that
    require arguments (using inspect.signature). Converts dataclass
    results to dicts automatically. Falls back to scanning ALL
    public parameterless methods if no standard method works.

    Ask-owner boundary (U3, was ClusterWORMZ._run_worm_method): a raised
    clarification (duck-typed: question + decision_id) normalises to a
    needs_input outcome instead of a crash, preserving the decision_id
    for the rendezvous. Matched by shape, not import, so this module
    never hard-depends on the delegation machinery.
    """
    try:
        instance = cls()
    except Exception as e:  # noqa: BLE001 — instantiation failure reports, never raises
        _note_failure("clusterworms:worm-instantiate", e)
        return {"success": False, "error": f"Instantiation failed: {str(e)}"}

    last_error = None
    last_error_method = None

    # Step 1: Try standard methods, skipping those requiring arguments
    for method_name in _STANDARD_METHODS:
        if hasattr(instance, method_name) and callable(getattr(instance, method_name)):
            method = getattr(instance, method_name)
            try:
                sig = inspect.signature(method)
                req_args = [
                    p.name for p in sig.parameters.values()
                    if p.default == inspect.Parameter.empty
                    and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                ]
                if req_args:
                    last_error = f"Missing required arguments: {req_args}"
                    last_error_method = method_name
                    continue
            except ValueError as exc:                 # noqa: BLE001 - was silent
                _note_failure("clusterworms:signature-inspect", exc)
                pass

            try:
                result = method()
                if hasattr(result, "__dict__") and not isinstance(result, dict):
                    result = result.__dict__
                return {"success": True, "method": method_name, "result": result}
            except Exception as e:  # noqa: BLE001 — method failure reports, tries fallback
                _note_failure("clusterworms:standard-method", e)
                clarified = _clarification_outcome(e, method_name)
                if clarified is not None:
                    return clarified
                return {"success": False, "error": str(e), "method": method_name}

    # Step 2: Fallback to ANY parameterless public method
    for method_name in dir(instance):
        if method_name.startswith('_'):
            continue
        method = getattr(instance, method_name)
        if not callable(method):
            continue
        try:
            sig = inspect.signature(method)
            req_args = [
                p.name for p in sig.parameters.values()
                if p.default == inspect.Parameter.empty
                and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            ]
            if req_args:
                continue
            result = method()
            if hasattr(result, "__dict__") and not isinstance(result, dict):
                result = result.__dict__
            return {"success": True, "method": method_name, "result": result}
        except Exception as e:  # noqa: BLE001 — try next standard method
            _note_failure("clusterworms:fallback-method", e)
            clarified = _clarification_outcome(e, method_name)
            if clarified is not None:
                return clarified
            continue

    if last_error:
        return {"success": False, "error": last_error, "method": last_error_method}

    return {"success": False, "error": f"No parameterless method found on {name}"}


# ═══════════════════════════════════════════════════════════════════════
# CLUSTER STATE
# ═══════════════════════════════════════════════════════════════════════

class ClusterState:
    """Shared state for cluster execution."""

    def __init__(self, cluster_id: str):
        self.cluster_id = cluster_id
        self.results: Dict[str, Any] = {}
        self.metrics: Dict[str, list] = defaultdict(list)
        self.messages: List[Dict] = []
        self.last_sync = datetime.now()
        self.lock = threading.Lock()

    def add_result(self, worm_name: str, result: Any):
        """Add result from worm."""
        with self.lock:
            self.results[worm_name] = result
            self.last_sync = datetime.now()

    def send_message(self, from_worm: str, to_worm: str, data: Any):
        """Send message between worms."""
        with self.lock:
            self.messages.append(
                {
                    "from": from_worm,
                    "to": to_worm,
                    "data": data,
                    "timestamp": datetime.now().isoformat(),
                }
            )

    def broadcast(self, data: Any, exclude: Optional[List[str]] = None):
        """Broadcast message to all worms."""
        with self.lock:
            for worm in self.results.keys():
                if exclude and worm in exclude:
                    continue
                self.messages.append(
                    {
                        "from": "BROADCAST",
                        "to": worm,
                        "data": data,
                        "timestamp": datetime.now().isoformat(),
                    }
                )

    def get_messages(self, worm_name: str) -> List[Dict]:
        """Get messages for worm."""
        with self.lock:
            return [
                m
                for m in self.messages
                if m["to"] == worm_name or m["to"] == "BROADCAST"
            ]

    def clear_messages(self, worm_name: Optional[str] = None):
        """Clear processed messages."""
        with self.lock:
            if worm_name:
                self.messages = [m for m in self.messages if m["to"] != worm_name]
            else:
                self.messages = []


# ═══════════════════════════════════════════════════════════════════════
# WORM EXECUTOR — Dynamic Resolution
# ═══════════════════════════════════════════════════════════════════════

class WormExecutor:
    """
    Executes a single worm in cluster context.
    
    Uses dynamic registry-based resolution instead of hardcoded if/elif.
    Falls back to legacy dispatch for backward compatibility.
    """

    def __init__(self, name: str, config: WormConfig, state: ClusterState, cancellation_token: Any = None):
        self.name = name
        self.config = config
        self.state = state
        self.cancellation_token = cancellation_token

    def execute(self, timeout: Optional[int] = None, max_retries: Optional[int] = None, cancellation_token=None) -> Dict[str, Any]:
        """Execute worm with configuration using dynamic resolution.
        
        When circuit breaker is enabled, wraps execution in breaker
        protection with exponential backoff retry.
        
        Args:
            timeout: Override timeout in seconds.
            max_retries: Override max retries for breaker backoff (e.g. from category config).
            cancellation_token: Optional CancellationToken for cooperative cancellation.
        """
        if cancellation_token and hasattr(cancellation_token, 'raise_if_cancelled'):
            try:
                cancellation_token.raise_if_cancelled()
            except (TaskCancelledError, Exception) as exc:   # noqa: BLE001 - was silent
                _note_failure("clusterworms:cancellation-check", exc)
                return {
                    "name": self.name,
                    "success": False,
                    "duration_ms": 0,
                    "result": {"error": "Execution cancelled via token"},
                    "error": "Execution cancelled via token",
                }
        # If circuit breaker is available, use it for production-grade execution
        breaker = _get_breaker_registry()
        if breaker:
            return self._execute_with_breaker(timeout, max_retries)
        return self._execute_direct(timeout)

    def _execute_with_breaker(self, timeout: Optional[int] = None, max_retries: Optional[int] = None) -> Dict[str, Any]:
        """Execute with circuit breaker protection + exponential backoff."""
        start = time.time()
        breaker_reg = _get_breaker_registry()
        breaker = breaker_reg.get(self.name) if breaker_reg else None
        if not breaker:
            return self._execute_direct(timeout)
        
        # Resolve max_retries: explicit override > cluster config > default
        if max_retries is not None:
            _max_retries = max_retries
        elif _cfg:
            _max_retries = _cfg.clusters.max_retries
        else:
            _max_retries = 3

        def _do_execute():
            return self._execute_direct(timeout)
        
        try:
            output = breaker.retry_with_backoff(_do_execute, max_retries=_max_retries)
        except RuntimeError as e:
            _note_failure("clusterworms:breaker-runtime-error", e)
            # Circuit open, cancelled, or all retries exhausted
            output = {
                "name": self.name,
                "success": False,
                "duration_ms": (time.time() - start) * 1000,
                "result": {"error": str(e)},
                "error": str(e),
                "breaker_state": breaker.state.value,
            }
        except Exception as e:  # noqa: BLE001 — breaker execution boundary
            _note_failure("clusterworms:breaker-execution", e)
            
            output = {
                "name": self.name,
                "success": False,
                "duration_ms": (time.time() - start) * 1000,
                "result": {"error": str(e)},
                "error": str(e),
            }
        
        self.state.add_result(self.name, output)
        return output

    def _execute_direct(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Direct worm execution without circuit breaker."""
        start = time.time()
        success = False
        result = None
        error = None
        extra: Dict[str, Any] = {}
        try:
            timeout = timeout or self.config.timeout
            
            # Primary: dynamic registry-based resolution
            resolved = _resolve_worm_dynamic(self.config.module)
            
            if resolved:
                success = resolved.get("success", False)
                if success:
                    result = resolved.get("result")
                else:
                    error = resolved.get("error", "Unknown error")
                    result = {"error": error}
            else:
                error = f"Unknown module: {self.config.module}"
                result = {"error": error}

        except _NeedsClarification as _nc:
            _note_failure("clusterworms:needs-clarification", _nc)
            # A worm asking for owner input is not a crash. Normalise it into a
            # needs_input outcome carrying the question and the decision_id, so
            # the ask is visible and joinable rather than swallowed.
            error = "needs owner input: %s" % _nc
            result = {"needs_input": _nc.question,
                      "decision_id": _nc.decision_id}
            extra = {"needs_input": _nc.question,
                     "decision_id": _nc.decision_id,
                     "outcome": "needs_input"}
            success = False
        except _ClarificationTimeout as _ct:
            _note_failure("clusterworms:clarification-timeout", _ct)
            # Fail-closed: no answer arrived, so nothing was decided. Marked
            # timed_out both at top level and inside result, because callers
            # read one or the other and neither may read it as success.
            error = str(_ct)
            result = {"timed_out": True,
                      "question": _ct.question,
                      "waited_s": _ct.waited_s,
                      "decision_id": _ct.decision_id}
            extra = {"timed_out": True,
                     "decision_id": _ct.decision_id,
                     "outcome": "clarification_timeout"}
            success = False
        except Exception as e:  # noqa: BLE001 — worm execution reports, never raises
            _note_failure("clusterworms:worm-execution", e)
            error = str(e)
            result = {"error": error}
            success = False

        duration = (time.time() - start) * 1000

        output = {
            "name": self.name,
            "success": success,
            "duration_ms": duration,
            "result": result,
            "error": error,
        }
        output.update(extra)
        return output


# ═══════════════════════════════════════════════════════════════════════
# CLUSTERWORMS — Main Cluster Orchestrator with PAMS + Mermaid
# ═══════════════════════════════════════════════════════════════════════

def _record_cluster_worm_telemetry(name: str, result: Dict[str, Any],
                                     cluster: Any = None,
                                     decision_id: Any = None) -> None:
    """Record one per-worm execution row (2026-09-09 tap).

    Delegates to the canonical emitter (2026-09-28): same event type,
    same keys, one schema for all producers. Fail-soft by design —
    telemetry must never break execution.
    """
    try:
        from CORE.WORMS.worm_telemetry import emit_worm_outcome
        emit_worm_outcome(name, result, cluster=cluster,
                          decision_id=str(decision_id) if decision_id else None,
                          source="ClusterWORMZ")
    except Exception as exc:                          # noqa: BLE001 - was silent
        _note_failure("clusterworms:telemetry-emit", exc)
        pass


@register_worm(
    category="orchestration", priority=8, cooldown_seconds=20.0,
    description="Coordinated worm collectives with shared state, dynamic resolution, PAMS recording, and Mermaid visualization"
)
class ClusterWORMZ:
    """
    Coordinated worm collectives with shared state and inter-worm messaging.
    
    Now with:
    - Dynamic worm resolution from WORM_REGISTRY (replaces hardcoded if/elif)
    - PAMS memory recording of every cluster action
    - MemoryBus publishing for inter-agent communication
    - Mermaid graph generation for cluster visualization
    - Category-based cluster auto-building from registry categories
    """

    VERSION = "2.2.0"

    def __init__(self, cluster_type: str = "system"):
        self.cluster_type = cluster_type
        self.cluster_id = f"cluster_{time.time_ns()}"
        self.configs: List[WormConfig] = []
        self.state = ClusterState(self.cluster_id)
        self.parallel = False
        self.timeout = 60
        self._execution_count: int = 0
        self._cancel_event: Optional[threading.Event] = None
        # Per-category config overrides (populated by build_from_category)
        self._category_name: Optional[str] = None
        self._category_config: Any = None
        # Cancellation token (3.14t-safe cooperative cancellation)
        self.cancellation_token = CancellationToken() if CancellationToken is not None else threading.Event()
        # Partial-failure bar for overall_status (2026-09-28): a run where
        # at least this share succeeded still counts as success.
        self._partial_failure_threshold = 1.0
        # U3: retirement/quarantine enforcement moved here from
        # ClusterWORMZ.execute. Skips from the last run; read by
        # aggregate_results. A skip is recorded, never silent.
        self._last_skips: Dict[str, List[str]] = {"retired": [],
                                                  "quarantined": []}

        self._load_cluster_config(cluster_type)
        self._record("cluster_created", {
            "cluster_type": cluster_type,
            "cluster_id": self.cluster_id,
        })

    def _load_cluster_config(self, cluster_type: str):
        """Load predefined cluster configuration."""
        if cluster_type in CLUSTER_REGISTRY:
            for worm_name in CLUSTER_REGISTRY[cluster_type]["worms"]:
                self.add_worm(worm_name, worm_name)

    def set_partial_failure_threshold(self, value: float) -> float:
        """Set the success share that still counts as overall success."""
        try:
            v = float(value)
        except (TypeError, ValueError) as exc:         # noqa: BLE001 - was silent
            _note_failure("clusterworms:threshold-parse", exc)
            v = 1.0
        self._partial_failure_threshold = min(1.0, max(0.0, v))
        return self._partial_failure_threshold

    # ── Dynamic Cluster Building ──────────────────────────────────

    @classmethod
    def build_from_category(cls, category: str) -> Optional["ClusterWORMZ"]:
        """
        Build a cluster dynamically from a category in the worm registry.
        """
        if not _REGISTRY_OK:
            return None

        discover_worms()
        cat_worms = get_worms_by_category(category)

        if not cat_worms:
            warnings.warn(
                f"No worms found in category '{category}'. "
                f"Available: {sorted(get_all_categories())}"
            )
            return None

        cluster = cls(f"cat_{category}")
        cluster._category_name = category
        try:
            from CORE.config import get_config as _get_config
            _cfg = _get_config()
            cluster._category_config = _cfg.get_category_config(category)
            if cluster._category_config.timeout:
                cluster.timeout = cluster._category_config.timeout
            if cluster._category_config.parallel is not None:
                cluster.parallel = cluster._category_config.parallel
        except Exception:
            cluster._category_config = None
        cluster.configs = []
        for worm_name in sorted(cat_worms.keys()):
            cluster.add_worm(worm_name, worm_name)

        cluster._record("cluster_built_from_category", {
            "category": category, "worm_count": len(cat_worms),
            "worms": sorted(cat_worms.keys()),
        })

        mem_bus = _get_mem_bus()
        if mem_bus:
            try:
                mem_bus.publish(
                    "cluster.category.built",
                    {"category": category, "worm_count": len(cat_worms)},
                    source="ClusterWORMZ",
                )
            except Exception as exc:  # noqa: BLE001 — bus publish best-effort
                _note_failure("clusterworms:bus-publish-category", exc)
                pass

        return cluster

    @classmethod
    def build_from_registry(cls, worms: List[str]) -> "ClusterWORMZ":
        """
        Build a cluster from a list of worm names, resolving them
        dynamically from the registry.
        """
        cluster = cls("custom")
        cluster.cluster_id = f"cluster_custom_{time.time_ns()}"
        cluster.configs = []
        
        for worm_name in worms:
            cluster.add_worm(worm_name, worm_name)
        
        return cluster

    @classmethod
    def build_full_ecosystem(cls) -> "ClusterWORMZ":
        """Build a cluster containing ALL registered worms across ALL categories."""
        if not _REGISTRY_OK:
            return cls("full")
        
        discover_worms()
        all_worms: Set[str] = set()
        for category in get_all_categories():
            cat_worms = get_worms_by_category(category)
            all_worms.update(cat_worms.keys())
        
        return cls.build_from_registry(sorted(all_worms))

    # ── Worm Management ───────────────────────────────────────────

    def add_worm(self, name: str, module: str, config: Optional[Dict] = None):
        """Add worm to cluster."""
        worm_config = WormConfig(
            name=name,
            module=module,
            enabled=config.get("enabled", True) if config else True,
            priority=config.get("priority", 0) if config else 0,
            timeout=config.get("timeout", 30) if config else 30,
            retry=config.get("retry", 1) if config else 1,
            params=config.get("params", {}) if config else {},
        )
        self.configs.append(worm_config)

    def remove_worm(self, name: str):
        """Remove worm from cluster."""
        self.configs = [c for c in self.configs if c.name != name]

    def set_parallel(self, parallel: bool):
        """Set parallel or sequential execution."""
        self.parallel = parallel

    def set_timeout(self, timeout: int):
        """Set execution timeout."""
        self.timeout = timeout

    # ── Execution ─────────────────────────────────────────────────

    def execute_all(self, worm_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Execute all worms in cluster.

        Recording strategy:
        - _record() -> PamsRecorder: persistent audit trail (all events)
        - _mem_bus.publish() -> MemoryBus: real-time pub/sub for inter-agent coordination
        These are complementary, not redundant — PAMS is for post-mortem,
        MemoryBus is for live coordination between agents.

        `worm_names` optionally restricts to a subset (unknown names are
        ignored — ran, nothing invented, nothing lost). Retirement and
        quarantine are ENFORCED here (U3, was ClusterWORMZ.execute): a
        retired or quarantined worm is skipped and recorded, never run.
        """
        self._cancel_event = threading.Event()
        self._last_skips = {"retired": [], "quarantined": []}
        self._record("cluster_execution_started", {
            "cluster_type": self.cluster_type,
            "worm_count": len([c for c in self.configs if c.enabled]),
            "parallel": self.parallel,
        })
        try:
            with _dm_lock:
                _dm_ref = _dm
            if _dm_ref is not None:
                _dm_ref.record("clusterwormz_execute_start",
                               {"cluster_type": self.cluster_type,
                                "cluster_id": self.cluster_id})
        except Exception as exc:                      # noqa: BLE001 - was silent
            _note_failure("clusterworms:directmemory-record-start", exc)
            pass
        
        mem_bus = _get_mem_bus()
        if mem_bus:
            try:
                mem_bus.publish(
                    "cluster.execute.start",
                    {"cluster_id": self.cluster_id, "worm_count": len(self.configs)},
                    source="ClusterWORMZ",
                )
            except Exception as exc:  # noqa: BLE001 — bus publish best-effort
                _note_failure("clusterworms:bus-publish-execute", exc)
                pass
        
        results = []
        enabled_configs = [c for c in self.configs if c.enabled]
        if worm_names is not None:
            wanted = set(worm_names)
            enabled_configs = [c for c in enabled_configs if c.name in wanted]
        # U3 enforcement (was ClusterWORMZ.execute): retirement and
        # quarantine gate the run, visibly. Skips land in _last_skips and
        # surface on the aggregate — a held-out worm must read as held out.
        enabled_configs = [c for c in enabled_configs
                           if not self._skip_enforced(c.name)]
        enabled_configs.sort(key=lambda x: x.priority, reverse=True)

        if self.parallel:
            # ════════════════════════════════════════════════════════════════
            # FIX: Pre-initialize all lazy singletons on the main thread
            # BEFORE entering the thread pool. Under Python 3.14t free-threading
            # (no GIL), importlib.import_module() and lazy singletons are NOT
            # thread-safe. Running them from worker threads causes deadlocks.
            # ════════════════════════════════════════════════════════════════
            if _REGISTRY_OK:
                discover_worms(progress_callback=_discover_progress)  # Populate worm registry
                print(file=sys.stderr)  # close progress line
            _get_breaker_registry()  # Breaker registry + config loading
            _ensure_legacy_modules()  # Pre-import legacy modules on main thread
            
            # FreeThreadPool — adaptive worker sizing for I/O-bound worm execution.
            # Worm workloads are dominated by disk/network I/O (file scans, HTTP calls,
            # module imports), not CPU. adaptive_workers(io_bound=True) provides up
            # to cpu_count * 4 workers (capped at 32) for better throughput.
            pool_cls: Any = FreeThreadPool  # optional import; None keeps the original first-use AttributeError failure mode
            max_workers = pool_cls.adaptive_workers(len(enabled_configs), io_bound=True)
            timed_out_worms: List[str] = []
            
            with pool_cls(max_workers=max_workers) as pool:
                # Batch-submit all worm tasks with named labels
                tasks = [(self._execute_single_worm, (config,), {"_label": config.name})
                         for config in enabled_configs]
                futures = pool.submit_all(tasks)
                
                try:
                    for future in as_completed(futures, timeout=self.timeout):
                        name = futures[future]
                        try:
                            out = future.result(timeout=1.0)
                            results.append(out)
                            mark_spawned(name)
                        except TimeoutError as exc:       # noqa: BLE001 - was silent
                            _note_failure("clusterworms:pool-worm-timeout", exc)
                            getattr(self.cancellation_token, "cancel")()
                            if self._cancel_event:
                                self._cancel_event.set()
                            future.cancel()
                            timed_out_worms.append(name)
                            results.append({
                                "name": name,
                                "success": False,
                                "duration_ms": self.timeout * 1000,
                                "result": {"error": f"Timed out after {self.timeout}s"},
                                "error": f"Timed out after {self.timeout}s",
                                "timed_out": True,
                            })
                        except Exception as e:  # noqa: BLE001 — per-worm boundary reports
                            _note_failure("clusterworms:pool-worm-failure", e)
                            results.append({
                                "name": name,
                                "success": False,
                                "duration_ms": 0,
                                "result": {"error": str(e)},
                                "error": str(e),
                            })
                except TimeoutError as exc:               # noqa: BLE001 - was silent
                    _note_failure("clusterworms:pool-batch-timeout", exc)
                    getattr(self.cancellation_token, "cancel")()
                    if self._cancel_event:
                        self._cancel_event.set()
                    # as_completed timed out — cancel and record stragglers
                    for future, name in futures.items():
                        if not future.done():
                            future.cancel()
                            timed_out_worms.append(name)
                            results.append({
                                "name": name,
                                "success": False,
                                "duration_ms": self.timeout * 1000,
                                "result": {"error": f"Timed out after {self.timeout}s"},
                                "error": f"Timed out after {self.timeout}s",
                                "timed_out": True,
                            })
            
            if timed_out_worms:
                self._record("cluster_thread_timeout", {
                    "cluster_type": self.cluster_type,
                    "timed_out_worms": timed_out_worms,
                    "timeout": self.timeout,
                })
        else:
            for config in enabled_configs:
                result = self._execute_single_worm(config)
                results.append(result)
                mark_spawned(config.name)

        self._execution_count += 1

        self._record("cluster_execution_completed", {
            "cluster_type": self.cluster_type,
            "results": len(results),
            "success": len([r for r in results if r.get("success")]),
        })
        try:
            with _dm_lock:
                _dm_ref = _dm
            if _dm_ref is not None:
                _dm_ref.record("clusterwormz_execute_complete",
                               {"cluster_type": self.cluster_type,
                                "cluster_id": self.cluster_id,
                                "results": len(results)})
        except Exception as exc:                      # noqa: BLE001 - was silent
            _note_failure("clusterworms:directmemory-record-complete", exc)
            pass

        return results

    def _skip_enforced(self, name: str) -> bool:
        """True when a worm must not run: retired or quarantined. Records why."""
        try:
            info = (WORM_REGISTRY.get(name) or {}) if _REGISTRY_OK else {}
        except Exception as exc:                      # noqa: BLE001 - was silent
            _note_failure("clusterworms:skip-registry-lookup", exc)
            info = {}
        if str(info.get("lifecycle_state") or "") == "retired":
            self._last_skips["retired"].append(name)
            return True
        try:
            from CORE import spine_registry as _sr
            if _sr.is_quarantined(name):
                self._last_skips["quarantined"].append(name)
                return True
        except Exception as exc:                      # noqa: BLE001 - was silent
            _note_failure("clusterworms:quarantine-check", exc)
            pass
        return False

    def _execute_single_worm(self, config: WormConfig,
                               decision_id: Any = None) -> Dict[str, Any]:
        """Execute a single worm with retry logic. Runs in a pool thread.
        
        This method is designed for lock-free parallel execution:
        each call is isolated (no shared state writes), returns its result,
        and the main thread collects results sequentially.
        """
        executor = WormExecutor(config.name, config, self.state, self.cancellation_token)
        result = executor.execute(
            timeout=self.timeout,
            max_retries=None,
        )

        if not _get_breaker_registry():
            delay = 0.1
            for attempt in range(max(0, config.retry - 1)):
                if result.get("success"):
                    break
                if getattr(self.cancellation_token, 'is_cancelled', False):
                    break
                if self._cancel_event and self._cancel_event.wait(delay):
                    break
                elif not self._cancel_event:
                    time.sleep(delay)
                delay = min(delay * 2.0, 60.0)
                result = executor.execute(
                    timeout=self.timeout,
                    max_retries=None,
                )

        _record_cluster_worm_telemetry(config.name, result, cluster=self,
                                         decision_id=decision_id)
        return result

    def execute_worm(self, name: str,
                     decision_id: Any = None) -> Optional[Dict[str, Any]]:
        """Execute single worm by name.

        Every path emits to the canonical tap (2026-09-28): selected but
        unconfigured and selected but unimportable used to vanish, hiding
        exactly the failures the spawn-credit join exists to surface.
        """
        for config in self.configs:
            if config.name == name:
                try:
                    executor = WormExecutor(config.name, config, self.state)
                    result = executor.execute()
                except Exception as exc:
                    _record_cluster_worm_telemetry(
                        name, {"success": False, "duration_ms": 0.0,
                               "error": "executor failed: %s" % str(exc)[:200]},
                        cluster=self, decision_id=decision_id)
                    raise
                _record_cluster_worm_telemetry(name, result if isinstance(
                    result, dict) else {"success": False, "duration_ms": 0.0,
                                        "error": "non-dict result"},
                    cluster=self, decision_id=decision_id)
                mark_spawned(name)
                return result
        _record_cluster_worm_telemetry(
            name, {"success": False, "duration_ms": 0.0,
                   "error": "not configured in this cluster"},
            cluster=self, decision_id=decision_id)
        return None

    # ── Result Aggregation ────────────────────────────────────────

    def aggregate_results(self) -> ClusterResult:
        """Aggregate results from all worms."""
        results = self.state.results

        metrics: Dict[str, float] = {
            "total_duration_ms": 0.0,
            "success_rate": 0.0,
            "avg_duration_ms": 0.0,
        }

        success_count = 0
        total_duration = 0.0

        for worm_name, result in results.items():
            if isinstance(result, dict) and "duration_ms" in result:
                total_duration += result["duration_ms"]
                if result.get("success", False):
                    success_count += 1

        metrics["total_duration_ms"] = total_duration
        metrics["success_rate"] = success_count / len(results) if results else 0
        metrics["avg_duration_ms"] = total_duration / len(results) if results else 0

        return ClusterResult(
            cluster_id=self.cluster_id,
            worm_results=results,
            aggregated_metrics=metrics,
            duration_ms=total_duration,
            success_count=success_count,
            failure_count=len(results) - success_count,
            partial_failure_threshold=self._partial_failure_threshold,
            retired_skipped=list(self._last_skips.get("retired", [])),
            quarantined_skipped=list(self._last_skips.get("quarantined", [])),
        )

    # ── Status ────────────────────────────────────────────────────

    def get_status(self) -> Dict:
        """Get cluster status."""
        pams = _get_pams()
        mem_bus = _get_mem_bus()
        breaker_reg = _get_breaker_registry()
        breaker_stats = {}
        if breaker_reg:
            try:
                breaker_stats = {
                    "breaker_ok": True,
                    "breaker_stats": breaker_reg.all_stats(),
                }
            except Exception as exc:  # noqa: BLE001 — breaker stats best-effort
                _note_failure("clusterworms:breaker-stats", exc)
                pass
        
        return {
            "cluster_id": self.cluster_id,
            "cluster_type": self.cluster_type,
            "version": self.VERSION,
            "worm_count": len(self.configs),
            "enabled_count": len([c for c in self.configs if c.enabled]),
            "parallel": self.parallel,
            "timeout": self.timeout,
            "execution_count": self._execution_count,
            "last_sync": self.state.last_sync.isoformat(),
            "free_threaded": is_free_threaded(),
            "worm_names": sorted([c.name for c in self.configs]),
            "registry_ok": _REGISTRY_OK,
            "pams_ok": pams is not None,
            "bus_ok": mem_bus is not None,
            "breaker_ok": breaker_reg is not None,
            "partial_failure_threshold": self._partial_failure_threshold,
            "category_name": self._category_name,
            "category_config_applied": self._category_config is not None,
            **breaker_stats,
        }

    # ── Mermaid Graphs ────────────────────────────────────────────

    def to_mermaid(self, title: str = "") -> str:
        """
        Generate Mermaid graph of this cluster's worms.
        
        Shows worm names, priorities, and the cluster structure.
        """
        label = title or f"{self.cluster_type.upper()} Cluster"
        lines = ["graph TB"]
        lines.append(f'    title["CLUSTER: {label}<br/>{len(self.configs)} worms | {self.cluster_id}"]')
        lines.append("    style title fill:#1a1a2e,stroke:#00d9ff,color:#00d9ff")
        lines.append("")
        
        lines.append(f"    subgraph {self.cluster_type}[{self.cluster_type.upper()}]")
        
        for config in sorted(self.configs, key=lambda c: c.priority, reverse=True):
            status = "enabled" if config.enabled else "disabled"
            color = "#00ff88" if config.enabled else "#666"
            lines.append(f'        {config.name}["{config.name}<br/>pri={config.priority} | {status}"]')
            lines.append(f"        style {config.name} fill:#16213e,stroke:{color},color:{color}")
        
        lines.append("    end")
        return "\n".join(lines)

    def to_mermaid_execution_flow(self) -> str:
        """
        Generate Mermaid flowchart of the execution order with results.
        """
        results = self.state.results
        lines = ["graph LR"]
        lines.append(f'    title["⚡ Execution Flow: {self.cluster_type}<br/>{len(results)} results"]')
        lines.append("    style title fill:#1a1a2e,stroke:#00d9ff,color:#00d9ff")
        lines.append("")
        
        sorted_names = sorted(self.configs, key=lambda c: c.priority, reverse=True)
        prev = None
        for config in sorted_names:
            name = config.name
            result = results.get(name, {})
            status = "OK" if result.get("success") else "FAIL"
            color = "#00ff88" if result.get("success") else "#ff4444"
            duration = result.get("duration_ms", 0)
            
            lines.append(f'    {name}["{status} {name}<br/>{duration:.0f}ms"]')
            lines.append(f"    style {name} fill:#16213e,stroke:{color},color:{color}")
            
            if prev:
                lines.append(f"    {prev} --> {name}")
            prev = name
        
        return "\n".join(lines)

    def to_mermaid_interactions(self) -> str:
        """
        Generate Mermaid sequence diagram of inter-worm messaging.
        """
        lines = ["sequenceDiagram"]
        lines.append(f"    title Inter-Worm Messaging: {self.cluster_type}")
        
        # Participants
        for config in self.configs:
            lines.append(f"    participant {config.name}")
        
        # Messages
        for msg in self.state.messages[:20]:
            from_w = msg.get("from", "???")
            to_w = msg.get("to", "???")
            data_str = str(msg.get("data", ""))[:40]
            lines.append(f"    {from_w}->>+{to_w}: {data_str}")
        
        return "\n".join(lines)

    # ── PAMS Memory Recording ─────────────────────────────────────

    def _record(self, event_type: str, data: Dict[str, Any]):
        """Record event to PAMS memory."""
        pams = _get_pams()
        if pams:
            try:
                pams.record_event(
                    event_type, data,
                    source=f"ClusterWORMZ.{self.cluster_type}",
                    severity="info",
                )
            except Exception as exc:  # noqa: BLE001 — telemetry best-effort
                _note_failure("clusterworms:pams-record-event", exc)
                pass

    def __repr__(self):
        return f"ClusterWORMZ({self.cluster_type}, {len(self.configs)} worms, v{self.VERSION})"


# ═══════════════════════════════════════════════════════════════════════
# GUI
# ═══════════════════════════════════════════════════════════════════════

def run_popup():
    """Run cluster worms with popup window."""
    try:
        import tkinter as tk
        from tkinter import ttk, scrolledtext
    except ImportError:
        print("tkinter not available — GUI disabled")
        return
    
    root = tk.Tk()
    root.title("CLUSTERWORMS — Coordinated Worm Collectives")
    root.geometry("1000x750")
    root.configure(bg="#1a1a2e")

    def log(msg, level="INFO"):
        colors = {
            "OK": "#00ff88",
            "FAIL": "#ff4444",
            "INFO": "#00aaff",
            "CLUSTER": "#ff00ff",
        }
        timestamp = datetime.now().strftime("%H:%M:%S")
        text_area.insert("end", f"[{timestamp}] [{level}] {msg}\n")
        text_area.see("end")
        root.update_idletasks()

    header = tk.Frame(root, bg="#16213e", pady=15)
    header.pack(fill="x")

    tk.Label(
        header,
        text="CLUSTERWORMS v2.0",
        font=("Consolas", 28, "bold"),
        bg="#16213e",
        fg="#00d4ff",
    ).pack()

    tk.Label(
        header,
        text="Tier 2: Coordinated Worm Collectives + Dynamic Resolution",
        font=("Consolas", 12),
        bg="#16213e",
        fg="#7c3aed",
    ).pack()

    status_label = tk.Label(
        root,
        text="Ready",
        font=("Consolas", 14),
        bg="#1a1a2e",
        fg="#00ff88",
    )
    status_label.pack(pady=5)

    control_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    control_frame.pack(fill="x", padx=20)

    tk.Label(
        control_frame,
        text="Cluster:",
        bg="#1a1a2e",
        fg="#00d4ff",
        font=("Consolas", 11),
    ).pack(side="left")

    cluster_var = tk.StringVar(value="system")
    cluster_combo = ttk.Combobox(
        control_frame,
        textvariable=cluster_var,
        values=list(CLUSTER_REGISTRY),
        width=15,
        font=("Consolas", 10),
    )
    cluster_combo.pack(side="left", padx=10)

    parallel_var = tk.BooleanVar(value=False)
    parallel_check = tk.Checkbutton(
        control_frame,
        text="Parallel Execution",
        variable=parallel_var,
        font=("Consolas", 11),
        bg="#1a1a2e",
        fg="#00ff88",
        selectcolor="#16213e",
    )
    parallel_check.pack(side="left", padx=20)

    progress_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    progress_frame.pack(fill="x", padx=20)

    progress = ttk.Progressbar(progress_frame, mode="determinate", length=950)
    progress.pack(fill="x")

    progress_label = tk.Label(
        progress_frame,
        text="0 / 0 worms",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00ff88",
    )
    progress_label.pack()

    stats_frame = tk.Frame(root, bg="#1a1a2e", pady=5)
    stats_frame.pack(fill="x", padx=20)

    stats_label = tk.Label(
        stats_frame,
        text="Worms: 0 | Success: 0 | Duration: 0ms",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00d4ff",
    )
    stats_label.pack()

    text_area = scrolledtext.ScrolledText(
        root, wrap="word", font=("Consolas", 10), bg="#0f0f1a", fg="#00ff88", height=35
    )
    text_area.pack(fill="both", expand=True, padx=20, pady=10)

    button_frame = tk.Frame(root, bg="#1a1a2e", pady=15)
    button_frame.pack(fill="x", padx=20)

    def run_cluster():
        status_label.config(text="Executing...")
        progress["value"] = 0
        text_area.delete("1.0", "end")

        cluster_type = cluster_var.get()
        parallel = parallel_var.get()

        log("=" * 60, "CLUSTER")
        log(f"CLUSTERWORMS v2 — {cluster_type.upper()} Cluster", "CLUSTER")
        log("=" * 60, "CLUSTER")

        cluster = ClusterWORMZ(cluster_type)
        cluster.set_parallel(parallel)

        status = cluster.get_status()
        log(f"Cluster: {status['cluster_id']}", "INFO")
        log(f"Worms: {status['worm_count']} | Parallel: {parallel}", "INFO")
        log(f"Dynamic Resolution: {'ON' if _REGISTRY_OK else 'OFF (legacy)'}", "INFO")
        log("-" * 60, "INFO")

        progress["value"] = 20
        progress_label.config(text="Executing worms...")
        root.update()

        results = cluster.execute_all()

        progress["value"] = 80
        log(f"\nExecution Results:", "INFO")
        for r in results:
            status_icon = "OK" if r.get("success") else "FAIL"
            method = r.get("result", {})
            if isinstance(method, dict):
                method_name = method.get("method", "?")
            else:
                method_name = "?"
            log(
                f"  [{status_icon}] {r['name']}: {r.get('duration_ms', 0):.0f}ms [{method_name}]",
                status_icon if r.get("success") else "FAIL",
            )

        agg = cluster.aggregate_results()

        log(f"\nAggregation:", "INFO")
        log(
            f"  Success Rate: {agg.success_count}/{len(results)} ({agg.success_count / max(len(results), 1) * 100:.0f}%)",
            "INFO",
        )
        log(f"  Total Duration: {agg.duration_ms:.0f}ms", "INFO")
        
        log(f"\nMermaid Graph:", "INFO")
        log(cluster.to_mermaid(), "INFO")

        progress["value"] = 100
        stats_label.config(
            text=f"Worms: {len(results)} | Success: {agg.success_count} | Duration: {agg.duration_ms:.0f}ms"
        )
        status_label.config(text="Complete!")

    def show_clusters():
        text_area.delete("1.0", "end")
        log("=" * 60, "CLUSTER")
        log("Available Clusters + Dynamic Categories", "CLUSTER")
        log("=" * 60, "CLUSTER")
        for name, info in CLUSTER_REGISTRY.items():
            log(f"\n{name.upper()}:", "INFO")
            log(f"  Description: {info['description']}", "INFO")
            log(f"  Worms: {', '.join(info['worms'])}", "INFO")
        
        if _REGISTRY_OK:
            log(f"\n── Dynamic Category Clusters ──", "CLUSTER")
            discover_worms()
            for cat in sorted(get_all_categories()):
                cat_worms = get_worms_by_category(cat)
                log(f"\n[{cat}]: {len(cat_worms)} worms", "INFO")
                log(f"  {', '.join(sorted(list(cat_worms)[:8]))}", "INFO")

    btn_run = tk.Button(
        button_frame,
        text="EXECUTE",
        command=run_cluster,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#00ff88",
        width=12,
    )
    btn_run.pack(side="left", padx=10)

    btn_show = tk.Button(
        button_frame,
        text="CLUSTERS",
        command=show_clusters,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#ff00ff",
        width=10,
    )
    btn_show.pack(side="left", padx=10)

    close_btn = tk.Button(
        button_frame,
        text="CLOSE",
        command=root.destroy,
        font=("Consolas", 13, "bold"),
        bg="#ff4444",
        fg="#ffffff",
        width=10,
    )
    close_btn.pack(side="right", padx=10)

    log("CLUSTERWORMS v2 Ready — Dynamic Resolution ON", "INFO")
    pams = _get_pams()
    mem_bus = _get_mem_bus()
    log(f"Registry OK: {_REGISTRY_OK} | PAMS OK: {pams is not None} | Bus OK: {mem_bus is not None}", "INFO")
    log("Select cluster type and click EXECUTE", "INFO")

    root.mainloop()


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(
        description="CLUSTERWORMS v2 — Coordinated Worm Collectives"
    )
    parser.add_argument(
        "--type", type=str, help="Cluster type (system, network, bibl, etc.)"
    )
    parser.add_argument("--list", action="store_true", help="List available clusters")
    parser.add_argument("--execute", action="store_true", help="Execute cluster")
    parser.add_argument("--parallel", action="store_true", help="Parallel execution")
    parser.add_argument("--popup", action="store_true", help="Show popup window")
    parser.add_argument("--mermaid", action="store_true", help="Generate Mermaid graph")
    parser.add_argument("--mermaid-flow", action="store_true", help="Execution flow Mermaid")
    parser.add_argument("--build-category", type=str, help="Build cluster from registry category")
    parser.add_argument("--discover", action="store_true", help="Discover and list all registry categories")

    args = parser.parse_args()

    if args.popup:
        run_popup()
    
    elif args.discover:
        if _REGISTRY_OK:
            discover_worms(progress_callback=_discover_progress)
            print(file=sys.stderr)  # close progress line
            print("Categories Available for Cluster Building:")
            print()
            for cat in sorted(get_all_categories()):
                cat_worms = get_worms_by_category(cat)
                print(f"  [{cat}] {len(cat_worms)} worms")
                for name in sorted(list(cat_worms)[:8]):
                    print(f"    - {name}")
                if len(cat_worms) > 8:
                    print(f"    ... and {len(cat_worms) - 8} more")
            print(f"\nTotal: {len(get_all_categories())} categories")
        else:
            print("Worm registry not available")
    
    elif args.build_category:
        cluster = ClusterWORMZ.build_from_category(args.build_category)
        if cluster:
            print(f"Built cluster from [{args.build_category}] category: {len(cluster.configs)} worms")
            for c in cluster.configs:
                print(f"  - {c.name}")
            cluster.set_parallel(args.parallel)
            results = cluster.execute_all()
            agg = cluster.aggregate_results()
            print(f"Success: {agg.success_count}/{len(results)}")
            print(f"Duration: {agg.duration_ms:.0f}ms")
            
            if args.mermaid:
                print("\n" + cluster.to_mermaid())
        else:
            print(f"No worms found for category '{args.build_category}'")
    
    elif args.mermaid:
        cluster = ClusterWORMZ(args.type or "system")
        print(cluster.to_mermaid())
    
    elif args.list:
        print("Available Clusters:")
        for name, info in CLUSTER_REGISTRY.items():
            print(f"  {name}: {info['description']}")
            print(f"    Worms: {', '.join(info['worms'])}")
        
        if _REGISTRY_OK:
            discover_worms(progress_callback=_discover_progress)
            print(file=sys.stderr)  # close progress line
            print(f"\nDynamic Categories: {len(get_all_categories())}")
            for cat in sorted(get_all_categories()):
                cat_worms = get_worms_by_category(cat)
                print(f"  [{cat}]: {len(cat_worms)} worms")
    
    elif args.execute:
        cluster_type = args.type or "system"
        cluster = ClusterWORMZ(cluster_type)
        cluster.set_parallel(args.parallel)
        results = cluster.execute_all()
        agg = cluster.aggregate_results()
        print(f"Cluster: {cluster_type}")
        print(f"Success: {agg.success_count}/{len(results)}")
        print(f"Duration: {agg.duration_ms:.0f}ms")
        
        if args.mermaid_flow:
            print("\n" + cluster.to_mermaid_execution_flow())

    else:
        print("CLUSTERWORMS v2 — Dynamic Worm Resolution")
        print()
        print("Usage:")
        print("  python CLUSTERWORMS.py --type system --execute")
        print("  python CLUSTERWORMS.py --list")
        print("  python CLUSTERWORMS.py --type bibl --parallel --execute")
        print("  python CLUSTERWORMS.py --build-category security --execute")
        print("  python CLUSTERWORMS.py --discover")
        print("  python CLUSTERWORMS.py --mermaid")
        print("  python CLUSTERWORMS.py --popup")


if __name__ == "__main__":
    main()
