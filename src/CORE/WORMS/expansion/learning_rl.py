# coding: utf-8
"""RL training worm -- RLTrainLoop (split from learning.py, 2026-09-07)."""

import json
import hashlib
import math
import time
import random
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple
from collections import defaultdict
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm  # pyright: ignore[reportAssignmentType] -- optional import: except-path defines fallback decorator; selected by import success
    _REGISTRY = True
except ImportError as exc:
    note_failure("learning_rl:L19", exc)
    _REGISTRY = False
def register_worm(**kw):
        return lambda cls: cls


# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("learning_rl:L29", exc)
    _FREETHREAD = False
FreeThreadPool = None
ThreadSafeDict = dict
ThreadSafeList = list
def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:  # noqa: BLE001 — 314t detection is best-effort
            note_failure("learning_rl:L38", exc)
            return False  # scanner:ignore


def _schema_state_key(vec, severity: str) -> str:
    """Stable state key for a feature vector + severity (2026-09-28).

    Restores the contract test_learning_rl_keys pins (deterministic
    in-process, severity-separated, stable across PYTHONHASHSEED): sha256
    over rounded components, never bare hash() (which is seed-salted).
    """
    try:
        parts = ["%.4f" % float(v) for v in (vec or [])]
    except (TypeError, ValueError) as exc:
        note_failure("learning_rl:L51", exc)
        parts = []
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return "schema:%s:%s" % (digest, severity)


def _annealed_epsilon(epsilon: float, decay: float, episode: int,
                      floor: float = 0.05) -> float:
    """Exploration rate after exponential annealing (2026-09-28).

    Extracted from the inline formula so the schedule is unit-testable:
    decays toward `floor` when decay > 0, clamped, never negative.
    Replaces the old piecewise `_schedule_value` lists.
    """
    try:
        eps = float(epsilon)
        dec = float(decay)
        ep = int(episode)
    except (TypeError, ValueError) as exc:
        note_failure("learning_rl:L69", exc)
        return max(0.0, float(epsilon or 0.0))
    if dec <= 0.0 or ep < 0:
        return max(0.0, eps)
    return max(floor, eps * math.exp(-dec * ep))


_UNMAPPED_SEVERITIES: Dict[str, int] = {}


def note_unmapped_severity(sev) -> None:
    """Record a severity the reward table does not cover, and say so once.

    A severity missing from the table scores 0.0 for every action, which is
    numerically identical to a learner that simply did badly. That is how a
    dead objective arm stayed invisible: the fitness looked like a measurement
    when it was a constant. Counting them separately keeps the failure
    attributable, and `unmapped_severities()` lets a caller assert the
    vocabulary still covers the data.
    """
    key = str(sev)
    _UNMAPPED_SEVERITIES[key] = _UNMAPPED_SEVERITIES.get(key, 0) + 1
    if _UNMAPPED_SEVERITIES[key] == 1:
        try:
            print("[learning_rl] WARNING: severity %r is not in the reward "
                  "table; those records score 0.0 for every action and the "
                  "objective arm is capped at zero. Add it." % key, flush=True)
        except Exception as exc:
            note_failure("learning_rl:L96", exc) # scanner:ignore


def unmapped_severities() -> Dict[str, int]:
    """Severities seen in the data that the reward table does not cover."""
    return dict(_UNMAPPED_SEVERITIES)


def _compute_reward(record: Dict, action: int) -> float:
    """Score how well the CHOSEN action fits the record severity.

    Hoisted out of train_from_memory (2026-09-08) so the reward model is
    directly unit-testable. Mapping:

      severity   good actions           bad actions
      error      remediate +1.0, alert +0.4
                 log -0.3, ignore -1.0
      warning    alert +0.7, remediate +0.2
                 log 0.0, ignore -0.7
      info       log +0.3, ignore +0.2
                 alert -0.3, remediate -0.5

    A data.success outcome (when present) refines: an error that was
    remediated and succeeded earns a bonus; a failed remediation/alert
    on an error is penalized. Duration still shaves a small amount to
    penalize slow operations.
    """
    sev = record.get("severity", "info")
    data = record.get("data", {})
    if isinstance(data, dict):
        duration = data.get("duration_ms", 0) or 0
    else:
        duration = 0

    base = {
        "error":   {3: 1.0, 2: 0.4, 1: -0.3, 0: -1.0},
        "warning": {3: 0.2, 2: 0.7, 1: 0.0, 0: -0.7},
        "info":    {3: -0.5, 2: -0.3, 1: 0.3, 0: 0.2},
        # `critical` (2026-09-30). MEASURED: every row in the db_health_alert
        # pool carries severity `critical` -- 7 of 7 -- and `critical` was not
        # in this table, so `.get(sev, {...all zeros...})` returned 0.0 for all
        # four actions on all seven rows. The alert objective therefore had a
        # hard ceiling of exactly 0.0 BEFORE any learning happened, which is
        # why `alert` read 0.0 on all 35 measured cycles and why the promoted
        # genome carries fitness_detail {info: 16.33, alert: 0.0}. No learner,
        # tabular or modern, can score above a constant.
        #
        # Action indices, per the docstring: 0 ignore, 1 log, 2 alert,
        # 3 remediate. `critical` is ranked above `error`, so the ordering and
        # spread are scaled from the `error` row: remediate best, alert next,
        # log mildly bad, ignore worst.
        "critical": {3: 1.4, 2: 0.7, 1: -0.5, 0: -1.4},
    }.get(sev)
    if base is None:
        # Previously this collapsed to all-zeros, which is indistinguishable
        # from "the learner scored zero" -- an unmapped input read as a
        # measured result. It is now recorded so the gap is visible, and the
        # neutral row is kept so an unmapped severity still yields a usable
        # (if uninformative) signal rather than an exception mid-rollout.
        note_unmapped_severity(sev)
        base = {3: 0.0, 2: 0.0, 1: 0.0, 0: 0.0}
    reward = base.get(int(action), 0.0)

    if isinstance(data, dict) and "success" in data:
        ok = bool(data["success"])
        if sev == "error":
            if ok and action == 3:
                reward += 0.3
            elif not ok and action in (2, 3):
                reward -= 0.4
        elif sev == "info" and ok and action in (0, 1):
            reward += 0.1

    if duration:
        reward -= duration / 10000.0
    return reward


