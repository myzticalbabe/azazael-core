#!/usr/bin/env python3
# coding: utf-8
"""audit_loop_budgets.py — are the loop's budgets large enough to be self-improving?

The architecture claims to be self-recursively improving: evolution measures a
candidate against production and promotes it when the evidence is good. That
claim requires MEASUREMENT RESOLUTION finer than the EFFECT it wants to detect.

The gate declares its effect size:

    PROMOTE_MARGIN = 0.75    (SPRT_DELTA1, same units as fitness)

Earlier today `diagnose_fitness_gradient.py` measured the noise floor of that
same fitness — the spread across the 3 exploration salts of ONE genome — at
1.3 to 11.0. If noise > effect, no amount of gating can separate a better genome
from a luckier one, and the search becomes a random walk that only ever walks
to the walls of the box. That is the corner-pinned champion.

This script decides between three explanations with measurement:

  C1  budget ladder.   Noise floor vs n_episodes. If the floor is set by the
                       budget it must fall as the budget grows (1/sqrt(n) for
                       an independent-sample mean). If it plateaus, the noise
                       is structural and no budget fixes it.

  C2  required budget. The n needed to resolve PROMOTE_MARGIN, extrapolated
                       from the measured slope, and what that costs in time.

  C3  paired estimator. The gates compare candidates against production. If
                       both are scored on the SAME salt seeds, the shared
                       exploration noise cancels in the DIFFERENCE. The current
                       fitness takes a MIN over salts, which destroys that
                       pairing and ADDS the variances instead. This test
                       measures paired per-salt deltas to see whether the
                       existing compute already carries a resolvable signal.

Usage:
    python scripts/audit_loop_budgets.py
    python scripts/audit_loop_budgets.py --ladder 40,80,160,320,640
"""

import argparse
import os
import random
import statistics
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evolution_cycle as ECO  # noqa: E402
from CORE.WORMS.expansion.learning_rl import RLTrainLoop  # noqa: E402
from CORE.WORMS.expansion.scheduling import EVAL_EPISODES, ScheduleConfig  # noqa: E402

DEFAULTS = {"learning_rate": 0.29, "epsilon": 0.11,
            "discount": 0.91, "epsilon_decay": 0.0}
CHAMPION = {"learning_rate": 0.9, "epsilon": 0.5,
            "discount": 0.5, "epsilon_decay": 0.09192479896276401}
MAX_STEPS = 50


def run_one(genes, pool, event_type, n_episodes, salt):
    """One salted eval of one pool. Returns avg_reward."""
    loop = RLTrainLoop()
    kw = dict(n_episodes=n_episodes, max_steps=MAX_STEPS,
              learning_rate=genes["learning_rate"], epsilon=genes["epsilon"],
              discount_factor=genes["discount"],
              epsilon_decay=genes["epsilon_decay"],
              records=pool, persist=False,
              rng=random.Random(ECO.genome_seed(genes, salt)))
    if event_type:
        kw["event_type"] = event_type
    return float(loop.train_from_memory(**kw).get("avg_reward", 0.0))


def per_salt_totals(genes, info, alert, n_episodes):
    """Total fitness per salt, in salt order — pairing-preserving."""
    out = []
    for salt in ECO._SALTS:
        i = run_one(genes, info, None, n_episodes, salt)
        a = run_one(genes, alert, "db_health_alert", n_episodes, salt)
        out.append(i + a)
    return out


