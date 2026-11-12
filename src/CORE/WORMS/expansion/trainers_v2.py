"""
trainers_v2.py -- New trainer architecture with per-worm hook identity.

Design principles:
- Every trainer carries a worm_id (the worm it acts on behalf of)
- worm_id propagates through all memory writes (data, tags, source)
- Hook system can filter/attribute by worm_id
- Clean break from old trainers (RLTrainLoop, GRPOTrainer, etc.)
"""

from __future__ import annotations

import os
import sys
import json
import time
import threading
import hashlib
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable
from collections import defaultdict, deque
from dataclasses import dataclass, field

try:
    from CORE.failure_ledger import note_failure
except ImportError:  # never break the host
    def note_failure(where, exc=None):
        print(f"[trainers_v2] {where}: {exc}", flush=True)
from pathlib import Path

# Add src to path for imports
_src = str(Path(__file__).resolve().parent.parent.parent)
if _src not in sys.path:
    sys.path.insert(0, _src)


@dataclass
class TrainerContext:
    """Context carried by every trainer invocation."""
    worm_id: str                          # The worm this trainer acts for
    task_id: str                          # Unique task/run identifier
    task_type: str                        # e.g., "intake", "grpo", "offline", "sb3"
    session_id: Optional[str] = None
    correlation_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_tags(self) -> List[str]:
        """Convert context to tags for memory writes."""
        base = [f"worm:{self.worm_id}", f"task:{self.task_id}", f"type:{self.task_type}"]
        if self.session_id:
            base.append(f"session:{self.session_id}")
        return base + self.tags

    def to_source(self, trainer_name: str) -> str:
        """Generate source string with worm identity."""
        return f"{trainer_name}[worm:{self.worm_id}]"

    def enrich_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Inject worm identity into data payload."""
        return {
            **data,
            "_worm_id": self.worm_id,
            "_task_id": self.task_id,
            "_task_type": self.task_type,
            "_trainer_ts": datetime.now(timezone.utc).isoformat(),
        }


class HookAwareTrainer:
    """Base class for trainers with per-worm hook identity."""

    def __init__(self, context: TrainerContext):
        self.context = context
        self._lock = threading.Lock()
        self._memory_store = None  # Lazy init

    @property
    def memory_store(self):
        """Lazy-load memory store."""
        if self._memory_store is None:
            try:
                from CORE.local_memory import get_memory_store
                self._memory_store = get_memory_store()
            except Exception:
                self._memory_store = None
        return self._memory_store

    def _record(self, event_type: str, data: Dict[str, Any],
                severity: str = "info", extra_tags: Optional[List[str]] = None) -> int:
        """Record event with worm identity propagated."""
        store = self.memory_store
        if not store:
            return 0

        enriched = self.context.enrich_data(data)
        tags = self.context.to_tags()
        if extra_tags:
            tags.extend(extra_tags)

        source = self.context.to_source(self.__class__.__name__)

        return store.record_event(
            source=source,
            event_type=event_type,
            data=enriched,
            severity="info",
            tags=tags,
            session_id=self.context.session_id,
            correlation_id=self.context.correlation_id,
        )

    def _record_error(self, error: Exception, where: str) -> None:
        """Record error with worm identity."""
        self._record(
            event_type="trainer_error",
            data={"where": where, "error": str(error), "type": type(error).__name__},
            severity="error",
        )

    def train(self) -> Dict[str, Any]:
        """Override in subclass. Return result dict."""
        raise NotImplementedError


# ════════════════════════════════════════════════════════════════════════════
# NEW RLTrainLoop v2 -- Q-learning with worm identity
# ════════════════════════════════════════════════════════════════════════════

class RLTrainLoopV2(HookAwareTrainer):
    """Q-learning trainer with per-worm hook identity.

    Replaces the old RLTrainLoop. Every Q-table update, episode, and
    memory write carries the worm_id.
    """

    VERSION = "2.0.0"

    def __init__(self, context: TrainerContext, alpha: float = 0.1,
                 gamma: float = 0.99, epsilon: float = 0.1):
        super().__init__(context)
        self._alpha = alpha
        self._gamma = gamma
        self._epsilon = epsilon
        self._q_table: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
        self._episodes: List[Dict] = []

    def train_from_memory(self, records: List[Dict], n_episodes: int = 60,
                          max_steps: int = 50, **kwargs) -> Dict[str, Any]:
        """Train Q-table from technique outcome records."""
        if not records:
            return {"error": "no records", "success": False}

        self._record("train_start", {"n_records": len(records), "n_episodes": n_episodes})

        # Load existing Q-table for this worm
        self._load_q_table()

        total_reward = 0.0
        for ep in range(n_episodes):
            ep_reward = self._run_episode(records, max_steps)
            total_reward += ep_reward

            # Record episode with worm identity
            self._record("episode_complete", {
                "episode": ep,
                "reward": ep_reward,
                "q_table_size": sum(len(v) for v in self._q_table.values()),
            })

        # Persist Q-table for this worm
        self._save_q_table()

        return {
            "success": True,
            "avg_reward": total_reward / max(1, n_episodes),
            "q_table_size": sum(len(v) for v in self._q_table.values()),
            "worm_id": self.context.worm_id,
        }

    def _run_episode(self, records: List[Dict], max_steps: int) -> float:
        """Run one training episode."""
        total = 0.0
        for _ in range(max_steps):
            rec = random.choice(records)
            state = self._extract_state(rec)
            action = self._choose_action(state)
            reward = self._compute_reward(rec, action)
            next_state = self._extract_next_state(rec)

            # Q-learning update
            self._update_q(state, action, reward, next_state)
            total += reward

        return total

    def _extract_state(self, record: Dict) -> str:
        """Extract state key from record."""
        hook = record.get("hook", "unknown")
        severity = record.get("severity", "info")
        ok = record.get("data", {}).get("ok", False) if isinstance(record.get("data"), dict) else False
        return f"{hook}|{severity}|{ok}"

    def _extract_next_state(self, record: Dict) -> str:
        return self._extract_state(record)  # Simplified

    def _choose_action(self, state: str) -> int:
        if random.random() < self._epsilon:
            return random.randint(0, 1)
        q_vals = self._q_table[state]
        if not q_vals:
            return 0
        return max(q_vals, key=lambda k: q_vals[k])

    def _compute_reward(self, record: Dict, action: int) -> float:
        data = record.get("data", {}) if isinstance(record.get("data"), dict) else {}

        # Check explicit success field first (used by alert/health events)
        success = data.get("success")
        if success is not None:
            # Explicit success/failure - use it directly
            return 1.0 if success else -1.0

        # Check ok field (used by technique outcomes)
        ok = data.get("ok")
        if ok is not None:
            # Technique outcome - reward for success
            return 1.0 if ok else -1.0

        # Check severity for explicit failure signals
        severity = record.get("severity", "info")
        if severity == "error" or severity == "critical":
            # Explicit error/critical = failure
            return -1.0
        elif severity == "warning":
            # Warning = mild penalty
            return -0.5

        # No clear signal = neutral (informational)
        return 0.0

    def _update_q(self, state: str, action: int, reward: float, next_state: str):
        current = self._q_table[state][action]
        max_next = max(self._q_table[next_state].values()) if self._q_table[next_state] else 0.0
        self._q_table[state][action] = current + self._alpha * (reward + self._gamma * max_next - current)

    def _load_q_table(self):
        """Load persisted Q-table for this worm."""
        store = self.memory_store
        if not store:
            return
        try:
            rows = store.query_memories(
                event_type="q_table_snapshot",
                source=f"RLTrainLoopV2[worm:{self.context.worm_id}]",
                limit=1,
            )
            if rows:
                data = json.loads(rows[0].get("data", "{}"))
                self._q_table = defaultdict(lambda: defaultdict(float))
                for k, v in data.items():
                    self._q_table[k] = defaultdict(float, {int(ak): av for ak, av in v.items()})
        except Exception as exc:
            # Fresh start: load failure resets to empty rather than
            # leaving a half-built table in place.
            note_failure("trainers_v2:qtable_load", exc)
            self._q_table = defaultdict(lambda: defaultdict(float))

    def _save_q_table(self):
        """Persist Q-table for this worm."""
        store = self.memory_store
        if not store:
            return
        try:
            serializable = {k: dict(v) for k, v in self._q_table.items()}
            self._record("q_table_snapshot", {"q_table": serializable})
        except Exception as exc:
            note_failure("trainers_v2:qtable_save", exc)


# ═════════════════════════════════════════════════════════════════════════════
# NEW GRPOTrainer v2 -- Group Relative Policy Optimization with worm identity
# ═════════════════════════════════════════════════════════════════════════════

class GRPOTrainerV2(HookAwareTrainer):
    """GRPO trainer with per-worm hook identity.

    Replaces the old GRPOTrainer. Every group generation, reward computation,
    and policy update carries the worm_id.
    """

    VERSION = "2.0.0"

    def __init__(self, context: TrainerContext, beta: float = 0.04, lr: float = 0.01):
        super().__init__(context)
        self._beta = beta
        self._lr = lr
        self._policy: Dict[str, Dict[str, float]] = defaultdict(dict)
        self._lock = threading.Lock()

    def generate_group(self, task_context: Dict, group_size: int = 8) -> List[str]:
        """Generate candidate group for a task."""
        # This would query WORM_REGISTRY filtered by task_context
        # For now return mock - real impl queries worm registry
        self._record("group_generated", {"task_context": task_context, "group_size": 8})
        return [f"Worm_{i}" for i in range(group_size)]

    def compute_group_rewards(self, group: List[str], task_context: Dict) -> List[float]:
        """Compute rewards for each candidate in group."""
        # Real impl runs each worm and measures outcome
        rewards = [random.random() for _ in group]
        self._record("group_rewards", {"group": group, "rewards": rewards})
        return rewards

    def update_policy(self, group: List[str], rewards: List[float], context_key: str = "default"):
        """Update policy using group-normalized advantages (GRPO)."""
        mean_r = sum(rewards) / len(rewards)
        advantages = [(r - mean_r) for r in rewards]

        with self._lock:
            for worm, adv in zip(group, advantages):
                self._policy[context_key][worm] = self._policy[context_key].get(worm, 0.0) + self._lr * adv

        self._record("policy_update", {"context_key": context_key, "updates": len(group)})

    def select_best(self, task_context: Dict) -> Optional[str]:
        """Select best worm for a task context."""
        context_key = task_context.get("context_key", "default")
        policy = self._policy.get(context_key, {})
        if not policy:
            return None
        return max(policy, key=lambda k: policy[k])


# ═════════════════════════════════════════════════════════════════════════════
# NEW SB3Trainer v2 -- Stable Baselines 3 wrapper with worm identity
# ═════════════════════════════════════════════════════════════════════════════

class SB3TrainerV2(HookAwareTrainer):
    """SB3 trainer wrapper with per-worm hook identity.

    Wraps stable-baselines3 algorithms (PPO, DQN, SAC, etc.) and ensures
    all memory writes carry worm_id.
    """

    VERSION = "2.0.0"

    SUPPORTED_ALGOS = ["PPO", "DQN", "A2C", "SAC", "TD3", "DDPG", "QRDQN", "TRPO", "TQC", "ARS", "MaskablePPO"]

    def __init__(self, context: TrainerContext, algo: str, **algo_kwargs):
        super().__init__(context)
        if algo not in self.SUPPORTED_ALGOS:
            raise ValueError(f"Unsupported algo: {algo}. Supported: {self.SUPPORTED_ALGOS}")
        self._algo = algo
        self._algo_kwargs = algo_kwargs
        self._model = None

    def train(self, env, total_timesteps: int = 10000, **kwargs) -> Dict[str, Any]:
        """Train SB3 model with worm identity."""
        try:
            from stable_baselines3 import PPO, DQN, A2C, SAC, TD3, DDPG
            from stable_baselines3.common.vec_env import DummyVecEnv
            from stable_baselines3.common.callbacks import BaseCallback
        except ImportError as e:
            return {"error": f"SB3 not available: {e}", "success": False, "worm_id": self.context.worm_id}

        self._record("sb3_train_start", {"algo": self._algo, "timesteps": total_timesteps})

        # Map algo string to class
        algo_map = {"PPO": PPO, "DQN": DQN, "A2C": A2C, "SAC": SAC, "TD3": TD3, "DDPG": DDPG}
        AlgoClass = algo_map.get(self._algo)
        if not AlgoClass:
            return {"error": f"Algo {self._algo} not mapped", "success": False}

        # Wrap env for worm identity
        try:
            vec_env = DummyVecEnv([lambda: env])
        except Exception as e:
            return {"error": f"Invalid env: {e}", "success": False,
                    "worm_id": self.context.worm_id}

        self._model = AlgoClass("MlpPolicy", vec_env, verbose=0, **self._algo_kwargs)

        # Train with callback that records worm identity
        class WormIdentityCallback(BaseCallback):
            def __init__(self, trainer):
                super().__init__()
                self.trainer = trainer

            def _on_step(self) -> bool:
                if self.num_timesteps % 1000 == 0:
                    self.trainer._record("sb3_step", {
                        "timestep": self.num_timesteps,
                        "algo": self.trainer._algo,
                    })
                return True

        callback = WormIdentityCallback(self)
        self._model.learn(total_timesteps=total_timesteps, callback=callback)

        # Evaluate
        mean_reward = self._evaluate(env)

        self._record("sb3_train_complete", {
            "algo": self._algo,
            "timesteps": total_timesteps,
            "mean_reward": mean_reward,
        })

        return {
            "success": True,
            "algo": self._algo,
            "mean_reward": mean_reward,
            "worm_id": self.context.worm_id,
        }

    def _evaluate(self, env, n_episodes: int = 5) -> float:
        rewards = []
        for _ in range(n_episodes):
            obs = env.reset()
            if isinstance(obs, tuple):  # gymnasium>=0.26 (obs, info)
                obs = obs[0]
            done = False
            ep_reward = 0
            while not done:
                action, _ = self._model.predict(obs, deterministic=True)  # type: ignore[union-attr]
                out = env.step(action)
                if len(out) == 5:  # gymnasium>=0.26
                    obs, reward, terminated, truncated, _ = out
                    done = terminated or truncated
                else:  # legacy gym 4-tuple
                    obs, reward, done, _ = out
                ep_reward += reward
            rewards.append(ep_reward)
        return sum(rewards) / len(rewards) if rewards else 0.0


# ═════════════════════════════════════════════════════════════════════════════
# Factory for creating trainers with worm identity
# ═════════════════════════════════════════════════════════════════════════════

def create_trainer(task_type: str, worm_id: str, task_id: Optional[str] = None,
                   session_id: Optional[str] = None, **kwargs) -> HookAwareTrainer:
    """Factory to create appropriate trainer for a task type with worm identity."""
    if task_id is None:
        task_id = f"{task_type}_{worm_id}_{int(time.time())}"

    ctx = TrainerContext(
        worm_id=worm_id,
        task_id=task_id,
        task_type=task_type,
        session_id=session_id,
    )

    if task_type in ("intake", "offline", "rl"):
        return RLTrainLoopV2(ctx, **kwargs)
    elif task_type == "grpo":
        return GRPOTrainerV2(ctx, **kwargs)
    elif task_type.lower() in ("ppo", "dqn", "a2c", "sac", "td3", "ddpg", "qrdqn", "trpo", "tqc", "ars", "maskableppo"):
        _canon = next((a for a in SB3TrainerV2.SUPPORTED_ALGOS
                       if a.lower() == task_type.lower()), task_type.upper())
        return SB3TrainerV2(ctx, _canon, **kwargs)
    else:
        raise ValueError(f"Unknown task_type: {task_type}")


# ═════════════════════════════════════════════════════════════════════════════
# Integration with training orchestrator
# ═════════════════════════════════════════════════════════════════════════════

def run_intake_cycle_v2(worm_id: str, limit: int = 500, **kwargs) -> Dict[str, Any]:
    """Run intake cycle with worm identity."""
    from CORE.training_intake import build_training_records
    from CORE.local_memory import get_memory_store

    task_id = f"intake_{worm_id}_{int(time.time())}"
    ctx = TrainerContext(worm_id=worm_id, task_id=task_id, task_type="intake")

    built = build_training_records(limit=limit)
    records = built.get("records") or []

    if not records:
        return {"success": False, "worm_id": worm_id, "note": "no records"}

    trainer = RLTrainLoopV2(ctx)
    result = trainer.train_from_memory(records, **kwargs)

    store = get_memory_store()
    if store:
        store.record_event(
            source=f"RLTrainLoopV2[worm:{worm_id}]",
            event_type="intake_training_orchestrated",
            data={**result, "worm_id": worm_id, "intake_hooks": len(built.get("by_hook") or {})},
            severity="info",
            tags=[f"worm:{worm_id}", "intake", "training"],
        )

    return {"success": True, "worm_id": worm_id, **result}


def run_grpo_cycle_v2(worm_id: str, group_size: int = 8, **kwargs) -> Dict[str, Any]:
    """Run GRPO cycle with worm identity."""
    ctx = TrainerContext(worm_id=worm_id, task_id=f"grpo_{worm_id}_{int(time.time())}", task_type="grpo")
    trainer = GRPOTrainerV2(ctx, **kwargs)

    task_context = {"task_type": "scheduled_training", "category": "utility"}
    group = trainer.generate_group(task_context, group_size)
    rewards = trainer.compute_group_rewards(group, task_context)
    trainer.update_policy(group, rewards)

    return {
        "success": True,
        "worm_id": worm_id,
        "group": group,
        "rewards": rewards,
        "best_worm": trainer.select_best(task_context),
    }


# Remove the old trainers from the namespace
# This ensures they can't be accidentally reused
# RLTrainLoop, GRPOTrainer, etc. should be imported from trainers_v2 instead

__all__ = [
    "TrainerContext",
    "HookAwareTrainer",
    "RLTrainLoopV2",
    "GRPOTrainerV2",
    "SB3TrainerV2",
    "create_trainer",
    "run_intake_cycle_v2",
    "run_grpo_cycle_v2",
]
