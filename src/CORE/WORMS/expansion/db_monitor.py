# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: MONITOR (Database Monitor Worm)

DatabaseMonitorWorm — monitors LocalMemoryStore health, size, growth, and error trends.
Records monitoring snapshots back to LocalMemoryStore for dashboard visibility.

Usage:
    monitor = DatabaseMonitorWorm()
    report = monitor.execute()  # Single health check
    monitor.start_monitor()     # Continuous monitoring (daemon thread)

Features:
- DB size and memory count tracking
- Daily growth rate analysis
- Error/warning trend detection
- Session activity summary
- Auto-vacuum status check
- PostgreSQL adapter support (optional)
"""

import sys
import os
import time
import random
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, replace
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Module-level integrity cache -------------------------------------
# PRAGMA integrity_check on a large SQLite DB takes seconds; check_health()
# runs it on every call and dashboard/health endpoints construct a fresh
# DatabaseMonitorWorm per request, so the cache lives at module scope.
_INTEGRITY_CACHE: (dict | None) = None
_INTEGRITY_CACHE_TIME: float = 0.0
_INTEGRITY_CACHE_TTL: float = 60.0


# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("db_monitor:L44", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5,
                      cooldown_seconds: float = 30.0, description: str = "",
                      tags: Optional[Set[str]] = None):
        def _identity(cls):
            return cls
        return _identity

# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded
    _FREETHREAD = True
except ImportError as exc:
    note_failure("db_monitor:L53", exc)
    _FREETHREAD = False
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("db_monitor:L59", exc)
            return False


@dataclass
class DbHealthSnapshot:
    """Snapshot of database health at a point in time."""
    timestamp: str
    db_size_mb: float
    total_memories: int
    total_sessions: int
    memory_growth_24h: int
    error_count_7d: int
    warning_count_7d: int
    sources_count: int
    event_types_count: int
    auto_vacuum_running: bool
    current_session: str
    storage_backend: str  # "sqlite" or "postgres"
    healthy: bool
    # Additive 2026-10-17: critical-severity 7d count was invisible to health.
    # Trailing + default so all existing positional/keyword constructions keep working.
    critical_count_7d: int = 0


@dataclass
class DbAlert:
    """Alert generated from database monitoring."""
    alert_id: str
    severity: str  # "info", "warning", "critical"
    message: str
    metric: str
    value: float
    threshold: float
    timestamp: str


class DatabaseMonitorWorm:
    """Monitors LocalMemoryStore health, size, growth, and error trends.

    Produces periodic snapshots of database health metrics and generates
    alerts when thresholds are breached. All snapshots are recorded back
    to LocalMemoryStore for dashboard visibility.

    Usage:
        monitor = DatabaseMonitorWorm()
        report = monitor.execute()  # Single health check
        monitor.start_monitor()     # Continuous monitoring (daemon thread)
    """

    VERSION = "1.0.0"

    # Alert thresholds
    WARN_DB_SIZE_MB: float = 100.0
    CRITICAL_DB_SIZE_MB: float = 500.0
    WARN_GROWTH_24H: int = 10000
    CRITICAL_GROWTH_24H: int = 50000
    WARN_ERRORS_7D: int = 50
    CRITICAL_ERRORS_7D: int = 200
    # Repeat-alert cooldown (2026-09-08 fix): the same metric+severity is
    # recorded at most once per cooldown window. Previously every execute()
    # re-recorded the whole in-memory alert history AND fresh instances
    # re-fired permanent breaches, compounding into ~200 alert rows/hour.
    ALERT_COOLDOWN_S: float = 3600.0
    # Sources excluded from real-error counts (2026-09-08 fix): the monitor
    # counting its own alert/snapshot rows made thresholds self-referential
    # (alerts bred alerts). Same lesson as the evolution eval pools.
    _SELF_EXCLUDE_SOURCES: list = ["DatabaseMonitorWorm"]

    def __init__(self, use_postgres: bool = False, store: Any = None):
        self._use_postgres = use_postgres
        self._injected_store = store
        self._lock = threading.Lock()
        self._monitor_thread: (threading.Thread | None) = None
        self._stop_event = threading.Event()
        self._snapshot_history: list[DbHealthSnapshot] = []
        self._alerts: list[DbAlert] = []
        self._last_growth_baseline: (int | None) = None
        self._last_check_time: (float | None) = None

    # -- Lazy Store Loader ------------------------------------------

    def _get_store(self):
        """Get the database store.

        Resolution order:
        1. Injected store (for testing)
        2. PostgreSQL (if use_postgres=True and available)
        3. Global LocalMemoryStore singleton
        """
        if self._injected_store is not None:
            return self._injected_store
        if self._use_postgres:
            try:
                from CORE.postgres_memory import PostgresMemoryStore
                return PostgresMemoryStore()
            except (ImportError, Exception) as exc:
                note_failure("db_monitor:L152", exc) # Fall through to SQLite
        try:
            from CORE.local_memory import get_memory_store
            return get_memory_store()
        except ImportError as exc:
            note_failure("db_monitor:L157", exc)
            return None

    def _get_store_backend(self) -> str:
        """Return the storage backend name."""
        if self._use_postgres:
            return "postgres"
        return "sqlite"

    # -- Integrity & Backup -----------------------------------------

    def check_integrity(self) -> dict[str, Any]:
        """Run database integrity check and return results.

        Calls the store's integrity_check() method if available.
        Returns a diagnostic dict whether or not the store supports it.
        """
        store = self._get_store()
        if not store:
            return {"passed": False, "ok": False, "errors": ["No store available"]}

        check_fn = getattr(store, "integrity_check", None)
        if not check_fn:
            return {"passed": True, "ok": True, "errors": [], "note": "Store does not support integrity_check"}

        # Serve from module-level cache while fresh. PRAGMA integrity_check on
        # a large SQLite DB (e.g. 1+ GB) takes seconds, and check_health() runs
        # it on every call — the dashboard/health endpoints construct a fresh
        # DatabaseMonitorWorm per request, so the cache must be shared at
        # module scope. TTL matches the worm's 60s cooldown.
        global _INTEGRITY_CACHE, _INTEGRITY_CACHE_TIME
        now = time.time()
        if (_INTEGRITY_CACHE is not None
                and now - _INTEGRITY_CACHE_TIME < _INTEGRITY_CACHE_TTL):
            return dict(_INTEGRITY_CACHE)

        try:
            result = check_fn()
            _INTEGRITY_CACHE = dict(result)
            _INTEGRITY_CACHE_TIME = now
            return dict(result)
        except Exception as e:
            note_failure("db_monitor:L198", e)
            return {"passed": False, "ok": False, "errors": [str(e)]}

    def create_backup(self, target_dir: Optional[str] = None) -> str:
        """Create a database backup.

        Args:
            target_dir: Directory for the backup file. Defaults to store's parent directory.

        Returns:
            Path to the backup file, or empty string if backup fails.
        """
        store = self._get_store()
        if not store:
            return ""

        backup_fn = getattr(store, "backup", None)
        if not backup_fn:
            return ""

        if not target_dir:
            db_path = getattr(store, "db_path", None)
            if db_path:
                target_dir = str(Path(db_path).parent)
            else:
                target_dir = str(Path.home() / ".azazael")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backend = self._get_store_backend()
        ext = "sql" if backend == "postgres" else "db"
        target_path = str(Path(target_dir) / f"memory_backup_{ts}.{ext}")

        try:
            result = backup_fn(target_path)
            self._record_backup_event(target_path, True)
            return result
        except Exception as e:
            note_failure("db_monitor:L234", e)
            self._record_backup_event(target_path, False, str(e))
            return ""

    def _record_backup_event(self, path: str, success: bool, error: Optional[str] = None):
        """Record a backup event to memory."""
        store = self._get_store()
        if not store:
            return
        try:
            store.record_event(
                source="DatabaseMonitorWorm",
                event_type="db_backup",
                data={"path": path, "success": success, "error": error},
                severity="info" if success else "error",
                tags=["db_monitor", "backup"],
            )
        except Exception as exc:
            note_failure("db_monitor:L251", exc)

    # -- Core Monitoring Logic --------------------------------------

    def check_health(self) -> DbHealthSnapshot:
        """Take a single health snapshot of the database.

        Returns:
            DbHealthSnapshot with current metrics.
        """
        store = self._get_store()
        if not store:
            return DbHealthSnapshot(
                timestamp=datetime.now(timezone.utc).isoformat(),
                db_size_mb=0.0, total_memories=0, total_sessions=0,
                memory_growth_24h=0, error_count_7d=0, warning_count_7d=0,
                critical_count_7d=0,
                sources_count=0, event_types_count=0,
                auto_vacuum_running=False, current_session="",
                storage_backend=self._get_store_backend(), healthy=False,
            )

        try:
            stats = store.get_stats()

            # Get recent error/warning counts. Exact COUNT(*) totals
            # (2026-09-08 fix): the sampled lists are fetch-capped, so
            # thresholds must use the exact totals. Self-source excluded
            # so alerts can't breed alerts.
            # Dual-backend signatures: LocalMemoryStore.query_real_errors
            # accepts exclude_sources; PostgresMemoryStore.query_real_errors
            # does not (it has no per-source filter yet). Build the kwargs
            # dynamically so the postgres path degrades to unfiltered instead
            # of raising TypeError, and the local path keeps self-exclusion.
            query_kwargs: Dict[str, Any] = {"days": 7, "limit": 500}
            if self._get_store_backend() != "postgres":
                query_kwargs["exclude_sources"] = self._SELF_EXCLUDE_SOURCES
            real = store.query_real_errors(**query_kwargs)
            error_count = real.get("total_errors_exact", real.get("total_errors", 0))
            warning_count = real.get("total_warnings_exact", real.get("total_warnings", 0))
            # Additive 2026-10-17: criticals were invisible to health; .get keeps
            # old stores/dicts (no key) reading as 0.
            critical_count = real.get("total_criticals_exact", real.get("total_criticals", 0))

            # Get daily growth (actual 24h count, not a projection)
            growth = store.get_db_growth(days=1)
            growth_24h = growth[-1]["count"] if growth else 0

            # Source and event type diversity
            sources = stats.get("sources", {})
            event_types = stats.get("event_types", {})

            # Auto-vacuum status
            auto_vacuum = getattr(store, "auto_vacuum_status", {})
            vacuum_running = auto_vacuum.get("running", False) if isinstance(auto_vacuum, dict) else False

            # Integrity check (run silently, capture any issues)
            integrity = self.check_integrity()
            integrity_ok = integrity.get("ok", False)

            # Determine health
            healthy = True
            if stats.get("db_size_mb", 0) >= self.WARN_DB_SIZE_MB:
                healthy = False
            if error_count >= self.WARN_ERRORS_7D:
                healthy = False
            if critical_count > 0:
                healthy = False  # additive 2026-10-17: any critical is unhealthy
            if not integrity_ok:
                healthy = False  # DB corruption always marks unhealthy

            snapshot = DbHealthSnapshot(
                timestamp=datetime.now(timezone.utc).isoformat(),
                db_size_mb=stats.get("db_size_mb", 0),
                total_memories=stats.get("total_memories", 0),
                total_sessions=stats.get("total_sessions", 0),
                memory_growth_24h=growth_24h,
                error_count_7d=error_count,
                warning_count_7d=warning_count,
                critical_count_7d=critical_count,
                sources_count=len(sources),
                event_types_count=len(event_types),
                auto_vacuum_running=vacuum_running,
                current_session=stats.get("current_session", ""),
                storage_backend=self._get_store_backend(),
                healthy=healthy,
            )

            # Growth-rate alerts on the ACTUAL 24h count (2026-09-08
            # fix): the old single-interval projection turned any burst
            # into absurd ~/day figures (e.g. 770K/day) that stayed
            # breached for as long as training loops spewed rows.
            if growth_24h >= self.CRITICAL_GROWTH_24H:
                self._add_alert(
                    severity="critical",
                    message=f"High memory growth in last 24h: {growth_24h} rows/day",
                    metric="growth_24h_actual",
                    value=growth_24h,
                    threshold=self.CRITICAL_GROWTH_24H,
                )
            elif growth_24h >= self.WARN_GROWTH_24H:
                self._add_alert(
                    severity="warning",
                    message=f"Elevated memory growth in last 24h: {growth_24h} rows/day",
                    metric="growth_24h_actual",
                    value=growth_24h,
                    threshold=self.WARN_GROWTH_24H,
                )

            # Check DB size thresholds
            db_size = stats.get("db_size_mb", 0)
            if db_size >= self.CRITICAL_DB_SIZE_MB:
                self._add_alert(
                    severity="critical",
                    message=f"Database size {db_size:.1f} MB exceeds critical threshold",
                    metric="db_size_mb", value=db_size, threshold=self.CRITICAL_DB_SIZE_MB,
                )
            elif db_size >= self.WARN_DB_SIZE_MB:
                self._add_alert(
                    severity="warning",
                    message=f"Database size {db_size:.1f} MB exceeds warning threshold",
                    metric="db_size_mb", value=db_size, threshold=self.WARN_DB_SIZE_MB,
                )

            # Critical-severity rows get their own alert (additive 2026-10-17):
            # previously they were counted nowhere, so a host-down storm was silent.
            if critical_count > 0:
                self._add_alert(
                    severity="critical",
                    message=f"{critical_count} critical-severity rows in last 7 days",
                    metric="critical_count_7d", value=critical_count, threshold=1,
                )

            # Check error thresholds
            if error_count >= self.CRITICAL_ERRORS_7D:
                self._add_alert(
                    severity="critical",
                    message=f"{error_count} errors in last 7 days exceeds critical threshold",
                    metric="error_count_7d", value=error_count, threshold=self.CRITICAL_ERRORS_7D,
                )
            elif error_count >= self.WARN_ERRORS_7D:
                self._add_alert(
                    severity="warning",
                    message=f"{error_count} errors in last 7 days exceeds warning threshold",
                    metric="error_count_7d", value=error_count, threshold=self.WARN_ERRORS_7D,
                )

            # Alert if integrity check failed
            if not integrity_ok:
                for err in integrity.get("errors", []):
                    self._add_alert(
                        severity="critical",
                        message=f"Database integrity failure: {err}",
                        metric="integrity_check", value=0, threshold=0,
                    )

            # Store snapshot in history
            with self._lock:
                self._snapshot_history.append(snapshot)
                if len(self._snapshot_history) > 100:
                    self._snapshot_history = self._snapshot_history[-100:]

            return snapshot

        except Exception as exc:
            note_failure("db_monitor:L394", exc)
            return DbHealthSnapshot(
                timestamp=datetime.now(timezone.utc).isoformat(),
                db_size_mb=0.0, total_memories=0, total_sessions=0,
                memory_growth_24h=0, error_count_7d=0, warning_count_7d=0,
                critical_count_7d=0,
                sources_count=0, event_types_count=0,
                auto_vacuum_running=False, current_session="",
                storage_backend=self._get_store_backend(), healthy=False,
            )

    def _add_alert(self, severity: str, message: str, metric: str,
                   value: float, threshold: float):
        """Add an alert to the history."""
        alert = DbAlert(
            alert_id=f"alert_{int(time.time())}_{random.randint(100, 999)}",
            severity=severity, message=message, metric=metric,
            value=value, threshold=threshold,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._alerts.append(alert)
            if len(self._alerts) > 100:
                self._alerts = self._alerts[-100:]

    # -- Learned-policy advice (advisory stage, 2026-10-03) --------------

    def _policy_advice(self, severity: str, source: str = "db") -> dict:
        """Attach learned-policy advice WITHOUT changing firing.

        Pure query: delegates to RLTrainLoop.recommend (itself read-only
        and never-raising) and returns its envelope. No alerts are
        recorded, no cooldowns touched, no thresholds moved — a wrong
        table therefore cannot trigger anything by itself. Works on a
        partially constructed worm: any failure degrades to unknown.
        """
        try:
            loop = getattr(self, "_rl_loop", None)
            if loop is None:
                try:
                    from CORE.WORMS.expansion.learning_rl import RLTrainLoop
                    loop = RLTrainLoop()
                except Exception as exc:
                    note_failure("db_monitor:L435", exc)
                    loop = None
            if loop is None:
                raise RuntimeError("no policy loop available")
            out = loop.recommend(severity, source=source)
            if out.get("action_name") not in (
                    "ignore", "log", "alert", "remediate", "unknown"):
                out["action_name"] = "unknown"
            return out
        except Exception as exc:
            note_failure("db_monitor:L444", exc)
            return {"action": -1, "action_name": "unknown", "known": False,
                    "state_key": "%s:%s" % (source, severity),
                    "source": source}

    #: The ONE learned action promoted to actuation. The policy may only
    #: ever ADD an alert row, never suppress one: ignore/log/remediate stay
    #: advisory-only. Suppression hides incidents and remediation acts on
    #: the world -- neither is earned by a pool that has never seen a
    #: failure (ok_rate=1.0 live). An actuated row is cooldown-deduped on
    #: its own policy: metric key and tagged policy_actuated, so a chatty
    #: table cannot flood the store.
    POLICY_ACTUATED_ACTION = "alert"

    def _alert_report(self, alert: DbAlert) -> dict:
        """Report dict for one alert, with learned-policy advice attached.

        The advice call is the advisory half of the promotion: every alert
        now carries what the table thought, whether or not the gate below
        lets it act. _policy_advice never raises (unknown on failure), so
        this cannot break the report.
        """
        return {
            "alert_id": alert.alert_id,
            "severity": alert.severity,
            "message": alert.message,
            "metric": alert.metric,
            "value": alert.value,
            "threshold": alert.threshold,
            "timestamp": alert.timestamp,
            "policy_advice": self._policy_advice(alert.severity),
        }

    def _maybe_policy_alert(self, alert: DbAlert) -> bool:
        """Let the learned policy raise ONE additive alert row, gated.

        Gate (all required): kill-switch MONITOR_POLICY_ACTUATION != "0";
        advice known=True; advised action exactly POLICY_ACTUATED_ACTION.
        The extra row reuses _record_alert, so base alert recording is
        untouched. Fail-open: any failure skips the extra row, noted.
        Returns True when the extra row was recorded.
        """
        try:
            if os.environ.get("MONITOR_POLICY_ACTUATION", "1") == "0":
                return False
            advice = self._policy_advice(alert.severity)
            if not advice.get("known", False):
                return False
            if advice.get("action_name") != self.POLICY_ACTUATED_ACTION:
                return False
            extra = replace(
                alert,
                alert_id="policy_%s" % alert.alert_id,
                metric="policy:%s" % alert.metric,
                message="[policy:%s] %s" % (advice.get("action_name"),
                                            alert.message),
            )
            self._record_alert(
                extra, extra_tags=("policy_actuated",),
                extra_data={"policy_state": advice.get("state_key"),
                            "policy_action": advice.get("action_name")})
            return True
        except Exception as e:
            note_failure("db_monitor:policy-actuation", e)
            return False

    # -- Recording to Memory ----------------------------------------

    def _record_snapshot(self, snapshot: DbHealthSnapshot):
        """Record a health snapshot to LocalMemoryStore."""
        store = self._get_store()
        if not store:
            return
        try:
            store.record_event(
                source="DatabaseMonitorWorm",
                event_type="db_health_snapshot",
                data={
                    "db_size_mb": snapshot.db_size_mb,
                    "total_memories": snapshot.total_memories,
                    "total_sessions": snapshot.total_sessions,
                    "memory_growth_24h": snapshot.memory_growth_24h,
                    "error_count_7d": snapshot.error_count_7d,
                    "warning_count_7d": snapshot.warning_count_7d,
                    "sources_count": snapshot.sources_count,
                    "event_types_count": snapshot.event_types_count,
                    "auto_vacuum_running": snapshot.auto_vacuum_running,
                    "storage_backend": snapshot.storage_backend,
                    "healthy": snapshot.healthy,
                },
                severity="info" if snapshot.healthy else "warning",
                tags=["db_monitor", "health", "snapshot"],
            )
        except Exception as exc:
            note_failure("db_monitor:L476", exc)

    def _recently_alerted(self, store: Any, metric: str, severity: str) -> bool:
        """True when the same metric+severity was recorded within cooldown.

        Store-backed (2026-09-08 fix) so it works across fresh worm
        instances, not just within one process's memory. Integrity
        failures bypass the cooldown — corruption must always re-alert.
        """
        if metric == "integrity_check":
            return False
        try:
            rows = store.query_memories(
                source="DatabaseMonitorWorm", event_type="db_health_alert",
                limit=30)
        except Exception as exc:
            note_failure("db_monitor:L492", exc)
            return False
        now = datetime.now(timezone.utc)
        local_tz = datetime.now().astimezone().tzinfo
        for r in rows:
            try:
                data = r.get("data", {}) or {}
                if isinstance(data, str):
                    import json as _json
                    data = _json.loads(data)
                if data.get("metric") != metric or r.get("severity") != severity:
                    continue
                ts = datetime.fromisoformat(str(r.get("timestamp", "")))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=local_tz)
                if (now - ts).total_seconds() < self.ALERT_COOLDOWN_S:
                    return True
            except Exception as exc:
                note_failure("db_monitor:L509", exc)
                continue
        return False

    def _record_alert(self, alert: DbAlert, extra_tags: tuple = (),
                      extra_data: (dict | None) = None):
        """Record an alert to LocalMemoryStore (cooldown-deduped).

        extra_tags/extra_data ride along for policy-actuated rows; both
        default empty so every existing caller is unaffected.
        """
        store = self._get_store()
        if not store:
            return
        try:
            if self._recently_alerted(store, alert.metric, alert.severity):
                return
            data = {
                "alert_id": alert.alert_id,
                "severity": alert.severity,
                "message": alert.message,
                "metric": alert.metric,
                "value": alert.value,
                "threshold": alert.threshold,
            }
            data.update(extra_data or {})
            store.record_event(
                source="DatabaseMonitorWorm",
                event_type="db_health_alert",
                data=data,
                severity=alert.severity,
                tags=["db_monitor", "alert", alert.severity,
                      *extra_tags],
            )
        except Exception as exc:
            note_failure("db_monitor:L535", exc)

    # -- Execute (Single Run) ---------------------------------------

    def execute(self) -> dict[str, Any]:
        """Execute a single database health check.

        Standard worm execute() interface. Called by ClusterWORMZ.

        Returns:
            dict with health snapshot, alerts, and status.
        """
        snapshot = self.check_health()

        # Record to memory
        self._record_snapshot(snapshot)

        # Record any new alerts, then DRAIN the pending list (2026-09-08
        # fix): previously the list was only copied, so every execute()
        # re-recorded the entire accumulated history as new rows.
        with self._lock:
            pending_alerts = list(self._alerts)
            self._alerts.clear()
        for alert in pending_alerts:
            self._record_alert(alert)
            self._maybe_policy_alert(alert)

        # Build rich report
        return {
            "version": self.VERSION,
            "timestamp": snapshot.timestamp,
            "healthy": snapshot.healthy,
            "db_size_mb": snapshot.db_size_mb,
            "total_memories": snapshot.total_memories,
            "total_sessions": snapshot.total_sessions,
            "memory_growth_24h": snapshot.memory_growth_24h,
            "error_count_7d": snapshot.error_count_7d,
            "warning_count_7d": snapshot.warning_count_7d,
            "sources_count": snapshot.sources_count,
            "event_types_count": snapshot.event_types_count,
            "auto_vacuum_running": snapshot.auto_vacuum_running,
            "storage_backend": snapshot.storage_backend,
            "alerts": [self._alert_report(a) for a in pending_alerts],
            "total_alerts": len(pending_alerts),
            "free_threaded": is_free_threaded(),
        }

    # -- Continuous Monitoring --------------------------------------

    def start_monitor(self, interval_seconds: float = 3600.0):
        """Start continuous database monitoring in a background daemon thread.

        Args:
            interval_seconds: How often to check (default: hourly).
        """
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._stop_event.clear()

        def _monitor_loop():
            while not self._stop_event.is_set():
                self.execute()
                # Sleep with stop-event checking
                deadline = time.time() + interval_seconds
                while time.time() < deadline:
                    if self._stop_event.wait(timeout=5):
                        return

        self._monitor_thread = threading.Thread(
            target=_monitor_loop, daemon=True, name="azazael-db-monitor"
        )
        self._monitor_thread.start()

    def stop_monitor(self):
        """Stop the continuous monitor gracefully."""
        self._stop_event.set()

    def is_monitor_running(self) -> bool:
        """Check if the monitor thread is alive."""
        return self._monitor_thread is not None and self._monitor_thread.is_alive()

    # -- Reporting --------------------------------------------------

    def get_report(self) -> dict[str, Any]:
        """Get a detailed report of recent monitoring history.

        Returns:
            dict with current status, snapshot history, and alerts.
        """
        with self._lock:
            recent_snapshots = self._snapshot_history[-10:] if self._snapshot_history else []
            recent_alerts = self._alerts[-20:] if self._alerts else []

        return {
            "version": self.VERSION,
            "monitor_running": self.is_monitor_running(),
            "total_snapshots": len(self._snapshot_history),
            "total_alerts": len(self._alerts),
            "thresholds": {
                "warn_db_size_mb": self.WARN_DB_SIZE_MB,
                "critical_db_size_mb": self.CRITICAL_DB_SIZE_MB,
                "warn_growth_24h": self.WARN_GROWTH_24H,
                "critical_growth_24h": self.CRITICAL_GROWTH_24H,
                "warn_errors_7d": self.WARN_ERRORS_7D,
                "critical_errors_7d": self.CRITICAL_ERRORS_7D,
            },
            "recent_snapshots": [
                {
                    "timestamp": s.timestamp,
                    "db_size_mb": s.db_size_mb,
                    "total_memories": s.total_memories,
                    "healthy": s.healthy,
                    "criticals_7d": getattr(s, "critical_count_7d", 0),
                }
                for s in recent_snapshots
            ],
            "recent_alerts": [
                {
                    "alert_id": a.alert_id,
                    "severity": a.severity,
                    "message": a.message,
                    "metric": a.metric,
                    "value": a.value,
                    "timestamp": a.timestamp,
                }
                for a in recent_alerts
            ],
            "free_threaded": is_free_threaded(),
        }

    def get_db_summary(self) -> dict[str, Any]:
        """Get a concise summary of database health (for dashboard/CLI use).

        Returns:
            dict with key health indicators.
        """
        snapshot = self.check_health()
        return {
            "status": "HEALTHY" if snapshot.healthy else "ISSUES DETECTED",
            "backend": snapshot.storage_backend,
            "size_mb": snapshot.db_size_mb,
            "memories": snapshot.total_memories,
            "sessions": snapshot.total_sessions,
            "growth_24h": snapshot.memory_growth_24h,
            "errors_7d": snapshot.error_count_7d,
            "warnings_7d": snapshot.warning_count_7d,
            "criticals_7d": getattr(snapshot, "critical_count_7d", 0),
            "sources": snapshot.sources_count,
            "event_types": snapshot.event_types_count,
            "auto_vacuum": snapshot.auto_vacuum_running,
            "timestamp": snapshot.timestamp,
            "alerts_pending": len(self._alerts),
        }


# -- Register as a worm ----------------------------------------------
if _REGISTRY:
    DatabaseMonitorWorm = register_worm(
        category="monitor", priority=6, cooldown_seconds=60.0,
        description="Database health monitor — tracks LocalMemoryStore size, growth, error trends, and generates alerts with configurable thresholds"
    )(DatabaseMonitorWorm)


# -- Self-test ---------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  DatabaseMonitorWorm — Self-test")
    print("=" * 60)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "src"))

    monitor = DatabaseMonitorWorm()

    # Test 1: Execute health check
    print("\n  [1] Running health check...")
    result = monitor.execute()
    print(f"      Healthy: {result['healthy']}")
    print(f"      DB Size: {result['db_size_mb']:.2f} MB")
    print(f"      Memories: {result['total_memories']}")
    print(f"      Sessions: {result['total_sessions']}")
    print(f"      Growth 24h: {result['memory_growth_24h']}")
    print(f"      Errors 7d: {result['error_count_7d']}")
    print(f"      Backend: {result['storage_backend']}")
    print(f"      Alerts: {result['total_alerts']}")

    # Test 2: Summary
    print("\n  [2] DB Summary:")
    summary = monitor.get_db_summary()
    print(f"      Status: {summary['status']}")
    print(f"      Backend: {summary['backend']}")
    print(f"      Size: {summary['size_mb']:.2f} MB")
    print(f"      Memories: {summary['memories']}")

    # Test 3: Report
    print("\n  [3] Full Report:")
    report = monitor.get_report()
    print(f"      Monitor running: {report['monitor_running']}")
    print(f"      Total snapshots: {report['total_snapshots']}")
    print(f"      Total alerts: {report['total_alerts']}")

    # Test 4: Quick start/stop monitor
    print("\n  [4] Quick start/stop monitor...")
    monitor.start_monitor(interval_seconds=99999)
    print(f"      Running: {monitor.is_monitor_running()}")
    monitor.stop_monitor()
    print(f"      Stopped: {not monitor.is_monitor_running()}")

    print("\n" + "=" * 60)
    print("  Self-test complete.")
    print("=" * 60)
