#!/usr/bin/env python3
# coding: utf-8
"""evolution_backfill.py — answer "does EV-4 work?" NOW, from real history.

WHY THIS EXISTS
---------------
EV-4 (per-arm promotion streak) was installed and the honest confirmation
route was to wait ~12 live cycles for the arm histories to fill. That is two
hours of not knowing. The evidence does not need to be waited for: the
promotion gate is a pure function over STORED rows, and every stored row
carries the `champion_genes` it measured. The search space is recoverable from
the gene NAMES, so `schema_hash` can be derived retroactively for every row
written before EV-4 existed.

So instead of waiting for the loop to prove the fix, this tool:
  1. reads the real stored rows (not the log -- the log is a rendering),
  2. derives each row's schema_hash exactly as `schema_hash()` does,
  3. REPLAYS the real gate functions from `evolution_cycle.py` — the same
     `_compute_streak` and `_sprt_history` the cycle imports — over that
     backfilled history,
  4. reports what the gate would have decided, per arm.

Two things this is careful about:

  * It imports the real functions rather than reimplementing them. A replay
    that re-implements the gate proves nothing about the gate; the entire
    value here is that the same code decides.
  * It DERIVES the schema from champion_genes, and reports how many rows it
    could not derive. Rows without champion_genes are counted as UNKNOWN
    rather than silently assigned to an arm, because guessing an arm would
    manufacture exactly the comparability this tool exists to measure.

Usage:
    python tools/evolution_backfill.py
    python tools/evolution_backfill.py --json
Exit: 0 if at least one arm reaches a promotable streak, 1 if not, 2 NO_DATA
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict

AZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(AZ, "src"))

# The real gate. Imported, not reimplemented.
sys.path.insert(0, os.path.join(AZ, "scripts"))
import evolution_cycle as EC  # noqa: E402

BASE_GENES = {"learning_rate", "epsilon", "discount", "epsilon_decay"}


def load_rows(limit=2000):
    from CORE.local_memory import get_memory_store
    out = []
    for r in get_memory_store().query_memories(
            event_type='evolution_result', limit=limit) or []:
        d = r.get('data', r) if isinstance(r, dict) else r
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                continue
        if isinstance(d, dict):
            out.append(d)
    return out


def derive_schema(d):
    """Search-space identity of a row, from the genes it actually measured."""
    for key in ("champion_genes", "best_genes", "seed_genes"):
        g = d.get(key)
        if isinstance(g, dict) and g:
            extra = sorted(set(g) - BASE_GENES)
            return EC.schema_hash(g), ("+".join(extra) if extra else "(base4)")
    return None, None


def backfill(rows):
    tagged = derived = unknown = 0
    for d in rows:
        got = d.get("schema_hash")
        if got:
            tagged += 1
            if not d.get("schema_gene"):
                _, gene = derive_schema(d)
                d["schema_gene"] = gene
            continue
        sh, gene = derive_schema(d)
        if sh:
            d["schema_hash"] = sh          # EV-4 retrofit, for replay only
            d["schema_gene"] = gene
            derived += 1
        else:
            d["schema_hash"] = None
            d["schema_gene"] = "UNKNOWN"
            unknown += 1
    return {"already_tagged": tagged, "backfilled": derived, "unknown": unknown}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=2000)
    a = ap.parse_args()

    try:
        rows = load_rows(a.limit)
    except Exception as e:
        print(f"NO_DATA: cannot read stored rows -- {type(e).__name__}: {e}")
        return 2
    if not rows:
        print("NO_DATA: no evolution_result rows in the memory store")
        return 2

    counts = backfill(rows)
    # Newest-first, the order both gate functions consume.
    rows.sort(key=lambda d: -(d.get("ts") or d.get("timestamp") or 0))

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rows": len(rows),
        "backfill": counts,
        "arms": [],
        "best_streak_seen": 0,
        "any_promotable": False,
    }

    # The gate is keyed on THREE dimensions, not one: a comparable run must be
    # contiguous in (pool_id, baseline_hash, schema_hash). Grouping by arm
    # alone and passing a single pool_id is what made the first replay of this
    # tool report streak 1/3 for arms holding 26+ wins -- the break was the era
    # changing mid-arm, not the schema. That is the same lesson as EV-2 and
    # Cranium: a break in ANY pinned dimension truncates the run.
    groups = defaultdict(list)
    for d in rows:
        key = (d.get("pool_id"), d.get("baseline_hash"), d.get("schema_gene"))
        groups[key].append(d)

    report["era_groups"] = len(groups)
    for (pool_id, base_id, gene), grows in sorted(
            groups.items(), key=lambda kv: -len(kv[1])):
        arows = sorted(grows, key=lambda d: -(d.get("ts") or d.get("timestamp") or 0))
        sh = next((r.get("schema_hash") for r in arows if r.get("schema_hash")), None)
        win_rows = [{"data": r} for r in arows if r.get("beats_baseline")]
        streak = EC._compute_streak(win_rows, pool_id, base_id, True, schema_id=sh)
        margins = EC._sprt_history([{"data": r} for r in arows], pool_id, base_id,
                                   schema_id=sh, scan=400)
        arm = {
            "schema_gene": gene or "UNKNOWN",
            "pool_id": str(pool_id)[:12],
            "baseline_id": str(base_id)[:12],
            "cycles": len(arows),
            "beat_baseline": len(win_rows),
            "streak_replay": streak,
            "promote_wins_required": EC.PROMOTE_WINS,
            "streak_reaches_gate": streak >= EC.PROMOTE_WINS,
            "sprt_margins_replay": len(margins),
            "margins": [round(float(m), 4) for m in margins],
        }
        report["arms"].append(arm)
        report["best_streak_seen"] = max(report["best_streak_seen"], streak)
        if arm["streak_reaches_gate"]:
            report["any_promotable"] = True

    report["largest_group"] = max((len(v) for v in groups.values()), default=0)

    if a.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("=" * 72)
        print(f"EV-4 REPLAY over {report['rows']} stored rows  "
              f"[{time.strftime('%H:%M:%S')}]")
        print(f"  already schema-tagged: {counts['already_tagged']}   "
              f"backfilled: {counts['backfilled']}   "
              f"UNKNOWN (no champion_genes): {counts['unknown']}")
        print(f"  gate requires {EC.PROMOTE_WINS} wins; "
              f"SPRT accepts n in {EC.PROMOTE_MIN_WINS}..{EC.PROMOTE_MAX_WINS}")
        print()
        for arm in report["arms"]:
            flag = "PROMOTABLE" if arm["streak_reaches_gate"] else "not yet"
            print(f"  {arm['schema_gene']:<22} pool={arm['pool_id']:<13} base={arm['baseline_id']:<13} "
                  f"cyc={arm['cycles']:<4} wins={arm['beat_baseline']:<3} "
                  f"streak={arm['streak_replay']}/{arm['promote_wins_required']} "
                  f"sprt_n={arm['sprt_margins_replay']} -> {flag}")
            if arm["margins"]:
                print(f"       margins: {arm['margins']}")
        print()
        print(f"  BEST STREAK ANY ARM COULD HAVE REACHED: "
              f"{report['best_streak_seen']}/{EC.PROMOTE_WINS}")
        if report["any_promotable"]:
            print("  VERDICT: at least one arm HAS enough like-for-like evidence. "
                  "EV-4 unblocks the gate; the live cycle should promote once "
                  "it next produces 3 same-arm wins above the margin.")
        else:
            need = EC.PROMOTE_WINS - report["best_streak_seen"]
            print(f"  VERDICT: no arm yet reaches the gate. Best is "
                  f"{report['best_streak_seen']}, so {need} more same-arm win(s) "
                  f"are required. Note the SPRT may accept fewer than "
                  f"PROMOTE_WINS if its posterior is decisive -- check the "
                  f"margins above for whether the evidence is already "
                  f"one-sided.")
    return 0 if report["any_promotable"] else 1


if __name__ == "__main__":
    sys.exit(main())


