"""Critic-free policy optimization contracts used by the algorithm laboratory.

This module implements the mathematical update contracts needed to compare
REINFORCE, RLOO, REINFORCE++, Dr GRPO, DAPO, GSPO, and GDPO without claiming
that a name alone makes an algorithm production-ready. Callers must still
provide real rollouts, deterministic seeds, and qualification evidence.

Measurement-space note (forensic, 2026-10-06): each estimator's advantage
lives in its own space — raw returns (REINFORCE), leave-one-out deltas
(RLOO), batch z-scores (REINFORCE++), group z-scores (GRPO), centered
deltas (Dr GRPO), decoupled multi-stream z-scores (GDPO). The per-algorithm
``loss`` in run_policy_lab is therefore NOT comparable across estimators;
PolicyEvidence.normalization names the space so no consumer can silently
rank across it. Cross-space decisions (promotion, gating) must go through
an explicit common scale (see bounded_margin in expansion/exmachina.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, isfinite, log, sqrt
from statistics import fmean, pstdev
from typing import Dict, Iterable, Mapping, Sequence, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass(frozen=True)
class PolicyEvidence:
    """One reproducible comparison result for a critic-free algorithm."""

    algorithm: str
    seed: int
    samples: int
    advantage_mean: float
    advantage_std: float
    loss: float
    accepted_groups: int
    skipped_groups: int
    normalization: str = "unknown"

    def as_dict(self) -> dict[str, float | int | str]:
        return asdict(self)


def _validate_rewards(rewards: Sequence[float]) -> list[float]:
    values = [float(value) for value in rewards]
    if not values or any(not isfinite(value) for value in values):
        raise ValueError("rewards must be a non-empty finite sequence")
    return values


def reinforce_advantages(rewards: Sequence[float]) -> list[float]:
    """REINFORCE returns-to-go with a zero baseline."""

    return _validate_rewards(rewards)


def rloo_advantages(rewards: Sequence[float]) -> list[float]:
    """RLOO subtracts every other sample's mean from each sample."""

    values = _validate_rewards(rewards)
    count = len(values)
    if count < 2:
        return [0.0]
    total = sum(values)
    return [value - (total - value) / (count - 1) for value in values]


def reinforce_plus_plus_advantages(
    rewards: Sequence[float],
    *,
    global_rewards: Sequence[float] | None = None,
    baseline: float | None = None,
) -> list[float]:
    """REINFORCE++ with global-batch normalization and optional baseline.

    Global normalization uses the complete supplied batch. When a learned
    baseline is supplied, rewards are centered by it before global scaling.
    """

    values = _validate_rewards(rewards)
    population = _validate_rewards(global_rewards or values)
    if baseline is None:
        centered_population = population
        centered = values
    else:
        center = float(baseline)
        if not isfinite(center):
            raise ValueError("baseline must be finite")
        centered_population = [value - center for value in population]
        centered = [value - center for value in values]
    scale = pstdev(centered_population)
    if scale <= 1e-12:
        return [0.0 for _ in centered]
    return [value / scale for value in centered]


def grpo_advantages(rewards: Sequence[float]) -> list[float]:
    """Standard GRPO local mean/std normalization."""

    values = _validate_rewards(rewards)
    scale = pstdev(values)
    if scale <= 1e-12:
        return [0.0 for _ in values]
    mean = fmean(values)
    return [(value - mean) / scale for value in values]


def dr_grpo_advantages(rewards: Sequence[float]) -> list[float]:
    """Dr GRPO local centering without std or response-length rescaling."""

    values = _validate_rewards(rewards)
    mean = fmean(values)
    return [value - mean for value in values]


