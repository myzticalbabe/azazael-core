#!/usr/bin/env python3
# coding: utf-8
"""render_all_monitors.py — single Chain-runnable monitor refresh (F: monitors).

Runs tools/render_monitors.py (azazael/exmachina/lilith) + tools/
render_ml_rl_monitors.py (ml/rl) + tools/render_ouroboros.py (the loop's own
liveness) back to back, fail-open per renderer: one renderer crashing must not
take down the others. Exit 0 unless ALL fail. Prints one summary line for the
Chain log.

The ouroboros renderer is new (2026-09-30) and is the only one that reads the
instruments' own output rather than the mesh: it paints
state/loop_integrity.json, state/fitness_arms.json,
state/terence_invariants.json and state/chain_ops.json onto a ten-stage ring, so
the wallboard is a readout of the loop rather than a picture of one.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _run(target: str, args: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, str(HERE / target), *args],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace")
    except Exception as exc:
        return False, f"{target}: spawn failed: {exc}"[:160]
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    tail = tail[-1] if tail else ""
    return proc.returncode == 0, f"{target}: rc={proc.returncode} {tail}"[:160]


def main() -> int:
    results = [
        _run("render_monitors.py", []),
        _run("render_ml_rl_monitors.py", []),
        _run("render_ouroboros.py", []),
    ]
    print("render-all: " + " | ".join(m for _, m in results), flush=True)
    # fail-open: one dead renderer must not blank the wallboards
    return 0 if any(ok for ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
