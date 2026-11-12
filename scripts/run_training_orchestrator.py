#!/usr/bin/env python3
"""
run_training_orchestrator.py — Run TrainingOrchestrator once (for Task Scheduler).

Called by Windows Task Scheduler to execute one SB3 training run.
Records results to LocalMemoryStore and prints a summary.

With no --algo, the algorithm ROTATES across every track in ROTATION by
5-minute slot, so each trains on a staggered cadence (one run per slot, each
track every len(ROTATION)*5 minutes) without colliding.

Usage:
    python scripts/run_training_orchestrator.py
    python scripts/run_training_orchestrator.py --algo DQN --timesteps 2000 --source SystemZ
"""

import os
import random
import sys
import time
import argparse
import contextlib
from datetime import datetime
from pathlib import Path
import json as _json
import hashlib
import random as _random
import tempfile

# Add src/ to path
_src = str(Path(__file__).resolve().parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

# Sources that are pure training/bookkeeping noise — excluding them keeps the
# SB3 slice on real worm/production signals instead of the training loops'
# own recorded events (RLTrainLoop+ModelVersioner are ~70% of the store).
EXCLUDED_TRAINING_SOURCES = [
    "RLTrainLoop",
    "RLTrainLoopV2",
    "ModelVersioner",
    "GRPOTrainer",
    "GRPOTrainerV2",
    "EvolutionEngine",
    "sb3_agent",
    "TrainingOrchestrator",
    "ABTestRunner",
    "FleetTrainer",
]


# Track definitions (rotation order, reward sources, runners) live in
# scripts/track_registry.py — the single source of truth. They used to be
# duplicated here and in the meta_director pick list, and drifted.
#
# PPO/DQN/A2C/QRDQN/MaskablePPO run on the Discrete(3) record env;
# SAC/TD3/DDPG/TQC/TRPO/ARS on a continuous Box(3) variant; GRPO is the
# worm-selection policy trainer; DPO is preference learning over SB3 run
# outcomes. All share the same 5-minute rotation slot, so each trains once
# every len(ROTATION)*5 minutes.
try:
    from track_registry import ROTATION, REWARD_EVENTS as _TRACK_EVENTS
except Exception:  # pragma: no cover - registry unavailable, keep running
    ROTATION = ["PPO", "DQN", "A2C", "SAC", "TD3", "DDPG", "QRDQN",
                "TRPO", "TQC", "ARS", "MaskablePPO", "GRPO", "DPO", "OFFLINE",
                "RL", "DREAMTUNE", "WORMGA", "INTAKE", "CLUSTER", "ALLPAR"]
    _TRACK_EVENTS = {
        "GRPO": ("grpo_training_orchestrated", "eval_mean_reward", None),
        "DPO": ("dpo_training_orchestrated", "eval_mean_reward", None),
        "OFFLINE": ("offline_training_orchestrated", "eval_mean_reward", None),
        "INTAKE": ("intake_training_orchestrated", "eval_mean_reward", None),
        "CLUSTER": ("cluster_training_orchestrated", "eval_mean_reward", None),
        "DREAMTUNE": ("dreamtune_training_orchestrated", "eval_mean_reward", None),
        "RL": ("training_run_complete", "avg_reward", "ScheduledTrainingWorm"),
        "WORMGA": ("wormga_training_orchestrated", "eval_mean_reward", None),
        "ALLPAR": ("allpar_training_orchestrated", "eval_mean_reward", None),
    }


def _next_track_version(event_type: str) -> str:
    """Monotonic per-track version (2026-09-09 fix).

    v{history}/v{pairs}/v{episodes} counters reset every run (each
    scheduled run is a fresh process) and conflated data size with
    lineage. COUNT+1 of the track's own artifact rows is the honest
    lineage and survives across processes.
    """
    try:
        from CORE.local_memory import get_memory_store
        import sqlite3
        store = get_memory_store()
        db = getattr(store, "db_path", None)
        if db:
            conn = sqlite3.connect(str(db), timeout=30)
            try:
                n = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE event_type = ?",
                    (event_type,)).fetchone()[0]
            finally:
                conn.close()
            return f"v{n + 1}"
    except Exception:
        pass
    return "v1"


def _load_director():
    """Import meta_director (scripts/ sibling); None on any problem."""
    try:
        import meta_director as _md
        return _md
    except Exception:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import meta_director as _md
            return _md
        except Exception:
            return None


def _current_slot() -> int:
    """The 5-minute rotation slot.

    Shared by `rotated_algo` (the pick) and `_director_observe` (the reward
    fold) so a run's `last_slot`/cooldown is written against the slot it
    actually trained in. Recomputing the slot at observe time drifted the
    ledger whenever a run crossed a slot boundary (a 168s run can).
    """
    return int(time.time()) // 300


def rotated_algo(slot=None) -> str:
    """Pick the algorithm for the current 5-minute slot.

    Meta-director (2026-09-09): Thompson sampling over per-track win
    posteriors replaces fixed rotation — compute follows the tracks
    that beat their own medians. Slot-seeded, so concurrent launches in
    one slot agree and the monitor replicates the pick. Falls back to
    plain rotation if the ledger is unreadable.
    """
    slot = _current_slot() if slot is None else int(slot)
    md = _load_director()
    if md is not None:
        try:
            return md.pick(slot, md.load_ledger(ROTATION), ROTATION)
        except Exception:
            pass
    return ROTATION[slot % len(ROTATION)]


def _track_recent_rewards(algo: str, n: int = 20) -> list:
    """Last-n eval rewards for a track (scale is track-local by design)."""
    try:
        from CORE.local_memory import get_memory_store
        import json as _json
        store = get_memory_store()
        if algo in _TRACK_EVENTS:
            et, field, src = _TRACK_EVENTS[algo]
            rows = store.query_memories(event_type=et, limit=200)
            vals = []
            for r in rows:
                if src is not None and r.get("source") != src:
                    continue
                d = r.get("data", {})
                if isinstance(d, str):
                    try:
                        d = _json.loads(d)
                    except Exception:
                        continue
                v = d.get(field)
                if isinstance(v, (int, float)):
                    vals.append(float(v))
            return vals[:n]
        rows = store.query_memories(event_type="sb3_training_orchestrated",
                                    limit=500)
        vals = []
        for r in rows:
            d = r.get("data", {})
            if isinstance(d, str):
                try:
                    d = _json.loads(d)
                except Exception:
                    continue
            if d.get("algo") != algo:
                continue
            v = d.get("eval_mean_reward")
            if isinstance(v, (int, float)):
                vals.append(float(v))
            if len(vals) >= n:
                break
        return vals
    except Exception:
        return []


