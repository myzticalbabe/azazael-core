# coding: utf-8
"""Model versioning worm -- ModelVersioner (split from learning.py, 2026-09-07)."""

import json
import time
import random
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple
from collections import defaultdict
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("learning_versioner:L17", exc)
    _REGISTRY = False
def register_worm(category: str = "utility", priority: int = 5,
                    cooldown_seconds: float = 30.0, description: str = "",
                    tags: (set[str] | None) = None) -> Any:
    return lambda cls: cls


# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("learning_versioner:L27", exc)
    _FREETHREAD = False
FreeThreadPool = None
ThreadSafeDict = dict
ThreadSafeList = list
def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:  # noqa: BLE001 — 314t detection is best-effort
            note_failure("learning_versioner:L36", exc)
            return False

@register_worm(category="learning", priority=5, cooldown_seconds=60.0,
    description="Model versioner v2 — persistent ML model version tracking via LocalMemoryStore with rollback support")
class ModelVersioner:
    """Tracks ML model versions with LocalMemoryStore persistence v2.0.
    
    Stores model versions in LocalMemoryStore for durability.
    Supports: register, compare, list, rollback, and history queries.
    
    Usage:
        mv = ModelVersioner()
        
        # Register a trained model
        mv.register_model("worm_dispatch", "1.0.0", {
            "avg_reward": 0.85, "success_rate": 0.92
        }, metadata={"training_source": "SystemZ"})
        
        # Compare versions
        comparison = mv.compare_versions("worm_dispatch", "1.0.0", "2.0.0")
        
        # List all versions of a model
        versions = mv.list_versions("worm_dispatch")
    """

    VERSION = "2.0.0"

    def __init__(self):
        self._models: Dict[str, List[Dict]] = {}
        self._lock = threading.Lock()
        self._store = None  # Lazy LocalMemoryStore

    def get_summary(self) -> Dict[str, Any]:
        """Worm contract: return a summary dict (see BaseWormMixin).

        `ModelVersioner` was split out of the `learning.py` monolith without a
        `get_summary()`, so the smoke test that instantiates every registered
        worm saw `None` here. Reports the in-memory version registry without
        touching LocalMemoryStore, so it is safe to call from the smoke test.
        """
        with self._lock:
            model_count = len(self._models)
            version_count = sum(len(v) for v in self._models.values())
        return {
            "success": True,
            "method": "get_summary",
            "result": {
                "version": self.VERSION,
                "model_count": model_count,
                "version_count": version_count,
            },
        }

    def _get_store(self) -> Any:
        """Lazy LocalMemoryStore connection — double-checked locking for 3.14t."""
        if self._store is None:
            with self._lock:
                if self._store is None:
                    try:
                        from CORE.local_memory import get_memory_store
                        self._store = get_memory_store()
                    except ImportError as exc:
                        note_failure("learning_versioner:L98", exc)
                        self._store = False
        return self._store if self._store else None
    def register_model(self, name: str, version: str, metrics: Dict[str, float],
                       metadata: Optional[Dict] = None) -> Dict:
        """Register a model version, persisting to LocalMemoryStore."""
        entry = {
            "name": name, "version": version, "metrics": metrics,
            "metadata": metadata or {},
            "registered_at": datetime.now().isoformat(),
        }
        with self._lock:
            prior = self._models.get(name) or []
            # Write-on-change: the LAST registration of this version is the one
            # being superseded. Comparing against the last (not against any
            # entry) is deliberate -- metrics returning to an older value is a
            # CHANGE, so `any(prior == metrics)` would lose it. In-process
            # history is always appended; only the store write is suppressed.
            last_same_version = next(
                (p for p in reversed(prior) if p.get("version") == version), None)
            unchanged = (last_same_version is not None
                         and last_same_version.get("metrics") == metrics
                         and (last_same_version.get("metadata") or {})
                         == (metadata or {}))
            self._models[name] = prior + [entry]

        # Persist to LocalMemoryStore (skipped when nothing about this version
        # changed -- otherwise a caller that re-registers on every cycle spams
        # the store with identical rows, which is what made the audit trail
        # unreadable rather than merely long).
        store = None if unchanged else self._get_store()
        if store:
            try:
                store.record_event(
                    source="ModelVersioner", event_type="model_registered",
                    data={
                        "model_name": name,
                        "model_version": version,
                        "metrics": metrics,
                        "metadata": metadata or {},
                        "version_count": len(self._models[name]),
                    },
                    severity="info", tags=["model", "versioner"]
                )
            except Exception as exc:  # noqa: BLE001 — version-clone telemetry best-effort
                note_failure("learning_versioner:L142", exc)

        return entry
    def compare_versions(self, name: str, v1: str, v2: str) -> Dict[str, Any]:
        """Compare metrics between two model versions."""
        versions = self._models.get(name, [])
        m1 = next((m for m in versions if m["version"] == v1), None)
        m2 = next((m for m in versions if m["version"] == v2), None)
        if not m1 or not m2:
            return {"error": "Version not found"}
        comparison = {}
        for k in set(list(m1["metrics"]) + list(m2["metrics"])):
            comparison[k] = {
                "v1": m1["metrics"].get(k),
                "v2": m2["metrics"].get(k),
                "delta": round(m2["metrics"].get(k, 0) - m1["metrics"].get(k, 0), 4),
            }
        return {"model": name, "v1": v1, "v2": v2, "comparison": comparison}

    def list_versions(self, name: str) -> List[Dict]:
        """List all versions of a model."""
        with self._lock:
            return list(self._models.get(name, []))
    def get_latest_version(self, name: str) -> Optional[Dict]:
        """Get the latest (most recently registered) version of a model."""
        with self._lock:
            versions = self._models.get(name, [])
            return versions[-1] if versions else None
    def rollback(self, name: str, target_version: str) -> Dict[str, Any]:
        """Rollback to a specific version (soft rollback — marks active)."""
        versions = self._models.get(name, [])
        target = next((v for v in versions if v["version"] == target_version), None)
        if not target:
            return {"error": f"Version {target_version} not found for model {name}"}

        # Record rollback event
        store = self._get_store()
        if store:
            store.record_event(
                source="ModelVersioner", event_type="model_rollback",
                data={
                    "model_name": name,
                    "rolled_back_to": target_version,
                    "current_version": versions[-1]["version"] if versions else "none",
                },
                severity="warning", tags=["model", "rollback"]
            )

        return {
            "model": name,
            "rolled_back_to": target_version,
            "target": target,
        }

    def get_history(self, name: str, include_store: bool = True) -> List[Dict]:
        """Get version history from memory and optionally LocalMemoryStore."""
        versions = self.list_versions(name) if not include_store else []

        if include_store:
            store_inst = self._get_store()
            if store_inst:
                stored = store_inst.search_memories(
                    f"model_registered {name}", source="ModelVersioner", limit=50
                )
                for entry in stored:
                    data = entry.get("data", {})
                    if isinstance(data, dict) and data.get("model_name") == name:
                        versions.append({
                            "name": data["model_name"],
                            "version": data["model_version"],
                            "metrics": data.get("metrics", {}),
                            "registered_at": entry.get("timestamp", ""),
                        })

        # Deduplicate by version
        seen = set()
        unique = []
        for v in versions:
                    v_str = str(v.get("version", ""))
                    if v_str not in seen:
                        seen.add(v_str)
                        unique.append(v)

        return unique


def pareto_retain(candidates, metrics_key="metrics"):
    """Pareto retention filter over version candidates (EP234, set half).

    candidates: [{..., metrics: {obj: float}}], all objectives maximized.
    Returns {"retained", "dominated"} (version strings). Wiring this into
    register_model as the retention predicate (collapsing EP228/EP234/EP462)
    stays deferred: admission policy needs coordinator approval.
    """
    vecs = {}
    for c in candidates or []:
        m = (c or {}).get(metrics_key, {}) or {}
        try:
            vecs[str(c.get("version", "?"))] = [float(v) for v in m.values()]
        except (TypeError, ValueError) as exc:
            note_failure("expansion/learning_versioner:253", exc)
            continue
    names = list(vecs)
    retained: list = []
    dominated: list = []
    for a in names:
        beaten = False
        for b in names:
            if a == b or len(vecs[b]) != len(vecs[a]) or not vecs[a]:
                continue
            if (all(y >= x for x, y in zip(vecs[a], vecs[b]))
                    and any(y > x for x, y in zip(vecs[a], vecs[b]))):
                beaten = True
                break
        (dominated if beaten else retained).append(a)
    return {"retained": retained, "dominated": dominated}
