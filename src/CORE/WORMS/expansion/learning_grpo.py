# coding: utf-8
"""
learning_grpo.py — GRPOTrainer + ABTestRunner (fast-loading focused module)

Extracted from learning.py (103K monolith) to avoid loading the entire
module when only GRPO or A/B testing is needed. This module is ~600 lines
and loads in milliseconds vs 300s+ for the full learning.py.

Used by:
- WORM_SELECTOR.py (GRPO policy for worm selection)
- scheduling.py (GRPO for scheduled training pipeline)
"""

import copy
import json
import math
import time
import random
import threading
from typing import Any, Callable, List, Optional
import json as _json

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
    note_failure("learning_grpo:L25", exc)
    _REGISTRY = False
    def register_worm(**kw):
        return lambda cls: cls


# -- Reward-scale co-tabulation (memory unity): decomposed shadow terms -----
try:
    from CORE.reward_scales import emit_canonical as _emit_canonical
except ImportError as exc:                               # never break the host
    note_failure("learning_grpo:reward-scales", exc)
    _emit_canonical = None  # type: ignore


# -- Lifecycle-owned collaborators (Lane C redo, 2026-10-22): first-party,
# cycle-free, declared ONCE at module load (None fallback each); __init__
# injects them as instance collaborators (module globals are the defaults).
try:
    from CORE.WORMS.expansion.policy_optimization import depo_filter as _depo_filter
    _DEPO_OK = True
except ImportError as exc:
    note_failure("learning_grpo:depo-import", exc)
    _depo_filter = None  # type: ignore
    _DEPO_OK = False
try:
    from CORE.latent_refine import bounded_correct as _bounded_correct
    _REFINE_OK = True
except ImportError as exc:
    note_failure("learning_grpo:refine-import", exc)
    _bounded_correct = None  # type: ignore
    _REFINE_OK = False
try:
    from CORE.skill_library import distill_trajectory_to_protocol as _distill_proto
    _DISTILL_OK = True
except ImportError as exc:
    note_failure("learning_grpo:distill-import", exc)
    _distill_proto = None  # type: ignore
    _DISTILL_OK = False
try:
    from CORE.local_memory import LocalMemoryStore as _LocalMemoryStore
    _STORE_OK = True
except ImportError as exc:
    note_failure("learning_grpo:store-import", exc)
    _LocalMemoryStore = None  # type: ignore
    _STORE_OK = False


def _softmax(scores: dict) -> dict[str, float]:
    """Numerically-stable softmax; log() of it is the log-softmax (EP351)."""
    if not scores:
        return {}
    shift = max(scores.values())
    exps = {k: math.exp(v - shift) for k, v in scores.items()}
    denom = sum(exps.values()) or 1.0
    return {k: v / denom for k, v in exps.items()}


# -- GRPO Trainer -----------------------------------------------------------


