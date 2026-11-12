# coding: utf-8
"""


__all__ = [
    "get_report",
    "main",
    "render_config",
    "render_db_health",
    "render_db_monitor",
    "render_dead_letters",
    "render_drift",
    "render_header",
    "render_health",
    "render_meta",
    "render_metrics",
    "render_scheduling",
    "render_session",
    "render_subheader",
    "render_workflows",
    "run_dashboard",
]

CORE/dashboard.py — CLI Observability Dashboard for Azazael.

Displays health check status, metrics snapshots, drift alerts,
and recent session activity in a clean terminal format.

Usage:
    python -m CORE.dashboard                 # Full overview
    python -m CORE.dashboard --health        # Health only
    python -m CORE.dashboard --metrics       # Metrics only
    python -m CORE.dashboard --drift         # Drift only
    python -m CORE.dashboard --watch         # Live-repeating view (every 5s)
    python -m CORE.dashboard --watch --interval 10  # Custom interval
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _ensure_imports():
    """Ensure all dashboard dependencies are importable."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))


# ═══════════════════════════════════════════════════════════════════════
# DASHBOARD RENDERERS
# ═══════════════════════════════════════════════════════════════════════

def render_header(title: str):
    """Render a section header."""
    width = 60
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def render_subheader(text: str):
    """Render a subsection header."""
    print(f"  -- {text}")


def _parse_since(since_str: (str | None)) -> (float | None):
    """Parse a --since time string into a Unix timestamp.

    Supported formats:
        - "24h" (hours)
        - "30m" (minutes)
        - "3600s" (seconds)
        - "2026-01-15" (date, start of day UTC)
        - "2026-01-15T10:30:00" (ISO datetime)
    Returns None if no filter, or a Unix timestamp.
    """
    if not since_str:
        return None
    s = since_str.strip()
    now = time.time()
    try:
        if s.endswith("h"):
            return now - float(s[:-1]) * 3600
        elif s.endswith("m"):
            return now - float(s[:-1]) * 60
        elif s.endswith("s"):
            return now - float(s[:-1])
        elif "T" in s:
            return datetime.fromisoformat(s).timestamp()
        else:
            return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError) as exc:
        note_failure("dashboard:L97", exc)
        return None


