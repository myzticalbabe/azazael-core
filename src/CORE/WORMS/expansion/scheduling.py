# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: SCHEDULING (1 worm)
ScheduledTrainingWorm — runs DataPipelineWorm -> RLTrainLoop -> ModelVersioner on schedule

Records training results to LocalMemoryStore for observability.
Supports daily, hourly, or interval-based scheduling with configurable max duration.
"""

import json
import time
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import sysconfig

# -- Failure ledger ---------------------------------------------------
#: Swallows recorded when the failure ledger is unavailable (it is a
#: stdlib-only leaf, so this fallback should never fire -- if it does,
#: the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("scheduling:L21", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded
    _FREETHREAD = True
except ImportError as exc:
    note_failure("scheduling:L30", exc)
    _FREETHREAD = False
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("scheduling:L36", exc)
            return False


# -- Allocation policy (EP187) ----------------------------------------
#: Resolved once at import, not inside the two methods that use it: a
#: function-level import is a counted anti-pattern here
#: (`import_inside_function`), and the ratchet rejects an increase in it.
#: `allocation.py` imports only stdlib plus the failure ledger, so this
#: direction is one-way -- it never imports this module, so there is no cycle.
try:
    from CORE.WORMS.expansion.allocation import (
        ThompsonAllocator, reward_for_outcome)
except ImportError as exc:
    # Both callers below test these for None; no availability flag is set,
    # because a flag nothing reads is itself the defect this sweep removes.
    note_failure("scheduling:allocation-import", exc)
    ThompsonAllocator = None
    reward_for_outcome = None


# G-3 (2026-09-11): single tuning/deployment horizon. Evolution evaluated
# candidates at N_EPISODES=80 while production trained at n_episodes=50 —
# horizon-sensitive genes (discount, epsilon_decay) were tuned on one
# horizon and deployed on another. Both now share EVAL_EPISODES.
EVAL_EPISODES = 80


@dataclass
class ScheduleConfig:
    """Configuration for a scheduled training run."""
    interval_hours: float = 24.0        # How often to run
    max_duration_minutes: float = 30.0   # Max training duration
    n_episodes: int = EVAL_EPISODES      # RL episodes per run (= tuning horizon)
    data_limit: int = 200                # Records to extract
    learning_rate: float = 0.29      # Evolved 2026-09-07 (was 0.1)
    discount_factor: float = 0.91    # Evolved 2026-09-07 (was 0.9)
    epsilon: float = 0.11            # Evolved 2026-09-07 (was 0.2)
    epsilon_decay: float = 0.0       # Evolved gene (2026-09-08): annealing speed
    source_filter: (str | None) = None  # None = all sources
    record_to_memory: bool = True        # Persist results
    exclude_sources: (list[str] | None) = None  # Skip self-generated bookkeeping sources
    tune_before_train: bool = False  # Opt-in: Stage 3 runs RLTrainLoop.tune_and_train
    # (CV over lr/gamma/eps, then final train on best) instead of a single
    # train_from_memory. Default False preserves the scheduled cost profile;
    # set True when a tuning pass is wanted before the scheduled train.
    # EP187 contribution-weighted scheduling: arm name -> contribution score.
    # None (the default) keeps the flat `interval_hours` above; supplying weights
    # is what puts the allocation policy, not the constant, in charge of *when*
    # to train. `contribution_arm` names the arm this schedule follows.
    contribution_weights: (dict[str, float] | None) = None
    contribution_arm: (str | None) = None

    @classmethod
    def evolved_param_paths(cls):
        """Promotion files production may adopt, most-preferred first.

        Mirrors the locations ``apply_evolved_overrides`` reads: the untracked
        runtime state file first, the legacy tracked config as fallback.
        (Added 2026-10-16: ``evolution_audit._champion_in_use`` called this
        and it did not exist, so every audit crashed with AttributeError.)
        """
        _root = (Path(__file__).resolve().parent.parent.parent.parent.parent)
        return [_root / "state" / "evolved_rl_params.json",
                _root / "config" / "evolved_rl_params.json"]

    def apply_evolved_overrides(self) -> bool:
        """Load champion genes promoted by evolution_cycle.py, if present.

        Reads state/evolved_rl_params.json (written after 3 consecutive
        wins over the production baseline). Range-validates before
        applying; falls back to built-in defaults on any problem.
        Rollback = delete the JSON file. Returns True if overrides applied.
        """
        try:
            import json as _json
            from pathlib import Path as _Path
            # Resolve to <project-root>/state/evolved_rl_params.json — the
            # same location evolution_cycle.py writes to. This file sits
            # 5 dirs below the root (src/CORE/WORMS/expansion/), so five
            # .parent hops, not four (path mismatch bug, 2026-09-08).
            p = (_Path(__file__).resolve().parent.parent.parent.parent.parent
                 / "state" / "evolved_rl_params.json")
            # EV-1 (EVOLUTION_PROCESS_AUDIT_20260910, D-1): prefer the untracked
            # runtime location and only fall back to the legacy tracked
            # config/ path for reading, so a repo git restore/checkout can no
            # longer silently revert production hyperparameters.
            if not p.exists():
                _legacy = (_Path(__file__).resolve()
                           .parent.parent.parent.parent.parent
                           / "config" / "evolved_rl_params.json")
                if _legacy.exists():
                    p = _legacy
            if not p.exists():
                return False
            data = _json.loads(p.read_text(encoding="utf-8"))
            g = {k: float(data["genes"][k])
                 for k in ("learning_rate", "epsilon", "discount")}
            if not (0.01 <= g["learning_rate"] <= 0.5
                    and 0.0 <= g["epsilon"] <= 0.5
                    and 0.8 <= g["discount"] <= 0.999):
                return False
            self.learning_rate = g["learning_rate"]
            self.epsilon = g["epsilon"]
            self.discount_factor = g["discount"]
            # Evolved gene (2026-09-08): exploration-annealing speed.
            # Absent from older promotion files -> keep built-in default.
            if "epsilon_decay" in data.get("genes", {}):
                dec = float(data["genes"]["epsilon_decay"])
                self.epsilon_decay = max(0.0, min(dec, 0.2))
            return True
        except Exception as exc:
            note_failure("scheduling:L107", exc)
            return False


@dataclass
class TrainingRun:
    """Record of a single training run.

    Honesty protocol (2026-09-11 audit): a run that did no training must
    never look like a successful one. Plateau/data skips set
    skipped=True (and success=False); synthetic-fallback runs set
    synthetic=True so ledgers and dashboards can exclude them.
    """
    run_id: str
    started_at: str
    completed_at: str
    duration_ms: float
    records_extracted: int
    n_episodes: int
    avg_reward: float
    q_table_size: int
    model_version: str
    success: bool
    error: (str | None) = None
    skipped: bool = False
    skip_reason: (str | None) = None
    synthetic: bool = False


# -- EP252 Drop-Stable-Rampup: schedule the training SET, not just the clock -----
# `ScheduledTrainingWorm` decided *when* to train; the size of the training set
# was a constant (`ScheduleConfig.data_limit`). Drop-Stable-Rampup schedules the
# size from what the runs actually did: grow it while the reward series holds
# (loss stable), back it off when a run destabilises (loss spike). Bounds are
# module constants so the policy is arguable in one place.
RAMPUP_MIN_LIMIT = 25         # below this the run has too little data to learn from
RAMPUP_MAX_LIMIT = 800        # extraction ceiling this pipeline is known to carry
RAMPUP_WINDOW = 3             # runs compared (needs >= 2 to have any change at all)
RAMPUP_STABILITY_TOLERANCE = 0.05   # |relative change| within this = still stable
RAMPUP_DROP_TOLERANCE = 0.10        # a last-step decline past this = destabilised
RAMPUP_GROWTH_FACTOR = 1.5
RAMPUP_BACKOFF_FACTOR = 0.5


def _relative_change(previous: Any, current: Any) -> float:
    """Relative change between two rewards; 0.0 when `previous` is 0.

    Dividing by a zero baseline raises ZeroDivisionError, and a zero *is* a real
    observation (a scheduled run that learned nothing) -- so it must not crash the
    scheduler. A zero baseline carries no relative information, so it reports no
    change rather than an infinite one.
    """
    if previous == 0:
        return 0.0
    return (current - previous) / abs(previous)


def rampup_data_limit(reward_history: List[Any],
                      base_limit: int,
                      *,
                      min_limit: int = RAMPUP_MIN_LIMIT,
                      max_limit: int = RAMPUP_MAX_LIMIT,
                      window: int = RAMPUP_WINDOW,
                      stability_tolerance: float = RAMPUP_STABILITY_TOLERANCE,
                      drop_tolerance: float = RAMPUP_DROP_TOLERANCE,
                      growth_factor: float = RAMPUP_GROWTH_FACTOR,
                      backoff_factor: float = RAMPUP_BACKOFF_FACTOR) -> Dict[str, Any]:
    """EP252 -- next run's training-set size, from reward stability.

    `reward_history` is oldest-first observed mean rewards (the `avg_reward` of
    prior `training_run_complete` rows). The rule, in order:

      * fewer than two observations -> HOLD. One point has no change in it, and
        a ramp taken from nothing is a fabricated input, not a schedule.
      * the LAST step declined past `drop_tolerance` -> DROP (back off).
      * every step within `stability_tolerance` -> RAMP (grow).
      * otherwise -> HOLD (noisy, but not destabilised: neither signal is
        strong enough to spend on).

    Returns {"limit", "action" ("ramp"|"drop"|"hold"), "stable",
    "destabilised", "reason", "observed", "changes", "window", "gaps"}. Both
    moves are clamped to [min_limit, max_limit]. Fails open to a HOLD at
    `base_limit` on any unusable input, recording the swallow first.
    """
    def _result(limit: int, action: str, stable: bool, destabilised: bool,
                reason: str, observed: List[float], changes: List[float],
                gaps: List[str]) -> Dict[str, Any]:
        return {"limit": int(limit), "action": action, "stable": bool(stable),
                "destabilised": bool(destabilised), "reason": reason,
                "observed": list(observed), "changes": list(changes),
                "window": len(observed), "gaps": list(gaps)}

    try:
        base = int(base_limit)
    except (TypeError, ValueError) as exc:
        note_failure("scheduling:rampup-base", exc)
        return _result(0, "hold", False, False, "non-numeric-base-limit", [], [],
                       ["non-numeric-base-limit"])

    try:
        horizon = max(2, int(window))
    except (TypeError, ValueError) as exc:
        note_failure("scheduling:rampup-window", exc)
        return _result(base, "hold", False, False, "non-numeric-window", [], [],
                       ["non-numeric-window"])

    # The tunables are caller-supplied too, so a bad one must degrade to the
    # module's own policy rather than raise deep inside the arithmetic below.
    try:
        lo = max(0, int(min_limit))
        hi = max(lo, int(max_limit))
        grow = float(growth_factor)
        back = float(backoff_factor)
        tol_stable = abs(float(stability_tolerance))
        tol_drop = abs(float(drop_tolerance))
    except (TypeError, ValueError) as exc:
        note_failure("scheduling:rampup-tunables", exc)
        lo, hi = RAMPUP_MIN_LIMIT, RAMPUP_MAX_LIMIT
        grow, back = RAMPUP_GROWTH_FACTOR, RAMPUP_BACKOFF_FACTOR
        tol_stable, tol_drop = RAMPUP_STABILITY_TOLERANCE, RAMPUP_DROP_TOLERANCE

    observed: List[float] = []
    for value in list(reward_history or [])[-horizon:]:
        try:
            observed.append(float(value))
        except (TypeError, ValueError) as exc:
            note_failure("scheduling:rampup-history", exc)
            return _result(base, "hold", False, False, "non-numeric-reward", [], [],
                           ["non-numeric-reward"])

    if len(observed) < 2:
        return _result(base, "hold", False, False, "insufficient-history",
                       observed, [], ["insufficient-history"])

    changes = [_relative_change(prev, cur)
               for prev, cur in zip(observed, observed[1:])]
    last = changes[-1]

    if last < -tol_drop:
        limit = max(lo, int(base * back))
        return _result(limit, "drop", False, True,
                       "destabilised: last reward moved %.1f%% (drop tolerance %.1f%%)"
                       % (last * 100.0, -tol_drop * 100.0),
                       observed, changes, [])

    if all(abs(c) <= tol_stable for c in changes):
        limit = min(hi, max(lo, int(base * grow)))
        return _result(limit, "ramp", True, False,
                       "stable: every step within %.1f%% over %d run(s)"
                       % (tol_stable * 100.0, len(observed)),
                       observed, changes, [])

    worst = max(changes)
    return _result(base, "hold", False, False,
                   "noisy: largest move %.1f%% is outside %.1f%% but the last "
                   "step %.1f%% is inside the drop tolerance"
                   % (abs(worst) * 100.0, tol_stable * 100.0, abs(last) * 100.0),
                   observed, changes, [])


# -- EP187 (Hive): contribution-weighted scheduling ---------------------------
# `ScheduleConfig.interval_hours` decides *when* to train as one constant, so an
# arm that keeps producing promoted or holdout-passed outcomes is trained exactly
# as often as one that never contributes. EP187's host edit — the one the rescore
# named for `ScheduleConfig.interval_hours` / `ScheduledTrainingWorm` — lets the
# observed contribution set the interval, with a floor so a starved arm stays
# reachable: a regime change has to be *discoverable*, which is the argument
# `WORMS/expansion/allocation.py` already makes for its operator budget. That
# module owns the mechanism and is consumed here, never reimplemented.
#
# Bounds are module constants so the policy is arguable in one place.
CONTRIBUTION_MIN_INTERVAL_HOURS = 1.0
CONTRIBUTION_MAX_INTERVAL_HOURS = 168.0    # one week: even the slowest arm runs
CONTRIBUTION_FLOOR = 0.02                  # the same floor `allocation.py` uses

#: EP187's other half — the Hive logits cache — is an established ACCEPT-GAP, not a
#: deferred task: Azazael owns no inference engine (providers are called over HTTP
#: and the API never exposes logits), so there is nothing in-repo to host a
#: logit/KV cache. Recorded here so a later pass does not re-open it as work.


def contribution_weighted_interval(
        base_interval_hours: float,
        contributions: Dict[str, Any],
        *,
        floor: float = CONTRIBUTION_FLOOR,
        min_interval_hours: float = CONTRIBUTION_MIN_INTERVAL_HOURS,
        max_interval_hours: float = CONTRIBUTION_MAX_INTERVAL_HOURS,
) -> Dict[str, Any]:
    """EP187 — per-arm interval from observed contribution shares.

    `contributions` maps an arm name to a non-negative contribution score (0 =
    has never contributed). The interval is inverse to a floored share, so an arm
    at parity with its peers keeps the base interval:

        share_a    = floored_a / sum(floored)   (floor applied, then renormalised)
        interval_a = base * (1/n) / share_a     (parity => base)

    Every result is clamped to [min_interval_hours, max_interval_hours]. Returns
    {"base", "intervals", "shares", "arms", "clamped", "gaps"}. A score that is
    not a number is skipped and reported in `gaps` rather than poisoning every
    share; no contributions at all is parity, not a division by zero. Unusable
    bounds fall back to this module's constants, recording the swallow first — a
    schedule is not worth a crash.
    """
    try:
        base = float(base_interval_hours)
    except (TypeError, ValueError) as exc:
        note_failure("scheduling:contribution-base", exc)
        return {"base": 0.0, "intervals": {}, "shares": {}, "arms": 0,
                "clamped": [], "gaps": ["non-numeric-base-interval"]}

    try:
        lo = max(0.0, float(min_interval_hours))
        hi = max(lo, float(max_interval_hours))
        fl = min(1.0, max(0.0, float(floor)))
    except (TypeError, ValueError) as exc:
        note_failure("scheduling:contribution-bounds", exc)
        lo = CONTRIBUTION_MIN_INTERVAL_HOURS
        hi = CONTRIBUTION_MAX_INTERVAL_HOURS
        fl = CONTRIBUTION_FLOOR

    scores: Dict[str, float] = {}
    gaps: List[str] = []
    for arm, value in dict(contributions or {}).items():
        try:
            scores[str(arm)] = max(0.0, float(value))
        except (TypeError, ValueError):
            # A non-numeric score is not an observation. Skipping it is honest;
            # passing it on would poison every share in the split.
            gaps.append("non-numeric-contribution:%s" % arm)

    if not scores:
        return {"base": base, "intervals": {}, "shares": {}, "arms": 0,
                "clamped": [], "gaps": gaps or ["no-contributions"]}

    n = len(scores)
    total = sum(scores.values())
    if total <= 0.0:
        # Nothing has contributed yet: parity. Dividing by a zero total would
        # raise, and pretending one arm was ahead would be a fabricated input.
        shares = {a: 1.0 / n for a in scores}
    else:
        raw = {a: s / total for a, s in scores.items()}
        floored = {a: max(v, fl) for a, v in raw.items()}
        fdenom = sum(floored.values()) or 1.0
        shares = {a: v / fdenom for a, v in floored.items()}

    even = 1.0 / n
    intervals: Dict[str, float] = {}
    clamped: List[str] = []
    for arm, share in shares.items():
        raw_interval = base * (even / share) if share > 0.0 else hi
        bounded = min(hi, max(lo, raw_interval))
        if abs(bounded - raw_interval) > 1e-9:
            clamped.append(arm)
        intervals[arm] = round(bounded, 4)

    return {"base": base, "intervals": intervals, "shares": shares, "arms": n,
            "clamped": clamped, "gaps": gaps}


class ScheduledTrainingWorm:
    """Runs the full ML training pipeline on a configurable schedule.

    Orchestrates: DataPipelineWorm -> FeatureEngineer -> RLTrainLoop -> ModelVersioner
    Records all results to LocalMemoryStore for dashboard visibility.

    Usage:
        trainer = ScheduledTrainingWorm()
        result = trainer.execute()  # Run once
        trainer.start_scheduler()   # Run on a schedule (daemon thread)
    """

    VERSION = "1.0.0"

    def __init__(self, config: (ScheduleConfig | None) = None):
        self.config = config or ScheduleConfig()
        # Champion promotion: use evolved hyperparameters when a champion
        # has been promoted (rollback = delete evolved_rl_params.json).
        try:
            if self.config.apply_evolved_overrides():
                print(f"[train] using evolved params: lr={self.config.learning_rate} "
                      f"eps={self.config.epsilon} disc={self.config.discount_factor}",
                      flush=True)
        except Exception as exc:
            note_failure("scheduling:L159", exc)
        self._lock = threading.Lock()
        self._run_history: list[TrainingRun] = []
        self._scheduler_thread: (threading.Thread | None) = None
        self._stop_event = threading.Event()
        self._last_model_version = "0.0.0"
        # EP187: built on first contribution feedback, never at construction, so
        # the flat schedule does not depend on the allocation module importing.
        self._allocator: (Any | None) = None

    # -- Lazy Component Loaders --------------------------------------

    def _get_pipeline(self):
        try:
            from CORE.WORMS.expansion.learning import DataPipelineWorm
            return DataPipelineWorm()
        except ImportError as exc:
            note_failure("scheduling:L173", exc)
            return None

    def _get_engineer(self):
        try:
            from CORE.WORMS.expansion.learning import FeatureEngineer
            return FeatureEngineer()
        except ImportError as exc:
            note_failure("scheduling:L180", exc)
            return None

    def _get_rl(self):
        try:
            from CORE.WORMS.expansion.learning import RLTrainLoop
            return RLTrainLoop()
        except ImportError as exc:
            note_failure("scheduling:L187", exc)
            return None

    def _get_versioner(self):
        try:
            from CORE.WORMS.expansion.learning import ModelVersioner
            return ModelVersioner()
        except ImportError as exc:
            note_failure("scheduling:L194", exc)
            return None

    def _get_ab(self):
        try:
            from CORE.WORMS.expansion.learning import ABTestRunner
            return ABTestRunner()
        except ImportError as exc:
            note_failure("scheduling:L201", exc)
            return None

    def _get_store(self):
        try:
            from CORE.local_memory import get_memory_store
            return get_memory_store()
        except ImportError as exc:
            note_failure("scheduling:L208", exc)
            return None

    def _get_logger(self):
        try:
            from CORE.azazael_logging import get_logger
            return get_logger("scheduling")
        except ImportError as exc:
            note_failure("scheduling:L215", exc)
            return None

    # -- EP187 contribution-weighted scheduling ----------------------

    def _get_allocator(self, seed: (str | None) = None):
        """Lazy `ThompsonAllocator` over the configured arms (EP187).

        The arms come from the configured weights when they exist; otherwise from
        `seed`, the arm being recorded. Seeding with the real arm name avoids
        inventing a placeholder arm whose posterior would then pollute the split.
        """
        alloc = getattr(self, "_allocator", None)
        if alloc is not None:
            return alloc
        if ThompsonAllocator is None:
            # Guarded at import (see the allocation block at the top of the
            # module); a successful import here would have to be re-imported
            # per call, which the anti-pattern ratchet counts.
            note_failure("scheduling:allocator-import", None)
            return None
        configured = list((getattr(self.config, "contribution_weights", None) or {}).keys())
        if configured:
            arms = [str(a) for a in configured]
        elif seed:
            arms = [str(seed)]
        else:
            arms = ["default"]
        self._allocator = ThompsonAllocator(arms)
        return self._allocator

    def next_interval_hours(self, arm: (str | None) = None) -> float:
        """EP187 — hours to wait before the next run.

        Without contribution weights this returns `config.interval_hours`, the
        number the scheduler used before this change — the flat schedule is the
        default, not a fallback, so no existing caller is re-timed by accident.
        With weights it returns the contribution-weighted interval for `arm` (or
        for the strongest arm when none is named), floored so a starved arm is
        still scheduled and a regime change stays discoverable.
        """
        weights = getattr(self.config, "contribution_weights", None)
        if not weights:
            return float(self.config.interval_hours)
        plan = contribution_weighted_interval(float(self.config.interval_hours), weights)
        intervals = plan.get("intervals") or {}
        if not intervals:
            return float(self.config.interval_hours)
        target = arm if arm is not None else getattr(self.config, "contribution_arm", None)
        if target is not None and str(target) in intervals:
            return float(intervals[str(target)])
        shares = plan.get("shares") or {}
        best = max(intervals.items(), key=lambda kv: shares.get(kv[0], 0.0))
        return float(best[1])

    def record_contribution(self, arm: str, outcome: str,
                            weight: (float | None) = None) -> Dict[str, Any]:
        """EP187 — feed one cycle outcome back into the allocation policy.

        `allocation.py` owns the mechanism and is consumed, not reimplemented:
        `reward_for_outcome` maps the outcome onto (success, weight) and
        `ThompsonAllocator` keeps the Beta posteriors. This republishes the
        resulting posterior means as `config.contribution_weights` — the field
        `next_interval_hours` reads — so the value has a source and the field has
        a reader. On any failure the weights are returned unchanged: a telemetry
        hiccup must not silently re-time the schedule.
        """
        current = dict(getattr(self.config, "contribution_weights", None) or {})
        alloc = self._get_allocator(seed=str(arm))
        if alloc is None:
            return current
        if reward_for_outcome is None:
            note_failure("scheduling:allocation-import", None)
            return current
        try:
            ok, w = reward_for_outcome(outcome)
            if weight is not None:
                w = float(weight)
            alloc.update(str(arm), success=ok, weight=w)
            self.config.contribution_weights = {
                str(a): float(v) for a, v in alloc.weights().items()}
            return dict(self.config.contribution_weights)
        except Exception as e:
            note_failure("scheduling:record-contribution", e)
            return current

    # -- Main Execution ----------------------------------------------

    def _record_plateau(self, reason: str) -> None:
        """Persist a `training_plateau` row for a run that declined to retrain.

        Written from `execute()` only. `_plateau_skip_reason()` is READ-ONLY on
        purpose (its callers include observation paths), so a bare call to the
        detector must not mutate history -- the operator asked a question, they
        did not start a run.

        The skip used to be visible only as stdout: the cycle printed SKIPPED
        and wrote nothing, so `get_status()` and the next run could not see the
        decision and the plateau signal was unpersisted evidence. Fails
        silently, because a telemetry write must never turn a deliberate skip
        into a crash.
        """
        try:
            store = self._get_store()
            if store is None:
                return
            episodes = int(getattr(self.config, "n_episodes", 0) or 0)
            try:
                recent = store.query_memories(event_type="training_run_complete",
                                             limit=1)
                if recent:
                    d = recent[0].get("data", {})
                    if isinstance(d, str):
                        import json as _json
                        d = _json.loads(d)
                    episodes = int(d.get("episodes", episodes) or episodes)
            except Exception as exc:
                note_failure("scheduling:L248", exc)
            store.record_event(
                source="ScheduledTrainingWorm", event_type="training_plateau",
                data={"reason": reason, "episodes": episodes,
                      "exploration_trigger": True},
                severity="info", tags=["training", "plateau"])
        except Exception as exc:
            note_failure("scheduling:L255", exc)

    def _plateau_skip_reason(self) -> Optional[str]:
        """Skip retraining when converged on unchanged data (learned knowledge).

        Compares the last two scheduled runs: identical record counts and
        reward within 2% means nothing left to learn. Returns reason or None.
        Read-only; fails open (None) on any error.
        """
        if not self.config.record_to_memory:
            # The plateau signal is read from `training_run_complete` rows,
            # which execute() only writes when record_to_memory is on. With
            # recording off there is no history to compare, so any skip
            # would be based on rows this config never produced.
            return None
        try:
            import json as _json
            store = self._get_store()
            if store is None:
                return None
            recent = store.query_memories(event_type="training_run_complete", limit=2)
            if len(recent) < 2:
                return None
            datas = []
            for r in recent:
                d = r.get("data", {})
                if isinstance(d, str):
                    try:
                        d = _json.loads(d)
                    except (ValueError, TypeError) as exc:
                        note_failure("scheduling:L285", exc)
                        return None
                datas.append(d if isinstance(d, dict) else {})
            a, b = datas[0], datas[1]
            # A plateau is only evidence about the config that produced it.
            # Comparing the newest two rows regardless of the requested
            # episodes made a run with a different config skip on evidence
            # from an unrelated one (e.g. `train --episodes 5` declining
            # because two 50-episode runs converged).
            if (a.get("episodes") != self.config.n_episodes
                    or b.get("episodes") != self.config.n_episodes):
                return None
            if a.get("records") != b.get("records"):
                return None
            ra, rb = float(a.get("avg_reward", 0) or 0), float(b.get("avg_reward", 0) or 0)
            if rb and abs(ra - rb) / abs(rb) < 0.02:
                return f"plateaued: reward {ra} vs {rb} on {a.get('records')} records"
            return None
        except Exception as exc:
            note_failure("scheduling:L303", exc)
            return None

    def _rampup_decision(self) -> Dict[str, Any]:
        """EP252 -- this run's training-set size, from the observed reward series.

        Reads prior `training_run_complete` rows (oldest-first) and hands the
        reward series to `rampup_data_limit`. READ-ONLY on history, like
        `_plateau_skip_reason`: a caller that only asks a question must not
        mutate anything. Fails open to the configured limit, and never raises --
        a schedule is not worth a crash.
        """
        base = int(getattr(self.config, "data_limit", 0) or 0)
        hold = {"limit": base, "action": "hold", "stable": False,
                "destabilised": False, "reason": "unavailable",
                "observed": [], "changes": [], "window": 0,
                "gaps": ["unavailable"]}
        if not self.config.record_to_memory:
            # The reward series lives in the rows execute() writes. With
            # recording off there is no series, so any ramp would be decided on
            # evidence this config never produced (`_plateau_skip_reason` makes
            # the same argument for the plateau signal).
            return {**hold, "reason": "recording-disabled",
                    "gaps": ["recording-disabled"]}
        try:
            store = self._get_store()
            if store is None:
                return hold
            recent = store.query_memories(event_type="training_run_complete",
                                          limit=int(RAMPUP_WINDOW))
            rewards: List[Any] = []
            for row in reversed(list(recent or [])):   # store returns newest-first
                data = row.get("data", {})
                if isinstance(data, str):
                    data = json.loads(data)
                # A row without `avg_reward` is not an observation. Skipping it
                # is honest; passing the None on would poison the whole series
                # and report a non-numeric reward the store never contained.
                if isinstance(data, dict) and data.get("avg_reward") is not None:
                    rewards.append(data.get("avg_reward"))
            return rampup_data_limit(rewards, base)
        except Exception as exc:
            note_failure("scheduling:rampup-decision", exc)
            return hold

    def execute(self) -> TrainingRun:
        """Execute one full training pipeline run.

        Pipeline: DataPipelineWorm -> FeatureEngineer -> RLTrainLoop -> ModelVersioner
        Returns a TrainingRun with all metrics.
        """
        start = time.perf_counter()
        run_id = f"train_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}"
        started_at = datetime.now().isoformat()
        error = None

        # Knowledge application: skip converged retraining on unchanged data.
        # Honesty protocol: a skip is skipped=True + success=False, never a
        # zero-record "success" (2026-09-11 audit — the 0-record SUCCESSFUL).
        skip_reason = self._plateau_skip_reason()
        if skip_reason:
            print(f"[train] {run_id} SKIPPED ({skip_reason})", flush=True)
            # A skip is still a run. Record it in history so the operator
            # (and get_status().total_runs) can see that scheduled training
            # ran and deliberately declined to retrain.
            _skipped_run = TrainingRun(
                run_id=run_id, started_at=started_at,
                completed_at=datetime.now().isoformat(),
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
                records_extracted=0, n_episodes=0, avg_reward=0.0,
                q_table_size=0, model_version=self._last_model_version,
                success=False, error=None,
                skipped=True, skip_reason=skip_reason,
            )
            self._run_history.append(_skipped_run)
            if len(self._run_history) > 50:
                self._run_history = self._run_history[-50:]
            self._record_plateau(skip_reason)
            return _skipped_run

        _synthetic = False  # bound before try: exception paths below read it

        # EP252 (Drop-Stable-Rampup): the training SET is scheduled too. The size
        # is decided from the observed reward series before extraction, because
        # `data_limit` IS what the extractor is asked for -- a ramp that does not
        # reach this call would change nothing. The decision is persisted on the
        # config so a ramp compounds across runs and a drop backs off from where
        # the ramp left it, and it is recorded with the run below.
        rampup = self._rampup_decision()
        data_limit = int(rampup.get("limit") or self.config.data_limit)
        self.config.data_limit = data_limit
        try:
            # Stage 1: DataPipelineWorm — extract data
            pipeline = self._get_pipeline()
            if not pipeline:
                raise RuntimeError("DataPipelineWorm unavailable")
            records = pipeline.extract_from_memory(
                source=self.config.source_filter,
                limit=data_limit,
                exclude_sources=self.config.exclude_sources,
            )
            n_records = len(records)

            # Production caller for DataPipelineWorm.schedule_dynamic_refresh:
            # refresh DYNAMIC_SHAPES on the data just extracted so the next
            # run's Stage 2 sees current shapes (fail-open telemetry).
            try:
                pipeline.schedule_dynamic_refresh()
            except Exception as se:
                note_failure("scheduling:shape-refresh", se)

            # Fallback to mock data if no records (2026-09-10 honesty
            # fix): fakes used to be counted as real records and the run
            # reported success on invented data. Now flagged synthetic
            # end-to-end and real count kept at zero.
            if n_records == 0:
                records = [
                    {"source": "SystemZ", "severity": "info", "data": {"duration_ms": 150, "synthetic": True}},
                    {"source": "ProcessZ", "severity": "info", "data": {"duration_ms": 200, "synthetic": True}},
                    {"source": "VulnScanner", "severity": "error", "data": {"duration_ms": 5000, "synthetic": True}},
                ]
                _synthetic = True

            # Stage 2: FeatureEngineer — extract features
            engineer = self._get_engineer()
            if engineer:
                features = engineer.extract_worm_features(records)
            else:
                features = {"count": 0, "feature_dim": 0}

            # Stage 3: RLTrainLoop — train. Resume persisted Q-knowledge
            # first (2026-09-10 fix): every run started tabula-rasa while
            # q_table_persisted rows piled up write-only. Load failure
            # falls back to fresh (logged, never fatal).
            rl = self._get_rl()
            _hybrid_reward = 0.0  # bound for both branches; set for real below
            if rl:
                try:
                    _resumed = bool(rl.load_q_table_from_store())
                except Exception as e:
                    _resumed = False
                    print(f"[train] {run_id} Q-resume failed, fresh start: {e}", flush=True)
                if _resumed:
                    print(f"[train] {run_id} resumed persisted Q-table", flush=True)
                _max_steps = min(50, max(10, n_records // 2))
                if self.config.tune_before_train:
                    # Production caller for RLTrainLoop.tune_and_train:
                    # CV-tune then final-train. tune_and_train nests the
                    # train result, so unwrap before reading metrics.
                    _tt = rl.tune_and_train(
                        source=self.config.source_filter,
                        n_episodes=self.config.n_episodes,
                        max_steps=_max_steps,
                    )
                    training_result = _tt.get("train_result", {}) \
                        if isinstance(_tt, dict) else {}
                else:
                    training_result = rl.train_from_memory(
                        source=self.config.source_filter,
                        n_episodes=self.config.n_episodes,
                        max_steps=_max_steps,
                        learning_rate=self.config.learning_rate,
                        discount_factor=self.config.discount_factor,
                        epsilon=self.config.epsilon,
                    )
                avg_reward = training_result.get("avg_reward", 0)
                q_table_size = training_result.get("q_table_size", 0)
                # Hybrid-reward telemetry (additive): this run's reward as
                # the ENVIRONMENT source; the aggregate rides the run record.
                try:
                    from CORE.WORMS.expansion.hybrid_rewards import (  # noqa: PLC0415 (lazy: no cycle)
                        HybridRewardComputer, RewardConfig, RewardSource)
                    _hr = HybridRewardComputer(RewardConfig(
                        source_weights={RewardSource.ENVIRONMENT: 1.0}))
                    _hr.update(RewardSource.ENVIRONMENT,
                               float(avg_reward or 0.0))
                    _hybrid_reward = _hr.compute_hybrid()
                except Exception as he:
                    note_failure("scheduling:hybrid-telemetry", he)
            else:
                avg_reward = 0.0
                q_table_size = 0

            # Stage 4: ModelVersioner — register model
            versioner = self._get_versioner()
            if versioner:
                # Increment version
                parts = self._last_model_version.split(".")
                major = int(parts[0])
                minor = int(parts[1]) + 1 if len(parts) > 1 else 1
                new_version = f"{major}.{minor}.0"
                self._last_model_version = new_version

                versioner.register_model(
                    name="scheduled_training",
                    version=new_version,
                    metrics={
                        "avg_reward": avg_reward,
                        "q_table_size": q_table_size,
                        "records": n_records,
                        "episodes": self.config.n_episodes,
                    },
                    metadata={
                        "source_filter": self.config.source_filter,
                        "run_id": run_id,
                        "pipeline_version": self.VERSION,
                    },
                )
            else:
                new_version = "0.0.0"

            # Record to LocalMemoryStore
            store = self._get_store()
            if store and self.config.record_to_memory:
                store.record_event(
                    source="ScheduledTrainingWorm",
                    event_type="training_run_complete",
                    data={
                        "algo": "RL",
                        "run_id": run_id,
                        "records": n_records,
                        "synthetic": _synthetic,
                        "episodes": self.config.n_episodes,
                        "data_limit": data_limit,
                        "rampup": str(rampup.get("action") or "hold"),
                        "avg_reward": avg_reward,
                        "q_table_size": q_table_size,
                        "hybrid_reward": _hybrid_reward,
                        "model_version": new_version,
                        "regime": _track_regime(),
                    },
                    severity="info",
                    tags=["training", "scheduled", "pipeline"],
                )

        except Exception as e:
            note_failure("scheduling:L449", e)
            error = str(e)
            avg_reward = 0.0
            q_table_size = 0
            n_records = 0
            new_version = "0.0.0"

        duration_ms = (time.perf_counter() - start) * 1000

        run = TrainingRun(
            run_id=run_id,
            started_at=started_at,
            completed_at=datetime.now().isoformat(),
            duration_ms=round(duration_ms, 2),
            records_extracted=n_records,
            n_episodes=self.config.n_episodes,
            avg_reward=avg_reward,
            q_table_size=q_table_size,
            model_version=new_version,
            success=error is None,
            error=error,
            synthetic=_synthetic,
        )

        with self._lock:
            self._run_history.append(run)
            # Keep last 50 runs
            if len(self._run_history) > 50:
                self._run_history = self._run_history[-50:]

        print(f"[train] {run_id} done: success={run.success} "
              f"episodes={run.n_episodes} reward={round(run.avg_reward, 2)} "
              f"q={run.q_table_size} version={run.model_version} "
              f"ms={round(run.duration_ms, 1)}", flush=True)

        return run

    # -- Scheduler ---------------------------------------------------

    def start_scheduler(self, interval_hours: (float | None) = None):
        """Start the scheduler in a background daemon thread.

        Args:
            interval_hours: Override the default interval
        """
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return

        if interval_hours:
            self.config.interval_hours = interval_hours

        self._stop_event.clear()

        def _scheduler_loop():
            while not self._stop_event.is_set():
                run = self.execute()
                # Log run summary via structured logging
                status = "ok" if run.success else "fail"
                log = self._get_logger()
                if log:
                    log.info("scheduled_training_run_complete", {
                        "status": status,
                        "run_id": run.run_id,
                        "avg_reward": round(run.avg_reward, 2),
                        "duration_ms": round(run.duration_ms, 2),
                    })
                # Sleep for interval (check stop event periodically).
                # EP187: the wait follows observed contribution when weights are
                # supplied, and is numerically identical to the flat interval
                # when they are not (next_interval_hours returns interval_hours).
                deadline = time.time() + self.next_interval_hours() * 3600
                while time.time() < deadline:
                    if self._stop_event.wait(timeout=5):
                        return

        self._scheduler_thread = threading.Thread(
            target=_scheduler_loop, daemon=True
        )
        self._scheduler_thread.start()

    def stop_scheduler(self):
        """Stop the scheduler gracefully."""
        self._stop_event.set()

    # -- Status ------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get scheduler status and run history."""
        with self._lock:
            recent_runs = self._run_history[-5:]
            return {
                "version": self.VERSION,
                "scheduler_running": (
                    self._scheduler_thread is not None
                    and self._scheduler_thread.is_alive()
                ),
                "interval_hours": self.config.interval_hours,
                "source_filter": self.config.source_filter,
                "total_runs": len(self._run_history),
                "last_model_version": self._last_model_version,
                "recent_runs": [
                    {
                        "run_id": r.run_id,
                        "success": r.success,
                        "avg_reward": r.avg_reward,
                        "records": r.records_extracted,
                        "duration_ms": r.duration_ms,
                    }
                    for r in recent_runs
                ],
                "free_threaded": is_free_threaded(),
            }