@register_worm(category="learning", priority=6, cooldown_seconds=120.0,
    description="RL train loop v2 — trains from DataPipelineWorm output with Q-learning policy optimization")
class RLTrainLoop:
    """Reinforcement learning training loop v2.0.

    Trains from real execution data extracted by DataPipelineWorm from
    LocalMemoryStore. Uses Q-learning with epsilon-greedy exploration.
    Records training metrics and persists learned Q-tables.

    Usage:
        loop = RLTrainLoop()

        # Train from DataPipelineWorm output
        stats = loop.train_from_memory(source="SystemZ", n_episodes=100)

        # Or train with a mock environment
        episode = loop.train_episode(env, agent)
    """

    VERSION = "2.0.0"

    # Chosen-action semantics for Q-learning: index -> response policy.
    # Rewards depend on whether the chosen action fits the record severity,
    # so different hyperparameters (esp. epsilon) yield different fitness --
    # this is what gives evolution a gradient to optimize.
    ACTION_NAMES = ["ignore", "log", "alert", "remediate"]

    def __init__(self):
        self._episodes: List[Dict] = []
        self._q_table: Dict[str, Dict[int, float]
            ] = defaultdict(lambda: defaultdict(float))
        self._lock = threading.Lock()
        self._pipeline = None  # Lazy DataPipelineWorm
        self._versioner = None  # Lazy ModelVersioner
        self._model_name = None  # Set during register_to_versioner()


    def _get_pipeline(self):
        """Lazy DataPipelineWorm for memory access — double-checked locking for 3.14t."""
        if self._pipeline is None:
            with self._lock:
                if self._pipeline is None:
                    try:
                        from CORE.WORMS.expansion.learning import DataPipelineWorm
                        self._pipeline = DataPipelineWorm()
                    except ImportError as exc:
                        note_failure("learning_rl:L220", exc)
                        self._pipeline = False
        return self._pipeline if self._pipeline else None
    def _get_versioner(self):
        """Lazy ModelVersioner for model version tracking — double-checked locking for 3.14t."""
        if self._versioner is None:
            with self._lock:
                if self._versioner is None:
                    try:
                        from CORE.WORMS.expansion.learning import ModelVersioner
                        self._versioner = ModelVersioner()
                    except ImportError as exc:
                        note_failure("learning_rl:L231", exc)
                        self._versioner = False
        return self._versioner if self._versioner else None
    def register_to_versioner(self, model_name: Optional[str] = None,
                               metrics: Optional[Dict[str, float]] = None,
                               metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Register the current Q-table policy with ModelVersioner for version tracking.

        Persists the learned policy to LocalMemoryStore via ModelVersioner,
        enabling rollback, comparison, and history queries.

        Args:
            model_name: Name for the model (default: "RLTrainLoop_QTable").
            metrics: Optional metrics dict (avg_reward, q_table_size, etc.)
            metadata: Optional metadata (source, n_episodes, etc.)

        Returns:
            Dict with registration result: model_name, version, registered_at
        """
        versioner = self._get_versioner()
        if not versioner:
            return {"error": "ModelVersioner unavailable"}

        name = model_name or f"RLTrainLoop_{
    datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._model_name = name
        stats = self.get_training_stats()

        _metrics = metrics or {
            "avg_reward": stats.get("avg_reward", 0),
            "q_table_size": stats.get("q_table_size", 0),
            "episodes": stats.get("episodes", 0),
        }
        _metadata = metadata or {
            "version": self.VERSION,
            "source": "RLTrainLoop",
        }

        version = f"v{stats.get('episodes', 0)}"

        try:
            result = versioner.register_model(  # pyright: ignore[reportAttributeAccessIssue]
                name=name, version=version,
                metrics=_metrics, metadata=_metadata,
            )
            # Also persist Q-table to LocalMemoryStore for durability
            self._save_q_table_to_store(name, version)
            return {
                "model_name": name,
                "version": version,
                "registered_at": result.get("registered_at", datetime.now().isoformat()),
                "metrics": _metrics,
            }
        except Exception as e:  # noqa: BLE001 — registration failure returns signed error
            note_failure("learning_rl:L285", e)
            return {"error": str(e)}

    def _save_q_table_to_store(self, model_name: str, version: str) -> bool:
        """Persist the Q-table to LocalMemoryStore for durable storage."""
        try:
            # Singleton — creating a fresh LocalMemoryStore per call re-runs
            # schema migration on every use (slow on large DBs).
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            # Convert Q-table to serializable format
            q_serializable = {}
            with self._lock:
                for state_key, actions in self._q_table.items():
                    q_serializable[state_key] = dict(actions)

            store.record_event(
                source="RLTrainLoop", event_type="q_table_persisted",
                data={
                    "model_name": model_name,
                    "model_version": version,
                    "q_table": q_serializable,
                    "q_table_size": len(q_serializable),
                    "episodes": len(self._episodes),
                },
                severity="info", tags=["rl", "q_table", "persist"],
            )
            return True
        except Exception as exc:  # noqa: BLE001 — persistence best-effort
            note_failure("learning_rl:L313", exc)
            return False  # scanner:ignore
    def load_q_table_from_store(self, model_name: Optional[str] = None, version: Optional[str] = None) -> bool:
        """Load a persisted Q-table from LocalMemoryStore.

        Args:
            model_name: Model name to search for (default: latest).
            version: Specific version to load.

        Returns:
            True if Q-table was loaded successfully.
        """
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()

            # Exact indexed lookup (2026-09-10 fix): the old FTS query
            # ('q_table_persisted RLTrainLoop') matched zero rows even
            # with 20k persisted tables in the store, so every load
            # silently returned False and all runs started tabula-rasa.
            if model_name:
                events = [r for r in store.query_memories(
                    event_type="q_table_persisted", source="RLTrainLoop",
                    limit=50) if (r.get("data", {}) or {}).get("model_name") == model_name
                    or (isinstance(r.get("data"), str) and model_name in r.get("data", ""))]
            else:
                events = store.query_memories(
                    event_type="q_table_persisted", source="RLTrainLoop",
                    limit=10)
            if version:
                events = [r for r in events
                          if ((r.get("data", {}) or {}).get("model_version") == version
                              if isinstance(r.get("data"), dict) else True)]
            if not events:
                return False

            for ev in reversed(events):
                data = ev.get("data", {})
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except Exception as exc:  # noqa: BLE001 — skip malformed serialized payload
                        note_failure("learning_rl:L354", exc)
                        continue
                if not isinstance(data, dict):
                    continue

                q_data = data.get("q_table")
                if not q_data:
                    continue

                # Rebuild Q-table from serialized format
                with self._lock:
                    self._q_table.clear()
                    for state_key, actions in q_data.items():
                        for action_val, q_val in actions.items():
                            self._q_table[state_key][int(action_val)] = float(q_val)

                # Also restore episode count
                episodes = data.get("episodes", 0)
                if episodes and not self._episodes:
                    self._episodes = [{"loaded": True, "count": episodes}]

                return True

            return False
        except Exception as exc:  # noqa: BLE001 — load best-effort
            note_failure("learning_rl:L378", exc)
            return False  # scanner:ignore
    def train_from_memory(self, source: Optional[str] = None, event_type: Optional[str] = None,
                          n_episodes: int = 100, max_steps: int = 50,
                          learning_rate: float = 0.1, discount_factor: float = 0.9,
                          epsilon: float = 0.1,
                          epsilon_decay: float = 0.0,
                          test_data: Optional[List[Dict]] = None,
                          use_data_split: bool = False,
                          val_ratio: float = 0.1, test_ratio: float = 0.2,
                          by_session: bool = True,
                          use_schema_features: bool = False,
                          exclude_sources: Optional[List[str]] = None,
                          records: Optional[List[Dict]] = None,
                          persist: bool = True,
                          rng: Optional[random.Random] = None,
                          use_replay: bool = False,
                          use_cutting_edge: bool = False) -> Dict[str, Any]:
        """Train from real execution data in LocalMemoryStore.

        Uses DataPipelineWorm to extract records, then treats each
        record as a training step. Builds a state-action-reward model
        based on worm execution patterns.

        If use_data_split=True, automatically splits memories into
        train/validation/test sets (by session_id to prevent leakage).

        If use_schema_features=True, uses SchemaAwareFeatureExtractor for
        richer state vectors (typed numeric features from the data blob).
        Otherwise uses simple source:severity state keys.

        If exclude_sources is provided, those source components are skipped
        during extraction (used to keep eval pools free of the training
        system's own recorded bookkeeping events).

        If persist=False (used by evolution_cycle GA evals), the run is
        scored but nothing is written back: no training_complete event, no
        ModelVersioner registration, no q_table_persisted event. This keeps
        the GA's ~96 eval runs per cycle from bloating the store and the
        model registry with bookkeeping rows.

        If epsilon_decay > 0 (2026-09-08 expansion), epsilon anneals toward
        a floor each episode: eps_eff = max(floor, epsilon * exp(-decay * ep)).
        A real policy knob — evolution can now search over how fast the agent
        shifts from exploration to exploitation without changing eval budget
        (unlike n_episodes/max_steps, which would create fake selection
        pressure via more Q-updates).

        If test_data is provided directly, uses it for held-out evaluation.

        If rng is provided (a random.Random instance), all exploration
        draws use it instead of the global random module, making the eval
        an isolated pure function of (genome, pool, seed) — required for
        parallel fitness evaluation under free-threading.

        If use_cutting_edge is True, ONE extra training step is routed through
        the cutting-edge model (SparseMoE + SelectiveSSM + Attention + FFN, from
        CORE.WORMS.expansion.cutting_edge_model) and reported under the
        "cutting_edge" key. Off by default: with use_cutting_edge=False the
        result dict is the one this method returned before the flag existed and
        the model module is never imported.

        Args:
            source: Filter memories by source component
            event_type: Filter by event type
            n_episodes: Number of training episodes to run
            max_steps: Max steps per episode
            learning_rate: Q-learning alpha
            discount_factor: Q-learning gamma
            epsilon: Exploration rate
            test_data: Optional held-out test records for evaluation
            use_data_split: If True, auto-split memories using split_memories()
            val_ratio: Fraction of sessions for validation when use_data_split=True
            test_ratio: Fraction of sessions for test when use_data_split=True
            by_session: If True, split by session_id to prevent leakage
            use_schema_features: If True, use SchemaAwareFeatureExtractor states
            records: Optional pre-extracted record list. When given, the
                live extraction is skipped and every episode trains on this
                frozen snapshot (used for pool-consistent GA evaluation).

        Returns:
            Dict with training stats, policy summary, and test metrics
        """
        pipeline = self._get_pipeline()
        if not pipeline:
            return {"error": "DataPipelineWorm unavailable", "episodes": 0}

        # Auto-split if requested (overrides explicit test_data)
        if use_data_split:
            split_result = pipeline.split_memories(  # pyright: ignore[reportAttributeAccessIssue]
                source=source, event_type=event_type,
                test_ratio=test_ratio, val_ratio=val_ratio,
                by_session=by_session,
            )
            records = split_result.get("train", [])
            test_data = split_result.get("test", [])
            split_stats = split_result.get("stats", {})
        else:
            # Extract all records (no split) — or reuse a caller-frozen
            # snapshot (2026-09-08: evolution_cycle freezes one pool per
            # cycle so every genome is scored on identical data instead
            # of a window that drifts between evaluations).
            if records is not None:
                split_stats = {"frozen_pool": len(records)}
            else:
                records = pipeline.extract_from_memory(  # pyright: ignore[reportAttributeAccessIssue]
                    source=source, event_type=event_type, limit=n_episodes * 10,
                    exclude_sources=exclude_sources,
                )
                split_stats = {}

        if not records:
            return {"error": "No training data found in LocalMemoryStore", "episodes": 0}

        # Build SchemaAwareFeatureExtractor if requested
        _schema_extractor = None
        if use_schema_features:
            try:
                from CORE.WORMS.expansion.learning_features import SchemaAwareFeatureExtractor
                _schema_extractor = SchemaAwareFeatureExtractor()
                _schema_extractor.fit(records)
            except Exception as exc:  # noqa: BLE001 — schema extraction best-effort
                note_failure("learning_rl:L491", exc)
                _schema_extractor = None

        def _get_state_key(record: Dict) -> str:
            """Build a state key from a record.

            When use_schema_features=True, uses the feature vector hash
            from SchemaAwareFeatureExtractor for richer state discrimination.
            Otherwise uses simple source:severity string key.
            """
            if _schema_extractor:
                features, _ = _schema_extractor.extract([record])
                if features:
                    # Stable cross-process key (2026-10-06 ARMOR retrofit):
                    # the old bare hash() here is salted per process
                    # (PYTHONHASHSEED), so schema-arm fitness was incomparable
                    # across evaluators. Route through the module-level
                    # sha256 helper like everything else.
                    sv = record.get("severity", "info")
                    return _schema_state_key(
                        [round(float(v), 2) for v in features[0]], sv)
            # Fallback to simple key
            src = record.get("source", "unknown")
            sv = record.get("severity", "info")
            return f"{src}:{sv}"

        start = time.perf_counter()
        episode_rewards = []
        # Isolated RNG (2026-09-09): callers pass a seeded Random for
        # deterministic thread-safe evals; default preserves old behavior.
        R = rng if rng is not None else random
        # Experience replay (2026-10-03, advisory stage): when use_replay,
        # every step is also stored in a bounded buffer and each episode
        # ends with extra Q-updates sampled from it via R, so the draws
        # stay deterministic under a seeded rng. Off by default: the base
        # trajectory is byte-identical with use_replay=False.
        replay_buf: list = [] if use_replay else None  # type: ignore

        for episode in range(n_episodes):
            total_reward = 0.0
            action_counts = defaultdict(int)
            # epsilon annealing (2026-09-08): exploration decays exponentially
            # toward a 5% floor when epsilon_decay > 0, so the agent shifts
            # from exploration to exploitation within a run.
            eps_eff = _annealed_epsilon(epsilon, epsilon_decay, episode)
            for step in range(min(max_steps, len(records))):
                record = records[(episode + step) % len(records)]

                # Build state key (simple or schema-aware)
                state_key = _get_state_key(record)

                # Choose action (epsilon-greedy)
                if state_key not in self._q_table or R.random() < eps_eff:
                    action = R.randint(0, 3)
                else:
                    action = max(self._q_table[state_key], key=self._q_table[state_key].get)  # pyright: ignore[reportArgumentType, reportCallIssue]

                # Compute reward -- depends on the chosen action so the
                # policy gradient is real (see _compute_reward docstring)
                reward = _compute_reward(record, action)

                # Q-learning update
                old_q = self._q_table[state_key].get(action, 0.0)
                next_max_q = max(self._q_table[state_key].values(), default=0.0)
                new_q = old_q + learning_rate * (reward + discount_factor * next_max_q - old_q)
                self._q_table[state_key][action] = new_q

                action_counts[action] += 1
                total_reward += reward
                if replay_buf is not None:
                    replay_buf.append((state_key, action, reward))
                    if len(replay_buf) > 5000:
                        del replay_buf[:len(replay_buf) - 5000]

            if replay_buf:
                for _ in range(min(len(replay_buf), max_steps)):
                    rs, ra, rr = replay_buf[R.randrange(len(replay_buf))]
                    ro = self._q_table[rs].get(ra, 0.0)
                    rn = max(self._q_table[rs].values(), default=0.0)
                    self._q_table[rs][ra] = ro + learning_rate * (
                        rr + discount_factor * rn - ro)

            episode_rewards.append(total_reward)

            episode_data = {
                "episode": episode + 1,
                "total_reward": total_reward,
                "steps": min(max_steps, len(records)),
                "actions": dict(action_counts),
                "timestamp": time.time(),
            }
            with self._lock:
                self._episodes.append(episode_data)

        duration_ms = (time.perf_counter() - start) * 1000

        # Evaluate on held-out test data if provided
        test_metrics = {}
        if test_data:
            test_rewards = []
            test_unknown_states = 0
            for record in test_data:
                state_key = _get_state_key(record)

                # Greedy action (no exploration)
                if state_key in self._q_table:
                    action = max(self._q_table[state_key], key=self._q_table[state_key].get)  # pyright: ignore[reportArgumentType, reportCallIssue]
                else:
                    action = 0
                    test_unknown_states += 1

                reward = _compute_reward(record, action)
                test_rewards.append(reward)

            test_metrics = {
                "test_records": len(test_data),
                "test_avg_reward": round(sum(test_rewards) / len(test_rewards), 4) if test_rewards else 0,
                "test_unknown_states": test_unknown_states,
            }

        # Build result dict before persistence (fixes used-before-def error)
        result = {
            "version": self.VERSION,
            "episodes": n_episodes,
            "avg_reward": round(sum(episode_rewards) / len(episode_rewards), 4) if episode_rewards else 0,
            "max_reward": round(max(episode_rewards), 4) if episode_rewards else 0,
            "min_reward": round(min(episode_rewards), 4) if episode_rewards else 0,
            "q_table_size": len(self._q_table),
            "duration_ms": round(duration_ms, 2),
            "data_source": source or "all",
            "persisted": False,
        }
        if test_metrics:
            result["test_metrics"] = test_metrics
        if split_stats:
            result["data_split"] = {
                "method": split_stats.get("split_method", "unknown"),
                "train_records": split_stats.get("train_records", len(records)),
                "test_records": split_stats.get("test_records", 0),
                "val_records": split_stats.get("val_records", 0),
                "total_sessions": split_stats.get("total_sessions", 0),
            }

        # -- Cutting-edge step (opt-in, off by default) --------------------
        # The CuttingEdgeModel backend had zero production callers; when the
        # caller asks for it, route ONE real training step through the model and
        # report what actually happened rather than a claim. The import is
        # function-level for the same reason the contract test documents:
        # importing a worm module at module scope registers a worm at
        # collection time, as a side effect of the import.
        if use_cutting_edge:
            from CORE.freethreading import WorkStealingPool
            from CORE.WORMS.expansion.cutting_edge_model import (
                CuttingEdgeConfig,
                CuttingEdgeModel,
            )

            # Real rows derived from the records in hand -- severity code, the
            # mean of the record's numeric data fields, field counts -- every
            # value bounded to 0..1 so the reward signals below are measurements
            # of this run rather than constants dressed up as one.
            _sev_code = {"info": 1.0, "warning": 0.5, "error": 0.25,
                         "critical": 0.0}
            _rows: List[List[float]] = []
            for _rec in records:
                _data = _rec.get("data") or {}
                _num = [float(v) for v in _data.values()
                        if isinstance(v, (int, float))
                        and not isinstance(v, bool)]
                _mean = sum(_num) / len(_num) if _num else 0.0
                _base = [
                    _sev_code.get(str(_rec.get("severity", "info")), 0.5),
                    min(1.0, abs(_mean)),
                    min(1.0, float(len(_num))),
                    min(1.0, float(len(_data))),
                    1.0 if _rec.get("source") else 0.0,
                ]
                _rows.append((_base * 2)[:8])

            _model = CuttingEdgeModel(CuttingEdgeConfig())
            with WorkStealingPool(num_workers=2) as _pool:
                _step = _model.train_parallel(_pool, tokens=_rows, timeout=60.0)

            _flat = [v for _row in _rows for v in _row]
            _signals = [
                (sum(1 for v in _flat if v) / len(_flat)) if _flat else 0.0,
                min(1.0, len(_rows) / 20.0),
                (sum(1.0 for _r in records
                     if _r.get("severity") in ("info", "warning"))
                 / len(records)) if records else 0.0,
            ]
            result["cutting_edge"] = {
                "trained": bool(
                    _step.get("experts_updated", 0) > 0
                    and _step.get("loss_after") is not None
                    and _step.get("loss_before") is not None
                    and _step["loss_after"] < _step["loss_before"]
                ),
                "experts_updated": int(_step.get("experts_updated", 0)),
                "loss_before": _step.get("loss_before"),
                "loss_after": _step.get("loss_after"),
                "reward": _model.reward(_signals, allow_network=False),
                "active_components": list(_model.active_components()),
            }

        # Persist learned policy to LocalMemoryStore + ModelVersioner.
        # Skipped entirely when persist=False (GA eval runs) so evolution
        # does not bloat the store or model registry with bookkeeping rows.
        if persist:
            try:
                from CORE.local_memory import get_memory_store
                store = get_memory_store()
                save_data = {
                    "n_episodes": n_episodes,
                    "total_duration_ms": round(duration_ms, 2),
                    "avg_reward": result["avg_reward"],
                    "q_table_size": len(self._q_table),
                    "source_filter": source,
                }
                if test_metrics:
                    save_data["test_avg_reward"] = test_metrics["test_avg_reward"]
                store.record_event(
                    source="RLTrainLoop", event_type="training_complete",
                    data=save_data,
                    severity="info", tags=["rl", "training", "v2"],
                )
                result["persisted"] = True

                # Auto-register with ModelVersioner if training produced results
                if len(self._q_table) > 0:
                    reg_result = self.register_to_versioner(
                        metrics={
                            "avg_reward": result["avg_reward"],
                            "q_table_size": len(self._q_table),
                            "episodes": n_episodes,
                        },
                    metadata={
                        "source_filter": source or "all",
                        "learning_rate": learning_rate,
                        "discount_factor": discount_factor,
                        "epsilon": epsilon,
                        "epsilon_decay": epsilon_decay,
                        "use_schema_features": use_schema_features,
                    },
                    )
                    if "error" not in reg_result:
                        result["registered_model"] = reg_result.get("model_name")
                        result["registered_version"] = reg_result.get("version")
            except Exception as exc:  # noqa: BLE001 — model registration best-effort
                note_failure("learning_rl:L677", exc) # scanner:ignore

        return result
    def hyperparameter_tune(self, source: Optional[str] = None, event_type: Optional[str] = None,
                              n_episodes: int = 50, max_steps: int = 30,
                              n_folds: int = 3, use_schema_features: bool = False,
                              lr_values: Optional[List[float]] = None,
                              gamma_values: Optional[List[float]] = None,
                              eps_values: Optional[List[float]] = None,
                              register_tune_model: bool = False) -> Dict[str, Any]:
        """Hyperparameter tuning via temporal cross-validation.

        Uses DataPipelineWorm.temporal_cross_validation() to create
        temporally-ordered train/test folds, then trains an RLTrainLoop
        on each fold for each hyperparameter combination, evaluating
        held-out test avg_reward to find the best config.

        Grid search over learning_rate, discount_factor (gamma), and
        epsilon. Default values are reasonable starting points.

        Args:
            source: Filter memories by source.
            event_type: Filter by event type.
            n_episodes: Episodes per training run.
            max_steps: Steps per episode.
            n_folds: Number of CV folds.
            use_schema_features: Use SchemaAwareFeatureExtractor states.
            lr_values: Learning rates to try.
            gamma_values: Discount factors to try.
            eps_values: Epsilon (exploration) values to try.

        Returns:
            Dict with best_params, best_score, and all_results.
        """
        pipeline = self._get_pipeline()
        if not pipeline:
            return {"error": "DataPipelineWorm unavailable"}

        # Default grid
        if lr_values is None:
            lr_values = [0.05, 0.1, 0.2]
        if gamma_values is None:
            gamma_values = [0.8, 0.9, 0.95]
        if eps_values is None:
            eps_values = [0.05, 0.1, 0.2]

        # Get CV folds from pipeline
        cv_result = pipeline.temporal_cross_validation(  # pyright: ignore[reportAttributeAccessIssue]
            source=source, event_type=event_type,
            n_folds=n_folds,
        )
        folds = cv_result.get("folds", [])
        if not folds:
            return {"error": "No data for CV folds", "cv_stats": cv_result.get("stats", {})}

        start = time.perf_counter()
        results = []
        best_score = -float("inf")
        best_params = {}

        # Save original state to restore after tuning
        _saved_q_table = self._q_table
        _saved_episodes = self._episodes

        for lr in lr_values:
            for gamma in gamma_values:
                for eps in eps_values:
                    fold_rewards = []

                    for fold in folds:
                        train_records = fold["train"]
                        test_records = fold["test"]

                        if not train_records or not test_records:
                            continue

                        # Use fresh Q-table per config/fold (non-destructive)
                        self._q_table = defaultdict(lambda: defaultdict(float))
                        self._episodes = []

                        # Quick training on fold
                        fold_result = self.train_from_memory(
                            source=source, event_type=event_type,
                            n_episodes=n_episodes // max(1, n_folds),
                            max_steps=max_steps,
                            learning_rate=lr,
                            discount_factor=gamma,
                            epsilon=eps,
                            test_data=test_records,
                            use_schema_features=use_schema_features,
                        )

                        if "test_metrics" in fold_result:
                            fold_rewards.append(fold_result["test_metrics"]["test_avg_reward"])

                    if not fold_rewards:
                        continue

                    avg_fold_reward = round(sum(fold_rewards) / len(fold_rewards), 4)

                    result_entry = {
                        "learning_rate": lr,
                        "discount_factor": gamma,
                        "epsilon": eps,
                        "avg_test_reward": avg_fold_reward,
                        "n_folds_with_data": len(fold_rewards),
                    }
                    results.append(result_entry)

                    if avg_fold_reward > best_score:
                        best_score = avg_fold_reward
                        best_params = {
                            "learning_rate": lr,
                            "discount_factor": gamma,
                            "epsilon": eps,
                        }

        duration_ms = (time.perf_counter() - start) * 1000

        # Restore original state
        self._q_table = _saved_q_table
        self._episodes = _saved_episodes

        # Optionally register best config as a model version
        registered_version = None
        if register_tune_model and best_params:
            try:
                from CORE.local_memory import get_memory_store
                store = get_memory_store()
                version = datetime.now().strftime("%Y%m%d_%H%M%S")
                store.record_event(
                    source="RLTrainLoop", event_type="tune_config_registered",
                    data={
                        "best_params": best_params,
                        "best_score": best_score,
                        "n_configs_tried": len(results),
                        "n_folds": n_folds,
                        "data_source": source or "all",
                        "use_schema_features": use_schema_features,
                    },
                    severity="info", tags=["rl", "tune", "v2"],
                )
                registered_version = version
            except Exception as exc:  # noqa: BLE001 — tune-run persistence best-effort
                note_failure("learning_rl:L821", exc) # scanner:ignore

        return {
            "version": self.VERSION,
            "best_params": best_params,
            "best_score": best_score,
            "results": sorted(results, key=lambda r: -r["avg_test_reward"]),
            "n_configs_tried": len(results),
            "n_folds": n_folds,
            "duration_ms": round(duration_ms, 2),
            "data_source": source or "all",
            "use_schema_features": use_schema_features,
            "registered_version": registered_version,
        }

    def tune_and_train(self, source: Optional[str] = None, event_type: Optional[str] = None,
                        n_episodes: int = 50, max_steps: int = 30,
                        n_folds: int = 3, use_schema_features: bool = False,
                        lr_values: Optional[List[float]] = None,
                        gamma_values: Optional[List[float]] = None,
                        eps_values: Optional[List[float]] = None,
                        register_tune_model: bool = False) -> Dict[str, Any]:
        """Tune hyperparams via CV, then train a final model with best params.

        Runs hyperparameter_tune() to find the optimal configuration via
        temporal cross-validation, then trains a final policy using those
        optimal hyperparameters on ALL available data.

        Args:
            Same as hyperparameter_tune() + same as train_from_memory().

        Returns:
            Dict with 'tune_result', 'train_result', and 'best_params'.
        """
        # Step 1: Tune hyperparams via CV
        tune_result = self.hyperparameter_tune(
            source=source, event_type=event_type,
            n_episodes=n_episodes, max_steps=max_steps,
            n_folds=n_folds, use_schema_features=use_schema_features,
            lr_values=lr_values, gamma_values=gamma_values,
            eps_values=eps_values,
            register_tune_model=register_tune_model,
        )

        if "error" in tune_result:
            return {"error": tune_result["error"], "tune_result": tune_result}

        best_params = tune_result.get("best_params", {})

        # Step 2: Train final model with best params on all data
        train_result = self.train_from_memory(
            source=source, event_type=event_type,
            n_episodes=n_episodes, max_steps=max_steps,
            learning_rate=best_params.get("learning_rate", 0.1),
            discount_factor=best_params.get("discount_factor", 0.9),
            epsilon=best_params.get("epsilon", 0.1),
            use_schema_features=use_schema_features,
        )

        return {
            "tune_result": tune_result,
            "train_result": train_result,
            "best_params": best_params,
            "best_score": tune_result.get("best_score"),
        }

    def train_episode(self, env, agent, max_steps: int = 1000,
                        learning_rate: float = 0.1,
                        discount_factor: float = 0.9) -> Dict[str, Any]:
        """Run a single training episode (backward-compatible mock training).

        Re-engineered in place (Oct-19): each mock step now applies the same
        Q-learning update as train_from_memory, so mock episodes grow the
        shared Q-table (visible via get_training_stats/recommend) instead of
        only appending dead stats. Signature stays backward-compatible.
        """
        state = getattr(env, "reset", lambda: 0)()
        total_reward = 0.0
        steps = 0
        q_updates = 0
        for _ in range(max_steps):
            action = getattr(agent, "act", lambda s: 0)(state)
            try:
                next_state, reward, done, *_ = getattr(env, "step", lambda a: (0, 0, True, {}))(action)
            except Exception as exc:  # noqa: BLE001 — broken env stops episode
                note_failure("learning_rl:L897", exc)
                break
            reward = float(reward)
            state_key = "mock:%s" % (repr(state),)
            try:
                action_idx = int(action)
            except (TypeError, ValueError):
                action_idx = 0
            old_q = self._q_table[state_key].get(action_idx, 0.0)
            next_max_q = max(self._q_table[state_key].values(), default=0.0)
            self._q_table[state_key][action_idx] = old_q + learning_rate * (
                reward + discount_factor * next_max_q - old_q)
            q_updates += 1
            total_reward += reward
            state = next_state
            steps += 1
            if done:
                break
        episode = {"steps": steps, "total_reward": total_reward, "timestamp": time.time(),
                   "q_updates": q_updates}
        with self._lock:
            self._episodes.append(episode)
        return episode
    def get_training_stats(self) -> Dict[str, Any]:
        with self._lock:
            if not self._episodes:
                return {"episodes": 0}
            rewards = [e["total_reward"] for e in self._episodes]
            return {"episodes": len(rewards), "avg_reward": round(sum(rewards) / len(rewards), 2),
                    "max_reward": max(rewards), "min_reward": min(rewards),
                    "q_table_size": len(self._q_table)}

    def recommend(self, severity: str, source: str = "db") -> Dict[str, Any]:
        """Advisory action query over the learned Q-table (2026-10-03).

        Read-only: returns the argmax action for `source:severity`, falls
        back to any `*:severity` key when the source is unseen, and returns
        action -1 / known False (never raises) when nothing is known. A
        monitor calls this to ATTACH advice; firing decisions stay with the
        monitor, so a wrong table cannot trigger anything by itself.
        """
        sev = str(severity or "")
        src = str(source or "")
        key = "%s:%s" % (src, sev)
        try:
            table = getattr(self, "_q_table", None) or {}
            row = table.get(key)
            if row is None:
                suffix = ":" + sev
                for k, v in table.items():
                    if isinstance(k, str) and k.endswith(suffix) and v:
                        row, key = v, k
                        break
            if row is None:
                try:
                    loader = getattr(self, "load_q_table_from_store", None)
                    if callable(loader):
                        loader()
                except Exception as exc:
                    note_failure("learning_rl:L943", exc) # scanner:ignore
                table = getattr(self, "_q_table", None) or {}
                row = table.get(key)
            if not row:
                return {"action": -1, "action_name": "unknown",
                        "known": False, "state_key": key, "source": src}
            action = max(row, key=lambda a: row.get(a, 0.0))
            names = list(getattr(self, "ACTION_NAMES",
                                 ["ignore", "log", "alert", "remediate"]))
            try:
                action = int(action)
            except (TypeError, ValueError) as exc:
                note_failure("learning_rl:L955", exc) # scanner:ignore
            name = (names[action] if isinstance(action, int)
                    and 0 <= action < len(names) else "unknown")
            return {"action": action, "action_name": name, "known": True,
                    "state_key": key, "source": src}
        except Exception as exc:
            note_failure("learning_rl:L961", exc)
            return {"action": -1, "action_name": "unknown",
                    "known": False, "state_key": key, "source": src}

    def tune_history(self, limit: int = 20) -> Dict[str, Any]:
        """Query tune_config_registered events from LocalMemoryStore.

        Queries all 'tune_config_registered' events to show the
        progression of best_params and best scores over time.

        Args:
            limit: Max history entries to return.

        Returns:
            Dict with history list and summary stats.
        """
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            events = store.search_memories(
                "tune_config_registered", source="RLTrainLoop", limit=limit
            )

            history = []
            for ev in events:
                raw_data = ev.get("data", {})
                if isinstance(raw_data, str):
                    try:
                        data = json.loads(raw_data)
                    except Exception as exc:  # noqa: BLE001 — skip malformed serialized payload
                        note_failure("learning_rl:L990", exc)
                        continue
                else:
                    data = raw_data
                if isinstance(data, dict) and "best_params" in data:
                    history.append({
                        "registered_at": ev.get("timestamp", ""),
                        "best_params": data["best_params"],
                        "best_score": data.get("best_score"),
                        "n_configs_tried": data.get("n_configs_tried"),
                        "n_folds": data.get("n_folds"),
                        "data_source": data.get("data_source"),
                    })

            # Compute progression summary
            scores = [h["best_score"] for h in history if h["best_score"] is not None]

            return {
                "history": history,
                "count": len(history),
                "summary": {
                    "best_overall": max(scores) if scores else None,
                    "worst_overall": min(scores) if scores else None,
                    "avg_score": round(sum(scores) / len(scores), 4) if scores else None,
                    "n_tune_runs": len(history),
                } if scores else {},
            }
        except Exception as e:  # noqa: BLE001 — tuning failure returns signed error
            note_failure("learning_rl:L1017", e)
            return {"error": str(e), "history": []}