def gdpo_advantages(streams: Mapping[str, Sequence[float]]) -> list[float]:
    """GDPO decoupled multi-reward normalization (ICML 2026, NVIDIA).

    Joint GRPO-style normalization over combined multi-reward rollouts
    collapses distinct reward combinations into identical advantages,
    destroying resolution. GDPO normalizes each reward stream
    INDEPENDENTLY (z-score within stream, zero-variance streams contribute
    0.0 rather than NaN) and combines per-sample by mean. Relative
    differences WITHIN each stream are preserved; no stream's scale can
    swamp another's.

    Args:
        streams: mapping of stream name -> per-sample rewards. All streams
            must cover the same samples in the same order with equal length.

    Returns:
        Per-sample combined advantages (mean of per-stream z-scores).
    """

    if not streams:
        raise ValueError("streams must be a non-empty mapping")
    names = list(streams)
    columns = [_validate_rewards(streams[name]) for name in names]
    width = len(columns[0])
    if any(len(column) != width for column in columns):
        raise ValueError("all streams must have equal length")
    norms: list[list[float]] = []
    for column in columns:
        scale = pstdev(column)
        if scale <= 1e-12:
            norms.append([0.0 for _ in column])
            continue
        mean = fmean(column)
        norms.append([(value - mean) / scale for value in column])
    return [fmean([norms[stream][index] for stream in range(len(names))])
            for index in range(width)]


def cispo_is_weights(
    log_ratios: Sequence[float],
    behavior_log_ratios: Sequence[float],
    *,
    clip_high: float = 2.0,
) -> list[float]:
    """CISPO clipped importance-sampling weights for off-policy reuse.

    Rollouts generated under a stale behavior policy stay trainable by
    weighting each token with w = pi_new/pi_old, clipped above at
    ``clip_high`` (and floored at 0.0 — a token the current policy
    rejects carries no gradient). Inputs are LOG ratios; the weight is
    exp(current - behavior). More reuse = cheaper training per token but
    higher off-policy error, so callers should track the mean weight as a
    staleness meter alongside the loss.
    """

    current = [float(value) for value in log_ratios]
    behavior = [float(value) for value in behavior_log_ratios]
    if len(current) != len(behavior):
        raise ValueError("current and behavior log-ratios must have equal length")
    if not all(isfinite(value) for value in current + behavior):
        raise ValueError("log-ratios must be finite")
    weights = []
    for new, old in zip(current, behavior):
        weight = exp(new - old)
        if weight < 0.0:
            weight = 0.0
        elif weight > clip_high:
            weight = clip_high
        weights.append(weight)
    return weights


def cispo_policy_loss(
    trace: TokenTrace,
    advantage: float,
    behavior_log_ratios: Sequence[float],
    *,
    clip_high: float = 2.0,
) -> float:
    """CISPO off-policy token loss: clipped IS weight times advantage.

    Same surrogate shape as the DAPO token loss, but each token's
    contribution is reweighted for the drift between the rollout policy
    and the current policy, so stale batches remain trainable instead of
    discarded. Valid (masked-in) tokens only.
    """

    valid = trace.valid_indices()
    current = [float(trace.log_ratios[index]) for index in valid]
    behavior_all = [float(value) for value in behavior_log_ratios]
    if len(behavior_all) != len(trace.log_ratios):
        raise ValueError("behavior log-ratios must match trace length")
    behavior = [behavior_all[index] for index in valid]
    if not current:
        return 0.0
    weights = cispo_is_weights(current, behavior, clip_high=clip_high)
    return -fmean(weight * advantage for weight in weights)


