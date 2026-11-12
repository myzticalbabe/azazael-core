#!/usr/bin/env python3
"""
DLQ Burndown — Terminal Burndown Chart of DLQ Failures

Shows a 24-hour burndown chart of worm execution failures,
grouped into hourly buckets with trends and projections.

Usage:
    python scripts/dlq_burndown.py                    # Full 24h burndown
    python scripts/dlq_burndown.py --hours 6          # Last 6 hours
    python scripts/dlq_burndown.py --no-color         # Monochrome mode
    python scripts/dlq_burndown.py --json             # JSON output
"""
import sys
import os
import time
import json
import math
from pathlib import Path
from collections import defaultdict
import argparse

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

NO_COLORS = Colors()
for attr in dir(NO_COLORS):
    if not attr.startswith("_"):
        setattr(NO_COLORS, attr, "")

def _c(color: bool) -> Colors:
    return Colors if color else NO_COLORS


# ── Data ─────────────────────────────────────────────────────────

def get_hourly_buckets(summary, hours: int = 24) -> list:
    """Group DLQ errors into hourly buckets for the last N hours."""
    now = time.time()
    cutoff = now - (hours * 3600)
    buckets: dict = {}
    
    # Initialize all hourly slots
    for h in range(hours):
        bucket_start = now - ((hours - h) * 3600)
        hour_label = time.strftime("%H:%M", time.localtime(bucket_start))
        buckets[bucket_start] = {
            "label": hour_label,
            "timestamp": bucket_start,
            "count": 0,
            "worms": set(),
            "categories": set(),
        }
    
    # Fill in errors (numeric epoch stamps; ISO strings are display-only)
    for err in summary.get("recent_errors", []):
        ts = err.get("timestamp_epoch", 0)
        if ts < cutoff:
            continue
        # Round to hour bucket
        bucket_key = int(ts / 3600) * 3600
        if bucket_key in buckets:
            buckets[bucket_key]["count"] += 1
            buckets[bucket_key]["worms"].add(err["worm"])
            buckets[bucket_key]["categories"].add(err.get("category", "?"))
    
    result = sorted(buckets.values(), key=lambda x: x["timestamp"])
    return result


def compute_trend(buckets: list) -> dict:
    """Compute trend direction and projection."""
    if len(buckets) < 2:
        return {"direction": "stable", "slope": 0.0, "projection_1h": 0}
    
    counts = [b["count"] for b in buckets[-12:] if b["count"] > 0]  # last 12h
    if len(counts) < 2:
        return {"direction": "stable", "slope": 0.0, "projection_1h": 0}
    
    # Simple linear trend: compare first half avg to second half avg
    mid = len(counts) // 2
    first_half = sum(counts[:mid]) / max(len(counts[:mid]), 1)
    second_half = sum(counts[mid:]) / max(len(counts[mid:]), 1)
    slope = second_half - first_half
    
    if slope > 1.0:
        direction = "rising"
    elif slope < -1.0:
        direction = "falling"
    else:
        direction = "stable"
    
    # Projection for next hour
    avg_hourly = sum(counts[-3:]) / max(len(counts[-3:]), 1) if len(counts) >= 3 else sum(counts) / len(counts)
    projected = max(0, int(avg_hourly + slope))
    
    return {"direction": direction, "slope": round(slope, 1), "projection_1h": projected}


# ── Display ──────────────────────────────────────────────────────

def show(hours: int, color: bool, as_json: bool):
    """Render the burndown chart."""
    c = _c(color)
    
    from CORE.WORMS.dead_letter_queue import DeadLetterQueue
    dlq = DeadLetterQueue()
    
    summary = dlq.get_dead_letter_summary(lookback_seconds=hours * 3600)
    buckets = get_hourly_buckets(summary, hours=hours)
    trend = compute_trend(buckets)
    
    max_count = max((b["count"] for b in buckets), default=0)
    chart_width = 40
    
    if as_json:
        output = {
            "hours": hours,
            "total_failed": summary["total_failed"],
            "affected_categories": summary["affected_categories"],
            "trend": trend,
            "buckets": [
                {
                    "label": b["label"],
                    "count": b["count"],
                    "worms": len(b["worms"]),
                    "categories": len(b["categories"]),
                }
                for b in buckets
            ],
        }
        print(json.dumps(output, indent=2))
        return
    
    # Header
    trend_icon = {"rising": "↑", "falling": "↓", "stable": "→"}.get(trend["direction"], "→")
    trend_color = c.RED if trend["direction"] == "rising" else (c.GREEN if trend["direction"] == "falling" else c.YELLOW)
    
    print(f"{c.BOLD}DLQ BURNDOWN{c.RESET}  —  last {hours}h  |  "
          f"Total: {summary['total_failed']}  Categories: {summary['affected_categories']}")
    print(f"  Trend: {trend_color}{trend['direction']} {trend_icon}{c.RESET}  "
          f"(slope {trend['slope']:+0.1f}/h)  "
          f"Projected next hour: ~{trend['projection_1h']} failures")
    print()
    
    if max_count == 0:
        print(f"  {c.GREEN}✅ No failures in the last {hours}h{c.RESET}")
        return
    
    # Burndown chart
    print(f"  {c.BOLD}Failures by Hour:{c.RESET}")
    print(f"  {'─' * (chart_width + 16)}")
    
    for bucket in buckets:
        count = bucket["count"]
        if count == 0:
            continue
        
        bar_len = max(1, int(count / max_count * chart_width))
        bar = "█" * bar_len
        
        bucket_color = c.RED if count >= 5 else (c.YELLOW if count >= 2 else c.CYAN)
        worms_str = f"{len(bucket['worms'])}w" if bucket["worms"] else ""
        
        print(f"  {c.DIM}{bucket['label']:>5}{c.RESET} "
              f"|{bucket_color}{bar}{' ' * (chart_width - bar_len)}{c.RESET}| "
              f"{bucket_color}{count:>3}{c.RESET}  {c.DIM}{worms_str}{c.RESET}")
    
    print(f"  {'─' * (chart_width + 16)}")
    
    # Summary stats
    if summary.get("per_worm"):
        print()
        print(f"  {c.BOLD}Top Worms:{c.RESET}")
        for worm, count in sorted(summary["per_worm"].items(), key=lambda x: -x[1])[:5]:
            bar = "█" * min(count, 30)
            worm_color = c.RED if count > 10 else (c.YELLOW if count > 3 else c.CYAN)
            print(f"    {worm_color}{bar}{c.RESET} {worm} ({count}x)")
    
    # Next steps
    if trend["direction"] == "rising" and trend["projection_1h"] > 5:
        print()
        print(f"  {c.RED}{c.BOLD}⚠ Rising trend detected!{c.RESET}")
        print(f"  Consider: --failed --retry-all to retry all failed worms")


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DLQ Burndown — terminal burndown chart of DLQ failures",
    )
    parser.add_argument("--hours", "-t", type=int, default=24,
                        help="Hours to chart (default: 24)")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable ANSI color output")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()
    
    show(args.hours, not args.no_color, args.json)


if __name__ == "__main__":
    main()
