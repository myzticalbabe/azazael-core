#!/usr/bin/env python3
# coding: utf-8
"""diagnose_fitness_gradient.py — is the GA's fitness a valid selection signal?

Observed 2026-09-18: the production champion in `state/evolved_rl_params.json`
sits exactly on the corners of the widened search space —

    learning_rate = 0.9   (CEILING of [0.01, 0.9])
    epsilon       = 0.5   (CEILING of [0.0, 0.5])
    discount      = 0.5   (FLOOR   of [0.5, 0.999])

A GA that walks to the wall is either (a) following a fitness that is monotone
in those genes, or (b) chasing noise. Both are fatal to adaptation and they are
distinguishable, which is the entire job of this script.

METHOD — the instrument is the GA's OWN fitness.
`fitness_genes` is what selection actually optimises (min over 3 exploration
salts of the info+alert reward). Re-implementing a proxy here would measure a
different landscape, so every number below comes from calling `fitness_genes`
itself, with the pools frozen once so extraction cost and drift cannot leak in.

Two earlier versions of this script were wrong, and the fixes are load-bearing:
  * it called `train_from_memory` WITHOUT `rng=`, so runs were unseeded and the
    curve was mostly sampling noise. Calls now go through `fitness_genes`,
    which seeds per (genome, salt) via `genome_seed`.
  * it reported single numbers with no noise floor, so a 0.05 difference looked
    like a finding. Every point now reports the spread across the 3 salts —
    a difference smaller than that spread is not a signal.

  B1  non-learned baselines. Per-step reward of every constant action and of
      the per-state oracle, over the exact index sequence the trainer walks.
      If the learned policy scores below a hardcoded constant action, the score
      is not measuring skill.

  B2  one-gene sweeps on `fitness_genes`, each with its noise floor.

  B3  head-to-head, champion vs built-in defaults.

Usage:
    python scripts/diagnose_fitness_gradient.py
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Imported from the runner itself so this instrument cannot drift from the GA.
import evolution_cycle as ECO  # noqa: E402
from CORE.WORMS.expansion.learning_rl import RLTrainLoop, _compute_reward  # noqa: E402
from CORE.WORMS.expansion.rl_gene_bounds import RL_GENE_BOUNDS  # noqa: E402

DEFAULTS = {"learning_rate": 0.29, "epsilon": 0.11,
            "discount": 0.91, "epsilon_decay": 0.0}
CHAMPION = {"learning_rate": 0.9, "epsilon": 0.5,
            "discount": 0.5, "epsilon_decay": 0.09192479896276401}


def frozen_pools(limit=500):
    from CORE.WORMS.expansion.learning import DataPipelineWorm
    exc = getattr(ECO, "EXCLUDED_TRAINING_SOURCES", [])
    pipe = DataPipelineWorm()
    info = pipe.extract_from_memory(limit=limit, exclude_sources=exc)
    alert = pipe.extract_from_memory(event_type="db_health_alert", limit=limit,
                                     exclude_sources=exc)
    return info, alert


def per_salt(genes, info, alert):
    """(total, info, alert) for each of the 3 salts — the GA's own samples."""
    out = []
    for salt in ECO._SALTS:
        rng_seed = ECO.genome_seed(genes, salt)
        import random as _r
        out.append(ECO._eval_salt(genes, info, alert, rng=_r.Random(rng_seed)))
    return out


def fitness_stats(genes, info, alert):
    """GA fitness (min over salts) + the spread that bounds its resolution."""
    parts = per_salt(genes, info, alert)
    totals = sorted(p[0] for p in parts)
    return {
        "fitness": round(totals[0], 4),          # min-of-salts == what selection sees
        "mean": round(sum(totals) / len(totals), 4),
        "spread": round(totals[-1] - totals[0], 4),
        "info": round(min(p[1] for p in parts), 4),
        "alert": round(min(p[2] for p in parts), 4),
    }