def inventory():
    """C1 part 0: the declared budgets, read from the code, not from prose."""
    print("=" * 78)
    print("BUDGET INVENTORY — values read from the running modules")
    print("=" * 78)
    cfg = ScheduleConfig()
    rows = [
        ("EVAL_EPISODES (eval budget / skill)", EVAL_EPISODES),
        ("_SALTS (exploration resamples)", len(ECO._SALTS)),
        ("max_steps (Q-updates per episode)", MAX_STEPS),
        ("=> Q-updates per eval (episodes*steps)", EVAL_EPISODES * MAX_STEPS),
        ("ScheduleConfig.data_limit (prod records)", cfg.data_limit),
        ("ScheduleConfig.n_episodes (prod)", cfg.n_episodes),
        ("PROMOTE_MARGIN (effect the gate wants)", ECO.PROMOTE_MARGIN),
        ("PROMOTE_MIN_WINS (evidence floor)", ECO.PROMOTE_MIN_WINS),
        ("PROMOTE_MAX_WINS (evidence cap)", ECO.PROMOTE_MAX_WINS),
        ("SPRT_DELTA1 (= PROMOTE_MARGIN)", ECO.SPRT_DELTA1),
    ]
    for label, val in rows:
        print(f"  {label:<42} {val}")
    print()
    print(f"  Resolution required to act: better than +/- "
          f"{ECO.PROMOTE_MARGIN}")
    return ECO.PROMOTE_MARGIN


def c1_ladder(info, alert, ladder, margin):
    """C1+C2: how does the noise floor respond to budget?"""
    print("\n" + "=" * 78)
    print("C1  BUDGET LADDER — noise floor (spread across salts, one genome)")
    print("=" * 78)
    print(f"  {'episodes':>9} {'Q-updates':>10} {'mean':>9} {'noise':>8} "
          f"{'sec/eval':>9}  {'noise/effect':>13}")

    curve = []
    for n in ladder:
        t0 = time.perf_counter()
        totals = per_salt_totals(DEFAULTS, info, alert, n)
        dt = time.perf_counter() - t0
        noise = max(totals) - min(totals)
        mean = statistics.fmean(totals)
        ratio = noise / margin if margin else float("inf")
        curve.append({"n": n, "noise": noise, "mean": mean, "sec": dt})
        print(f"  {n:>9} {n * MAX_STEPS:>10} {mean:>9.3f} {noise:>8.3f} "
              f"{dt:>9.2f}  {ratio:>12.1f}x")

    if len(curve) >= 2:
        n1, n2 = curve[0], curve[-1]
        if n1["noise"] > 0 and n2["noise"] > 0:
            # noise ~ n**-p  ->  p = -log(noise2/noise1)/log(n2/n1)
            import math
            p = -math.log(n2["noise"] / n1["noise"]) / math.log(n2["n"] / n1["n"])
            print(f"\n  observed noise decay exponent p = {p:.3f}")
            print("  (p == 0.5 means the budget bounds it, as for an independent")
            print("   sample mean. p ~ 0 means a plateau: more episodes will not help.)")
            need = None
            if p > 0.05:
                need = n1["n"] * (n1["noise"] / margin) ** (1.0 / p)
                print(f"\n  episodes required for noise < effect ({margin}): "
                      f"~{need:,.0f}")
                per_eval = n1["sec"] / n1["n"]
                est = need * per_eval
                print(f"  at {per_eval * 1000:.1f} ms per episode that is "
                      f"~{est:.0f}s per single eval")
                print(f"  the gate needs {ECO.PROMOTE_MIN_WINS}-"
                      f"{ECO.PROMOTE_MAX_WINS} wins, x2 genomes per comparison,")
                print(f"  => ~{est * ECO.PROMOTE_MIN_WINS * 2:.0f}s "
                      f"({est * ECO.PROMOTE_MIN_WINS * 2 / 3600:.1f}h) minimum "
                      f"for ONE promotion decision")
            else:
                print("\n  noise does NOT decay with budget -> structural, not")
                print("  budgetary. Raising EVAL_EPISODES cannot fix it.")
    return curve