def _director_observe(algo: str, reward, slot=None) -> None:
    """Fold one run into the director ledger: 1 iff it beat the track's
    own median (scale-free), optimistic win on thin history.

    `slot` is the slot the run was *picked* in; it must be carried through,
    not recomputed (see _current_slot).
    """
    try:
        if not isinstance(reward, (int, float)):
            return
        md = _load_director()
        if md is None:
            return
        hist = _track_recent_rewards(algo, 20)
        won = True if len(hist) < 2 else bool(float(reward) > sorted(hist)[len(hist) // 2])
        md.observe(md.load_ledger(ROTATION), algo, won,
                   slot=_current_slot() if slot is None else int(slot))
    except Exception:
        pass


def _gen_run_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}"


def _regime() -> dict:
    """Regime snapshot for this run (STACK Phase 1 instruments).

    Stamped into every track artifact so a contextual router can learn
    regime -> reward. Fail-soft: {} when the store is unreachable.
    """
    try:
        from CORE.local_memory import track_regime
        return track_regime()
    except Exception:
        return {}


LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "training_orchestrator.log"


class _Tee:
    """Duplicate stdout to the run log (2026-09-08: the scheduled task
    captures no output, so runs were invisible — same pattern as
    dream_cycle's file log)."""

    def __init__(self, *files):
        self._files = files

    def write(self, s):
        for f in self._files:
            f.write(s)

    def flush(self):
        for f in self._files:
            f.flush()


def run_grpo_cycle() -> dict:
    """One GRPO policy update on the worm registry (worm-selection track).

    Generates a candidate group, scores it from historical worm execution,
    applies the group-normalized advantage update, and records an artifact
    with the same shape as the SB3 runs so the dashboard shows it in the
    same comparison. Reward scale differs (0..1 success-rate-ish, not the
    SB3 episode totals).
    """
    from CORE.training_intake import gather as _gather_intake
    from CORE.local_memory import get_memory_store
    from CORE.WORMS.expansion.trainers_v2 import create_trainer

    run_id = _gen_run_id("grpotrain")
    t0 = time.time()
    # Use new trainer with worm identity
    # create_trainer returns a ready GRPOTrainerV2 (not a context) — use directly.
    trainer = create_trainer(task_type="grpo", worm_id="orchestrator_grpo", task_id=f"grpo_{run_id}")
    task_context = {"task_type": "scheduled_training", "category": "utility"}
    group = trainer.generate_group(task_context, group_size=8)
    rewards = trainer.compute_group_rewards(group, task_context)
    trainer.update_policy(group, rewards, context_key="scheduled")
    # Training-structure accommodation (Oct-10): freeze an anchor snapshot
    # for EP360 and gather the unity-row intake for observability. Reward
    # shaping stays OFF (link_weight=0) pending coordinator approval.
    # Note: GRPOTrainerV2 doesn't have snapshot_anchor; using gather for intake stats
    try:
        from CORE.training_intake import gather as _gather_intake
        intake = _gather_intake()
    except Exception:
        intake = {"store_error": "intake-unavailable"}

    mean_r = sum(rewards) / len(rewards) if rewards else 0.0
    std_r = (sum((r - mean_r) ** 2 for r in rewards) / len(rewards)) ** 0.5 if rewards else 0.0
    best_worm = group[rewards.index(max(rewards))] if rewards else None
    duration_ms = round((time.time() - t0) * 1000, 2)

    data = {
        "run_id": run_id,
        "algo": "GRPO",
        "total_timesteps": 0,
        "records_used": len(group),
        "regime": _regime(),
        "data_source": "worm_registry",
        "model_version": _next_track_version("grpo_training_orchestrated"),
        "eval_mean_reward": round(mean_r, 4),
        "eval_std_reward": round(std_r, 4),
        "best_worm": best_worm,
        "best_reward": round(max(rewards), 4) if rewards else None,
        "duration_ms": duration_ms,
        "anchor_snapshots": anchor.get("n_anchors"),
        "intake_links": intake.get("n_link"),
        "intake_link_ok": intake.get("link_ok_rate"),
        "intake_rewards": intake.get("n_rewards"),
        "intake_cal_scored": (intake.get("calibration") or {}).get("n_scored"),
    }
    store = get_memory_store()
    store.record_event(
        source="TrainingOrchestrator", event_type="grpo_training_orchestrated",
        data=data, severity="info", tags=["grpo", "training", "orchestrated"],
    )
    return {"algo": "GRPO", "success": True, "records_used": len(group),
            "model_version": data["model_version"],
            "eval_mean_reward": data["eval_mean_reward"],
            "best_worm": best_worm, "duration_ms": duration_ms}


def run_offline_cycle() -> dict:
    """One offline-RL pass: Q-learning with a temporal train/holdout split.

    The learner never sees a live environment — it fits the policy from logged
    memories only, then scores generalization on the temporally-later holdout
    session (use_data_split + by_session prevents leakage). The reported reward
    is the HELD-OUT test_avg_reward, so a rising OFFLINE track means the
    logged data is teaching a policy that transfers to newer behavior.
    (Added 2026-09-08; same artifact shape as the other tracks.)
    """
    from CORE.local_memory import get_memory_store
    from CORE.WORMS.expansion.trainers_v2 import create_trainer

    run_id = _gen_run_id("offline")
    t0 = time.time()
    # Use new trainer with worm identity
    # create_trainer returns a ready RLTrainLoopV2 (not a context) — use directly.
    loop = create_trainer(task_type="offline", worm_id="orchestrator_offline", task_id=f"offline_{run_id}")
    result = loop.train_from_memory(
        n_episodes=80, max_steps=50,
        use_data_split=True, by_session=True,
        val_ratio=0.1, test_ratio=0.2,
        exclude_sources=EXCLUDED_TRAINING_SOURCES,
        persist=False)  # scored offline; nothing written back by the learner

    if "error" in result:
        raise RuntimeError(result["error"])

    test_avg = 0.0
    if result.get("test_metrics"):
        test_avg = float(result["test_metrics"].get("test_avg_reward", 0.0))
    split = result.get("data_split", {})
    train_recs = split.get("train_records", 0)
    test_recs = split.get("test_records", 0)
    duration_ms = round((time.time() - t0) * 1000, 2)

    data = {
        "run_id": run_id,
        "algo": "OFFLINE",
        "total_timesteps": 0,
        "records_used": train_recs,
        "regime": _regime(),
        "data_source": f"memory_split(train={train_recs},holdout={test_recs})",
        "model_version": _next_track_version("offline_training_orchestrated"),
        "eval_mean_reward": round(test_avg, 4),
        "eval_std_reward": 0.0,
        "train_avg_reward": round(float(result.get("avg_reward", 0.0)), 4),
        "holdout_records": test_recs,
        "duration_ms": duration_ms,
    }
    store = get_memory_store()
    store.record_event(
        source="TrainingOrchestrator", event_type="offline_training_orchestrated",
        data=data, severity="info", tags=["offline", "rl", "orchestrated"],
    )
    return {"algo": "OFFLINE", "success": True, "records_used": train_recs,
            "model_version": data["model_version"],
            "eval_mean_reward": data["eval_mean_reward"],
            "duration_ms": duration_ms}


def _estimator_arm(records):
    """ML/estimators comparison arm over a frozen trainer pool (gap-3 wire).

    Fits logistic_regression on SchemaAwareFeatureExtractor features+labels
    from the SAME frozen pool the Q-learner trained on and records train
    agreement beside the Q-policy metric. A single-class pool or any failure
    names itself in estimator_error instead of vanishing (rule 12: no silent
    arms) and never fabricates a score.
    """
    try:
        from CORE.WORMS.expansion.learning_features import (  # noqa: E402
            SchemaAwareFeatureExtractor as _Extractor)
        from ML.estimators import fit_score as _ml_fit_score  # noqa: E402
        _fx = _Extractor()
        _X, _y, _meta = _fx.extract_features_and_labels(records)
        if _X and _y and len(set(_y)) > 1:
            _, _agree = _ml_fit_score("logistic_regression", _X, _y)
            return {
                "estimator_family": "logistic_regression",
                "estimator_train_agreement": round(float(_agree), 4),
                "estimator_feature_dim": int((_meta or {}).get("dim", 0)),
            }
        return {
            "estimator_family": None,
            "estimator_error": ("single-class pool (%d rows): agreement "
                                 "undefined, arm skipped" % len(_y)),
        }
    except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
        return {"estimator_family": None,
                "estimator_error": "%s: %s"
                % (type(exc).__name__, exc)}


def run_intake_cycle() -> dict:
    """One INTAKE track run: Q-learning over the MEASURED technique outcomes.

    Closes item H-3 of `state/ACTION_REGISTRY_2026-10-10.md` and the gap in
    Engram #368/#770 ("harvested benefit unapplied: 1/33 in prod, zero trainer
    intake"). Two things were true of the `technique-link` stream, which already
    existed because every live hook call writes one: no trainer filtered on that
    source at all, and the row's verdict was only half-visible to the trainers'
    reward model -- `record_link()` declares `severity` (info for a success,
    warning for a failure) and puts the outcome in `data["ok"]`, while
    `learning_rl._compute_reward` reads a top-level `severity` PLUS the
    refinement `data["success"]`, which the row does not carry.
    `build_training_records()` is that translation (it preserves the producer's
    severity and adds only the missing field); this track is the production
    caller that turns it into an edge instead of an exercise.

    The learner is RLTrainLoop -- one of the four trainers #770 names -- driven
    through its own `records=` path, so the run measures the real trainer and the
    pool is frozen for the whole run (every episode sees identical data).
    `eval_mean_reward` is therefore POLICY AGREEMENT WITH MEASURED OUTCOMES: how
    well the current Q-policy's chosen action fits a technique that actually
    succeeded or failed. It is comparable across runs on the same pool, and it is
    not an environment return -- which is why a thin or unmeasured stream must
    not read as a score: a starved run reports success=False with
    records_used=0 and writes no artifact (the rule the other cycles follow).

    Persistence is left ON and the persisted Q-table is loaded first, so the arm
    accumulates a policy across runs rather than reporting the same number
    forever (a constant-valued arm is the dead-objective shape `learning_rl`
    already carries a lesson for). Its own rows are `RLTrainLoop`/
    `ModelVersioner` sourced, both already in EXCLUDED_TRAINING_SOURCES, so this
    track cannot feed its own output back into other tracks' pools.
"""
    from CORE.training_intake import build_training_records
    from CORE.local_memory import get_memory_store
    from CORE.WORMS.expansion.trainers_v2 import create_trainer

    run_id = _gen_run_id("intake")
    t0 = time.time()
    # 500 is the store's own per-query ceiling; asking for more only adds a
    # clamp warning to the run log.
    built = build_training_records(limit=500)
    records = built.get("records") or []
    if not records:
        return {
            "algo": "INTAKE", "success": False, "records_used": 0,
            "model_version": None, "eval_mean_reward": None,
            "duration_ms": round((time.time() - t0) * 1000, 2),
            "note": ("skipped: no MEASURED technique outcomes (%d rows seen, "
                      "%d unmeasured%s)"
                      % (built.get("n_rows", 0), built.get("n_unmeasured", 0),
                         ", error=%s" % (built.get("store_error")
                                          or built.get("read_error"))
                         if (built.get("store_error")
                             or built.get("read_error")) else "")),
        }

    # Use new trainer with worm identity
    # create_trainer returns a ready RLTrainLoopV2 (not a context) — use directly.
    loop = create_trainer(task_type="intake", worm_id="orchestrator_intake", task_id=f"intake_{run_id}")
    # Best-effort warm start: the run continues the persisted policy instead of
    # relearning the same pool from zero every slot. Returns False when there is
    # no table yet, which is a fresh start, not a failure.
    loaded = False
    try:
        loaded = bool(loop.load_q_table_from_store())
    except Exception as exc:  # noqa: BLE001 - a missing table is not a failure
        print(f"  [WARN] intake q-table load skipped: {exc}", flush=True)
    result = loop.train_from_memory(
        records=records, n_episodes=60,
        max_steps=min(50, len(records)),
        use_schema_features=False,
    )
    if "error" in result:
        raise RuntimeError(result["error"])

    # Gap-3 wire (Engram #1655): ML/estimators comparison arm (shared helper,
    # also used by run_cluster_cycle). ADDITIVE ONLY, never touches the
    # Q-policy, the pool, or any existing field.
    estimator_info = _estimator_arm(records)

    duration_ms = round((time.time() - t0) * 1000, 2)
    data = {
        "run_id": run_id,
        "algo": "INTAKE",
        "total_timesteps": 0,
        "records_used": len(records),
        "regime": _regime(),
        "data_source": f"memory:{built.get('source')}",
        "model_version": _next_track_version("intake_training_orchestrated"),
        "eval_mean_reward": round(float(result.get("avg_reward", 0.0)), 4),
        "eval_std_reward": 0.0,
        "q_table_size": int(result.get("q_table_size", 0)),
        "intake_ok_rate": built.get("ok_rate"),
        "intake_hooks": len(built.get("by_hook") or {}),
        "intake_unmeasured": built.get("n_unmeasured"),
        "warm_started": loaded,
        "duration_ms": duration_ms,
    }
    data.update(estimator_info)
    store = get_memory_store()
    store.record_event(
        source=f"RLTrainLoopV2[worm:orchestrator_intake]", event_type="intake_training_orchestrated",
        data=data, severity="info", tags=["intake", "technique", "training",
                                          "orchestrated", "worm:orchestrator_intake"],
    )
    return {"algo": "INTAKE", "success": True, "records_used": len(records),
            "model_version": data["model_version"],
            "eval_mean_reward": data["eval_mean_reward"],
            "duration_ms": duration_ms,
            "note": ("%d technique hooks, ok_rate=%s, unmeasured=%s"
                     % (data["intake_hooks"], data["intake_ok_rate"],
                        data["intake_unmeasured"]))}


def run_cluster_cycle() -> dict:
    """One CLUSTER track run: Q-learning over MEASURED cluster outcomes.

    Gap-4 resolve (Engram #1655): the reader half the H-3 producer
    shape-registration left open. Mirrors run_intake_cycle exactly -- same
    RLTrainLoop `records=` path, same frozen-pool discipline, same starve
    rule (empty pool reports success=False and writes nothing), same
    additive estimator arm. Output rows are TrainingOrchestrator-sourced
    (already in EXCLUDED_TRAINING_SOURCES) so no track feeds itself.
    learning_grpo's reward scoring FROM these rows is Beam turf and untouched.
"""
    from CORE.training_intake import build_cluster_records
    from CORE.local_memory import get_memory_store
    from CORE.WORMS.expansion.trainers_v2 import create_trainer

    run_id = _gen_run_id("cluster")
    t0 = time.time()
    built = build_cluster_records(limit=500)
    records = built.get("records") or []
    if not records:
        return {
            "algo": "CLUSTER", "success": False, "records_used": 0,
            "model_version": None, "eval_mean_reward": None,
            "duration_ms": round((time.time() - t0) * 1000, 2),
            "note": ("skipped: no MEASURED cluster outcomes (%d rows seen, "
                      "%d unmeasured%s)"
                      % (built.get("n_rows", 0), built.get("n_unmeasured", 0),
                         ", error=%s" % (built.get("store_error")
                                          or built.get("read_error"))
                         if (built.get("store_error")
                             or built.get("read_error")) else "")),
        }

    # Use new trainer with worm identity
    # create_trainer returns a ready RLTrainLoopV2 (not a context) — use directly.
    loop = create_trainer(task_type="offline", worm_id="orchestrator_cluster", task_id=f"cluster_{run_id}")
    loaded = False
    try:
        loaded = bool(loop.load_q_table_from_store())
    except Exception as exc:  # noqa: BLE001 - a missing table is not a failure
        print(f"  [WARN] cluster q-table load skipped: {exc}", flush=True)
    result = loop.train_from_memory(
        records=records, n_episodes=60,
        max_steps=min(50, len(records)),
        use_schema_features=False,
    )
    if "error" in result:
        raise RuntimeError(result["error"])

    estimator_info = _estimator_arm(records)
    duration_ms = round((time.time() - t0) * 1000, 2)
    data = {
        "run_id": run_id,
        "algo": "CLUSTER",
        "total_timesteps": 0,
        "records_used": len(records),
        "regime": _regime(),
        "data_source": f"memory:{built.get('source')}",
        "model_version": _next_track_version("cluster_training_orchestrated"),
        "eval_mean_reward": round(float(result.get("avg_reward", 0.0)), 4),
        "eval_std_reward": 0.0,
        "q_table_size": int(result.get("q_table_size", 0)),
        "cluster_ok_rate": built.get("ok_rate"),
        "cluster_hooks": len(built.get("by_hook") or {}),
        "cluster_unmeasured": built.get("n_unmeasured"),
        "warm_started": loaded,
        "duration_ms": duration_ms,
    }
    data.update(estimator_info)
    store = get_memory_store()
    store.record_event(
        source=f"RLTrainLoopV2[worm:orchestrator_cluster]", event_type="cluster_training_orchestrated",
        data=data, severity="info", tags=["cluster", "technique", "training",
                                          "orchestrated", "worm:orchestrator_cluster"],
    )
    return {"algo": "CLUSTER", "success": True, "records_used": len(records),
            "model_version": data["model_version"],
            "eval_mean_reward": data["eval_mean_reward"],
            "duration_ms": duration_ms,
            "note": ("%d cluster hooks, ok_rate=%s, unmeasured=%s"
                     % (data["cluster_hooks"], data["cluster_ok_rate"],
                        data["cluster_unmeasured"]))}


def _thought_family(task, words: int = 3) -> str:
    """Task family for DPO affinity pairing: first `words` lowercased words.

    Verb-led tasks cluster naturally ('fix hof ...' vs 'refactor hof'
    stay apart — different operations deserve different contrasts).
    """
    return " ".join(str(task or "").lower().split()[:words])


#: How many thought rows the DPO pairing scans. The original 120 was saturated
#: by ONE high-volume family (measured 2026-09-28: 139 of 147 rows were
#: "evolution promotion verdict" successes and there was exactly ONE failure in
#: the whole store), so no contrast could survive into the window. Widening it
#: is necessary but not sufficient -- which is why the census below is printed
#: on every skip instead of a bare "no contrast".
_THOUGHT_WINDOW = 600


def thought_contrast(rows):
    """The newest same-family success/failure contrast, plus a census.

    ``rows`` is ``[(mem_id, data_dict), ...]`` in NEWEST-FIRST order.

    Two tiers are tried, strictest first:

    * ``family`` -- identical first-3-word family (the original rule): the same
      operation on the same object.
    * ``verb``   -- identical first word: the same OPERATION on a different
      object. A strictly weaker signal, so the tier is RETURNED and recorded
      rather than silently averaged in with strong pairs.

    Returns ``{"pair": (ok, bad, family) | None, "tier": str, "census": {...}}``.
    The census is always returned so that "no pair" is diagnosable -- how many
    rows were scanned, how many families existed, how many had a contrast at
    all, and which families dominate the window.
    """
    ok3, bad3, ok1, bad1 = {}, {}, {}, {}
    counts = {}
    scanned = 0
    for mem_id, data in rows:
        if not isinstance(data, dict) or "success" not in data:
            continue
        task = str(data.get("task", "?"))
        fam3 = _thought_family(task)
        if not fam3:
            continue
        scanned += 1
        good = bool(data.get("success"))
        entry = (mem_id, task[:60])
        (ok3 if good else bad3).setdefault(fam3, entry)
        (ok1 if good else bad1).setdefault(_thought_family(task, 1), entry)
        slot = counts.setdefault(fam3, [0, 0])
        slot[0 if good else 1] += 1

    def _pick(ok, bad):
        # Dict order is insertion order, i.e. newest-first, so the FIRST family
        # with a contrast is the most recently failing one (unchanged intent).
        for fam, bad_entry in bad.items():
            if fam in ok:
                return (ok[fam], bad_entry, fam)
        return None

    pair = _pick(ok3, bad3)
    tier = "family" if pair else ""
    if pair is None:
        pair = _pick(ok1, bad1)
        tier = "verb" if pair else ""
    top = sorted(counts.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:3]
    return {
        "pair": pair,
        "tier": tier,
        "census": {
            "scanned": scanned,
            "families": len(counts),
            "families_with_both": sorted(set(ok3) & set(bad3)),
            "verb_families_with_both": sorted(set(ok1) & set(bad1)),
            "top_families": [{"family": f, "success": c[0], "fail": c[1]}
                             for f, c in top],
        },
    }


def run_dpo_cycle() -> dict:
    """One DPO preference-learning update from the latest SB3 run outcomes.

    Takes the highest- and lowest-reward recent SB3 runs as a chosen/rejected
    pair, records it with ABTestRunner, then computes the DPO loss over the
    accumulated preference dataset. Recorded under dpo_training_orchestrated.
    """
    from CORE.WORMS.expansion.learning_grpo import ABTestRunner
    from CORE.local_memory import get_memory_store
    from CORE.direct_memory import DirectMemory

    run_id = _gen_run_id("dpotrain")
    t0 = time.time()
    store = get_memory_store()
    runner = ABTestRunner()
    exp = "sb3_preferences"

    # Latest run per algo that has a reward — forms the chosen/rejected pair.
    latest = {}
    for r in store.query_memories(event_type="sb3_training_orchestrated", limit=60):
        d = r.get("data", {})
        if isinstance(d, str):
            try:
                d = _json.loads(d)
            except Exception:
                continue
        algo = d.get("algo")
        rw = d.get("eval_mean_reward")
        if algo and rw is not None and algo not in latest:
            latest[algo] = (float(rw), d)
    if len(latest) >= 2:
        ranked = sorted(latest.items(), key=lambda kv: kv[1][0])
        rejected, (rr, _) = ranked[0]
        chosen, (cr, _) = ranked[-1]
        if cr > rr:
            runner.record_preference(
                exp, chosen=chosen, rejected=rejected,
                context={"task_type": "scheduled_training"},
                chosen_reward=cr, rejected_reward=rr)

    # Chain-of-thought preferences (2026-09-10, affinity upgrade): pair
    # the newest successful thought trace against the newest failed one
    # FROM THE SAME TASK FAMILY. Cross-task contrasts are label noise —
    # a skipped pair beats a misleading one. Family = first 3 words of
    # the lowercased task (verb-led tasks cluster naturally); when no exact
    # family has a contrast a WEAKER same-verb tier is tried, and the tier is
    # recorded on the pair (`family_tier`) so a weak pair is never mistaken
    # for a strong one. The window size and a family census are printed on
    # every skip, so a stall is diagnosable instead of merely reported.
    thought_pairs = 0
    try:
        # fetch_thoughts pages past LocalMemoryStore's MAX_QUERY_LIMIT clamp.
        # A plain query_memories(limit=_THOUGHT_WINDOW) returns at most 500 and
        # drops the OLDEST rows — the minority families that carry the only
        # success/fail contrast — so the window widening below would have been
        # capped back to 500 anyway, silently.
        _rows = [(r.get("id"), r.get("data")) for r in
                 DirectMemory().fetch_thoughts(limit=_THOUGHT_WINDOW)]
        _sel = thought_contrast(_rows)
        _pair = _sel["pair"]
        if _pair:
            (_oid, _otask), (_bid, _btask), _fam = _pair
            runner.record_preference(
                "thought_preferences", chosen="thought#%s" % _oid,
                rejected="thought#%s" % _bid,
                context={"task_type": "chain_of_thought",
                         "family": _fam,
                         "family_tier": _sel["tier"],
                         "chosen_task": _otask, "rejected_task": _btask},
                chosen_reward=1.0, rejected_reward=0.0)
            thought_pairs = 1
        else:
            _c = _sel["census"]
            _top = ", ".join("%s (ok=%d fail=%d)"
                             % (t["family"] or "?", t["success"], t["fail"])
                             for t in _c["top_families"]) or "none"
            print("thought-preference pairing skipped: no same-family "
                  "success/fail contrast in %d thought rows (%d scanned, "
                  "%d families, %d with both). Biggest families: %s"
                  % (_THOUGHT_WINDOW, _c["scanned"], _c["families"],
                     len(_c["families_with_both"]), _top), flush=True)
    except Exception as e:
        print("thought-preference pairing skipped: %s" % str(e)[:120], flush=True)

    # Hydrate cumulative history (2026-09-10 fix): fresh processes start
    # with empty in-memory pairs, so without this the loss covers only
    # the current run's pairs. Load persisted rows first (both exps).
    runner.load_pairs(exp)
    runner.load_pairs("thought_preferences")
    result = runner.compute_dpo_loss(exp)
    pairs = runner.get_preference_pairs(exp, limit=1000)
    margins = [float(p.get("reward_margin", 0.0)) for p in pairs]
    mean_m = sum(margins) / len(margins) if margins else 0.0
    std_m = (sum((m - mean_m) ** 2 for m in margins) / len(margins)) ** 0.5 if margins else 0.0
    duration_ms = round((time.time() - t0) * 1000, 2)

    data = {
        "run_id": run_id,
        "algo": "DPO",
        "total_timesteps": 0,
        "records_used": len(pairs),
        "regime": _regime(),
        "data_source": exp,
        "model_version": _next_track_version("dpo_training_orchestrated"),
        "eval_mean_reward": round(mean_m, 4),
        "eval_std_reward": round(std_m, 4),
        "dpo_loss": result.get("loss"),
        "n_pairs": result.get("n_pairs", 0),
        "thought_pairs": len(runner.get_preference_pairs(
            "thought_preferences", limit=1000)),
        "duration_ms": duration_ms,
    }
    store.record_event(
        source="TrainingOrchestrator", event_type="dpo_training_orchestrated",
        data=data, severity="info", tags=["dpo", "training", "orchestrated"],
    )
    return {"algo": "DPO", "success": True, "records_used": len(pairs),
            "model_version": data["model_version"],
            "eval_mean_reward": data["eval_mean_reward"],
            "dpo_loss": data["dpo_loss"], "duration_ms": duration_ms}


def run_rl_cycle() -> dict:
    """One ScheduledTrainingWorm pipeline run (2026-09-09 wiring).

    This is the designated consumer of evolution's promoted
    hyperparameters: ScheduledTrainingWorm.__init__ loads
    config/evolved_rl_params.json via apply_evolved_overrides. The worm
    was never scheduled (last manual run 2026-09-07), so the RL loop
    evolution optimizes had been dead for 2 days. On the rotation it
    runs every len(ROTATION)*5 minutes; the plateau-skip inside
    execute() prevents spam once converged.
    """
    from CORE.WORMS.expansion.scheduling import ScheduledTrainingWorm
    t0 = time.time()
    worm = ScheduledTrainingWorm()
    run = worm.execute()
    duration_ms = round((time.time() - t0) * 1000, 2)
    try:
        from CORE.local_memory import track_regime
        _regime = track_regime()
    except Exception:
        _regime = {}
    return {"algo": "RL", "success": bool(getattr(run, "success", True)),
            "records_used": run.records_extracted,
            "model_version": run.model_version,
            "eval_mean_reward": round(run.avg_reward, 4),
            "regime": _regime,
            "duration_ms": duration_ms}


def run_dreamtune_cycle() -> dict:
    """One GA pass over dreaming hyperparameters (2026-09-09 track #16).

    Nothing tuned the dream generator itself (creativity_factor and
    scenario_count were hand-set constants), yet suggestion quality is
    what the policy bridge consumes. Small search, cheap live evals:
    population 8 x up to 4 generations, 2 salts x 2 episodes per genome,
    each scored on mean dream quality + mean top-suggestion confidence.
    ONE DreamingModule per run (frozen scenario set, like frozen eval
    pools); evals run persist=False so scoring never bloats dream
    history; RNG seeded per genome+salt for comparability. Uses
    GeneticOptimizer (fail-loud without fitness — supplied here).
    v1 scope: reports the best params; application (dream_cycle reading
    a tuned-creativity file) is a follow-up, like evolution's promotion.
    """
    import random as _random
    from CORE.WORMS.expansion.dreaming import DreamingModule
    from CORE.WORMS.expansion.evolution import GeneticOptimizer
    from CORE.local_memory import get_memory_store

    def _seed(genes, salt):
        s = salt + '|' + ','.join('%s=%.6f' % (k, float(genes[k])) for k in sorted(genes))
        return int(hashlib.sha256(s.encode('utf-8')).hexdigest()[:8], 16)

    t0 = time.time()
    run_id = _gen_run_id("dreamtune")
    dm = DreamingModule()
    # Frozen scenario set for the whole run (like frozen eval pools):
    # recall once so every genome is scored on identical scenarios.
    recalled = dm.recall_scenarios(limit=300, max_age_days=30)
    n_scenarios = recalled.get("recalled", 0)
    if n_scenarios == 0:
        duration_ms = round((time.time() - t0) * 1000, 2)
        return {"algo": "DREAMTUNE", "success": False, "records_used": 0,
                "model_version": _next_track_version("dreamtune_training_orchestrated"),
                "eval_mean_reward": 0.0, "duration_ms": duration_ms,
                "note": "skipped: no scenarios recalled"}

    def _fitness(genome):
        scores = []
        for salt in ('a', 'b'):
            _random.seed(_seed(genome.genes, salt))
            try:
                dreams = dm.dream(
                    num_episodes=2,
                    scenario_count=int(round(genome.genes.get('scenario_count', 20))),
                    creativity_factor=float(genome.genes.get('creativity_factor', 0.3)),
                    use_curative=True, persist=False)
            except Exception:
                scores.append(0.0)
                continue
            if not dreams:
                scores.append(0.0)
                continue
            quals, confs = [], []
            for dr in dreams:
                quals.append(float(getattr(dr, 'quality_score', 0.0) or 0.0))
                sug = [s.get('confidence', 0.0) for s in
                       (getattr(dr, 'policy_suggestions', None) or [])
                       if isinstance(s, dict)]
                sug.sort(reverse=True)
                confs.extend(sug[:2])
            q = sum(quals) / len(quals) if quals else 0.0
            c = sum(confs) / len(confs) if confs else 0.0
            scores.append(0.5 * q + 0.5 * c)
        return min(scores) if scores else 0.0

    opt = GeneticOptimizer()
    opt.initialize("DreamingModule",
                   {"creativity_factor": (0.05, 0.9),
                    "scenario_count": (10, 60)},
                   population_size=8)
    res = opt.optimize("DreamingModule", generations=4, fitness_fn=_fitness)
    duration_ms = round((time.time() - t0) * 1000, 2)

    data = {
        "run_id": run_id,
        "algo": "DREAMTUNE",
        "total_timesteps": 0,
        "records_used": n_scenarios,
        "regime": _regime(),
        "data_source": "dream_scenarios",
        "model_version": _next_track_version("dreamtune_training_orchestrated"),
        "eval_mean_reward": round(float(res.get("best_fitness", 0.0)), 4),
        "best_params": res.get("best_parameters", {}),
        "generations": res.get("generations", 0),
        "duration_ms": duration_ms,
    }
    store = get_memory_store()
    store.record_event(
        source="TrainingOrchestrator", event_type="dreamtune_training_orchestrated",
        data=data, severity="info", tags=["dreamtune", "training", "orchestrated"],
    )
    # Publish tuned params for dream_cycle.py (2026-09-09 apply step).
    # Precedence at apply time: explicit tuned file < metacog steering
    # (the pipeline's internal steer_by_strategy still wins when active).
    try:
        from pathlib import Path as _Path
        import json as _json
        _tuned = _Path(__file__).resolve().parent.parent / "config" / "tuned_creativity.json"
        _tmp = _tuned.with_suffix(".json.tmp")
        _tmp.write_text(_json.dumps({
            "creativity_factor": float(res.get("best_parameters", {}).get(
                "creativity_factor", 0.3)),
            "scenario_count": int(round(float(res.get("best_parameters", {}).get(
                "scenario_count", 20)))),
            "fitness": data["eval_mean_reward"],
            "run_id": run_id,
            "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, indent=2), encoding="utf-8")
        os.replace(_tmp, _tuned)
    except Exception:
        pass
    return {"algo": "DREAMTUNE", "success": True,
            "records_used": data["records_used"],
            "model_version": data["model_version"],
            "eval_mean_reward": data["eval_mean_reward"],
            "duration_ms": duration_ms}


# Safe read-only worms for live micro-evals (status/summary/uptime only —
# never anything that writes, moves, or deletes).
WORMGA_WORMS = ("SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ", "CLOCKZ", "HEALTHZ")


def _wormga_probe(worm, timeout_s=10.0):
    """Run one safe worm once, daemon-guarded. Returns (success, ms, error).

    _legacy_execute has no timeout of its own, so it runs on a daemon
    thread: a hung worm is recorded as a timeout failure and can never
    hang the track (daemons don't block process exit).
    """
    import threading
    box = {}
    try:
        from CORE.WORMS.CLUSTERWORMS import _legacy_execute
    except Exception as e:
        return False, 0.0, "unresolvable: %s" % str(e)[:100]

    def _run():
        t0 = time.time()
        try:
            out = _legacy_execute(worm)
            box["ok"] = out is not None
            box["ms"] = (time.time() - t0) * 1000.0
            box["err"] = None if out is not None else "unresolvable"
        except Exception as e:
            box["ok"] = False
            box["ms"] = (time.time() - t0) * 1000.0
            box["err"] = str(e)[:200]

    th = threading.Thread(target=_run, daemon=True,
                          name="wormga-probe-%s" % worm)
    th.start()
    th.join(timeout=timeout_s)
    if th.is_alive():
        return False, timeout_s * 1000.0, "probe-timeout"
    return (box.get("ok", False), round(box.get("ms", 0.0), 2),
            box.get("err"))


def run_wormga_cycle() -> dict:
    """One per-worm execution-policy GA pass (2026-09-09 track #17).

    Genome: shared {timeout_ms, max_retries} applied to the safe-worm
    set. Fitness (live, causal): success_rate - 0.2 * normalized time,
    min over 2 reps. Micro-evals write NO rows (persist=False doctrine —
    like evolution/ dreamtune evals); the single artifact below carries
    the reliability leaderboard every run, so the track is observably
    useful even when the landscape is flat (healthy machine: everything
    succeeds and the GA honestly converges immediately).
    Retries apply per worm on failure inside one rep.
    """
    from CORE.WORMS.expansion.evolution import GeneticOptimizer
    from CORE.local_memory import get_memory_store

    t0 = time.time()
    run_id = _gen_run_id("wormga")
    samples = []  # (worm, ok, ms) across the whole run -> leaderboard

    def _fitness(genome):
        timeout_ms = max(500, min(15000, float(genome.genes.get('timeout_ms', 5000))))
        retries = int(round(max(0, min(3, float(genome.genes.get('max_retries', 1))))))
        scores = []
        for _rep in range(2):
            oks, mss = 0, []
            for w in WORMGA_WORMS:
                ok, ms, _err = _wormga_probe(w, timeout_s=timeout_ms / 1000.0 + 2.0)
                for _r in range(retries):
                    if ok:
                        break
                    ok, ms, _err = _wormga_probe(w, timeout_s=timeout_ms / 1000.0 + 2.0)
                oks += 1 if ok else 0
                mss.append(min(ms, timeout_ms))
                samples.append((w, ok, ms))
            succ = oks / len(WORMGA_WORMS)
            t_norm = (sum(mss) / len(mss)) / timeout_ms if mss else 1.0
            scores.append(succ - 0.2 * min(1.0, t_norm))
        return min(scores) if scores else 0.0

    opt = GeneticOptimizer()
    opt.initialize("ClusterWormPolicy",
                   {"timeout_ms": (500, 15000), "max_retries": (0, 3)},
                   population_size=6)
    res = opt.optimize("ClusterWormPolicy", generations=3, fitness_fn=_fitness)
    duration_ms = round((time.time() - t0) * 1000, 2)

    board = {}
    for w, ok, ms in samples:
        b = board.setdefault(w, {"n": 0, "ok": 0, "ms": 0.0})
        b["n"] += 1
        b["ok"] += 1 if ok else 0
        b["ms"] += ms
    leaderboard = {w: {"success_rate": round(v["ok"] / v["n"], 3),
                       "avg_ms": round(v["ms"] / v["n"], 1),
                       "n": v["n"]} for w, v in board.items()}

    data = {
        "run_id": run_id,
        "algo": "WORMGA",
        "total_timesteps": 0,
        "records_used": len(samples),
        "regime": _regime(),
        "data_source": "live_microevals",
        "model_version": _next_track_version("wormga_training_orchestrated"),
        "eval_mean_reward": round(float(res.get("best_fitness", 0.0)), 4),
        "best_params": res.get("best_parameters", {}),
        "generations": res.get("generations", 0),
        "leaderboard": leaderboard,
        "duration_ms": duration_ms,
    }
    store = get_memory_store()
    store.record_event(
        source="TrainingOrchestrator", event_type="wormga_training_orchestrated",
        data=data, severity="info", tags=["wormga", "training", "orchestrated"],
    )
    return {"algo": "WORMGA", "success": True,
            "records_used": data["records_used"],
            "model_version": data["model_version"],
            "eval_mean_reward": data["eval_mean_reward"],
            "duration_ms": duration_ms}


def run_all_parallel_cycle() -> dict:
    """One NeuralPipeline "ALL" run — the former shadow chain, now one track.

    Runs SB3 + GRPO + QLEARN in parallel through CORE/NeuralPipeline and records
    the same-shaped artifact as the other tracks, so it is directed by the same
    bandit and scored by the same reward path instead of being a second
    orchestrator with its own (broken) result assembly.
    """
    from CORE.NeuralPipeline import NeuralPipeline
    from CORE.local_memory import get_memory_store

    t0 = time.time()
    run_id = _gen_run_id("allpar")
    pipeline = NeuralPipeline(max_workers=3, llm_augmentation=False,
                              use_cutting_edge=NeuralPipeline.opt_in_from_env())
    try:
        # source=None = all memories. Hardcoding "SystemZ" (as the NeuralPipeline
        # example does) matched zero rows here, so the track could never train.
        result = pipeline.run_end_to_end(
            source=None, event_type=None, agent_type="ALL",
            limit=200, register_model=False, use_data_split=True)
    except Exception as e:  # the pipeline records its own DLQ entry; be explicit
        duration_ms = round((time.time() - t0) * 1000, 2)
        return {"algo": "ALLPAR", "success": False, "records_used": 0,
                "model_version": "n/a", "eval_mean_reward": 0.0,
                "duration_ms": duration_ms, "error": str(e)[:200]}

    success_count = int(result.get("success_count", 0) or 0)
    ok = result.get("status") == "success" and success_count > 0
    duration_ms = round((time.time() - t0) * 1000, 2)

    data = {
        "run_id": run_id,
        "algo": "ALLPAR",
        "total_timesteps": 0,
        "records_used": int(result.get("train_records", 0) or 0),
        "regime": _regime(),
        "data_source": "neural_pipeline_all",
        "model_version": _next_track_version("allpar_training_orchestrated"),
        # Scalar, bandit-comparable: how many of the 3 agents trained. The
        # director's reward is median-relative, so any stable scalar works.
        "eval_mean_reward": float(success_count),
        "agents_trained": result.get("agents_trained", []),
        "duration_ms": duration_ms,
    }
    store = get_memory_store()
    store.record_event(
        source="TrainingOrchestrator", event_type="allpar_training_orchestrated",
        data=data, severity="info",
        tags=["allpar", "neural_pipeline", "orchestrated"],
    )
    return {"algo": "ALLPAR", "success": ok, "records_used": data["records_used"],
            "model_version": data["model_version"],
            "eval_mean_reward": data["eval_mean_reward"],
            "agents_trained": data["agents_trained"],
            "duration_ms": duration_ms}


def main():
    """Tee all run output to logs/training_orchestrator.log, then run."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as lf:
        with contextlib.redirect_stdout(_Tee(sys.stdout, lf)):
            # Scheduler-visible failure (2026-09-10): an unhandled
            # traceback inside a track used to vanish (scheduler captures
            # no output and the log only got prints). Fatals now land in
            # the log with a FATAL banner and a nonzero exit.
            try:
                return _run_impl()
            except SystemExit as e:
                print("STATUS: EXIT %s" % e.code, flush=True)
                raise
            except BaseException:
                import traceback
                print("FATAL\n%s" % traceback.format_exc()[-3000:], flush=True)
                print("STATUS: ORCHESTRATOR FATAL", flush=True)
                return 4


def _run_impl():
    parser = argparse.ArgumentParser(
        description="Run TrainingOrchestrator once (for Task Scheduler)",
    )
    parser.add_argument("--algo", default=None, choices=ROTATION,
                        help="Algorithm (default: rotate through %s by slot)" % "/".join(ROTATION))
    parser.add_argument("--timesteps", type=int, default=5000,
                        help="Total training timesteps")
    parser.add_argument("--source", default=None,
                        help="Data source filter (default: all)")
    parser.add_argument("--interval", type=float, default=24.0,
                        help="Scheduling interval in hours (default: 24)")

    args = parser.parse_args()

    # Single-instance guard (2026-09-10): each algo must complete its
    # per-cycle chain before the next cycle starts. The 5-min scheduler
    # allows overlaps ("Stop If Still Running: Disabled"); this
    # non-blocking lock serializes runs instead. Held open for process
    # life; OS releases on death.
    import msvcrt
    try:
        _lock_fh = open(os.path.join(tempfile.gettempdir(),
                                     'training_orchestrator.lock'), 'w', encoding='utf-8')
        msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, IOError):
        # Overlap is an honest skip, not a success: the chain carves out
        # rc=1 as skipped-ok, while rc=0 records a training that never ran
        # (measured 2026-09-28: FullPass-era logs full of phantom "ok"s from
        # exactly this path colliding with the 900s chain train job).
        print('SKIP overlap: previous training run still running', flush=True)
        return 1

    slot = _current_slot()
    algo = args.algo or rotated_algo(slot)
    print(f"Rotation pick: algo={algo} (explicit override: {bool(args.algo)})",
          flush=True)

    # Warm registry for accurate data (needed by GRPO's worm selection)
    print("Warming worm registry...", flush=True)
    try:
        from CORE.worm_registry import discover_worms
        n = discover_worms()
        print(f"  {n} worms registered", flush=True)
    except Exception as e:
        print(f"  [WARN] Registry warm failed: {e}", flush=True)

    # Non-SB3 tracks: GRPO worm-selection, DPO preference learning, OFFLINE
    # holdout, INTAKE technique-outcome intake, RL scheduled pipeline,
    # DREAMTUNE dream-GA, WORMGA per-worm execution-policy GA, and ALLPAR
    # (the folded-in parallel ensemble).
    cycle_fns = {
        "GRPO": run_grpo_cycle,
        "DPO": run_dpo_cycle,
        "OFFLINE": run_offline_cycle,
        "INTAKE": run_intake_cycle,
        "CLUSTER": run_cluster_cycle,
        "RL": run_rl_cycle,
        "DREAMTUNE": run_dreamtune_cycle,
        "WORMGA": run_wormga_cycle,
        "ALLPAR": run_all_parallel_cycle,
    }
    if algo in cycle_fns:
        fn = cycle_fns[algo]
        try:
            r = fn()
        except Exception as e:
            print(f"STATUS: {algo} CYCLE FAILED — {e}", flush=True)
            return 3
        print("=" * 60)
        print(f"  TrainingOrchestrator — {algo} Run Complete")
        print("=" * 60)
        print(f"  Algorithm:    {r['algo']}")
        print(f"  Records used: {r['records_used']}")
        print(f"  Model ver:    {r.get('model_version') or 'N/A'}")
        print(f"  Eval reward:  {r.get('eval_mean_reward')}")
        if r.get('dpo_loss') is not None:
            print(f"  DPO loss:     {r.get('dpo_loss')}")
        if r.get('best_worm'):
            print(f"  Best worm:    {r.get('best_worm')}")
        print(f"  Duration:     {r.get('duration_ms', 0):.0f}ms")
        if r.get("note"):
            print(f"  Note:         {r.get('note')}")
        if r.get("error"):
            print(f"  Error:        {r.get('error')}")
        print("=" * 60)
        ok = bool(r.get("success", True))
        if ok and not r.get("records_used"):
            # Zero-record "success" (2026-09-28 audit): GRPO/DPO/OFFLINE have
            # no empty-input guards, so a data-starved run reports success
            # with reward 0.0 -- the worm layer's honesty protocol forbids
            # exactly this (a skip is skipped, never a zero-record win).
            # Demote to skip: no director fold, exit 1 like any skip.
            print("STATUS: %s SKIPPED (0 records -- no training signal)"
                  % algo, flush=True)
            return 1
        print("STATUS: %s TRAINING %s"
              % (algo, "SUCCESSFUL" if ok else "SKIPPED/FAILED"), flush=True)
        # Only a real run carries a performance signal; a skip or a failed run
        # must not be folded into the director as a win (WORMGA/RL did exactly
        # that before, reporting SUCCESSFUL on success=False and reward=0.0).
        if ok:
            _director_observe(algo, r.get("eval_mean_reward"), slot)
        return 0 if ok else 1

    print(f"Starting TrainingOrchestrator (algo={algo}, "
          f"timesteps={args.timesteps}, source={args.source or 'all'})...",
          flush=True)

    try:
        from CORE.WORMS.expansion.scheduling import TrainingOrchestrator, OrchestratorConfig

        config = OrchestratorConfig(
            algo=algo,
            total_timesteps=args.timesteps,
            source_filter=args.source,
            interval_hours=args.interval,
            record_to_memory=True,
            register_model=True,
            exclude_sources=EXCLUDED_TRAINING_SOURCES,
        )
        orch = TrainingOrchestrator(config)

        t0 = time.time()
        result = orch.execute()
        elapsed = time.time() - t0

        print("=" * 60)
        print(f"  TrainingOrchestrator — Run Complete")
        print("=" * 60)
        print(f"  Run ID:       {result.run_id}")
        print(f"  Algorithm:    {result.algo}")
        print(f"  Trained:      {result.trained}")
        print(f"  Timesteps:    {result.total_timesteps}")
        print(f"  Records used: {result.records_used}")
        print(f"  Data source:  {result.data_source}")
        print(f"  Model ver:    {result.model_version or 'N/A'}")
        if result.eval_mean_reward is not None:
            print(f"  Eval reward:  {result.eval_mean_reward} ± {result.eval_std_reward}")
        print(f"  Duration:     {result.duration_ms:.0f}ms ({elapsed:.1f}s)")
        if result.error:
            print(f"  Error:        {result.error}")
        print("=" * 60)

        # Return exit code based on success
        if result.trained:
            print("STATUS: TRAINING SUCCESSFUL", flush=True)
            _director_observe(algo, result.eval_mean_reward, slot)
            return 0
        elif result.error:
            print(f"STATUS: TRAINING FAILED — {result.error}", flush=True)
            return 2
        else:
            print("STATUS: TRAINING SKIPPED (no data or SB3 not available)", flush=True)
            return 1

    except Exception as e:
        print(f"STATUS: CRITICAL ERROR — {e}", flush=True)
        return 3


if __name__ == "__main__":
    sys.exit(main())
