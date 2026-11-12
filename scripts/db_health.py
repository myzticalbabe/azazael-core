#!/usr/bin/env python3
"""
DB Health CLI — Database Health Report for LocalMemoryStore.

Usage:
    python scripts/db_health.py                  # Full health report
    python scripts/db_health.py --quick           # Quick summary
    python scripts/db_health.py --stats           # Statistics only
    python scripts/db_health.py --performance     # Performance metrics
    python scripts/db_health.py --export PATH     # Export report to JSON
    python scripts/db_health.py --backup PATH     # Backup the database
    python scripts/db_health.py --vacuum          # Run VACUUM
"""
import sys
import json
import time
from pathlib import Path

def _get_dlq():
    """Lazy-loaded DeadLetterQueue for DLQ queries (flat, U3 — was NCM)."""
    from CORE.WORMS.dead_letter_queue import DeadLetterQueue
    return DeadLetterQueue()

# Ensure src/ is in path
_script_dir = Path(__file__).resolve().parent
_src_dir = _script_dir.parent / "src"
sys.path.insert(0, str(_src_dir))


def print_header(title: str):
    """Print a formatted header."""
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_section(label: str, data: dict, indent: int = 2):
    """Print a formatted section of key-value pairs."""
    for key, value in data.items():
        prefix = " " * indent
        if isinstance(value, dict):
            print(f"{prefix}{key}:")
            for k, v in value.items():
                print(f"{prefix}  {k}: {v}")
        elif isinstance(value, list):
            print(f"{prefix}{key}: [{len(value)} items]")
        else:
            print(f"{prefix}{key}: {value}")


def cmd_report(args):
    """Full health report."""
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    report = store.get_health_report()
    
    health = report["health"]
    print_header(f"DB HEALTH REPORT — Grade {health['grade']} ({health['score']}/{health['max_score']})")
    print(f"  DB Path: {report['db_path']}")
    print(f"  Store Version: {report['store_version']}")
    print(f"  Timestamp: {report['timestamp']}")
    
    # Health score breakdown
    print_header("Health Score Breakdown")
    for dim, detail in sorted(health["details"].items()):
        bar = "█" * int(detail["score"] / max(detail["max"], 1) * 20) + "░" * (20 - int(detail["score"] / max(detail["max"], 1) * 20))
        print(f"  {dim:25} [{bar}] {detail['score']:>2}/{detail['max']:<2}")
    
    if health["warnings"]:
        print_header(f"Warnings ({len(health['warnings'])})")
        for w in health["warnings"]:
            print(f"  ⚠ {w}")
    
    # Stats summary
    stats = report["stats"]
    print_header("Database Stats")
    print(f"  Memories:      {stats['total_memories']}")
    print(f"  Sessions:      {stats['total_sessions']} ({stats['active_sessions']} active, {stats['completed_sessions']} completed)")
    print(f"  DB Size:       {stats['db_size_mb']} MB (+{stats['wal_size_mb']} MB WAL) = {stats['total_size_mb']} MB total")
    print(f"  Fragmentation: {stats['fragmentation_ratio']}")
    print(f"  Avg events/session: {stats['avg_events_per_session']}")
    
    # Performance
    perf = report["performance"]
    print_header("Query Performance")
    print(f"  Total queries: {perf['total_queries']}")
    print(f"  Avg time:      {perf['avg_query_time_ms']} ms")
    print(f"  Max time:      {perf['max_query_time_ms']} ms")
    print(f"  Slow queries:  {perf['slow_query_count']} ({perf['slow_query_pct']}%)")
    
    # Indexes
    indexes = report["indexes"]
    print_header(f"Indexes ({len(indexes)})")
    for idx in indexes:
        cols = ", ".join(idx["columns"])
        print(f"  {idx['index_name']:45} ({cols})")
    
    # Maintenance
    maint = report["maintenance"]
    print_header("Maintenance")
    for name, status in maint.items():
        print(f"  {name}: {'RUNNING' if status.get('running') else 'STOPPED'}")
        print(f"    Interval: {status.get('interval_hours', 'N/A')}h")
        print(f"    Thread: {'alive' if status.get('thread_alive') else 'not running'}")
    
    # Growth & error trends
    growth = report.get("growth_trends", [])
    errors = report.get("error_trends", [])
    print_header("Recent Activity")
    if growth:
        total_growth = sum(g["count"] for g in growth)
        print(f"  Events (last 7 days): {total_growth}")
        for g in growth[-3:]:
            print(f"    {g['date']}: {g['count']} events")
    else:
        print("  No events in last 7 days")
    if errors:
        total_err = sum(e["errors"] + e["warnings"] for e in errors)
        print(f"  Errors/Warnings (last 7 days): {total_err}")
    
    # Dead letter queue summary (flat DLQ store)
    try:
        dlq = _get_dlq()
        dlq_summary = dlq.get_dead_letter_summary()
        _dlq_alerts(dlq_summary)
        if dlq_summary["total_failed"] > 0:
            print_header(f"Dead Letter Queue — {dlq_summary['total_failed']} Failed Executions")
            print(f"  Affected Categories: {dlq_summary['affected_categories']}")
            for category, count in sorted(dlq_summary.get("per_category", {}).items()):
                print(f"    {category}: {count} failures")
            print(f"  (see --failed for details)")
    except Exception:
        pass  # DLQ store not available — skip DLQ section