def depo_filter(
    difficulties: Mapping[str, float],
    *,
    low: float = 0.05,
    high: float = 0.95,
    budget: int | None = None,
) -> list[str]:
    """DEPO difficulty-estimated online filtering (2026).

    Rollout budget is wasted on prompts the policy already masters
    (difficulty ~= 0) or cannot touch yet (difficulty ~= 1): both yield
    zero-variance groups downstream. Keep only the competence frontier —
    prompts strictly inside (low, high). Difficulty is a caller-supplied
    estimate in [0, 1] (e.g. recent fail rate); this contract only applies
    the band, it does not estimate. Orthogonal to DAPO's dynamic sampling,
    which filters AFTER rollout; DEPO filters BEFORE, saving the rollout.

    budget (EP412 precision/trajectory knob): optional explicit rollout cap.
    When set, the frontier is truncated to the first `budget` names in input
    order (deterministic). None (default) keeps the whole frontier.
    """

    if not 0.0 <= low < high <= 1.0:
        raise ValueError("require 0 <= low < high <= 1")
    kept = []
    for name, difficulty in difficulties.items():
        value = float(difficulty)
        if not isfinite(value):
            raise ValueError(f"difficulty for {name!r} must be finite")
        if low < value < high:
            kept.append(name)
    if budget is not None:
        kept = kept[:max(0, int(budget))]
    return kept


def espo_tripwire(observed: Sequence[float], *, baseline: float = 0.25,
                  threshold: float = 0.5) -> Dict[str, object]:
    """Online per-step regret tripwire -> absorbing failure state (ESPO).

    Pure accumulator: each observation above `baseline` adds regret; the
    first step pushing cumulative regret past `threshold` flips the state
    to absorbing-failure and stops the scan. Returns {"budget" (steps
    consumed), "regret", "state"}. The trip decision this feeds
    (force_trip) is EP383's; this contract only measures.
    """
    base = float(baseline)
    limit = float(threshold)
    budget, regret, state = 0.0, 0.0, "running"
    for value in observed or []:
        budget += 1.0
        excess = float(value) - base
        regret += excess if excess > 0.0 else 0.0
        if regret > limit:
            state = "absorbing-failure"
            break
    return {"budget": budget, "regret": round(regret, 6), "state": state}


def pareto_front(points: Mapping[str, Sequence[float]]) -> dict:
    """Pareto-nondominated set over multi-objective points (EP234, set half).

    points: {name: (obj1, obj2, ...)}, all maximized. A point is retained iff
    no other point meets-or-beats it on every objective and beats it on one.
    The retention PREDICATE that would collapse EP228/EP234/EP462 into one
    edit stays deferred: no criteria-vector caller exists yet.
    """
    names = list(points.keys())
    vecs = {n: [float(v) for v in points[n]] for n in names}
    front: list[str] = []
    dominated: list[str] = []
    for a in names:
        beaten = False
        for b in names:
            if a == b:
                continue
            if len(vecs[b]) != len(vecs[a]):
                continue
            if (all(y >= x for x, y in zip(vecs[a], vecs[b]))
                    and any(y > x for x, y in zip(vecs[a], vecs[b]))):
                beaten = True
                break
        (dominated if beaten else front).append(a)
    return {"front": front, "dominated": dominated}


def dppo_rescale(keep_prob: float) -> float:
    """DPPO unbiased-pruning rescale factor (2026).

    Pruning low-value completions shifts the sampling distribution; the
    retained gradient stays unbiased only when reweighted by 1/keep_prob
    (importance-sampling correction). Heuristic pruning without this
    factor is estimation bias, not efficiency.
    """

    prob = float(keep_prob)
    if not isfinite(prob) or prob <= 0.0 or prob > 1.0:
        raise ValueError("keep_prob must be in (0, 1]")
    return 1.0 / prob


class SPOTracker:
    """SPO persistent global tracker (2025-26 lineage).

    Per-prompt grouping degenerates when a group's rewards have zero
    variance (sigma -> 0, advantages vanish). SPO keeps ONE persistent
    running mean/variance across batches (Welford, numerically stable)
    and normalizes against it — no per-prompt sync, no zero-variance
    collapse, at the cost of losing prompt-local difficulty adjustment.
    The tracker object IS the state; serialize count/mean/m2 to persist.
    """

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def update(self, rewards: Sequence[float]) -> None:
        """Fold a batch into the running statistics."""

        for value in _validate_rewards(rewards):
            self.count += 1
            delta = value - self.mean
            self.mean += delta / self.count
            self.m2 += delta * (value - self.mean)

    @property
    def variance(self) -> float:
        """Population variance of everything seen (0.0 when empty)."""

        if self.count < 1:
            return 0.0
        return self.m2 / self.count

    def advantages(self, rewards: Sequence[float]) -> list[float]:
        """Advantages against the persistent baseline (pre-update stats)."""

        values = _validate_rewards(rewards)
        scale = sqrt(self.variance)
        if self.count < 1 or scale <= 1e-12:
            return [0.0 for _ in values]
        return [(value - self.mean) / scale for value in values]