def c3_paired(info, alert, n_episodes):
    """C3: does pairing candidate vs production on the same salts recover signal?"""
    print("\n" + "=" * 78)
    print("C3  PAIRED ESTIMATOR — same salt seeds for both genomes")
    print("=" * 78)
    cand = per_salt_totals(CHAMPION, info, alert, n_episodes)
    base = per_salt_totals(DEFAULTS, info, alert, n_episodes)

    print(f"  salts: {ECO._SALTS}")
    print(f"  {'salt':>5} {'production':>12} {'candidate':>11} {'delta':>9}")
    deltas = []
    for salt, c, b in zip(ECO._SALTS, cand, base):
        deltas.append(c - b)
        print(f"  {salt:>5} {b:>12.3f} {c:>11.3f} {c - b:>+9.3f}")

    unpaired_spread = max(cand + base) - min(cand + base)
    paired = statistics.fmean(deltas)
    paired_spread = max(deltas) - min(deltas)
    print()
    print(f"  unpaired spread across all 6 samples : {unpaired_spread:.3f}")
    print(f"  paired mean delta (candidate-prod)   : {paired:+.3f}")
    print(f"  paired spread of the delta           : {paired_spread:.3f}")

    print(f"\n  MIN-of-salts (what the GA uses today):")
    print(f"    production = {min(base):.3f}   candidate = {min(cand):.3f}   "
          f"margin = {min(cand) - min(base):+.3f}")
    print(f"  the min destroys the pairing: it compares two INDEPENDENT")
    print(f"  minima, so the noise ADDS instead of cancelling.")

    if paired_spread < unpaired_spread:
        gain = unpaired_spread / paired_spread if paired_spread > 0 else float("inf")
        print(f"\n  pairing reduces the spread {gain:.2f}x for the SAME compute.")
        if abs(paired) > paired_spread:
            print(f"  and the paired delta ({paired:+.3f}) EXCEEDS its own spread "
                  f"({paired_spread:.3f})")
            print("  => the signal is already in the data; the estimator, not the")
            print("     budget, is what discards it.")
        else:
            print(f"  but the paired delta is still inside its spread: the effect")
            print(f"  itself is smaller than the residual noise.")
    else:
        print("\n  pairing does not reduce the spread here.")
    return paired, paired_spread, unpaired_spread


def c4_resolution(info, alert, ladder, margin):
    """C4: at each budget, is the champion-vs-defaults call actually decidable?

    Only the PAIRED column is a noise estimate. `max(cand+base)-min(cand+base)`
    is the spread of six samples that straddle twoDIFFERENT genomes, so it
    contains the treatment effect (+1.7) as well as the sampling noise, and it
    grows with the effect rather than falling with the budget. It is printed
    only to show how badly an unpaired statistic misleads; do not read it as a
    noise floor. An earlier revision of this table did read it that way.
    """
    print("\n" + "=" * 78)
    print("C4  DECIDABILITY — is a real call resolvable at each budget?")
    print("=" * 78)
    print(f"  effect the gate must resolve: {margin}")
    print(f"  {'episodes':>9} {'paired noise':>13} {'paired delta':>13} "
          f"{'decidable?':>11}")
    for n in ladder:
        cand = per_salt_totals(CHAMPION, info, alert, n)
        base = per_salt_totals(DEFAULTS, info, alert, n)
        d = [c - b for c, b in zip(cand, base)]
        paired = max(d) - min(d)
        delta = statistics.fmean(d)
        ok = "yes" if paired < margin else "no"
        print(f"  {n:>9} {paired:>13.3f} {delta:>+13.3f} {ok:>11}")
    print("\n  Paired noise is the only honest resolution figure here. A gate")
    print("  cannot act on an effect smaller than it, at any budget.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ladder", default="40,80,160,320",
                    help="comma-separated episode budgets")
    args = ap.parse_args()
    ladder = [int(x) for x in args.ladder.split(",")]

    print("LOOP BUDGET AUDIT")
    print(f"ladder: {ladder}   salts: {ECO._SALTS}")

    from CORE.WORMS.expansion.learning import DataPipelineWorm
    pipe = DataPipelineWorm()
    exc = getattr(ECO, "EXCLUDED_TRAINING_SOURCES", [])
    info = pipe.extract_from_memory(limit=500, exclude_sources=exc)
    alert = pipe.extract_from_memory(event_type="db_health_alert", limit=500,
                                     exclude_sources=exc)
    print(f"frozen pools: info={len(info)} alert={len(alert)}")

    margin = inventory()
    curve = c1_ladder(info, alert, ladder, margin)
    if curve:
        c3_paired(info, alert, ladder[-1])
        c4_resolution(info, alert, ladder, margin)


if __name__ == "__main__":
    main()
