# coding: utf-8
"""
SessionStateManager — Auto-save/restore worm state across runs.

Pluggable save_state/load_state interface with versioned schemas.
Persists to LocalMemoryStore and supports schema migration.

Usage:
    ssm = SessionStateManager()
    ssm.register_worm_state("MyWorm", get_state_fn, set_state_fn, version="1.0.0")
    ssm.snapshot_all()       # Save all registered worm states
    ssm.restore_all()        # Load and restore all worm states
    ssm.migrate_state("MyWorm", "1.0.0", "2.0.0")

Part of AZAZAEL Worm Registry — Category: utility, Priority: 5
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


__all__ = ["SessionStateManager"]

try:
    from CORE.worm_registry import register_worm, BaseWormMixin
    _REGISTRY_OK = True
except ImportError as exc:
    note_failure("session_state:L31", exc)
    _REGISTRY_OK = False
    BaseWormMixin: Any = object
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


# ── LocalMemoryStore (lazy, thread-safe) ───────────────────────────
_store: Any = None
_store_lock = threading.Lock()


def _get_store():
    """Lazy LocalMemoryStore — double-checked locking for 3.14t."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                try:
                    from CORE.local_memory import LocalMemoryStore
                    _store = LocalMemoryStore()
                except ImportError as exc:
                    note_failure("session_state:L53", exc)
                    _store = False
    return _store if _store else None


# ── DirectMemory (lazy, thread-safe) ───────────────────────────────
_dm: Any = None
_dm_lock = threading.Lock()


def _get_dm():
    global _dm
    if _dm is None:
        with _dm_lock:
            if _dm is None:
                try:
                    from CORE.direct_memory import DirectMemory
                    _dm = DirectMemory(session_id="session_state_manager")
                except ImportError as exc:
                    note_failure("session_state:L71", exc)
                    _dm = False
    return _dm if _dm else None


