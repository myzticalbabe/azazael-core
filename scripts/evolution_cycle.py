"""Evolution cycle: evolve RLTrainLoop hyperparameters for training reward.

Robustness: seeded RNG (logged, replayable), seeded population from last
best genome + HOF + TPE suggests + QD elite (continuity across runs),
early stopping on convergence (max 15 gens, MIN_GENS 6 gate), per-gen
trajectory (+mean, EV-8) in the artifact row.
Genome: {learning_rate, epsilon, discount, epsilon_decay}. Fitness
(v3-dual-robust): min over 3 exploration salts of (info avg +
critical-alert avg) over EVAL_EPISODES-run evaluations. Search: GA
(SBX+poly) + SepCMAES second arm (winner-takes-best) + NSGA-II knee
refinement. PROMOTES to production defaults when the SPRT evidence gate
accepts H1 (replacing the old fixed 3-win rule), then pareto alert-floor,
fresh-pool holdout, and one-shot canary all pass (guardrailed, rollback
= restore .prev / delete state/evolved_rl_params.json). All runs recorded
by the training loop itself.

Deterministic fitness (2026-09-07 fix): RLTrainLoop explores via the
module-level `random` module, so an unseeded evaluation is a fresh random
draw every time — the same genome could score +14.7 in one run and -8.1
in the next, making the GA chase noise. fitness() now seeds the RNG from
a stable hash of the genome's genes, so a genome's score is a pure
function of (genome, eval pool). The pool fingerprint (severity
histogram) is recorded in the artifact so landscape drift between runs
is observable instead of silent.

Import-safety (2026-09-08 fix): all execution lives in main(), guarded by
`if __name__ == '__main__'`. Importing this module (for tests or the
benchmark) now loads only the pure helpers; it no longer triggers a GA run
or a promotion write.
"""
import hashlib
import json
import math
import os
import random
import sqlite3
import sys
import argparse
# Repo-relative src (Oct 6, 2026 fix): the hardcoded
# 'C:/Users/clint/azazael/src' pointed at a STALE checkout and poisoned
# sys.path for any pytest process importing this module, so later CORE.*
# imports resolved stale (e.g. web_dashboard SB3 card "No data" vs live
# "No SB3 training runs yet" — isolation failure in chunked runs).
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import time
from datetime import datetime, timezone

os.makedirs(os.path.join(_REPO, 'logs'), exist_ok=True)
_LOG = open(os.path.join(_REPO, 'logs', 'evolution_cycle.log'), 'a', encoding='utf-8')


def log(msg):
    line = '%s %s' % (time.strftime('%Y-%m-%dT%H:%M:%S'), msg)
    print(line, flush=True)
    _LOG.write(line + '\n')
    _LOG.flush()


def _git_sha():
    """Runner tree's git sha for row attribution. Returns None if unavailable."""
    try:
        import subprocess as _sp
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = _sp.run(['git', 'rev-parse', 'HEAD'], cwd=root,
                    capture_output=True, text=True, timeout=10,
                    encoding='utf-8', errors='replace')
        if r.returncode == 0:
            return r.stdout.strip() or None
    except Exception as e:
        try:
            log('git sha lookup failed: %s' % str(e)[:80])
        except Exception:
            _ = None
    return None


from CORE.WORMS.expansion.evolution import EvolutionEngine
from CORE.WORMS.expansion.trainers_v2 import RLTrainLoopV2, create_trainer
from CORE.WORMS.expansion.scheduling import EVAL_EPISODES
import shutil


# G-3: shared tuning/deployment horizon (was a hardcoded 80 here vs 50 in
# ScheduleConfig — discount/decay tuned on one horizon, deployed on another).
N_EPISODES = EVAL_EPISODES  # eval budget per skill (runs take ~10-15s, fits the slot)

# NSGA-II multi-objective refinement (2026-09-08 expansion): after the scalar
# loop, info mastery and critical-alert mastery are treated as separate
# objectives, the Pareto front of the evolved population is surfaced, and a
# 'knee' genome (max normalized objective sum) is picked as the balanced
# alternative. Budget-bounded: 2 generations costs ~50-60s extra, fits the
# 5-min slot next to the scalar loop's ~40s.
MULTI_GENS = 2

# Sources that are pure training/bookkeeping noise (the training loops record
# their own training_complete / model_registered events back into the store;
# together RLTrainLoop+RLTrainLoopV2+ModelVersioner are ~70% of all rows). Excluding them
# keeps the eval pools on real worm/production signals instead of the system
# optimizing against its own feedback loop.
EXCLUDED_TRAINING_SOURCES = [
    'RLTrainLoop',
    'RLTrainLoopV2',
    'ModelVersioner',
    'GRPOTrainer',
    'EvolutionEngine',
    'sb3_agent',
    'TrainingOrchestrator',
    'ABTestRunner',
    'FleetTrainer',
]


def genome_seed(genes, salt='a'):
    """Stable per-genome RNG seed (same genes+salt -> same seed, any process)."""
    s = salt + '|' + ','.join('%s=%.6f' % (k, float(genes[k])) for k in sorted(genes))
    return int(hashlib.sha256(s.encode('utf-8')).hexdigest()[:8], 16)


# Salts for the conservative min-estimator. Three, not two: one lucky seed
# must not be able to carry a genome into production.
_SALTS = ('a', 'b', 'c')

#: Identity of the margin estimator, stamped into every evolution_result row as
#: `margin_estimator`. Two rows are comparable only when the SAME estimator
#: produced their margins, so this string is part of a row's era --
#: `scripts/benchmark_evolution.py` groups rows by it and reports unstamped ones
#: separately. The stamp producer had gone missing: measured 2026-11-02, all 63
#: live evolution_result rows lacked the key, so every row read as its own era.
MARGIN_ESTIMATOR = 'min3-salt'


def _trainer_extra_kwargs(genes):
    """Forward extra (Phase E) genes to the trainer, or nothing at all.

    `_eval_salt` reads exactly four keys, so a fifth gene is DROPPED unless it
    is forwarded — the search would appear to grow while the fitness landscape
    stayed four-dimensional. Wrapped in try/except because a training kwargs
    failure must never take out the whole cycle.
    """
    if not PROTEUS_MORPH:
        return {}
    try:
        from CORE.WORMS.expansion.proteus_hooks import morph_trainer_kwargs
        return morph_trainer_kwargs(genes)
    except Exception:
        return {}


def _eval_salt(genes, info_pool=None, alert_pool=None, rng=None):
    """One dual-skill evaluation. Returns (total, info, alert).

    persist=False (2026-09-08): GA eval runs score without writing back —
    no training_complete / model_registered / q_table_persisted rows, so a
    full cycle no longer bloats the store or model registry with ~96
    bookkeeping events per run.

    epsilon_decay (2026-09-08 expansion): passed through from the genome so
    the GA can evolve exploration-annealing speed.

    Frozen pools (2026-09-08 fix): when info_pool/alert_pool are given, both
    skills train on those snapshots instead of re-extracting the live
    window — the 300-row eval window drifts ~3 fitness points during a
    single cycle (monitor writes), which used to contaminate trajectories
    and the promotion comparison. None = live extraction (backward compat).

    Isolated RNG (2026-09-09): pass a seeded random.Random and the eval is
    a pure function of (genome, pools, seed) with zero global-state
    traffic — safe for parallel fitness evaluation under free-threading.

    3-salt min (2026-09-09): the min is taken over three salts, so one
    lucky seed can't carry a genome. +50% eval cost, covered by the 4x
    parallel speedup from isolated RNG.
    """
    _extra = _trainer_extra_kwargs(genes)
    # Use new trainer with worm identity for evolution cycle
    loop = create_trainer(task_type="offline", worm_id="evolution_cycle", task_id=f"eval_{genome_seed(genes)}")
    info_stats = loop.train_from_memory(
        **_extra,
        n_episodes=N_EPISODES, max_steps=50,
        learning_rate=float(genes['learning_rate']),
        epsilon=float(genes['epsilon']),
        epsilon_decay=float(genes.get('epsilon_decay', 0.0)),
        discount_factor=float(genes['discount']),
        exclude_sources=EXCLUDED_TRAINING_SOURCES,
        records=info_pool,
        persist=False,
        rng=rng)
    loop2 = create_trainer(task_type="offline", worm_id="evolution_cycle", task_id=f"eval_alert_{genome_seed(genes)}")
    alert_stats = loop2.train_from_memory(
        **_extra,
        n_episodes=N_EPISODES, max_steps=50,
        learning_rate=float(genes['learning_rate']),
        epsilon=float(genes['epsilon']),
        epsilon_decay=float(genes.get('epsilon_decay', 0.0)),
        discount_factor=float(genes['discount']),
        event_type='db_health_alert',
        exclude_sources=EXCLUDED_TRAINING_SOURCES,
        records=alert_pool,
        persist=False,
        rng=rng)
    info = float(info_stats.get('avg_reward', 0.0))
    alert = float(alert_stats.get('avg_reward', 0.0))
    return (_qd_weighted_total(info, alert), info, alert)


def fitness_skill(genes, idx, info_pool=None, alert_pool=None):
    """Deterministic single-objective fitness (min over 2 salts), like fitness_genes.

    idx: 1 = info-skill score, 2 = alert-skill score. Used by the NSGA-II
    multi-objective phase so the Pareto front trades info mastery vs
    critical-alert mastery as separate objectives.
    """
    best = None
    for salt in _SALTS:
        parts = _eval_salt(genes, info_pool, alert_pool,
                           rng=random.Random(genome_seed(genes, salt)))
        v = parts[idx]
        best = v if best is None else min(best, v)
    return best


def fitness_genes(genes, info_pool=None, alert_pool=None):
    """v3-dual-robust fitness: min over 2 exploration salts.

    v3 (2026-09-08): single-salt evals let lucky exploration draws win, so
    selection chased noise within the 11-14 band. min-of-2 salts pressures
    robust genes (both draws must score). min <= single-salt mean, so v3
    totals run slightly below v2 for the same genome — same units, and the
    monitor's scale-break note covers the step. Deterministic: pure
    function of (genome, pools).
    """
    best = None
    for salt in _SALTS:
        total, _, _ = _eval_salt(genes, info_pool, alert_pool,
                                 rng=random.Random(genome_seed(genes, salt)))
        best = total if best is None else min(best, total)
    return best


def fitness_parts(genes, info_pool=None, alert_pool=None):
    """Recompute (total, info, alert) of the argmin salt — reproduces fitness_genes exactly."""
    results = []
    for salt in _SALTS:
        results.append(_eval_salt(genes, info_pool, alert_pool,
                                  rng=random.Random(genome_seed(genes, salt))))
    results.sort(key=lambda r: r[0])
    total, info, alert = results[0]
    return (round(total, 4), round(info, 4), round(alert, 4))


def fitness(genome):
    return fitness_genes(genome.genes)


def baseline_hash(genes):
    """Stable id of the production baseline genome (2026-09-10 audit EV-2).

    The promotion streak was era-pinned by pool_id only, so a mid-era change of
    the BASELINE slipped through: on 2026-09-10 an external restore reverted
    production from a 29.67-fitness genome to a 28.23 one while pool_id stayed
    identical, and wins measured against the two different baselines were
    counted as a single streak. The streak now also requires an unchanged
    baseline genome; rows written before this fix carry no baseline_hash and
    therefore break the streak (fails closed).
    """
    return hashlib.sha256(
        ','.join('%s=%.6f' % (k, float(genes[k])) for k in sorted(genes))
        .encode('utf-8')).hexdigest()[:16]


# EV-5 (2026-09-30): objective-arm liveness veto.
#
# MEASURED: over 35 consecutive cycles `alert` is exactly 0.0 and `total`
# equals `info` to four decimals on every single line, because the frozen
# alert pool holds SEVEN rows against info's 500. So the loop has been running
# a single-objective search while reporting a two-objective score, and
# state/evolved_rl_params.json was promoted on 2026-09-28 carrying
# fitness_detail {info: 16.3329, alert: 0.0}.
#
# WHY NOTHING CAUGHT IT: every instrument reads `total`, and `total` looks
# healthy. A scalar aggregating one live component and one dead component is
# indistinguishable from a scalar where both are live. This is the same shape
# as the Intermesh finding that 22 of 26 natures declare no data contract, so
# `emits`/`requires` return empty dicts that read as CLEAN.
#
# THE VETO: a total is only a multi-objective score if every component
# contributes. Promoting on a total that equals one arm asserts a capability
# the measurement does not contain. This is deliberately a HARD STOP, and
# deliberately a constant rather than a heuristic, so relaxing it is a
# decision someone makes on the record instead of a threshold that drifts.
#
# It will halt promotion until the alert arm is either grown to a fittable
# pool or formally retired. That is the point: the alternative is a loop that
# certifies a capability it cannot measure.
FITNESS_ARM_FLOOR = 1e-9
FITNESS_ARM_VETO = True

# EV-7 (2026-09-30): the loop's own health gates its promotions. See the
# block at the promotion decision for why this had to be wired at all.
LOOP_INTEGRITY_VETO = True
LOOP_INTEGRITY_MAX_AGE_S = 3600.0     # a stale verdict may not gate a live run