# =======================================================================
# SCHEDULED DB MONITOR
# =======================================================================

@dataclass
class DbMonitorScheduleConfig:
    """Configuration for scheduled database monitoring."""
    interval_hours: float = 1.0               # How often to check (default: hourly)
    warn_db_size_mb: float = 100.0             # Warning threshold
    critical_db_size_mb: float = 500.0         # Critical threshold
    record_to_memory: bool = True               # Persist results to LocalMemoryStore
    use_postgres: bool = False                  # Use PostgreSQL backend


@dataclass
class DbMonitorRun:
    """Record of a single database monitoring run."""
    run_id: str
    started_at: str
    completed_at: str
    duration_ms: float
    healthy: bool
    db_size_mb: float
    total_memories: int
    total_sessions: int
    growth_24h: int
    errors_7d: int
    warnings_7d: int
    alerts_generated: int
    success: bool
    error: (str | None) = None


class ScheduledDbMonitorWorm:
    """Runs DatabaseMonitorWorm on a configurable schedule.

    Performs periodic database health checks and records results
    to LocalMemoryStore for dashboard visibility. Works alongside
    ScheduledTrainingWorm in the scheduled pipeline.

    Usage:
        monitor = ScheduledDbMonitorWorm()
        result = monitor.execute()  # Run once
        monitor.start_scheduler()   # Run on a schedule (daemon thread)
    """

    VERSION = "1.0.0"

    def __init__(self, config: (DbMonitorScheduleConfig | None) = None, store: Any = None):
        self.config = config or DbMonitorScheduleConfig()
        self._injected_store = store
        self._lock = threading.Lock()
        self._run_history: list[DbMonitorRun] = []
        self._scheduler_thread: (threading.Thread | None) = None
        self._stop_event = threading.Event()

    def _get_monitor(self):
        """Lazy DatabaseMonitorWorm loader."""
        try:
            from CORE.WORMS.expansion.db_monitor import DatabaseMonitorWorm
            return DatabaseMonitorWorm(use_postgres=self.config.use_postgres, store=self._injected_store)
        except ImportError as exc:
            note_failure("scheduling:L621", exc)
            return None

    def _get_store(self):
        """Lazy LocalMemoryStore loader."""
        try:
            from CORE.local_memory import get_memory_store
            return get_memory_store()
        except ImportError as exc:
            note_failure("scheduling:L629", exc)
            return None

    def _get_logger(self):
        """Lazy logger loader."""
        try:
            from CORE.azazael_logging import get_logger
            return get_logger("db_monitor_scheduler")
        except ImportError as exc:
            note_failure("scheduling:L637", exc)
            return None

    def execute(self) -> DbMonitorRun:
        """Execute one database monitoring run.

        Runs DatabaseMonitorWorm.check_health() and records results.

        Returns:
            DbMonitorRun with all health metrics.
        """
        start = time.perf_counter()
        run_id = f"dbmon_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}"
        started_at = datetime.now().isoformat()
        error = None

        try:
            monitor = self._get_monitor()
            if not monitor:
                raise RuntimeError("DatabaseMonitorWorm unavailable")

            result = monitor.execute()
            healthy = result.get("healthy", False)
            db_size = result.get("db_size_mb", 0.0)
            memories = result.get("total_memories", 0)
            sessions = result.get("total_sessions", 0)
            growth = result.get("memory_growth_24h", 0)
            errors = result.get("error_count_7d", 0)
            warnings = result.get("warning_count_7d", 0)
            alerts = result.get("total_alerts", 0)

            # Production caller for DatabaseMonitorWorm.create_backup: a
            # healthy run snapshots the DB it just verified (fail-open;
            # create_backup returns "" and records its own event on failure).
            if healthy:
                try:
                    monitor.create_backup()
                except Exception as be:
                    note_failure("scheduling:dbmon-backup", be)

        except Exception as e:
            note_failure("scheduling:L668", e)
            error = str(e)
            healthy = False
            db_size = 0.0
            memories = 0
            sessions = 0
            growth = 0
            errors = 0
            warnings = 0
            alerts = 0

        duration_ms = (time.perf_counter() - start) * 1000

        run = DbMonitorRun(
            run_id=run_id,
            started_at=started_at,
            completed_at=datetime.now().isoformat(),
            duration_ms=round(duration_ms, 2),
            healthy=healthy,
            db_size_mb=db_size,
            total_memories=memories,
            total_sessions=sessions,
            growth_24h=growth,
            errors_7d=errors,
            warnings_7d=warnings,
            alerts_generated=alerts,
            success=error is None,
            error=error,
        )

        with self._lock:
            self._run_history.append(run)
            if len(self._run_history) > 100:
                self._run_history = self._run_history[-100:]

        return run

    # -- Scheduler ---------------------------------------------------

    def start_scheduler(self, interval_hours: (float | None) = None):
        """Start the scheduler in a background daemon thread.

        Args:
            interval_hours: Override the default interval
        """
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return

        if interval_hours:
            self.config.interval_hours = interval_hours

        self._stop_event.clear()

        def _scheduler_loop():
            while not self._stop_event.is_set():
                run = self.execute()
                status = "ok" if run.success and run.healthy else "fail"
                log = self._get_logger()
                if log:
                    log.info("scheduled_db_monitor_complete", {
                        "status": status,
                        "run_id": run.run_id,
                        "db_size_mb": round(run.db_size_mb, 1),
                        "memories": run.total_memories,
                        "alerts": run.alerts_generated,
                    })
                deadline = time.time() + self.config.interval_hours * 3600
                while time.time() < deadline:
                    if self._stop_event.wait(timeout=5):
                        return

        self._scheduler_thread = threading.Thread(
            target=_scheduler_loop, daemon=True, name="azazael-db-monitor-scheduler"
        )
        self._scheduler_thread.start()

    def stop_scheduler(self):
        """Stop the scheduler gracefully."""
        self._stop_event.set()

    # -- Status ------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get scheduler status and run history."""
        with self._lock:
            recent_runs = self._run_history[-5:]
            healthy_count = sum(1 for r in self._run_history if r.healthy)
            return {
                "version": self.VERSION,
                "scheduler_running": (
                    self._scheduler_thread is not None
                    and self._scheduler_thread.is_alive()
                ),
                "interval_hours": self.config.interval_hours,
                "total_runs": len(self._run_history),
                "healthy_runs": healthy_count,
                "success_rate": round(healthy_count / max(len(self._run_history), 1) * 100, 1),
                "recent_runs": [
                    {
                        "run_id": r.run_id,
                        "success": r.success,
                        "healthy": r.healthy,
                        "db_size_mb": r.db_size_mb,
                        "memories": r.total_memories,
                        "alerts": r.alerts_generated,
                        "duration_ms": r.duration_ms,
                    }
                    for r in recent_runs
                ],
                "free_threaded": is_free_threaded(),
            }