def get_report(since: (str | None) = None) -> dict[str, Any]:
    """Get full observability report as a dict.

    Args:
        since: Optional time filter (e.g., "24h", "30m", "2026-01-15")
    """
    since_ts = _parse_since(since)
    report: dict[str, Any] = {}
    report["timestamp"] = datetime.now().isoformat()
    if since:
        report["since"] = since

    try:
        from CORE.metrics import health_check
        report["health"] = health_check.report()
    except Exception as e:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L116", e)
        report["health"] = {"overall": "down", "error": str(e)}

    # The mesh is part of this report now (TODO P2 item 9): `--json`, the
    # Prometheus/health server and the web dashboard all read `get_report()`, so
    # one entry here makes the mesh visible to every one of them at once.
    report["mesh"] = _mesh_report()

    try:
        from CORE.metrics import metrics_collector
        snap = metrics_collector.snapshot()
        # Filter metrics by time if since is set
        if since_ts:
            filtered = {}
            for name, agg in snap.items():
                points = metrics_collector.query(name)
                recent = [p for p in points if p.timestamp >= since_ts]
                if recent:
                    vals = [p.value for p in recent]
                    filtered[name] = {
                        "count": len(vals),
                        "sum": sum(vals),
                        "avg": sum(vals) / len(vals),
                        "min": min(vals),
                        "max": max(vals),
                    }
            snap = filtered
        report["metrics"] = snap
    except Exception as e:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L144", e)
        report["metrics"] = {"error": str(e)}

    try:
        from CORE.metrics import drift_alert
        issues = drift_alert.check()
        if since_ts:
            # Drift issues don't carry timestamps; just report recent count
            issues = issues[:max(0, len(issues))]
        report["drift"] = [{"type": i.issue_type, "severity": i.severity, "message": i.message}
                          for i in issues]
    except Exception as e:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L155", e)
        report["drift"] = {"error": str(e)}

    try:
        from CORE.circuit_breaker import breaker_registry
        report["dead_letters"] = breaker_registry.get_dead_letter_summary()
    except Exception as e:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L161", e)
        report["dead_letters"] = {"error": str(e)}

    try:
        from CORE.config import get_config
        cfg = get_config()
        report["config"] = {
            "version": cfg.version,
            "pams_url": cfg.pams.url,
            "circuit_breaker": {"enabled": cfg.circuit_breaker.enabled, "threshold": cfg.circuit_breaker.failure_threshold},
            "clusters": {"timeout": cfg.clusters.default_timeout, "max_parallel": cfg.clusters.max_parallel_worms, "retries": cfg.clusters.max_retries},
            "log_level": cfg.observability.logging.level,
        }
    except Exception as exc:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L174", exc)

    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        mem_stats = store.get_stats()
        report["session"] = {
            "total_memories": mem_stats.get("total_memories", 0),
            "total_sessions": mem_stats.get("total_sessions", 0),
            "db_size_mb": mem_stats.get("db_size_mb", 0),
            "top_sources": dict(sorted(mem_stats.get("sources", {}).items(), key=lambda x: -x[1])[:5]),
            "top_events": dict(sorted(mem_stats.get("event_types", {}).items(), key=lambda x: -x[1])[:5]),
        }
    except Exception as exc:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L188", exc)

    try:
        from CORE.WORMS.METACLUSTER import MetaCluster
        mc = MetaCluster()
        mc_state = mc.get_status()
        report["metacluster"] = {
            "categories": mc_state.get("ncm", {}).get("natures", 0),
            "total_worms": mc_state.get("ncm", {}).get("total_worms", 0),
            "routing_paths": mc_state.get("routing_paths", 0),
            "cross_category_routes": mc_state.get("cross_category_routes", 0),
            "orchestration_history": mc_state.get("orchestration_history", 0),
        }
    except Exception as exc:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L202", exc)

    try:
        from CORE.workflow import get_workflow_engine
        engine = get_workflow_engine()
        wf_status = engine.get_status()
        report["workflows"] = {
            "templates": wf_status["templates_list"],
            "total_executions": wf_status["history_count"],
            "last_execution": wf_status["last_execution"],
        }
    except Exception as exc:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L214", exc)

    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        # ScheduledTrainingWorm (Q-learning)
        results = store.search_memories("ScheduledTrainingWorm", limit=10)
        training_runs = []
        for entry in results:
            data = entry.get("data", {})
            if isinstance(data, dict):
                training_runs.append({
                    "run_id": data.get("run_id", "?"),
                    "success": data.get("success", False),
                    "avg_reward": data.get("avg_reward", 0),
                    "records": data.get("records", 0),
                    "episodes": data.get("episodes", 0),
                    "model_version": data.get("model_version", "?"),
                })
        report["scheduling"] = {"recent_runs": training_runs, "total_runs": len(training_runs)}
    except Exception as exc:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L235", exc)

    try:
        # TrainingOrchestrator (SB3 deep RL)
        store = get_memory_store()
        sb3_results = store.search_memories("TrainingOrchestrator", limit=10)
        sb3_runs = []
        for entry in sb3_results:
            data = entry.get("data", {})
            if isinstance(data, dict):
                sb3_runs.append({
                    "run_id": data.get("run_id", "?"),
                    "algo": data.get("algo", "?"),
                    "total_timesteps": data.get("total_timesteps", 0),
                    "records_used": data.get("records_used", 0),
                    "data_source": data.get("data_source", "?"),
                    "model_version": data.get("model_version", "?"),
                    "duration_ms": data.get("duration_ms", 0),
                })
        report["sb3_training"] = {"recent_runs": sb3_runs, "total_runs": len(sb3_runs)}
    except Exception as exc:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L256", exc)

    try:
        from CORE.WORMS.expansion.db_monitor import DatabaseMonitorWorm
        monitor = DatabaseMonitorWorm()
        report["db_monitor"] = monitor.get_db_summary()
        report["db_monitor_full"] = monitor.get_report()
    except Exception as exc:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L264", exc)

    return report