def _loop_integrity_gate():
    """(ok, reason). ok=False blocks promotion; reason is loggable.

    Reads CURRENT verdicts only. `current_verdict` is the recent window and
    `verdict` is all-time, because an all-time verdict is true forever and a
    condition fixed twenty cycles ago would otherwise hold a healthy loop
    hostage indefinitely.

    A MISSING or STALE file is NOT healthy and is NOT a block: the tool may
    simply not have run yet, and refusing to promote on absent instrumentation
    would be a worse failure than promoting without it. It reports BLIND so
    the gap is visible rather than silent.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    checks = (("loop_integrity.json", "current_verdict"),
              ("fitness_arms.json", "verdict"))
    now = time.time()
    for fname, key in checks:
        path = os.path.join(root, "state", fname)
        if not os.path.exists(path):
            log('EV-7 integrity: %s absent -- BLIND, not treated as healthy '
                'and not treated as blocking' % fname)
            continue
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age > LOOP_INTEGRITY_MAX_AGE_S:
            log('EV-7 integrity: %s is %.0fs old (> %.0fs) -- BLIND, stale '
                'verdict may not gate a live cycle' % (fname, age,
                                                       LOOP_INTEGRITY_MAX_AGE_S))
            continue
        try:
            with open(path, encoding='utf-8') as fh:
                d = json.load(fh)
        except Exception as e:
            log('EV-7 integrity: %s unreadable (%s) -- BLIND' % (fname, e))
            continue
        v = d.get(key)
        if v in ("DEGRADED", "DEAD_ARM", "FAIL"):
            findings = d.get("current_findings") or d.get("findings") or []
            first = str(findings[0])[:120] if findings else ''
            return False, ('%s reports %s%s -- a cycle that promotes while '
                           'the loop is measurably degrading is a symptom, '
                           'not progress' % (fname, v,
                                             (': ' + first) if first else ''))
    return True, None


def fitness_arms_live(parts, floor=FITNESS_ARM_FLOOR, total=None):
    """True when every objective arm actually contributes to `total`.

    `parts` is the {'info': x, 'alert': y} mapping from `fitness_parts`.
    An arm at or below `floor` contributes nothing, so `total` is really the
    other arm alone and the score does not represent this one. Pure function,
    so it is testable without running a GA (same convention as
    `_compute_streak`).
    """
    if not isinstance(parts, dict) or not parts:
        return False
    for name, val in parts.items():
        try:
            if float(val) <= float(floor):
                return False
        except (TypeError, ValueError):
            return False  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer
    return True


def dead_fitness_arms(parts, floor=FITNESS_ARM_FLOOR):
    """Names of the arms contributing nothing — the reason a veto fired."""
    if not isinstance(parts, dict):
        return ['(malformed parts)']
    dead = []
    for name, val in parts.items():
        try:
            if float(val) <= float(floor):
                dead.append(str(name))
        except (TypeError, ValueError):
            dead.append(str(name))
    return dead


def schema_hash(genes):
    """Stable id of the SEARCH SPACE, not of one genome (2026-09-30, EV-4).

    Third instance of one recurring defect in this file, and the same remedy
    each time. `baseline_hash` (EV-2) fixed it when a mid-era BASELINE change
    let wins against two different references count as one streak. Cranium
    (2026-09-12) fixed it when an era change broke the SPRT's evidence: "that
    break landed 28 times in 51 cycles (~1.8 cycles/era) while the SPRT needs
    2..6 same-era observations. The evidence could never accumulate, so the
    gate produced 1 promotion in 51 cycles."

    This time the SCHEMA moves. `morph_candidate_gene(cycle_index, ...)` is a
    round-robin, so slot 5 alternates every cycle, and `genome_seed` hashes
    every gene -- meaning two genomes differing only in slot 5 are evaluated by
    different trajectories and are not the same measurement. Measured over 33
    cycles: 100% schema alternation across 2 arms, `beats=False streak=0/3` on
    every one, SPRT `n=0` on 21/33, and 0 promotions. The streak was being
    evaluated on CONSECUTIVE cycles, which alternate by construction, so it
    could never reach 2.

    Note this is a *space* identity, not a genome identity: the gene NAMES,
    not their values. Within one schema, differing values are exactly what the
    search is exploring and `genome_seed` already makes them comparably
    deterministic. Across schemas the gene SET differs, `_trainer_extra_kwargs`
    forwards a different kwarg, and the fitness function is genuinely a
    different function.

    The streak is therefore computed PER ARM (see `_compute_streak`): rows
    carrying a different schema_hash are skipped, not counted and not treated
    as breaks. Measured comparability available today: use_schema_features
    n=17 (16 pairs), by_session n=16 (15 pairs).
    """
    try:
        return hashlib.sha256(
            '|'.join(sorted(str(k) for k in genes)).encode('utf-8')
        ).hexdigest()[:16]
    except Exception:
        return ''  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer


def _promoted_file():
    """First existing promoted-genes file: state/ (canonical), then config/."""
    for _p in (PROMOTE_JSON, LEGACY_PROMOTE_JSON):
        if os.path.exists(_p):
            return _p
    return None


def _migrate_legacy_promotions():
    """One-time copy of pre-EV-1 promotion state from config/ into state/.

    Copy-don't-move: the legacy files stay on disk. Preserves rollback depth
    (.prev/.prev2) and makes the untracked state/ location authoritative so a
    repo operation cannot revert production genes.
    """
    for _suffix in ('', '.prev.json', '.prev2.json', '.canary.json',
                    '.canary-failed.json'):
        _dst = PROMOTE_JSON + _suffix
        _src = LEGACY_PROMOTE_JSON + _suffix
        if not os.path.exists(_dst) and os.path.exists(_src):
            try:
                shutil.copyfile(_src, _dst)
                log('migrated %s -> state/' % os.path.basename(_src))
            except Exception as e:
                log('migration failed for %s: %s' % (_src, str(e)[:80]))


def _state_key_for(record):
    """Mirror of `learning_rl._get_state_key` (nested there, so not importable)."""
    return "%s:%s" % (record.get('source', 'unknown'),
                      record.get('severity', 'info'))


def _extract_alert_pool(limit=None):
    """EV-6 (2026-09-30): the alert arm, selected on SEVERITY.

    Replaces `event_type='db_health_alert'`, which matched 7 rows store-wide.
    Selection is by the property that actually makes a record an alert, so the
    arm scales with the data instead of with a provenance label nobody writes.

    Queried as a COLUMN (`memories.severity`, `memories.source` are first-class
    in the store schema), not extracted from the JSON blob: filtering 123,279
    rows in Python over a generic recent window returned 2 usable rows, because
    121,606 of them are `info`. A column predicate gets the ~1,600 that exist
    and uses the index the schema provides.

    Reports its severity mix, because the two failure modes look identical from
    the fitness number alone: an arm that is genuinely poor, and an arm whose
    severities are missing from the reward table -- which scores 0.0 for every
    action and caps the arm at exactly zero, as `critical` did on all 35
    cycles before it was added. Reporting the mix is what tells them apart.
    """
    limit = int(limit or (N_EPISODES * 10))
    db = os.path.join(os.path.expanduser("~"), ".azazael", "memory_store.db")
    if not os.path.exists(db):
        log('alert pool: store not found at %s' % db)
        return []
    excl = [s for s in (EXCLUDED_TRAINING_SOURCES or ()) if s]
    sql = ("SELECT source, event_type, data, severity FROM memories "
           "WHERE severity IN ('critical','error','warning')")
    params = []
    if excl:
        sql += " AND source NOT IN (%s)" % ",".join("?" * len(excl))
        params += excl
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"),
                              uri=True, timeout=30)
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, params).fetchall()
        con.close()
    except Exception as e:
        log('alert pool: query failed (%s: %s)' % (type(e).__name__, str(e)[:80]))
        return []
    out, mix = [], {}
    for r in rows:
        d = {}
        try:
            d = json.loads(r["data"]) or {}
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {}
        sev = str(r["severity"] or d.get("severity") or "").lower()
        mix[sev] = mix.get(sev, 0) + 1
        # The state key is `source:severity`; `source` is a real column here,
        # so the key discriminates without inventing a value. Only fall back if
        # the column itself is empty, because stamping 'unknown' on every row
        # would reproduce the single-state collapse at a larger scale.
        src = str(r["source"] or d.get("source") or "").strip()
        d["source"] = src or str(d.get("component") or r["event_type"]
                                  or "unattributed")
        d.setdefault("severity", sev)
        d.setdefault("event_type", r["event_type"])
        out.append(d)
    if out:
        log('alert pool (by severity): %d rows  mix=%s  distinct_states=%d'
            % (len(out), mix, len({_state_key_for(d) for d in out})))
    else:
        log('alert pool (by severity): EMPTY -- no critical/error/warning rows;'
            ' the arm will train on nothing and score 0.0')
    return out


def pool_fingerprint():
    """Severity histogram of the eval pool — landscape drift indicator.

    Also reports the hard (critical-alert) pool size and newest timestamp,
    so hard-example inflow starvation is visible: if hard_count stalls for
    days, evolution is re-mastering frozen rows and needs fresh production
    errors (chaos drills, re-armed watchdogs, real incidents).
    """
    try:
        from collections import Counter
        from CORE.WORMS.expansion.learning import DataPipelineWorm
        recs = DataPipelineWorm().extract_from_memory(
            limit=300, exclude_sources=EXCLUDED_TRAINING_SOURCES)
        fp = dict(Counter(r.get('severity', 'unknown') for r in recs))
        hard = DataPipelineWorm().extract_from_memory(
            event_type='db_health_alert', limit=2000,
            exclude_sources=EXCLUDED_TRAINING_SOURCES)
        if hard:
            fp['hard_count'] = len(hard)
            fp['hard_newest'] = max(
                (r.get('timestamp', '') for r in hard), default='')
        return fp
    except Exception:
        return {}


def last_best_genes():
    """Champion genome from the most recent evolution_result row (or None).

    Prefers best_genes — the genome that actually WON the generation.
    EV-3 (2026-09-10): champion_genes used to record the input seed, so the
    chain was a fixed point — seed(n+1) == seed(n) — and champion_reeval froze
    at 21.815 across 7 consecutive runs (the old docstring claimed otherwise).
    Legacy rows without best_genes fall back to champion_genes (2026-09-08
    artifacts).
    """
    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        rows = store.query_memories(event_type='evolution_result', limit=1)
        if rows:
            data = rows[0].get('data', {}) or {}
            genes = data.get('best_genes') or data.get('champion_genes')
            if isinstance(genes, dict) and genes:
                return genes
    except Exception as e:
        log('seed lookup failed (fresh start): %s' % str(e)[:120])
    return None


def _pareto_blocked_streak(limit=30):
    """Consecutive recent cycles blocked by the pareto balance gate.

    EV-9 (2026-09-11): a gate that blocks forever is indistinguishable from a
    healthy system unless the repetition is measured. 34 consecutive cycles
    reaching SPRT 'promote' and promoting nothing looked exactly like normal
    operation in the log. Returns how many of the most recent artifacts FAILED
    the gate; a cycle that never reached the gate (no margin/SPRT, or a legacy
    row without one) ends the streak, because it is not evidence of blockage.
    """
    try:
        from CORE.local_memory import get_memory_store
        rows = get_memory_store().query_memories(
            event_type='evolution_result', limit=int(limit))
    except Exception:
        return 0  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer
    streak = 0
    for row in rows or []:
        gate = (row.get('data', {}) or {}).get('pareto_gate') or {}
        if not gate or gate.get('passed') is not False:
            break
        streak += 1
    return streak


# Champion promotion (2026-09-08): 3 consecutive wins over the production
# baseline promotes genes to state/evolved_rl_params.json, which
# ScheduledTrainingWorm loads as hyperparameter overrides.
# Guardrails: deterministic 3-salt re-eval (no luck), margin 0.75,
# scale-consistent streak (v3 rows only), range-validated genes, fresh-pool
# holdout gate, .prev backup + one-shot auto-canary with landscape probe.
PROMOTE_MARGIN = 0.75
PROMOTE_WINS = 3

# --- SPRT promotion gate (2026-09-11, followup #2) --------------------------
# The fixed "3 consecutive wins" rule had no variance model: a +0.76 margin and
# a +10.0 margin both required exactly 3 wins, and the era/baseline pinning
# (pool_id + baseline_hash) existed only to stop heterogeneous margins being
# compared. SPRT treats each cycle's margin as ONE observation of a true effect
# and stops as soon as the evidence is sufficient, so large margins promote
# sooner and marginal ones need more evidence.
#
#   H0: true margin = SPRT_DELTA0 (no real improvement)
#   H1: true margin = SPRT_DELTA1 (worth acting on == PROMOTE_MARGIN)
#
# Decisions: LLR >= SPRT_UPPER -> accept H1 (promote); LLR <= SPRT_LOWER ->
# accept H0 (reset). PROMOTE_MIN_WINS is a hard floor so a single lucky cycle
# can never promote on a degenerate sigma.
SPRT_ALPHA = 0.05     # tolerated false-promote rate
SPRT_BETA = 0.10      # tolerated false-hold rate
SPRT_DELTA0 = 0.0     # H0 effect size
SPRT_DELTA1 = PROMOTE_MARGIN  # H1 effect size (same unit as the gate margin)
PROMOTE_MIN_WINS = 2  # floor: never promote on one observation
PROMOTE_MAX_WINS = 6  # cap: inconclusive evidence stops accumulating
SPRT_UPPER = math.log((1.0 - SPRT_BETA) / SPRT_ALPHA)
SPRT_LOWER = math.log(SPRT_BETA / (1.0 - SPRT_ALPHA))
PROMOTE_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'state', 'evolved_rl_params.json')
# Legacy location (2026-09-10 audit EV-1/D-1): live genes used to be written to
# config/evolved_rl_params.json, which is TRACKED by git. A git restore/checkout
# reverted production genes to the committed Sep-7 genome at 23:24:38 on
# 2026-09-10, silently bypassing streak -> holdout -> promote -> canary in
# reverse. state/ is gitignored, so repo operations can no longer touch
# production genes. This path is still read as a fallback, never written.
LEGACY_PROMOTE_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'config', 'evolved_rl_params.json')


PROMOTE_KEYS = ('learning_rate', 'epsilon', 'discount', 'epsilon_decay')


# === Promotion pipeline (canonical doc, followup #10) =====================
# One genome reaches production only through all four gates, in order:
#   1. STREAK — beats production by PROMOTE_MARGIN on the frozen pools in
#      PROMOTE_WINS consecutive cycles (scale-consistent: v3 rows only).
#   2. HOLDOUT — beats the baseline by the same margin on FRESH pools
#      extracted after the GA ran (fail closed on extraction failure).
#      Blocks snapshot overfitting (caught 2 on day one: 23:54, 12:29).
#   3. PROMOTE — .prev -> .prev2, current -> .prev, candidate written to
#      evolved_rl_params.json, one-shot canary armed.
#   4. CANARY — next fresh-pool run re-evaluates production + built-in
#      defaults (landscape probe). Stable landscape + regress > CANARY_TOL
#      restores .prev (fallback .prev2). Fires once, then self-deletes.
#      Caught a real 2.96 regression on day one (12:04).
# Fitness throughout is the 3-salt min over isolated RNG (deterministic:
# same genome + same pool_id => bit-identical score). Margins, not raw
# scores, are the cross-pool-era currency — raw fitness is pool-polluted
# (±1.5 baseline swings observed across eras).
# Rollback, manual: restore .prev.json (.prev2.json) over the production
# file, or delete the production file to fall back to built-in defaults.
# =========================================================================
PREV_JSON = PROMOTE_JSON + '.prev.json'
PREV2_JSON = PROMOTE_JSON + '.prev2.json'
CANARY_JSON = PROMOTE_JSON + '.canary.json'
CANARY_TOL = 1.5
LANDSCAPE_TOL = 1.0


def _compute_streak(rows, pool_id, baseline_id, beats, schema_id=None):
    """Consecutive same-era wins over the production baseline (EV-2).

    Extracted from main() (2026-09-10) so the promotion gate is testable
    without running a GA. A prior win counts only when it came from the SAME
    pool era (pool_id) AND was measured against the SAME production baseline
    (baseline_hash): either changing mid-streak invalidates the accumulated
    wins, because they were scored against different data or a different
    reference genome.

    Rows lacking either tag (pre-fix artifacts) break the streak — fails
    closed, so a promotion always needs PROMOTE_WINS wins recorded by current
    code. Returns 0 whenever this cycle did not beat the baseline.

    EV-4 (2026-09-30): the streak is now computed PER SCHEMA ARM. A row whose
    `schema_hash` differs is SKIPPED rather than treated as a break, because
    the morph rotates the extra gene every cycle and a cross-arm comparison is
    not a valid streak link. Breaking on it (the naive fix) would hold the
    streak at 1 forever under a full round-robin; skipping it lets each arm
    accumulate over its own history, which is the comparison that is actually
    like-for-like. An era or baseline change still breaks, unchanged.

    Rows predating EV-4 carry no schema_hash. They are treated as a DIFFERENT
    arm (skipped, not counted) so an unlabelled row can never manufacture a
    streak across a space change — fails closed for the new dimension while
    keeping the old dimensions strict.
    """
    if not beats:
        return 0
    streak = 1
    for row in list(rows or []):
        if streak >= PROMOTE_WINS:
            break
        data = row.get('data', {}) or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                break
        if not isinstance(data, dict):
            break
        if data.get('pool_id') != pool_id:
            break
        # EV-2: a mid-era BASELINE change invalidates the streak too.
        if data.get('baseline_hash') != baseline_id:
            break
        # INSTRUMENT ERA: a margin measured by a different estimator, or over a
        # different eval budget, is not a like-for-like observation. Fails
        # CLOSED -- a row with no stamp at all (a pre-2026-09-18 artifact) breaks
        # the streak instead of counting, and the row written by this driver now
        # always carries MARGIN_ESTIMATOR + N_EPISODES, so a missing stamp can
        # only come from older code or a hand-edited store.
        if data.get('margin_estimator') != MARGIN_ESTIMATOR:
            break
        if data.get('eval_episodes') != N_EPISODES:
            break
        # EV-4: a different SEARCH SPACE is a different arm, not a break.
        if schema_id and data.get('schema_hash') != schema_id:
            continue
        if (str(data.get('fitness', '')).startswith('v3')
                and data.get('beats_baseline')):
            streak += 1
        else:
            break
    return min(streak, PROMOTE_WINS)


# --- SPRT helpers (followup #2) --------------------------------------------

def _row_payload(row):
    """Decode one stored evolution_result row into its data dict (or None)."""
    data = (row or {}).get('data', {})
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return None  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer
    return data if isinstance(data, dict) else None


def sprt_llr_step(delta, sigma, delta1=SPRT_DELTA1, delta0=SPRT_DELTA0):
    """One SPRT log-likelihood-ratio increment for a normal mean.

    Observation delta ~ N(mu, sigma); H0: mu=delta0, H1: mu=delta1.
    The increment is ((d-d0)^2 - (d-d1)^2) / (2 sigma^2), which is zero at the
    indifference point (d0+d1)/2, positive for strong evidence and negative for
    evidence against. sigma is floored so a degenerate estimate cannot make the
    ratio explode.
    """
    try:
        d = float(delta)
    except (TypeError, ValueError):
        return 0.0  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer
    if not math.isfinite(d):
        return 0.0
    s = max(float(sigma), 1e-6)
    return ((d - delta0) ** 2 - (d - delta1) ** 2) / (2.0 * s * s)


def salt_spread(genes, info_pool, alert_pool):
    """Per-salt total spread: an in-cycle floor for the SPRT sigma.

    fitness_genes() reports min-over-salts, which deliberately hides the spread
    it is robust against. Recovering the raw per-salt totals gives a noise
    estimate available on the very first cycle, before any margin history
    exists. Returns a standard deviation (0.0 when it cannot be computed).
    """
    try:
        totals = []
        for salt in _SALTS:
            total, _, _ = _eval_salt(genes, info_pool, alert_pool,
                                     rng=random.Random(genome_seed(genes, salt)))
            totals.append(float(total))
        if len(totals) < 2:
            return 0.0
        mean = sum(totals) / len(totals)
        var = sum((t - mean) ** 2 for t in totals) / (len(totals) - 1)
        return var ** 0.5
    except Exception:
        return 0.0  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer


def estimate_margin_sigma(margins, salt_floor=0.0):
    """Sigma for the SPRT: observed margin dispersion, floored by eval noise.

    Each cycle contributes one margin observation, so the dispersion of the
    era's margins is the per-observation standard deviation the test needs. With
    fewer than two observations it falls back to the salt-spread floor.
    """
    clean = []
    for m in (margins or []):
        try:
            fm = float(m)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fm):
            clean.append(fm)
    floor = max(abs(float(salt_floor or 0.0)), 1e-6)
    if len(clean) >= 2:
        mean = sum(clean) / len(clean)
        var = sum((m - mean) ** 2 for m in clean) / (len(clean) - 1)
        return max(var ** 0.5, floor)
    return floor


def _sprt_history(rows, pool_id, baseline_id, limit=PROMOTE_MAX_WINS,
                  schema_id=None, scan=40):
    """Era-pinned prior margins (newest-first), per SCHEMA ARM (EV-4).

    Same pinning as _compute_streak: only rows from the SAME pool era and the
    SAME production baseline are comparable, and only v3-scale rows carry a
    usable margin. A break is a hard stop, so a promotion always rests on a
    contiguous run of same-era evidence.

    EV-4 (2026-09-30): the run must additionally be one SEARCH SPACE. This is
    the gate that actually decides promotion — the streak at the call site is
    explicitly "retained only as a display/compatibility counter" — so a
    per-arm streak alone would have changed nothing.

    Two details make this work under a full round-robin:

      * A row from a different schema_hash is SKIPPED, not a break. The morph
        rotates the extra gene every cycle, so treating it as a break pins the
        evidence run at length 1 forever. Skipping lets an arm assemble its own
        contiguous history out of the interleaved stream.
      * `scan` defaults well above `limit`, because skipping consumes scan
        budget. With 100% alternation a 6-row window can only ever surface 3
        same-arm rows — below the SPRT's 2..6 requirement at the top end, and
        below PROMOTE_WINS=3 for a streak. The scan window is what makes the
        per-arm history reachable; the returned length is still capped at
        `limit` so the SPRT's own bounds are unchanged.

    Rows written before EV-4 carry no schema_hash and are treated as a
    different arm, so an unlabelled row cannot inject evidence across a space
    change.
    """
    out = []
    for row in list(rows or [])[:max(int(scan), int(limit))]:
        data = _row_payload(row)
        if data is None:
            break
        if data.get('pool_id') != pool_id:
            break
        if data.get('baseline_hash') != baseline_id:
            break
        if schema_id and data.get('schema_hash') != schema_id:
            continue          # EV-4: different search space, different arm
        if not str(data.get('fitness', '')).startswith('v3'):
            break
        margin = data.get('margin')
        if margin is None:
            # Rows written before the margin key existed: derive it.
            try:
                margin = float(data.get('best_total')) - float(data.get('baseline'))
            except (TypeError, ValueError):
                break
        try:
            out.append(float(margin))
        except (TypeError, ValueError):
            break
        if len(out) >= limit:
            break
    return out


def _margin_repeat_streak(rows, margin_now, tol=1e-9, limit=12):
    """How many of the newest v3 rows repeat `margin_now`. Pure; no I/O.

    Extracted from `main` so the stall rule is testable without running a
    2,400-line cycle - the same reason `cranium_override` was extracted, and
    for the same reason: logic that only executes in a regime you cannot reach
    is logic that is never observed.

    Newest-first input; counting stops at the first different margin, so the
    value is a *consecutive* repeat count, not a frequency.
    """
    n = 0
    for row in list(rows or [])[:int(limit)]:
        d = _row_payload(row)
        if d is None or not str(d.get('fitness', '')).startswith('v3'):
            continue
        m = d.get('margin')
        try:
            m = float(m)
        except (TypeError, ValueError):
            try:
                m = float(d.get('best_total')) - float(d.get('baseline'))
            except (TypeError, ValueError):
                break
        if abs(m - float(margin_now)) > float(tol):
            break
        n += 1
    return n


def _sprt_collapse(seq, tol=1e-9):
    """Collapse consecutive re-measurements. Returns (collapsed, n_dropped).

    Why this is required for the SPRT to mean anything here:
    `_load_or_freeze_pools` REUSES one pool snapshot for up to `POOL_MAX_AGE_S`
    (1800s) so that streak comparisons are apples-to-apples. That is deliberate
    and was the fix for the baseline swinging 31.6 -> 26.4 on identical genes.

    The consequence was not intended. Measured 2026-09-12: twenty consecutive
    live cycles recorded the SAME margin, 0.5563, all with
    `pool_snapshot_reused: True` and the same `pool_id`. The candidate is
    re-found deterministically from the champion seed, so the margin is not
    twenty observations of "about +0.56" - it is ONE observation, re-recorded.

    The SPRT's whole premise is independent samples, and `sprt_llr_step` is
    additive, so feeding it copies inflates the log-likelihood ratio without
    bound: the live log reached llr 328 at n=7 on margins 1.33-1.39, which is
    a repeated number, not seven measurements. Deduping is the conservative
    direction - it can only make promotion harder.
    """
    out = []
    for m in (seq or []):
        try:
            v = float(m)
        except (TypeError, ValueError):
            continue
        if out and abs(v - out[-1]) <= float(tol):
            continue
        out.append(v)
    return out, max(0, len(list(seq or [])) - len(out))


def _sprt_decide(margins, salt_floor=0.0):
    """Run the SPRT over era-pinned margins plus this cycle's margin.

    margins is the newest-first list of prior same-era margins; the FINAL
    element is the current cycle's margin, so callers append it last.

    Returns (llr, sigma, n, decision) where decision is one of
    'promote' / 'reject' / 'continue' / 'inconclusive'.
    """
    seq = [float(m) for m in (margins or [])]
    if not seq:
        return 0.0, max(abs(float(salt_floor or 0.0)), 1e-6), 0, 'reject'
    sigma = estimate_margin_sigma(seq[:-1], salt_floor)
    llr = sum(sprt_llr_step(m, sigma) for m in seq)
    n = len(seq)
    if n < PROMOTE_MIN_WINS:
        decision = 'continue'
    elif llr >= SPRT_UPPER:
        decision = 'promote'
    elif llr <= SPRT_LOWER:
        decision = 'reject'
    elif n >= PROMOTE_MAX_WINS:
        decision = 'inconclusive'
    else:
        decision = 'continue'
    return round(llr, 4), round(sigma, 6), n, decision


# --- Pareto balance gate (followup #1, 2026-09-11) --------------------------
# Why a plain dominance gate would be USELESS here: fitness is a scalar sum, so
# if the baseline dominated the candidate on BOTH objectives it would also have
# the larger total, and `beats` would already be False. Dominance can therefore
# never fire on a candidate that passed the margin gate. The constraint that
# CANNOT be expressed by the total is a one-sided regression: a genome may lift
# info hard enough to raise the sum while collapsing the critical-alert skill
# (measured: alert -13.0 with total +1.0). That is exactly how the hard pool
# starved. So the gate enforces a BOUNDED alert regression, and the NSGA-II knee
# (the balanced point on the info/alert front) is the fallback candidate.
#
# EV-9 (2026-09-11): the bound has to be SCALED to the objective. A fixed
# absolute tolerance is dimensionally wrong here: on the live pool the alert
# objective spans sd ~2.38 across genomes (60 random genomes: min 2.21, max 8.54,
# sd 2.377), so 0.25 is ~0.1 sd - below the population's own spread, so it cannot
# separate a trade-off from noise. Worse, it made the gate UNREACHABLE: 34
# consecutive cycles cleared the margin and SPRT said 'promote' on 33 of them,
# yet 0 promoted, because production alert (8.3712) sits +1.04 sd up while every
# candidate sat ~0.6 lower. A 7% dip is a trade, not a gutted objective. The
# rejection also looked like normal operation in the log, so the repetition is
# now measured and escalated too (see _pareto_blocked_streak).
PARETO_ALERT_TOL = 0.25      # absolute floor (matters only for a tiny objective)
PARETO_ALERT_TOL_PCT = 0.15  # ...and at most this fraction of production alert


def _pareto_refine(evo, info_pool, alert_pool):
    """NSGA-II refinement over (info, alert) objectives; returns front + knee.

    Extracted from main() so it can run BEFORE the promotion decision. The
    engine's Pareto machinery (non-dominated sort + crowding distance) is used
    as-is; objectives are negated because the engine minimizes. The knee is the
    front member with the max normalized objective sum - the balanced pick.
    """
    try:
        res = evo.optimize_multi_objective(
            [lambda g: -fitness_skill(g.genes, 1, info_pool, alert_pool),
             lambda g: -fitness_skill(g.genes, 2, info_pool, alert_pool)],
            generations=MULTI_GENS)
        front = res.get('pareto_front', []) or []
        if not front:
            return None
        obj = [f['objectives'] for f in front]
        lo = [min(o[i] for o in obj) for i in range(2)]
        hi = [max(o[i] for o in obj) for i in range(2)]
        rng = [max(1e-9, hi[i] - lo[i]) for i in range(2)]

        def _norm(o):
            return sum((o[i] - lo[i]) / rng[i] for i in range(2))

        knee = max(front, key=lambda f: _norm(f['objectives']))
        pareto = {
            'front': [{'genes': f['genes'],
                       'info': round(-f['objectives'][0], 4),
                       'alert': round(-f['objectives'][1], 4)}
                      for f in front],
            'knee': {'genes': knee['genes'],
                     'info': round(-knee['objectives'][0], 4),
                     'alert': round(-knee['objectives'][1], 4)},
            'generations': res.get('generations', MULTI_GENS),
        }
        log('multi-objective: front=%d knee=%s info=%.3f alert=%.3f' % (
            len(front),
            {k: round(v, 4) for k, v in knee['genes'].items()},
            pareto['knee']['info'], pareto['knee']['alert']))
        return pareto
    except Exception as e:
        log('multi-objective phase failed: %s' % str(e)[:160])
        return None


def _pareto_gate(cand_genes, base_genes, info_pool, alert_pool,
                 knee_genes=None, tol=PARETO_ALERT_TOL):
    """Balance gate: a promotion may not gut one objective to lift the total.

    Requires the candidate to hold the critical-alert objective within `tol` of
    production. If the champion regresses it but the NSGA-II knee does not (and
    the knee still clears the margin over baseline), the knee is returned as the
    promotion candidate instead. Fail-closed on any error.

    Returns a report dict: passed / source / the compared objective pairs, and
    knee_genes when the knee is the chosen candidate.
    """
    try:
        cand = fitness_parts(cand_genes, info_pool, alert_pool)
        base = fitness_parts(base_genes, info_pool, alert_pool)
        # EV-9 (2026-09-11): scaled tolerance - see PARETO_ALERT_TOL_PCT. The
        # absolute value is a FLOOR, not the rule, so a tiny objective cannot
        # make the gate trivially passable.
        tol_eff = max(float(tol), PARETO_ALERT_TOL_PCT * float(base[2]))
        floor = round(base[2] - tol_eff, 4)
        rep = {'cand_total': cand[0], 'cand_info': cand[1], 'cand_alert': cand[2],
               'base_total': base[0], 'base_info': base[1], 'base_alert': base[2],
               'alert_floor': floor, 'alert_tol': round(tol_eff, 4),
               'source': 'champion', 'passed': bool(cand[2] >= floor)}
        if rep['passed']:
            return rep
        if isinstance(knee_genes, dict) and knee_genes:
            knee = fitness_parts(knee_genes, info_pool, alert_pool)
            if knee[2] >= floor and knee[0] > base[0] + PROMOTE_MARGIN:
                rep.update({'passed': True, 'source': 'knee',
                            'knee_genes': knee_genes, 'knee_total': knee[0],
                            'knee_info': knee[1], 'knee_alert': knee[2]})
        return rep
    except Exception as e:
        return {'passed': False, 'reason': str(e)[:120]}


def production_genes():
    """Currently-promoted genes, else ScheduleConfig built-in defaults."""
    try:
        _pf = _promoted_file()
        if not _pf:
            raise FileNotFoundError('no promoted genes file (state/ or config/)')
        with open(_pf, encoding='utf-8') as f:
            data = json.load(f)
        # Range-check against GENE_BOUNDS rather than hardcoded numbers
        # (2026-09-11): the literals here duplicated GENE_BOUNDS and would have
        # silently rejected any genome promoted onto a newly-widened bound,
        # falling back to ScheduleConfig defaults instead of loading it.
        g = {k: float(data['genes'][k])
             for k in ('learning_rate', 'epsilon', 'discount')}
        if all(GENE_BOUNDS[k][0] <= g[k] <= GENE_BOUNDS[k][1] for k in g):
            g['epsilon_decay'] = float(data['genes'].get('epsilon_decay', 0.0))
            _dlo, _dhi = GENE_BOUNDS['epsilon_decay']
            g['epsilon_decay'] = max(_dlo, min(g['epsilon_decay'], _dhi))
            return g
        log('promoted file out of range, using built-in defaults')
    except Exception:
        pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled
    from CORE.WORMS.expansion.scheduling import ScheduleConfig
    c = ScheduleConfig()
    return {'learning_rate': float(c.learning_rate),
            'epsilon': float(c.epsilon),
            'discount': float(c.discount_factor),
            'epsilon_decay': float(getattr(c, 'epsilon_decay', 0.0))}


def _builtin_default_genes():
    """ScheduleConfig built-ins — the landscape probe for canary checks."""
    from CORE.WORMS.expansion.scheduling import ScheduleConfig
    c = ScheduleConfig()
    return {'learning_rate': float(c.learning_rate),
            'epsilon': float(c.epsilon),
            'discount': float(c.discount_factor),
            'epsilon_decay': float(getattr(c, 'epsilon_decay', 0.0))}


# ── PROTEUS Phase C: counterfactual-era oracle gate ─────────────────────
# ON BY DEFAULT and binding when a model evaluates. See PROTEUS_DEEP_EXPANSION.md
# section 2.2.
#
# Why it exists (E9). On the 2026-09-11 batches all 50 cycles beat the FROZEN
# SNAPSHOT pool by +2.91..+3.39 and SPRT promoted 50/50, yet of the 4 cycles
# that reached the fresh holdout ALL 4 failed. A +3.3 margin on a reused pool
# bought zero confirmed improvement, because the search had overfit the pool.
#
# The oracle asks a strictly harder question: is the genome predicted to clear
# `baseline + PROMOTE_MARGIN` across a DISTRIBUTION of imagined eras, not on one
# draw? It is falsifiable against criterion 1 of the ANIMA plan (holdout pass
# rate, currently 0/4).
#
# Fail-open by design: every skip path (disabled flag, no model file, model
# without eras, gene-count mismatch, query error) returns ok=True WITH a
# stated reason, and the promotion condition binds on ok — so the gate can
# only block when it actually evaluated. An advisory organ that can silently
# block promotion would be worse than not having it.
PROTEUS_ORACLE = str(os.environ.get('AZAZAEL_PROTEUS_ORACLE', '1')).strip().lower() not in (
    '0', '', 'false', 'no', 'off')
PROTEUS_ORACLE_MIN_P = float(os.environ.get('AZAZAEL_PROTEUS_MIN_P', '0.75'))
PROTEUS_ORACLE_TRAIN = str(os.environ.get('AZAZAEL_PROTEUS_TRAIN', '1')).strip().lower() not in (
    '0', '', 'false', 'no', 'off')
PROTEUS_ORACLE_TRAIN_MIN = float(os.environ.get('AZAZAEL_PROTEUS_TRAIN_MIN', '30'))
ORACLE_JSON = os.path.join(os.path.dirname(PROMOTE_JSON), 'fitness_oracle.json')
ORACLE_LOG = os.path.join(os.path.dirname(PROMOTE_JSON), 'fitness_oracle.last.json')

# --- PROTEUS Phase D/E flags (2026-09-12) -----------------------------------
# Wired but OFF by default. Each flag moves exactly ONE variable, per the
# plan's constraint 5: Morph changes the genome, Circulation changes which
# operator is funded. Enabling both in one batch reproduces precisely the
# confounding the earlier ordering avoided.
_OFF = ('0', '', 'false', 'no', 'off')


def _flag(name, default='0'):
    return str(os.environ.get(name, default)).strip().lower() not in _OFF


PROTEUS_CRANIUM = _flag('AZAZAEL_PROTEUS_CRANIUM')
PROTEUS_CIRCULATION = _flag('AZAZAEL_PROTEUS_CIRCULATION')
PROTEUS_MORPH = _flag('AZAZAEL_PROTEUS_MORPH')
CIRCULATION_JSON = os.path.join(os.path.dirname(PROMOTE_JSON),
                                'proteus_circulation.json')
MORPH_HISTORY = os.path.join(os.path.dirname(PROMOTE_JSON),
                             'proteus_morph.jsonl')

# --- Tensor genome: the deliverable (2026-09-12) -----------------------------
# Genes become typed tensors that carry their own measured usefulness, so the
# genome can be PRUNED and COMPILED into a model rather than only searched.
#
# Unlike the Oracle, this needs no GIL interpreter: it is pure Python, so it
# runs identically under 3.15t and the 3.15 GIL sidecar. It is refreshed every cycle
# because it costs O(rows) and reuses the same history feed the oracle uses.
#
# Advisory and fail-open by construction: a genome refresh must never be able to
# abort a promotion cycle.
TENSOR_GENOME_JSON = os.path.join(os.path.dirname(PROMOTE_JSON),
                                  'tensor_genome.json')
TENSOR_GENOME_SPEC = os.path.join(os.path.dirname(PROMOTE_JSON),
                                  'tensor_genome.spec.json')
TENSOR_GENOME_LOG = os.path.join(os.path.dirname(PROMOTE_JSON),
                                 'tensor_genome.last.json')
PROTEUS_TENSOR = _flag('AZAZAEL_TENSOR_GENOME', '1')
TENSOR_HISTORY_ROWS = int(os.environ.get('AZAZAEL_TENSOR_ROWS', '600'))
# The cycle runs while other writers (the live monitor's recorder, a concurrent
# scheduled cycle) hold SQLite locks. The refresh is advisory, so a transient
# lock must not be reported as a failure — it is retried with a short backoff.
TENSOR_READ_RETRIES = int(os.environ.get('AZAZAEL_TENSOR_RETRIES', '3'))


def _query_history_with_retry(limit):
    """Read evolution_result rows, tolerating transient SQLite locks.

    Returns (rows, error). A locked database is a normal, expected condition
    here, not a defect: the store is shared with the live monitor and with any
    scheduled cycle. Retrying is cheaper than serialising every reader.
    """
    from CORE.local_memory import get_memory_store

    last = None
    for attempt in range(max(1, TENSOR_READ_RETRIES)):
        try:
            rows = get_memory_store().query_memories(
                event_type='evolution_result', limit=int(limit)) or []
            return rows, None
        except Exception as e:
            last = e
            msg = str(e).lower()
            if 'locked' not in msg and 'busy' not in msg:
                return None, e
            time.sleep(0.4 * (attempt + 1))
    return None, last

# Genes that are not free to be pruned because a downstream consumer requires
# them by name (the trainer kwargs and the production default writer both read
# exactly these keys). Pruning one would break a contract, not a model.
TENSOR_KEEP = tuple(PROMOTE_KEYS)


def _tensor_genome_refresh():
    """Rebuild the self-describing genome from real history. Advisory.

    Returns a summary dict. Never raises: any failure yields `ok=False` with a
    reason, because the caller is a promotion cycle that must not be aborted by
    an artifact write.
    """
    if not PROTEUS_TENSOR:
        return {'ok': False, 'skipped': 'disabled (AZAZAEL_TENSOR_GENOME=0)'}
    try:
        from CORE.WORMS.expansion.tensorgene import TensorGenome

        rows, err = _query_history_with_retry(TENSOR_HISTORY_ROWS)
        if rows is None:
            return {'ok': False,
                    'skipped': 'history read failed: %s' % str(err)[:140]}
        if not rows:
            return {'ok': False, 'skipped': 'no evolution_result rows'}

        # The morph genes are read from MORPH_KNOWN_GENES, the registry of
        # every gene the organ has trialled (the ONE source of truth for their
        # names and kinds). Hardcoding "morph_switch"
        # here typed the real gene (`use_schema_features`) as a continuous
        # `float` with `unknown` provenance -- the rotation holds two
        # candidates and neither is named morph_switch, so the declaration was
        # wrong for every row that ever carried one.
        #
        # A morph gene is present only on the cycles that trialled it. Its
        # absence elsewhere is exactly why `correlate` skips rather than
        # imputes: those rows are not observations of that gene.
        kind_map = dict((k, 'float') for k in PROMOTE_KEYS)
        provenance_map = dict((k, 'core') for k in PROMOTE_KEYS)
        bounds_map = dict(GENE_BOUNDS)
        try:
            # MORPH_KNOWN_GENES, not MORPH_CANDIDATES: rows written while
            # `use_schema_features` was still trialled carry it, and typing it
            # wrong (continuous float / unknown provenance) is exactly the
            # defect this map exists to prevent. The registry covers retired
            # genes; the rotation does not.
            from CORE.WORMS.expansion.proteus_hooks import MORPH_KNOWN_GENES
            for _name, _kind, _lo, _hi, _kw in MORPH_KNOWN_GENES:
                kind_map[_name] = _kind
                provenance_map[_name] = 'morph'
                try:
                    bounds_map[str(_name)] = (float(_lo), float(_hi))
                except Exception:
                    pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled
        except Exception as e:
            log('tensor genome: MORPH_CANDIDATES unavailable (%s)' % str(e)[:80])

        genome = TensorGenome().declare(
            rows, kind_map=kind_map, bounds_map=bounds_map,
            provenance_map=provenance_map)
        if not genome.genes:
            return {'ok': False, 'skipped': 'history carried no gene mappings'}

        summary = genome.summary()
        genome.save_json(TENSOR_GENOME_JSON)

        # `compile_spec` already carries `below_floor` (candidates, non-
        # destructively). Pruning for real is deliberately NOT done here: the
        # search still uses these genes, and a spec that removes them would
        # desynchronise the artifact from the genome it describes.
        spec = genome.compile_spec()
        with open(TENSOR_GENOME_SPEC, 'w', encoding='utf-8') as fh:
            json.dump(spec, fh, indent=2, default=str)

        out = dict(summary)
        out['ok'] = True
        out['rows'] = len(rows)
        out['keep'] = list(TENSOR_KEEP)
        out['genes'] = [genome.genes[n].to_dict() for n in genome.order()]
        with open(TENSOR_GENOME_LOG, 'w', encoding='utf-8') as fh:
            json.dump(out, fh, indent=2, default=str)

        log('tensor genome: %d genes width %d identifiable %d/%d most=%s' % (
            summary['n_genes'], summary['width'], summary['identifiable'],
            summary['n_genes'], summary['most_useful']))
        return out
    except Exception as e:
        log('tensor genome refresh failed: %s' % str(e)[:140])
        return {'ok': False, 'skipped': 'error: %s' % str(e)[:140]}


def _morph_cycle_index():
    """Cycle counter for the Morph round-robin.

    Read from the artifact rather than held in a new state file, so the
    rotation survives a restart AND stays in lock-step with what was actually
    trialled. Deriving it from wall-clock would desynchronise the accounting.
    """
    try:
        with open(MORPH_HISTORY, encoding='utf-8') as fh:
            return sum(1 for line in fh if line.strip())
    except Exception:
        return 0  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer
# Training needs torch. The production runner executes every cycle under the
# free-threaded 3.15t build (py315t), where the torch wheel cannot load, so
# training runs in the 3.15 GIL sidecar as a subprocess and only JSON crosses back.
PY_GIL = os.environ.get(
    'AZAZAEL_PY_GIL',
    r'C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe')
TRAIN_ORACLE_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'train_fitness_oracle.py')


def _oracle_model_stale():
    """True when the JSON model is missing or older than the retrain window.

    The window exists so a batch trains at most once: a model that changed
    mid-batch would change the treatment between cycles and make the run
    uninterpretable.
    """
    if not os.path.exists(ORACLE_JSON):
        return True
    try:
        age_min = (time.time() - os.path.getmtime(ORACLE_JSON)) / 60.0
    except Exception:
        return False  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer
    return age_min > PROTEUS_ORACLE_TRAIN_MIN


def _train_oracle_subprocess():
    """Ask the GIL interpreter to (re)train and export the JSON model."""
    try:
        import subprocess
        p = subprocess.run([PY_GIL, TRAIN_ORACLE_SCRIPT], capture_output=True,
                           text=True, timeout=900, cwd=os.path.dirname(
                               os.path.dirname(os.path.abspath(__file__))), encoding="utf-8", errors="replace")
        return 'rc=%s %s' % (p.returncode, (p.stdout or '').strip()[-200:])
    except Exception as e:
        return 'train spawn failed: %s' % str(e)[:140]


def _oracle_gate(cand_genes, threshold):
    """PROTEUS Phase C pre-gate: imagined-era robustness. Advisory, fail-open.

    Returns a dict whose `ok` is permission to CONTINUE the promotion
    decision, never a promotion itself. `threshold` is an absolute fitness
    the genome must clear in each imagined era.

    Torch-free by construction: it loads a JSON model and evaluates with a
    pure-Python forward pass, so it works under the free-threaded runner.
    """
    if not PROTEUS_ORACLE:
        return {'ok': True, 'skipped': 'disabled (set AZAZAEL_PROTEUS_ORACLE=1)'}
    try:
        from CORE.WORMS.expansion.oracle import FitnessOracle

        if PROTEUS_ORACLE_TRAIN and _oracle_model_stale():
            log('oracle model stale/missing -> %s' % _train_oracle_subprocess())
        if not os.path.exists(ORACLE_JSON):
            return {'ok': True, 'skipped': 'no model at %s' % ORACLE_JSON}

        oracle = FitnessOracle.load_json(ORACLE_JSON)
        eras = oracle.meta.get('eras') or []
        if not eras:
            return {'ok': True, 'skipped': 'model carries no era descriptors'}
        if oracle.n_gene != len(PROMOTE_KEYS):
            return {'ok': True, 'skipped': 'model n_gene=%d != %d' % (
                oracle.n_gene, len(PROMOTE_KEYS))}

        vec = [float(cand_genes[k]) for k in PROMOTE_KEYS]
        dec = oracle.era_robust_decision_pure(
            vec, eras, threshold=float(threshold), min_prob=PROTEUS_ORACLE_MIN_P)
        out = dict(dec)
        out['ok'] = bool(dec.get('passed'))
        out['n_eras'] = len(eras)
        out['model_rows'] = oracle.meta.get('n_rows')
        out['trained_at'] = oracle.meta.get('trained_at')
        try:
            with open(ORACLE_LOG, 'w', encoding='utf-8') as f:
                json.dump(dict(out, at=time.strftime('%Y-%m-%dT%H:%M:%S')), f, indent=2)
        except Exception:
            pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled
        return out
    except Exception as e:
        return {'ok': True, 'skipped': 'error: %s' % str(e)[:140]}


def _holdout_gate(cand_genes, base_genes):
    """Evaluate candidate vs baseline on freshly-extracted pools.

    Returns dict with cand/base/margin/passed, or {'passed': False,
    'reason': ...} on extraction failure (fail closed — no promotion on
    an unverified holdout). Runs serially: 2 genomes x 3 salts.
    """
    try:
        from CORE.WORMS.expansion.learning import DataPipelineWorm
        fresh_info = DataPipelineWorm().extract_from_memory(
            limit=N_EPISODES * 10, exclude_sources=EXCLUDED_TRAINING_SOURCES)
        fresh_alert = DataPipelineWorm().extract_from_memory(
            event_type='db_health_alert', limit=N_EPISODES * 10,
            exclude_sources=EXCLUDED_TRAINING_SOURCES)
        if not fresh_info or not fresh_alert:
            return {'passed': False, 'reason': 'degenerate fresh pools'}
        # Prioritized sampling must apply to the fresh pools too, otherwise the
        # holdout scores a different (uniform) distribution than the gate did.
        fresh_alert = prioritize_pool(fresh_alert, seed=len(fresh_alert))
        fc = round(fitness_genes(cand_genes, fresh_info, fresh_alert), 4)
        fb = round(fitness_genes(base_genes, fresh_info, fresh_alert), 4)
        margin = round(fc - fb, 4)
        return {'passed': bool(fc > fb + PROMOTE_MARGIN), 'cand': fc,
                'base': fb, 'margin': margin,
                'fresh_info': len(fresh_info), 'fresh_alert': len(fresh_alert)}
    except Exception as e:
        return {'passed': False, 'reason': str(e)[:120]}


def _write_canary(promoted_genes, promote_fit, default_fit):
    try:
        with open(CANARY_JSON, 'w', encoding='utf-8') as f:
            json.dump({'genes': promoted_genes, 'promote_fit': promote_fit,
                       'default_fit': default_fit,
                       'written_at': time.strftime('%Y-%m-%dT%H:%M:%S')},
                      f, indent=2)
    except Exception as e:
        log('canary write failed: %s' % str(e)[:120])


def _select_hof(info_pool, alert_pool):
    """Margin-ranked hall-of-fame seed, re-evaluated on current pools.

    Returns hof_genes dict or None. Pure selection + scoring; all logging
    inside. See the promotion-pipeline canonical doc for why margin, not
    raw champion_reeval.
    """
    try:
        from CORE.local_memory import get_memory_store
        _store = get_memory_store()
        _rows = _store.query_memories(event_type='evolution_result', limit=40)
        _cands = []  # (margin, champ_reeval, genes)
        for _r in _rows:
            _d = _r.get('data', {}) or {}
            if isinstance(_d, str):
                try:
                    _d = json.loads(_d)
                except Exception:
                    continue
            if not str(_d.get('fitness', '')).startswith('v3'):
                continue
            _base = _d.get('baseline')
            _tot = _d.get('best_total')
            if not isinstance(_base, (int, float)) or not _base:
                continue
            if not isinstance(_tot, (int, float)):
                continue
            _genes = _d.get('best_genes')
            if not isinstance(_genes, dict) or not _genes:
                continue
            _cands.append((round(_tot - _base, 4), _d.get('champion_reeval'), _genes))
        _cands.sort(key=lambda c: -c[0])
        _ranked = []  # (re-eval, margin, genes), best re-eval first
        _seen_genes, _attempts = set(), 0
        for _margin, _champ, _genes in _cands:
            if _attempts >= 3:
                break
            try:
                _gkey = tuple(sorted((k, round(float(v), 6)) for k, v in _genes.items()))
            except Exception:
                continue
            if _gkey in _seen_genes:
                continue
            _seen_genes.add(_gkey)
            _attempts += 1
            try:
                _re = round(fitness_genes(_genes, info_pool, alert_pool), 4)
            except Exception as e:
                log('hof re-eval failed: %s' % str(e)[:100])
                continue
            _ranked.append((_re, _margin, _genes))
        if not _ranked:
            log('hall-of-fame: no margin-ranked candidate re-evaluated')
            return None
        _ranked.sort(key=lambda c: -c[0])
        # Novelty rotation (2026-09-11): always taking the re-eval winner
        # fed the identical champion back every cycle within an era (the
        # +1.41 x8 / +0.36 x5 margin pins) — same winner, same margin,
        # cooldown-blocked forever. Rotate the seed among the top-3 by
        # 5-min slot so consecutive cycles explore different lineage.
        # Deterministic per slot; fail-open to rank 0.
        try:
            _slot = int(time.time()) // 300 % max(1, len(_ranked))
        except Exception:
            _slot = 0
        _re, _margin, _genes = _ranked[_slot % len(_ranked)]
        log('hall-of-fame seed: rank %d/%d margin=+%s re-eval=%s genes=%s' % (
            _slot % len(_ranked), len(_ranked), _margin, _re,
            {k: round(v, 4) for k, v in _genes.items()}))
        return _genes
    except Exception as e:
        log('hall-of-fame lookup failed: %s' % str(e)[:120])
        return None


# Widened 2026-09-11 (50-run analysis, E8). The promoted genome had drifted to a
# VERTEX of this box: learning_rate = 0.5 was exactly the old upper bound,
# discount = 0.8033 sat within 0.003 of the 0.8 floor, and epsilon_decay =
# 0.0859 pressed a 0.1 ceiling. The QD archive's global best cell held the
# identical pinned genome. With three of four genes against a wall the effective
# search space was ~1-D and its optimum was already occupied, which is why every
# era re-converged to the same corner and candidate margins clustered just under
# the promotion gate instead of clearing it.
#
# learning_rate stays below 1.0 deliberately: tabular Q-learning is only
# guaranteed to converge for a step size in (0, 1], so the ceiling is 0.9 rather
# than an open bound.
GENE_BOUNDS = {
    'learning_rate': (0.01, 0.9),
    'epsilon': (0.0, 0.5),
    'discount': (0.5, 0.999),
    'epsilon_decay': (0.0, 0.3),  # exploration-annealing speed (2026-09-08)
}

# --- v2.0 secondaries (2026-09-11 audit followups) ---------------------------
# CMA-ES second arm: 2 gens x ~8 evals (~11% overhead) seeded at the GA
# champion. Winner-takes-best on the SAME frozen pools, then the SAME
# SPRT -> pareto -> holdout -> canary gates. Gates never see two
# candidates; they see one winner, so no gate logic changes.
CMA_ARM_GENS = 2
# MAP-Elites behavior descriptor: (info, alert) mastery — the two objectives
# the Pareto gate already trades off. Behaviors are z-scored against the
# rolling 40-cycle median/IQR (_qd_norm_stats), so ANY operating band
# spreads across the fixed 4x4 grid on (-2, +2)^2. Raw-value grids kept
# collapsing (1-2 cells: the band is ~5 wide wherever it sits).
# Archive persists in state/ so diversity survives across cycles.
QD_BEHAVIOR_DIMS = [('info', 4, -2.0, 2.0), ('alert', 4, -2.0, 2.0)]
# --- Engram #814 QD archive admission alternatives (gated, one variable each) ---
def _env_float(name, default):
    """Env float with fail-open default (a garbage env value must never
    crash this module's import)."""
    try:
        return float(str(os.environ.get(name, default)).strip() or default)
    except (TypeError, ValueError):
        return float(default)


# Q1: Dominated Novelty Search admission rule (drop-in grid replacement)
QD_DOMINATED_NOVELTY = _flag('AZAZAEL_QD_DOMINATED_NOVELTY')
# 0.25 (Oct-19): measured min elite-pair NN distance on the live archive is
# 0.2441 across 12 elites, so 0.15 admitted everything on novelty and Q1 never
# discriminated. 0.25 sits just above the closest elite pair: a candidate must
# be at least that novel to win on novelty alone, else fitness decides.
# Override preserved via AZAZAEL_QD_NOVELTY_THRESHOLD.
QD_NOVELTY_THRESHOLD = _env_float('AZAZAEL_QD_NOVELTY_THRESHOLD', 0.25)
# Q2: CMA-MAE soft archive with learning-rate alpha
QD_SOFT_ARCHIVE = _flag('AZAZAEL_QD_SOFT_ARCHIVE')
QD_SOFT_ARCHIVE_ALPHA = _env_float('AZAZAEL_QD_SOFT_ALPHA', 0.3)
# Q3: VQ-Elites-lite cell-remap measurement (adopt only if coverage > 9/16)
QD_VQ_ELITES = _flag('AZAZAEL_QD_VQ_ELITES')
# Q4: alert-weighted selection pressure (j=3 probe). The (*,3) alert column
# stayed empty across 4 live cycles: alert z maxes at +0.82 against the +1.0
# bin edge, so selection never produces high-alert outliers. Weight > 1.0
# steers total toward alert-heavy genomes; 1.0 (default) is exactly info+alert.
QD_ALERT_WEIGHT = _env_float('AZAZAEL_QD_ALERT_WEIGHT', 1.0)


def _qd_weighted_total(info, alert, weight=None):
    """Selection total with alert pressure. Pure; weight=None reads the flag."""
    w = QD_ALERT_WEIGHT if weight is None else float(weight)
    return float(info) + w * float(alert)
# --- Engran #849 QD-ESCAPE: bounded non-operator novelty trials -------------
QD_ARCHIVE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'state', 'qd_archive.json'))
QD_ESCAPE = _flag('AZAZAEL_QD_ESCAPE')
QD_ESCAPE_MAX_TRIALS = 3
QD_ESCAPE_JSONL = os.path.join(os.path.dirname(QD_ARCHIVE_PATH),
                               'bake_kilo_qdescape.jsonl')
