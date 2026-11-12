#!/usr/bin/env python3
"""stack_phase0.py -- STACK Phase 0 measurement (read-only, zero behavior change).

Measures what a stacking meta-learner could actually learn from, given that
tracks emit aggregate run metrics (not per-worm predictions):

  1. Track reward trajectories + pairwise Pearson (shared-drift check: do
     tracks rise/fall together with the environment, or independently?).
  2. Uncertainty coverage per track (which tracks report usable spreads).
  3. Per-worm-class outcome volume (is class-level routing feasible now?).
  4. Instrumentation gap: per-decision track scores don't exist yet --
     quantifies what L1 grain is feasible today vs needs new emission.

Usage:
    python scripts/stack_phase0.py [--json] [--limit N]

Exit 0 always (measurement never fails the caller); prints report.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n != len(ys) or n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            / math.sqrt(sxx * syy))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stack_phase0")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args(argv)

    from track_registry import ROTATION, REWARD_EVENTS
    from CORE.local_memory import get_memory_store
    store = get_memory_store()

    series: dict[str, list[float]] = {}
    unc: dict[str, dict] = {}
    for algo in ROTATION:
        vals: list[float] = []
        spreads = 0
        total = 0
        if algo in REWARD_EVENTS:
            et, field, src = REWARD_EVENTS[algo]
            rows = store.query_memories(event_type=et, limit=args.limit)
            for r in rows:
                d = r.get("data", {})
                if isinstance(d, str):
                    try:
                        d = json.loads(d)
                    except Exception:
                        continue
                if src is not None and r.get("source") != src:
                    continue
                v = d.get(field)
                if isinstance(v, (int, float)):
                    vals.append(float(v))
                    total += 1
                    # Spread coverage for custom tracks: accept any std key.
                    # RL/DREAMTUNE/WORMGA/ALLPAR emit no eval_std_reward yet,
                    # so they report 0.0 until instrumented — same rule as SB3.
                    for _sk in ("eval_std_reward", "std_reward", "std",
                                "spread", "reward_std"):
                        if isinstance(d.get(_sk), (int, float)):
                            spreads += 1
                            break
        else:
            rows = store.fetch_all_memories(
                event_type="sb3_training_orchestrated", limit=5000)
            for r in rows:
                d = r.get("data", {})
                if isinstance(d, str):
                    try:
                        d = json.loads(d)
                    except Exception:
                        continue
                if d.get("algo") != algo:
                    continue
                v = d.get("eval_mean_reward")
                if isinstance(v, (int, float)):
                    vals.append(float(v))
                    total += 1
                    # 2026-09-28 fix: spreads were only counted for custom
                    # tracks, reporting 0.0 for all SB3 tracks while their
                    # stored rows DO carry eval_std_reward.
                    if isinstance(d.get("eval_std_reward"), (int, float)):
                        spreads += 1
        vals.reverse()  # oldest first for trajectory sense
        series[algo] = vals
        unc[algo] = {"n": total,
                     "with_spread": spreads,
                     "spread_coverage": round(spreads / total, 2) if total else 0.0}

    # Pairwise trajectory correlation (shared drift vs independence).
    algos = [a for a in ROTATION if len(series.get(a, [])) >= 3]
    pairs = []
    for i in range(len(algos)):
        for j in range(i + 1, len(algos)):
            a, b = algos[i], algos[j]
            n = min(len(series[a]), len(series[b]))
            r = _pearson(series[a][-n:], series[b][-n:])
            if r is not None:
                pairs.append({"a": a, "b": b, "r": round(r, 3), "n": n})
    pairs.sort(key=lambda p: -abs(p["r"]))
    redund = [p for p in pairs if abs(p["r"]) >= 0.9]
    indep = [p for p in pairs if abs(p["r"]) < 0.3]

    # Per-worm-class outcome volume (class-level routing feasibility).
    # Family = nature field when present (the designed grouping), else the
    # worm-name stem. Earlier prefix-slicing shattered families into n=2
    # shards and understated feasibility.
    fam_counts: dict[str, int] = defaultdict(int)
    fam_wins: dict[str, int] = defaultdict(int)
    try:
        rows = store.fetch_all_memories(
            event_type="clusterwormz_worm_executed", limit=5000)
        for r in rows:
            d = r.get("data", {})
            if isinstance(d, str):
                try:
                    d = json.loads(d)
                except Exception:
                    continue
            fam = str(d.get("nature") or d.get("cluster_type")
                      or str(d.get("worm", "?"))[:12])
            fam_counts[fam] += 1
            if d.get("success"):
                fam_wins[fam] += 1
    except Exception:
        pass
    fams = sorted(fam_counts.items(), key=lambda kv: -kv[1])[:15]

    # E3-TIR ROI proxy (2026-09-29): mean reward per run-row. Tracks cost
    # no data budget to report, so rows stand in for cost: reward per row
    # ranks tracks by return on data, not just raw reward.
    roi = {}
    for a in ROTATION:
        vals = series.get(a, [])
        n = unc.get(a, {}).get("n", 0) or len(vals)
        if vals and n:
            roi[a] = round((sum(vals) / len(vals)) / max(1, n), 5)

    report = {
        "tracks_measured": {a: len(series.get(a, [])) for a in ROTATION},
        "top_correlated_pairs": pairs[:10],
        "redundant_pairs_r_ge_09": redund,
        "independent_pairs_r_lt_03": len(indep),
        "uncertainty_coverage": unc,
        "roi_per_row": roi,
        "top_worm_families": [
            {"family": f, "n": n,
             "win_rate": round(fam_wins[f] / n, 3)} for f, n in fams],
        "instrumentation_gap": (
            "Tracks emit aggregate run metrics, not per-worm predictions. "
            "Class-level routing (worm-family x track win rates) is feasible "
            "NOW from the ledger. Per-decision stacking needs tracks to emit "
            "per-worm scores -- new instrumentation, Phase 2 prerequisite."),
    }

    if args.json:
        print(json.dumps(report, indent=1, default=str))
        return 0

    print("=== STACK Phase 0: redundancy + feasibility ===")
    print("tracks with >=3 runs:",
          sum(1 for a in ROTATION if len(series.get(a, [])) >= 3),
          "/", len(ROTATION))
    print("\n-- top trajectory correlations --")
    for p in pairs[:10]:
        flag = "  <-- REDUNDANT" if abs(p["r"]) >= 0.9 else ""
        print("  %s x %s  r=%s (n=%d)%s" % (p["a"], p["b"], p["r"], p["n"], flag))
    print("\n-- uncertainty coverage (tracks reporting spreads) --")
    for a in ROTATION:
        u = unc[a]
        if u["n"]:
            print("  %s: %d runs, spread coverage %s" % (a, u["n"], u["spread_coverage"]))
    print("\n-- worm-family outcome volume (top 8) --")
    for f, n in fams[:8]:
        print("  %s: n=%d win=%s" % (f, n, round(fam_wins[f] / n, 3)))
    print("\n-- verdict --")
    print("  " + report["instrumentation_gap"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