def cmd_quick(args):
    """Quick summary."""
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    health = store.health_check()
    stats = store.get_stats()
    
    print(f"DB: {stats['db_size_mb']} MB | {stats['total_memories']} memories | {stats['total_sessions']} sessions")
    print(f"Health: {health['score']}/{health['max_score']} (Grade {health['grade']})")
    print(f"Queries: {stats['performance']['total_queries']} total, {stats['performance']['avg_query_time_ms']} ms avg")
    if health['warnings']:
        print(f"Warnings: {len(health['warnings'])}")
        for w in health['warnings'][:2]:
            print(f"  ⚠ {w}")


def cmd_stats(args):
    """Statistics only."""
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    print(json.dumps(store.get_stats(), indent=2, default=str))


def cmd_performance(args):
    """Performance metrics only."""
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    print(json.dumps(store.get_performance_stats(), indent=2, default=str))


def cmd_export(args):
    """Export report to JSON."""
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    report = store.get_health_report()
    path = args[0] if args else f"db_health_report_{store.current_session_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Report exported to: {path}")


def cmd_backup(args):
    """Backup the database."""
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    path = args[0] if args else f"db_backup_{store.current_session_id}.db"
    result = store.backup(path)
    print(f"Backup created: {result}")


def cmd_vacuum(args):
    """Run VACUUM."""
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    before = store.get_stats()
    store.vacuum()
    after = store.get_stats()
    freed = before["db_size_mb"] - after["db_size_mb"]
    print(f"VACUUM complete: {before['db_size_mb']} MB -> {after['db_size_mb']} MB ({freed:.2f} MB freed)")


def _dlq_alerts(summary):
    """Check DLQ alert thresholds and print warnings.

    - Warning (⚠): > 5 failed executions in the last hour
    - Critical: > 20 failed executions in the last hour
    - Per-worm warning: any single worm has failed > 3 times
    """
    recent = summary.get("recent_errors", [])
    now = time.time()
    one_hour_ago = now - 3600

    # Count failures in last hour (numeric epoch; ISO stamps are display-only)
    hourly_failures = [e for e in recent
                       if e.get("timestamp_epoch", 0) >= one_hour_ago]
    hourly_count = len(hourly_failures)

    if hourly_count > 20:
        print(f"  CRITICAL: {hourly_count} failed executions in the last hour!")
    elif hourly_count > 5:
        print(f"  ⚠ WARNING: {hourly_count} failed executions in the last hour")

    # Per-worm thresholds
    if summary.get("per_worm"):
        for worm, count in sorted(summary["per_worm"].items(), key=lambda x: -x[1]):
            if count > 10:
                print(f"  CRITICAL: {worm} has failed {count} times (threshold: 10)")
            elif count > 3:
                print(f"  ⚠ WARNING: {worm} has failed {count} times (threshold: 3)")