QD_ESCAPE_BASELINE_QD_SCORE = 507.82
QD_ESCAPE_BASELINE_FILLED = 9
# TPE seeds per cycle (evaluated inside the GA population, zero extra evals).
TPE_SUGGEST_N = 2
TPE_HISTORY_LIMIT = 40


def _load_qd_archive():
    """Load persisted QD archive cells; {} on fresh start / corrupt file."""
    try:
        if os.path.exists(QD_ARCHIVE_PATH):
            with open(QD_ARCHIVE_PATH, encoding='utf-8') as f:
                data = json.load(f)
            cells = data.get('cells', {}) if isinstance(data, dict) else {}
            return cells if isinstance(cells, dict) else {}
    except Exception as e:
        log('qd archive load failed: %s' % str(e)[:100])
    return {}


def _save_qd_archive(cells):
    try:
        os.makedirs(os.path.dirname(QD_ARCHIVE_PATH), exist_ok=True)
        tmp = QD_ARCHIVE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'cells': cells,
                       'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                       'dims': QD_BEHAVIOR_DIMS}, f, indent=2)
        os.replace(tmp, QD_ARCHIVE_PATH)
    except Exception as e:
        log('qd archive save failed: %s' % str(e)[:100])


def _qd_cell_of(behavior):
    """Grid cell for an (info, alert) behavior tuple."""
    cell = []
    for (_n, bins, lo, hi), b in zip(QD_BEHAVIOR_DIMS, behavior):
        b = max(lo, min(hi, float(b)))
        idx = min(bins - 1, int((b - lo) / ((hi - lo) or 1.0) * bins))
        cell.append(idx)
    return tuple(cell)


