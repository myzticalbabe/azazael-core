#!/usr/bin/env python3
# coding: utf-8
"""Free-threaded implementation for modern OS (Win10+, macOS 10.15+, Linux).

Runs under Python 3.15t+ with free-threaded build. Uses shared census logic.
"""

from __future__ import annotations

import argparse

from .shared import run_dispatcher, is_free_threaded


def run(args: argparse.Namespace, target: str = "") -> int:
    """Entry point the launcher calls: args carries cmd, max_workers and save."""
    workers = getattr(args, "max_workers", None) or 32
    return run_dispatcher(
        args, workers, gil_free=is_free_threaded(), impl_name="free-threaded"
    )


def main() -> int:
    """Entry point for direct execution (testing)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-workers", type=int, default=32)
    parser.add_argument("--save", type=str)
    parser.add_argument("cmd", choices=["census", "fix", "all"])
    args = parser.parse_args()

    # Free-threaded implementation: gil_free=True (detected at runtime)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())