def _show_dlq(dlq, args):
    """Core DLQ display logic — shared by --failed and --watch."""
    clear_flag = "--clear" in args
    retry_flag = "--retry" in args
    retry_all_flag = "--retry-all" in args
    retry_nature_flag = "--retry-nature" in args

    # Aggregated summary from the flat DLQ store (was NCM per-nature)
    summary = dlq.get_dead_letter_summary(lookback_seconds=86400 * 7)  # 1 week

    # Alert thresholds (warnings/critical before empty check — show even if queue is now empty)
    _dlq_alerts(summary)

    if summary["total_failed"] == 0:
        print("✅ No failed executions in dead letter queue")
        return

    print_header(f"DEAD LETTER QUEUE — {summary['total_failed']} Failed Executions")
    print(f"  Affected Categories: {summary['affected_categories']}")
    for category, count in sorted(summary.get("per_category", {}).items()):
        print(f"    {category}: {count} failures")

    print()
    print("  Recent Failures (most recent first):")
    for f in summary["recent_errors"][:15]:
        ts = time.strftime("%H:%M:%S", time.localtime(f["timestamp_epoch"]))
        state = f" [{f['breaker_state']}]" if f.get("breaker_state") else ""
        print(f"    [{ts}] {f['category']}/{f['worm']}{state}: {f['error'][:80]}")
    if len(summary["recent_errors"]) > 15:
        print(f"    ... and {len(summary['recent_errors']) - 15} more")

    if summary.get("per_worm"):
        print()
        print("  Per-Worm Breakdown:")
        for worm, count in sorted(summary["per_worm"].items(), key=lambda x: -x[1]):
            print(f"    {worm}: {count} failures")

    if clear_flag:
        dlq.clear()
        print()
        print("✅ Dead letter queue cleared")

    if retry_flag:
        print()
        print("  Retrying failed worms...")
        result = dlq.retry_all_failed_worms()
        print(f"  Retried: {result['total_retried']}, "
              f"Succeeded: {result['total_succeeded']}")

    if retry_all_flag:
        print()
        print("  Retrying ALL failed worms across all categories...")
        result = dlq.retry_all_failed_worms()
        print(f"  Retried: {result['total_retried']}, Succeeded: {result['total_succeeded']}")
        if result["still_failed"] > 0:
            print(f"  Still Failed: {result['still_failed']}")
            for err in result["errors"][:10]:
                print(f"    ⚠ {err}")
            if len(result["errors"]) > 10:
                print(f"    ... and {len(result['errors']) - 10} more")

    if retry_nature_flag:
        idx = args.index("--retry-nature")
        if idx + 1 < len(args):
            category = args[idx + 1]
            print()
            print(f"  Retrying failed worms in [{category}]...")
            result = dlq.retry_all_failed_worms(category=category)
            print(f"  Retried: {result['total_retried']}, "
                  f"Succeeded: {result['total_succeeded']}")
            if result["still_failed"] > 0:
                print(f"  Still Failed: {result['still_failed']}")
                for err in result["errors"]:
                    print(f"    ⚠ {err}")
            if result["total_retried"] == 0:
                print(f"  Category '{category}' has no retryable letters")
        else:
            print("  Missing category name for --retry-nature")
            print("  Usage: --failed --retry-nature security")


def _send_notification(title: str, message: str):
    """Send a desktop notification using plyer (cross-platform)."""
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=10)
        return True
    except Exception as e:
        print(f"  ⚠ Notification failed: {e}")
        return False