def _qd_norm_stats():
    """Rolling median/IQR of (info, alert) over the last 40 artifacts.

    Fail-open defaults bracket the historical band. Powers the z-scored
    behavior descriptor so the grid illuminates regardless of scale.
    """
    try:
        from CORE.local_memory import get_memory_store
        rows = get_memory_store().query_memories(
            event_type="evolution_result", limit=40)
        infos, alerts = [], []
        for r in rows or []:
            d = (r.get("data", {}) or {})
            if isinstance(d, str):
                try:
                    d = json.loads(d)
                except Exception:
                    continue
            if isinstance(d.get("best_info"), (int, float)):
                infos.append(float(d["best_info"]))
            if isinstance(d.get("best_alert"), (int, float)):
                alerts.append(float(d["best_alert"]))
        out = {}
        for name, vals, fb in (("info", infos, (15.0, 2.0)),
                               ("alert", alerts, (9.0, 2.0))):
            if len(vals) >= 5:
                vals = sorted(vals)
                med = vals[len(vals) // 2]
                q1, q3 = vals[len(vals) // 4], vals[3 * len(vals) // 4]
                out[name] = (med, max(0.5, q3 - q1))
            else:
                out[name] = fb
        return out
    except Exception:
        return {"info": (15.0, 2.0), "alert": (9.0, 2.0)}


def _qd_autocenter(cells):
    """Recenter the QD grid on the archive's observed behavior median.

    A hand-set grid rots when the operating band drifts (the (-10,40)
    collapse measured 2026-09-11: 100 cycles, 2/16 cells). When the
    median behavior sits more than one cell-width outside the grid
    center, re-bracket: width = clamp(2x observed spread, 6, 30) around
    the median, then reinsert every elite under the new dims. Returns
    the remapped cells. Mutates the global QD_BEHAVIOR_DIMS.
    """
    global QD_BEHAVIOR_DIMS
    try:
        beh = [tuple(c.get("behavior", ())) for c in cells.values()
               if isinstance(c, dict) and len(c.get("behavior", ())) >= 2]
        if len(beh) < 3:
            return cells
        infos = sorted(float(b[0]) for b in beh)
        alerts = sorted(float(b[1]) for b in beh)
        med_i = infos[len(infos) // 2]
        med_a = alerts[len(alerts) // 2]
        spr_i = max(1.0, infos[-1] - infos[0])
        spr_a = max(1.0, alerts[-1] - alerts[0])
        new_dims = []
        moved = False
        for (_n, bins, lo, hi), med, spr in (
                (QD_BEHAVIOR_DIMS[0], med_i, spr_i),
                (QD_BEHAVIOR_DIMS[1], med_a, spr_a)):
            width = max(6.0, min(30.0, spr * 2.0))
            nlo, nhi = med - width / 2.0, med + width / 2.0
            center = (lo + hi) / 2.0
            if abs(med - center) > (hi - lo) / bins:
                moved = True
            new_dims.append((_n, bins, round(nlo, 3), round(nhi, 3)))
        if not moved:
            return cells
        QD_BEHAVIOR_DIMS = new_dims
        fresh = {}
        for c in cells.values():
            if not isinstance(c, dict):
                continue
            try:
                _t, _i, _a = float(c["fitness"]), float(c["behavior"][0]), float(c["behavior"][1])
            except Exception:
                continue
            key = "%d,%d" % _qd_cell_of((_i, _a))
            cur = fresh.get(key)
            if cur is None or _t > float(cur.get("fitness", float("-inf"))):
                fresh[key] = {"genes": dict(c.get("genes", {})),
                              "fitness": _t, "behavior": [_i, _a]}
        log("qd autocenter: dims=%s cells %d->%d" % (QD_BEHAVIOR_DIMS, len(cells), len(fresh)))
        return fresh
    except Exception as e:  # noqa: BLE001 — fail-open, keep old grid
        log("qd autocenter failed: %s" % str(e)[:100])
        return cells


def _tpe_seeds(n=TPE_SUGGEST_N):
    """Suggest seed genomes from TPE over past artifact bests.

    History = last TPE_HISTORY_LIMIT evolution_result rows' (best_genes,
    best_total). Zero extra evals: suggestions join the GA population as
    seeds. Returns [] on fresh start / any error (fail-open: GA unaffected).
    """
    try:
        from CORE.local_memory import get_memory_store
        from CORE.WORMS.expansion.evolution import TPESurrogate
        rows = get_memory_store().query_memories(
            event_type='evolution_result', limit=TPE_HISTORY_LIMIT)
        tpe = TPESurrogate({k: tuple(v) for k, v in GENE_BOUNDS.items()})
        seen = 0
        for r in rows or []:
            d = (r.get('data', {}) or {})
            if isinstance(d, str):
                try:
                    d = json.loads(d)
                except Exception:
                    continue
            g, t = d.get('best_genes'), d.get('best_total')
            if isinstance(g, dict) and isinstance(t, (int, float)) and g:
                try:
                    tpe.observe({k: float(g[k]) for k in GENE_BOUNDS if k in g}, float(t))
                    seen += 1
                except Exception:
                    continue
        if seen < 8:
            return []
        # Richer history (2026-09-11): observe winner + knee + seed per
        # cycle instead of winners only — 3x density, zero extra evals.
        # (Winner observes happened above; add knee and seed here.)
        try:
            for r in rows or []:
                d = (r.get("data", {}) or {})
                if isinstance(d, str):
                    try:
                        d = json.loads(d)
                    except Exception:
                        continue
                _knee = (d.get("pareto") or {}).get("knee") or {}
                _kg, _ki, _ka = _knee.get("genes"), _knee.get("info"), _knee.get("alert")
                if isinstance(_kg, dict) and isinstance(_ki, (int, float)) \
                        and isinstance(_ka, (int, float)):
                    try:
                        tpe.observe({k: float(_kg[k]) for k in GENE_BOUNDS if k in _kg},
                                    float(_ki) + float(_ka))
                    except Exception:
                        pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled
                _sg = d.get("seed_genes")
                _cr = d.get("champion_reeval")
                if isinstance(_sg, dict) and isinstance(_cr, (int, float)):
                    try:
                        tpe.observe({k: float(_sg[k]) for k in GENE_BOUNDS if k in _sg},
                                    float(_cr))
                    except Exception:
                        pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled
        except Exception as e:
            log("tpe enrich failed: %s" % str(e)[:100])
        out = []
        for _ in range(n):
            try:
                out.append(tpe.suggest())
            except Exception:
                break
        return [g for g in out if isinstance(g, dict) and g]
    except Exception as e:
        log('tpe seeds failed: %s' % str(e)[:100])
        return []


def _qd_suggest(cells):
    """One diverse seed from the persisted QD archive (random elite +
    Gaussian perturb). None when the archive is empty."""
    try:
        if not cells:
            return None
        import random as _r
        key = _r.choice(list(cells.keys()))
        elite = (cells[key] or {}).get('genes', {})
        if not isinstance(elite, dict) or not elite:
            return None
        out = {}
        for k, v in elite.items():
            if k in GENE_BOUNDS and isinstance(v, (int, float)) and not isinstance(v, bool):
                lo, hi = GENE_BOUNDS[k]
                out[k] = max(lo, min(hi, _r.gauss(float(v), 0.1 * (hi - lo))))
            else:
                out[k] = v
        return out if out else None
    except Exception as e:
        log('qd suggest failed: %s' % str(e)[:100])
        return None


# --- Engran #814 Q1: Dominated Novelty Search admission helper ---------------
# Novelty = min Euclidean distance in normalized behavior space to any
# existing archive elite. A candidate is novel if it clears the threshold,
# which lets the archive fill behavior regions that pure fitness admission
# would leave empty (the Q1 hypothesis).
def _qd_novelty_of(cells, behavior):
    """Min distance from `behavior` to any elite's behavior (norm space)."""
    try:
        beh = [c.get('behavior') for c in cells.values()
               if isinstance(c, dict) and isinstance(c.get('behavior'), (list, tuple))
               and len(c['behavior']) >= 2]
        if not beh:
            return float('inf')
        bx, by = float(behavior[0]), float(behavior[1])
        best = None
        for b in beh:
            d = math.hypot(bx - float(b[0]), by - float(b[1]))
            if best is None or d < best:
                best = d
        return best
    except Exception:
        return float('inf')


# --- Engran #814 Q3: VQ-Elites-lite cell-remap measurement -------------------
# Measurement-first: estimate coverage if behavior dims were learned instead
# of hand-set. Logs the number; adoption (replacing QD_BEHAVIOR_DIMS) happens
# ONLY when QD_VQ_ELITES is on AND measured coverage beats 9/16 same-era.
def _qd_vq_elites_report(cells):
    """Project behaviors onto a learned 2D subspace and count filled cells.

    Uses PCA-like first two principal components of the archive's behavior
    cloud as learned dims, then re-bins into a 4x4 grid and reports filled
    cells. Pure measurement: no archive mutation unless the caller acts on it.
    """
    try:
        beh = [c.get('behavior') for c in cells.values()
               if isinstance(c, dict) and isinstance(c.get('behavior'), (list, tuple))
               and len(c['behavior']) >= 2]
        if len(beh) < 3:
            return 0.0, len(cells)
        xs = sorted(float(b[0]) for b in beh)
        ys = sorted(float(b[1]) for b in beh)
        # centroid
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        # variance along each axis (diagonal of covariance, i.e. PCA axes
        # when the two dims are uncorrelated -- good enough for a lite report)
        vx = sum((x - cx) ** 2 for x in xs) / len(xs)
        vy = sum((y - cy) ** 2 for y in ys) / len(ys)
        # grid width = 2 * std dev around centroid, 4 bins each axis
        wx = max(1.0, math.sqrt(vx) * 2.0)
        wy = max(1.0, math.sqrt(vy) * 2.0)
        filled = set()
        for b in beh:
            xi = min(3, int(max(0.0, (float(b[0]) - (cx - wx / 2.0)) / wx * 4)))
            yi = min(3, int(max(0.0, (float(b[1]) - (cy - wy / 2.0)) / wy * 4)))
            filled.add((xi, yi))
        coverage = len(filled) / 16.0
        return coverage, len(filled)
    except Exception as e:
        log('qd vq-elites report failed: %s' % str(e)[:100])
        return 0.0, len(cells)


def _qd_admit_cell(cells, key, genes, t_fit, behavior, raw):
    """QD archive admission decision + write (Engram #814 Q1/Q2 wiring).

    Returns True when admitted. Baseline (all flags off): admit iff the
    cell is empty or the candidate beats the occupant — identical to the
    pre-#814 rule. Q1 adds novelty admission (novelty OR fitness). Q2
    learns a per-cell acceptance threshold while elite fitness stays
    pure; the threshold belongs to the cell and survives replacement.
    """
    cur = cells.get(key)
    if not isinstance(cur, dict):
        cells[key] = {'genes': dict(genes), 'fitness': t_fit,
                      'behavior': [round(behavior[0], 3),
                                   round(behavior[1], 3)],
                      'raw': [round(raw[0], 3), round(raw[1], 3)]}
        return True
    cur_fit = float(cur.get('fitness', float('-inf')))
    threshold = None
    if QD_DOMINATED_NOVELTY:
        # Q1: Dominated Novelty Search — admit on novelty OR fitness.
        novelty = _qd_novelty_of(cells, behavior)
        admit = (novelty > QD_NOVELTY_THRESHOLD) or (t_fit > cur_fit)
    elif QD_SOFT_ARCHIVE and QD_SOFT_ARCHIVE_ALPHA > 0.0:
        # Q2: CMA-MAE soft archive — admit against the learned threshold.
        thr = float(cur.get('threshold', cur_fit))
        blend = ((1.0 - QD_SOFT_ARCHIVE_ALPHA) * thr
                 + QD_SOFT_ARCHIVE_ALPHA * t_fit)
        threshold = round(blend, 4)
        admit = t_fit > thr
    else:
        admit = t_fit > cur_fit
    if admit:
        cells[key] = {'genes': dict(genes), 'fitness': t_fit,
                      'behavior': [round(behavior[0], 3),
                                   round(behavior[1], 3)],
                      'raw': [round(raw[0], 3), round(raw[1], 3)]}
        if threshold is not None:
            cells[key]['threshold'] = threshold
    elif threshold is not None:
        cells[key]['threshold'] = threshold
    return admit


def _second_arm_name():
    """Alternate the second arm by 5-min slot: even slots sep-CMA-ES, odd
    slots Differential Evolution. Deterministic per slot; gives both
    secondaries live duel data against the GA champion."""
    try:
        return "de" if (int(time.time()) // 300) % 2 else "cma"
    except Exception:
        return "cma"  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer


def _run_cma_arm(start_genes, fitness_frozen, workers=8, force_arm=None,
                 gene_bounds=None):
    """Second search arm: sep-CMA-ES or DE (alternating by slot), seeded at
    the GA champion.

    Budget: ~2 gens x ~8 evals either way. Returns (genes, total) or
    (None, None) on any error (fail-open: GA champion stands). The report
    carries 'arm' (cma/de); 'winner' keeps cma/ga semantics (secondary/GA)
    so existing monitor/funnel parsing is untouched.
    """
    # force_arm lets the Phase D circulation allocator fund a SPECIFIC arm.
    # Without it the internal slot alternation would override the posterior's
    # choice, and the feedback loop would credit the wrong operator.
    arm = str(force_arm) if force_arm in ('cma', 'de') else _second_arm_name()
    # `gene_bounds` lets Phase E hand the arm the SAME genome the GA searched.
    # Without it the arm is built from the 4-key GENE_BOUNDS, so when its
    # candidate wins the extra gene is simply absent from the winner and the
    # recorded dimension drops back to 4 (measured: cycle 8, dimension 4,
    # extra {}).
    _bounds = gene_bounds or GENE_BOUNDS
    try:
        if arm == "de":
            from CORE.WORMS.expansion.evolution import DifferentialEvolution
            opt = DifferentialEvolution(
                {k: tuple(v) for k, v in _bounds.items()},
                popsize=10, seed_genes=start_genes)
            res = opt.optimize(lambda g: float(fitness_frozen(
                type('G', (), {'genes': g})())), generations=2)
            res["_arm"] = "de"
            return res.get("best_genes"), res.get("best_fitness")
        from CORE.WORMS.expansion.evolution import SepCMAES
        cma = SepCMAES({k: tuple(v) for k, v in _bounds.items()},
                       seed_genes=start_genes)
        res = cma.optimize(lambda g: float(fitness_frozen(
            type('G', (), {'genes': g})())), generations=CMA_ARM_GENS,
            workers=workers)
        genes = res.get('best_genes')
        if not isinstance(genes, dict) or not genes:
            return None, None
        return genes, res.get('best_fitness')
    except Exception as e:
        log('cma arm failed: %s' % str(e)[:120])
        return None, None


def _check_canary(info_pool, alert_pool, pool_reused):
    """One-shot post-promotion watch. Returns a report dict for the artifact.

    Deferred while pools are shared (same data the promotion was scored
    on — re-eval would be circular). Fires once on the first run with
    fresh pools, then deletes the canary file.
    """
    try:
        with open(CANARY_JSON, encoding='utf-8') as f:
            can = json.load(f)
    except Exception:
        return {}
    if not info_pool or not alert_pool:
        return {'status': 'deferred-degenerate-pools'}
    if pool_reused:
        return {'status': 'deferred-shared-pools'}
    report = {'status': 'unknown'}
    try:
        if _promoted_file() is None:
            report = {'status': 'cleared-manual-rollback'}
            log('canary cleared: production file removed manually')
        else:
            prod = production_genes()
            dflt = _builtin_default_genes()
            prod_now = round(fitness_genes(prod, info_pool, alert_pool), 4)
            dflt_now = round(fitness_genes(dflt, info_pool, alert_pool), 4)
            landscape_shift = abs(dflt_now - float(can.get('default_fit', dflt_now)))
            regress = float(can.get('promote_fit', prod_now)) - prod_now
            report = {'status': 'pass', 'prod_now': prod_now,
                      'default_now': dflt_now,
                      'landscape_shift': round(landscape_shift, 4),
                      'regress': round(regress, 4)}
            if landscape_shift > LANDSCAPE_TOL:
                report['status'] = 'inconclusive-landscape-moved'
                log('canary inconclusive: landscape moved (default shift %.3f)' % landscape_shift)
            elif regress > CANARY_TOL:
                # Depth-2 backup (followup #5): restore .prev, falling back
                # to .prev2 — a double-bad-promotion can no longer clobber
                # the last known-good genome.
                _restored_from = None
                for _cand_prev in (PREV_JSON, PREV2_JSON):
                    if os.path.exists(_cand_prev):
                        _restored_from = _cand_prev
                        break
                if _restored_from:
                    with open(_restored_from, encoding='utf-8') as f:
                        prev = json.load(f)
                    with open(PROMOTE_JSON + '.canary-failed.json', 'w', encoding='utf-8') as f:
                        json.dump({'genes': prod,
                                   'failed_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                                   'regress': round(regress, 4)}, f, indent=2)
                    with open(PROMOTE_JSON, 'w', encoding='utf-8') as f:
                        json.dump(prev, f, indent=2)
                    report['status'] = 'ROLLED-BACK'
                    report['restored_from'] = os.path.basename(_restored_from)
                    log('canary FAILED: production regressed %.3f — restored %s' % (
                        regress, _restored_from))
                else:
                    report['status'] = 'regressed-no-prev'
                    log('canary FAILED: regressed %.3f but no .prev/.prev2 to restore' % regress)
            else:
                log('canary PASS: production holding (regress %.3f)' % regress)
    except Exception as e:
        report = {'status': 'error', 'reason': str(e)[:120]}
        log('canary check failed: %s' % str(e)[:120])
    try:
        os.remove(CANARY_JSON)
    except Exception:
        pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled
    return report


# --- Prioritized alert-pool sampling (followup #4, 2026-09-11) --------------
# WHY: the alert pool is a hard-capped 500-row sliding window
# (`query_memories` does min(limit, 500)) sampled UNIFORMLY by the RL loop.
# So when the system is healthy and no new alerts arrive, the objective keeps
# re-training on the same frozen rows - the starvation the audit found. Two
# structural facts make the old metric blind to it: hard_count SATURATES at 500
# (it can never reveal starvation) and uniform sampling gives a 3-day-old row
# the same weight as one from a minute ago.
#
# WHAT THIS IS: the SAMPLING half of prioritized experience replay -
# proportional resampling by an intrinsic priority p_i^alpha. The
# importance-sampling CORRECTION half is deliberately NOT implemented, because
# RLTrainLoop.train_from_memory() accepts no per-sample weights; claiming full
# PER here would be false. Priority is intrinsic (severity x recency), not
# TD-error driven, so it needs no policy feedback and stays a pure function.
PRIORITY_ALPHA = 0.6            # exponent on the priority (1.0 = fully greedy)
PRIORITY_HALFLIFE_H = 24.0      # recency half-life for aging rows
PRIORITY_RECENCY_FLOOR = 0.15   # old rows fade but never vanish entirely
SEVERITY_WEIGHT = {'critical': 4.0, 'error': 2.5, 'warning': 1.5, 'info': 1.0}


def _now_epoch():
    return datetime.now(timezone.utc).timestamp()


def _age_hours(timestamp, now=None):
    """Age of an ISO timestamp in hours, or None when unparseable."""
    if not timestamp:
        return None
    try:
        ts = str(timestamp).replace('Z', '+00:00')
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return ((now if now is not None else _now_epoch())
                - dt.timestamp()) / 3600.0
    except Exception:
        return None  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer


def pool_priority(record, now=None):
    """Intrinsic priority of one alert row: severity weight x recency decay.

    No policy feedback, so this is deterministic and testable. Recency uses an
    exponential half-life and is floored, so an old-but-critical row still gets
    sampled rather than ageing out of the distribution entirely.
    """
    rec = record or {}
    sev = str(rec.get('severity', 'info')).lower()
    weight = float(SEVERITY_WEIGHT.get(sev, 1.0))
    age = _age_hours(rec.get('timestamp'), now)
    if age is not None:
        decay = 0.5 ** (max(0.0, float(age)) / PRIORITY_HALFLIFE_H)
        weight *= max(PRIORITY_RECENCY_FLOOR, decay)
    return max(weight, 1e-6)


def prioritize_pool(records, size=None, alpha=PRIORITY_ALPHA, now=None, seed=0):
    """Proportional resampling of a pool by priority (deterministic given seed).

    Returns a NEW list, same length by default, drawn with replacement so high
    priority rows appear more often. Both zero-mean and all-equal priorities
    degrade gracefully to an unweighted draw, so this can never empty a pool.
    """
    if not records:
        return list(records or [])
    n = int(size) if size else len(records)
    now_ts = now if now is not None else _now_epoch()
    try:
        prios = [max(float(pool_priority(r, now_ts)), 1e-9) ** float(alpha)
                 for r in records]
    except Exception:
        return list(records)
    total = sum(prios)
    if not (total > 0.0):
        return list(records)
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        target = rng.random() * total
        acc = 0.0
        pick = records[-1]
        for i, p in enumerate(prios):
            acc += p
            if target <= acc:
                pick = records[i]
                break
        out.append(pick)
    return out


POOL_SNAPSHOT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'state', 'evolution_pool_snapshot.json'))
POOL_MAX_AGE_S = 1800  # 30 min: consecutive 5-min cycles share one pool


def _blend_intake_pool(base_pool):
    """INTAKE-wire (Engram #798): append technique-outcome records to the
    info pool, fail-open.

    The QD/GA loop scored genomes on DataPipelineWorm pools only, while the
    technique-outcome intake (`build_training_records()`) fed a different
    trainer run — same trainer class, disjoint record pools (probe
    `tools/probe_intake_fitness_798.py`: pure-intake re-RANKS genomes vs
    the current pool, max |delta| 5.24, so the pools genuinely disagree).

    `INTAKE_POOL_BLEND` (default 0.5): intake rows appended ~= blend x
    base size. 0 disables the wire without touching code; garbage falls
    back to the default with a log line. ANY runtime failure (import,
    build, shape) returns the base pool unchanged and logs — the loop
    never breaks because the intake is unreachable. Composition is logged
    so pool-era tagging keeps meaning the base era plus a stated mix.
    """
    base = list(base_pool or [])
    try:
        raw = os.environ.get("INTAKE_POOL_BLEND", "0.5")
        blend = max(0.0, min(1.0, float(str(raw).strip() or "0.5")))
    except (TypeError, ValueError):
        log('intake blend: bad INTAKE_POOL_BLEND, using 0.5')
        blend = 0.5
    if not base or blend <= 0.0:
        log('intake blend: off (blend=%.2f base=%d)' % (blend, len(base)))
        return base
    try:
        from CORE.training_intake import build_training_records
        n_take = int(len(base) * blend)
        built = build_training_records(limit=max(n_take, 1))
        rows = built.get("records") or []
        taken = rows[:n_take]
        log('intake blend: info=%d (base=%d intake=%d blend=%.2f ok_rate=%s)'
            % (len(base) + len(taken), len(base), len(taken), blend,
               built.get("ok_rate")))
        return base + taken
    except Exception as e:
        log('intake blend failed, base pool unchanged: %s' % str(e)[:120])
        return base


def _load_or_freeze_pools():
    """Shared frozen pools across consecutive runs (2026-09-09 hardening).

    Within-run freezing (2026-09-08) made trajectories pool-consistent,
    but the production baseline still swung 31.6 -> 26.4 on identical
    genes between runs minutes apart. Reusing one snapshot while fresh
    makes streak comparisons apples-to-apples too.

    Returns (info_pool, alert_pool, reused, age_s, pool_id). pool_id is
    the snapshot's saved_at epoch (followup #7: pool-era tagging — every
    fitness number is labeled with the data era that produced it, so
    cross-era comparisons in the log/artifacts are explicit, not
    inferred). 0 = degenerate/live extraction, no era.
    Degenerate (empty) extractions are never persisted and fall back to
    live per-eval extraction. The cycle lock serializes readers/writers.
    """
    from CORE.WORMS.expansion.learning import DataPipelineWorm
    now = time.time()
    try:
        with open(POOL_SNAPSHOT_PATH, encoding='utf-8') as f:
            snap = json.load(f)
        age = now - float(snap.get('saved_at', 0))
        info_pool, alert_pool = snap.get('info'), snap.get('alert')
        if (isinstance(info_pool, list) and isinstance(alert_pool, list)
                and info_pool and alert_pool and age < POOL_MAX_AGE_S):
            log('reusing pool snapshot (age %ds): info=%d alert=%d' % (
                age, len(info_pool), len(alert_pool)))
            # Prioritized sampling (followup #4): resample the alert pool by
            # severity x recency. The snapshot itself stays RAW (so pool_id and
            # pool_fingerprint keep meaning the same data era) and the seed is
            # the era's saved_at, so every genome and every cycle within the era
            # sees the SAME prioritized draw - prioritization must not become a
            # second source of per-genome variance.
            alert_out = prioritize_pool(
                alert_pool, seed=int(float(snap.get('saved_at', 0))))
            info_out = _blend_intake_pool(info_pool)
            return info_out, alert_out, True, int(age), int(float(snap.get('saved_at', 0)))
        log('pool snapshot unusable (age %s), re-freezing' % (
            ('%ds' % age) if age >= 0 else 'missing'))
    except Exception as e:
        log('pool snapshot load failed, extracting fresh: %s' % str(e)[:100])
    try:
        info_pool = DataPipelineWorm().extract_from_memory(
            limit=N_EPISODES * 10, exclude_sources=EXCLUDED_TRAINING_SOURCES)
        # EV-6 (2026-09-30): grow the alert arm by SEVERITY, not by event_type.
        #
        # The arm was defined as `event_type='db_health_alert'`, and only 7
        # records in the store carry that label -- so the objective trained on a
        # 7-row pool whose state key is `source:severity`, and every one of
        # those rows has no `source` field. Measured: 1 distinct state, a
        # four-cell Q-table, and a 1-state BANDIT rather than a control problem.
        # No learner can demonstrate anything on that, which is why the arm
        # needed the reward table fixed before it meant anything at all.
        #
        # The DATA WAS ALREADY THERE. The store holds 123,279 memories:
        # 1,477 `error`, 156 `warning`, 8 `critical`, 121,606 `info`. The
        # high-severity records were simply never routed into the arm, because
        # "alert" was expressed as a provenance label rather than as a
        # property of the data.
        #
        # So the arm is now selected on severity -- the thing that actually
        # makes a record an alert -- which yields ~1,600 rows with genuine
        # `source` diversity, and therefore states a learner can discriminate.
        # A severity with no row in the reward table scores 0.0 for every
        # action and caps the arm at zero (that is how `critical` read 0.0 on
        # all 35 cycles before it was added), so the selection is paired with
        # a report of which severities are present.
        alert_pool = _extract_alert_pool()
        if not info_pool or not alert_pool:
            log('pool extraction degenerate (info=%d alert=%d), '
                'using live evals without persisting' % (
                    len(info_pool or []), len(alert_pool or [])))
            return None, None, False, 0, 0
        log('frozen pools: info=%d alert=%d' % (len(info_pool), len(alert_pool)))
        # The snapshot stores the RAW pool; prioritization is applied on the way
        # out so it is never double-applied on a later reuse.
        alert_out = prioritize_pool(alert_pool, seed=int(now))
        log('prioritized alert pool: %d rows resampled by severity x recency'
            % len(alert_out))
        tmp = POOL_SNAPSHOT_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'saved_at': now, 'info': info_pool,
                       'alert': alert_pool}, f, ensure_ascii=False, default=str)
        os.replace(tmp, POOL_SNAPSHOT_PATH)
        info_out = _blend_intake_pool(info_pool)
        return info_out, alert_out, False, 0, int(now)
    except Exception as e:
        log('pool freeze failed, falling back to live evals: %s' % str(e)[:120])
        return None, None, False, 0, 0


# --- Meta-evolution (2026-09-09): evolve the search itself ---------------
# A 1+1 evolution strategy over the GA's own hyperparameters. One cycle =
# one meta-sample (slow by nature: ~12/hour), so the design is a cautious
# hill-climb, not a full GA: exploit the best-known meta-params 75% of
# cycles, explore a single-param perturbation otherwise; promote the
# candidate after 3 runs if its mean beats the champion's EMA.
#
# Meta-fitness = same-pool MARGIN (best_total - baseline_fit), the honest
# signal per the 2026-09-08 audit: absolute fitness is pool-polluted,
# margin isolates search quality on identical data.
#
# Deliberately NOT evolved: population_size (wall-clock cost),
# elitism_count (stability), PROMOTE_MARGIN/WINS (safety-critical —
# letting the search relax its own promotion guardrail is textbook
# reward hacking; those stay hand-set).
META_STATE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'config', 'evolved_meta_params.json'))
META_BOUNDS = {
    'mutation_rate': (0.02, 0.4),
    'crossover_rate': (0.3, 1.0),
    'tournament_size': (2, 5),
    'early_stop_patience': (5, 8),
    'early_stop_tolerance': (0.001, 0.05),
}
META_DEFAULTS = {
    'mutation_rate': 0.15,
    'crossover_rate': 0.7,
    'tournament_size': 3,
    'early_stop_patience': 5,
    'early_stop_tolerance': 0.01,
}
META_INT_KEYS = ('tournament_size', 'early_stop_patience')
# EV-4 (2026-09-10): search-integrity knobs are NOT explorable. The meta-search
# had driven early_stop_patience to its own lower bound (3), so the GA stopped
# at gen 3-4 of a 15-gen budget every cycle and every trajectory came out flat.
# Letting a search relax its own stopping rule (or PROMOTE_MARGIN/WINS) is
# reward hacking; only real search knobs are perturbed.
META_EXPLORE_KEYS = ('mutation_rate', 'crossover_rate', 'tournament_size')
# Minimum generations before a convergence claim is honoured (EV-4):
# has_converged() reads within-process history only (each cycle is a new
# process), so an early plateau is not evidence of convergence.
MIN_GENS = 6
META_EXPLORE_PROB = 0.25
META_CANDIDATE_RUNS = 3


def _clamp_meta(params):
    """Force meta-params into META_BOUNDS (EV-4).

    Every source of meta-params must pass through here. A stale 'candidate'
    persisted in the meta state file otherwise pins a search-integrity knob
    below its floor: on 2026-09-10 the file's candidate still carried
    early_stop_patience=3, and _meta_propose() returned it unclamped — so the
    floor added to _meta_load() alone was bypassed and the GA kept stopping at
    gen 3-4.
    """
    out = {}
    for k, (lo, hi) in META_BOUNDS.items():
        v = min(hi, max(lo, float(params.get(k, META_DEFAULTS[k]))))
        out[k] = int(round(v)) if k in META_INT_KEYS else v
    return out


def _meta_load():
    try:
        with open(META_STATE_PATH, encoding='utf-8') as f:
            st = json.load(f)
        params = {k: float(st.get('params', {}).get(k, META_DEFAULTS[k]))
                  for k in META_BOUNDS}
        for k in META_INT_KEYS:
            params[k] = int(round(params[k]))
        params = _clamp_meta(params)
        return {'params': params,
                'score_ema': float(st.get('score_ema', 0.0)),
                'runs': int(st.get('runs', 0)),
                'candidate': st.get('candidate'),
                'candidate_runs': int(st.get('candidate_runs', 0)),
                'candidate_sum': float(st.get('candidate_sum', 0.0))}
    except Exception:
        return {'params': dict(META_DEFAULTS), 'score_ema': 0.0, 'runs': 0,
                'candidate': None, 'candidate_runs': 0, 'candidate_sum': 0.0}


def _meta_save(st):
    try:
        tmp = META_STATE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f, indent=2)
        os.replace(tmp, META_STATE_PATH)
    except Exception as e:
        log('meta state save failed: %s' % str(e)[:100])


