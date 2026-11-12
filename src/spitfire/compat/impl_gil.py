#!/usr/bin/env python3
# coding: utf-8
"""GIL implementation for legacy Windows (7, 8.1).

Runs under Python 3.8/3.12 with the GIL. Uses shared census logic.
"""

from __future__ import annotations

import argparse

from .shared import run_dispatcher


def run(args: argparse.Namespace, target: str = "") -> int:
    """Entry point the launcher calls: args carries cmd, max_workers and save."""
    workers = getattr(args, "max_workers", None) or 32
    return run_dispatcher(args, workers, gil_free=False, impl_name="GIL")


def main() -> int:
    """Entry point for direct execution (testing)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-workers", type=int, default=32)
    parser.add_argument("--save", type=str)
    parser.add_argument("cmd", choices=["census", "fix", "all"])
    args = parser.parse_args()

    # GIL implementation: gil_free=False
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())