def dpo_loss(
    chosen_logprob: float,
    rejected_logprob: float,
    ref_chosen_logprob: float,
    ref_rejected_logprob: float,
    *,
    beta: float = 0.1,
) -> float:
    """DPO logistic preference loss (Rafailov et al. 2023).

    No reward model: the optimal reward is implicit in the policy /
    reference log-ratio gap. Pushes P(chosen) above P(rejected) while the
    beta-scaled ratios keep the policy near the reference. Sensitive to
    noisy pairs (gap pushed toward infinity even for near-ties).
    """

    for value in (chosen_logprob, rejected_logprob,
                  ref_chosen_logprob, ref_rejected_logprob, beta):
        if not isfinite(float(value)):
            raise ValueError("DPO inputs and beta must be finite")
    if beta <= 0.0:
        raise ValueError("beta must be positive")
    gap = ((chosen_logprob - ref_chosen_logprob)
           - (rejected_logprob - ref_rejected_logprob))
    return log(1.0 + exp(-beta * gap))


def ipo_loss(
    chosen_logprob: float,
    rejected_logprob: float,
    ref_chosen_logprob: float,
    ref_rejected_logprob: float,
    *,
    beta: float = 0.1,
) -> float:
    """IPO squared-error preference loss (2024).

    DPO's sigmoid pushes the winner/loser gap toward infinity even when
    the loser is merely second-best. IPO replaces it with an MSE target
    of 1/(2*beta) on the same log-ratio gap: near-ties saturate instead
    of diverging. Same signature as DPO, different pathology.
    """

    for value in (chosen_logprob, rejected_logprob,
                  ref_chosen_logprob, ref_rejected_logprob, beta):
        if not isfinite(float(value)):
            raise ValueError("IPO inputs and beta must be finite")
    if beta <= 0.0:
        raise ValueError("beta must be positive")
    gap = ((chosen_logprob - ref_chosen_logprob)
           - (rejected_logprob - ref_rejected_logprob))
    return (gap - 1.0 / (2.0 * beta)) ** 2


def kto_loss(
    logprob: float,
    ref_logprob: float,
    *,
    desirable: bool,
    beta: float = 0.1,
    loss_aversion: float = 2.0,
) -> float:
    """KTO prospect-theoretic loss (2024): no pairs required.

    Single completions labeled desirable/undesirable; the reference
    point is the policy-vs-reference gap and LOSSES weigh
    ``loss_aversion``x more than equal wins (Kahneman-Tversky asymmetry).
    For unpaired/binary feedback where pair construction is impossible.
    """

    for value in (logprob, ref_logprob, beta, loss_aversion):
        if not isfinite(float(value)):
            raise ValueError("KTO inputs must be finite")
    if beta <= 0.0 or loss_aversion <= 0.0:
        raise ValueError("beta and loss_aversion must be positive")
    gap = logprob - ref_logprob
    if desirable:
        return log(1.0 + exp(-beta * gap))
    return loss_aversion * log(1.0 + exp(beta * gap))