def _active_strategy():
    """Metacog's active strategy (latest strategy_steered row), or '?'.

    G-7 steering point: the one place strategy enters evolution. Fail-open
    — unknown strategy behaves like a neutral one, never blocks a cycle.
    """
    try:
        from CORE.local_memory import get_memory_store
        rows = get_memory_store().query_memories(
            event_type="strategy_steered", limit=1)
        if rows:
            d = (rows[0].get("data", {}) or {})
            s = str(d.get("strategy", "?") or "?")
            return s
    except Exception as e:  # noqa: BLE001
        log("strategy lookup failed: %s" % str(e)[:100])
    return "?"


def _meta_propose(st, explore_prob=META_EXPLORE_PROB):
    """Pick this cycle's meta-params: exploit champion or explore one
    perturbed dimension. Returns (params, is_candidate).

    explore_prob overrides the module default so callers (G-7 steering:
    defensive halves exploration) can modulate without touching the
    safety-reviewed constant.
    """
    if st['candidate'] is None and random.random() < explore_prob:
        cand = dict(st['params'])
        key = random.choice(META_EXPLORE_KEYS)
        lo, hi = META_BOUNDS[key]
        if key in META_INT_KEYS:
            cand[key] = int(min(hi, max(lo, cand[key] + random.choice((-1, 1)))))
        else:
            cand[key] = min(hi, max(lo, cand[key] * random.uniform(0.8, 1.25)))
        st['candidate'] = cand
        st['candidate_runs'] = 0
        st['candidate_sum'] = 0.0
        log('meta: exploring %s=%s (champ %s)' % (key, cand[key], st['params'][key]))
        return cand, True
    if st['candidate'] is not None:
        return _clamp_meta(dict(st['candidate'])), True
    return _clamp_meta(dict(st['params'])), False