def _mesh_report() -> dict[str, Any]:
    """The neural mesh's own state, read from `spine_registry`.

    This dashboard predates the mesh entirely: it reported health, metrics,
    drift and circuit breakers with no reference to `spine_registry`, `NATURE`
    or the fabric (LIVE_MONITOR_RESTRUCTURE_TODO.md §3, P2 item 9 — "decide
    dashboard.py's fate"). Decision: **keep it, rebase its data on the spine.**
    Retiring a working 34 KB terminal dashboard would lose the Prometheus/JSON
    surfaces that nothing else provides, and rebasing costs one function.

    Every block degrades to an `error` string exactly like the rest of this
    report — a dashboard that raises is a dashboard nobody runs.
    """
    mesh: dict[str, Any] = {"timestamp": datetime.now().isoformat()}
    try:
        from CORE import spine_registry as sp

        q = sp.get_q_weights()
        counts = sp.get_q_update_counts()
        pulled = {k for k, c in counts.items() if int(c or 0) > 0}
        starved = [k for k, v in q.items()
                   if k not in pulled and abs(float(v or 0.0)) < 1e-9]
        mesh["q"] = {
            "keys": len(q), "measured": len(pulled), "starved": len(starved),
            "seeded": max(0, len(q) - len(pulled) - len(starved)),
            "positive": sum(1 for v in q.values() if v > 0),
            "negative": sum(1 for v in q.values() if v < 0),
        }

        # Quorum is counted over DISTINCT ENVS in the shared ledger (audit #1),
        # not by a local majority, so report envs rather than votes.
        votes = sp.get_quarantine_votes() or {}
        decided, envs = 0, set()
        for box in votes.values():
            tally: dict[str, int] = {}
            for env, rec in (box or {}).items():
                envs.add(env)
                verdict = (rec or {}).get("verdict", "?")
                tally[verdict] = tally.get(verdict, 0) + 1
            if tally and max(tally.values()) >= 2:
                decided += 1
        mesh["quarantine"] = {"findings": len(votes), "decided": decided,
                              "envs": sorted(envs)}

        fabric = sp.get_fabric_view() or {}
        mesh["fabric"] = {"providers": sorted(fabric),
                          "count": len(fabric),
                          "updated_at": {k: v.get("updated_at", "")
                                         for k, v in fabric.items()}}

        clock: dict[str, Any] = {"skew_ms": sp.get_clock_skew_ms()}
        try:
            from CORE.hlc import hlc
            h = hlc()
            clock["node"] = h.node
            clock["refused"] = h.rejected()
            clock["refused_skew_ms"] = h.rejected_skew_ms()
            clock["observations"] = h.observations()
        except Exception as exc:  # pragma: no cover - defensive
            note_failure("dashboard:L328", exc)
        mesh["clock"] = clock
        mesh["queue"] = sp.get_spine_summary()
    except Exception as e:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L332", e)
        mesh["error"] = str(e)

    # `state/mesh_cycle.json` had no monitor anywhere; it is the only per-nature
    # ok/fail/ms record of what the last sweep actually executed.
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        path = os.path.join(root, "state", "mesh_cycle.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="ignore") as fh:
                cycle = json.load(fh)
            mesh["cycle"] = {
                "at": cycle.get("at"), "ok": cycle.get("ok"),
                "fail": cycle.get("fail"),
                "duration_s": cycle.get("duration_s"),
                "complete": cycle.get("complete"),
                "categories": len(cycle.get("natures") or {}),
                "worst": sorted(
                    ((n, (r or {}).get("ms")) for n, r in (cycle.get("natures") or {}).items()),
                    key=lambda kv: -(kv[1] or 0))[:5],
            }
    except Exception as e:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L354", e)
        mesh["cycle"] = {"error": str(e)}
    return mesh


def render_mesh():
    """Render the neural-mesh panel."""
    mesh = _mesh_report()
    render_header("NEURAL MESH  [--]  spine brainstem + fabric + clock + cycle")
    if mesh.get("error"):
        print(f"  [!!] mesh unavailable: {mesh['error']}")
        return

    q = mesh.get("q", {})
    starved = q.get("starved", 0)
    print(f"  Q brainstem: {q.get('keys', 0)} keys, {q.get('measured', 0)} measured, "
          f"{starved} starved, {q.get('seeded', 0)} seeded")
    if starved:
        print("       starved = weight 0.0 AND never pulled (the cold-start collapse)")
    print(f"       positive {q.get('positive', 0)} / negative {q.get('negative', 0)}")

    qua = mesh.get("quarantine", {})
    print(f"  Quarantine ledger: {qua.get('decided', 0)} decided of "
          f"{qua.get('findings', 0)} findings across "
          f"{len(qua.get('envs', []))} env(s) {qua.get('envs', [])}")
    # ASCII only in console output: an em dash here renders as garbage on a
    # cp1252 Windows console (seen in the first run of this panel).
    print("       quorum counts DISTINCT ENVS - a local majority cannot quarantine")

    clock = mesh.get("clock", {})
    print(f"  Clock: skew {clock.get('skew_ms', 0):+,}ms on {clock.get('node', '?')}, "
          f"{clock.get('refused', 0)} refused "
          f"(worst {clock.get('refused_skew_ms', 0):,}ms), "
          f"{clock.get('observations', 0)} observed")

    fab = mesh.get("fabric", {})
    providers = fab.get("providers", [])
    print(f"  Fabric: {fab.get('count', 0)} provider(s) with persisted state")
    for name in providers[:8]:
        print(f"       - {name}")
    if len(providers) > 8:
        print(f"       ... and {len(providers) - 8} more")

    cyc = mesh.get("cycle", {})
    if cyc.get("error"):
        print(f"  Cycle: unreadable ({cyc['error']})")
    elif cyc:
        print(f"  Cycle: {cyc.get('ok', '?')} ok / {cyc.get('fail', '?')} fail, "
              f"{cyc.get('duration_s', '?')}s, {cyc.get('natures', 0)} natures, "
              f"complete={cyc.get('complete')}, at {cyc.get('at')}")
        for name, ms in cyc.get("worst", []):
            print(f"       slowest: {name} {ms}ms")
    else:
        print("  Cycle: state/mesh_cycle.json not present")
    print()


def render_health():
    """Render health check report."""
    try:
        from CORE.metrics import health_check
    except ImportError:
        print("  [DOWN] CORE.metrics not available")
        return

    report = health_check.report()

    overall = report["overall"]
    color = "[OK]" if overall == "ok" else "[!!]" if overall == "down" else "[..]"
    render_header(f"HEALTH CHECK  {color}  overall: {overall}")
    print(f"  Timestamp: {report['timestamp']}")
    print(f"  Components: {report['counts']['ok']} ok / {report['counts']['degraded']} degraded / {report['counts']['down']} down")
    print()

    for name, comp in sorted(report["components"].items()):
        icon = "[OK]" if comp["status"] == "ok" else "[!!]" if comp["status"] == "down" else "[..]"
        print(f"  {icon} {name}")
        print(f"       {comp['message'][:80]}")
        if comp["details"]:
            details_str = json.dumps(comp["details"], default=str)[:80]
            print(f"       {details_str}")
        print()


def render_metrics():
    """Render metrics snapshot."""
    try:
        from CORE.metrics import metrics_collector
    except ImportError:
        print("  [DOWN] CORE.metrics not available")
        return

    snapshot = metrics_collector.snapshot()

    render_header(f"METRICS  ({len(snapshot)} metrics tracked)")
    if not snapshot:
        print("  (no metrics recorded yet)")
        print()

    for name, agg in sorted(snapshot.items()):
        if agg["count"] > 0:
            print(f"  {name}")
            print(f"       count={agg['count']}  avg={agg['avg']}  min={agg['min']}  max={agg['max']}")
            print(f"       p50={agg['p50']}  p95={agg['p95']}  p99={agg['p99']}")
            print()


def render_drift():
    """Render drift alert check."""
    try:
        from CORE.metrics import drift_alert
    except ImportError:
        print("  [DOWN] CORE.metrics not available")
        return

    issues = drift_alert.check()

    icon = "[OK]" if not issues else "[!!]"
    render_header(f"DRIFT ALERT  {icon}  ({len(issues)} issues)")
    if not issues:
        print("  No drift issues detected.")
        print()
        return

    for issue in issues:
        severity_icon = "[CRITICAL]" if issue.severity == "critical" else "[WARN]" if issue.severity == "warning" else "[INFO]"
        print(f"  {severity_icon} {issue.message[:90]}")
        if issue.details:
            print(f"       {json.dumps(issue.details, default=str)[:80]}")
    print()


def render_workflows():
    """Render WorkflowEngine status."""
    try:
        from CORE.workflow import get_workflow_engine
        engine = get_workflow_engine()
        status = engine.get_status()
    except Exception as e:  # noqa: BLE001 — non-fatal render isolation
        print(f"  [DOWN] WorkflowEngine: {e}")
        return

    render_header(f"WORKFLOW ENGINE  ({status['templates']} templates, {status['history_count']} executions)")
    
    # Templates
    if status['templates_list']:
        print("  Available templates:")
        for t in status['templates_list']:
            print(f"    {t}")
    print()

    # Last execution
    last = status.get('last_execution')
    if last:
        icon = "[OK]" if last["success"] else "[!!]"
        print("  Last execution:")
        print(f"    Workflow: {last['name']}  {icon}")
        print(f"    Duration: {last['duration_ms']:.0f}ms")
        print(f"    ID: {last['id']}")
    else:
        print("  No workflows executed yet.")
    print()

    # Recent history
    try:
        history = engine.get_history(limit=3)
        if history:
            render_subheader("Recent executions")
            for h in history:
                icon = "[OK]" if h.success else "[!!]"
                print(f"  {icon} {h.workflow_name}  ({h.success_count} ok / {h.failure_count} fail / {h.skipped_count} skipped)  {h.duration_ms:.0f}ms")
            print()
    except Exception as exc:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L526", exc)


def render_scheduling():
    """Render ScheduledTrainingWorm status from persisted memory."""
    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        # Query recent training runs from LocalMemoryStore
        results = store.search_memories("ScheduledTrainingWorm", limit=5)
        stats = store.get_stats()
    except Exception as e:  # noqa: BLE001 — non-fatal render isolation
        print(f"  [DOWN] ScheduledTrainingWorm: {e}")
        return

    total_memories = stats.get("total_memories", 0)
    render_header(f"SCHEDULED TRAINING  ({len(results)} recent runs)")

    if not results:
        print("  No training runs recorded yet. Run ScheduledTrainingWorm.execute() to start.")
        print()
        return

    render_subheader("Recent training runs")
    for entry in results:
        data = entry.get("data", {})
        if isinstance(data, dict):
            icon = "[OK]" if data.get("success", False) else "[!!]"
            run_id = data.get("run_id", data.get("model_version", "?"))
            reward = data.get("avg_reward", 0)
            records = data.get("records", 0)
            episodes = data.get("episodes", 0)
            print(f"  {icon} {run_id}")
            print(f"       reward={reward:.2f}  records={records}  episodes={episodes}")
        else:
            print(f"  [?] {str(data)[:60]}")
    print()

    render_subheader("System overview")
    print(f"  Total memories in store: {total_memories}")
    print("  Training data persisted to LocalMemoryStore for durability")
    print()


def render_dead_letters():
    """Render dead letter queue summary from circuit breaker registry."""
    try:
        from CORE.circuit_breaker import breaker_registry
    except ImportError:
        print("  [DOWN] CORE.circuit_breaker not available")
        return

    summary = breaker_registry.get_dead_letter_summary()
    total = summary["total_dead_letters"]
    icon = "[OK]" if total == 0 else "[!!]"
    render_header(f"DEAD LETTER QUEUE  {icon}  ({total} total)")
    
    if total == 0:
        print("  No dead letters. All circuit breakers healthy.")
        print()
        return

    print("  Per-breaker counts:")
    for name, count in sorted(summary["per_breaker"].items(), key=lambda x: -x[1]):
        print(f"    {name}: {count} dead letters")
    
    if summary["recent_errors"]:
        print("\n  Recent errors (last hour):")
        for err in summary["recent_errors"][:5]:
            ts = datetime.fromtimestamp(err["timestamp"]).strftime("%H:%M:%S")
            print(f"    [{ts}] {err['breaker']}: {err['error'][:80]}")
    print()


def render_db_health():
    """Render database health trends from LocalMemoryStore."""
    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        stats = store.get_stats()
        real = store.query_real_errors(days=7)
        trends = store.get_error_trends(days=30)
        growth = store.get_db_growth(days=30)
        vacuum_status = store.auto_vacuum_status
    except Exception as e:  # noqa: BLE001 — non-fatal render isolation
        print(f"  [DOWN] DB Health: {e}")
        return

    render_header("DATABASE HEALTH")

    # ── Overview ──
    print(f"  DB Size: {stats['db_size_mb']} MB")
    print(f"  Total Events: {stats['total_memories']}")
    print(f"  Sessions: {stats['total_sessions']}")
    print(f"  Auto-Vacuum: {'[ON]' if vacuum_status.get('running') else '[OFF]'} (every {vacuum_status.get('interval_hours', '?')}h)")
    print()

    # ── Real Errors (filtered) ──
    icon = "[OK]" if real.get("total_errors", 0) == 0 else "[!!]"
    print(f"  Real Errors (last 7d): {icon} {real.get('total_errors', 0)}")
    print(f"  Real Warnings (last 7d): {real.get('total_warnings', 0)}")
    # Additive 2026-10-17: criticals were invisible here; .get keeps old dicts working.
    print(f"  Real Criticals (last 7d): {real.get('total_criticals', real.get('total_criticals_exact', 0))}")
    print(f"  Test artifacts filtered: {real.get('filtered_errors', 0)} errors, {real.get('filtered_warnings', 0)} warnings, {real.get('filtered_criticals', 0)} criticals")
    print()

    # ── Recent real errors ──
    if real["errors"]:
        render_subheader("Recent real errors")
        for e in real["errors"][:5]:
            data_preview = str(e.get("data", ""))[:800]
            ts = str(e.get("timestamp", ""))[:19]
            print(f"  [{ts}] {e['source']} / {e['event_type']}")
            print(f"       {data_preview}")
        print()

    # ── Error Trends ──
    if trends:
        render_subheader("Error/Warning/Critical Trend (30 days)")
        max_count = max((d.get("errors", 0) + d.get("warnings", 0) + d.get("criticals", 0)) for d in trends) if trends else 1
        for d in trends:
            err = d.get("errors", 0)
            warn = d.get("warnings", 0)
            crit = d.get("criticals", 0)
            total = err + warn + crit
            bar_len = min(int(total / max(max_count, 1) * 30), 30) if max_count > 0 else 0
            bar = "#" * bar_len
            print(f"  {d['date']} | {bar:<30s} | E={err:3d} W={warn:3d} C={crit:3d}")
        print()

    # ── Event Growth ──
    if growth:
        render_subheader("Event Growth (30 days)")
        max_events = max(d["count"] for d in growth) if growth else 1
        for d in growth:
            bar_len = min(int(d["count"] / max(max_events, 1) * 30), 30) if max_events > 0 else 0
            bar = "=" * bar_len
            print(f"  {d['date']} | {bar:<30s} | {d['count']:5d} events")
        print()

    # ── Health verdict ──
    db_size_mb = stats["db_size_mb"]
    try:
        from CORE.config import get_config
        cfg = get_config()
        max_size_warn = cfg.memory_store.max_size_mb_warn
    except Exception as exc:  # noqa: BLE001 — non-fatal subsystem isolation
        note_failure("dashboard:L670", exc)
        max_size_warn = 100

    if db_size_mb > max_size_warn:
        verdict_txt = "[!!] DB is large (>" + str(round(db_size_mb)) + " MB) -- consider purging old data"
    elif real.get("total_criticals", real.get("total_criticals_exact", 0)) > 0:
        verdict_txt = "[!!] Critical-severity rows present - investigate immediately"
    elif real.get("total_errors", 0) > 10:
        verdict_txt = "[!!] High error count - investigate real errors"
    elif not vacuum_status.get("running"):
        verdict_txt = "[..] Auto-vacuum is OFF -- enable for long-running instances"
    else:
        verdict_txt = "[OK] Database healthy"
    print(f"  Verdict: {verdict_txt}")
    print()


def render_db_monitor():
    """Render DatabaseMonitorWorm health snapshot."""
    try:
        from CORE.WORMS.expansion.db_monitor import DatabaseMonitorWorm
        monitor = DatabaseMonitorWorm()
        summary = monitor.get_db_summary()
        report = monitor.get_report()
    except Exception as e:  # noqa: BLE001 — non-fatal render isolation
        print(f"  [DOWN] DatabaseMonitorWorm: {e}")
        return

    status = summary.get("status", "UNKNOWN")
    icon = "[OK]" if status == "HEALTHY" else "[!!]"
    alerts = summary.get("alerts_pending", 0)
    alert_icon = "" if alerts == 0 else f"  ({alerts} alerts pending)"
    render_header(f"DATABASE MONITOR  {icon}  {status}  {alert_icon}")

    # ── Summary metrics ──
    print(f"  Backend:     {summary.get('backend', '?')}")
    print(f"  DB Size:     {summary.get('size_mb', 0):.1f} MB")
    print(f"  Memories:    {summary.get('memories', 0)}")
    print(f"  Sessions:    {summary.get('sessions', 0)}")
    print(f"  Growth 24h:  {summary.get('growth_24h', 0)}")
    print(f"  Errors 7d:   {summary.get('errors_7d', 0)}")
    print(f"  Warnings 7d: {summary.get('warnings_7d', 0)}")
    print(f"  Criticals 7d:{summary.get('criticals_7d', 0)}")
    print(f"  Sources:     {summary.get('sources', 0)}")
    print(f"  Events:      {summary.get('event_types', 0)}")
    print(f"  Auto-Vacuum: {'[ON]' if summary.get('auto_vacuum', False) else '[OFF]'}")
    print(f"  Snapshot:    {summary.get('timestamp', '?')}")
    print()

    # ── Thresholds ──
    thresh = report.get("thresholds", {})
    if thresh:
        render_subheader("Alert thresholds")
        print(f"  DB size warn:      {thresh.get('warn_db_size_mb', '?')} MB")
        print(f"  DB size critical:  {thresh.get('critical_db_size_mb', '?')} MB")
        print(f"  Growth warn:       {thresh.get('warn_growth_24h', '?')}/day")
        print(f"  Growth critical:   {thresh.get('critical_growth_24h', '?')}/day")
        print(f"  Errors warn:       {thresh.get('warn_errors_7d', '?')}/7d")
        print(f"  Errors critical:   {thresh.get('critical_errors_7d', '?')}/7d")
        print()

    # ── Recent alerts ──
    recent_alerts = report.get("recent_alerts", [])
    if recent_alerts:
        render_subheader(f"Recent alerts ({len(recent_alerts)})")
        for a in recent_alerts[:5]:
            sev_icon = "[CRITICAL]" if a["severity"] == "critical" else "[WARN]" if a["severity"] == "warning" else "[INFO]"
            print(f"  {sev_icon} {a.get('message', '?')[:80]}")
        print()


def render_session():
    """Render recent session info."""
    try:
        from CORE.local_memory import LocalMemoryStore
        store = LocalMemoryStore()
        stats = store.get_stats()
    except Exception:  # noqa: BLE001 — non-fatal render isolation
        print("  [DOWN] LocalMemoryStore not available")
        return

    render_header("SESSION INFO")
    print(f"  Total memories: {stats.get('total_memories', '?')}")
    print(f"  Total sessions: {stats.get('total_sessions', '?')}")
    print(f"  DB size: {stats.get('db_size_mb', '?')} MB")
    print(f"  Current session: {stats.get('current_session', '?')}")
    print()

    render_subheader("Top sources")
    sources = stats.get("sources", {})
    for s, count in sorted(sources.items(), key=lambda x: -x[1])[:5]:
        print(f"    {s}: {count}")
    print()

    render_subheader("Top event types")
    event_types = stats.get("event_types", {})
    for et, count in sorted(event_types.items(), key=lambda x: -x[1])[:5]:
        print(f"    {et}: {count}")
    print()


def render_meta():
    """Render MetaCluster status."""
    try:
        from CORE.WORMS.METACLUSTER import MetaCluster
        mc = MetaCluster()
        status = mc.get_status()
    except Exception as e:  # noqa: BLE001 — non-fatal render isolation
        print(f"  [DOWN] MetaCluster: {e}")
        return

    render_header("METACLUSTER")
    ncm = status.get("ncm", {})
    print(f"  Natures: {ncm.get('natures', '?')}")
    print(f"  Total worms: {ncm.get('total_worms', '?')}")
    print(f"  Routing paths: {status.get('routing_paths', '?')}")
    print(f"  Cross-Nature routes: {status.get('cross_category_routes', '?')}")
    print(f"  Orchestration history: {status.get('orchestration_history', '?')} runs")
    print(f"  Free-threaded: {status.get('free_threaded', '?')}")
    last = status.get("last_orchestration")
    if last:
        print(f"  Last orchestration: {last}")
    print()


def render_config():
    """Render config status."""
    try:
        from CORE.config import get_config, get_warnings
        cfg = get_config()
        warnings = get_warnings()
    except Exception as e:  # noqa: BLE001 — non-fatal render isolation
        print(f"  [DOWN] Config: {e}")
        return

    render_header("CONFIGURATION")
    print(f"  Version: {cfg.version}")
    print(f"  PAMS URL: {cfg.pams.url}")
    print(f"  Circuit Breaker: enabled={cfg.circuit_breaker.enabled}, threshold={cfg.circuit_breaker.failure_threshold}")
    print(f"  Clusters: timeout={cfg.clusters.default_timeout}s, max_parallel={cfg.clusters.max_parallel_worms}, retries={cfg.clusters.max_retries}")
    print(f"  Log level: {cfg.observability.logging.level}")
    if warnings:
        for w in warnings:
            print(f"  [WARN] {w}")
    print()


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def run_dashboard(args):
    """Run the dashboard with the given args."""
    since = getattr(args, 'since', None)

    # JSON output mode
    if getattr(args, 'json_output', False):
        report = get_report(since=since)
        print(json.dumps(report, indent=2, default=str))
        return

    if args.health:
        render_health()
    elif args.mesh:
        render_mesh()
    elif args.metrics:
        render_metrics()
    elif args.drift:
        render_drift()
    elif args.db_health:
        render_db_health()
    elif args.db_monitor_view:
        render_db_monitor()
    else:
        render_header(f"AZAZAEL OBSERVABILITY DASHBOARD  ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
        render_config()
        render_health()
        render_mesh()
        render_metrics()
        render_dead_letters()
        render_drift()
        render_db_health()
        render_db_monitor()
        render_session()
        render_scheduling()
        render_meta()
        render_workflows()
        print("=" * 60)
        print("  Dashboard complete. Use --watch for live view.")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Azazael Observability Dashboard — health, metrics, drift",
    )
    parser.add_argument("--health", action="store_true", help="Health check only")
    parser.add_argument("--mesh", action="store_true",
                        help="Neural mesh only (brainstem, quarantine, fabric, clock, cycle)")
    parser.add_argument("--metrics", action="store_true", help="Metrics only")
    parser.add_argument("--drift", action="store_true", help="Drift alert only")
    parser.add_argument("--db-health", action="store_true", dest="db_health",
                        help="Database health trends only")
    parser.add_argument("--db-monitor", action="store_true", dest="db_monitor_view",
                        help="DatabaseMonitorWorm health snapshot only")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output full report as JSON instead of formatted terminal view")
    parser.add_argument("--since", default=None,
                        help='Time filter: "24h", "30m", "3600s", or ISO date like "2026-01-15"')
    parser.add_argument("--watch", action="store_true", help="Live-repeating view")
    parser.add_argument("--interval", type=int, default=5, help="Refresh interval (seconds)")
    parser.add_argument("--health-server", action="store_true",
                        help="Start health endpoint server (port 18420) in background before dashboard")
    parser.add_argument("--health-server-port", type=int, default=18420,
                        help="Port for health endpoint server")
    parser.add_argument("--prometheus-server", action="store_true",
                        help="Start Prometheus metrics endpoint (port 18421) in background before dashboard")
    parser.add_argument("--prometheus-server-port", type=int, default=18421,
                        help="Port for Prometheus metrics endpoint")
    parser.add_argument("--all-servers", action="store_true",
                        help="Start both health server AND Prometheus endpoint in background before dashboard")
    parser.add_argument("--output", default=None,
                        help="Write JSON report to file (e.g., report.json)")
    parser.add_argument("--export-html", default=None,
                        help="Export standalone HTML report to file (e.g., report.html)")

    args = parser.parse_args()

    # Handle --all-servers: starts both health + Prometheus
    if args.all_servers:
        args.health_server = True
        args.prometheus_server = True

    # Handle export modes
    if args.output:
        report = get_report(since=getattr(args, 'since', None))
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"  Report written to {os.path.abspath(args.output)}")
        return

    if args.export_html:
        try:
            from CORE.web_dashboard import export_html
            path = export_html(args.export_html, since=getattr(args, 'since', None))
            print(f"  HTML report exported to {path}")
        except Exception as e:  # noqa: BLE001 — CLI export must not crash main
            print(f"  [ERROR] Export failed: {e}")
        return

    # Start health server in background if requested
    health_thread = None
    if args.health_server:
        try:
            from CORE.health_server import HealthServer
            health_server = HealthServer(port=args.health_server_port)
            health_thread = health_server.start_background()
            print(f"  Health server started on port {args.health_server_port}")
            if not args.watch:
                time.sleep(0.5)  # Let server bind before dashboard renders
        except Exception as e:  # noqa: BLE001 — optional server; warn, keep going
            print(f"  [WARN] Could not start health server: {e}")

    # Start Prometheus server in background if requested
    prometheus_thread = None
    if args.prometheus_server:
        try:
            from CORE.health_server import PrometheusServer
            prometheus_server = PrometheusServer(port=args.prometheus_server_port)
            prometheus_thread = prometheus_server.start_background()
            print(f"  Prometheus endpoint started on port {args.prometheus_server_port}")
            if not args.watch:
                time.sleep(0.3)
        except Exception as e:  # noqa: BLE001 — optional server; warn, keep going
            print(f"  [WARN] Could not start Prometheus server: {e}")

    # Start background DB monitor if --db-monitor or full overview
    _db_monitor_instance = None
    if args.watch and (args.db_monitor_view or not any([args.health, args.metrics, args.drift, args.db_health])):
        try:
            from CORE.WORMS.expansion.db_monitor import DatabaseMonitorWorm
            _db_monitor_instance = DatabaseMonitorWorm()
            _db_monitor_instance.start_monitor(interval_seconds=args.interval * 2)
        except Exception as exc:  # noqa: BLE001 — optional background monitor; skip
            note_failure("dashboard:L950", exc)

    # Start background SB3 TrainingOrchestrator alongside DB monitor
    _training_orchestrator = None
    if args.watch and not any([args.health, args.metrics, args.drift, args.db_health, args.db_monitor_view]):
        try:
            from CORE.WORMS.expansion.scheduling import TrainingOrchestrator, OrchestratorConfig
            _training_orchestrator = TrainingOrchestrator(
                OrchestratorConfig(
                    algo="PPO", total_timesteps=5000, interval_hours=24.0,
                    record_to_memory=True, register_model=True,
                )
            )
            _training_orchestrator.start_scheduler(interval_hours=24.0)
            print("  TrainingOrchestrator scheduler started (interval=24h)")
        except Exception as e:  # noqa: BLE001 — optional scheduler; warn, keep going
            print(f"  [WARN] Could not start TrainingOrchestrator: {e}")

    try:
        if args.watch:
            while True:
                print("\033[H\033[J", end="")
                run_dashboard(args)
                time.sleep(args.interval)
        else:
            run_dashboard(args)
    finally:
        if health_thread and health_thread.is_alive():
            print("  Stopping health server...")
            health_server.stop()
        if prometheus_thread and prometheus_thread.is_alive():
            print("  Stopping Prometheus endpoint...")
            prometheus_server.stop()
        if _db_monitor_instance:
            _db_monitor_instance.stop_monitor()
        if _training_orchestrator:
            _training_orchestrator.stop_scheduler()


if __name__ == "__main__":
    main()
