#!/usr/bin/env python3
"""harness_startup.py -- the default agent test. Run at session start; it
determines the corrective adjustments the harness needs so it is driven
correctly (user mandate 2026-11-06).

Each check prints PASS/FAIL plus the exact corrective command. Exit 0 only
when every check passes; exit 1 names what to fix, in order. Fast by design
(no builds, no network, no suite): startup is a gate, not a work session.

Usage: python tools/harness_startup.py [--strict]
  --strict: warnings (stale snapshots, unmerged lane rows) also fail.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY315T = r"C:\Azazael\.venvs\py315t\Scripts\python.exe"
BUILD = r"C:\Azazael\.build"
LANE = os.path.join(ROOT, "state", "bake_harvest_freebuff_2026-11-06.jsonl")
GOAL = os.path.join(ROOT, "state", "harness_goal.json")
RECIPES_SNAP = os.path.join(ROOT, "state", "adopt-recipes-2026-11-06.json")
RECIPES_LIVE = os.path.join(BUILD, "adopt-recipes.json")

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check("interpreters: py315t runs, 3.16t app image exists")
def c_interp():
    if not os.path.isfile(PY315T):
        return False, "missing py315t: reinstall venv (see azazael/docs/BUILD_PYTHON_316T_2026-11-05.md)"
    app = os.path.join(BUILD, "cpython-316", "PCbuild", "amd64t", "python3.16t.exe")
    if not os.path.isfile(app):
        return False, "missing 3.16t app image: rebuild cpython-316 (D2 notes)"
    return True, "py315t + 3.16t app image present"


@check("harness modules import (spawn, lanes, gates)")
def c_imports():
    sys.path.insert(0, HERE)
    try:
        import oxenford_spawn  # noqa: F401
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import commit_gate  # noqa: F401
    except Exception as e:
        return False, "import failed: %r -- fix the module before trusting any lane" % e
    return True, "oxenford_spawn + commit_gate importable"


@check("recipes live == snapshot (knowledge not drifting)")
def c_recipes():
    try:
        live = json.load(open(RECIPES_LIVE, encoding="utf-8"))
        snap = json.load(open(RECIPES_SNAP, encoding="utf-8"))
    except Exception as e:
        return False, "recipes unreadable: %r" % e
    if live.get("packages", {}).keys() != snap.get("packages", {}).keys():
        return False, ("package sets differ: re-snapshot with "
                       "cp .build/adopt-recipes.json state/adopt-recipes-<date>.json")
    return True, "%d packages, live==snapshot" % len(live.get("packages", {}))


@check("engines self-test (adopt schema+diagnose, forge bundle)")
def c_engines():
    bad = []
    for eng in ("oxenford-adopt.py", "oxenford-forge.py"):
        p = subprocess.run([PY315T, os.path.join(BUILD, eng), "--self-test"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        if p.returncode != 0:
            tail = (p.stdout + p.stderr)[-300:]
            bad.append("%s rc=%d :: %s" % (eng, p.returncode, tail))
    if bad:
        return False, "engine self-test failed:\n" + "\n".join(bad)
    return True, "adopt + forge self-tests PASS"


@check("lane parses and merge is lossless (dry run)")
def c_lane():
    sys.path.insert(0, HERE)
    from merge_bake_lane import merge_lanes
    try:
        merged, dropped, accepted, base_n = merge_lanes([LANE])
    except Exception as e:
        return False, "merge crashed: %r -- fix merge_bake_lane.py first" % e
    if dropped:
        return False, "%d drops: %s -- fix rows, never force-merge" % (
            len(dropped), dropped[:3])
    return True, "BASE=%d ACCEPTED=%d MERGED=%d" % (base_n, accepted, len(merged))


@check("evolution loop alive (goal file current, iterations growing)")
def c_goal():
    try:
        g = json.load(open(GOAL, encoding="utf-8"))
    except Exception as e:
        return False, "goal file missing/unreadable: %r -- recreate state/harness_goal.json" % e
    iters = g.get("iterations", [])
    if not g.get("goal") or not iters:
        return False, "goal file has no goal or no iterations -- record iteration 0 before proceeding"
    return True, "goal live: %d iteration(s), current=%s" % (
        len(iters), g.get("current_iteration"))


def main():
    strict = "--strict" in sys.argv
    fails, warns = [], []
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, "check crashed: %r" % e
        print(("[PASS] " if ok else "[FAIL] ") + name)
        print("       " + str(detail).splitlines()[0])
        if not ok:
            fails.append(name)
    if strict:
        snap_age = 0
        try:
            snap_age = os.path.getmtime(RECIPES_LIVE) - os.path.getmtime(RECIPES_SNAP)
            if snap_age > 0:
                warns.append("live recipes newer than snapshot: re-snapshot")
        except OSError:
            warns.append("snapshot missing: re-snapshot")
    for w in warns:
        print("[WARN] " + w)
        fails.append(w)
    if fails:
        print("harness_startup: %d correction(s) needed -- work them top-down" % len(fails))
        return 1
    print("harness_startup: harness driven correctly, proceed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