def _meta_score(st, params, is_candidate, margin):
    """Fold one cycle's margin into the meta-state; promote qualifying
    candidates. Returns a short status string for the log/artifact."""
    if is_candidate:
        st['candidate_runs'] += 1
        st['candidate_sum'] += margin
        mean = st['candidate_sum'] / st['candidate_runs']
        if st['candidate_runs'] >= META_CANDIDATE_RUNS:
            old_ema = st['score_ema']
            if mean > old_ema:
                st['params'] = _clamp_meta(dict(st['candidate']))
                st['score_ema'] = mean
                msg = ('meta: candidate PROMOTED (mean %+.2f > ema %+.2f): %s'
                       % (mean, old_ema, st['params']))
            else:
                msg = ('meta: candidate rejected (mean %+.2f <= ema %+.2f)'
                       % (mean, old_ema))
            st['candidate'] = None
            st['candidate_runs'] = 0
            st['candidate_sum'] = 0.0
        else:
            msg = ('meta: candidate %d/%d, mean %+.2f (ema %+.2f)'
                   % (st['candidate_runs'], META_CANDIDATE_RUNS, mean,
                      st['score_ema']))
    else:
        st['runs'] += 1
        alpha = 0.2
        st['score_ema'] += alpha * (margin - st['score_ema'])
        msg = 'meta: champ ema %+.2f over %d runs' % (st['score_ema'], st['runs'])
    _meta_save(st)
    return msg


def _acquire_cycle_lock():
    """Single-instance guard (2026-09-08): a manual run colliding with the
    scheduled one was observed twice (12:14 and 12:49 — two processes
    interleaved in the log and raced on the promotion file). Non-blocking;
    returns the open handle to hold for the run, or None when another
    cycle owns the lock. The OS releases msvcrt locks on process death,
    so no stale-lock cleanup is needed."""
    import msvcrt
    lock_path = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', 'logs', 'evolution_cycle.lock'))
    fh = open(lock_path, 'w', encoding="utf-8")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return fh
    except OSError:
        fh.close()
        return None


