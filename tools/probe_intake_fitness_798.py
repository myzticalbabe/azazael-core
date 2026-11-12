#!/usr/bin/env python3
"""probe_intake_fitness_798.py — READ-ONLY probe for Engram #798 item 1.

Question: does scoring genomes on the INTAKE record pool discriminate
differently than scoring on the DataPipeline pool? If yes, wiring the
intake pool into the QD/GA loop can move selection (wire justified). If
no, wiring changes nothing and QD-flatness lies elsewhere (redirect).

Method (one variable): the production fitness path
(`evolution_cycle.fitness_genes` -> `_eval_salt` -> `train_from_memory`
with `persist=False`) is called on the SAME genomes and the SAME frozen
alert pool; only `info_pool` swaps between the DataPipeline extraction
(identical call to the freeze path) and `build_training_records()`
output. Seeded RNG makes each arm deterministic, so any delta above
float dust is signal, not noise.

READS ONLY: store reads + compute. No snapshot writes, no store writes
(`persist=False` throughout), no commits. Gaps are reported, never
silently dropped.
"""

from __future__ import annotations

import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(os.path.abspath(__file__).replace("tools", "scripts"))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "src"), _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evolution_cycle as evc  # noqa: E402 (import-safe: no GA run on import)
from CORE.training_intake import build_training_records  # noqa: E402
from CORE.WORMS.expansion.learning import DataPipelineWorm  # noqa: E402

#: Float dust threshold: arms are deterministic, so anything above this
#: is a real pool difference, not RNG noise.
EPS = 1e-6


def mutate(genes: dict, key: str, factor: float) -> dict:
    """Single-gene multiplicative mutant (seeded caller picks key/factor)."""
    out = dict(genes)
    out[key] = float(out[key]) * factor
    return out


def main() -> int:
    print("== pools ==")
    current = DataPipelineWorm().extract_from_memory(
        limit=evc.N_EPISODES * 10,
        exclude_sources=evc.EXCLUDED_TRAINING_SOURCES)
    print("  current info pool : %d rows (DataPipelineWorm, freeze-path call)"
          % len(current or []))
    built = build_training_records(limit=500)
    intake = built.get("records") or []
    print("  intake info pool  : %d rows (build_training_records, ok_rate=%s)"
          % (len(intake), built.get("ok_rate")))
    # One frozen alert pool for BOTH arms (fixed seed, not int(now)):
    # live extraction drifts ~3pts per cycle, which would contaminate
    # the comparison. The alert arm is identical in both arms by construction.
    alert = evc.prioritize_pool(evc._extract_alert_pool(), seed=798)
    print("  shared alert pool : %d rows (frozen, seed=798)" % len(alert or []))
    if not current or not intake or not alert:
        print("  DEGENERATE: current=%d intake=%d alert=%d -- probe refuses "
              "to compare against an empty arm"
              % (len(current or []), len(intake), len(alert or [])))
        return 3

    champ = evc.last_best_genes()
    if not champ:
        print("  NO CHAMPION: no evolution_result row in store -- nothing to score")
        return 3
    rng = random.Random(798)
    num_keys = sorted(k for k, v in champ.items()
                      if isinstance(v, (int, float)) and k != "epsilon_decay")
    genomes = [("champion", dict(champ))]
    for i in range(2):
        key = num_keys[rng.randrange(len(num_keys))]
        factor = 1.15 if i == 0 else 0.85
        genomes.append(("mut-%s-x%s" % (key, factor), mutate(champ, key, factor)))
    print("  genomes: %s" % [n for n, _ in genomes])

    print("\n== arm A: current pool vs arm B: intake pool (same genomes, same alert) ==")
    rows = []
    for name, genes in genomes:
        fa = evc.fitness_genes(genes, info_pool=current, alert_pool=alert)
        fb = evc.fitness_genes(genes, info_pool=intake, alert_pool=alert)
        rows.append({"genome": name, "current": fa, "intake": fb,
                     "delta": fb - fa})
        print("  %-18s current=%10.4f intake=%10.4f delta=%+10.4f"
              % (name, fa, fb, fb - fa))

    rank_a = sorted(rows, key=lambda r: -r["current"])
    rank_b = sorted(rows, key=lambda r: -r["intake"])
    order_a = [r["genome"] for r in rank_a]
    order_b = [r["genome"] for r in rank_b]
    max_abs = max(abs(r["delta"]) for r in rows)
    print("\n  rank on current: %s" % order_a)
    print("  rank on intake : %s" % order_b)
    print("  max |delta|    : %.4f" % max_abs)

    if order_a != order_b or max_abs > EPS:
        verdict = ("INTAKE-DISCRIMINATES: the intake pool re-ranks or "
                   "re-margins genomes vs the current pool -- wiring it "
                   "into the loop can move selection")
    else:
        verdict = ("INTAKE-FLAT: identical rankings and margins -- wiring "
                   "the pool changes nothing; QD-flatness lies elsewhere "
                   "(archive diversity / behavior dims per DIVERGE-2)")
    print("\n== verdict ==\n  %s" % verdict)
    print(json.dumps({"probe": "intake-fitness-798", "rank_current": order_a,
                      "rank_intake": order_b, "max_abs_delta": round(max_abs, 6),
                      "verdict": verdict.split(":")[0]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