# =======================================================================
# TRAINING ORCHESTRATOR — SB3 Deep RL Pipeline
# =======================================================================

@dataclass
class OrchestratorConfig:
    """Configuration for TrainingOrchestrator runs."""
    algo: str = "PPO"                           # SB3 algorithm
    total_timesteps: int = 5000                  # Training timesteps per run
    policy: str = "MlpPolicy"                    # SB3 policy type
    device: str = "auto"                         # torch device
    learning_rate: (float | None) = None        # Override default LR
    max_age_days: int = 30                       # Data recency filter
    source_filter: (str | None) = None          # None = all sources
    interval_hours: float = 24.0                 # Schedule interval
    max_duration_minutes: float = 30.0           # Max run duration
    record_to_memory: bool = True                # Persist results to DB
    register_model: bool = True                  # Auto-register with ModelVersioner
    exclude_sources: (list[str] | None) = None   # Skip self-generated bookkeeping sources
    worm_id: (str | None) = None                 # Worm identity threaded into SB3Agent


@dataclass
class OrchestratorRun:
    """Record of a single SB3 training orchestration run."""
    run_id: str
    started_at: str
    completed_at: str
    duration_ms: float
    algo: str
    total_timesteps: int
    records_used: int
    data_source: str
    trained: bool
    model_version: (str | None) = None
    eval_mean_reward: (float | None) = None
    eval_std_reward: (float | None) = None
    error: (str | None) = None