class GRPOTrainer:
    """Group Relative Policy Optimization for worm selection.

    For each task, generates a group of candidate worms, scores them
    relative to each other, and updates the policy using group-normalized
    advantages. No separate critic model needed (unlike PPO).

    Based on DeepSeek-R1's GRPO approach: normalize rewards within
    each group to compute advantages, then apply policy gradient.

    Usage:
        trainer = GRPOTrainer()

        # Generate candidates for a task
        candidates = trainer.generate_group(task_context, group_size=8)

        # Run each candidate and record rewards
        rewards = trainer.compute_group_rewards(candidates, task_context)

        # Update policy
        trainer.update_policy(candidates, rewards)

        # Get recommended worm for a task
        best = trainer.select_best(task_context)
    """

    VERSION = "1.0.0"

    def __init__(self, beta: float = 0.04, lr: float = 0.01, load_policy: bool = True,
                 step_decay: float = 0.0, dense_reward_weight: float = 0.0,
                 trust_region: float = 1.0, frontier_filter=None,
                 corrector=None, protocol_distiller=None,
                 protocol_store=None):
        """
        Args:
            beta: KL penalty coefficient (higher = more conservative).
            lr: Policy learning rate.
            load_policy: If True, auto-load persisted policy from LocalMemoryStore
                          on init. set to False for testing with clean state.
            step_decay: EP350 sampling schedule. In (0, 1] the group sampler
                          leans toward the learned policy as updates
                          accumulate (temperature = step_decay ** n_updates).
                          0.0 (the default) leaves the sampler untouched.
            dense_reward_weight: EP351. > 0 fuses the checkpoint-pair implicit
                          reward into each group's rewards before the policy
                          update. 0.0 (the default) leaves them untouched.
            trust_region: EP472. L2 bound on the per-update correction vector
                          (bounded_correct trust-region clip). Must be > 0;
                          a non-positive value degrades to the unclipped
                          update via the corrector's own gap path.
            frontier_filter: EP412 DEPO filter (lifecycle collaborator;
                          default module-level depo_filter, None disables).
            corrector: EP472 LRT mask-corrector (default bounded_correct).
            protocol_distiller: EP401 distiller (default
                          distill_trajectory_to_protocol).
            protocol_store: EP401 replay-protocol store (default
                          LocalMemoryStore; None disables).
        """
        self._beta = beta
        self._lr = lr
        self._policy: dict[str, dict[str, float]] = {}  # context -> {worm: score}
        self._history: list[dict] = []
        self._anchors: list[dict] = []  # anchor snapshots (EP360, Oct-10)
        self._replay: list[dict] = []   # EP350 offline replay buffer
        self._step_decay = float(step_decay)                    # EP350
        self._dense_reward_weight = float(dense_reward_weight)  # EP351
        self._trust_region = float(trust_region)                # EP472
        # Lifecycle collaborators: resolved ONCE here, never imported per call.
        self._frontier_filter = (frontier_filter if frontier_filter is not None
                                 else _depo_filter)
        self._corrector = (corrector if corrector is not None
                           else _bounded_correct)
        self._protocol_distiller = (protocol_distiller
                                    if protocol_distiller is not None
                                    else _distill_proto)
        self._protocol_store = (protocol_store if protocol_store is not None
                                else _LocalMemoryStore)
        self._lock = threading.Lock()
        # Auto-load persisted policy from LocalMemoryStore
        if load_policy:
            self.load_policy()

    def generate_group(self, task_context: dict, group_size: int = 8) -> list[str]:
        """Generate a group of candidate worms for a task.

        Selects candidates from WORM_REGISTRY using SpawnDiversityEngine
        weighted selection, with some random exploration.

        Args:
            task_context: dict with task_type, category, etc.
            group_size: Number of candidates to generate.

        Returns:
            list of worm names.
        """
        try:
            from CORE.worm_registry import WORM_REGISTRY
        except ImportError as exc:
            note_failure("learning_grpo:L94", exc)
            return []

        registry = WORM_REGISTRY
        if not registry:
            return []

        # Get all worm names, prefer matching category
        preferred_category = task_context.get("category", "")
        all_worms = list(registry)

        # Split into preferred (matching category) and others
        preferred = [w for w in all_worms
                     if registry[w].get("category") == preferred_category]
        others = [w for w in all_worms if w not in preferred]

        # Shuffle both lists
        random.shuffle(preferred)
        random.shuffle(others)

        # EP350 sampling schedule: as updates accumulate the sampler leans on
        # the learned policy instead of pure chance. Disabled by default, so
        # step_decay=0.0 => temperature 1.0 => this never reorders anything.
        temperature = self.sampling_temperature()
        if temperature < 1.0 and random.random() > temperature:
            with self._lock:
                ctx_pol = dict(self._policy.get(
                    task_context.get("context_key", "default"), {}))
            preferred.sort(key=lambda w: ctx_pol.get(w, 0.0), reverse=True)
            others.sort(key=lambda w: ctx_pol.get(w, 0.0), reverse=True)

        # EP412 (DEPO): filter the pool to the competence frontier BEFORE
        # selection. Estimate = live policy (1 - min-max-normalized score).
        # Cold policy (<2 distinct scores) or absent members pass through:
        # no estimate is not evidence of mastery. Fail-open: full pool.
        pool = preferred + others
        # EP412: lifecycle collaborator resolved in __init__; no per-call import.
        if self._frontier_filter is not None:
            try:
                with self._lock:
                    ctx_pol = dict(self._policy.get(
                        task_context.get("context_key", "default"), {}))
                scored = {w: ctx_pol[w] for w in pool if w in ctx_pol}
                if len(set(scored.values())) >= 2:
                    lo = min(scored.values())
                    span = (max(scored.values()) - lo) or 1.0
                    difficulties = {w: 1.0 - (s - lo) / span
                                    for w, s in scored.items()}
                    frontier = set(self._frontier_filter(difficulties))
                    pool = [w for w in pool if w in frontier or w not in scored]
            except Exception as exc:
                note_failure("learning_grpo:depo-filter", exc)

        # Fill group: preferred first, then others
        candidates = []
        for w in pool:
            if len(candidates) >= group_size:
                break
            if w not in candidates:
                candidates.append(w)

        return candidates[:group_size]

    def compute_group_rewards(self, group: list[str], task_context: dict,
                               evaluator: Optional[Callable] = None) -> list[float]:
        """Compute rewards for each candidate in the group.

        Args:
            group: list of worm names.
            task_context: Task context dict.
            evaluator: Optional callable(worm_name, context) -> reward.
                      If None, uses historical performance from LocalMemoryStore.

        Returns:
            list of reward floats (one per candidate, same order).
        """
        if evaluator:
            return [evaluator(w, task_context) for w in group]

        # Default: score from REAL execution history. The old default queried
        # event_type="worm_execution" — a legacy event NO producer records, so
        # every worm scored a flat 0.0 and the GRPO policy update was a no-op
        # (fix, 2026-09-08). Real signal streams, in preference order:
        #   1. cluster_execution_completed / category_cluster_executed
        #      (modern cluster events, recorded by CLUSTERWORMS/CLUSTERWORMS)
        #   2. any recent events from that worm source (fallback), minus
        #      self-recorded training bookkeeping
        # Reward = success_rate (success = severity != error). Duration is
        # emitted canonically but no longer subtracted (fusion removed, #1037).
        rewards = []
        try:
            from CORE.local_memory import LocalMemoryStore
            store = LocalMemoryStore()
            event_prefs = ("cluster_execution_completed", "category_cluster_executed")
            for worm_name in group:
                records = []
                for et in event_prefs:
                    records = store.query_memories(
                        source=worm_name, event_type=et, limit=20)
                    if records:
                        break
                if not records:
                    records = store.query_memories(source=worm_name, limit=20)
                if records:
                    success_count = sum(1 for r in records if r.get("severity") != "error")
                    success_rate = success_count / len(records)
                    avg_duration = 0.0
                    for r in records:
                        data = r.get("data", {})
                        if isinstance(data, dict):
                            avg_duration += data.get("duration_ms", 0)
                    avg_duration /= max(len(records), 1)
                    if _emit_canonical is not None:
                        try:
                            _emit_canonical(store, success_rate, "rate_01",
                                            "grpo:success_rate",
                                            worm=worm_name)
                            _emit_canonical(store, avg_duration, "millis",
                                            "grpo:avg_duration_ms",
                                            worm=worm_name)
                        except Exception as exc:
                            note_failure("learning_grpo:reward-shadow", exc)
                    # millis baseline is unusable (n=1, zero variance) -> to_common_scale
                    # refuses; fusion removed. reward derives from rate_01 alone.
                    # (See Engram #1037.) Both quantities still emitted canonically.
                    reward = success_rate
                else:
                    reward = 0.0  # No execution history yet: neutral reward
                rewards.append(reward)
        except Exception as exc:
            note_failure("learning_grpo:L180", exc)
            rewards = [0.0] * len(group)

        return rewards

    def compute_advantages(self, rewards: list[float],
                             global_stats: tuple | None = None) -> list[float]:
        """Group-normalized advantages + REINFORCE++ global-norm fallback.

        DAPO: if std_r < 1e-4 (zero-variance group) return [] to signal skip.
        REINFORCE++: if global_stats (mean, std) given, use global std.
        """
        if not rewards:
            return []
        mean_r = sum(rewards) / len(rewards)
        variance = sum((r - mean_r) ** 2 for r in rewards) / len(rewards)
        std_r = variance ** 0.5
        if std_r < 1e-4:
            return []  # DAPO: drop zero-gradient group, resample task
        if global_stats is not None:
            _, gstd = global_stats
            if gstd and gstd > 1e-8:
                std_r = gstd  # REINFORCE++ unbiased global norm
        return [(r - mean_r) / std_r for r in rewards]

    def compute_mgpo_advantages(self, rewards: list[float],
                                subgroups: list[list[int]] | None = None,
                                coarse_weight: float = 0.5) -> list[float]:
        """Multi-granularity group-relative advantages (EP155, MGPO).

        Whole-group normalisation washes out sub-group signal: a strong
        member of a weak sub-group reads average. MGPO computes the same
        group-norm at two granularities — coarse (whole group) and fine
        (each index-sublist in `subgroups`) — and blends them:
        adv = coarse_weight * coarse + (1 - coarse_weight) * fine.
        Members in no sub-group (or empty `subgroups`) keep the coarse
        value. Zero-variance levels fall back to 0.0 for their members
        instead of dropping the group — the caller, not this function,
        decides resampling. Pure function: no store, no policy write.
        """
        if not rewards:
            return []
        coarse = self.compute_advantages(list(rewards))
        if not coarse:
            coarse = [0.0] * len(rewards)
        if not subgroups:
            return coarse
        fine = [0.0] * len(rewards)
        for sub in subgroups:
            idx = [i for i in (sub or []) if 0 <= i < len(rewards)]
            if len(idx) < 2:
                for i in idx:
                    fine[i] = coarse[i]
                continue
            sub_rewards = [rewards[i] for i in idx]
            sub_adv = self.compute_advantages(sub_rewards)
            if not sub_adv:
                sub_adv = [0.0] * len(idx)
            for i, a in zip(idx, sub_adv):
                fine[i] = a
        covered = {i for sub in subgroups for i in (sub or [])
                   if 0 <= i < len(rewards)}
        for i in range(len(rewards)):
            if i not in covered:
                fine[i] = coarse[i]
        cw = min(1.0, max(0.0, float(coarse_weight)))
        return [cw * c + (1.0 - cw) * f
                for c, f in zip(coarse, fine)]

    def update_policy(self, group: list[str], rewards: list[float],
                      context_key: str = "default",
                      record_replay: bool = True):
        """Update the policy using GRPO.

        Applies policy gradient with group-normalized advantages:
        policy[worm] += lr * advantage * (1 - beta * kl_penalty)

        Every live update also self-ingests its (context, group, rewards)
        as a replay trajectory (in-memory buffer, capped), so the offline
        replay path always has material from real runs — the loop accounts
        for the EP350 integration instead of leaving it callerless.
        replay_offline_step passes record_replay=False: its steps already
        come from the buffer, and re-buffering them would churn it.

        Args:
            group: list of worm names.
            rewards: list of rewards (same order as group).
            context_key: Key for the policy context (e.g., task_type).
            record_replay: buffer this update as a replay trajectory.
        """
        # EP351: the dense checkpoint-pair implicit reward is fused at the one
        # point rewards enter the policy update. Weight 0.0 (default) => no-op.
        if self._dense_reward_weight > 0.0:
            rewards = self.apply_dense_reward(group, rewards, context_key)

        advantages = self.compute_advantages(rewards)

        # DAPO: skip no-signal groups instead of no-op update. The group still
        # ran in this context, so register it (with neutral scores) before
        # returning -- get_policy() must report every evaluated context, even
        # ones that produced no gradient.
        if not advantages:
            with self._lock:
                policy = self._policy.setdefault(context_key, {})
                for worm in group:
                    policy.setdefault(worm, 0.0)
            return {"skipped": True, "reason": "zero-variance"}

        with self._lock:
            if context_key not in self._policy:
                self._policy[context_key] = {}

            policy = self._policy[context_key]

            # Initialize new worms with neutral score
            for worm in group:
                if worm not in policy:
                    policy[worm] = 0.0

            # TR-GRPO token-regulate analogue: downweight fluke winners.
            # w = clip(1.5*sigmoid((p-0.3)/0.1), 0.5, 1.5) on softmax scores.
            import math
            exps = {w: math.exp(policy[w]) for w in group}
            denom = sum(exps.values()) or 1.0
            probs = {w: exps[w] / denom for w in group}

            def tr_w(p):
                return min(1.5, max(0.5, 1.5 / (1.0 + math.exp(-(p - 0.3) / 0.1))))

            # Policy gradient update with KL penalty (k2 form: 0.5*(s-ref)^2)
            # EP472 (LRT mask-corrector): the raw per-worm deltas are the
            # correction vector; bounded_correct scales them to norm <=
            # trust_region and returns the new state. Corrector gaps (or a
            # non-positive bound) fall back to the unclipped update, so a
            # corrector failure never blocks learning.
            deltas = []
            for worm, advantage in zip(group, advantages):
                old_score = policy[worm]
                # KL penalty: penalize large changes from old policy
                kl_penalty = self._beta * old_score
                w = tr_w(probs[worm])
                # Update: move in direction of advantage, penalized by KL
                deltas.append(self._lr * (w * advantage - kl_penalty))
            trust: dict[str, Any] = {"bound": self._trust_region,
                                     "clipped": False, "gaps": []}
            new_scores: list[float] | None = None
            try:
                if self._corrector is None:
                    raise RuntimeError("corrector-unavailable")
                out = self._corrector([policy[w] for w in group],
                                      deltas, self._trust_region)
                if out.get("gaps"):
                    trust["gaps"] = list(out["gaps"])
                elif len(out.get("state", [])) == len(group):
                    new_scores = [float(x) for x in out["state"]]
                    trust["clipped"] = bool(out.get("scale", 1.0) < 1.0)
                    trust["applied_norm"] = out.get("applied_norm")
            except Exception as exc:
                note_failure("learning_grpo:trust-region", exc)
                trust["gaps"] = ["corrector-error"]
            if new_scores is not None:
                for worm, score in zip(group, new_scores):
                    policy[worm] = score
            else:
                for worm, delta in zip(group, deltas):
                    policy[worm] += delta

            # Normalize scores to prevent drift
            scores = list(policy.values())
            if scores:
                max_abs = max(abs(s) for s in scores) if scores else 1.0
                if max_abs > 10.0:
                    scale = 10.0 / max_abs
                    for w in policy:
                        policy[w] *= scale

        # Record history
        entry = {
            "group": group,
            "rewards": rewards,
            "advantages": advantages,
            "context_key": context_key,
            "timestamp": time.time(),
            "trust_region": trust,
        }
        with self._lock:
            self._history.append(entry)
            if len(self._history) > 500:
                self._history = self._history[-500:]

        if record_replay:
            self.ingest_replay_trajectory(
                {"steps": [{"context_key": context_key, "worm": w,
                             "reward": float(r)}
                            for w, r in zip(group, rewards)]},
                source="update_policy:%s" % context_key)

        # Persist to LocalMemoryStore
        try:
            from CORE.local_memory import LocalMemoryStore
            store = LocalMemoryStore()
            best_idx = rewards.index(max(rewards))
            store.record_event(
                source="GRPOTrainer", event_type="policy_update",
                data={
                    "context_key": context_key,
                    "group_size": len(group),
                    "best_worm": group[best_idx],
                    "best_reward": round(max(rewards), 4),
                    "avg_reward": round(sum(rewards) / len(rewards), 4),
                },
                severity="info", tags=["grpo", "policy", context_key],
            )
        except Exception as exc:
            note_failure("learning_grpo:L299", exc)

        # Auto-save full policy snapshot after every update
        self.save_policy()

    # -- EP350: offline replay protocol + step-decaying sampler -------------

    def ingest_replay_trajectory(self, trajectory: dict, source: str = "") -> dict:
        """Admit one recorded trajectory as a replayed prefix (EP350).

        "Training without live environments" needs the recorded decision
        prefix, not the environment: a trajectory is the sequence of
        (context_key, worm, reward) triples a real run already produced. An
        unusable trajectory reports accepted=False with its reason, so a
        caller cannot mistake an empty ingest for a stored prefix.
        """
        steps = []
        for raw in (trajectory or {}).get("steps", []) or []:
            if not isinstance(raw, dict) or not raw.get("worm"):
                continue
            steps.append({"context_key": str(raw.get("context_key", "default")),
                          "worm": str(raw["worm"]),
                          "reward": float(raw.get("reward", 0.0))})
        if not steps:
            return {"accepted": False, "reason": "no usable steps",
                    "n_buffered": len(self._replay)}
        with self._lock:
            self._replay.append({"source": str(source), "steps": steps,
                                 "timestamp": time.time()})
            self._replay = self._replay[-200:]
            n = len(self._replay)
        # EP401: distill the admitted trajectory into a protocol artifact and
        # record it — replayed experience builds the reusable protocol library
        # (rendered via emit_skill_md). Collaborators resolved in __init__;
        # best-effort: never blocks admission.
        try:
            if self._protocol_distiller is None or self._protocol_store is None:
                raise RuntimeError("protocol-wire-unavailable")
            proto = self._protocol_distiller(
                {"actions": [s["worm"] for s in steps],
                 "step_rewards": [s["reward"] for s in steps],
                 "outcome": "replay:%s" % str(source),
                 "evidence": ["context:%s" % s["context_key"]
                              for s in steps],
                 "outcome_reward": (sum(s["reward"] for s in steps)
                                     / len(steps))},
                "grpo-replay")
            if proto.get("protocol"):
                self._protocol_store().record_event(
                    source="GRPOTrainer", event_type="replay_protocol",
                    data={"protocol": proto["protocol"],
                          "n_steps": proto["n_steps"],
                          "outcome_reward": proto["outcome_reward"],
                          "source": str(source)},
                    severity="info",
                    tags=["grpo", "replay", "protocol"])
        except Exception as exc:
            note_failure("learning_grpo:replay-protocol", exc)
        return {"accepted": True, "n_steps": len(steps), "n_buffered": n}

    def replay_batch(self, limit: int = 8) -> list[dict]:
        """Newest buffered replay prefixes (EP350)."""
        with self._lock:
            return list(self._replay[-max(int(limit), 1):])

    def replay_offline_step(self, prefix: dict | None = None) -> dict:
        """Update the policy from a replayed prefix - no environment runs.

        Steps are grouped by context_key and each group goes through the
        ordinary update_policy, so the offline path reuses the live one
        rather than forking a second trainer whose gradient nothing compares.
        """
        if prefix is None:
            buffered = self.replay_batch(1)
            prefix = buffered[0] if buffered else None
        if not prefix:
            return {"updated": False, "reason": "no buffered prefix"}
        by_ctx: dict[str, list[dict]] = {}
        for step in prefix.get("steps", []) or []:
            by_ctx.setdefault(step.get("context_key", "default"), []).append(step)
        results = {}
        for ctx, steps in by_ctx.items():
            results[ctx] = self.update_policy(
                [s["worm"] for s in steps],
                [float(s.get("reward", 0.0)) for s in steps],
                context_key=ctx, record_replay=False)
        return {"updated": True, "contexts": results,
                "n_steps": sum(len(s) for s in by_ctx.values())}

    def sampling_temperature(self) -> float:
        """EP350 step-decaying exploration temperature.

        1.0 when the schedule is disabled (the default), so generate_group is
        unchanged; otherwise step_decay ** n_updates, i.e. exploration decays
        as the policy accumulates evidence.
        """
        if self._step_decay <= 0.0:
            return 1.0
        with self._lock:
            n = len(self._history)
        return max(0.0, min(1.0, self._step_decay ** n))

    # -- EP351: checkpoint-pair dense implicit reward ------------------------

    def dense_implicit_reward(self, context_key: str = "default",
                              anchor_label: str | None = None) -> dict[str, float]:
        """Dense implicit reward over the student's own states (EP351).

        Direct-OPD reads its dense signal off the GAP between two checkpoints
        of the same model (post-RL vs pre-RL) instead of a fresh rollout. The
        checkpoints here are policy snapshots: pre-RL is a named anchor (or
        `_ref_policy`), post-RL is the live policy, and the log-ratio is taken
        over the log-softmax of each worm's score - the same student-state
        contrast the paper forms from its own log-probs.
        """
        with self._lock:
            post = dict(self._policy.get(context_key, {}))
            pre: dict = {}
            if anchor_label is not None:
                for anchor in reversed(self._anchors):
                    if anchor.get("label") == anchor_label:
                        pre = dict(anchor.get("policies", {}).get(context_key, {}))
                        break
            if not pre:
                pre = dict(getattr(self, "_ref_policy", {}).get(context_key, {}))
        if not post:
            return {}
        keys = sorted(set(post) | set(pre))
        if not pre:
            # No pre-RL checkpoint exists yet: a uniform prior is the honest
            # reference and keeps the term computable on the very first update.
            pre = {k: 0.0 for k in keys}
        post_p = _softmax({k: float(post.get(k, 0.0)) for k in keys})
        pre_p = _softmax({k: float(pre.get(k, 0.0)) for k in keys})
        return {k: math.log(max(post_p[k], 1e-12))
                   - math.log(max(pre_p[k], 1e-12)) for k in keys}

    def apply_dense_reward(self, group: list[str], rewards: list[float],
                           context_key: str = "default") -> list[float]:
        """EP351 fusion: add the checkpoint-pair term to a group's rewards.

        Opt-in via dense_reward_weight (default 0.0), so a trainer that did
        not ask for it is handed back the rewards it passed in, unchanged.
        """
        if self._dense_reward_weight <= 0.0:
            return list(rewards)
        dense = self.dense_implicit_reward(context_key)
        if not dense:
            return list(rewards)
        return [float(r) + self._dense_reward_weight * dense.get(w, 0.0)
                for w, r in zip(group, rewards)]

    def snapshot_anchor(self, label: str = "") -> dict:
        """Freeze the current policy tables as a named anchor reference set.

        Training-structure accommodation, EP360 anchor half (Oct-10): the
        mixed-objective step the delta names stays deferred (no weight-space
        host), but the anchor snapshots it would compare against now exist:
        a deep copy of every context policy, timestamped and capped at 20.
        Read-only w.r.t. training; never mutates the live policy.
        """
        with self._lock:
            snap = {ctx: dict(pol) for ctx, pol in self._policy.items()}
            self._anchors.append({"label": str(label or "anchor"),
                                  "policies": snap,
                                  "timestamp": time.time()})
            self._anchors = self._anchors[-20:]
            n = len(self._anchors)
        return {"anchored": True, "label": str(label or "anchor"),
                "n_contexts": len(snap), "n_anchors": n}

    def select_best(self, context_key: str = "default") -> (str | None):
        """Select the best worm for a given context based on learned policy.

        Args:
            context_key: Policy context key.

        Returns:
            Best worm name, or None if no policy exists.
        """
        with self._lock:
            policy = self._policy.get(context_key, {})
        if not policy:
            return None
        best = max(policy, key=policy.get)  # pyright: ignore[reportArgumentType, reportCallIssue] -- max over dict keys with float values; policy.get returns float | None but policy is non-empty here
        # Evolution joint: blend metacognition confidence into selection.
        # Unknown worms score neutral (0.5x2 = unchanged order). Any failure
        # keeps GRPO order. See MetaCognitionWorm.get_confidence.
        try:
            from CORE.WORMS.expansion.meta_cognition import MetaCognitionWorm
            mc = MetaCognitionWorm()

            def _blended(w):
                try:
                    conf = mc.get_confidence(w)  # pyright: ignore[reportAttributeAccessIssue] -- MetaCognitionWorm instance; get_confidence exists at runtime
                except Exception as exc:
                    note_failure("learning_grpo:L329", exc)
                    conf = 0.5
                return policy[w] * (0.5 + conf)

            ranked = sorted(policy, key=_blended, reverse=True)
            if ranked:
                return ranked[0]
        except Exception as exc:
            note_failure("learning_grpo:L336", exc)
        return best

    def get_policy(self, context_key: Optional[str] = None) -> dict[str, Any]:
        """Get the current policy.

        Args:
            context_key: If provided, return only this context's policy.

        Returns:
            dict with policy scores.
        """
        with self._lock:
            if context_key:
                return dict(self._policy.get(context_key, {}))
            return {k: dict(v) for k, v in self._policy.items()}

    def save_policy(self) -> bool:
        """Persist learned policy to LocalMemoryStore as JSON.

        Stores the full policy dict and recent history so the policy
        survives across sessions. Called automatically after updates.

        Returns:
            True if persisted successfully.
        """
        try:
            from CORE.local_memory import LocalMemoryStore
            store = LocalMemoryStore()
            with self._lock:
                policy_data = {
                    k: dict(v) for k, v in self._policy.items()
                }
            store.record_event(
                source="GRPOTrainer", event_type="policy_snapshot",
                data={
                    "policy": policy_data,
                    "beta": self._beta,
                    "lr": self._lr,
                    "n_contexts": len(policy_data),
                    "total_worms": sum(len(v) for v in policy_data.values()),
                },
                severity="info", tags=["grpo", "policy", "snapshot"],
            )
            # Cleanup: delete old policy_snapshot events (keep only latest 3)
            try:
                old = store.query_memories(
                    source="GRPOTrainer", event_type="policy_snapshot", limit=50
                )
                if len(old) > 3:
                    ids_to_delete = [r["id"] for r in old[3:]]
                    # (2026-09-10, A6: routed through the public tombstone
                    # API — no more private connections.)
                    marked = store.tombstone(ids_to_delete)
                    if marked != len(ids_to_delete):
                        print("GRPO tombstone cleanup partial: %d/%d" % (
                            marked, len(ids_to_delete)), flush=True)
            except Exception as e:  # noqa: BLE001 — logged, never silent
                print("GRPO tombstone cleanup failed: %s" % str(e)[:160], flush=True)
            return True
        except Exception as exc:
            note_failure("learning_grpo:L397", exc)
            return False

    def load_policy(self) -> bool:
        """Load persisted policy from LocalMemoryStore.

        Queries the most recent policy_snapshot event and restores
        the learned policy. Called automatically on init.

        Returns:
            True if policy was loaded successfully.
        """
        try:
            from CORE.local_memory import LocalMemoryStore
            store = LocalMemoryStore()
            events = store.query_memories(
                source="GRPOTrainer", event_type="policy_snapshot", limit=1
            )
            if not events:
                return False
            data = events[0].get("data", {})
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict) or "policy" not in data:
                return False
            with self._lock:
                for ctx, worms in data["policy"].items():
                    if isinstance(worms, dict):
                        self._policy[ctx] = {
                            w: float(s) for w, s in worms.items()
                        }
            return bool(self._policy)
        except Exception as exc:
            note_failure("learning_grpo:L429", exc)
            return False

    def get_stats(self) -> dict[str, Any]:
        """Get trainer statistics."""
        with self._lock:
            n_updates = len(self._history)
            n_contexts = len(self._policy)
            total_worms = sum(len(p) for p in self._policy.values())
            recent_rewards = []
            for entry in self._history[-10:]:
                recent_rewards.extend(entry["rewards"])
        return {
            "version": self.VERSION,
            "n_updates": n_updates,
            "n_contexts": n_contexts,
            "total_worms_scored": total_worms,
            "beta": self._beta,
            "lr": self._lr,
            "recent_avg_reward": round(
                sum(recent_rewards) / max(len(recent_rewards), 1), 4
            ) if recent_rewards else None,
        }

    def get_summary(self) -> dict[str, Any]:
        """Worm contract: return a summary dict (see BaseWormMixin).

        `GRPOTrainer` was split out of the `learning.py` monolith without a
        `get_summary()`, so the smoke test that instantiates every registered
        worm saw `None` here. `get_stats()` already computes the policy
        telemetry; this only adds the `success` envelope the registry executors
        expect.
        """
        return {"success": True, "method": "get_stats", "result": self.get_stats()}