def b1_baselines(info, alert, episodes, max_steps):
    """B1: what do NON-learned policies score on the trainer's own sequence?"""
    print("=" * 74)
    print("B1  non-learned baselines — per-step reward, no learning")
    print("=" * 74)

    names = {0: "ignore", 1: "log", 2: "alert", 3: "remediate"}

    def seq(pool):
        n = len(pool)
        return [pool[(e + s) % n] for e in range(episodes) for s in range(max_steps)]

    results = {}
    for label, pool in (("info", info), ("alert", alert)):
        if not pool:
            continue
        s = seq(pool)
        const = {a: round(sum(_compute_reward(r, a) for r in s) / len(s), 4)
                 for a in (0, 1, 2, 3)}
        # Per-state oracle: best action for each (source:severity) state.
        by_state = {}
        for r in s:
            key = f"{r.get('source', 'unknown')}:{r.get('severity', 'info')}"
            by_state.setdefault(key, []).append(r)
        oracle_total = 0.0
        for key, rows in by_state.items():
            best = max((sum(_compute_reward(r, a) for r in rows) / len(rows))
                       for a in (0, 1, 2, 3))
            oracle_total += best * len(rows)
        oracle = round(oracle_total / len(s), 4)
        results[label] = {"const": const, "oracle": oracle, "n_states": len(by_state)}
        print(f"\n  pool: {label}  ({len(s)} steps, {len(by_state)} states)")
        for a in (0, 1, 2, 3):
            mark = "  <-- best constant" if const[a] == max(const.values()) else ""
            print(f"    constant {names[a]:<10} {const[a]:>9}{mark}")
        print(f"    per-state ORACLE   {oracle:>9}   "
              f"(upper bound: knows the best action per state)")
    return results


def b2_sweep(info, alert, episodes, steps):
    """B2: does each gene have an interior optimum, above the noise floor?"""
    print("\n" + "=" * 74)
    print("B2  one-gene sweeps on the GA's own fitness (min over 3 salts)")
    print("=" * 74)

    grids = {
        "learning_rate": [0.01, 0.1, 0.29, 0.5, 0.7, 0.9],
        "epsilon":       [0.0, 0.05, 0.11, 0.2, 0.35, 0.5],
        "discount":      [0.5, 0.6, 0.75, 0.91, 0.99, 0.999],
        "epsilon_decay": [0.0, 0.05, 0.1, 0.2, 0.3],
    }

    findings = {}
    for gene, values in grids.items():
        print(f"\n  {gene}   (others pinned at built-in defaults)   "
              f"box=[{RL_GENE_BOUNDS[gene][0]}, {RL_GENE_BOUNDS[gene][1]}]")
        print(f"    {'value':>8} {'fitness':>9} {'mean':>9} {'noise':>8}   curve")
        pts = []
        for v in values:
            g = dict(DEFAULTS)
            g[gene] = v
            st = fitness_stats(g, info, alert)
            pts.append((v, st))
            bar = "#" * max(0, int(st["fitness"] / 2))
            print(f"    {v:>8} {st['fitness']:>9} {st['mean']:>9} "
                  f"{st['spread']:>8}   {bar}")
        best_v, best_st = max(pts, key=lambda kv: kv[1]["fitness"])
        noise = max(st["spread"] for _, st in pts)
        interior = best_v not in (values[0], values[-1])
        # Is the winner actually above the field, or inside the noise?
        margin = round(best_st["fitness"] - sorted(
            st["fitness"] for _, st in pts)[-2], 4)
        findings[gene] = {"best_v": best_v, "best": best_st["fitness"],
                          "noise": noise, "interior": interior,
                          "margin": margin, "resolved": margin > noise}
        verdict = ("interior optimum" if interior else "MONOTONE — runs to a wall")
        print(f"    -> best={best_v} fitness={best_st['fitness']} "
              f"margin_over_2nd={margin} noise_floor={noise}")
        print(f"       {verdict}; "
              f"{'above noise' if margin > noise else 'WITHIN NOISE (not a real signal)'}")
    return findings