def main(crossover_operator=None, mutation_operator=None,
         mutation_rate=None, early_stop_patience=None):
    _lock = _acquire_cycle_lock()
    if _lock is None:
        log('another evolution cycle holds the lock, skipping this run')
        return
    _migrate_legacy_promotions()
    seed = int(time.time()) % 100000
    random.seed(seed)
    log('seed=%d' % seed)

    meta_st = _meta_load()
    # G-7 steering point: strategy enters evolution here and only here.
    # Defensive halves meta exploration (exploit the known-good champion
    # instead of perturbing search knobs); anything else explores at the
    # reviewed default. Logged + persisted per cycle for later analysis.
    strategy = _active_strategy()
    _explore = META_EXPLORE_PROB * (0.5 if strategy.strip().lower() == "defensive" else 1.0)
    log('strategy=%s explore_prob=%s' % (strategy, _explore))
    meta_params, meta_is_cand = _meta_propose(meta_st, explore_prob=_explore)
    log('meta: %s params=%s' % (
        'candidate' if meta_is_cand else 'champion',
        {k: round(v, 4) for k, v in meta_params.items()}))

    evo = EvolutionEngine(population_size=24, elitism_count=3,
                          crossover_rate=meta_params['crossover_rate'],
                          mutation_rate=mutation_rate if mutation_rate is not None
                          else meta_params['mutation_rate'],
                          tournament_size=meta_params['tournament_size'],
                          early_stop_patience=early_stop_patience if early_stop_patience is not None
                          else meta_params['early_stop_patience'],
                          early_stop_tolerance=meta_params['early_stop_tolerance'],
                          crossover_operator=crossover_operator or 'sbx',
                          mutation_operator=mutation_operator or 'polynomial')
    seed_genes = last_best_genes()

    # Frozen eval pools (2026-09-08 fix, 2026-09-09 shared-snapshot
    # hardening): one snapshot per cycle, reused by consecutive cycles
    # while fresh, so genomes, champion re-eval AND the baseline — this
    # run and the next — are all scored on identical data. Same
    # extraction params the live path used (limit = N_EPISODES * 10),
    # so historical fitness stays comparable.
    # ORDER (2026-09-10 fix): pools freeze BEFORE the HOF reseed — the
    # reseed re-evaluates on current pools, and calling it with undefined
    # pools killed two live cycles with NameError.
    info_pool, alert_pool, pool_reused, pool_age_s, pool_id = _load_or_freeze_pools()

    def fitness_frozen(genome):
        return fitness_genes(genome.genes, info_pool, alert_pool)

    # One-shot canary watch for the previous promotion (2026-09-09).
    # Deferred automatically while pools are shared.
    canary_report = _check_canary(info_pool, alert_pool, pool_reused)
    if canary_report:
        log('canary: %s' % canary_report.get('status'))

    # Hall of fame (2026-09-10 audit fix #1+#4): rank by MARGIN over the
    # baseline, not raw champion_reeval. Raw scores are pool-era-relative
    # (a 27.6 crowned in an easy era re-evals 21.2 today — worse than
    # fresh randoms, wasting seed slots). Margin is pool-invariant, and
    # the top-3 by margin are re-evaluated on the CURRENT pools so the
    # seed proves itself in this era before joining the population.
    # Rows with a falsy baseline are skipped (margin undefined).
    hof_genes = _select_hof(info_pool, alert_pool)

    # v2.0 seeds: champion + HOF + TPE suggests (exploit history) + one QD
    # diverse elite (explore). TPE/QD suggestions cost zero extra evals —
    # they join the GA population as seeds. All fail-open: GA unaffected.
    _qd_cells = _qd_autocenter(_load_qd_archive())
    _tpe = _tpe_seeds()
    if _tpe:
        log('tpe seeds: %d suggestions from %d-row history' % (len(_tpe), TPE_HISTORY_LIMIT))
    _qd_seed = _qd_suggest(_qd_cells)
    if _qd_seed:
        log('qd seed: diverse elite from %d-cell archive' % len(_qd_cells))
    _seeds = [g for g in (seed_genes, hof_genes, *_tpe, _qd_seed)
              if isinstance(g, dict) and g]
    # Dedupe identical gene dicts
    _seen = set()
    _unique_seeds = []
    for _g in _seeds:
        try:
            _key = tuple(sorted((k, round(float(v), 6)) for k, v in _g.items()))
        except Exception:
            continue
        if _key not in _seen:
            _seen.add(_key)
            _unique_seeds.append(_g)

    # --- PROTEUS Phase E: rotate ONE extra gene into the search space -------
    # gated, fail-open. `_trainer_extra_kwargs` is the matching pass-through:
    # without it the added gene is sampled by the engine and then discarded by
    # the evaluator, so the search would look 5-D and score 4-D.
    _gene_specs, _morph_gene = None, None
    _morph_index = -1
    if PROTEUS_MORPH:
        try:
            from CORE.WORMS.expansion.proteus_hooks import (
                morph_gene_specs, morph_strip)
            _morph_index = _morph_cycle_index()
            _gene_specs, _morph_gene = morph_gene_specs(_morph_index, GENE_BOUNDS)
            # Seeds come from last_best_genes(), i.e. the PREVIOUS cycle's
            # winner, which carries whatever extra gene that cycle trialled.
            # The engine fills missing genes but never prunes extras, so
            # without this strip the genome accumulates candidates forever
            # (measured: a dimension-6 row carrying BOTH genes). Each cycle
            # must be a clean 4 + 1 factorial or the per-gene verdict is
            # confounded.
            _stripped = 0
            for _sg in _unique_seeds:
                try:
                    _clean = morph_strip(_sg)
                    if len(_clean) != len(_sg):
                        _stripped += 1
                    _sg.clear()
                    _sg.update(_clean)
                except Exception:
                    continue
            if _stripped:
                log('morph: stripped prior extra genes from %s seed genome(s)'
                    % _stripped)
            log('morph: cycle %s adds gene %s (kind=%s)' % (
                _morph_index, (_morph_gene or {}).get('name'),
                (_morph_gene or {}).get('kind')))
        except Exception as e:
            _gene_specs, _morph_gene = None, None
            log('morph specs failed (fail-open to 4-gene): %s' % str(e)[:120])

    evo.initialize_population(dict(GENE_BOUNDS), seed_genomes=_unique_seeds,
                              gene_specs=_gene_specs)

    # Parallel eval workers (2026-09-09): fitness is now RNG-isolated per
    # genome (no global-random traffic), so the population evaluates with
    # true parallelism under free-threading. 8 workers ≈ one per 3 genomes
    # at population 24; results assign back in order, stats bit-identical.
    _workers = 8

    trajectory = []
    trajectory_avg = []
    for gen in range(15):
        stats = evo.evaluate_population(fitness_frozen, workers=_workers)
        best = evo.get_best_genome()
        trajectory.append(round(stats.get('best_fitness', 0.0), 4))
        # EV-8 (2026-09-11): the population MEAN is what distinguishes a
        # plateau at a strong seeded champion from a true no-gradient stall.
        # With elitism, `best_fitness` is flat BY CONSTRUCTION until a child
        # beats the elite, so `best` alone cannot tell the two apart - and
        # the monitor was asserting 'fitness has no gradient' on that basis
        # even though 40 random genomes on this pool give 40 distinct scores.
        trajectory_avg.append(round(stats.get('avg_fitness', 0.0), 4))
        log(f'gen {gen}: best={stats.get("best_fitness")} '
            f'genes={best.genes if best else None}')
        # EV-4: honour a convergence claim only after MIN_GENS generations.
        if (gen + 1) >= MIN_GENS and evo.has_converged():
            log(f'converged at gen {gen}, stopping early')
            break
        evo.evolve()

    best = evo.get_best_genome()
    log('BEST: %s' % (best.genes if best else None))
    best_total, best_info, best_alert = (
        fitness_parts(best.genes, info_pool, alert_pool) if best else (0.0, 0.0, 0.0))
    log('BEST parts: total=%s info=%s alert=%s' % (best_total, best_info, best_alert))

    # Baseline moved ahead of the CMA arm (gate needs the margin first).
    try:
        baseline_genes = production_genes()
        baseline_fit = round(fitness_genes(baseline_genes, info_pool, alert_pool), 4)
        log('production baseline: %s -> %s' % (
            {k: round(v, 4) for k, v in baseline_genes.items()}, baseline_fit))
    except Exception as e:
        baseline_genes, baseline_fit = {}, 0.0
        log('baseline eval failed: %s' % str(e)[:120])

    # --- v2.0 CMA-ES second arm (audit followup, 2026-09-11) -----------------
    # Seeded at the GA champion, 2 gens x ~8 evals. Winner-takes-best on the
    # SAME frozen pools; the winner alone enters SPRT -> pareto -> holdout ->
    # canary, so gate logic is unchanged. Fail-open: any error keeps GA best.
    # Gated (2026-09-11, post-RUN100: 4/100 wins): the arm earns its ~11%
    # overhead only where the GA looks contestable — margin below 2x the
    # promote threshold. A GA champion already crushing it stands unopposed.
    cma_report = {'ran': False, 'winner': 'ga', 'arm': _second_arm_name(),
                  'funded_by': 'circulation' if PROTEUS_CIRCULATION else '2x_gate'}

    # The secondary arm must search the SAME genome as the GA. With Phase E on
    # that genome is 4 continuous genes + 1 switch, and the switch is offered
    # to the continuous optimiser as a (0.0, 1.0) coordinate (thresholded at
    # 0.5 in morph_trainer_kwargs). Otherwise an arm win silently drops the
    # extra dimension: measured cycle 8, dimension 4, extra {}.
    _arm_gene_bounds = dict(GENE_BOUNDS)
    if PROTEUS_MORPH and _morph_gene:
        _arm_gene_bounds[_morph_gene['name']] = (0.0, 1.0)
    _gate_margin = (best_total - baseline_fit) if best and baseline_genes else float("-inf")
    _circ = {}
    if PROTEUS_CIRCULATION:
        # Phase D replacement for the fixed gate. The old rule
        # `margin < 2 * PROMOTE_MARGIN` could only ever say "not needed right
        # now"; every margin on the 2026-09-11 batch was >= 2.91 against a 1.5
        # threshold, so the second arm was skipped 50/50 and the operator was
        # structurally unreachable once the regime shifted. Thompson sampling
        # with a floor share keeps a starved arm discoverable.
        try:
            from CORE.WORMS.expansion.proteus_hooks import circulation_decide
            _circ = circulation_decide(CIRCULATION_JSON)
            cma_report['circulation'] = _circ
            _circ_arm = _circ.get('arm')
            # The allocator is restricted to SEARCH_ARMS, so a funded secondary
            # is always 'cma' or 'de' and cma_report['arm'] therefore names the
            # arm that ACTUALLY runs. Feedback keys on this label, so the two
            # cannot drift apart.
            if _circ_arm in ('cma', 'de'):
                cma_report['arm'] = _circ_arm
            _cma_wanted = bool(_circ.get('run_secondary')) and best is not None
            log('circulation: arm=%s weights=%s -> secondary %s' % (
                _circ_arm, _circ.get('weights'),
                'RUNS' if _cma_wanted else 'skipped'))
        except Exception as e:
            _circ = {'error': str(e)[:140]}
            _cma_wanted = best is not None and _gate_margin < 2 * PROMOTE_MARGIN
            log('circulation failed (fail-open to 2x gate): %s' % str(e)[:120])
    else:
        _cma_wanted = best is not None and _gate_margin < 2 * PROMOTE_MARGIN
    if not _cma_wanted and best is not None:
        cma_report['skipped'] = 'ga margin %.3f clears 2x threshold' % _gate_margin
        log('%s arm: skipped (%s)' % (cma_report['arm'].upper(), cma_report['skipped']))
    if _cma_wanted:
        _cma_genes, _cma_fit = _run_cma_arm(
            best.genes, fitness_frozen, _workers,
            force_arm=(cma_report.get('arm') if PROTEUS_CIRCULATION else None),
            gene_bounds=_arm_gene_bounds)
        if isinstance(_cma_genes, dict) and _cma_genes:
            try:
                _cma_total, _cma_info, _cma_alert = fitness_parts(
                    _cma_genes, info_pool, alert_pool)
                cma_report.update({'ran': True, 'cma_total': _cma_total,
                                   'ga_total': best_total})
                _armtag = cma_report['arm'].upper()
                if _cma_total > best_total:
                    from CORE.WORMS.expansion.evolution import Genome as _Genome
                    best = _Genome(genes=dict(_cma_genes), fitness=float(_cma_fit or _cma_total),
                                   generation=evo._generation + 1, parent_ids=[])
                    best_total, best_info, best_alert = _cma_total, _cma_info, _cma_alert
                    cma_report['winner'] = 'cma'
                    log('%s arm: WON total=%s (ga=%s) info=%s alert=%s' % (
                        _armtag, _cma_total, cma_report['ga_total'], _cma_info, _cma_alert))
                else:
                    log('%s arm: held (%s=%s ga=%s), GA champion stands' % (
                        _armtag, cma_report['arm'], _cma_total, best_total))
            except Exception as e:
                log('second-arm scoring failed: %s' % str(e)[:120])

    # --- v2.0 MAP-Elites update (audit followup, 2026-09-11) -----------------
    # Behavior descriptor = (info, alert) mastery: the archive keeps the
    # fittest genome per behavior cell, so a future D-2-class collapse (one
    # cell dominating fitness) preserves stepping stones elsewhere. Top-5 GA
    # + CMA best + winner enter the grid; archive persists in state/.
    qd_report = {'filled': len(_qd_cells), 'added': 0}
    try:
        _qd_cands = []
        try:
            _pop_sorted = sorted(evo._population, key=lambda g: -g.fitness)[:5]
            _qd_cands.extend([dict(g.genes) for g in _pop_sorted])
        except Exception:
            pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled
        if best is not None:
            _qd_cands.append(dict(best.genes))
        _nstat = _qd_norm_stats()
        for _g in _qd_cands:
            try:
                _t, _i, _a = fitness_parts(_g, info_pool, alert_pool)
            except Exception:
                continue
            _ni = (_i - _nstat["info"][0]) / _nstat["info"][1]
            _na = (_a - _nstat["alert"][0]) / _nstat["alert"][1]
            _cell = _qd_cell_of((_ni, _na))
            _key = '%d,%d' % _cell
            _admit = _qd_admit_cell(_qd_cells, _key, _g, float(_t),
                                    (_ni, _na),
                                    (round(_i, 3), round(_a, 3)))
            if _admit:
                qd_report['added'] += 1
        _save_qd_archive(_qd_cells)
        # Q3: VQ-Elites-lite measurement (report only; adopt only if coverage > 9/16)
        _vq_extra = {}
        if QD_VQ_ELITES:
            _vq_cov, _vq_filled = _qd_vq_elites_report(_qd_cells)
            _vq_extra['vq_coverage'] = round(_vq_cov, 4)
            log('qd vq-elites: learned-dims coverage=%.4f (%d/16 cells; baseline 9/16=0.5625)'
                % (_vq_cov, _vq_filled))
        _total_cells = 1
        for (_n, _b, _l, _h) in QD_BEHAVIOR_DIMS:
            _total_cells *= _b
        _fits = [float(c.get('fitness', 0.0)) for c in _qd_cells.values()
                 if isinstance(c, dict)]
        qd_report.update({'filled': len(_qd_cells), 'total_cells': _total_cells,
                          'coverage': round(len(_qd_cells) / _total_cells, 4) if _total_cells else 0.0,
                          'qd_score': round(sum(_fits), 2) if _fits else 0.0,
                          'best': round(max(_fits), 4) if _fits else 0.0})
        qd_report.update(_vq_extra)
        log('qd archive: %(filled)d/%(total_cells)d cells coverage=%(coverage)s '
            'qd_score=%(qd_score)s best=%(best)s (+%(added)d this cycle)' % qd_report)
    except Exception as e:
        log('qd update failed: %s' % str(e)[:120])

    # --- Engran #849 QD-ESCAPE: bounded non-operator novelty trials ----------
    # When the QD archive is flat (filled stuck at baseline, score at/below
    # baseline) and operators are exhausted, run a bounded number of pure-random
    # novelty trials (no GA/CMA operators) to stir behavior-space. Each trial
    # is appended to state/bake_kilo_qdescape.jsonl. Gated by AZAZAEL_QD_ESCAPE.
    if QD_ESCAPE:
        try:
            _bt_report = {}
            try:
                with open(os.path.join(_REPO, 'state', 'backtrack_report.json'),
                          encoding='utf-8') as _bfh:
                    _bt_report = json.load(_bfh)
            except Exception:
                _bt_report = {}
            _bt_overall = _bt_report.get('overall', {}) if isinstance(_bt_report, dict) else {}
            _flip_rate = float(_bt_overall.get('flip_rate', 0.0) or 0.0)
            _stuck = (qd_report.get('filled', 0) <= QD_ESCAPE_BASELINE_FILLED
                      and qd_report.get('qd_score', 0.0) <= QD_ESCAPE_BASELINE_QD_SCORE)
            if _stuck:
                _nstat = _qd_norm_stats()
                _trials = []
                for _ti in range(QD_ESCAPE_MAX_TRIALS):
                    _g = {}
                    for _k, (_lo, _hi) in GENE_BOUNDS.items():
                        _g[_k] = random.uniform(_lo, _hi)
                    try:
                        _t, _i, _a = fitness_parts(_g, info_pool, alert_pool)
                    except Exception:
                        continue
                    _ni = (_i - _nstat["info"][0]) / _nstat["info"][1]
                    _na = (_a - _nstat["alert"][0]) / _nstat["alert"][1]
                    _cell = _qd_cell_of((_ni, _na))
                    _key = '%d,%d' % _cell
                    _before = len(_qd_cells)
                    _admit = _qd_admit_cell(_qd_cells, _key, _g, float(_t),
                                            (_ni, _na),
                                            (round(_i, 3), round(_a, 3)))
                    _trials.append({
                        'trial': _ti,
                        'admitted': bool(_admit),
                        'genes': _g,
                        'fitness': round(float(_t), 4),
                        'info': round(float(_i), 4),
                        'alert': round(float(_a), 4),
                        'behavior': [round(_ni, 4), round(_na, 4)],
                        'cell': _key,
                        'added': len(_qd_cells) - _before,
                    })
                    log('qd escape trial %d: admitted=%s fitness=%s cell=%s' % (
                        _ti, _admit, round(float(_t), 4), _key))
                if _trials:
                    _save_qd_archive(_qd_cells)
                    try:
                        os.makedirs(os.path.dirname(QD_ESCAPE_JSONL), exist_ok=True)
                        with open(QD_ESCAPE_JSONL, 'a', encoding='utf-8') as _efh:
                            for _tr in _trials:
                                _efh.write(json.dumps(_tr) + '\n')
                    except Exception as e:
                        log('qd escape jsonl write failed: %s' % str(e)[:100])
                log('qd escape: stuck(flip_rate=%.4f) ran %d trials, archive %d->%d' % (
                    _flip_rate, len(_trials), qd_report.get('filled', 0), len(_qd_cells)))
            else:
                log('qd escape: not stuck (filled=%d score=%s) - no trials' % (
                    qd_report.get('filled', 0), qd_report.get('qd_score', 0.0)))
        except Exception as e:
            log('qd escape failed: %s' % str(e)[:120])

    # Champion re-evaluation on the current pool: with deterministic fitness
    # this score is comparable across runs, separating 'genome got worse'
    # from 'the data landscape moved' (drift shows in pool_fingerprint).
    champ_score = None
    if seed_genes:
        try:
            champ_score = round(fitness_genes(seed_genes, info_pool, alert_pool), 4)
            log('champion re-eval on current pool: %s' % champ_score)
        except Exception as e:
            log('champion re-eval failed: %s' % str(e)[:120])

    # ARMOR anchor (2026-10-06 retrofit): no-regression vs the reigning
    # champion on the CURRENT pools. beats compares candidate vs production
    # baseline; this compares candidate vs seed champion re-scored on the
    # same current pools, so a candidate that regressed relative to the
    # incumbent cannot promote even while beating a stale baseline.
    # champ_score None (cold start, no seed) disables the pin (fail-open
    # only when there is nothing to regress from).
    beats_anchor = True
    margin_vs_champ = 0.0
    if champ_score is not None and best:
        try:
            margin_vs_champ = round(best_total - champ_score, 4)
            beats_anchor = bool(margin_vs_champ >= 0.0)
        except Exception:
            beats_anchor = True
    if not beats_anchor:
        log('ANCHOR VETO: candidate regressed vs reigning champion '
            '(margin_vs_champ=%s)' % margin_vs_champ)

    # (baseline evaluated above, ahead of the CMA gate)
    baseline_id = baseline_hash(baseline_genes) if baseline_genes else ''
    _margin_raw = (best_total - baseline_fit) if best else 0.0
    beats = bool(best) and (_margin_raw > PROMOTE_MARGIN)
    margin_now = round(_margin_raw, 4)

    # Entry vs pool-fit (2026-11-02): `beats` answers "does this genome fit the
    # FROZEN pools", which is the overfit filter itself. Using it as the
    # pipeline's entry condition applies that filter twice, and the second
    # application costs exactly the evidence the SPRT exists to collect:
    # measured 2026-11-02, 5 candidates cleared the frozen gate at 1.84x the
    # threshold, none generalised on fresh pools (0.17x) and 0/5 holdouts
    # passed, while every cycle BELOW the frozen gate recorded `sprt not_run`
    # with an empty cranium and contributed nothing to the cross-era pooling
    # that accumulates precisely this evidence. Entry is therefore "a candidate
    # exists"; the frozen margin still enters at promotion through
    # `_adaptive_ok`, and the streak (`_compute_streak(..., beats)`) stays a
    # frozen-gate counter, so a promotion cannot be assembled from sub-gate
    # margins.
    _candidate_worthy = bool(best)
    # Reporting alias, deliberately distinct from the gate variable: the
    # outcome label further down describes the frozen-gate result, and the
    # source-level guard in tests/test_evolution_gates.py forbids `beats` from
    # appearing in any gate position. A true sentence about the outcome is not
    # a gate.
    _beat_baseline_this_cycle = beats

    # --- stall detection (2026-09-12) ---------------------------------------
    # A frozen pool (`POOL_MAX_AGE_S` = 1800s) makes consecutive cycles
    # re-compute the SAME margin, so `beats=False` repeats and the SPRT, the
    # Pareto refine, the holdout and Cranium are ALL skipped. Measured: 100
    # consecutive cycles with `beats=False`, twenty of them recording the
    # identical margin 0.5563. Nothing in the log said the loop had stopped
    # producing information - it looked exactly like normal operation, which is
    # the same failure the pareto-blocked streak was added to expose.
    #
    # So the repetition is measured and recorded. A cycle that repeats the
    # previous margin on a reused pool is NOT a new observation, and downstream
    # consumers can now see that instead of inferring it.
    _repeat = 0
    try:
        from CORE.local_memory import get_memory_store
        _recent = get_memory_store().query_memories(
            event_type='evolution_result', limit=12)
        _repeat = _margin_repeat_streak(_recent, margin_now)
    except Exception:
        _repeat = 0
    _non_informative = bool(_repeat >= 2 and pool_reused)
    if _non_informative:
        log('STALL: margin %s repeated %d cycles on a reused pool (id=%s) - '
            'no new information; SPRT/pareto/holdout cannot run, so this '
            'cycle is recorded as non-informative'
            % (margin_now, _repeat + 1, pool_id))
    streak = 0
    # E2 (2026-09-11, 50-run analysis): this default used to be 'reject', so a
    # cycle the margin gate already failed was recorded and displayed as an SPRT
    # rejection even though the test never ran (n=0). 31 of 51 analysed cycles
    # were that case, which made the SPRT look far more decisive than it was.
    # 'not_run' keeps the two situations distinguishable everywhere downstream.
    sprt_llr, sprt_sigma, sprt_n, sprt_decision = 0.0, 0.0, 0, 'not_run'
    # (_repeat / _non_informative are computed by the stall check above.)
    _sprt_dup = 0
    if _candidate_worthy:
        streak = 1
        _hrows = []
        # EV-4 (2026-09-30): identity of the search space this cycle measured.
        # Derived from the genes the champion actually carries, so it matches
        # the `schema_hash` written into this cycle's own result row below and
        # the one every prior row carries. Empty/None disables the per-arm
        # pinning and reverts to the pre-EV-4 behaviour (fail-safe, not
        # fail-open: an unidentified space simply does not gain a streak).
        _schema_id = ''
        try:
            _schema_id = schema_hash(
                (getattr(best, 'genes', None) or seed_genes or {}))
        except Exception:
            _schema_id = ''
        try:
            from CORE.local_memory import get_memory_store
            _hrows = get_memory_store().query_memories(
                event_type='evolution_result', limit=PROMOTE_MAX_WINS)
            # Era-pinned streak criteria live in _compute_streak (EV-2).
            # EV-4: also pinned to this cycle's search space. Display-only --
            # the SPRT below is the gate that actually promotes.
            streak = _compute_streak(_hrows, pool_id, baseline_id, beats,
                                    schema_id=_schema_id)
        except Exception as e:
            log('streak lookup failed: %s' % str(e)[:120])
        # SPRT (2026-09-11, followup #2): variance-aware sequential gate that
        # replaces the fixed 3/3 rule as the promotion criterion. The era-pinned
        # streak is retained only as a display/compatibility counter.
        try:
            _prior = _sprt_history(_hrows, pool_id, baseline_id,
                                   schema_id=_schema_id)
            _floor = salt_spread(best.genes, info_pool, alert_pool)
            # Collapse re-measurements BEFORE the test. A frozen pool makes
            # consecutive margins bit-identical, and the additive LLR would
            # otherwise treat one number as a growing stack of evidence.
            _seq, _sprt_dup = _sprt_collapse(_prior + [margin_now])
            if _sprt_dup:
                log('SPRT: collapsed %d re-measurement(s) of an identical '
                    'margin (frozen pool) -> n=%d' % (_sprt_dup, len(_seq)))
            sprt_llr, sprt_sigma, sprt_n, sprt_decision = _sprt_decide(
                _seq, _floor)
        except Exception as e:
            log('sprt failed: %s' % str(e)[:120])
    log('promotion standing: beats=%s margin=%s sprt=%s(n=%s llr=%s sigma=%s) '
        'streak=%s/%s' % (beats, margin_now, sprt_decision, sprt_n, sprt_llr,
                          sprt_sigma, streak, PROMOTE_WINS))
    sprt_ok = sprt_decision == 'promote'

    # --- PROTEUS Phase D: Cranium cross-era pooling (2026-09-12) ------------
    # Why this exists: `_sprt_history` hard-stops at the first pool_id change,
    # and on the 2026-09-11 batch that break landed 28 times in 51 cycles
    # (~1.8 cycles/era) while the SPRT needs 2..6 same-era observations. The
    # evidence could never accumulate, so the gate produced 1 promotion in 51
    # cycles. Cranium treats era as a random effect and pools through the grand
    # mean, so a NEW era ADDS information instead of resetting the count.
    # Both directions are wired: rescue a stalled SPRT, and veto an SPRT
    # promote whose pooled posterior is strongly below the promote threshold.
    cranium = {}
    if PROTEUS_CRANIUM and _candidate_worthy:
        try:
            from CORE.WORMS.expansion.proteus_hooks import (
                cranium_decide, cranium_override)
            cranium = cranium_decide(_hrows, pool_id, margin_now,
                                     threshold=SPRT_DELTA1)
            # Policy lives in a pure function so both branches are testable
            # without running this 2,350-line script (see cranium_override).
            _sprt_before = sprt_decision
            _ov = cranium_override(sprt_decision, cranium)
            sprt_decision = _ov['decision']
            sprt_ok = _ov['sprt_ok']
            cranium['action'] = _ov['action']
            cranium['sprt_before'] = _sprt_before
            if _ov['action'] == 'rescue':
                log('CRANIUM rescue: pooled over %s eras (n=%s) p_above=%s '
                    'posterior %s+-%s -> SPRT gained a promotion' % (
                        cranium.get('n_eras'), cranium.get('n_obs'),
                        cranium.get('p_above_threshold'),
                        cranium.get('posterior_mean'),
                        cranium.get('posterior_sd')))
            elif _ov['action'] == 'veto':
                log('CRANIUM veto: pooled posterior p_above=%s < alpha over %s '
                    'eras -> SPRT promote withdrawn (E9 shape)' % (
                        cranium.get('p_above_threshold'), cranium.get('n_eras')))
            log('cranium: action=%s (sprt %s -> %s) %s' % (
                _ov['action'], _sprt_before, sprt_decision,
                {k: v for k, v in cranium.items()
                 if k not in ('action', 'sprt_before')}))
        except Exception as e:
            cranium = {'error': str(e)[:140]}
            log('cranium failed (fail-open to SPRT): %s' % str(e)[:120])
    if _candidate_worthy and sprt_decision == 'reject':
        log('SPRT rejected: evidence against a real improvement (llr %s <= %s) '
            '- streak reset' % (sprt_llr, round(SPRT_LOWER, 4)))

    # --- NSGA-II refinement + Pareto balance gate (2026-09-11, followup #1) ---
    # Refinement now runs BEFORE the promotion decision, so the knee is an input
    # to it instead of a post-hoc report, and the scalar total can no longer buy
    # a promotion by trading away the critical-alert objective.
    # E1 (2026-09-11, 50-run analysis): the refine is ~90% of cycle wall clock
    # (1066s of 1177s over 51 runs) and its knee/front are ONLY read inside the
    # `beats and sprt_ok` block below. On a cycle the margin gate already failed
    # that search burned ~21s to produce a front nothing consumes - 33 of the 51
    # analysed cycles. Gate the expensive search on the cheap decision that can
    # actually use its output.
    pareto = (_pareto_refine(evo, info_pool, alert_pool)
              if (_candidate_worthy and sprt_ok and best) else None)
    knee_genes = ((pareto or {}).get('knee') or {}).get('genes')
    pareto_gate = {}
    cand_genes = best.genes if best else {}
    cand_total, cand_info, cand_alert = best_total, best_info, best_alert
    if _candidate_worthy and sprt_ok and best:
        pareto_gate = _pareto_gate(cand_genes, baseline_genes, info_pool,
                                   alert_pool, knee_genes=knee_genes)
        log('pareto gate: %s' % {k: v for k, v in pareto_gate.items()
                                if k != 'knee_genes'})
        if pareto_gate.get('source') == 'knee' and pareto_gate.get('knee_genes'):
            # The champion regressed the alert objective but the balanced front
            # point does not and still clears the margin: promote the knee.
            cand_genes = pareto_gate['knee_genes']
            cand_total = pareto_gate.get('knee_total', cand_total)
            cand_info = pareto_gate.get('knee_info', cand_info)
            cand_alert = pareto_gate.get('knee_alert', cand_alert)
            log('pareto gate: promoting the NSGA-II knee instead of the champion')
        elif not pareto_gate.get('passed'):
            log('promotion BLOCKED by pareto balance gate'
                ' (alert %s < floor %s)' % (pareto_gate.get('cand_alert'),
                                            pareto_gate.get('alert_floor')))
    pareto_ok = bool(pareto_gate.get('passed'))
    # EV-9: surface a persistent blockage instead of logging the same line every
    # cycle. The streak counts PRIOR cycles, so >=10 means this is the 11th+.
    pareto_blocked_streak = _pareto_blocked_streak() if pareto_gate else 0
    if not pareto_ok and pareto_blocked_streak >= 10:
        log('pareto gate has now blocked %d consecutive promotions - the alert '
            'floor may be unsatisfiable; review PARETO_ALERT_TOL_PCT'
            % (pareto_blocked_streak + 1))
    meta_msg = _meta_score(meta_st, meta_params, meta_is_cand,
                           (best_total - baseline_fit) if best else 0.0)
    log(meta_msg)

    promoted = False
    holdout = {}

    # PROTEUS Phase C pre-gate. Runs BEFORE the holdout deliberately: it is the
    # cheaper, STRICTER question ("does this clear baseline+margin across
    # imagined eras?"), so it belongs upstream of the single fresh draw it is
    # meant to strengthen. Placing it after the holdout made it unreachable --
    # measured 2026-09-12: 14 of 14 cycles reached beats+sprt+pareto yet sat in
    # holdout cooldown, so an after-holdout gate never evaluated once.
    if _candidate_worthy and sprt_ok and pareto_ok and best:
        oracle_gate = _oracle_gate(cand_genes, baseline_fit + PROMOTE_MARGIN)
        log('oracle gate: %s' % oracle_gate)
    else:
        oracle_gate = {'ok': True, 'skipped': 'earlier gate did not pass'}

    if _candidate_worthy and sprt_ok and pareto_ok and best:
        # Holdout cooldown (2026-09-10): a blocked streak re-tests
        # holdout every cycle within one pool era (~25s apart at
        # rapid-fire cadence) with no new evidence. Re-test only when
        # the frozen margin improved since the last block; era change
        # clears the cooldown automatically via pool_id mismatch.
        _cd_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'state', 'holdout_cooldown.json')
        _cd_margin = None
        try:
            if os.path.exists(_cd_path):
                _cd = json.load(open(_cd_path, encoding='utf-8'))
                if _cd.get('pool_id') == pool_id:
                    _cd_margin = float(_cd.get('margin', -1e9))
        except Exception:
            _cd_margin = None
        _margin_now = round(best_total - baseline_fit, 4)
        if _cd_margin is not None and _margin_now <= _cd_margin + 0.05:
            log('holdout cooldown: margin %s not improved since block (%s), '
                'waiting for era change or stronger genome' % (
                    _margin_now, _cd_margin))
            holdout = {'passed': False, 'cooldown': True,
                       'margin': _margin_now, 'prior_block': _cd_margin}
        else:
            holdout = _holdout_gate(cand_genes, baseline_genes)
        log('holdout: %s' % holdout)
        if not holdout.get('passed') and not holdout.get('cooldown'):
            log('promotion BLOCKED by holdout gate')
            try:
                with open(_cd_path, 'w', encoding='utf-8') as f:
                    json.dump({'pool_id': pool_id, 'margin': _margin_now,
                               'at': time.strftime('%Y-%m-%dT%H:%M:%S')}, f)
            except Exception as e:
                log('cooldown write failed: %s' % str(e)[:100])
    if not oracle_gate.get('ok'):
        log('promotion BLOCKED by oracle counterfactual-era gate: %s'
            % oracle_gate)

    # Phase E is a MEASUREMENT phase, not a deployment. A 5-gene genome is
    # scored WITH the extra gene active (`_trainer_extra_kwargs`), but
    # production evaluates with only the four genes unless Morph is also on —
    # through `production_genes()`, which deliberately reads just the four.
    # Promoting it would write a champion whose recorded fitness does not
    # describe how it will be scored, which is exactly the incomparability the
    # plan warned about. Trial it, measure it, do not ship it.
    _morph_blocks_promotion = bool(PROTEUS_MORPH and _morph_gene)
    if _morph_blocks_promotion and _candidate_worthy and sprt_ok and pareto_ok \
            and holdout.get('passed'):
        log('morph: promotion WITHHELD — genome carries the trial gene %s, so '
            'its measured fitness is not the fitness production would score'
            % _morph_gene.get('name'))

    # --- PROTEUS Phase E: the matched A/B (2026-09-12) ----------------------
    # The comparison above asks "did a 5-gene genome beat the 4-gene production
    # baseline", which conflates the added gene with the search that produced
    # the champion. Measured on this ledger: 52 of 69 trials never changed the
    # trainer call at all, so that comparison was reporting the search and the
    # gene was credited for it.
    #
    # The A/B holds everything fixed — SAME champion, SAME frozen pools, SAME
    # seeds — and moves ONE gene: the trainer's default value versus the value
    # that actually changes the call. That difference is attributable to the
    # gene by construction. It also FORCES the trial arm off the default, which
    # is the point: a trial that leaves the call alone cannot be evidence no
    # matter how the search scored.
    #
    # Cost: 2 evaluations (6 salt-runs) against ~144 for one generation of a
    # 24-genome population, so ~4% of a generation. Fail-open: a ledger row
    # without an A/B is still a row, and MUTAGEN reports it as unattributable
    # rather than guessing.
    _morph_ab = None
    if _morph_blocks_promotion and best is not None:
        try:
            from CORE.WORMS.expansion.proteus_hooks import (
                morph_ab_delta, morph_ab_plan)
            _ab_plan = morph_ab_plan(_morph_gene, best.genes)
            if _ab_plan:
                _ab_ctl = dict(best.genes)
                _ab_ctl[_ab_plan['gene']] = _ab_plan['control']
                _ab_trt = dict(best.genes)
                _ab_trt[_ab_plan['gene']] = _ab_plan['treatment']
                _c_tot, _c_info, _c_alert = fitness_parts(
                    _ab_ctl, info_pool, alert_pool)
                _t_tot, _t_info, _t_alert = fitness_parts(
                    _ab_trt, info_pool, alert_pool)
                _morph_ab = {
                    'gene': _ab_plan['gene'],
                    'default': _ab_plan['default'],
                    'control_value': _ab_plan['control'],
                    'treatment_value': _ab_plan['treatment'],
                    'control_fitness': _c_tot,
                    'treatment_fitness': _t_tot,
                    'control_parts': {'info': _c_info, 'alert': _c_alert},
                    'treatment_parts': {'info': _t_info, 'alert': _t_alert},
                    'delta': morph_ab_delta(_c_tot, _t_tot),
                }
                log('morph A/B: %s default(%s)=%.4f treatment(%s)=%.4f '
                    'delta=%s' % (
                        _ab_plan['gene'], _ab_plan['control'], _c_tot,
                        _ab_plan['treatment'], _t_tot,
                        _morph_ab['delta']))
        except Exception as e:
            _morph_ab = None
            log('morph A/B failed (fail-open, ledger still written): %s'
                % str(e)[:120])

    # EV-5 (2026-09-30): refuse to promote on a fitness whose arms are not all
    # live. See FITNESS_ARM_VETO for the measurement that motivates this. The
    # check is computed BEFORE the condition so the reason is loggable, and a
    # veto is reported rather than silently dropping the promotion.
    _arm_parts = {'info': cand_info, 'alert': cand_alert}
    _arms_live = fitness_arms_live(_arm_parts)
    _dead_arms = dead_fitness_arms(_arm_parts)
    if FITNESS_ARM_VETO and not _arms_live:
        log('VETO (EV-5 objective-arm liveness): dead arm(s) %s -- total %s '
            'equals the surviving arm, so this score does not represent a '
            '%s capability. Promotion blocked until the arm is grown or '
            'formally retired.' % (
                ','.join(_dead_arms), round(cand_total, 4), '/'.join(_dead_arms)))

    # EV-7 (2026-09-30): the loop's OWN health is a promotion input. This is
    # the last ouroboros arrow, and its absence is why the loop could not
    # notice its own stagnation.
    #
    # `state/loop_integrity.json` and `state/fitness_arms.json` are written by
    # tools/evolution_progress.py and tools/fitness_arm_liveness.py. Until this
    # block existed, each had exactly ONE reader: the tool that wrote it. They
    # were computed and thrown away -- the same defect the Intermesh audit
    # found in the natures, and the same one this file's own docstring cites
    # for the A/B reference. So "a run that promotes nothing scores
    # identically to a productive one" was TRUE, and the system could not
    # distinguish them.
    #
    # The gate is pinned on the loop's health the same way it is pinned on
    # schema and baseline: a cycle may not count toward promotion while the
    # system is measurably degraded. Read FRESH -- a stale file must not be
    # allowed to hold a healthy loop hostage, and a missing one must not read
    # as healthy either.
    _integrity, _integ_reason = _loop_integrity_gate()
    if _integ_reason:
        log('VETO (EV-7 loop integrity): %s' % _integ_reason)

    # ARMOR adaptive margin (2026-10-06 retrofit): the required margin
    # scales with measurement staleness. A margin recomputed on a reused
    # pool (_repeat>0, see stall check above) is weaker evidence, so it must
    # clear a HIGHER bar; a fresh-pool margin uses the base bar. This is the
    # adaptive trust region: uncertain measurements need more evidence.
    # streak/SPRT above keep the raw beats (evidence chain undisturbed);
    # only the final promotion decision uses the adaptive bar.
    try:
        _stale_cycles = min(int(_repeat), 4) if pool_reused else 0
    except Exception:
        _stale_cycles = 0
    _eff_margin = PROMOTE_MARGIN * (1.0 + 0.25 * _stale_cycles)
    _adaptive_ok = bool(margin_now >= _eff_margin)
    if not _adaptive_ok:
        log('VETO (adaptive margin): margin %s < effective bar %s '
            '(base %s x staleness %d)' % (
                margin_now, round(_eff_margin, 4), PROMOTE_MARGIN,
                _stale_cycles))

    if beats_anchor and _adaptive_ok and sprt_ok and pareto_ok and best and holdout.get('passed') \
            and oracle_gate.get('ok') and not _morph_blocks_promotion \
            and (not FITNESS_ARM_VETO or _arms_live) \
            and (not _integrity or not LOOP_INTEGRITY_VETO):
        try:
            if os.path.exists(PROMOTE_JSON):
                # Rotate backups depth-2 (followup #5): .prev -> .prev2,
                # current -> .prev.
                if os.path.exists(PREV_JSON):
                    with open(PREV_JSON, encoding='utf-8') as f:
                        prev_data = f.read()
                    with open(PREV2_JSON, 'w', encoding='utf-8') as f:
                        f.write(prev_data)
                with open(PROMOTE_JSON, encoding='utf-8') as f:
                    prev_data = f.read()
                with open(PREV_JSON, 'w', encoding='utf-8') as f:
                    f.write(prev_data)
            with open(PROMOTE_JSON, 'w', encoding='utf-8') as f:
                json.dump({
                    'genes': cand_genes,
                    'fitness': cand_total,
                    'fitness_detail': {'info': cand_info, 'alert': cand_alert},
                    # EV-5: arm liveness travels WITH the artifact. A promoted
                    # file that records alert=0.0 must also record that the arm
                    # was dead, so a reader never has to infer it.
                    'fitness_arms_live': _arms_live,
                    'fitness_dead_arms': _dead_arms,
                    # EV-4: the cross-arm bridge. `_c_tot` is the SAME base-4
                    # genome scored with the rotated gene at its neutral value,
                    # so it is comparable across arms on one scale. It was
                    # computed every cycle and logged, and never reached the
                    # artifact. Recording it is what makes an arm-vs-arm
                    # comparison possible after the fact.
                    'morph_reference_fitness': ((_morph_ab or {}).get(
                        'control_fitness')),
                    'morph_reference_delta': ((_morph_ab or {}).get('delta')),
                    'schema_hash': schema_hash(cand_genes),
                    'baseline': baseline_fit,
                    'holdout': holdout,
                    'oracle': oracle_gate,
                    'wins': streak,
                    'margin': margin_now,
                    'sprt_llr': sprt_llr,
                    # Traceability: if a trial gene were ever present here, the
                    # artifact says which one, so the measurement can be
                    # attributed rather than guessed at.
                    'proteus_morph_gene': (_morph_gene or {}).get('name'),
                    'pareto_source': pareto_gate.get('source', 'champion'),
                    'promoted_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                    'run_seed': seed,
                }, f, indent=2)
            promoted = True
            log('PROMOTED to %s (prev kept at %s, auto-canary armed)' % (
                PROMOTE_JSON, PREV_JSON))
            try:
                _write_canary(cand_genes, cand_total, round(fitness_genes(
                    _builtin_default_genes(), info_pool, alert_pool), 4))
            except Exception as e:
                log('canary arm failed: %s' % str(e)[:120])
        except Exception as e:
            log('promotion write failed: %s' % str(e)[:120])

    # EV-7 (2026-09-10): the engine advertises checkpoint save/restore but the
    # live loop never called it (checkpoint_saved = 17 rows total, none since
    # 2026-09-09), so continuity was dead code. Save once per cycle: one row
    # carrying the full population + best genome, restorable via
    # EvolutionEngine.load_checkpoint().
    try:
        _ck = evo.save_checkpoint('cycle-%s' % seed)
        log('checkpoint saved: gen %s best=%s' % (
            _ck.get('generation'), _ck.get('best_fitness')))
    except Exception as e:
        log('checkpoint save failed: %s' % str(e)[:120])

    # T-3 (2026-10-18): refresh the arm-liveness instrument every cycle.
    # state/fitness_arms.json was written ONLY by the standalone tool, so the
    # live loop rotted it (Oct 11 -> 595917s stale) and EV-7 read BLIND.
    # Fail-open: a liveness failure must never break the cycle.
    try:
        _tools = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'tools')
        if _tools not in sys.path:
            sys.path.insert(0, _tools)
        import fitness_arm_liveness as _fal
        _rep = _fal.analyse(_fal.parse(_fal.LOG))
        _rep['input'] = _fal._input_state(_fal.LOG)
        if _rep.get('verdict') != 'NO_DATA':
            with open(_fal.OUT, 'w', encoding='utf-8') as _fh:
                json.dump(_rep, _fh, indent=2, default=str)
        log('fitness_arms refreshed: %s' % _rep.get('verdict'))
    except Exception as e:
        log('fitness_arms refresh failed: %s' % str(e)[:120])

    # (NSGA-II refinement ran earlier, before the promotion decision —
    # followup #1, 2026-09-11. `pareto` is already populated above.)
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()
        dm.record('evolution_result', {
            'action': 'evolution run complete',
            'fitness': 'v3-dual-robust',
            'best_genes': best.genes if best else {},
            'best_total': best_total,
            'best_info': best_info,
            'best_alert': best_alert,
            'baseline': baseline_fit,
            'beats_baseline': beats,
            # Written next to `beats_baseline` on purpose: the two answer
            # different questions (did it clear the frozen gate vs did the
            # pipeline even run) and a reader must be able to tell them apart.
            'candidate_worthy': _candidate_worthy,
            'promoted': promoted,
            'generations': len(trajectory),
            'population': 24,
            'episodes': N_EPISODES,
            # Era stamp (see MARGIN_ESTIMATOR): without it a row is unlabelled
            # and cannot be compared with the margins the gate accumulates.
            'margin_estimator': MARGIN_ESTIMATOR,
            'seed': seed,
            'trajectory': trajectory,
            'trajectory_avg': trajectory_avg,
            'seeded_from_prior': bool(seed_genes),
            'meta_params': meta_params,
            'meta_candidate': meta_is_cand,
            'meta_msg': meta_msg,
            # G-7 steering point: active metacog strategy + the explore
            # probability it produced (joins strategy↔search analysis).
            'strategy': strategy,
            'explore_prob': _explore,
            'champion_reeval': champ_score,
            'baseline_hash': baseline_id,
            # EV-4 (2026-09-30): identity of the SEARCH SPACE (gene names),
            # not of this genome's values. The promotion streak is computed per
            # arm using this, because the morph rotates slot 5 every cycle and
            # consecutive cycles are therefore never the same measurement.
            'schema_hash': schema_hash(best.genes if best else seed_genes),
            'schema_gene': (_morph_gene or {}).get('name'),
            # EV-3 (2026-09-10): champion_genes records the WINNER. It used to
            # record the input seed, so last_best_genes() returned its own
            # input: seed(n+1) == seed(n), the chain was a fixed point, and
            # champion_reeval froze at 21.815 across 7 consecutive runs.
            'champion_genes': best.genes if best else {},
            'seed_genes': seed_genes,
            'pool_fingerprint': pool_fingerprint(),
            'pool_snapshot_reused': pool_reused,
            'pool_snapshot_age_s': pool_age_s,
            # Stall visibility (2026-09-12): true when this cycle re-measured
            # the previous cycles' margin on a frozen pool. Consumers must not
            # count these as independent observations.
            'margin_repeat_streak': _repeat,
            'non_informative': _non_informative,
            'pool_id': pool_id,
            'pareto': pareto,
            'pareto_gate': {k: v for k, v in (pareto_gate or {}).items()
                            if k != 'knee_genes'},
            'pareto_blocked_streak': pareto_blocked_streak,
            'promoted_source': pareto_gate.get('source', 'champion'),
            'holdout': holdout,
            'canary': canary_report,
            # v2.0 secondaries (2026-09-11): CMA arm + QD archive + TPE seeds.
            'cma_arm': cma_report,
            'qd_archive': qd_report,
            'tpe_seeds': len(_tpe),
            'search_arms': {'ga': 'EvolutionEngine/SBX+poly', 'cma': 'SepCMAES/2gens',
                            'qd': 'MAP-Elites/(info,alert)', 'suggest': 'TPE'},
            # SPRT gate state (2026-09-11, followup #2). `margin` is the raw
            # per-cycle effect that the sequential test accumulates, so it is
            # persisted explicitly (older rows only carry best_total/baseline).
            'margin': margin_now,
            'sprt': {'llr': sprt_llr, 'sigma': sprt_sigma, 'n': sprt_n,
                     'decision': sprt_decision, 'upper': round(SPRT_UPPER, 4),
                     'lower': round(SPRT_LOWER, 4),
                     # Re-measurements dropped because the pool was frozen.
                     # Without this the inflation is invisible after the fact.
                     'collapsed_duplicates': _sprt_dup},
            # PROTEUS Phase D/E (2026-09-12). Persisted so a batch can be
            # attributed: without these keys a Cranium rescue is
            # indistinguishable from a plain SPRT promote after the fact.
            'cranium': cranium,
            'circulation': _circ,
            'morph': {'cycle_index': _morph_index,
                      'gene': _morph_gene,
                      'dimension': len(best.genes) if best else 0,
                      # The attributable reading, recorded next to the
                      # comparison so a batch can be judged on the A/B rather
                      # than on the confounded fitness delta.
                       'ab': _morph_ab},
            # P0 tail-gate instrumentation (#985): per-row tail verdicts that
            # were previously blind (0/200). Additive only; no decision changed.
            'alert_pool_context': {
                'size': len(alert_pool or []),
                'info_size': len(info_pool or []),
                'mix': {s: sum(1 for d in (alert_pool or [])
                               if str(d.get('severity', '')).lower() == s)
                        for s in ('critical', 'error', 'warning')},
                'distinct_states': len({_state_key_for(d) for d in (alert_pool or [])}),
            },
            'tail_gates': {
                'morph_block': bool(_morph_blocks_promotion),
                'morph_block_reason': ('trial gene %s present; fitness not '
                                       'production-comparable'
                                       % ((_morph_gene or {}).get('name'))
                                       if _morph_blocks_promotion else None),
                'oracle_ok': bool((oracle_gate or {}).get('ok', True)),
                'oracle_skipped_reason': (oracle_gate or {}).get('skipped'),
                'integrity_ok': bool(_integrity),
                'integrity_reason': _integ_reason,
                'adaptive_margin_base': PROMOTE_MARGIN,
                'adaptive_margin_effective': round(_eff_margin, 4),
                'adaptive_ok': bool(_adaptive_ok),
                'margin_now': margin_now,
                'canary': canary_report if canary_report else None,
                # Fail-open aggregate (task #1059): wired trigger surface. True
                # unless a critical tail gate is explicitly False; missing/errored
                # gates default to allow, so a broken gate never blocks a
                # healthy promotion.
                'tail_gate_ok': bool(
                    (not _morph_blocks_promotion)
                    and bool((oracle_gate or {}).get('ok', True))
                    and bool(_integrity is False or not LOOP_INTEGRITY_VETO)
                    and bool(_adaptive_ok)),
            },
            # Promotion-input instrument (#965 follow-up): the full
            # conjunction at the promotion `if` (beats/beats_anchor/
            # _adaptive_ok/sprt_ok/pareto_ok/holdout/oracle/morph/arm-veto/
            # integrity-veto). tail_gates covers 4 of them; these are the
            # rest, so a green row that still does not promote names its
            # blocker. Additive only; no decision changed.
            'promotion_inputs': {
                'beats': bool(beats),
                'candidate_worthy': bool(_candidate_worthy),
                'beats_anchor': bool(beats_anchor),
                'sprt_ok': bool(sprt_ok),
                'pareto_ok': bool(pareto_ok),
                'arm_veto_cfg': bool(FITNESS_ARM_VETO),
                'arms_live': bool(_arms_live),
                'dead_arms': list(_dead_arms or []),
                'arm_veto_active': bool(FITNESS_ARM_VETO
                                       and not _arms_live),
                'integrity_veto_cfg': bool(LOOP_INTEGRITY_VETO),
                'integrity_veto_active': bool(_integrity
                                             and LOOP_INTEGRITY_VETO),
            },
            'git_sha': _git_sha(),
        }, tags=['evolution', 'artifact'])

        # --- PROTEUS Phase D/E feedback (2026-09-12) -----------------------
        # Credit the operator that actually ran. Outcome strings are weighted
        # by `reward_for_outcome`; a holdout pass is worth far more than
        # beating a reused snapshot because E9 showed a +3.3 snapshot margin
        # buying 0/4 holdout passes. Failures are swallowed: the cycle has
        # already produced its artifact.
        _outcome = ('holdout_pass' if (promoted and holdout.get('passed'))
                    else 'promoted' if promoted
                    else 'beat_baseline' if _beat_baseline_this_cycle
                    else 'flat')
        if PROTEUS_CIRCULATION and cma_report.get('ran'):
            try:
                from CORE.WORMS.expansion.proteus_hooks import circulation_update
                _upd = circulation_update(CIRCULATION_JSON,
                                          str(cma_report.get('arm')), _outcome)
                log('circulation feedback: arm=%s outcome=%s %s' % (
                    cma_report.get('arm'), _outcome, _upd))
            except Exception as e:
                log('circulation feedback failed: %s' % str(e)[:120])
        if PROTEUS_MORPH and _morph_index >= 0:
            try:
                from CORE.WORMS.expansion.proteus_hooks import (
                    morph_is_inert, morph_record)
                # 2026-09-12: a trial only measures its gene if it changes what
                # the trainer is called with. Measured on this ledger: 52 of 69
                # rows resolved to the trainer's OWN default, so
                # `fitness - baseline_fit` was reporting the search, not the
                # gene — and every one was retained. `morph_record` stamps the
                # verdict on the row either way; logging it here is what makes
                # a null cycle distinguishable from a real win in the cycle
                # log, which is where anyone actually looks first.
                if morph_is_inert(_morph_gene, (best.genes if best else {})):
                    log('morph: trial %s was INERT - its resolved value equals '
                        'the trainer default, so this row cannot attribute the '
                        'margin (fitness %.4f vs baseline %.4f)' % (
                            (_morph_gene or {}).get('name'), best_total,
                            baseline_fit))
                if _morph_ab and _morph_ab.get('delta') is not None \
                        and float(_morph_ab['delta']) <= 0.0:
                    log('morph: A/B says the trial gene did NOT pay for itself '
                        '(delta %s) - the fitness delta above is the search'
                        % _morph_ab['delta'])
                morph_record(MORPH_HISTORY, _morph_index, _morph_gene,
                             (best.genes if best else {}), best_total,
                             baseline_fit, ab=_morph_ab)
            except Exception as e:
                log('morph record failed: %s' % str(e)[:120])
        # Tensor genome (the deliverable): refresh the self-describing artifact
        # from the same history the oracle feed reads. Pure Python, so it runs
        # under 3.15t as well as the 3.15 GIL sidecar. Advisory: a failure
        # here must never abort a promotion cycle, so it is swallowed and logged.
        _tensor_genome_refresh()
        if promoted and best:
            dm.record('evolution_promotion', {
                'action': 'champion promoted to production defaults',
                'genes': best.genes,
                'fitness': best_total,
                'baseline': baseline_fit,
                'holdout': holdout,
                'wins': streak,
                'rollback': 'auto-canary armed; manual: restore %s' % PREV_JSON,
            }, tags=['evolution', 'promotion'])
        # Auto-thought (2026-09-10): the verdict becomes chain-of-thought
        # training data without human transcription. Concise reasoning +
        # outcome; DPO pairs it against other verdicts automatically.
        try:
            _margin = round((best_total - baseline_fit) if best else 0.0, 4)
            if promoted:
                _v, _why = 'PROMOTED', 'cleared streak+holdout, canary armed'
            elif holdout and not holdout.get('passed'):
                _v, _why = 'BLOCKED by holdout', 'frozen wins did not survive fresh pools'
            elif not _candidate_worthy:
                _v, _why = 'HELD', 'no candidate survived the search'
            else:
                _v, _why = ('EVIDENCE %d (llr %s)' % (sprt_n, sprt_llr),
                            'accumulating within pool era (SPRT %s)' % sprt_decision)
            dm.record_thought(
                task='evolution promotion verdict',
                reasoning=('%s: best %s vs baseline %s (margin %s) on pool era %s. %s.' % (
                    _v, best_total, baseline_fit, _margin, pool_id, _why)),
                actions=['evaluate', 'holdout' if holdout else 'streak', 'canary' if promoted else 'hold'],
                success=True,
                source='EvolutionCycle',
                tags=['evolution'],
                session_id='evolution-%s' % seed,
            )
        except Exception as e:
            log('auto-thought record failed: %s' % e)
    except Exception as e:
        log('artifact record failed: %s' % e)
    # Terminal line (2026-10-19): main() used to return silently, so a
    # finished early-converged cycle was indistinguishable from a kill.
    # Every exit path through here now says so with the run's numbers.
    try:
        log('cycle complete: seed=%s best=%s baseline=%s promoted=%s' % (
            seed, round(best_total, 4) if best else None,
            round(baseline_fit, 4), bool(promoted)))
    except Exception as _cc_exc:
        log('cycle-complete line failed: %s' % str(_cc_exc)[:100])
        _cycle_complete_unlogged = True