def cmd_failed(args):
    """Show dead letter queue (failed worm executions from the DLQ store).

    Usage:
        python scripts/db_health.py --failed              # All failures
        python scripts/db_health.py --failed --clear       # Clear the queue
        python scripts/db_health.py --failed --retry       # Retry failed worms
        python scripts/db_health.py --failed --retry-all   # Retry across ALL categories
        python scripts/db_health.py --failed --retry-nature CATEGORY  # Retry one category
        python scripts/db_health.py --failed --watch [SECONDS]  # Watch mode
        python scripts/db_health.py --failed --notify      # Desktop notification on alert
    """
    dlq = _get_dlq()
    
    notify_flag = "--notify" in args

    # Watch mode
    watch_flag = "--watch" in args
    watch_interval = 5  # default 5 seconds
    if watch_flag:
        idx = args.index("--watch")
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            try:
                watch_interval = int(args[idx + 1])
            except ValueError:
                pass
        last_notify_count = 0
        try:
            while True:
                print("\033[H\033[J", end="", flush=True)
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                print(f"DLQ Watch Mode — refreshing every {watch_interval}s ({timestamp})")
                print(f"Press Ctrl+C to stop")
                print()
                _show_dlq(dlq, args)
                
                # Desktop notification on new failures
                if notify_flag:
                    summary = dlq.get_dead_letter_summary(lookback_seconds=3600)
                    if summary["total_failed"] > last_notify_count and summary["total_failed"] > 5:
                        _send_notification(
                            f"{summary['total_failed']} DLQ Failures",
                            f"{summary['affected_categories']} categories affected in last hour"
                        )
                        last_notify_count = summary["total_failed"]
                
                time.sleep(watch_interval)
        except KeyboardInterrupt:
            print()
            print("Watch mode stopped.")
        return

    _show_dlq(dlq, args)
    
    # One-shot notification
    if notify_flag:
        summary = dlq.get_dead_letter_summary(lookback_seconds=3600)
        if summary["total_failed"] > 5:
            _send_notification(
                f"{summary['total_failed']} DLQ Failures",
                f"{summary['affected_categories']} categories affected in last hour. "
                f"Use --failed --retry-all to retry."
            )
        else:
            print("  ✅ Threshold not exceeded — no notification sent")
    return


def main():
    args = sys.argv[1:]
    
    if not args or args[0] in ("--help", "-h"):
        print("DB Health CLI — LocalMemoryStore Health Report")
        print()
        print("Usage:")
        print("  python scripts/db_health.py                  Full health report")
        print("  python scripts/db_health.py --quick          Quick summary")
        print("  python scripts/db_health.py --stats          Statistics only")
        print("  python scripts/db_health.py --performance    Performance metrics")
        print("  python scripts/db_health.py --export [PATH]  Export report to JSON")
        print("  python scripts/db_health.py --backup [PATH]  Backup the database")
        print("  python scripts/db_health.py --vacuum         Run VACUUM")
        print("  python scripts/db_health.py --failed              Show dead letter queue")
        print("  python scripts/db_health.py --failed --clear       Clear DLQ")
        print("  python scripts/db_health.py --failed --retry       Retry all failed worms")
        print("  python scripts/db_health.py --failed --retry-all   Retry across ALL categories")
        print("  python scripts/db_health.py --failed --retry-nature CATEGORY  Retry one category")
        print("  python scripts/db_health.py --failed --watch [SECONDS]      Watch mode (polling)")
        print("  python scripts/db_health.py --failed --notify                Desktop notification on alert")
        return
    
    cmd = args[0]
    cmd_args = args[1:]
    
    commands = {
        "--quick": cmd_quick,
        "--stats": cmd_stats,
        "--performance": cmd_performance,
        "--export": cmd_export,
        "--backup": cmd_backup,
        "--vacuum": cmd_vacuum,
        "--failed": cmd_failed,
    }
    
    if cmd in commands:
        commands[cmd](cmd_args)
    else:
        print(f"Unknown command: {cmd}")
        print("Use --help to see available commands.")
        sys.exit(1)


if __name__ == "__main__":
    main()