def _track_regime() -> dict:
    """Regime snapshot for this run (STACK Phase 1 instruments).

    Delegates to CORE.local_memory.track_regime; fail-soft {} here too
    so a telemetry hiccup can never break a training run.
    """
    try:
        from CORE.local_memory import track_regime
        return track_regime()
    except Exception as exc:
        note_failure("scheduling:L829", exc)
        return {}


class TrainingOrchestrator:
    """
    Orchestrates the full SB3 deep RL training pipeline on a schedule.

    Pipeline:
        DataPipelineWorm (extract) -> SB3Agent (train) -> ModelVersioner (register)

    Unlike ScheduledTrainingWorm (which uses Q-learning via RLTrainLoop),
    this worm wraps SB3's PPO/DQN/A2C algorithms for true deep RL training
    from LocalMemoryStore data.

    Usage:
        orch = TrainingOrchestrator()
        result = orch.execute()  # Run once
        orch.start_scheduler()   # Daemon schedule
    """

    VERSION = "1.0.0"

    def __init__(self, config: (OrchestratorConfig | None) = None):
        self.config = config or OrchestratorConfig()
        self._lock = threading.Lock()
        self._run_history: list[OrchestratorRun] = []
        self._scheduler_thread: (threading.Thread | None) = None
        self._stop_event = threading.Event()
        self._last_model_version: (str | None) = None

    # -- Lazy Component Loaders --------------------------------------

    def _get_pipeline(self):
        """Lazy DataPipelineWorm for memory access."""
        try:
            from CORE.WORMS.expansion.learning import DataPipelineWorm
            return DataPipelineWorm()
        except ImportError as exc:
            note_failure("scheduling:L867", exc)
            return None

    def _get_versioner(self):
        """Lazy ModelVersioner for version tracking."""
        try:
            from CORE.WORMS.expansion.learning import ModelVersioner
            return ModelVersioner()
        except ImportError as exc:
            note_failure("scheduling:L875", exc)
            return None

    def _get_store(self):
        """Lazy LocalMemoryStore."""
        try:
            from CORE.local_memory import get_memory_store
            return get_memory_store()
        except ImportError as exc:
            note_failure("scheduling:L883", exc)
            return None

    def _get_logger(self):
        """Lazy logger."""
        try:
            from CORE.azazael_logging import get_logger
            return get_logger("training_orchestrator")
        except ImportError as exc:
            note_failure("scheduling:L891", exc)
            return None

    # -- Main Execution ----------------------------------------------

    def execute(self) -> OrchestratorRun:
        """
        Execute one full SB3 training orchestration run.

        Pipeline: DataPipelineWorm -> SB3Agent -> ModelVersioner
        Records results to LocalMemoryStore.
        """
        start = time.perf_counter()
        run_id = f"sb3train_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}"
        started_at = datetime.now().isoformat()
        error = None
        trained = False
        model_version = None
        records_used = 0
        total_timesteps = 0
        data_source = self.config.source_filter or "all"

        try:
            # Stage 1: Extract data via DataPipelineWorm
            pipeline = self._get_pipeline()
            if not pipeline:
                raise RuntimeError("DataPipelineWorm unavailable")

            records = pipeline.extract_from_memory(
                source=self.config.source_filter,
                limit=5000,
                max_age_days=self.config.max_age_days,
                exclude_sources=self.config.exclude_sources,
            )
            records_used = len(records)

            if records_used == 0:
                # Fallback: create SB3Agent with default env for dry-run
                pass

            # Stage 2: Create SB3Agent and train
            try:
                from CORE.sb3_agent import SB3Agent
            except ImportError:
                raise RuntimeError("SB3Agent unavailable")

            agent = SB3Agent(
                config=None,
                algo=self.config.algo,
                worm_id=self.config.worm_id,
                policy=self.config.policy,
                device=self.config.device,
                algo_kwargs={
                    "learning_rate": self.config.learning_rate,
                } if self.config.learning_rate else {},
            )

            train_result = agent.train(
                source=self.config.source_filter,
                total_timesteps=self.config.total_timesteps,
                register_model=self.config.register_model,
                max_age_days=self.config.max_age_days,
                exclude_sources=self.config.exclude_sources,
            )

            trained = train_result.get("trained", False)
            total_timesteps = train_result.get("total_timesteps", 0)

            if trained:
                # Stage 3: Get model version from ModelVersioner
                if self.config.register_model:
                    versioner = self._get_versioner()
                    if versioner:
                        latest = versioner.get_latest_version(
                            train_result.get("registered_model", f"SB3_{self.config.algo}")
                        )
                        if latest:
                            model_version = latest.get("version", "v1")
                            self._last_model_version = model_version

                # Record to LocalMemoryStore
                store = self._get_store()
                if store and self.config.record_to_memory:
                    store.record_event(
                        source="TrainingOrchestrator",
                        event_type="sb3_training_orchestrated",
                        data={
                            "run_id": run_id,
                            "algo": self.config.algo,
                            "total_timesteps": total_timesteps,
                            "records_used": records_used,
                            "data_source": data_source,
                            "model_version": model_version or "untracked",
                            "eval_mean_reward": train_result.get("eval_mean_reward"),
                            "eval_std_reward": train_result.get("eval_std_reward"),
                            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                            "regime": _track_regime(),
                        },
                        severity="info",
                        tags=["sb3", "training", "orchestrated"],
                    )

        except Exception as e:
            note_failure("scheduling:L992", e)
            error = str(e)

        duration_ms = (time.perf_counter() - start) * 1000

        run = OrchestratorRun(
            run_id=run_id,
            started_at=started_at,
            completed_at=datetime.now().isoformat(),
            duration_ms=round(duration_ms, 2),
            algo=self.config.algo,
            total_timesteps=total_timesteps,
            records_used=records_used,
            data_source=data_source,
            trained=trained,
            model_version=model_version,
            eval_mean_reward=train_result.get("eval_mean_reward"),
            eval_std_reward=train_result.get("eval_std_reward"),
            error=error,
        )

        with self._lock:
            self._run_history.append(run)
            if len(self._run_history) > 50:
                self._run_history = self._run_history[-50:]

        return run

    # -- Scheduler ---------------------------------------------------

    def start_scheduler(self, interval_hours: (float | None) = None):
        """Start the scheduler in a background daemon thread.

        Calls execute() on the configured interval. Checks stop_event
        every 5 seconds for responsive shutdown.

        Args:
            interval_hours: Override the configured interval.
        """
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return

        if interval_hours:
            self.config.interval_hours = interval_hours

        self._stop_event.clear()

        def _scheduler_loop():
            while not self._stop_event.is_set():
                run = self.execute()
                status = "ok" if run.trained else ("fail" if run.error else "skip")
                log = self._get_logger()
                if log:
                    log.info("sb3_training_orchestration_complete", {
                        "status": status,
                        "run_id": run.run_id,
                        "algo": run.algo,
                        "timesteps": run.total_timesteps,
                        "records": run.records_used,
                        "duration_ms": round(run.duration_ms, 2),
                    })
                # Sleep for interval (check stop event periodically)
                deadline = time.time() + self.config.interval_hours * 3600
                while time.time() < deadline:
                    if self._stop_event.wait(timeout=5):
                        return

        self._scheduler_thread = threading.Thread(
            target=_scheduler_loop, daemon=True, name="azazael-sb3-orchestrator"
        )
        self._scheduler_thread.start()

    def stop_scheduler(self):
        """Stop the scheduler gracefully."""
        self._stop_event.set()

    # -- Status ------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get orchestrator status and run history."""
        with self._lock:
            recent_runs = self._run_history[-5:]
            trained_count = sum(1 for r in self._run_history if r.trained)
            return {
                "version": self.VERSION,
                "scheduler_running": (
                    self._scheduler_thread is not None
                    and self._scheduler_thread.is_alive()
                ),
                "config": {
                    "algo": self.config.algo,
                    "total_timesteps": self.config.total_timesteps,
                    "policy": self.config.policy,
                    "source_filter": self.config.source_filter,
                    "interval_hours": self.config.interval_hours,
                },
                "total_runs": len(self._run_history),
                "trained_runs": trained_count,
                "last_model_version": self._last_model_version,
                "recent_runs": [
                    {
                        "run_id": r.run_id,
                        "trained": r.trained,
                        "algo": r.algo,
                        "timesteps": r.total_timesteps,
                        "records": r.records_used,
                        "duration_ms": r.duration_ms,
                        "error": r.error,
                    }
                    for r in recent_runs
                ],
            }

    def get_run_history(self, limit: int = 10) -> list[dict]:
        """Get recent run history."""
        with self._lock:
            return [
                {
                    "run_id": r.run_id,
                    "started_at": r.started_at,
                    "completed_at": r.completed_at,
                    "duration_ms": r.duration_ms,
                    "algo": r.algo,
                    "timesteps": r.total_timesteps,
                    "records": r.records_used,
                    "trained": r.trained,
                    "model_version": r.model_version,
                    "error": r.error,
                }
                for r in self._run_history[-limit:]
            ]


# =======================================================================
# REGISTRATIONS
# =======================================================================

if _REGISTRY:
    ScheduledTrainingWorm = register_worm(
        category="learning", priority=4, cooldown_seconds=300.0,
        description="Scheduled ML training pipeline — runs DataPipelineWorm -> RLTrainLoop -> ModelVersioner on configurable interval"
    )(ScheduledTrainingWorm)

    ScheduledDbMonitorWorm = register_worm(
        category="monitor", priority=6, cooldown_seconds=120.0,
        description="Scheduled database monitor — runs DatabaseMonitorWorm health checks on configurable interval with alert thresholds"
    )(ScheduledDbMonitorWorm)

    TrainingOrchestrator = register_worm(
        category="learning", priority=5, cooldown_seconds=300.0,
        description="SB3 deep RL orchestrator — DataPipelineWorm -> SB3Agent (PPO/DQN/A2C) -> ModelVersioner on configurable schedule"
    )(TrainingOrchestrator)
