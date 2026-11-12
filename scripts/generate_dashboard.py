#!/usr/bin/env python3
"""
generate_dashboard.py — Generate standalone Azazael dashboard index.html

Exports a complete HTML dashboard with all cards (Health, DB Monitor, Metrics,
Drift, Dead Letters, Session, MetaCluster, Workflows, Training, SB3 Training,
Configuration) to a standalone HTML file.

Usage:
    python scripts/generate_dashboard.py                          # → dashboard/index.html
    python scripts/generate_dashboard.py --output custom.html    # Custom path
    python scripts/generate_dashboard.py --open                  # Open in browser
    python scripts/generate_dashboard.py --watch                 # Auto-refresh (5s)
    python scripts/generate_dashboard.py --since 24h             # Last 24h filter
"""

import os
import sys
import time
import argparse
import subprocess
from pathlib import Path

# Add src/ to path
_src = str(Path(__file__).resolve().parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)


def generate_dashboard(output_path: str, since: str = None,
                       watch: bool = False, interval: int = 5,
                       open_browser: bool = False) -> str:
    """Generate a standalone dashboard HTML file.

    Args:
        output_path: Path for the output HTML file.
        since: Optional time filter ("24h", "30m", etc.).
        watch: If True, regenerate on interval.
        interval: Refresh interval in seconds (only relevant for watch mode).
        open_browser: If True, open the generated file in browser.

    Returns:
        Absolute path to the generated file.
    """
    # Warm the registry for accurate data
    try:
        from CORE.worm_registry import discover_worms
        discover_worms()
    except Exception:
        pass

    # Use web_dashboard.export_html for the canonical card rendering
    from CORE.web_dashboard import export_html

    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    path = export_html(output_path, since=since)
    print(f"  Dashboard exported to {path}")

    if open_browser:
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False, timeout=30)
            else:
                subprocess.run(["xdg-open", path], check=False, timeout=30)
        except Exception as e:
            print(f"  [WARN] Could not open browser: {e}")

    return path


def main():
    parser = argparse.ArgumentParser(
        description="Generate standalone Azazael dashboard HTML file",
    )
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: dashboard/index.html)")
    parser.add_argument("--since", default=None,
                        help='Time filter: "24h", "30m", "3600s", or ISO date')
    parser.add_argument("--open", "-O", action="store_true",
                        help="Open the generated file in browser")
    parser.add_argument("--watch", "-w", action="store_true",
                        help="Regenerate every N seconds")
    parser.add_argument("--interval", "-i", type=int, default=5,
                        help="Refresh interval in seconds (default: 5)")

    args = parser.parse_args()

    # Default output: dashboard/index.html next to this script
    if args.output is None:
        script_dir = Path(__file__).resolve().parent
        output = str(script_dir.parent / "dashboard" / "index.html")
    else:
        output = args.output

    if args.watch:
        print(f"  Watching: regenerating every {args.interval}s (Ctrl+C to stop)")
        try:
            while True:
                generate_dashboard(output, since=args.since, open_browser=args.open)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n  Stopped.")
    else:
        generate_dashboard(output, since=args.since, open_browser=args.open)


if __name__ == "__main__":
    main()
