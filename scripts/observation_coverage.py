"""Observation coverage — how much of the fleet the learner can actually see.

WHY THIS EXISTS (2026-09-21)
----------------------------
Rung L1 of the evolution ladder scores a wiring genome against observed per-organ
outcomes. Its resolution is therefore capped by observation, not by search power:

    eligible organs : 211
    organs with any outcome : ~15-25
    L1 search resolution    : ~7%

At 7%, ~93% of mutations are provably INERT - the genome can be changed and the
objective cannot tell, because the organ touched has never reported anything. So
"widen observation" is the binding constraint on the whole rung, and this tool is
how that constraint is measured instead of asserted.

It answers four questions a widening effort needs:

1. **Which producers feed the tap?** Rows carry the emitting `source`, so a silent
   execution path is visible as a producer with zero rows rather than as an
   absence nobody can locate. Four producers existed; two were silent, and that
   was only discoverable after they were unified onto one emitter.
2. **Which natures are dark?** Coverage grouped by nature turns "7% resolution"
   into a list of places to widen, and a nature with eligible organs and zero
   observations is a named gap rather than a statistic.
3. **What did the system demand and never receive?** `chosen_but_never_run` is the
   join gap: selected for execution, no outcome ever came back.
4. **What would widening buy?** Resolution is reported both as it stands and as it
   would be with the currently-chosen-never-run organs reporting.

Read-only. Never writes to the store, the registry or the tree.

Run:
    python scripts/observation_coverage.py
    python scripts/observation_coverage.py --json
    python scripts/observation_coverage.py --top 15
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

_AZAZAEL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_AZAZAEL_ROOT, "src"))

EVENT_TYPE = "clusterwormz_worm_executed"

#: Every producer required to emit into the tap, with the call site that must
#: reach it. A producer listed here with zero rows is a SILENT path - which is
#: the defect this list exists to detect, so it is declared rather than inferred
#: from whatever happens to appear in the data.
EXPECTED_PRODUCERS = {
    "ClusterWORMZ": "ClusterWORMZ._execute_one / .execute_worm",
    "ConcurrencyWormExecutor": "CONCURRENCY.WormExecutor.execute_worm (all exits)",
}


def _fetch_rows(store, limit=20000, page=500):
    """Every tap row, newest first. Paginated: the store clamps to `page`."""
    rows = []
    offset = 0
    while len(rows) < limit:
        want = min(page, limit - len(rows))
        batch = store.query_memories(event_type=EVENT_TYPE, limit=want,
                                     offset=offset)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < want:
            break
        offset += len(batch)
    return rows


def producer_counts(rows):
    c = Counter()
    for r in rows:
        c[str(r.get("source") or "unknown")] += 1
    return c


def silent_producers(counts):
    """Declared producers that emitted nothing."""
    return {name: site for name, site in EXPECTED_PRODUCERS.items()
            if counts.get(name, 0) == 0}


def coverage_by_nature(eligible, observed, registry):
    """Per-nature eligible/observed counts, darkest first."""
    per = defaultdict(lambda: {"eligible": 0, "observed": 0})
    for name in eligible:
        reg = registry.get(name) or {}
        nature = str(reg.get("category") or "uncategorised")
        per[nature]["eligible"] += 1
        if name in observed:
            per[nature]["observed"] += 1
    out = []
    for nature, d in per.items():
        el, ob = d["eligible"], d["observed"]
        out.append({
            "nature": nature,
            "eligible": el,
            "observed": ob,
            "resolution": round(ob / el, 4) if el else 0.0,
        })
    # Darkest first, then by size: the biggest untouched surface leads the list.
    out.sort(key=lambda e: (e["resolution"], -e["eligible"]))
    return out


def build_report(store=None, limit=20000):
    from CORE.worm_registry import WORM_REGISTRY, discover_worms
    from CORE.WORMS.expansion import wiring_genome as wg
    from CORE.WORMS.expansion.spawn_credit import build_spawn_credit, resolve_store

    try:
        discover_worms()
    except Exception:
        pass  # scanner:ignore -- discovery failure falls back to the already-populated registry; the coverage report still renders
    registry = dict(WORM_REGISTRY)
    eligible = sorted(n for n in registry if wg.is_evolvable_organ(n))

    store = store or resolve_store()
    credit = build_spawn_credit(store)
    rewards = credit.rewards
    observed = set(rewards) & set(eligible)

    rows = _fetch_rows(store, limit=limit)
    counts = producer_counts(rows)

    chosen_never = credit.chosen_but_never_run()
    recoverable = sorted(set(chosen_never) & set(eligible))
    # What widening would buy: organs that were ASKED FOR and never reported.
    potential = sorted(observed | set(recoverable))

    n_elig = len(eligible)
    return {
        "event_type": EVENT_TYPE,
        "rows_scanned": len(rows),
        "registry_organs": len(registry),
        "eligible_organs": n_elig,
        "observed_organs": len(observed),
        "search_resolution": round(len(observed) / n_elig, 6) if n_elig else 0.0,
        "recoverable_organs": len(recoverable),
        "potential_resolution": (round(len(potential) / n_elig, 6)
                                 if n_elig else 0.0),
        "recoverable": recoverable[:40],
        "producers": dict(counts.most_common()),
        "silent_producers": silent_producers(counts),
        "attribution_mode": credit.attribution_mode,
        "n_outcome_rows": credit.n_outcome_rows,
        "n_choice_rows": credit.n_choice_rows,
        "n_malformed": credit.n_malformed,
        "chosen_but_never_run": len(chosen_never),
        "by_nature": coverage_by_nature(eligible, observed, registry),
        "errors": credit.errors[:5],
    }


def main():
    ap = argparse.ArgumentParser(description="observation coverage for the learner")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=12,
                    help="how many dark natures to print (default 12)")
    ap.add_argument("--limit", type=int, default=20000)
    args = ap.parse_args()

    rep = build_report(limit=args.limit)

    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True, default=str))
        return 0

    print("=" * 74)
    print("OBSERVATION COVERAGE")
    print("=" * 74)
    print("  rows scanned        %d" % rep["rows_scanned"])
    print("  registry organs     %d" % rep["registry_organs"])
    print("  eligible organs     %d" % rep["eligible_organs"])
    print("  observed organs     %d" % rep["observed_organs"])
    print("  SEARCH RESOLUTION   %.1f%%   (L1 can discriminate this much of the"
          " genotype)" % (rep["search_resolution"] * 100))
    print("  chosen-never-run    %d eligible  -> resolution would reach %.1f%%"
          % (rep["recoverable_organs"], rep["potential_resolution"] * 100))
    print("  attribution         %s" % rep["attribution_mode"])
    if rep["n_malformed"]:
        print("  malformed rows      %d" % rep["n_malformed"])

    print()
    print("-" * 74)
    print("PRODUCERS (who reaches the tap)")
    print("-" * 74)
    for name, n in rep["producers"].items():
        print("  %-34s %6d rows" % (name, n))
    if rep["silent_producers"]:
        print()
        print("  *** SILENT PRODUCERS (declared, emitted nothing) ***")
        for name, site in rep["silent_producers"].items():
            print("      %-30s %s" % (name, site))
    else:
        print("  every declared producer emitted.")

    print()
    print("-" * 74)
    print("DARKEST NATURES (widen here first)")
    print("-" * 74)
    print("  %-22s %8s %8s %10s" % ("nature", "eligible", "observed", "resol."))
    for e in rep["by_nature"][: args.top]:
        flag = "  <- no observations" if e["observed"] == 0 else ""
        print("  %-22s %8d %8d %9.1f%%%s"
              % (e["nature"], e["eligible"], e["observed"],
                 e["resolution"] * 100, flag))

    if rep["recoverable"]:
        print()
        print("-" * 74)
        print("CHOSEN BUT NEVER RUN (asked for, no outcome)")
        print("-" * 74)
        for name in rep["recoverable"][:20]:
            print("  %s" % name)

    if rep["errors"]:
        print()
        print("errors: %s" % rep["errors"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
