#!/usr/bin/env python3
# coding: utf-8
"""Measured prune of the tensor genome (ROADMAP 1.4).

WHY A SEPARATE, COPY-ONLY SCRIPT
--------------------------------
`TensorGenome.prune()` is destructive and the live search still uses the genes it
would remove, so the roadmap requires the prune be MEASURED before it is approved:
run the real `prune(keep=TENSOR_KEEP)` on a COPY, report what it would drop and
what the compiled model would cost, and separately measure the cost of dropping
`epsilon` — the coordinate the genome already flags at rho ~= -0.03.

The live artifact is never written. The script records the genome file's size and
mtime before and after and asserts they are unchanged, so "genome intact until
approved" is checked rather than promised.

"RE-QUERY USEFULNESS AFTER DROP"
-------------------------------
A gene is only safe to drop if its weak association is not an artifact of the
window that produced it. So every candidate's usefulness is recomputed on the
later slice of history as well as the full set — an out-of-sample check the
single-window number cannot make. A gene whose association is weak overall but
strong in the later era is reported as NOT safe to drop.

ARTIFACT
--------
  state/tensor_genome.prune_report.json

Usage:
    python scripts/prune_tensor_genome.py
    python scripts/prune_tensor_genome.py --rows 1200
    python scripts/prune_tensor_genome.py --floor 0.20
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, os.path.join(ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROMOTE_KEYS = ("learning_rate", "epsilon", "discount", "epsilon_decay")
# Mirrors `TENSOR_KEEP` in scripts/evolution_cycle.py: the four genes a
# downstream consumer reads BY NAME, which may not be pruned however they score.
TENSOR_KEEP = tuple(PROMOTE_KEYS)
GENOME_JSON = os.path.join(ROOT, "state", "tensor_genome.json")
OUT = os.path.join(ROOT, "state", "tensor_genome.prune_report.json")
USEFULNESS_FLOOR = 0.15


def log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg), flush=True)


def _file_stamp(path):
    try:
        st = os.stat(path)
        return {"size": int(st.st_size), "mtime": round(float(st.st_mtime), 3)}
    except Exception:
        return None  # scanner:ignore -- probe semantics: an unstat-able path counts as absent; None is the designed missing-stamp marker


def _candidate_genes(genome):
    """Below-floor genes plus `epsilon`, the roadmap's named what-if."""
    names = set(genome.prune_floor_names())
    names.add("epsilon")
    return sorted(n for n in names if n in genome.genes)


def _history(limit):
    try:
        from CORE.local_memory import get_memory_store
        rows = get_memory_store().query_memories(
            event_type="evolution_result", limit=int(limit)) or []
    except Exception as exc:
        log("history read failed: %s" % str(exc)[:140])
        return []
    # Oldest first, so "early"/"late" mean what they say regardless of the
    # store's own ordering.
    return list(reversed(list(rows)))


def _corr(rows, gene):
    from CORE.WORMS.expansion.tensorgene import correlate
    r = correlate(rows, gene) or {}
    return {"usefulness": r.get("usefulness"),
            "samples": r.get("samples"),
            "direction": r.get("direction"),
            "identifiable": r.get("identifiable")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=800)
    ap.add_argument("--floor", type=float, default=USEFULNESS_FLOOR)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    from CORE.WORMS.expansion.tensorgene import TensorGenome

    try:
        genome = TensorGenome.load_json(GENOME_JSON)
    except Exception as exc:
        log("FAILED to load %s: %s" % (GENOME_JSON, str(exc)[:140]))
        return 2

    stamp_before = _file_stamp(GENOME_JSON)
    before = {"genes": list(genome.order()),
              "width": genome.width,
              "summary": genome.summary()}

    # --- what the REAL prune would do, on a copy ---------------------------
    copy_kept = TensorGenome.load_json(GENOME_JSON)
    dropped_kept = copy_kept.prune(floor=float(args.floor), keep=TENSOR_KEEP)

    # --- the roadmap's named what-if: drop epsilon too ---------------------
    keep_no_eps = [k for k in TENSOR_KEEP if k != "epsilon"]
    copy_eps = TensorGenome.load_json(GENOME_JSON)
    dropped_eps = copy_eps.prune(floor=float(args.floor), keep=keep_no_eps)

    # --- out-of-sample re-query -------------------------------------------
    rows = _history(args.rows)
    cut = int(len(rows) * 0.7)
    early, late = rows[:cut], rows[cut:]
    candidates = _candidate_genes(genome)
    oos = {}
    for g in candidates:
        oos[g] = {"full": _corr(rows, g),
                  "early": _corr(early, g),
                  "late": _corr(late, g)}

    verdict = []
    for g in candidates:
        late_u = (oos[g]["late"] or {}).get("usefulness")
        weak_late = isinstance(late_u, (int, float)) and abs(float(late_u)) < float(args.floor)
        verdict.append({
            "gene": g,
            "weak_full": bool(genome.genes[g].usefulness < float(args.floor)),
            "weak_late": bool(weak_late),
            # SAFE only when the weak association holds out of sample. A gene
            # that is weak overall but strong in the later era stays.
            "safe_to_drop": bool(weak_late),
            "reason": ("weak association holds in the later era"
                       if weak_late else
                       "association is NOT weak out-of-sample - keep"),
        })

    cleanup = {
        "prune_kept_dropped": dropped_kept,
        "prune_kept_width_before": before["width"],
        "prune_kept_width_after": copy_kept.width,
        "prune_epsilon_dropped": dropped_eps,
        "prune_epsilon_width_before": before["width"],
        "prune_epsilon_width_after": copy_eps.width,
        "epsilon_measured_cost": {
            "usefulness_full": (oos.get("epsilon", {}).get("full") or {}).get("usefulness"),
            "usefulness_late": (oos.get("epsilon", {}).get("late") or {}).get("usefulness"),
            "width_saved": before["width"] - copy_eps.width,
            "model_params_saved": max(0, before["width"] - copy_eps.width),
        },
    }

    stamp_after = _file_stamp(GENOME_JSON)
    report = {
        "kind": "tensor_genome_prune_report",
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "floor": float(args.floor),
        "keep": list(TENSOR_KEEP),
        "before": before,
        "cleanup": cleanup,
        "out_of_sample": oos,
        "verdict": verdict,
        "rows_used": len(rows),
        "approved": False,          # a prune is never applied by this script
        "genome_untouched": bool(stamp_before == stamp_after),
        "genome_stamp_before": stamp_before,
        "genome_stamp_after": stamp_after,
    }
    try:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
    except Exception as exc:
        log("FAILED to write %s: %s" % (args.out, str(exc)[:140]))
        return 2

    log("prune(keep=TENSOR_KEEP) would drop: %s" % (dropped_kept or "nothing"))
    log("prune(no-epsilon-keep) would drop:     %s" % (dropped_eps or "nothing"))
    for v in verdict:
        log("  %-16s safe_to_drop=%-5s (%s)" % (v["gene"], v["safe_to_drop"], v["reason"]))
    log("genome untouched: %s -> %s" % (report["genome_untouched"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