def snapshot_log_ratios(store=None, limit: int = 10) -> dict[str, Any]:
    """Direct-OPD implicit reward over STORED snapshot pairs (EP351, live rows).

    The _l6_evolve adapter computes this ratio over supplied toy dicts; this
    reads the real `GRPOTrainer/policy_snapshot` rows instead. For each
    consecutive pair (older, newer): per shared context, softmax both score
    vectors — scores are unbounded floats that can be negative, so the raw
    log of a score is meaningless — then mean over keys of
    log(new_p) - log(old_p). Identical policies score exactly 0.0.
    Returns per-pair {older_id, newer_id, n_contexts, mean_log_ratio,
    max_abs_log_ratio} with verdict "pairs" / "single-snapshot" /
    "no-input". Read-only: never writes, never tombstones. Never raises.
    """
    out: dict[str, Any] = {"ok": False, "pairs": [], "n_snapshots": 0,
                            "verdict": "no-input"}
    try:
        if store is None:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
        rows = (store.query_memories(source="GRPOTrainer",
                                     event_type="policy_snapshot",
                                     limit=max(2, int(limit))) or [])
    except Exception as exc:
        note_failure("learning_grpo:log-ratio-read", exc)
        out["verdict"] = "leg-failed"
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    snaps = []
    for r in rows:
        try:
            data = (r or {}).get("data", {})
            if isinstance(data, str):
                data = json.loads(data)
            policy = (data or {}).get("policy", {})
            if not isinstance(policy, dict) or not policy:
                continue
            snaps.append({"id": (r or {}).get("id"), "policy": policy})
        except Exception as exc:
            # A snapshot skipped here is missing from the delta AND from the
            # count the verdict is computed from, so "no change" could mean
            # "unreadable row". Count it.
            note_failure("learning_grpo:snapshots", exc)
            continue
    out["n_snapshots"] = len(snaps)
    if len(snaps) < 2:
        out["ok"] = True
        out["verdict"] = ("single-snapshot" if snaps else "no-input")
        return out
    pairs = []
    for newer, older in zip(snaps[:-1], snaps[1:]):
        deltas = []
        for ctx, new_worms in (newer["policy"] or {}).items():
            old_worms = (older["policy"] or {}).get(ctx)
            if not isinstance(new_worms, dict) or not isinstance(old_worms, dict):
                continue
            keys = [k for k in new_worms if k in old_worms]
            try:
                new_vals = [float(new_worms[k]) for k in keys]
                old_vals = [float(old_worms[k]) for k in keys]
            except (TypeError, ValueError) as exc:
                note_failure("learning_grpo:pair-values", exc)
                continue
            if not keys:
                continue
            mx = max(new_vals + old_vals)
            new_p = [math.exp(v - mx) for v in new_vals]
            old_p = [math.exp(v - mx) for v in old_vals]
            sn, so = sum(new_p), sum(old_p)
            if sn <= 0 or so <= 0:
                continue
            for nv, ov in zip(new_p, old_p):
                deltas.append(math.log((nv / sn) / max(ov / so, 1e-300)))
        if not deltas:
            continue
        pairs.append({
            "newer_id": newer["id"], "older_id": older["id"],
            "n_contexts": sum(
                1 for ctx in (newer["policy"] or {})
                if isinstance((newer["policy"] or {}).get(ctx), dict)
                and isinstance((older["policy"] or {}).get(ctx), dict)),
            "mean_log_ratio": round(sum(deltas) / len(deltas), 6),
            "max_abs_log_ratio": round(max(abs(d) for d in deltas), 6),
            "n_cells": len(deltas),
        })
    out["pairs"] = pairs
    out["ok"] = True
    out["verdict"] = "pairs" if pairs else "no-common-context"
    return out


