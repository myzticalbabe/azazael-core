# coding: utf-8
"""Why does the promotion margin gate almost never pass?

Observed: 100 consecutive live cycles with `beats=False`. Cranium is guarded by
`beats`, so the organ is unreachable for a reason that has nothing to do with
Cranium.

`beats = (best_total - baseline_fit) > PROMOTE_MARGIN`, where `baseline_fit` is
the fitness of whatever genome is in `state/evolved_rl_params.json`. That makes
the gate a RATCHET: promoting sets the baseline to the winner, so one promotion
resets the margin to ~0 and every subsequent promotion needs a full
`PROMOTE_MARGIN` of improvement over the current champion.

This script separates the candidate explanations with measurements rather than
arguments:

  R  ratchet        margin distribution sits below the gate and baseline tracks
                    the best-ever fitness
  L  landscape      random genomes span a narrow range, so +0.75 does not exist
  D  drift          baseline rises over time faster than best_total

Run:  python scripts/diagnose_margin_gate.py
"""
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import evolution_cycle as ec  # noqa: E402


def rows(limit=400):
    from CORE.local_memory import get_memory_store
    return get_memory_store().query_memories(
        event_type="evolution_result", limit=limit)


def payload(r):
    d = r.get("data") if isinstance(r, dict) else None
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            return None  # scanner:ignore -- probe contract: unparseable payload rows are skipped, never crash the diagnosis
    return d if isinstance(d, dict) else None


def main():
    print("=" * 74)
    print("MARGIN GATE DIAGNOSIS")
    print("=" * 74)
    print("PROMOTE_MARGIN = %s   (gate: best_total - baseline > this)"
          % ec.PROMOTE_MARGIN)

    pf = ec.PROMOTE_JSON
    print("\n[promoted champion]  %s" % pf)
    if os.path.exists(pf):
        data = json.load(open(pf, encoding="utf-8"))
        print("  genes    : %s" % {k: round(float(v), 4)
                                   for k, v in (data.get("genes") or {}).items()})
        print("  fitness  : %s" % data.get("fitness"))
        print("  baseline : %s  wins %s  margin %s"
              % (data.get("baseline"), data.get("wins"), data.get("margin")))
        print("  promoted : %s" % data.get("promoted_at"))
        print("  holdout  : %s" % data.get("holdout"))
    else:
        print("  (none — built-in ScheduleConfig defaults are the baseline)")

    base = ec.production_genes()
    print("\n[baseline in force]  %s"
          % {k: round(float(v), 4) for k, v in base.items()})

    all_rows = rows()
    recs = []
    for r in all_rows:
        d = payload(r)
        if not d or not str(d.get("fitness", "")).startswith("v3"):
            continue
        bt, bl = d.get("best_total"), d.get("baseline")
        try:
            bt, bl = float(bt), float(bl)
        except (TypeError, ValueError):
            continue
        m = d.get("margin")
        try:
            m = float(m)
        except (TypeError, ValueError):
            m = bt - bl
        recs.append({"seed": d.get("seed"), "best": bt, "base": bl,
                     "margin": m, "promoted": bool(d.get("promoted")),
                     "pool": str(d.get("pool_id") or "")[:10]})

    print("\n[history]  %d usable v3 rows" % len(recs))
    if not recs:
        print("no comparable history")
        return 0

    ms = [r["margin"] for r in recs]
    print("  margin  mean %+.3f  median %+.3f  sd %.3f  min %+.3f  max %+.3f"
          % (st.mean(ms), st.median(ms), st.pstdev(ms), min(ms), max(ms)))
    gate = ec.PROMOTE_MARGIN
    over = [m for m in ms if m > gate]
    print("  rows clearing the %.2f gate: %d / %d (%.1f%%)"
          % (gate, len(over), len(ms), 100.0 * len(over) / len(ms)))

    print("\n[distribution vs the gate]")
    edges = [(-1e9, 0.0), (0.0, 0.25), (0.25, 0.5), (0.5, gate),
             (gate, 1.5), (1.5, 1e9)]
    for lo, hi in edges:
        n = sum(1 for m in ms if lo <= m < hi)
        lbl = ("< 0" if lo < -1e8 else "%+.2f..%+.2f" % (lo, hi))
        mark = "  <- GATE" if hi == gate else ""
        print("   %-16s %4d  %s%s" % (lbl, n, "#" * min(60, n), mark))

    promoted = [r for r in recs if r["promoted"]]
    print("\n[ratchet check]  promotions recorded in this window: %d"
          % len(promoted))
    if promoted:
        for r in promoted[:5]:
            print("   seed=%s best=%s base=%s margin=%+.3f"
                  % (r["seed"], r["best"], r["base"], r["margin"]))

    # RATCHET: does the baseline track the historical best?
    bests = [r["best"] for r in recs]
    bases = [r["base"] for r in recs]
    print("\n  best_total  mean %.3f  max %.3f" % (st.mean(bests), max(bests)))
    print("  baseline    mean %.3f  max %.3f" % (st.mean(bases), max(bases)))
    gap = [b - x for b, x in zip(bests, bases)]
    print("  best-baseline gap: mean %+.3f  median %+.3f  max %+.3f"
          % (st.mean(gap), st.median(gap), max(gap)))

    # First vs last third: is the baseline drifting up faster than the best?
    k = max(1, len(recs) // 3)
    early, late = recs[-k:], recs[:k]     # newest-first ordering
    print("\n[drift]  newest %d vs oldest %d rows" % (len(late), len(early)))
    print("  best   %7.3f -> %7.3f   (%+.3f)"
          % (st.mean([r["best"] for r in early]),
             st.mean([r["best"] for r in late]),
             st.mean([r["best"] for r in late]) - st.mean([r["best"] for r in early])))
    print("  base   %7.3f -> %7.3f   (%+.3f)"
          % (st.mean([r["base"] for r in early]),
             st.mean([r["base"] for r in late]),
             st.mean([r["base"] for r in late]) - st.mean([r["base"] for r in early])))
    print("  margin %7.3f -> %7.3f   (%+.3f)"
          % (st.mean([r["margin"] for r in early]),
             st.mean([r["margin"] for r in late]),
             st.mean([r["margin"] for r in late]) - st.mean([r["margin"] for r in early])))

    print("\n" + "=" * 74)
    print("READING")
    print("=" * 74)
    frac_over = len(over) / len(ms)
    if frac_over < 0.10:
        print("* The gate is a RATCHET: only %.1f%% of cycles clear it." % (100 * frac_over))
        print("  `baseline_fit` is the fitness of the PROMOTED champion, so each")
        print("  promotion raises the bar to the thing just promoted. Improvement is")
        print("  therefore required against the best genome ever found, not against")
        print("  the built-in defaults - a strictly harder and self-tightening test.")
        print("* Consequence for Cranium: it is guarded by `beats`, so the ratchet")
        print("  also makes the cross-era organ unreachable. One mechanism, two")
        print("  symptoms.")
    else:
        print("* The gate is clearing regularly; investigate the failing gate instead.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
