#!/usr/bin/env python3
# coding: utf-8
"""Audit every function in the meta-evolutionary chain -- and trace which fire.

Two questions, one tool:

1. STATIC: every function defined in the adaptive/evolutionary modules, with its
   disposition from the census (wired / pending), so the chain's *reachable*
   surface is enumerated rather than assumed.

2. DYNAMIC (the strace equivalent; there is no strace on Windows): run a real
   `SpineCycleWorm.run_cycle(apply=False)` under `sys.settrace` restricted to the
   chain modules, and record which of those functions are actually ENTERED.

The second is what answers "why doesn't it adapt on its own volition". A function
can be correct, tested, and still never execute because nothing on the driving
path calls it -- `SplitBrainResolverWorm.report_fork` was exactly that: the only
writer of the state its own `resolve()` read, with no caller, so the stage
reported `no_forks` for every fork on every cycle. Static analysis cannot see
that. A call trace can.

Usage:
    python tools/evolution_chain_audit.py              # static audit only
    python tools/evolution_chain_audit.py --trace      # + one traced cycle
    python tools/evolution_chain_audit.py --trace --apply
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]

# The chain-of-events that would have to run for the system to adapt itself.
# Grouped by role so a missing group is visible as a gap, not a count.
CHAIN: dict[str, tuple[str, ...]] = {
    "selection": ("expansion/queens.py", "CORE/worm_governance.py"),
    "variation": ("expansion/morph.py", "expansion/evolution.py"),
    "consolidation": ("expansion/dreaming.py", "expansion/consolidation.py",
                      "expansion/engram.py"),
    "regulation": ("expansion/spine.py", "expansion/mesh.py",
                   "expansion/homeostat.py"),
    "elimination": ("expansion/immune.py", "expansion/quarantine.py"),
    "plasticity": ("expansion/synapse.py", "expansion/dendrite.py",
                   "expansion/axon.py"),
}

CENSUS = "NEURAL_MESH_WIRE_OR_CUT.json"
MAP = "NEURAL_MESH_INTEGRATION_MAP.json"


def _module_path(rel: str) -> Path:
    return ROOT / "src" / "CORE" / "WORMS" / rel


def static_audit() -> dict[str, Any]:
    """Every function in the chain modules, with its census disposition."""
    pending: dict[str, str] = {}
    for name in (CENSUS, MAP):
        p = ROOT / name
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        findings = data.get("findings") or data
        if isinstance(findings, dict):
            for key, rec in findings.items():
                if isinstance(rec, dict):
                    pending[str(key)] = str(rec.get("disposition") or "pending")
        elif isinstance(findings, list):
            for rec in findings:
                if isinstance(rec, dict):
                    pending[str(rec.get("qualname") or rec.get("key"))] = str(
                        rec.get("disposition") or "pending")

    out: dict[str, Any] = {}
    for role, rels in CHAIN.items():
        rows: list[dict[str, Any]] = []
        for rel in rels:
            path = _module_path(rel)
            if not path.is_file():
                rows.append({"module": rel, "error": "missing"})
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError as exc:
                rows.append({"module": rel, "error": "syntax: %s" % exc})
                continue
            count = 0
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("__"):
                    continue  # dunders are reached implicitly, PF16 calibrates them out
                count += 1
            rows.append({"module": rel, "functions": count})
        out[role] = rows
    out["_pending"] = pending
    return out


def _chain_module_of(filename: str) -> str | None:
    """Role for a file, if it belongs to the chain."""
    norm = filename.replace("\\", "/").lower()
    for role, rels in CHAIN.items():
        for rel in rels:
            if norm.endswith(rel.lower()):
                return role
    return None


def trace_cycle(apply: bool, max_seconds: float) -> dict[str, Any]:
    """Run a real cycle under a chain-restricted call tracer.

    The tracer only records ENTER events for files in the chain, so overhead is
    bounded to those modules rather than the whole interpreter.
    """
    sys.path.insert(0, str(ROOT / "src"))
    fired: dict[str, set[str]] = {}
    started = time.time()

    def local_tracer(frame, event, arg):
        if event != "call":
            return None
        role = _chain_module_of(frame.f_code.co_filename)
        if role is None:
            return None
        if time.time() - started > max_seconds:
            return None
        fired.setdefault(role, set()).add(
            "%s:%s" % (Path(frame.f_code.co_filename).name, frame.f_code.co_name))
        return None

    result: dict[str, Any] = {"apply": apply, "error": None}
    try:
        from CORE.WORMS.expansion.spine import SpineCycleWorm
        cyc = SpineCycleWorm()
        sys.settrace(local_tracer)
        try:
            report = cyc.run_cycle(apply=apply)
        finally:
            sys.settrace(None)
        if isinstance(report, dict):
            result["stage_keys"] = sorted(report.keys())
            result["summary"] = {
                k: (v if not isinstance(v, dict) else
                    {kk: vv for kk, vv in v.items()
                     if kk in ("success", "reason", "candidates", "evaluated",
                               "retired", "proposed", "applied", "current",
                               "previous", "new", "resolved", "entrenched",
                               "changes", "verified", "restore_ok")})
                for k, v in list(report.items())[:40]
            }
    except Exception as exc:  # a broken organ must not hide the trace
        result["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    result["elapsed_s"] = round(time.time() - started, 2)
    result["fired"] = {k: sorted(v) for k, v in fired.items()}
    result["fired_counts"] = {k: len(v) for k, v in fired.items()}
    return result


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trace", action="store_true", help="run one traced cycle")
    ap.add_argument("--apply", action="store_true", help="pass apply=True to the cycle")
    ap.add_argument("--max-seconds", type=float, default=240.0,
                    help="stop recording after N seconds")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    static = static_audit()
    out: dict[str, Any] = {"static": static}
    if args.trace:
        out["trace"] = trace_cycle(args.apply, args.max_seconds)

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print("=== META-EVOLUTIONARY CHAIN: static surface ===")
    for role, rows in static.items():
        if role.startswith("_"):
            continue
        total = sum(r.get("functions", 0) for r in rows)
        missing = [r["module"] for r in rows if "error" in r]
        print("  %-14s %3d functions across %d module(s)%s"
              % (role, total, len(rows), "  MISSING: %s" % missing if missing else ""))

    if args.trace:
        t = out["trace"]
        print()
        print("=== TRACED CYCLE (apply=%s, %.2fs) ===" % (t["apply"], t["elapsed_s"]))
        if t.get("error"):
            print("  cycle raised: %s" % t["error"])
        print("  stages reported: %s" % ", ".join(t.get("stage_keys") or [])[:400])
        print("  functions ENTERED per role:")
        for role in CHAIN:
            n = t["fired_counts"].get(role, 0)
            print("    %-14s %3d %s" % (role, n, "" if n else "  <-- ENTIRE ROLE NEVER RAN"))
        print()
        for role, names in sorted(t["fired"].items()):
            print("  [%s] %s" % (role, ", ".join(names[:14])))
        summary = t.get("summary") or {}
        for key in ("morphogenesis", "queens", "fabric", "homeostat", "dreams",
                    "quarantine", "topology", "evolution"):
            if key in summary:
                print("  %-14s %s" % (key, json.dumps(summary[key], default=str)[:180]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
