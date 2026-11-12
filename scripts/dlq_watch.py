#!/usr/bin/env python3
"""
DLQ Watch — Standalone Dead Letter Queue Monitor

Colorized polling monitor for worm execution failures.
Groups errors by time bucket (30s windows) for trend analysis.

Usage:
    python scripts/dlq_watch.py                     # Watch with 5s refresh
    python scripts/dlq_watch.py --interval 3         # Custom refresh interval
    python scripts/dlq_watch.py --no-color           # Monochrome mode
    python scripts/dlq_watch.py --retry              # Auto-retry on detection
"""
import sys
import os
import time
from pathlib import Path
from collections import defaultdict, deque
import argparse

# Ensure src/ is in path
_script_dir = Path(__file__).resolve().parent
_src_dir = _script_dir.parent / "src"
sys.path.insert(0, str(_src_dir))

# ── ANSI Colors ──────────────────────────────────────────────────
class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"
    CLEAR = "\033[2J\033[H"

NO_COLORS = Colors()
for attr in dir(Colors):
    if not attr.startswith("_"):
        setattr(NO_COLORS, attr, "")
NO_COLORS.CLEAR = "\033[H"  # just cursor home, no clear

def _color(enabled: bool) -> Colors:
    return Colors if enabled else NO_COLORS

# ── Time Bucketing ───────────────────────────────────────────────

def bucket_errors(errors: list, bucket_seconds: int = 30) -> list:
    """Group errors into time buckets for trend display."""
    if not errors:
        return []

    buckets: dict = {}
    for err in errors:
        ts = err.get("timestamp_epoch", 0)
        bucket_key = int(ts / bucket_seconds) * bucket_seconds
        if bucket_key not in buckets:
            buckets[bucket_key] = {
                "time": bucket_key,
                "count": 0,
                "worms": set(),
                "categories": set(),
                "examples": [],
            }
        buckets[bucket_key]["count"] += 1
        buckets[bucket_key]["worms"].add(err["worm"])
        buckets[bucket_key]["categories"].add(err.get("category", "?"))
        if len(buckets[bucket_key]["examples"]) < 2:
            buckets[bucket_key]["examples"].append(err["error"][:60])

    result = sorted(buckets.values(), key=lambda x: -x["time"])
    return result


# ── Display ──────────────────────────────────────────────────────

def format_timestamp(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def show(dlq, interval: int, color: bool, auto_retry: bool, prev_buckets: deque):
    """Render the DLQ watch display."""
    c = _color(color)

    summary = dlq.get_dead_letter_summary(lookback_seconds=86400 * 7)
    buckets = bucket_errors(summary.get("recent_errors", []), bucket_seconds=30)

    # Track new buckets since last refresh
    new_this_cycle = 0
    if prev_buckets:
        current_keys = {b["time"] for b in buckets}
        prev_keys = {b["time"] for b in prev_buckets}
        new_keys = current_keys - prev_keys
        new_this_cycle = sum(
            b["count"] for b in buckets if b["time"] in new_keys
        )

    # Clear and render
    print(c.CLEAR, end="")
    timestamp = format_timestamp(time.time())

    # Header
    if summary["total_failed"] > 0:
        header_color = c.RED if summary["total_failed"] > 20 else c.YELLOW
    else:
        header_color = c.GREEN
    print(f"{c.BOLD}DLQ WATCH{c.RESET}  {c.DIM}{timestamp}{c.RESET}")
    print(f"  Total: {header_color}{summary['total_failed']}{c.RESET} "
          f"| Categories: {summary['affected_categories']} "
          f"| Refresh: {interval}s"
          f"{c.RED if new_this_cycle > 0 else c.GREEN}"
          f" | New: +{new_this_cycle}{c.RESET}")
    print()

    if summary["total_failed"] == 0:
        print(f"  {c.GREEN}✅ No failed executions{c.RESET}")
        return

    # Alert thresholds (numeric epoch; ISO stamps are display-only)
    hourly = [e for e in summary.get("recent_errors", [])
              if e.get("timestamp_epoch", 0) >= time.time() - 3600]
    if len(hourly) > 20:
        print(f"  {c.RED}{c.BOLD}CRITICAL:{c.RESET} {len(hourly)} failures in last hour")
    elif len(hourly) > 5:
        print(f"  {c.YELLOW}⚠ WARNING:{c.RESET} {len(hourly)} failures in last hour")

    # Time-bucketed error groups
    if buckets:
        print(f"  {c.BOLD}Error Timeline (30s buckets):{c.RESET}")
        for bucket in buckets[:8]:  # Show last 8 buckets
            ts = format_timestamp(bucket["time"])
            bar_len = min(bucket["count"], 40)
            bar = "█" * bar_len
            color = c.RED if bucket["count"] >= 5 else (c.YELLOW if bucket["count"] >= 3 else c.CYAN)
            print(f"    {c.DIM}[{ts}]{c.RESET} {color}{bar}{c.RESET} {bucket['count']}x "
                  f"({len(bucket['worms'])} worms, {len(bucket['categories'])} categories)")
            for ex in bucket["examples"]:
                print(f"      {c.DIM}{ex}{c.RESET}")
        if len(buckets) > 8:
            print(f"    {c.DIM}... and {len(buckets) - 8} earlier buckets{c.RESET}")

    # Per-worm breakdown
    if summary.get("per_worm"):
        print()
        top_worms = sorted(summary["per_worm"].items(), key=lambda x: -x[1])[:5]
        for worm, count in top_worms:
            color = c.RED if count > 10 else (c.YELLOW if count > 3 else c.CYAN)
            bar = "█" * min(count, 30)
            print(f"  {color}{bar}{c.RESET} {worm} ({count}x)")

    # Auto-retry (eligibility report when no executor is attached)
    if auto_retry and summary["total_failed"] > 0:
        print()
        print(f"  {c.YELLOW}Auto-retrying...{c.RESET}")
        result = dlq.retry_all_failed_worms()
        print(f"  {c.DIM}eligible: {result['total_retried']}{c.RESET}")


# ── Main Loop ────────────────────────────────────────────────────

def main():

    parser = argparse.ArgumentParser(
        description="DLQ Watch — Dead Letter Queue Monitor",
    )
    parser.add_argument("--interval", "-i", type=int, default=5,
                        help="Refresh interval in seconds (default: 5)")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable ANSI color output")
    parser.add_argument("--retry", "-r", action="store_true",
                        help="Auto-retry failed worms on each cycle")
    args = parser.parse_args()

    color = not args.no_color
    interval = max(1, args.interval)

    prev_buckets: deque = deque(maxlen=20)
    from CORE.WORMS.dead_letter_queue import DeadLetterQueue
    dlq = DeadLetterQueue()

    try:
        while True:
            show(dlq, interval, color, args.retry, prev_buckets)
            # Update prev_buckets for next comparison
            recent_summary = dlq.get_dead_letter_summary(lookback_seconds=86400 * 7)
            buckets = bucket_errors(recent_summary.get("recent_errors", []), bucket_seconds=30)
            prev_buckets.clear()
            prev_buckets.extend(buckets)
            time.sleep(interval)
    except KeyboardInterrupt:
        c = Colors if color else NO_COLORS
        print(f"\n  {c.DIM}Watch stopped.{c.RESET}")


if __name__ == "__main__":
    main()