if __name__ == '__main__':
    # Scheduler-visible failure (2026-09-10): tracebacks used to go to
    # stdout, which the scheduler discards — the task read 0x0 while the
    # loop was dead (the NameError stall was caught only by log absence).
    # Fatals now land in the cycle log AND the exit code.
    try:
        _ap = argparse.ArgumentParser(
            description='Run one QD evolution cycle with optional operator overrides.')
        _ap.add_argument('--crossover-operator', choices=['sbx', 'uniform'],
                         help='crossover operator override (default: sbx)')
        _ap.add_argument('--mutation-operator', choices=['polynomial', 'gaussian', 'levy', 'uniform'],
                         help='mutation operator override (default: polynomial)')
        _ap.add_argument('--mutation-rate', type=float,
                         help='mutation rate override (default: from evolved_meta_params)')
        _ap.add_argument('--early-stop-patience', type=int,
                         help='early stop patience override (default: from evolved_meta_params)')
        _args = _ap.parse_args()
        main(crossover_operator=_args.crossover_operator,
             mutation_operator=_args.mutation_operator,
             mutation_rate=_args.mutation_rate,
             early_stop_patience=_args.early_stop_patience)
    except SystemExit:
        raise
    except BaseException:
        import traceback
        try:
            log('FATAL\n%s' % traceback.format_exc()[-3000:])
        except Exception:
            pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled
        sys.exit(1)