@register_worm(
    category="utility", priority=5, cooldown_seconds=60.0,
    description="Session state manager — auto-save/restore worm state across runs with versioned schemas"
)
class SessionStateManager(BaseWormMixin):
    """Centralized session state manager for all worms.

    Provides a pluggable save_state/load_state interface that any
    worm can register with. Supports:

    - State snapshots: save all registered worm states at once
    - State restoration: load and rehydrate worm instances
    - Versioned schemas: track state format versions per worm
    - Schema migration: transform state from v1 → v2 on worm upgrade
    - Cooldown-aware: respects per-worm cooldown intervals
    - Thread-safe: all operations use RLock

    Usage:
        ssm = SessionStateManager()

        # Register worm states
        ssm.register_worm_state(
            "MyWorm",
            get_state=lambda: worm._serialize(),
            set_state=lambda data: worm._hydrate(data),
            version="1.0.0",
        )

        # Snapshot all states
        snapshot_id = ssm.snapshot_all()

        # Restore on next run
        restored = ssm.restore_all()

        # Migrate a worm's state schema
        ssm.register_migration(
            "MyWorm", from_version="1.0.0", to_version="2.0.0",
            migrate_fn=lambda data: {**data, "new_field": 0}
        )
    """

    VERSION = "1.0.0"

    def __init__(self):
        self._lock = threading.RLock()
        self._registrations: dict[str, dict[str, Any]] = {}
        self._migrations: dict[str, dict[str, Callable]] = {}
        self._snapshots: list[dict] = []
        self._execution_count = 0
        self._last_snapshot_id: str = ""
        self._restore_count = 0

    # ── Registration ──────────────────────────────────────────────

    def register_worm_state(
        self,
        worm_name: str,
        get_state: Callable[[], dict[str, Any]],
        set_state: Callable[[dict[str, Any]], None],
        version: str = "1.0.0",
        cooldown_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Register a worm's state getter/setter for persistence.

        Args:
            worm_name: Unique worm identifier.
            get_state: Callable that returns serializable state dict.
            set_state: Callable that rehydrates worm from state dict.
            version: Schema version (used for migration).
            cooldown_seconds: Min interval between snapshots (0 = always).

        Returns:
            Dict with registration status.
        """
        with self._lock:
            self._registrations[worm_name] = {
                "get_state": get_state,
                "set_state": set_state,
                "version": version,
                "cooldown_seconds": cooldown_seconds,
                "last_snapshot_ts": 0.0,
                "migrated_count": 0,
            }

        dm = _get_dm()
        if dm:
            try:
                dm.record(
                    "worm_state_registered",
                    {"worm": worm_name, "version": version},
                    tags=["session_state", "registration"],
                )
            except Exception as exc:
                note_failure("session_state:L168", exc)

        return {
            "worm": worm_name,
            "version": version,
            "registered": True,
            "total_registered": len(self._registrations),
        }

    def unregister_worm_state(self, worm_name: str) -> dict[str, Any]:
        """Remove a worm's state registration."""
        with self._lock:
            removed = self._registrations.pop(worm_name, None)
        return {
            "worm": worm_name,
            "removed": removed is not None,
        }

    def list_registered(self) -> list[dict[str, str]]:
        """List all registered worms with their versions."""
        with self._lock:
            return [
                {"name": name, "version": reg["version"]}
                for name, reg in sorted(self._registrations.items())
            ]

    # ── Migration ──────────────────────────────────────────────────

    def register_migration(
        self,
        worm_name: str,
        from_version: str,
        to_version: str,
        migrate_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """Register a state migration from one schema version to another.

        Args:
            worm_name: Worm to migrate.
            from_version: Source schema version.
            to_version: Target schema version.
            migrate_fn: Transform function (old_state → new_state).

        Returns:
            Dict with migration registration status.
        """
        key = f"{worm_name}:{from_version}→{to_version}"
        with self._lock:
            if worm_name not in self._migrations:
                self._migrations[worm_name] = {}
            self._migrations[worm_name][key] = migrate_fn

        return {
            "worm": worm_name,
            "from": from_version,
            "to": to_version,
            "registered": True,
        }

    def migrate_state(
        self,
        worm_name: str,
        state: dict[str, Any],
        target_version: str,
    ) -> dict[str, Any]:
        """Migrate a worm's state through all registered migrations.

        Applies migrations in order: v1→v2→...→target_version.
        If no migrations exist for a step, state is passed through unchanged.

        Args:
            worm_name: Worm whose state to migrate.
            state: Current state dict (with _schema_version field).
            target_version: Desired schema version.

        Returns:
            Migrated state dict with updated _schema_version.
        """
        current = state.get("_schema_version", "1.0.0")
        result = dict(state)

        with self._lock:
            migrations = self._migrations.get(worm_name, {})

        while current != target_version:
            # Find a migration from current → next
            found = False
            for key, fn in sorted(migrations.items()):
                parts = key.split("→")
                if len(parts) == 2 and parts[0] == current:
                    result = fn(result)
                    result["_schema_version"] = parts[1]
                    current = parts[1]
                    found = True

                    with self._lock:
                        if worm_name in self._registrations:
                            self._registrations[worm_name]["migrated_count"] += 1
                    break

            if not found:
                # No migration path — accept as-is
                break

        return result

    # ── Snapshot ───────────────────────────────────────────────────

    def snapshot_worm(self, worm_name: str) -> dict[str, Any]:
        """Snapshot a single worm's state.

        Respects cooldown: if last snapshot was within cooldown_seconds,
        returns a cached result.

        Args:
            worm_name: Name of registered worm to snapshot.

        Returns:
            Dict with snapshot data or error.
        """
        with self._lock:
            reg = self._registrations.get(worm_name)
            if reg:
                cooldown = reg.get("cooldown_seconds", 0)
                last_ts = reg.get("last_snapshot_ts", 0)
            else:
                cooldown = 0
                last_ts = 0.0

        if not reg:
            return {"worm": worm_name, "error": "Not registered"}

        now = time.time()

        if cooldown > 0 and now - last_ts < cooldown:
            # Return last snapshot from history — unwrap to match normal format
            with self._lock:
                for snap in reversed(self._snapshots):
                    if snap.get("worm") == worm_name:
                        return dict(snap)  # already has {"worm", "data", "version", "timestamp"}

        try:
            state = reg["get_state"]()
        except Exception as e:
            note_failure("session_state:L312", e)
            return {"worm": worm_name, "error": str(e)}

        state["_schema_version"] = state.get("_schema_version", reg.get("version", "1.0.0"))
        state["_snapshot_ts"] = datetime.now().isoformat()
        state["_worm_name"] = worm_name

        snapshot = {
            "worm": worm_name,
            "version": reg.get("version", "1.0.0"),
            "timestamp": state["_snapshot_ts"],
            "data": state,
        }

        with self._lock:
            reg["last_snapshot_ts"] = now
            self._snapshots.append(snapshot)
            if len(self._snapshots) > 500:
                self._snapshots = self._snapshots[-200:]

        return snapshot

    def snapshot_all(self) -> dict[str, Any]:
        """Snapshot all registered worm states.

        Returns:
            Dict with snapshot_id, per-worm results, and summary.
        """
        start = time.perf_counter()
        snapshot_id = f"ss_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        results = {}

        with self._lock:
            names = list(self._registrations.keys())

        for name in names:
            results[name] = self.snapshot_worm(name)

        ok = sum(1 for r in results.values() if "error" not in r)
        fail = len(results) - ok

        # Persist to LocalMemoryStore
        store = _get_store()
        if store:
            try:
                store.record_event(
                    source="SessionStateManager",
                    event_type="snapshot_all",
                    data={
                        "snapshot_id": snapshot_id,
                        "worms": len(results),
                        "ok": ok,
                        "fail": fail,
                    },
                    severity="info",
                    tags=["session_state", "snapshot"],
                )
            except Exception as exc:
                note_failure("session_state:L369", exc)

        self._last_snapshot_id = snapshot_id

        return {
            "snapshot_id": snapshot_id,
            "timestamp": datetime.now().isoformat(),
            "worms_snapshot": len(results),
            "ok": ok,
            "fail": fail,
            "results": results,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
        }

    # ── Restore ────────────────────────────────────────────────────

    def restore_worm(self, worm_name: str) -> dict[str, Any]:
        """Restore a single worm from its most recent snapshot.

        Args:
            worm_name: Name of registered worm to restore.

        Returns:
            Dict with restore status.
        """
        with self._lock:
            reg = self._registrations.get(worm_name)
            target_version = reg.get("version", "1.0.0") if reg else "1.0.0"
            # Find most recent snapshot for this worm
            snapshot = None
            for snap in reversed(self._snapshots):
                if snap.get("worm") == worm_name and "error" not in snap.get("data", {}):
                    snapshot = snap
                    break

        if not reg:
            return {"worm": worm_name, "error": "Not registered"}
        if not snapshot:
            return {"worm": worm_name, "error": "No snapshot available"}

        state = snapshot.get("data", {})
        state_version = state.get("_schema_version", "1.0.0")

        # Migrate if schema versions differ
        if state_version != target_version:
            try:
                state = self.migrate_state(worm_name, state, target_version)
            except Exception as e:
                note_failure("session_state:L417", e)
                return {"worm": worm_name, "error": f"Migration failed: {e}"}

        try:
            reg["set_state"](state)
        except Exception as e:
            note_failure("session_state:L422", e)
            return {"worm": worm_name, "error": str(e)}

        self._restore_count += 1

        return {
            "worm": worm_name,
            "restored": True,
            "from_version": state_version,
            "to_version": target_version,
            "snapshot_ts": snapshot.get("timestamp", ""),
        }

    def restore_all(self) -> dict[str, Any]:
        """Restore all registered worms from their most recent snapshots.

        Returns:
            Dict with per-worm restore results and summary.
        """
        start = time.perf_counter()
        results = {}

        with self._lock:
            names = list(self._registrations.keys())

        for name in names:
            results[name] = self.restore_worm(name)

        ok = sum(1 for r in results.values() if r.get("restored"))
        fail = len(results) - ok

        # Persist
        store = _get_store()
        if store:
            try:
                store.record_event(
                    source="SessionStateManager",
                    event_type="restore_all",
                    data={"worms": len(results), "ok": ok, "fail": fail},
                    severity="info" if fail == 0 else "warning",
                    tags=["session_state", "restore"],
                )
            except Exception as exc:
                note_failure("session_state:L464", exc)

        return {
            "worms_restored": len(results),
            "ok": ok,
            "fail": fail,
            "results": results,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
        }

    # ── Persistence to disk ────────────────────────────────────────

    def export_snapshots(self, filepath: str = "") -> str:
        """Export all snapshots to a JSON file.

        Args:
            filepath: Output path (default: ~/.azazael/session_state.json).

        Returns:
            File path written.
        """
        if not filepath:
            filepath = str(Path.home() / ".azazael" / "session_state.json")

        Path(filepath).parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            snapshots = list(self._snapshots)
            registrations = {
                name: {"version": r["version"], "cooldown_seconds": r["cooldown_seconds"]}
                for name, r in self._registrations.items()
            }

        export = {
            "exported_at": datetime.now().isoformat(),
            "manager_version": self.VERSION,
            "registrations": registrations,
            "snapshot_count": len(snapshots),
            "snapshots": snapshots,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(export, f, indent=2, default=str)

        return filepath

    def import_snapshots(self, filepath: str) -> dict[str, Any]:
        """Import snapshots from a JSON file.

        Args:
            filepath: Path to session_state.json.

        Returns:
            Dict with import counts.
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        snapshots = data.get("snapshots", [])
        with self._lock:
            self._snapshots.extend(snapshots)
            if len(self._snapshots) > 500:
                self._snapshots = self._snapshots[-200:]

        return {
            "imported": len(snapshots),
            "total_snapshots": len(self._snapshots),
            "source_file": filepath,
        }

    # ── Status ─────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Get SessionStateManager status."""
        with self._lock:
            return {
                "version": self.VERSION,
                "registrations": len(self._registrations),
                "snapshots": len(self._snapshots),
                "last_snapshot_id": self._last_snapshot_id,
                "restore_count": self._restore_count,
                "registered_worms": sorted(self._registrations.keys()),
            }

    # ── Standard worm lifecycle ────────────────────────────────────

    def execute(self) -> dict[str, Any]:
        """Standard worm execute() — snapshot all and report.

        Runs a full snapshot cycle, persists results, and returns
        a summary of registered and snapshotted worms.
        """
        start = time.perf_counter()
        self._execution_count += 1

        result = self.snapshot_all()

        # Persist snapshot file
        try:
            self.export_snapshots()
        except Exception as exc:
            note_failure("session_state:L565", exc)

        dm = _get_dm()
        if dm:
            try:
                dm.record(
                    "session_state_executed",
                    {
                        "execution": self._execution_count,
                        "worms": result.get("worms_snapshot", 0),
                        "ok": result.get("ok", 0),
                        "fail": result.get("fail", 0),
                    },
                    tags=["session_state", "execute"],
                )
            except Exception as exc:
                note_failure("session_state:L581", exc)

        result["execution_count"] = self._execution_count
        result["manager_version"] = self.VERSION

        return result

    def get_summary(self):
        return {"status": "ok", "worm": "SessionStateManager"}