def simpo_loss(
    chosen_logprob: float,
    rejected_logprob: float,
    *,
    chosen_len: int,
    rejected_len: int,
    beta: float = 2.0,
    gamma: float = 0.5,
) -> float:
    """SimPO reference-free preference loss (2024).

    Drops the reference model entirely (halves memory): length-normalized
    log-probabilities stand in for the ratios, and a target margin gamma
    keeps a fixed gap between winner and loser. beta scales the margin.
    Length normalization removes the verbosity bias DPO inherits.
    """

    for value in (chosen_logprob, rejected_logprob, beta, gamma):
        if not isfinite(float(value)):
            raise ValueError("SimPO inputs must be finite")
    if chosen_len <= 0 or rejected_len <= 0:
        raise ValueError("lengths must be positive")
    if beta <= 0.0:
        raise ValueError("beta must be positive")
    gap = chosen_logprob / chosen_len - rejected_logprob / rejected_len
    return log(1.0 + exp(-beta * (gap - gamma)))


def sapo_gate(ratio: float, *, temperature: float = 1.0) -> float:
    """SAPO soft gate (2026): smooth attenuation instead of hard clipping.

    Hard PPO/DAPO clipping zeroes the gradient the instant a ratio crosses
    epsilon, discarding moderately off-policy tokens that still carry
    signal. The soft gate attenuates smoothly — full weight near 1.0,
    decaying tails — so the update degrades gracefully with staleness.
    temperature < 1 sharpens the gate, > 1 softens it.
    """

    value = float(ratio)
    if not isfinite(value) or value < 0.0:
        raise ValueError("ratio must be a finite non-negative number")
    if not isfinite(temperature) or temperature <= 0.0:
        raise ValueError("temperature must be positive")
    return 2.0 / (1.0 + exp(abs(log(max(value, 1e-12))) / temperature))


def sapo_policy_loss(
    trace: TokenTrace,
    advantage: float,
    *,
    temperature: float = 1.0,
) -> float:
    """SAPO off-policy-tolerant token loss via the soft gate."""

    ratios = [exp(value) for value in _valid_log_ratios(trace)]
    if not ratios:
        return 0.0
    return -fmean(sapo_gate(value, temperature=temperature) * advantage
                  for value in ratios)


def ngrpo_mix(
    anchor: Sequence[float],
    neighbors: Sequence[Sequence[float]],
    *,
    alpha: float = 0.5,
) -> list[float]:
    """N-GRPO semantic-neighbor mixing stub (2026).

    Token-level sampling only paraphrases; isotropic noise derails
    rollouts off the semantic manifold. N-GRPO mixes the anchor embedding
    with its nearest semantic neighbors: pure function on vectors, no
    model needed. alpha blends anchor (1-alpha) with the neighbor mean
    (alpha); alpha=0 recovers the anchor exactly. Callers supply
    similarity-retrieved neighbors — the contract only guarantees the
    convex geometry, not the retrieval.
    """

    base = [float(value) for value in anchor]
    if not base or any(not isfinite(value) for value in base):
        raise ValueError("anchor must be a non-empty finite vector")
    if not 0.0 <= float(alpha) <= 1.0 or not isfinite(float(alpha)):
        raise ValueError("alpha must be in [0, 1]")
    if not neighbors:
        return list(base)
    hood = [[float(value) for value in neighbor] for neighbor in neighbors]
    if any(len(member) != len(base) for member in hood):
        raise ValueError("neighbors must match anchor dimension")
    if any(not isfinite(value) for member in hood for value in member):
        raise ValueError("neighbors must be finite")
    mean = [fmean([member[index] for member in hood])
            for index in range(len(base))]
    weight = float(alpha)
    return [(1.0 - weight) * a + weight * m for a, m in zip(base, mean)]


@dataclass(frozen=True)
class TokenTrace:
    """Token log-probability deltas and validity mask for one rollout."""

    log_ratios: tuple[float, ...]
    mask: tuple[bool, ...] | None = None
    reward: float = 0.0
    group_rewards: tuple[float, ...] | None = None
    overlong: bool = False

    def valid_indices(self) -> tuple[int, ...]:
        if self.mask is None:
            return tuple(range(len(self.log_ratios)))
        if len(self.mask) != len(self.log_ratios):
            raise ValueError("mask and log_ratios must have equal length")
        return tuple(index for index, active in enumerate(self.mask) if active)