def snapshot_concentration(store=None, limit: int = 10) -> dict[str, Any]:
    """Per-context concentration over STORED snapshot pairs (RIPO/ARMOR health).

    RIPO's isometry and ARMOR's anchors both guard one failure mode:
    exploration collapse, where all mass piles onto one worm. There are no
    token rollouts here to clip or anchor, so this watches the observable
    equivalent: for the NEWER snapshot of each consecutive pair, softmax
    each shared context and record entropy (nats) plus max share, aggregated
    as mean_entropy / min_entropy / max_max_share over contexts. A live
    policy with min_entropy near 0 and max_max_share near 1 is collapsed,
    however stable its log-ratio drift looks. Read-only: never writes,
    never tombstones. Never raises.
    """
    out: dict[str, Any] = {"ok": False, "pairs": [], "n_snapshots": 0,
                            "verdict": "no-input"}
    try:
        if store is None:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
        rows = (store.query_memories(source="GRPOTrainer",
                                     event_type="policy_snapshot",
                                     limit=max(2, int(limit))) or [])
    except Exception as exc:
        note_failure("learning_grpo:concentration-read", exc)
        out["verdict"] = "leg-failed"
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    snaps = []
    for r in rows:
        try:
            data = (r or {}).get("data", {})
            if isinstance(data, str):
                data = json.loads(data)
            policy = (data or {}).get("policy", {})
            if not isinstance(policy, dict) or not policy:
                continue
            snaps.append({"id": (r or {}).get("id"), "policy": policy})
        except Exception as exc:
            # A snapshot skipped here is missing from the delta AND from the
            # count the verdict is computed from, so "no change" could mean
            # "unreadable row". Count it.
            note_failure("learning_grpo:snapshots", exc)
            continue
    out["n_snapshots"] = len(snaps)
    if len(snaps) < 2:
        out["ok"] = True
        out["verdict"] = ("single-snapshot" if snaps else "no-input")
        return out
    pairs = []
    for newer, older in zip(snaps[:-1], snaps[1:]):
        entropies = []
        shares = []
        n_ctx = 0
        for ctx, new_worms in (newer["policy"] or {}).items():
            old_worms = (older["policy"] or {}).get(ctx)
            if not isinstance(new_worms, dict) or not isinstance(old_worms, dict):
                continue
            keys = [k for k in new_worms if k in old_worms]
            try:
                new_vals = [float(new_worms[k]) for k in keys]
            except (TypeError, ValueError) as exc:
                note_failure("learning_grpo:entropy-values", exc)
                continue
            if not keys:
                continue
            mx = max(new_vals)
            exps = [math.exp(v - mx) for v in new_vals]
            tot = sum(exps)
            if tot <= 0:
                continue
            probs = [e / tot for e in exps]
            ent = -sum(p * math.log(max(p, 1e-300)) for p in probs)
            entropies.append(ent)
            shares.append(max(probs))
            n_ctx += 1
        if not entropies:
            continue
        pairs.append({
            "newer_id": newer["id"], "older_id": older["id"],
            "n_contexts": n_ctx,
            "mean_entropy": round(sum(entropies) / len(entropies), 6),
            "min_entropy": round(min(entropies), 6),
            "max_max_share": round(max(shares), 6),
        })
    out["pairs"] = pairs
    out["ok"] = True
    out["verdict"] = "pairs" if pairs else "no-common-context"
    return out