def b3_head_to_head(info, alert):
    print("\n" + "=" * 74)
    print("B3  head-to-head — champion vs built-in defaults")
    print("=" * 74)
    rows = {}
    for label, genes in (("defaults", DEFAULTS), ("champion", CHAMPION)):
        rows[label] = fitness_stats(genes, info, alert)
    print(f"  {'genome':<10} {'fitness':>9} {'mean':>9} {'noise':>8} "
          f"{'info':>9} {'alert':>9}")
    for label in ("defaults", "champion"):
        r = rows[label]
        print(f"  {label:<10} {r['fitness']:>9} {r['mean']:>9} {r['spread']:>8} "
              f"{r['info']:>9} {r['alert']:>9}")
    d = round(rows["champion"]["fitness"] - rows["defaults"]["fitness"], 4)
    noise = max(rows["defaults"]["spread"], rows["champion"]["spread"])
    print()
    if d > noise:
        print(f"  champion beats defaults by {d} (noise floor {noise}) — real.")
    elif d > 0:
        print(f"  champion nominally ahead by {d}, but the noise floor is "
              f"{noise}: NOT a resolved difference.")
    else:
        print(f"  champion is BEHIND defaults by {abs(d)} (noise floor {noise}).")
        print("  Promotion gates certified a genome that loses to the hardcoded")
        print("  fallback.")
    return d, noise


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    episodes = int(getattr(ECO, "N_EPISODES", 80))
    max_steps = 50
    print("FITNESS GRADIENT DIAGNOSTIC  (instrument = evolution_cycle.fitness_genes)")
    print(f"episodes={episodes} max_steps={max_steps} salts={ECO._SALTS}")

    info, alert = frozen_pools(args.limit)
    print(f"frozen pools: info={len(info)} alert={len(alert)}")
    if not info and not alert:
        print("no eval pool — nothing to diagnose")
        return

    base = b1_baselines(info, alert, episodes, max_steps)
    findings = b2_sweep(info, alert, episodes, max_steps)
    b3_head_to_head(info, alert)

    # B4: the decisive comparison — learned policy vs non-learned baselines.
    print("\n" + "=" * 74)
    print("B4  does the learned policy beat a constant action?")
    print("=" * 74)
    print("  Like-for-like: the RL figure is the MEAN over the same 3 salts the")
    print("  GA samples; the baselines are exact means over the same index")
    print("  sequence. Comparing a min-of-salts RL figure against a mean")
    print("  baseline would be biased against the policy, so it is not done here.")
    for label, pool, et in (("info", info, None), ("alert", alert, "db_health_alert")):
        if not pool or label not in base:
            continue
        per_step = []
        for salt in ECO._SALTS:
            import random as _r
            loop = RLTrainLoop()
            kw = dict(n_episodes=episodes, max_steps=max_steps,
                      learning_rate=CHAMPION["learning_rate"],
                      epsilon=CHAMPION["epsilon"],
                      discount_factor=CHAMPION["discount"],
                      epsilon_decay=CHAMPION["epsilon_decay"],
                      records=pool, persist=False,
                      rng=_r.Random(ECO.genome_seed(CHAMPION, salt)))
            if et:
                kw["event_type"] = et
            per_step.append(loop.train_from_memory(**kw)["avg_reward"] / max_steps)
        learned = round(sum(per_step) / len(per_step), 4)
        best_const = max(base[label]["const"].values())
        oracle = base[label]["oracle"]
        mean_const = round(sum(base[label]["const"].values()) / 4, 4)
        print(f"\n  pool {label}:  ({len(per_step)} salts: "
              f"{', '.join(f'{v:.3f}' for v in per_step)})")
        print(f"    learned (champion)          {learned:>8} per step")
        print(f"    best constant action        {best_const:>8} per step")
        print(f"    mean of the 4 constants     {mean_const:>8} per step")
        print(f"    per-state oracle (ceiling)  {oracle:>8} per step")
        print(f"    gap to the per-state oracle: "
              f"{round(oracle - learned, 4)} per step")
        if learned < best_const:
            print(f"    *** the learned policy is WORSE than a hardcoded constant "
                  f"({round(best_const - learned, 4)} per step)")
            print("    the Q-update is not producing a better-than-trivial policy")
        else:
            print(f"    learned beats the best constant by "
                  f"{round(learned - best_const, 4)}")

    print("\n" + "=" * 74)
    print("SUMMARY — gene-by-gene verdict")
    print("=" * 74)
    print(f"  {'gene':<16} {'best':>8} {'margin':>9} {'noise':>8}  verdict")
    for gene, f in findings.items():
        v = ("interior optimum" if f["interior"]
             else f"MONOTONE -> wall ({f['best_v']})")
        res = "" if f["resolved"] else "  [within noise]"
        print(f"  {gene:<16} {f['best_v']:>8} {f['margin']:>9} "
              f"{f['noise']:>8}  {v}{res}")
    print("\n  A MONOTONE gene cannot be tuned, only driven to whichever wall is")
    print("  higher. A gene whose optimum sits inside the noise floor gives")
    print("  selection nothing to follow — those two failure modes together")
    print("  explain a champion pinned to the corners.")


if __name__ == "__main__":
    main()