def _valid_log_ratios(trace: TokenTrace) -> list[float]:
    return [float(trace.log_ratios[index]) for index in trace.valid_indices()]


def token_policy_loss(
    trace: TokenTrace,
    advantage: float,
    *,
    clip_low: float = 0.2,
    clip_high: float = 0.28,
) -> float:
    """DAPO token-level clipped surrogate with an asymmetric clip range."""

    ratios = _valid_log_ratios(trace)
    if not ratios:
        return 0.0
    unclipped = [exp(value) for value in ratios]
    clipped = [min(clip_high, max(clip_low, value)) for value in unclipped]
    losses = [-min(value * advantage, clipped_value * advantage)
              for value, clipped_value in zip(unclipped, clipped)]
    return fmean(losses)


def sequence_policy_loss(
    trace: TokenTrace,
    advantage: float,
    *,
    clip_low: float = 0.2,
    clip_high: float = 0.28,
) -> float:
    """GSPO sequence-level geometric-mean ratio with asymmetric clipping."""

    ratios = _valid_log_ratios(trace)
    if not ratios:
        return 0.0
    sequence_ratio = exp(fmean(ratios))
    clipped_ratio = min(clip_high, max(clip_low, sequence_ratio))
    return -min(sequence_ratio * advantage, clipped_ratio * advantage)


def select_dapo_groups(
    groups: Iterable[TokenTrace],
    *,
    max_tokens: int,
    target_groups: int,
) -> tuple[list[TokenTrace], int]:
    """Dynamically retain informative, non-overlong groups within a token budget."""

    if max_tokens <= 0 or target_groups <= 0:
        return [], 0
    selected: list[TokenTrace] = []
    used = 0
    skipped = 0
    for trace in groups:
        valid = trace.valid_indices()
        if trace.overlong or not valid:
            skipped += 1
            continue
        group_rewards = _validate_rewards(
            trace.group_rewards if trace.group_rewards is not None
            else (trace.reward,))
        if len(group_rewards) < 2 or pstdev(group_rewards) <= 1e-12:
            skipped += 1
            continue
        cost = len(valid)
        if used + cost > max_tokens or len(selected) >= target_groups:
            skipped += 1
            continue
        selected.append(trace)
        used += cost
    return selected, skipped


def run_policy_lab(
    rewards_by_algorithm: dict[str, Sequence[float]],
    *,
    seed: int,
) -> list[PolicyEvidence]:
    """Run deterministic advantage contracts and return comparable evidence."""

    estimators = {
        "REINFORCE": (reinforce_advantages, "raw returns"),
        "RLOO": (rloo_advantages, "leave-one-out deltas"),
        "REINFORCE++": (reinforce_plus_plus_advantages, "batch z-scores"),
        "GRPO": (grpo_advantages, "group z-scores"),
        "DR_GRPO": (dr_grpo_advantages, "centered deltas"),
    }
    evidence: list[PolicyEvidence] = []
    for algorithm, rewards in rewards_by_algorithm.items():
        if algorithm not in estimators:
            raise ValueError(f"unsupported laboratory algorithm: {algorithm}")
        fn, space = estimators[algorithm]
        values = _validate_rewards(rewards)
        advantages = fn(values)
        loss = -fmean(advantage * value for advantage, value in zip(advantages, values))
        evidence.append(PolicyEvidence(
            algorithm=algorithm,
            seed=int(seed),
            samples=len(values),
            advantage_mean=fmean(advantages),
            advantage_std=pstdev(advantages),
            loss=float(loss),
            accepted_groups=1 if advantages else 0,
            skipped_groups=0 if advantages else 1,
            normalization=space,
        ))
    return evidence