# -- A/B Test Runner with DPO ----------------------------------------------


@register_worm(category="learning", priority=4, cooldown_seconds=120.0,
    description="A/B test runner \u2014 split-test framework with DPO preference learning")
class ABTestRunner:
    """A/B test framework with statistical analysis and DPO preference learning.

    Supports:
    - Traditional A/B tracking (impressions, conversions)
    - DPO-style pairwise preference recording (chosen vs rejected)
    - Preference score computation per variant
    - Export of preference pairs for offline DPO training

    Usage:
        runner = ABTestRunner()
        runner.create_experiment("worm_selection", ["SystemZ", "ProcessZ", "VulnScanner"])

        # Traditional tracking
        runner.track("worm_selection", "SystemZ", converted=True)

        # DPO preference recording
        runner.record_preference("worm_selection",
            chosen="SystemZ", rejected="ProcessZ",
            context={"task_type": "monitor"},
            chosen_reward=0.9, rejected_reward=0.3)

        # Get preference pairs for training
        pairs = runner.get_preference_pairs("worm_selection")
    """

    VERSION = "2.0.0"

    def __init__(self):
        self._experiments: dict[str, dict] = {}
        self._preferences: dict[str, list[dict]] = {}  # experiment -> preference pairs
        self._lock = threading.Lock()

    def load_pairs(self, experiment: str, limit: int = 1000) -> int:
        """Hydrate in-memory pairs from persisted preference_recorded rows.

        (2026-09-10 fix: each DPO run is a fresh process, so the learner
        only ever saw that run's pairs — loss over 1 pair, history
        accumulating unread in the store. Loading first makes the loss
        cumulative. Idempotent per instance: skips already-loaded
        (chosen, rejected, timestamp) triples.)
        """
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            rows = store.query_memories(event_type="preference_recorded",
                                        limit=limit)
        except Exception as exc:
            note_failure("learning_grpo:L517", exc)
            return 0
        loaded = 0
        with self._lock:
            cur = self._preferences.setdefault(experiment, [])
            seen = {(p.get("chosen"), p.get("rejected"), p.get("timestamp"))
                    for p in cur}
            for r in reversed(rows):  # oldest first keeps chronological order
                d = r.get("data", {})
                if isinstance(d, str):
                    try:
                        d = _json.loads(d)
                    except Exception as exc:
                        note_failure("learning_grpo:L530", exc)
                        continue
                if not isinstance(d, dict) or d.get("experiment") != experiment:
                    continue
                key = (d.get("chosen"), d.get("rejected"),
                       r.get("timestamp", ""))
                if key in seen:
                    continue
                seen.add(key)
                cur.append({
                    "chosen": d.get("chosen"),
                    "rejected": d.get("rejected"),
                    "context": d.get("context") or {},
                    "chosen_reward": float(d.get("chosen_reward", 0.0)),
                    "rejected_reward": float(d.get("rejected_reward", 0.0)),
                    "reward_margin": round(float(d.get("chosen_reward", 0.0))
                                           - float(d.get("rejected_reward", 0.0)), 4),
                    "timestamp": r.get("timestamp", ""),
                })
                loaded += 1
        return loaded

    def create_experiment(self, name: str, variants: list[str]):
        with self._lock:
            self._experiments[name] = {v: {"impressions": 0, "conversions": 0} for v in variants}
            if name not in self._preferences:
                self._preferences[name] = []

    def track(self, experiment: str, variant: str, converted: bool):
        with self._lock:
            exp = self._experiments.get(experiment, {})
            if variant in exp:
                exp[variant]["impressions"] += 1
                if converted:
                    exp[variant]["conversions"] += 1

    def record_preference(self, experiment: str, chosen: str, rejected: str,
                           context: Optional[dict] = None, chosen_reward: float = 0.0,
                          rejected_reward: float = 0.0) -> dict[str, Any]:
        """Record a DPO-style pairwise preference.

        When two variants are evaluated on the same task, record which
        one performed better. This builds a preference dataset for
        Direct Preference Optimization training.

        Args:
            experiment: Experiment name.
            chosen: The better-performing variant.
            rejected: The worse-performing variant.
            context: Task context (task_type, source, etc.).
            chosen_reward: Reward for chosen variant.
            rejected_reward: Reward for rejected variant.

        Returns:
            The recorded preference entry.
        """
        entry = {
            "chosen": chosen,
            "rejected": rejected,
            "context": context or {},
            "chosen_reward": chosen_reward,
            "rejected_reward": rejected_reward,
            "reward_margin": round(chosen_reward - rejected_reward, 4),
            "timestamp": time.time(),
        }
        with self._lock:
            if experiment not in self._preferences:
                self._preferences[experiment] = []
            self._preferences[experiment].append(entry)

        # Persist to LocalMemoryStore
        try:
            from CORE.local_memory import LocalMemoryStore
            store = LocalMemoryStore()
            store.record_event(
                source="ABTestRunner", event_type="preference_recorded",
                data={
                    "experiment": experiment,
                    "chosen": chosen,
                    "rejected": rejected,
                    "context": context or {},
                    "chosen_reward": chosen_reward,
                    "rejected_reward": rejected_reward,
                },
                severity="info", tags=["dpo", "preference", experiment],
            )
        except Exception as exc:
            note_failure("learning_grpo:L616", exc)

        return entry

    def get_preference_pairs(self, experiment: str,
                             limit: int = 1000) -> list[dict[str, Any]]:
        """Get recorded preference pairs for DPO training.

        Args:
            experiment: Experiment name.
            limit: Max pairs to return.

        Returns:
            list of preference pair dicts.
        """
        with self._lock:
            pairs = list(self._preferences.get(experiment, []))
        return pairs[-limit:]

    def compute_preference_scores(self, experiment: str) -> dict[str, dict[str, Any]]:
        """Compute preference scores for each variant.

        A variant's preference score is the fraction of pairwise
        comparisons where it was chosen over the other variant.

        Args:
            experiment: Experiment name.

        Returns:
            dict mapping variant -> {chosen_count, rejected_count, preference_score}.
        """
        with self._lock:
            pairs = list(self._preferences.get(experiment, []))

        scores: dict[str, dict[str, int]] = {}
        for pair in pairs:
            chosen = pair["chosen"]
            rejected = pair["rejected"]
            if chosen not in scores:
                scores[chosen] = {"chosen_count": 0, "rejected_count": 0}
            if rejected not in scores:
                scores[rejected] = {"chosen_count": 0, "rejected_count": 0}
            scores[chosen]["chosen_count"] += 1
            scores[rejected]["rejected_count"] += 1

        result = {}
        for variant, counts in scores.items():
            total = counts["chosen_count"] + counts["rejected_count"]
            result[variant] = {
                "chosen_count": counts["chosen_count"],
                "rejected_count": counts["rejected_count"],
                "total_comparisons": total,
                "preference_score": round(
                    counts["chosen_count"] / max(total, 1), 4
                ),
            }
        return result

    def compute_dpo_loss(self, experiment: str, beta: float = 0.1) -> dict[str, Any]:
        """Compute DPO loss from preference pairs.

        DPO loss = -mean(log(sigmoid(beta * (r_chosen - r_rejected)))).
        A lower loss means the reward model better explains the preferences.

        Args:
            experiment: Experiment name.
            beta: Temperature parameter (higher = more conservative).

        Returns:
            dict with loss value and diagnostics.
        """
        with self._lock:
            pairs = list(self._preferences.get(experiment, []))

        if not pairs:
            return {"loss": None, "n_pairs": 0, "error": "No preference pairs"}

        losses = []
        for pair in pairs:
            margin = pair["chosen_reward"] - pair["rejected_reward"]
            x = beta * margin
            loss_val = math.log(1.0 + math.exp(-x))
            losses.append(loss_val)

        avg_loss = sum(losses) / len(losses)
        return {
            "loss": round(avg_loss, 6),
            "n_pairs": len(pairs),
            "beta": beta,
            "avg_margin": round(
                sum(p["chosen_reward"] - p["rejected_reward"] for p in pairs) / len(pairs), 4
            ),
        }

    def get_results(self, experiment: str) -> dict[str, Any]:
        with self._lock:
            exp = self._experiments.get(experiment, {})
        results = {}
        for var, metrics in exp.items():
            rate = metrics["conversions"] / max(metrics["impressions"], 1)
            results[var] = {"conversion_rate": round(rate, 4), **metrics}

        # Include preference scores if available
        pref_scores = self.compute_preference_scores(experiment)
        for var, pref in pref_scores.items():
            if var in results:
                results[var]["preference_score"] = pref["preference_score"]
                results[var]["preference_count"] = pref["total_comparisons"]
            else:
                results[var] = pref

        return {"experiment": experiment, "variants": results}

    def get_summary(self):
        return {"status":"ok","worm":"ABTestRunner"}


def order_by_difficulty(items: list, difficulty, reverse: bool = False) -> dict:
    """Order training items by caller-supplied difficulty (EP355, input half).

    items: [name] or [{"name", ...}]. difficulty: {name: float} or
    callable(name) -> float. Returns the ordered names + scores. Train
    GATING on the order (and any RL-trainer integration) stays deferred:
    ordering without a gating consumer would be decoration.
    """
    def _score(it):
        nm = it.get("name", "?") if isinstance(it, dict) else str(it)
        try:
            val = float(difficulty(nm) if callable(difficulty)  # pyright: ignore[reportArgumentType]
                        else difficulty.get(nm, 0.5))
        except (TypeError, ValueError, AttributeError) as exc:
            note_failure("expansion/learning_grpo:803", exc)
            val = 0.5
        return val
    ordered = sorted(list(items or []), key=_score, reverse=bool(reverse))
    names = [(it.get("name", "?") if isinstance(it, dict) else str(it))
             for it in ordered]
    return {"ordered": names,
            "difficulties": {n: _score(it) for n, it in zip(names, ordered)}}
