#!/usr/bin/env python3
# coding: utf-8
"""fitness_arm_liveness.py — is every objective arm actually alive?

THE FINDING THIS EXISTS FOR
---------------------------
The evolution cycle's fitness has two arms, `info` and `alert`, and `BEST
parts:` reports them separately. Measured over 14+ consecutive cycles:

    total=17.2115 info=17.2115 alert=0.0
    total=16.7202 info=16.7202 alert=0.0
    total=17.269  info=17.269  alert=0.0
    ... every single line.

`total` IS `info`. The `alert` arm is not bad or declining, it is exactly
zero, so the loop has been running a single-objective search while reporting
a two-objective score. The `frozen pools: info=500 alert=7` line says why:
the alert pool holds 7 rows against info's 500, a 1:71 ratio, and a learner
scores 0.0 on seven episodes.

WHY NOTHING CAUGHT IT
--------------------
Every instrument available reads `total`, and `total` looks healthy:
`audit_loop_liveness` sees a live loop; `evolution_progress` sees zero
promotions and attributes it to schema alternation; and the margin
distribution measured by `process_benchmark` is genuinely well-behaved
(n=34, p75=0.9012, 82.4% beating baseline) -- because it is entirely the info
arm. A scalar that aggregates a live component and a dead one is
indistinguishable from a scalar where both are alive. That is the same defect
as the Intermesh finding that 22 of 26 natures declare no data contract, so
`emits`/`requires` return empty dicts that read as CLEAN.

THE RULE
--------
A total is only a multi-objective score if every component contributes. This
file reports each arm's liveness, its share of the total, and the pool size
feeding it, and treats `total == arm` as a FAILURE STATE rather than a
decomposition. Absence of a component must not read as health.

Usage:
    python tools/fitness_arm_liveness.py
    python tools/fitness_arm_liveness.py --json
Exit: 0 ALL ARMS LIVE / 1 DEAD OR STARVED ARM / 2 NO_DATA / 3 NO_INPUT

NO_DATA MEANS IT READ THE LOG AND FOUND NO `BEST parts:` LINES. NO_INPUT MEANS
IT COULD NOT READ THE LOG AT ALL. Until 2026-10-11 those were one verdict with
one exit code, so a missing log reported exactly like a loop that never scored --
blindness rendered as a measurement (action registry item L-5).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "evolution_cycle.log")
OUT = os.path.join(ROOT, "state", "fitness_arms.json")

# The log's line vocabulary is defined once, in tools/evolution_log.py, so this
# reader cannot drift from the other three. RE_PROMO here is the short standing
# line; evolution_progress reads the same line to full sprt depth.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evolution_log import (RE_MORPH, RE_PARTS, RE_POOLS, RE_PROMO,  # noqa: E402
                           iter_lines)

ARMS = ("info", "alert")
ZERO = 1e-9          # a component at or below this contributes nothing
MIN_POOL_RATIO = 0.10   # an arm with <10% of the largest pool is starved


def _input_state(path: str) -> dict:
    """What the reader actually got, so NO_DATA cannot mean two things.

    MEASURED (action registry item L-5): this tool reported NO_DATA with the same
    message and the same exit code whether the log was absent or
    present-without-`BEST parts:` lines, so "I could not read my input" and "the
    loop never scored a two-arm total" were the same report. This states which.
    """
    if not os.path.exists(path):
        return {"path": path, "found": False, "bytes": 0, "unreadable": True,
                "reason": "log not found"}
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return {"path": path, "found": True, "bytes": 0, "unreadable": True,
                "reason": "stat failed: %s" % exc}
    if size == 0:
        return {"path": path, "found": True, "bytes": 0, "unreadable": False,
                "reason": "log exists but is empty"}
    return {"path": path, "found": True, "bytes": size, "unreadable": False,
            "reason": "read"}


def parse(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    parts, pools, arms_seq, cur_gene = [], [], [], None
    for line in iter_lines(path):
        m = RE_PARTS.match(line)
        if m:
            parts.append({"ts": m.group(1), "total": float(m.group(2)),
                          "info": float(m.group(3)), "alert": float(m.group(4))})
            continue
        m = RE_POOLS.match(line)
        if m:
            pools.append({"ts": m.group(1), "info": int(m.group(2)),
                          "alert": int(m.group(3))})
            continue
        m = RE_MORPH.match(line)
        if m:
            cur_gene = m.group(2)
            continue
    return {"parts": parts, "pools": pools}


def analyse(d: dict) -> dict:
    parts, pools = d.get("parts") or [], d.get("pools") or []
    if not parts:
        return {"verdict": "NO_DATA", "cycles": 0}

    out = {}
    for arm in ARMS:
        xs = [p[arm] for p in parts]
        live = [x for x in xs if x > ZERO]
        # share of the reported total this arm accounts for, on each cycle
        shares = [(p[arm] / p["total"]) if p["total"] else 0.0 for p in parts]
        pool = next((q[arm] for q in reversed(pools) if arm in q), None)
        out[arm] = {
            "n": len(xs),
            "nonzero_cycles": len(live),
            "always_zero": len(live) == 0,
            "mean": round(statistics.fmean(xs), 4) if xs else None,
            "max": round(max(xs), 4) if xs else None,
            "mean_share_of_total": round(statistics.fmean(shares), 4) if shares else None,
            "pool_rows": pool,
        }

    biggest = max((out[a]["pool_rows"] or 0) for a in ARMS) if any(
        out[a]["pool_rows"] for a in ARMS) else 0
    starved = [a for a in ARMS
               if out[a]["pool_rows"] is not None and biggest
               and out[a]["pool_rows"] / biggest < MIN_POOL_RATIO]

    findings = []
    for a in ARMS:
        if out[a]["always_zero"]:
            findings.append(
                f"ARM DEAD: `{a}` is exactly 0.0 on all {out[a]['n']} cycles. "
                f"total is therefore identical to the other arm, and the score "
                f"does not represent {a} at all.")
        elif out[a]["mean_share_of_total"] is not None and out[a]["mean_share_of_total"] < 0.05:
            findings.append(
                f"ARM STARVED: `{a}` contributes "
                f"{out[a]['mean_share_of_total']:.1%} of the reported total.")
    for a in starved:
        findings.append(
            f"POOL STARVED: `{a}` has {out[a]['pool_rows']} rows against "
            f"{biggest} for the largest arm "
            f"({out[a]['pool_rows'] / biggest:.1%} of it, "
            f"threshold {MIN_POOL_RATIO:.0%}). A learner cannot score on an "
            f"arm with almost no episodes, so the arm is unfittable by "
            f"construction rather than by search failure.")

    return {
        "verdict": "DEAD_ARM" if findings else "ALL_ARMS_LIVE",
        "cycles": len(parts),
        "total_mean": round(statistics.fmean([p["total"] for p in parts]), 4),
        "total_is_pure_single_arm": any(
            out[a]["always_zero"] for a in ARMS),
        "arms": out,
        "findings": findings,
        "recommended": (
            "Two options, and the choice is not a measurement question: either "
            "grow the starved pool until the arm is fittable, or formally "
            "RETIRE the arm so the reported fitness stops implying a capability "
            "that does not exist. Promoting on a total that equals one arm is "
            "not available as a third option."
        ) if findings else None,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    state = _input_state(LOG)
    rep = analyse(parse(LOG))
    rep["input"] = state
    if rep.get("verdict") != "NO_DATA" and not a.no_write:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, default=str)

    if a.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        if rep["verdict"] == "NO_DATA":
            if not state["found"] or state["unreadable"]:
                print("fitness_arm_liveness: NO_INPUT - could not read %s (%s)"
                      % (LOG, state["reason"]))
                return 3
            print("fitness_arm_liveness: NO_DATA - read %s (%d bytes) but parsed "
                  "no `BEST parts:` lines" % (LOG, state["bytes"]))
            return 2
        print(f"fitness_arm_liveness: {rep['verdict']}")
        print(f"  cycles: {rep['cycles']}   mean total: {rep['total_mean']}")
        print(f"  total is a PURE single-arm score: {rep['total_is_pure_single_arm']}")
        for arm, v in rep["arms"].items():
            print(f"  {arm:<6} nonzero={v['nonzero_cycles']}/{v['n']:<4} "
                  f"mean={v['mean']:<9} share={v['mean_share_of_total']:<7} "
                  f"pool_rows={v['pool_rows']}")
        for f in rep["findings"]:
            print(f"\n  - {f}")
        if rep.get("recommended"):
            print(f"\n  {rep['recommended']}")
    return 0 if rep["verdict"] == "ALL_ARMS_LIVE" else 1


if __name__ == "__main__":
    sys.exit(main())
