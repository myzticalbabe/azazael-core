#!/usr/bin/env python3
# coding: utf-8
"""trace_adaptation.py — hop-by-hop runtime trace of the self-adaptation chain.

MAP/TRACE EACH FUNCTION. Answers one question with evidence rather than
inspection: does a genome that evolution promoted actually change what
production training does?

The chain, and the function at each hop:

  H1  training data    DataPipelineWorm.extract_from_memory
  H2  trainer engaged  ScheduledTrainingWorm._get_rl      (RLTrainLoop)
  H3  promotion file   ScheduleConfig.evolved_param_paths
  H4  gene validation  rl_gene_bounds.validate_genes
  H5  reader           ScheduleConfig.apply_evolved_overrides
  H6  worm wiring      ScheduledTrainingWorm.__init__
  H7  CONSUMER         RLTrainLoop.train_from_memory  <-- captured live, not read
  H8  evolution source evolution_result rows (last_best_genes)
  H9  version registry ModelVersioner.list_versions / get_history

H7 is the point of the script. H5 returning True only proves the config
*holds* the genome; it does not prove the genome reaches the Q-update. So
H7 monkeypatches train_from_memory, runs a real execute(), and compares the
kwargs the trainer was actually called with against the fields the config
claims to have applied. Anything the config holds but never passes shows up
as DROPPED.

Side effects are contained: record_to_memory is forced off for the traced
run, so the trace does not write training bookkeeping into the store.

Usage:
    python scripts/trace_adaptation.py            # full trace
    python scripts/trace_adaptation.py --no-run   # skip H7's live training run
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
for _p in (_SRC, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OK = "  OK  "
BAD = " FAIL "
WARN = " WARN "

# Fields the reader claims to apply, mapped to the kwarg name the trainer takes.
# epsilon_decay is named identically in both, which is exactly why a missing
# passthrough is easy to miss by eye.
CONFIG_TO_TRAINER = {
    "learning_rate": "learning_rate",
    "discount_factor": "discount_factor",
    "epsilon": "epsilon",
    "epsilon_decay": "epsilon_decay",
}


def _hdr(n, title):
    print("\n" + "=" * 74)
    print(f"H{n}  {title}")
    print("=" * 74)


def h1_training_data():
    """H1: is there data for the trainer to learn from at all?"""
    _hdr(1, "training data  ->  DataPipelineWorm.extract_from_memory")
    try:
        from CORE.WORMS.expansion.learning import DataPipelineWorm
    except Exception as exc:
        print(f"{BAD} import failed: {type(exc).__name__}: {exc}")
        return None
    try:
        pipe = DataPipelineWorm()
        recs = pipe.extract_from_memory(limit=200)
        n = len(recs)
        sources = {}
        for r in recs:
            sources[r.get("source", "?")] = sources.get(r.get("source", "?"), 0) + 1
        print(f"{OK if n else WARN} extracted {n} record(s)")
        if n:
            top = sorted(sources.items(), key=lambda kv: -kv[1])[:5]
            print(f"       top sources: {top}")
        else:
            print("       NOTE: 0 records -> execute() substitutes 3 SYNTHETIC")
            print("             rows; any reward from there is not learned skill.")
        return n
    except Exception as exc:
        print(f"{BAD} extract failed: {type(exc).__name__}: {exc}")
        return None


def h2_trainer():
    """H2: does the worm resolve an RLTrainLoop?"""
    _hdr(2, "trainer engaged  ->  ScheduledTrainingWorm._get_rl")
    try:
        from CORE.WORMS.expansion.scheduling import ScheduledTrainingWorm
    except Exception as exc:
        print(f"{BAD} import failed: {type(exc).__name__}: {exc}")
        return None
    try:
        worm = ScheduledTrainingWorm()
        rl = worm._get_rl()
        if rl is None:
            print(f"{BAD} _get_rl() returned None — execute() would run with")
            print("       avg_reward=0.0 / q_table_size=0 and still report success")
            return None
        print(f"{OK} RLTrainLoop resolved: {type(rl).__name__}")
        return worm
    except Exception as exc:
        print(f"{BAD} construction failed: {type(exc).__name__}: {exc}")
        return None


def h3_promotion_file():
    """H3: which promotion file is the chain actually going to read?"""
    _hdr(3, "promotion file  ->  ScheduleConfig.evolved_param_paths")
    from CORE.WORMS.expansion.scheduling import ScheduleConfig

    paths = ScheduleConfig.evolved_param_paths()
    existing = []
    for p in paths:
        marker = "EXISTS" if p.exists() else "absent"
        print(f"  [{marker}] {p}")
        if p.exists():
            existing.append(p)
    # Preference order is only a *candidate* order now; validity decides.
    # (state/ untracked = canonical, config/ tracked = legacy)
    if not existing:
        print(f"{FAIL if False else WARN} no promotion file present -> chain runs on")
        print("       built-in defaults. Evolution has promoted nothing yet, or")
        print("       the file was deleted (rollback).")
    else:
        print(f"{OK} {len(existing)} candidate(s) present")
    return existing


def h4_validate(paths):
    """H4: does each present candidate carry a genome this build accepts?"""
    _hdr(4, "gene validation  ->  rl_gene_bounds.validate_genes")
    if not paths:
        print("  (skipped — no candidate files)")
        return None
    try:
        from CORE.WORMS.expansion.rl_gene_bounds import RL_GENE_BOUNDS, validate_genes
    except Exception as exc:
        print(f"{BAD} import failed: {type(exc).__name__}: {exc}")
        return None

    print("  canonical bounds (single source shared by writer + reader):")
    for g in sorted(RL_GENE_BOUNDS):
        lo, hi = RL_GENE_BOUNDS[g]
        print(f"    {g:<16} [{lo}, {hi}]")

    winner = None
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"{BAD} {p.parent.name}/: unreadable ({type(exc).__name__})")
            continue
        genes = data.get("genes")
        check = validate_genes(genes)
        if check.ok:
            print(f"{OK} {p.parent.name}/: VALID  {check.genes}")
            if check.ignored:
                print(f"       ignored genes: {sorted(check.ignored)}")
            if winner is None:
                winner = check.genes
        else:
            print(f"{BAD} {p.parent.name}/: OUT OF RANGE — {'; '.join(check.problems)}")
            print("       a promoted champion is being REJECTED at this hop")
    return winner


def h5_reader():
    """H5: what does a fresh config end up holding?"""
    _hdr(5, "reader  ->  ScheduleConfig.apply_evolved_overrides")
    from CORE.WORMS.expansion import scheduling as sch

    default = sch.ScheduleConfig()
    defaults = {k: getattr(default, k) for k in CONFIG_TO_TRAINER}
    cfg = sch.ScheduleConfig()
    applied = cfg.apply_evolved_overrides()
    live = {k: getattr(cfg, k) for k in CONFIG_TO_TRAINER}

    print(f"  applied = {applied}")
    print(f"  {'field':<18} {'built-in':>10} {'effective':>12}  source")
    for k in CONFIG_TO_TRAINER:
        src = "EVOLVED" if abs(live[k] - defaults[k]) > 1e-12 else "built-in"
        print(f"  {k:<18} {defaults[k]:>10} {live[k]:>12}  {src}")
    return cfg, applied


def h7_consumer(worm, cfg, run_live=True):
    """H7: the real question — what does the TRAINER get called with?"""
    _hdr(7, "CONSUMER  ->  RLTrainLoop.train_from_memory  (captured live)")
    from CORE.WORMS.expansion.learning_rl import RLTrainLoop

    captured = {}
    original = RLTrainLoop.train_from_memory

    def spy(self, *args, **kwargs):
        captured.update(kwargs)
        return original(self, *args, **kwargs)

    RLTrainLoop.train_from_memory = spy
    try:
        if not run_live:
            print("  (--no-run: skipping the live execute())")
            return None
        # Keep the trace side-effect free: no training bookkeeping is written.
        worm.config.record_to_memory = False
        run = worm.execute()
    finally:
        RLTrainLoop.train_from_memory = original

    if not captured:
        print(f"{BAD} train_from_memory was NEVER CALLED")
        if getattr(run, "skipped", False):
            print(f"       execute() short-circuited: skipped=True")
            print(f"       reason: {getattr(run, 'skip_reason', None)}")
            print("       -> NO adaptation can occur on a skipped run, no matter")
            print("          how valid the promoted genome is.")
        else:
            print("       execute() completed without reaching the trainer")
        return None

    print("  kwargs the trainer was ACTUALLY called with:")
    for k in sorted(captured):
        print(f"    {k:<18} = {captured[k]}")

    print("\n  config field -> trainer kwarg (the passthrough audit):")
    dropped = []
    for field, kwarg in CONFIG_TO_TRAINER.items():
        want = getattr(cfg, field)
        got = captured.get(kwarg, "<NOT PASSED>")
        if got == "<NOT PASSED>":
            dropped.append((field, kwarg, want))
            print(f"  {BAD} {field:<16} -> {kwarg:<16} DROPPED "
                  f"(config holds {want}, trainer never sees it)")
        elif isinstance(got, float) and abs(got - want) > 1e-12:
            print(f"  {WARN} {field:<16} -> {kwarg:<16} MISMATCH "
                  f"(config {want} != trainer {got})")
        else:
            print(f"  {OK} {field:<16} -> {kwarg:<16} {got}")
    if dropped:
        print(f"\n  {len(dropped)} evolved gene(s) applied but never used -> the GA")
        print("  can evolve them and production will ignore the result.")
    else:
        print("\n  every applied gene reaches the trainer.")
    print(f"\n  run: success={run.success} skipped={run.skipped} "
          f"reward={run.avg_reward} q_table={run.q_table_size} "
          f"records={run.records_extracted}")
    return captured


def h8_evolution():
    """H8: what has evolution actually produced to promote?"""
    _hdr(8, "evolution source  ->  evolution_result rows")
    try:
        from CORE.local_memory import LocalMemoryStore
        store = LocalMemoryStore()
        rows = store.fetch_all_memories(event_type="evolution_result",
                                        limit=5000)
        if not rows:
            print(f"{WARN} no evolution_result rows -> nothing for the GA to")
            print("       have promoted. The loop has never closed a generation.")
            return
        print(f"{OK} {len(rows)} evolution_result row(s)")
        best = None
        for r in rows:
            d = r.get("data", r) if isinstance(r, dict) else {}
            if isinstance(d, dict) and d.get("best_genes"):
                best = d
        if best:
            print(f"  newest best_genes = {best.get('best_genes')}")
            keys = sorted(k for k in best if "fit" in k or "reeval" in k)
            print(f"  fitness-bearing fields: "
                  f"{ {k: best[k] for k in keys} }")
        else:
            print(f"{WARN} rows exist but none carry best_genes")
    except Exception as exc:
        print(f"{BAD} {type(exc).__name__}: {exc}")


def h9_versioner():
    """H9: does the registry survive the process (read path)?"""
    _hdr(9, "version registry  ->  ModelVersioner read path")
    try:
        from CORE.WORMS.expansion.learning_versioner import ModelVersioner
        from CORE.local_memory import LocalMemoryStore
        v = ModelVersioner()
        # Discover model names from the persisted registrations, since the
        # read API is per-model (list_versions(name)).
        names = []
        try:
            for row in LocalMemoryStore().fetch_all_memories(
                    event_type="model_registered", limit=2000):
                d = row.get("data", row) if isinstance(row, dict) else {}
                # The versioner records `model_name` (not `name`/`model`) — an
                # earlier version of this tracer looked for the wrong keys and
                # reported "no model_registered rows" over a store that had
                # tens of thousands of them. Wrong key, not missing data.
                n = d.get("model_name") or d.get("name") or d.get("model")
                if n and n not in names:
                    names.append(n)
        except Exception:
            pass
        if not names:
            print(f"{WARN} no model_registered rows found in the store")
            return
        print(f"  {len(names)} model name(s) persisted: {names[:6]}"
              f"{'...' if len(names) > 6 else ''}")
        for name in names[:5]:
            versions = v.list_versions(name) or []
            hist = v.get_history(name)
            newest = versions[-1].get("version") if versions else None
            print(f"    {name:<28} versions={len(versions):<4} newest={newest}"
                  f"  history={len(hist) if hist else 0}")
        if v.get_history(names[0]) is None:
            print(f"{BAD} get_history() returned None — read path is broken")
        else:
            print(f"{OK} read path returns data (write-only registry is fixed)")
    except Exception as exc:
        print(f"{BAD} {type(exc).__name__}: {exc}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-run", action="store_true",
                    help="skip the live execute() in H7")
    args = ap.parse_args()

    print("ADAPTATION CHAIN TRACE")
    print(f"project root: {_ROOT}")

    h1_training_data()
    worm = h2_trainer()
    paths = h3_promotion_file()
    h4_validate(paths)
    cfg, applied = h5_reader()
    if worm is not None:
        print("\n" + "-" * 74)
        print("H6  worm wiring  ->  ScheduledTrainingWorm.__init__")
        print("-" * 74)
        live = {k: getattr(worm.config, k) for k in CONFIG_TO_TRAINER}
        print(f"  worm.config holds: {live}")
        print(f"  matches a fresh config? "
              f"{all(abs(live[k] - getattr(cfg, k)) < 1e-12 for k in CONFIG_TO_TRAINER)}")
        h7_consumer(worm, cfg, run_live=not args.no_run)
    h8_evolution()
    h9_versioner()
    print("\n" + "=" * 74)
    print("trace complete")
    print("=" * 74)


if __name__ == "__main__":
    main()
