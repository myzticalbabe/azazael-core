#!/usr/bin/env python3
# coding: utf-8
"""SpitFire CLI — TswArm tree-swarming tool entry point.

This is the main entry point compiled by Nuitka. It delegates to the
OS-appropriate implementation via the launcher.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..compat.launcher import dispatch
from ..spawn_agents import main as spawn_main


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="SpitFire",
        description="TswArm — Tree-swarming census and fix tool",
    )
    ap.add_argument(
        "-V", "--version", action="version", version=f"SpitFire 1.0.0"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # Census command
    c = sub.add_parser("census", help="Run dirty-worktree census (read-only)")
    c.add_argument("--max-workers", type=int, default=32)
    c.add_argument("--save", type=Path, help="Output JSONL path")

    # Fix command
    f = sub.add_parser("fix", help="Apply per-line suppressions to flagged files")
    f.add_argument("--file", type=Path, help="Fix single file only")
    f.add_argument("--report", action="store_true", help="Re-derive deltas only")

    # All command
    a = sub.add_parser("all", help="Run census then fix")

    # Spawn command (parallel builds)
    s = sub.add_parser("spawn", help="Spawn parallel build subagents for targets")
    s.add_argument(
        "--targets",
        default="win7,win81,win10,macos,linux",
        help="Comma-separated targets",
    )
    s.add_argument(
        "--mode",
        choices=["agent_manager", "kilo_task", "specs"],
        default="specs",
        help="Spawn mode",
    )
    s.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for spec files",
    )
    s.add_argument(
        "--background",
        action="store_true",
        help="Run subagents in background",
    )
    s.add_argument(
        "--print-prompts",
        action="store_true",
        help="Print prompts to stdout",
    )

    return ap


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Handle spawn command directly (doesn't need OS dispatch)
    if args.cmd == "spawn":
        return spawn_main()

    # Dispatch to OS-appropriate implementation
    return dispatch(args)


if __name__ == "__main__":
    sys.exit(main())