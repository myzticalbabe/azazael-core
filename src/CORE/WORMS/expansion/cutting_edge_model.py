# coding: utf-8
"""Cutting-edge model architecture -- MoE + SSM hybrid (task #507).

Design target: a next-generation model Azazael can actually run, ablate and
export, built out of the pieces the tree already ships rather than out of new
dependencies. Three of #507's stated premises do not survive contact with the
tree, so the design is built to the VERIFIED tree and says so:

  * ``learning.py`` is **not** a 2,099-line monolith. It is an 84-line
    backward-compatibility shim; the training surface was already split into
    ``learning_rl`` / ``learning_grpo`` / ``learning_features`` /
    ``learning_pipeline`` / ``learning_versioner``. This module composes THOSE
    and adds no second trainer.
  * ``PrioritizedReplayBuffer`` was **retired 2026-10-11** (zero production
    callers; ``learning_replay.py`` moved to ``archive/retired-trainers-*``).
    Nothing here imports it: the replay hook accepts a caller-supplied buffer
    (duck-typed ``add`` / ``sample``) so a retirement cannot resurrect it.
  * **``sys.tachyon`` does not exist** on the project's 3.15t build and the
    literal ``abi3t`` appears in no file under ``src/``. ``capabilities()``
    probes for real and reports absence as absence; a missing profiler is
    reported, never silently treated as a working one.

WHAT IS HERE
    CuttingEdgeConfig  -- one frozen dataclass holding every ablation switch.
    SparseMoE          -- top-k sparse routing, 8..64 experts, auxiliary
                          load-balancing loss (req. 1).
    SelectiveSSM       -- Mamba-style selective scan, O(n) in sequence length,
                          reported with a linear/quadratic op split (req. 2).
    AttentionBlock     -- optional quadratic block, kept as the control the SSM
                          is measured against.
    FFNBlock           -- position-wise MLP.
    CuttingEdgeModel   -- composes the enabled blocks, forwards, ablates, and
                          exposes the integration adapters (req. 6).

    The forward path is pure standard library. numpy/torch are imported only by
    the export helpers, so discovering this module (``discover_worms()`` imports
    every worm module) stays cheap and works on both interpreters.

ABLATION DISCIPLINE (the part that makes the switches worth having)
    Parameters are drawn from a PER-COMPONENT seeded generator, not one shared
    stream. Removing a component therefore cannot shift the initialisation of
    the components that remain: an ablation differs only by the component that
    was removed, which is the property that makes a removal study mean anything.
    ``component_fingerprint()`` exposes that property to a test.

INTEGRATION POINTS (each verified to exist; see the architecture doc)
    SchemaAwareFeatureExtractor    -> from_schema_features
    WorkStealingPool               -> train_parallel       (submit_group)
    DeadLetterQueue                -> record_failure
    ModelVersioner                 -> register_version
    providers.get_provider         -> reward               (6-API reward shaping)
    glia.OligodendrocyteWorm       -> deployment_plan      (quant format choice)
    servemesh.ModelShardServerWorm -> deployment_plan      (serve)

AUTHOR: Azazael swarm (task #507).  VERSION: 1.0.0
"""

from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import math
import random
import re
import sys
import sysconfig
import threading
import zlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, cast, Dict, List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).
try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm  # pyright: ignore[reportAssignmentType]
    _REGISTRY = True
except ImportError as exc:                               # registry optional at import time
    note_failure("cutting_edge_model:L93", exc)
    _REGISTRY = False  # pyright: ignore[reportConstantRedefinition]

    def register_worm(**kw):                      # noqa: ANN003 - shim
        return lambda cls: cls


__all__ = [
    "AttentionBlock",
    "CuttingEdgeConfig",
    "CuttingEdgeModel",
    "FFNBlock",
    "NullsFFNBlock",
    "NullsMaskRegistry",
    "SelectiveSSM",
    "SparseMoE",
    "COMPONENTS",
    "DEFAULT_BALANCE_GAMMA",
    "DEFAULT_BALANCE_MAX_ITERS",
    "DEFAULT_BALANCE_TOKENS",
    "DEFAULT_GRADIENT_TOKENS",
    "DEFAULT_LOSSFREE_ALPHA",
    "DEFAULT_LPR_PROTO_ALPHA",
    "EXPERT_MAX",
    "EXPERT_MIN",
    "MAX_GATE_BIAS",
    "NULLS_POOL_FRACTION",
    "NULLS_REGISTRY_FILENAME",
    "NULLS_SOURCE_FRACTION",
    "ROUTE_MODES",
    "gini_coefficient",
    "load_entropy_normalised",
    "nulls_sizing",
]

#: The four blocks #507 requires to be independently switchable (req. 3).
COMPONENTS: Tuple[str, ...] = ("moe", "ssm", "attention", "ffn")

#: #507 req. 1 bounds: sparse routing over 8..64 experts, top-k = 2.
EXPERT_MIN = 8
EXPERT_MAX = 64
DEFAULT_TOP_K = 2

#: Tokens used for the finite-difference gradient. Each expert's gradient costs
#: ``d_model`` full-batch evaluations, so the cost is O(len(tokens)) and a
#: production batch cannot fit a pool timeout. The sample is bounded AND
#: reported, and the before/after loss pair is measured on the SAME sampled rows.
#:
#: 64 is MEASURED, not guessed. At d_model=64 / n_experts=8 / 8 workers on this
#: host, against the pipeline's 10s default timeout: a 64-row sample updates 8 of
#: 8 experts in ~6.8s, while 128 and 256 rows both miss the deadline and update 0
#: of 8 -- the production defect this bound exists to remove. A caller wanting a
#: larger sample must pass a correspondingly larger ``timeout`` with it.
DEFAULT_GRADIENT_TOKENS = 64

#: Per-expert gate-bias nudge for Switch-Transformer-style count balancing.
DEFAULT_BALANCE_GAMMA = 0.01

#: Clamp so a long run of imbalance cannot drive the bias off to infinity.
MAX_GATE_BIAS = 2.0

#: Iteration cap for the bounded remediation loop (``SparseMoE.rebalance``). ONE
#: nudge is not remediation: after a single ``balance_step`` the live ClusterWORMZ
#: batch still used 2 of 8 experts with ``entropy_normalised`` exactly 1/3 and
#: ``max_skew`` 4.0 (engram #521). The loop applies the nudge until the load is
#: even or this cap runs out, and the cap is NAMED AND REPORTED when hit -- so a
#: caller can tell "converged" from "gave up at the bound" (see ``rebalance``).
#:
#: 256 is derived, not guessed: a bias travels at ``DEFAULT_BALANCE_GAMMA`` = 0.01
#: per step and is clamped to +/-``MAX_GATE_BIAS`` = 2.0, so a FULL sweep of the
#: clamp range is ``2.0 / 0.01`` = 200 steps. 256 covers a full sweep with
#: headroom. Measured: a one-expert skew injected at MAX_GATE_BIAS needs 183
#: steps to clear on the gate-aligned fixture -- a 64-step cap left it "capped",
#: which is why this number is not 64.
DEFAULT_BALANCE_MAX_ITERS = 256

#: Tokens used per rebalance iteration. ``balance_step`` routes the batch to
#: measure the load, so each iteration costs O(rows * n_experts * d_model) in pure
#: Python and the loop runs up to ``DEFAULT_BALANCE_MAX_ITERS`` times -- an
#: unbounded batch makes the loop's cost unbounded inside the trainer's lock. The
#: sample is bounded AND reported (``rows_used`` / ``rows_available``); the
#: gate-routed distribution is what is being measured, and a prefix of it measures
#: the same distribution. Measured at production ``d_model`` 64 / 8 experts on this
#: host: a full 256-iteration loop over 256 rows is 0.16 s, so the cap and the
#: sample bound together keep remediation well inside the pool's 10 s timeout.
DEFAULT_BALANCE_TOKENS = 256

#: Router options (Engram #813, P0 no-redo: the shipped router is mode
#: ``"switch"`` and stays byte-identical to the pre-#813 behaviour while no
#: mode is set). Modes are ONE variable each, co-routed options against the
#: standing 2-of-8 collapse -- not a rewrite of the gate:
#:
#: * ``"switch"`` -- shipped top-k softmax router, unchanged.
#: * ``"bias"`` -- R1, Loss-Free balancing (arXiv:2408.15664) as a separate
#:   selection-only bias vector updated sign-wise after every batch, clamped
#:   like ``MAX_GATE_BIAS``. Selection reads it; the gate VALUES do not.
#: * ``"bip"`` -- R2, BIP quota assignment (arXiv:2502.15451). Each batch is
#:   a binary-integer-programming assignment problem: each expert gets an
#:   equal quota and every token takes its highest-affinity experts whose
#:   quota is still open. Greedy, deterministic, exact quotas.
#: * ``"lpr"`` -- R3, Latent Prototype Routing READING (arXiv:2506.21328):
#:   assignment over per-expert latent prototypes (cluster perspective)
#:   through the same quota solver, protos warmed up from the gate row.
ROUTE_MODES: Tuple[str, ...] = ("switch", "bias", "bip", "lpr")

#: Loss-Free bias step per batch (R1). Sign-decision update -- one step of
#: :data:`DEFAULT_BALANCE_GAMMA`-sized authority per batch on a vector that
#: composes with ``gate_bias`` instead of fighting it, and is clamped to the
#: same ``MAX_GATE_BIAS`` so a long one-sided run cannot run away.
DEFAULT_LOSSFREE_ALPHA = 0.01

#: Prototype EMA rate for the LPR mode (R3, arXiv:2506.21328 reading).
DEFAULT_LPR_PROTO_ALPHA = 0.2

#: Switch name per component, so config <-> component mapping has one home.
_USE_FLAG = {name: "use_" + name for name in COMPONENTS}


# ---------------------------------------------------------------------------
# Small numeric helpers -- stdlib only, so the module imports anywhere.
# ---------------------------------------------------------------------------

#: Numbers embedded in a provider's free-text reply (signed, optional decimal).
#: Regex extraction rather than try/float: a single-statement `except: return
#: False` guard is an `except_return_default` finding for the staged scanner, and
#: the structural fix is to not need the handler at all.
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _softmax(xs: Sequence[float]) -> List[float]:
    if not xs:
        return []
    top = max(xs)
    exps = [math.exp(x - top) for x in xs]
    total = sum(exps)
    if total <= 0.0:
        return [1.0 / len(xs)] * len(xs)
    return [e / total for e in exps]


def gini_coefficient(counts: Sequence[int]) -> float:
    """Gini of the per-expert load, one number for "how skewed is this gate".

    0.0 is perfectly even, 1.0 is one expert taking everything. Complements
    ``load_entropy_normalised`` (entropy is forgiving of a few zero-count
    experts at high k; Gini is not) -- both numbers go on every snapshot from
    Engram #813, and the baseline above was measured first as
    entropy 0.3333 / Gini 0.75 on the live 2-of-8 fixture.
    """
    sorted_counts = sorted(int(c) for c in counts)
    n = len(sorted_counts)
    total = sum(sorted_counts)
    if total <= 0 or n < 2:
        return 0.0
    weighted = sum((i + 1) * c for i, c in enumerate(sorted_counts))
    # Standard Gini: 2*sum((i+1)*c_i)/(n*total) - (n+1)/n. An even load
    # (counts all equal) measures EXACTLY 0.0 and a [0..0,x] load (n-1)/n.
    return 2.0 * weighted / (n * total) - (n + 1.0) / n


def load_entropy_normalised(counts: Sequence[int], n_experts: int) -> float:
    """Load entropy over experts, normalised so 0.0 = one expert and 1.0 = even.

    The measurable form of "is routing collapsed": 1.0 is a perfectly uniform
    load, and for a top-k router the reachable FLOOR is ``log(top_k)/log(n_experts)``
    -- with top-k 2 over 8 experts that is exactly 1/3, which is where the live
    ClusterWORMZ batch sat. Reported as ``entropy_normalised`` by ``rebalance``.
    """
    total = sum(counts)
    if total <= 0 or n_experts < 2:
        return 0.0
    probs = [c / total for c in counts if c > 0]
    return -sum(p * math.log(p) for p in probs) / math.log(n_experts)


def _gelu(x: float) -> float:
    """Tanh approximation of GELU -- no dependency on erf."""
    return 0.5 * x * (1.0 + math.tanh(0.7978845608 * (x + 0.044715 * x * x * x)))


def _softplus(x: float) -> float:
    """log(1+e^x), stable for large and very negative x."""
    if x > 20.0:
        return x
    if x < -20.0:
        return math.exp(x)
    return math.log1p(math.exp(x))


def _rms_norm(vec: Sequence[float], eps: float = 1e-6) -> List[float]:
    mean_sq = sum(v * v for v in vec) / max(1, len(vec))
    scale = 1.0 / math.sqrt(mean_sq + eps)
    return [v * scale for v in vec]


class _Rand:
    """Deterministic parameter source.

    One generator per COMPONENT, seeded from the config seed and the component
    name, so removing a component cannot re-draw the parameters of the ones that
    survive. That is what makes an ablation attributable to the removal.
    """

    def __init__(self, seed: int) -> None:
        self._r = random.Random(int(seed))

    def matrix(self, n_out: int, n_in: int, scale: Optional[float] = None) -> List[List[float]]:
        s = scale if scale is not None else 1.0 / math.sqrt(max(1, n_in))
        return [[self._r.gauss(0.0, 1.0) * s for _ in range(n_in)] for _ in range(n_out)]

    def vector(self, n: int, scale: float = 1.0) -> List[float]:
        return [self._r.gauss(0.0, 1.0) * scale for _ in range(n)]


def _component_seed(base_seed: int, name: str) -> int:
    """Stable per-component seed (crc32, not hash(), which is salted per process)."""
    return int(base_seed) ^ zlib.crc32(name.encode("utf-8"))


def _fingerprint(parts: Sequence[Any]) -> str:
    """Short digest of nested numeric parameters -- used to prove non-interference."""
    h = hashlib.sha256()
    for part in parts:
        h.update(json.dumps(part, sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CuttingEdgeConfig:
    """Every architectural switch in one place (req. 3, req. 7).

    Frozen on purpose: ``variant()`` derives an ablated model from a new config
    instead of mutating a shared one, so a study cannot corrupt its own baseline.

    The four ``use_*`` flags are the ablation switches proper. The three
    ``enable_*`` / ``profile_hooks`` fields are the 3.15t-native requests; they
    are recorded as configuration and their effect is reported by
    ``CuttingEdgeModel.capabilities()`` rather than assumed.
    """

    # -- shape ------------------------------------------------------------
    d_model: int = 64
    n_layers: int = 2
    d_state: int = 8
    d_ff: int = 128
    n_experts: int = EXPERT_MIN
    top_k: int = DEFAULT_TOP_K
    aux_loss_coef: float = 0.01

    # -- ablation switches (req. 3) ---------------------------------------
    use_moe: bool = True
    use_ssm: bool = True
    use_attention: bool = True
    use_ffn: bool = True

    # -- 3.15t-native requests (req. 4) -----------------------------------
    enable_lazy_imports: bool = False
    enable_jit: bool = False
    profile_hooks: bool = False

    seed: int = 0
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        self.validate()

    # -- validation -------------------------------------------------------
    def validate(self) -> None:
        """Reject a config that cannot describe a real model.

        Raises rather than clamps: a silent clamp would let an ablation study
        believe it had removed a component it had merely misconfigured.
        """
        if self.d_model < 1:
            raise ValueError("d_model must be >= 1, got %r" % (self.d_model,))
        if self.n_layers < 1:
            raise ValueError("n_layers must be >= 1, got %r" % (self.n_layers,))
        if self.d_state < 1:
            raise ValueError("d_state must be >= 1, got %r" % (self.d_state,))
        if self.d_ff < 1:
            raise ValueError("d_ff must be >= 1, got %r" % (self.d_ff,))
        if not EXPERT_MIN <= self.n_experts <= EXPERT_MAX:
            raise ValueError(
                "n_experts must be within %d..%d (task #507 req. 1), got %r"
                % (EXPERT_MIN, EXPERT_MAX, self.n_experts))
        if not 1 <= self.top_k <= self.n_experts:
            raise ValueError(
                "top_k must be within 1..n_experts, got %r (n_experts=%r)"
                % (self.top_k, self.n_experts))
        if self.aux_loss_coef < 0.0:
            raise ValueError("aux_loss_coef must be >= 0, got %r" % (self.aux_loss_coef,))

    # -- introspection ----------------------------------------------------
    def active_components(self) -> Tuple[str, ...]:
        """The components this config builds, in build order."""
        return tuple(name for name in COMPONENTS if getattr(self, _USE_FLAG[name]))

    def ablation_switches(self) -> Dict[str, bool]:
        """{component: enabled} -- the exact ablation view of this config."""
        return {name: bool(getattr(self, _USE_FLAG[name])) for name in COMPONENTS}

    def variant(self, **overrides: Any) -> "CuttingEdgeConfig":
        """A new config with ``overrides`` applied (validated by construction)."""
        return replace(self, **overrides)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CuttingEdgeConfig":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def fingerprint(self) -> str:
        """Stable identity for this configuration (distinct ablations differ)."""
        return _fingerprint([self.to_dict()])


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

class _Expert:
    """One expert: two-layer MLP with an output bias used as its learning surface."""

    def __init__(self, d_model: int, d_hidden: int, rand: _Rand) -> None:
        self.w1 = rand.matrix(d_hidden, d_model)
        self.b1 = rand.vector(d_hidden, 0.0)
        self.w2 = rand.matrix(d_model, d_hidden)
        self.b2 = rand.vector(d_model, 0.0)

    def forward(self, x: Sequence[float], bias2: Optional[Sequence[float]] = None) -> List[float]:
        """MLP with an overridable output bias.

        ``bias2`` REPLACES ``self.b2`` inside the same additive term -- it does
        NOT replace the whole output. That keeps the objective identical whether
        or not a probe bias is supplied, which is what lets a finite-difference
        step compare a loss taken before an update against one taken after, on
        ONE function. An earlier revision returned ``bias2`` directly, so the
        before/after pair measured two different objectives (a bias-only
        quadratic vs the MLP output) and the comparison was meaningless.
        """
        hidden = [_gelu(_dot(row, x) + b) for row, b in zip(self.w1, self.b1)]
        b2 = self.b2 if bias2 is None else list(bias2)
        return [_dot(row, hidden) + b for row, b in zip(self.w2, b2)]

    def param_count(self) -> int:
        return (len(self.w1) * len(self.w1[0]) + len(self.b1)
                + len(self.w2) * len(self.w2[0]) + len(self.b2))


class SparseMoE:
    """Sparse mixture of experts with auxiliary load balancing (req. 1).

    Routing is a softmax gate over experts with a top-k selection per token. The
    auxiliary loss is the Switch-Transformer form

        aux = n_experts * sum_i (f_i * P_i)

    where ``f_i`` is the fraction of tokens whose top-k includes expert ``i`` and
    ``P_i`` is that expert's mean gate probability. It is minimised by a balanced
    routing, so it pushes against expert collapse; ``forward`` returns it
    un-multiplied and the caller applies ``aux_loss_coef``.
    """

    def __init__(self, d_model: int, n_experts: int, top_k: int, seed: int,
                 route_mode: str = "switch") -> None:
        self.d_model = int(d_model)
        self.n_experts = int(n_experts)
        self.top_k = int(top_k)
        if route_mode not in ROUTE_MODES:
            raise ValueError("unknown route_mode %r; expected one of %s"
                             % (route_mode, list(ROUTE_MODES)))
        self.route_mode: str = route_mode
        # R1 Loss-Free selection bias (arXiv:2408.15664): SEPARATE from
        # ``gate_balance``'s count-based gate_bias so the shipped mode stays
        # byte-identical and the two remeadies cannot fight over one vector.
        self._lossfree_bias: List[float] = [0.0] * self.n_experts
        # Cumulative routing tally over every forward since construction --
        # per-batch top-k pins can NAME a per-batch entropy floor while the
        # run itself utilises everyone, and only the cumulative view sees the
        # run (Engram #813 acceptance is cumulative, like the live 2-of-8
        # report that motivated it).
        self._cumulative: List[int] = [0] * self.n_experts
        # R3 prototype space (arXiv:2506.21328 reading): None until the first
        # forward seeds one prototype per expert from its gate row.
        self._prototypes: Optional[List[List[float]]] = None
        gap = max(1, 2 * self.d_model)
        self.gate = _Rand(_component_seed(seed, "moe.gate")).matrix(self.n_experts, self.d_model)
        #: Per-expert bias added to the gate logits. Zeros at construction, so
        #: routing is identical to the pre-balancing behaviour until a balance
        #: step moves it. This is the only learning path the gate has -- see
        #: ``balance_step`` for why the gate matrix alone cannot recover.
        self.gate_bias: List[float] = [0.0] * self.n_experts
        self.experts = [
            _Expert(self.d_model, gap, _Rand(_component_seed(seed, "moe.expert.%d" % i)))
            for i in range(self.n_experts)
        ]
        self.last_stats: Dict[str, Any] = {}
        #: The activations the LAST forward actually routed. Balancing must regulate
        #: THIS distribution, not the caller's training tokens: the tokens handed to
        #: ``train_parallel`` are the model's OUTPUT rows, which is a different
        #: distribution from what the gate sees inside the stack. Regulating the
        #: wrong signal is how a "balancing" step measures as making routing worse.
        self.last_inputs: List[Sequence[float]] = []

    # -- routing ----------------------------------------------------------
    def set_route_mode(self, mode: str) -> None:
        """Switch the router option (Engram #813). Raises on an unknown mode."""
        if mode not in ROUTE_MODES:
            raise ValueError("unknown route_mode %r; expected one of %s"
                             % (mode, list(ROUTE_MODES)))
        self.route_mode = mode

    def _update_lossfree_bias(self, counts: Sequence[int],
                              alpha: float = DEFAULT_LOSSFREE_ALPHA,
                              clamp: Optional[float] = None) -> Dict[str, Any]:
        """One sign-step of the R1 Loss-Free selection bias (arXiv:2408.15664).

        After a batch, each expert's bias moves ONE step toward balance: a
        share above the equal share gets ``-alpha``, below gets ``+alpha``,
        and an expert EXACTLY at its share gets no step (a strict sign rule;
        treating at-target as starved would push a perfectly even load off
        balance forever). It lives on a SEPARATE vector on purpose:
        ``gate_bias`` is ``balance_step``'s Switch remedy and the two
        remedies must not be able to fight over one vector.

        ``clamp`` is the everyday ``+-MAX_GATE_BIAS`` UNLESS the caller
        passes the batch's own measured selection-score spread -- which is
        what bias-mode ``forward`` does. Measured reason: a FIXED
        2.0-wide authority could not flip ANY of 96 seeded fixed gates on
        the #813 collapse fixture (gate score spreads all measured > 4.0;
        the shipped-seed fixture measured 27.0), so a constant clamp was
        not a safety margin but a structural wall, and the remedy could
        never re-mediate the collapse it exists for. The gate's own spread
        is the smallest authority that is guaranteed to be WIDE ENOUGH,
        and it is still bounded, reported, and derived -- not guessed.
        """
        clamp_val = MAX_GATE_BIAS if clamp is None else float(clamp)
        total = sum(counts)
        target = total / float(self.n_experts) if self.n_experts else 0.0
        if target <= 0:
            return {"updated": False, "reason": "no load measured",
                    "bias": list(self._lossfree_bias)}
        clamped = 0
        moved = 0
        for i in range(self.n_experts):
            if counts[i] > target:
                step = -alpha
            elif counts[i] < target:
                step = alpha
            else:
                # EXACTLY at its share: no step. A strict sign rule that
                # treats at-target as starved would push a perfectly even
                # load off balance forever (measured on the fixtures in the
                # #813 battery before this branch existed).
                step = 0.0
            if step:
                moved += 1
            new = max(-clamp_val,
                      min(clamp_val, self._lossfree_bias[i] + step))
            if new != self._lossfree_bias[i] + step:
                clamped += 1
            self._lossfree_bias[i] = new
        return {"updated": True, "reason": None, "target": target,
                "moved_count": moved, "clamped_count": clamped,
                "bias": list(self._lossfree_bias)}

    def _assign_bip(self, rows: Sequence[Sequence[float]]) \
            -> Tuple[List[List[int]], List[List[float]], List[List[float]]]:
        """R2 BIP quota assignment (arXiv:2502.15451) — the batch-solver route.

        Expert load balancing is a binary-integer-programming assignment
        problem: every token must supply exactly ``top_k`` expert slots and
        every expert may receive at most its equal-share quota. Solving
        BIP exactly is NP-hard, so the paper ships the same greedy we use:
        each token takes its highest-affinity experts whose quota is still
        open, falling to the next by affinity. On a batch where tokens can
        supply no unique preference (the collinear fixture) the quotas bind
        and the assignment becomes exactly even — which measured at 8/8
        experts used with per-batch entropy 1.0, where the shipped top-k
        router reports 2/8 and 1/3.

        One variable relative to ``switch``: the solver. Affinity is the
        SAME affine gate score (``_dot(gate_i, token) + gate_bias_i``),
        tokens keep their stable (score, index) ordering, and the leftover
        quota (``top_k * n_tokens`` not divisible by ``n_experts``) goes to
        the lowest-CUMULATIVE-count experts first, ties by index — so a
        repeated batch rotates its remainder deterministically instead of
        systematically favouring the front of the row.

        Weights stay on the same softmax vocabulary: each assigned expert
        keeps its share of the token's softmax mass, re-normalised over the
        assigned set, so only WHO is selected changes, not how loud.
        """
        n = len(rows)
        total_slots = self.top_k * n
        quotas = [total_slots // self.n_experts] * self.n_experts
        remainder = total_slots % self.n_experts
        if remainder:
            order_of_need = sorted(range(self.n_experts),
                                   key=lambda i: (self._cumulative[i], i))
            for i in order_of_need[:remainder]:
                quotas[i] += 1

        scored: List[List[Tuple[float, int]]] = []
        prob_rows: List[List[float]] = []
        for token in rows:
            logits = [_dot(row, token) + self.gate_bias[i]
                      for i, row in enumerate(self.gate)]
            probs = _softmax(logits)
            prob_rows.append(probs)
            scored.append(sorted((-probs[i], i) for i in range(self.n_experts)))

        left = list(quotas)
        chosen: List[List[int]] = []
        weights: List[List[float]] = []
        assigned_counts = [0] * self.n_experts
        for t in range(n):
            picks: List[int] = []
            for _neg_rank, i in scored[t]:
                if left[i] > 0:
                    picks.append(i)
                    left[i] -= 1
                    assigned_counts[i] += 1
                    if len(picks) == self.top_k:
                        break
            # A solver must never return fewer slots than the batch owes:
            # if every affinity expert is somehow closed (possible only if
            # quotas under-fill), the remaining picks come from the most
            # open experts so the invariant holds and is reported, not
            # silently violated.
            while len(picks) < self.top_k:
                fallback = max(range(self.n_experts),
                               key=lambda i: (left[i], -i))
                picks.append(fallback)
                left[fallback] -= 1
                assigned_counts[fallback] += 1
            chosen.append(picks)

        for t in range(n):
            selected = chosen[t]
            mass = sum(prob_rows[t][i] for i in selected) or 1.0
            weights.append([prob_rows[t][i] / mass for i in selected])
        return chosen, weights, prob_rows

    def _assign_lpr(self, rows: Sequence[Sequence[float]]) \
            -> Tuple[List[List[int]], List[List[float]], List[List[float]],
                     List[List[Tuple[int, List[float]]]]]:
        """R3 LPR assignment READING (arXiv:2506.21328) — prototype clusters.

        Latent Prototype Routing re-derives token routing from a
        CLUSTERING perspective: experts are latent prototypes and each
        token belongs by cluster affinity, not by a winner-take-all score.
        Our deterministic reading keeps the assignment solver (equal
        quotas, greedy by affinity, rotating remainder — the same solver
        ``_assign_bip`` uses) but swaps the affinity surface from the
        affine gate row to the expert's latent prototype: a prototype is
        the EMA of the rows the expert actually routed (warm-started from
        its gate row on the first batch), alpha = DEFAULT_LPR_PROTO_ALPHA.
        One variable relative to BIP = the affinity surface; one variable
        relative to switch = prototype clustering + assignment.

        Why it earns its place next to BIP: BIP quotas bind on ANY batch,
        including the collinear one, but on a batch whose tokens DO have
        distinct affinities the quota solver leaves a token with its
        second-best even if prototypes say the token belongs with a
        near-tie cluster — the LPR reading measures specialization
        (prototype spread) instead of only load. The diversity stats that
        ride the mode (``diversity_stats``) are the SimBal specialties
        (arXiv:2506.14038 / arXiv:2505.22323): orthogonality across expert
        prototypes and specialization variance.
        """
        if self._prototypes is None:
            # Warm start: prototypes = the gate rows, so the first batch's
            # LPR assignment is a pure gate reading, and the EMA has
            # something real to drift FROM.
            self._prototypes = [list(row) for row in self.gate]
        scored: List[List[Tuple[float, int]]] = []
        prob_rows: List[List[float]] = []
        for token in rows:
            # Affinity is PURE prototype: no gate_bias. Clusters read the
            # expert's latent centre; the Switch remedy stays a remedy and
            # the prototype space stays its own surface — that separation
            # is what makes the LPR-vs-BIP reading ONE variable.
            logits = [_dot(proto, token)
                      for proto in self._prototypes]
            probs = _softmax(logits)
            prob_rows.append(probs)
            scored.append(sorted((-probs[i], i)
                                 for i in range(self.n_experts)))

        total_slots = self.top_k * len(rows)
        quotas = [total_slots // self.n_experts] * self.n_experts
        remainder = total_slots % self.n_experts
        if remainder:
            order_of_need = sorted(range(self.n_experts),
                                   key=lambda i: (self._cumulative[i], i))
            for i in order_of_need[:remainder]:
                quotas[i] += 1

        left = list(quotas)
        chosen: List[List[int]] = []
        routed_rows: List[List[Tuple[int, List[float]]]] = []
        for t in range(len(rows)):
            picks: List[int] = []
            for _neg_rank, i in scored[t]:
                if left[i] > 0:
                    picks.append(i)
                    left[i] -= 1
                    if len(picks) == self.top_k:
                        break
            while len(picks) < self.top_k:
                fallback = max(range(self.n_experts),
                               key=lambda i: (left[i], -i))
                picks.append(fallback)
                left[fallback] -= 1
            chosen.append(picks)
            routed_rows.append([(i, list(rows[t])) for i in picks])

        weights: List[List[float]] = []
        for t in range(len(rows)):
            selected = chosen[t]
            mass = sum(prob_rows[t][i] for i in selected) or 1.0
            weights.append([prob_rows[t][i] / mass for i in selected])
        return chosen, weights, prob_rows, routed_rows

    def _update_prototypes(
            self, routed_rows: List[List[Tuple[int, List[float]]]]) -> None:
        """EMA-drift each expert's prototype toward its routed rows.

        Runs AFTER the batch's routing (self-influence is excluded by
        construction): a prototype that received rows moves toward their
        mean by DEFAULT_LPR_PROTO_ALPHA, an expert with no routed rows
        keeps its prototype untouched — no measurement, no movement, the
        same honesty contract every other measured update in this file
        follows.
        """
        if self._prototypes is None:
            return
        for i in range(self.n_experts):
            pts = [row
                   for token_routed in routed_rows
                   for (expert_id, row) in token_routed
                   if expert_id == i]
            if not pts:
                continue
            mean = [sum(col) / len(pts) for col in zip(*pts)]
            proto = self._prototypes[i]
            self._prototypes[i] = [
                (1.0 - DEFAULT_LPR_PROTO_ALPHA) * p
                + DEFAULT_LPR_PROTO_ALPHA * m for p, m in zip(proto, mean)]

    def diversity_stats(self) -> Dict[str, Any]:
        """SimBal wording of "how different are the experts" (Engram #813).

        orthogonality_loss: the MEAN off-diagonal cosine between expert
        prototypes — 0.0 means every prototype is perpendicular (perfect
        specialization, the SimBal target), 1.0 means they all point the
        same way (a collapsed gate seen from the specialization side).
        specialization_variance: the per-dimension variance of the
        prototypes' means = are the prototypes spread apart at all.
        Both are MEASUREMENTS (arXiv:2506.14038 / 2505.22323 as the wording
        source); with our #813 reading they ride the LPR mode's prototype
        space rather than a learned loss, because this model has no
        gradient path for the gate.
        """
        if self._prototypes is None:
            return {"orthogonality_loss": None, "specialization_variance": None,
                    "reason": "no prototype measured yet",
                    "n_pairs": 0}
        protos = self._prototypes
        squares = [sum(v * v for v in p) for p in protos]
        n_pairs = 0
        cosine_sum = 0.0
        for i in range(self.n_experts):
            for j in range(i + 1, self.n_experts):
                if squares[i] <= 0.0 or squares[j] <= 0.0:
                    continue
                denom = math.sqrt(squares[i] * squares[j]) or 1.0
                dot = _dot(protos[i], protos[j])
                cosine_sum += dot / denom
                n_pairs += 1
        dim_means = [sum(col) / len(protos) for col in zip(*protos)]
        spec_var = (sum(
            sum((p[d] - dim_means[d]) ** 2 for d in range(len(dim_means)))
            for p in protos) / max(1, len(protos) * len(dim_means)))
        return {
            "orthogonality_loss": round(cosine_sum / max(1, n_pairs), 6)
            if n_pairs else None,
            "specialization_variance": round(spec_var, 6),
            "reason": None,
            "n_pairs": n_pairs,
        }

    def _selection_spread(self, tokens: Sequence[Sequence[float]]) -> float:
        """max-min of the base selection scores this batch's gate would give.

        The measured authority for the Loss-Free clamp (see
        ``_update_lossfree_bias``): the smallest clamp guaranteed wide
        enough to move any expert across this batch's full ranking window.
        An empty batch reads 0.0, which collapses the clamp to no movement
        -- the correct authority for "nothing was measured".
        """
        lo: Optional[float] = None
        hi: Optional[float] = None
        for token in tokens:
            for row in self.gate:
                s = _dot(row, token)
                if lo is None or s < lo:
                    lo = s
                if hi is None or s > hi:
                    hi = s
        return (hi - lo) if (lo is not None and hi is not None) else 0.0

    def route(self, tokens: Sequence[Sequence[float]]) -> Tuple[List[List[int]], List[List[float]], List[List[float]]]:
        """Return (top-k indices, renormalised weights, full mean probs) per token.

        Mode-aware (Engram #813): ``switch`` is the shipped router and its
        path is byte-identical. ``bias`` (R1, arXiv:2408.15664) is the
        Loss-Free contract: the gate VALUES stay on the un-biased logits
        (so expert weighting is never distorted) and only the top-k
        SELECTION reads the extra ``_lossfree_bias`` vector.
        """
        chosen: List[List[int]] = []
        weights: List[List[float]] = []
        prob_rows: List[List[float]] = []
        for token in tokens:
            logits = [_dot(row, token) + self.gate_bias[i]
                      for i, row in enumerate(self.gate)]
            probs = _softmax(logits)
            prob_rows.append(probs)
            if self.route_mode == "bias":
                sel = [logits[i] + self._lossfree_bias[i]
                       for i in range(self.n_experts)]
            else:
                sel = logits
            order = sorted(range(self.n_experts), key=lambda i: (-sel[i], i))
            top = order[:self.top_k]
            mass = sum(probs[i] for i in top) or 1.0
            chosen.append(top)
            weights.append([probs[i] / mass for i in top])
        return chosen, weights, prob_rows

    def forward(self, tokens: Sequence[Sequence[float]]) -> Tuple[List[List[float]], float, Dict[str, Any]]:
        """Forward a batch. Returns (outputs, aux_loss, stats)."""
        n_tokens = len(tokens)
        if n_tokens == 0:
            self.last_stats = {"tokens": 0, "aux_loss": 0.0, "used_experts": 0}
            return [], 0.0, dict(self.last_stats)

        chosen, weights, prob_rows = self.route(tokens)
        # Defined up front so the lpr-only branch below never reads an unbound
        # variable when this batch routed under a different mode.
        routed_rows: List[List[Tuple[int, List[float]]]] = []
        # R2 BIP (Engram #813): a BATCH-level solver replaces per-token
        # selection in bip mode; the per-token route() output is thrown
        # away by the solver, so no mode smells the other's work.
        if self.route_mode == "bip":
            chosen, weights, prob_rows = self._assign_bip(self._coerce_rows(tokens))
        elif self.route_mode == "lpr":
            chosen, weights, prob_rows, routed_rows = self._assign_lpr(
                self._coerce_rows(tokens))
        self.last_inputs = list(tokens)
        out = [[0.0] * self.d_model for _ in range(n_tokens)]
        counts = [0] * self.n_experts
        for t, token in enumerate(tokens):
            for slot, expert_id in enumerate(chosen[t]):
                counts[expert_id] += 1
                w = weights[t][slot]
                expert_out = self.experts[expert_id].forward(token)
                row = out[t]
                for c in range(self.d_model):
                    row[c] += w * expert_out[c]

        f = [c / float(self.top_k * n_tokens) for c in counts]
        p = [sum(row[i] for row in prob_rows) / n_tokens for i in range(self.n_experts)]
        aux = float(self.n_experts) * sum(fi * pi for fi, pi in zip(f, p))

        # Cumulative view (Engram #813): every ACTUALLY routed token counts,
        # across batches, so per-batch top-k pinning cannot hide run-level
        # utilisation behind a per-batch entropy floor.
        for i, c in enumerate(counts):
            self._cumulative[i] += c

        # R1 Loss-Free (Engram #813): in bias mode the selection bias steps
        # after EVERY batch -- the router option that moves without a caller
        # remembering remediation. Reported, never silent.
        lossfree: Dict[str, Any] = {"updated": False}
        diversity: Dict[str, Any] = {"orthogonality_loss": None,
                                     "specialization_variance": None}
        if self.route_mode == "bias":
            lossfree = self._update_lossfree_bias(
                counts, clamp=self._selection_spread(tokens))
        elif self.route_mode == "lpr":
            # R3: prototypes drift AFTER this batch has routed (no
            # self-influence), and the drift is a measured EMA, reported.
            self._update_prototypes(routed_rows)
            diversity = self.diversity_stats()

        self.last_stats = {
            "tokens": n_tokens,
            "aux_loss": aux,
            "used_experts": sum(1 for c in counts if c > 0),
            "counts": counts,
            "balance": sorted(f),
            # Engram #813 instrumentation, same keys the balance snapshot
            # gained: one vocabulary for "how skewed is this gate" no matter
            # which path measured it.
            "gini": gini_coefficient(counts),
            "min_max_ratio": (min(counts) / max(counts)) if max(counts) else 0.0,
            "lossfree": lossfree,
            "diversity": diversity,
        }
        return out, aux, dict(self.last_stats)

    def param_count(self) -> int:
        return (len(self.gate) * len(self.gate[0]) + len(self.gate_bias)
                + sum(e.param_count() for e in self.experts))

    @staticmethod
    def _coerce_rows(tokens: Sequence[Any]) -> List[List[float]]:
        """Coerce a batch into float rows; a scalar row becomes one feature."""
        rows: List[List[float]] = []
        for row in (tokens or []):
            if isinstance(row, (list, tuple)):
                rows.append([float(v) for v in row])
            else:
                rows.append([float(row)])
        return rows

    def _load_snapshot(self, rows: Sequence[Sequence[float]]) -> Dict[str, Any]:
        """Measure the CURRENT load of the gate on ``rows`` (no nudge applied).

        One place derives every load number, so ``balance_step`` and ``rebalance``
        cannot disagree about what a collapsed gate looks like.
        """
        chosen, _weights, _probs = self.route(rows)
        counts = [0] * self.n_experts
        for top in chosen:
            for expert_id in top:
                counts[expert_id] += 1
        total = sum(counts)
        target = total / float(self.n_experts) if self.n_experts else 0.0
        return {
            "counts": counts,
            "target": target,
            "used_experts": sum(1 for c in counts if c > 0),
            "entropy_normalised": load_entropy_normalised(counts, self.n_experts),
            # Engram #813 instrumentation: entropy is forgiving of zero-count
            # experts at higher k; Gini is not, and the ratio names the two
            # ends of the load directly. Additive keys only -- no existing
            # reader of the snapshot changes meaning.
            "gini": gini_coefficient(counts),
            "min_max_ratio": (min(counts) / max(counts)) if max(counts) else 0.0,
            "starved_experts": [i for i, c in enumerate(counts) if c == 0],
            "max_skew": (max(counts) / target) if target else 0.0,
        }

    def cumulative_snapshot(self) -> Dict[str, Any]:
        """The load over EVERY forward since construction (Engram #813).

        Per-batch snapshots carry a structural floor -- a collinear batch pins
        top-k's used set no matter what the router does (test E,
        ``log(top_k)/log(n_experts)``) -- so mode acceptance is stated on the
        cumulative view, which is the view the live 2-of-8 report was read
        on. ``tokens_routed`` names the observation's support: a zero-token
        cumulative view is "nothing measured", never "balanced".
        """
        counts = list(self._cumulative)
        return {
            "counts": counts,
            "tokens_routed": sum(counts),
            "used_experts": sum(1 for c in counts if c > 0),
            "n_experts": self.n_experts,
            "entropy_normalised": load_entropy_normalised(counts, self.n_experts),
            "gini": gini_coefficient(counts),
            "starved_experts": [i for i, c in enumerate(counts) if c == 0],
            "min_max_ratio": (min(counts) / max(counts)) if max(counts) else 0.0,
        }

    @staticmethod
    def _load_is_balanced(snapshot: Dict[str, Any]) -> bool:
        """No starved expert and no expert above 1.25x its target share."""
        return (bool(snapshot["counts"]) and not snapshot["starved_experts"]
                and snapshot["max_skew"] <= 1.25)

    def balance_step(self, tokens: Sequence[Sequence[float]],
                     gamma: float = DEFAULT_BALANCE_GAMMA) -> Dict[str, Any]:
        """Nudge the per-expert gate bias toward an even load (count-based).

        This is the gate's ONLY learning path. The gate matrix is set at
        construction and nothing trains it: ``train_parallel`` updates the
        experts' output bias, and ``aux_loss_coef`` only scales a number that is
        reported. So a collapsed gate -- 2 of 8 experts taking every token --
        stays collapsed no matter how long training runs, which is exactly what
        the measured routing entropy showed. A count-based bias nudge is the
        Switch-Transformer remedy and needs no gradient: the routing table is
        what has to move, and a bias moves it directly.

        Deterministic on purpose. The same config and the same tokens give the
        same nudge, so two runs stay comparable and an ablation stays
        attributable. Each bias is clamped to ``MAX_GATE_BIAS`` so a long run of
        one-sided imbalance cannot drive a logit off to infinity.

        The returned ``balanced`` flag means only "a nudge was applied": it is
        True for any non-empty batch, however collapsed. Read ``load_balanced``
        (no starved expert and no expert above 1.25x target), ``max_skew`` and
        ``starved_experts`` for whether the LOAD is actually even.
        """
        rows = self._coerce_rows(tokens)
        if not rows:
            return {"balanced": False, "reason": "no tokens to route",
                    "counts": [], "target": 0.0,
                    "gini": 0.0, "min_max_ratio": 0.0,
                    "gate_bias": list(self.gate_bias)}

        snapshot = self._load_snapshot(rows)
        counts = snapshot["counts"]
        target = snapshot["target"]
        starved = snapshot["starved_experts"]
        max_skew = snapshot["max_skew"]
        for i in range(self.n_experts):
            step = -gamma if counts[i] > target else gamma
            self.gate_bias[i] = max(-MAX_GATE_BIAS,
                                    min(MAX_GATE_BIAS, self.gate_bias[i] + step))
        # ``balanced`` means "a nudge was applied", NOT "the load is even". It
        # has always been True whenever there were tokens to route: a live run
        # reported balanced=True alongside counts [0, 4586, 0, 0, 0, 3531, 1055,
        # 0] with target 1146.5 -- one expert at 4x its share and 5 of 8 starved.
        # The truthful measurements are ``load_balanced`` / ``max_skew`` /
        # ``starved_experts``; a monitor should read those, not ``balanced``.
        load_balanced = bool(target) and not starved and max(counts) <= target * 1.25
        return {"balanced": True, "reason": None, "counts": counts,
                "target": target,
                "load_balanced": load_balanced,
                "max_skew": max_skew,
                "starved_experts": starved,
                # Engram #813 additive keys, forwarded from the SAME snapshot
                # every other reader uses, so the vocabulary cannot drift.
                "gini": snapshot["gini"],
                "min_max_ratio": snapshot["min_max_ratio"],
                "gate_bias": list(self.gate_bias)}

    def rebalance(self, tokens: Sequence[Sequence[float]],
                  gamma: float = DEFAULT_BALANCE_GAMMA,
                  max_iters: int = DEFAULT_BALANCE_MAX_ITERS,
                  max_tokens: Optional[int] = DEFAULT_BALANCE_TOKENS) -> Dict[str, Any]:
        """Apply :meth:`balance_step` until the load is even or ``max_iters`` runs out.

        ONE nudge is not remediation. After a single ``balance_step`` the live
        ClusterWORMZ batch still used 2 of 8 experts, ``entropy_normalised``
        exactly 1/3, ``starved_experts`` 6 wide and ``max_skew`` 4.0 (engram
        #521 made that visible; this loop is what acts on it). The gate's load is
        what has to move, and one 0.01 bias nudge does not move it.

        The loop is BOUNDED and the bound is NAMED AND REPORTED rather than
        implied. ``max_iters`` (``DEFAULT_BALANCE_MAX_ITERS``) is the cap;
        ``cap_hit`` is True when the load was still uneven at the cap, and
        ``bound`` names why it could not improve. Two structural reasons are
        named, in precedence order:

        ``"top_k_pinned"`` -- a collinear batch makes the gate's argmax
        identical for every token, so top-k pins the used set to exactly
        ``top_k`` experts and per-batch ``entropy_normalised`` is stuck at
        ``log(top_k)/log(n_experts)``. For the live 2-of-8 gate that floor is
        exactly ``log(2)/log(8)`` = 1/3 -- so a report of "2 experts, entropy
        1/3" is the BOUND, not a failure of the loop, and the two are told apart
        here instead of being left for the caller to guess. It keeps precedence
        because a pinned used set is a floor no bias can move, clamp or not.

        ``"bias_clamp_exhausted"`` -- the loop ran out of iterations while at
        least one bias sat ON the ``MAX_GATE_BIAS`` clamp, so the gate has spent
        its whole authority and the load is still uneven (engram #528: the live
        batch at cap 256 reported ``bound=None`` here, and cap 512, 1024 and
        2048 were byte-identical -- a fixed point with one bias pinned at 2.0 of
        2.0 and the load still 3.08x its even share). Naming it matters because
        ``bound=None`` reads as "no bound", which invites the false conclusion
        that more iterations would help.

        ``before`` and ``after`` are measured snapshots of the same rows, so the
        effect is falsifiable: a caller compares the two rather than trusting a
        ``converged`` flag that a constant would satisfy. ``iterations`` is how
        many nudges were actually applied (0 if the load was already even), and
        ``rows_used`` / ``rows_available`` report the sampling bound.

        ``before``/``after`` describe the PROBE, which is a bounded SAMPLE, so on
        their own they cannot tell "the accumulating bias never reached the rows
        outside the probe" apart from "nothing measured those rows after the
        loop" -- both are the same observation on the probe's own numbers
        (engram #527: the live probe went from 2 of 8 experts to 8 of 8 while the
        run's 4235-row routing verdict stayed at 2 of 8). ``before_full`` /
        ``after_full`` therefore report the SAME before/after pair over EVERY
        available row, ``rows_full`` names the count they describe, and
        ``full_balanced`` is derived from ``after_full``. When the probe already
        IS the whole batch (``rows_used == rows_available``) the two pairs are
        one measurement and are returned equal rather than recomputed.
        """
        rows = self._coerce_rows(tokens)
        #: Untruncated, for the full-batch yardstick below. Kept separate because
        #: ``rows`` is rebound to the probe a few lines down.
        all_rows = rows
        available = len(rows)
        cap = int(max_iters)
        if max_tokens is not None and int(max_tokens) > 0 and available > int(max_tokens):
            rows = rows[:int(max_tokens)]
        if not rows:
            return {"balanced": False, "reason": "no tokens to route",
                    "counts": [], "target": 0.0,
                    "converged": False, "cap_hit": False, "bound": None,
                    "iterations": 0, "max_iters": cap, "gamma": gamma,
                    "before": None, "after": None,
                    "before_full": None, "after_full": None,
                    "rows_used": 0, "rows_available": available,
                    "rows_full": available, "full_balanced": False,
                    "gate_bias": list(self.gate_bias)}

        #: Charged only when the probe actually IS a sample (the lengths differ).
        #: Otherwise ``before``/``after`` already describe every row, so a second
        #: identical pass would be cost carrying no information.
        sampled = len(rows) != available
        before = self._load_snapshot(rows)
        before_full = self._load_snapshot(all_rows) if sampled else before
        current = before
        iterations = 0
        while iterations < cap and not self._load_is_balanced(current):
            self.balance_step(rows, gamma=gamma)
            iterations += 1
            current = self._load_snapshot(rows)
        after = current
        after_full = self._load_snapshot(all_rows) if sampled else after

        converged = self._load_is_balanced(after)
        cap_hit = bool(iterations >= cap and not converged)
        bound: Optional[str] = None
        if cap_hit and after["used_experts"] <= self.top_k:
            # Precedence: a collinear batch pins the used set regardless of how
            # much bias authority is left, so the clamp case must not shadow it.
            bound = "top_k_pinned"
        elif cap_hit and any(abs(b) >= MAX_GATE_BIAS for b in self.gate_bias):
            #: The third case the vocabulary could not name. Reached only when
            #: the loop is out of iterations AND some bias is on the clamp --
            #: i.e. the gate is out of AUTHORITY, not out of iterations. On the
            #: shipped config (cap 256) the live batch has no clamped bias, so
            #: every Rounds 1-5 shipped-config report stays ``bound=None``; this
            #: fires in the >=512-cap regime.
            bound = "bias_clamp_exhausted"
        return {
            # ``balance_step``'s keys, so an existing consumer keeps working.
            "balanced": True,
            "reason": None,
            "counts": after["counts"],
            "target": after["target"],
            "load_balanced": converged,
            "max_skew": after["max_skew"],
            "starved_experts": after["starved_experts"],
            "gate_bias": list(self.gate_bias),
            # The remediation report itself.
            "converged": converged,
            "cap_hit": cap_hit,
            "bound": bound,
            "iterations": iterations,
            "max_iters": cap,
            "gamma": gamma,
            "before": before,
            "after": after,
            "rows_used": len(rows),
            "rows_available": available,
            # The same before/after pair over EVERY row, so "did the remediation
            # reach the full batch" is a measurement rather than an inference.
            # Equal to ``before``/``after`` whenever the probe was the whole
            # batch (``rows_used == rows_available``).
            "before_full": before_full,
            "after_full": after_full,
            "rows_full": available,
            "full_balanced": self._load_is_balanced(after_full),
        }


class SelectiveSSM:
    """Mamba-style selective state-space scan -- O(n) in sequence length (req. 2).

    Per channel ``c`` and state ``s``::

        dt_c        = softplus(delta_w_c * u_c + delta_b_c)     # input-dependent
        A_c,s       = -exp(a_log_c,s)                           # negative => stable
        h_c,s  <-   exp(dt_c * A_c,s) * h_c,s + dt_c * B_c,s * u_c
        y_c         = sum_s C_c,s * h_c,s + D_c * u_c

    The scan is carried token by token and the op count is reported split into
    linear and quadratic terms, so the linear claim is measurable (the
    ``forward`` contract exposes it) instead of asserted.
    """

    LINEAR = "linear"
    QUADRATIC = "quadratic"

    def __init__(self, d_model: int, d_state: int, seed: int) -> None:
        self.d_model = int(d_model)
        self.d_state = int(d_state)
        r = _Rand(_component_seed(seed, "ssm"))
        self.a_log = r.matrix(self.d_model, self.d_state, scale=0.0)
        self.delta_w = r.vector(self.d_model, 0.1)
        self.delta_b = r.vector(self.d_model, 0.0)
        self.b_proj = r.matrix(self.d_model, self.d_state, scale=0.5)
        self.c_proj = r.matrix(self.d_model, self.d_state, scale=0.5)
        self.d_skip = r.vector(self.d_model, 1.0)
        self.last_ops: Dict[str, Any] = {}

    def forward(self, seq: Sequence[Sequence[float]]) -> Tuple[List[List[float]], Dict[str, Any]]:
        n = len(seq)
        if n == 0:
            self.last_ops = {"n": 0, self.LINEAR: 0, self.QUADRATIC: 0}
            return [], dict(self.last_ops)

        linear_ops = 0
        out: List[List[float]] = []
        h = [[0.0] * self.d_state for _ in range(self.d_model)]
        for token in seq:
            y = [0.0] * self.d_model
            for c in range(self.d_model):
                dt = _softplus(self.delta_w[c] * token[c] + self.delta_b[c])
                acc = 0.0
                for s in range(self.d_state):
                    decay = math.exp(dt * (-math.exp(self.a_log[c][s])))
                    h[c][s] = decay * h[c][s] + dt * self.b_proj[c][s] * token[c]
                    acc += self.c_proj[c][s] * h[c][s]
                    linear_ops += 4
                y[c] = acc + self.d_skip[c] * token[c]
                linear_ops += 2
            out.append(y)

        # Linear: every step touches a constant number of state elements.
        self.last_ops = {"n": n, self.LINEAR: linear_ops, self.QUADRATIC: 0}
        return out, dict(self.last_ops)

    def param_count(self) -> int:
        return (len(self.a_log) * len(self.a_log[0]) + len(self.delta_w) + len(self.delta_b)
                + len(self.b_proj) * len(self.b_proj[0])
                + len(self.c_proj) * len(self.c_proj[0]) + len(self.d_skip))


class AttentionBlock:
    """Quadratic scaled dot-product attention -- the control the SSM is measured against."""

    LINEAR = "linear"
    QUADRATIC = "quadratic"

    def __init__(self, d_model: int, seed: int) -> None:
        self.d_model = int(d_model)
        r = _Rand(_component_seed(seed, "attention"))
        self.wq = r.matrix(d_model, d_model)
        self.wk = r.matrix(d_model, d_model)
        self.wv = r.matrix(d_model, d_model)
        self.wo = r.matrix(d_model, d_model)
        self.last_ops: Dict[str, Any] = {}

    def forward(self, seq: Sequence[Sequence[float]]) -> Tuple[List[List[float]], Dict[str, Any]]:
        n = len(seq)
        if n == 0:
            self.last_ops = {"n": 0, self.LINEAR: 0, self.QUADRATIC: 0}
            return [], dict(self.last_ops)

        q = [[_dot(row, tok) for row in self.wq] for tok in seq]
        k = [[_dot(row, tok) for row in self.wk] for tok in seq]
        v = [[_dot(row, tok) for row in self.wv] for tok in seq]
        proj_ops = 3 * n * self.d_model * self.d_model

        scale = 1.0 / math.sqrt(self.d_model)
        quad_ops = 0
        out: List[List[float]] = []
        for i in range(n):
            scores = []
            for j in range(n):
                scores.append(_dot(q[i], k[j]) * scale)
                quad_ops += 2 * self.d_model
            attn = _softmax(scores)
            mixed = [0.0] * self.d_model
            for j in range(n):
                w = attn[j]
                if w == 0.0:
                    continue
                vj = v[j]
                for c in range(self.d_model):
                    mixed[c] += w * vj[c]
                quad_ops += self.d_model
            out.append([_dot(row, mixed) for row in self.wo])

        self.last_ops = {"n": n, self.LINEAR: proj_ops, self.QUADRATIC: quad_ops}
        return out, dict(self.last_ops)

    def param_count(self) -> int:
        return 4 * self.d_model * self.d_model


class FFNBlock:
    """Position-wise feed-forward (Two-layer MLP)."""

    LINEAR = "linear"
    QUADRATIC = "quadratic"

    def __init__(self, d_model: int, d_ff: int, seed: int) -> None:
        self.d_model = int(d_model)
        self.d_ff = int(d_ff)
        r = _Rand(_component_seed(seed, "ffn"))
        self.w1 = r.matrix(d_ff, d_model)
        self.w2 = r.matrix(d_model, d_ff)
        self.last_ops: Dict[str, Any] = {}

    def forward(self, seq: Sequence[Sequence[float]]) -> Tuple[List[List[float]], Dict[str, Any]]:
        n = len(seq)
        out = []
        for token in seq:
            hidden = [_gelu(_dot(row, token)) for row in self.w1]
            out.append([_dot(row, hidden) for row in self.w2])
        self.last_ops = {"n": n, self.LINEAR: n * (self.d_ff * self.d_model * 2),
                         self.QUADRATIC: 0}
        return out, dict(self.last_ops)

    def param_count(self) -> int:
        return self.d_ff * self.d_model * 2


# ---------------------------------------------------------------------------
# NULLs -- natively unlearnable training (task #519 / DeepSeek Task #511)
# ---------------------------------------------------------------------------
#
# Technique: partition each MLP's intermediate width into a shared backbone
# (always active) and a memorization sink pool that is sparsely activated, one
# deterministic pseudo-random mask per training source. Training is the ordinary
# loss with ONE change -- the post-nonlinearity activations are multiplied by
# ``[backbone ones | source mask]`` -- so the sink columns of the output
# projection absorb what a source needs, and excluding that source at inference
# multiplies its sink neurons away. No retraining is needed to forget a source:
# the mask carries the forgetting, and the source -> mask ledger is auditable
# (RULE 0V, right to be forgotten).
#
# Reference: NULLs, "Natively Unlearnable Large Language Models"
# (arXiv:2606.13873); this is the correctness prototype, not a scale demo.
#
# STATED LIMITATION: only the FFN block's intermediate is partitioned. A MoE
# expert is an MLP too, but its only learning surface in this module is the
# OUTPUT bias ``b2``, which sits after the intermediate -- masking an expert's
# intermediate would gate its activations without localizing what it memorized,
# so partitioning it would look like NULLs without being NULLs. The expert gap
# is reported in ``nulls_report()`` so the omission is visible.

#: Sizing, as RATIOS of the MLP intermediate width -- never a literal width.
NULLS_POOL_FRACTION = 0.25
NULLS_SOURCE_FRACTION = 0.5

#: Sidecar for the ledger. Seed + sizing + the source list, NOT the masks: a
#: mask is regenerated from the seed, so a stored mask could drift from the code.
NULLS_REGISTRY_FILENAME = "nulls_mask_registry.json"
NULLS_REGISTRY_DEFAULT_PATH = "state/" + NULLS_REGISTRY_FILENAME


def nulls_sizing(intermediate_width: int,
                 pool_fraction: float = NULLS_POOL_FRACTION,
                 source_fraction: float = NULLS_SOURCE_FRACTION) -> Dict[str, Any]:
    """Partition ``intermediate_width`` into backbone | pool, and size one mask.

    Read from the config by the caller rather than hardcoded here, so changing
    ``d_ff`` moves the partition with it. Raises on an unusable width instead of
    clamping, for the same reason the config does: a silent clamp would let a
    study believe it had partitioned something it had merely misconfigured.
    """
    width = int(intermediate_width)
    if width < 1:
        raise ValueError("intermediate width must be >= 1, got %r" % (intermediate_width,))
    if width == 1:
        pool = 1                     # a 1-wide MLP has room only for the sink
    else:
        pool = int(round(width * float(pool_fraction)))
        pool = max(1, min(pool, width - 1))   # the backbone keeps at least one
    backbone = width - pool
    n_source = min(pool, max(1, int(round(pool * float(source_fraction)))))
    return {
        "intermediate_width": width,
        "backbone": backbone,
        "pool": pool,
        "n_source": n_source,
        "pool_fraction": float(pool_fraction),
        "source_fraction": float(source_fraction),
    }


class NullsMaskRegistry:
    """source_id -> deterministic sparse mask over the sink pool.

    The mask is DERIVED, never stored: a local ``random.Random`` seeded from the
    model seed and the source's integer key. Local, because the alternative (the
    module-level RNG) makes one source's mask depend on whatever else drew from
    the global stream -- no global state means two processes regenerate the
    identical mask. A persisted registry therefore holds only the seed, the
    sizing and the source list, and cannot disagree with the code.

    A source is a worm's corpus name (a str) or an integer id; a str is keyed by
    crc32, which is stable across processes, unlike ``hash()`` (salted per
    process, so it would silently change every mask between runs).

    ``disjoint`` is the allocation POLICY, and it is recorded in the ledger
    because the same seed and sources mean different masks under each:

    - ``True`` (default): sources are dealt DISJOINT slices of the pool. This is
      the regime criterion 3 is stated in -- excluding one source must leave the
      other's recall exactly where it was.
    - ``False``: independent per-source random draws, which overlap at the
      chance rate ``n_source^2 / pool``. This is the NULLs paper's regime, where
      that overlap IS the interference; it is kept available so the interference
      can be measured rather than asserted.
    """

    #: v2: mask SEMANTICS changed (disjoint partition by default). A v1 ledger is
    #: refused rather than reused: an unlearning ledger whose masks now mean
    #: something else would report exclusions that never happened.
    VERSION = "nulls.v2"

    def __init__(self, sizing: Dict[str, Any], seed: int = 0,
                 sources: Optional[Sequence[Any]] = None,
                 disjoint: bool = True) -> None:
        self.sizing = dict(sizing)
        self.seed = int(seed)
        self.disjoint = bool(disjoint)
        self.pool = int(self.sizing["pool"])
        self.n_source = int(self.sizing["n_source"])
        self._labels: Dict[int, str] = {}
        self._masks: Dict[int, List[int]] = {}
        self._partition: Optional[Dict[int, List[int]]] = None
        for source_id in (sources or []):
            self.register(source_id)

    # -- keys -------------------------------------------------------------
    @staticmethod
    def source_key(source_id: Any) -> int:
        """Integer key for a source (bool is rejected: it is an int subclass)."""
        if isinstance(source_id, bool):
            raise TypeError("source_id must be an int or a str, not a bool")
        if isinstance(source_id, int):
            return int(source_id)
        return int(zlib.crc32(str(source_id).encode("utf-8")))

    def register(self, source_id: Any) -> int:
        """Record a source in the ledger (idempotent) and return its key."""
        key = self.source_key(source_id)
        if key not in self._labels:
            # The dealt partition is a function of the registered SET, so a new
            # source re-deals it -- and every memoised mask derived from it.
            self._partition = None
            self._masks = {}
        self._labels.setdefault(key, str(source_id))
        return key

    @property
    def sources(self) -> List[str]:
        """The registered source labels, in registration order."""
        return [self._labels[k] for k in self._labels]

    # -- masks ------------------------------------------------------------
    def capacity(self) -> int:
        """How many sources the pool can hold a FULL ``n_source`` mask each."""
        return self.pool // max(1, self.n_source)

    def _independent_mask(self, key: int) -> List[int]:
        """One source's own seeded draw -- the pre-v2 (paper) semantics."""
        rng = random.Random(_component_seed(self.seed, "nulls.source.%d" % key))
        row = [0] * self.pool
        for position in rng.sample(range(self.pool), self.n_source):
            row[position] = 1
        return row

    def _dealt_partition(self) -> Dict[int, List[int]]:
        """The pool dealt out in ``n_source``-wide chunks, one chunk per source.

        Positions come from ONE seeded permutation of the pool, and the chunks
        are handed out in ascending KEY order. Ascending key rather than
        registration order so the allocation is a function of the registered SET:
        two processes holding the same ledger deal the same masks, whichever
        one named its sources first.

        Why disjoint, when criterion 1 asks for the chance overlap to be
        MEASURED: overlap and criteria 3(a)+(b) cannot both hold, and the two are
        not a matter of degree. A fact lives in a source's sink columns, so a
        column two sources share carries two different requirements at once:

        - 3(a) all-mode: after B is written, A's recall reads B's values in the
          shared columns. Measured on the overlapping registry (8 of 32 shared):
          A accuracy 0.0 against B 1.0 -- A was overwritten, not averaged.
        - 3(b) exclusion: which columns an exclusion clears depends on the
          allocation. Under a per-source read, probing B with
          ``exclude_source_ids=[A]`` never touched A's columns anyway, so the
          criterion only bites where the excluded source's columns were live --
          and a union read, the mode in which "no exclusions" means every sink
          neuron is active, is damaged by A's own UNIQUE columns once B's are
          written (measured ``mean_abs_error`` 0.0967, accuracy 0.0). Disjoint
          parts make the two readings the same reading.

        A disjoint partition is the only allocation at which "excluding A leaves
        B's recall exactly where it was" is true rather than approximately true.
        ``expected_overlap()`` still reports the chance figure, and
        ``disjoint=False`` still reproduces the overlapping registry so that the
        interference is a measurement rather than a claim.
        """
        if self._partition is None:
            rng = random.Random(_component_seed(self.seed, "nulls.pool"))
            order = rng.sample(range(self.pool), self.pool)
            dealt: Dict[int, List[int]] = {}
            for rank, key in enumerate(sorted(self._labels)):
                start = rank * self.n_source
                dealt[key] = order[start:start + self.n_source]
            self._partition = dealt
        return {k: list(v) for k, v in self._partition.items()}

    def mask(self, source_id: Any) -> List[int]:
        """The sparse 0/1 mask over the pool for one source (memoised)."""
        key = self.register(source_id)
        if key not in self._masks:
            if self.disjoint:
                # Refuse on the SET, not on whichever source happens to sort last:
                # chunks are dealt in key order, so an over-subscribed pool leaves
                # a DIFFERENT source short depending on the keys involved, and a
                # refusal that moved with crc32 values would be untestable.
                if len(self._labels) > self.capacity():
                    raise ValueError(
                        "NULLs pool exhausted: capacity %d sources hold a full "
                        "%d-wide mask each (pool %d, n_source %d), but this ledger "
                        "holds %d -- the %s source's exclusion could not be exact. "
                        "Lower source_fraction to hold more sources."
                        % (self.capacity(), self.n_source, self.pool, self.n_source,
                           len(self._labels),
                           {1: 'first', 2: 'second', 3: 'third'}.get(
                               len(self._labels), '%dth' % len(self._labels))))
                positions = self._dealt_partition().get(key, [])
                if len(positions) < self.n_source:
                    raise ValueError(
                        "NULLs dealt source %r only %d of %d sink columns; its "
                        "exclusion could not be exact."
                        % (self._labels.get(key, source_id), len(positions),
                           self.n_source))
            else:
                positions = [p for p, bit in enumerate(self._independent_mask(key))
                             if bit]
            row = [0] * self.pool
            for position in positions:
                row[position] = 1
            self._masks[key] = row
        return list(self._masks[key])

    def active_mask(self, source_ids: Sequence[Any]) -> List[int]:
        """The union of several sources' masks: a position is on if ANY selects it.

        A union rather than a product: under ``disjoint=False`` two masks can
        share a position (chance overlap ``n_source^2 / pool``), and a shared
        neuron must stay active while at least one source that uses it is
        present. Under the default disjoint policy the union is simply the sum
        of the parts, which is what makes an exclusion exact.
        """
        row = [0] * self.pool
        for source_id in source_ids:
            for i, bit in enumerate(self.mask(source_id)):
                if bit:
                    row[i] = 1
        return row

    def overlap(self, source_a: Any, source_b: Any) -> int:
        """Measured shared positions between two sources' masks."""
        a = self.mask(source_a)
        b = self.mask(source_b)
        return sum(1 for x, y in zip(a, b) if x and y)

    def expected_overlap(self) -> float:
        """``n_source^2 / pool``: the overlap INDEPENDENT draws would give.

        The counterfactual, kept because it is the number criterion 1 names: it
        is what a per-source random draw overlaps at, and under the default
        disjoint policy it is reported next to a measured ``overlap()`` of 0
        rather than being the measured value.
        """
        return (float(self.n_source) ** 2) / float(self.pool) if self.pool else 0.0

    def fingerprint(self) -> str:
        """Identity of the LEDGER: seed, sizing, sources AND the mask policy."""
        return _fingerprint([self.VERSION, self.seed, self.sizing,
                             sorted(self._labels), self.disjoint])

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """The ledger as JSON: no masks, only what regenerates them."""
        return {
            "version": self.VERSION,
            "seed": self.seed,
            "sizing": dict(self.sizing),
            "disjoint": self.disjoint,
            "capacity": self.capacity(),
            "sources": [{"key": k, "label": self._labels[k]} for k in self._labels],
            "fingerprint": self.fingerprint(),
        }

    def save(self, path: Any = None) -> str:
        """Write the ledger next to the model artifacts; returns the path written."""
        target = Path(path) if path is not None else Path(NULLS_REGISTRY_DEFAULT_PATH)
        if target.suffix == "":
            target = target / NULLS_REGISTRY_FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True),
                          encoding="utf-8")
        return str(target)

    @classmethod
    def load(cls, path: Any = None) -> "NullsMaskRegistry":
        """Rebuild a registry from a sidecar and REGENERATE its masks."""
        target = Path(path) if path is not None else Path(NULLS_REGISTRY_DEFAULT_PATH)
        data = json.loads(target.read_text(encoding="utf-8"))
        if data.get("version") != cls.VERSION:
            raise ValueError("unsupported NULLs registry version %r (expected %r)"
                             % (data.get("version"), cls.VERSION))
        # The policy travels with the ledger: v2 refuses a v1 file outright, and
        # a v2 file states its policy rather than defaulting into one.
        registry = cls(data["sizing"], seed=int(data["seed"]),
                       disjoint=bool(data.get("disjoint", True)))
        for entry in data.get("sources", []):
            registry.register(entry["label"])
        return registry


class NullsFFNBlock(FFNBlock):
    """``FFNBlock`` whose intermediate width is backbone | sink pool.

    Same weights, same seed, same arithmetic as ``FFNBlock``: with no mask its
    forward path IS the parent's, which is what makes "nulls off means
    unchanged" a property of the code rather than a promise. The sink is the
    pool slice of ``w2``'s input columns -- the columns only pool neurons feed --
    so a step that updates those columns in place is training the sink, and the
    mask is what decides whether the trained content is reachable.
    """

    def __init__(self, d_model: int, d_ff: int, seed: int,
                 sizing: Dict[str, Any]) -> None:
        super().__init__(d_model, d_ff, seed)
        self.sizing = dict(sizing)
        self.pool_offset = int(self.sizing["backbone"])
        self.pool = int(self.sizing["pool"])

    def forward(self, seq: Sequence[Sequence[float]],
                mask: Optional[Sequence[int]] = None) -> Tuple[List[List[float]], Dict[str, Any]]:
        """Forward with the post-GELU activations multiplied by ``mask``.

        ``mask=None`` delegates to the parent, so an unmasked call cannot drift
        from ``FFNBlock``. A mask is validated rather than zipped short: a
        mismatched length would silently truncate the hidden state to the shorter
        of the two, which reads as a working forward with half the MLP.
        """
        if mask is None:
            return super().forward(seq)
        if len(mask) != self.d_ff:
            raise ValueError("mask width %d != d_ff %d" % (len(mask), self.d_ff))
        n = len(seq)
        out = []
        for token in seq:
            hidden = [_gelu(_dot(row, token)) for row in self.w1]
            gated = [h * m for h, m in zip(hidden, mask)]
            out.append([_dot(row, gated) for row in self.w2])
        self.last_ops = {"n": n, self.LINEAR: n * (self.d_ff * self.d_model * 2),
                         self.QUADRATIC: 0}
        return out, dict(self.last_ops)


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

@register_worm(category="learning", priority=5, cooldown_seconds=120.0,
               description="Cutting-edge MoE+SSM hybrid model (task #507): sparse top-k "
                           "routing with aux load balancing, O(n) selective scan, and "
                           "per-component ablation switches")
class CuttingEdgeModel:
    """Composes the enabled blocks into one ablatable model.

    Every switch is independent and ADDITIVE (req. 3): a layer builds the
    sequence mixers (``ssm``, then ``attention``) followed by the channel mixers
    (``moe``, then ``ffn``), each present only when its own flag is on. An
    either/or build would let a config claim ``use_attention=True`` while the
    model silently built no attention -- ``active_components()`` would then
    describe a model that does not exist. Turning everything off leaves an
    embedding-only baseline (the empty set a full-factorial ablation needs)
    rather than an error.
    """

    VERSION = "1.0.0"

    def __init__(self, config: Optional[CuttingEdgeConfig] = None,
                 nulls: Any = None) -> None:
        self.config = config or CuttingEdgeConfig()
        self._lock = threading.Lock()

        # NULLs is a CONSTRUCTOR flag, not a config field: adding a field to the
        # frozen config would change `to_dict()` and therefore the fingerprint of
        # every existing config, which is an identity change to models that have
        # nothing to do with this feature. Off by default.
        self.use_nulls = bool(getattr(nulls, "enabled", nulls) or False)
        self.nulls_pool_fraction = float(getattr(nulls, "pool_fraction", NULLS_POOL_FRACTION))
        self.nulls_source_fraction = float(getattr(nulls, "source_fraction",
                                                  NULLS_SOURCE_FRACTION))
        self._nulls_sizing: Optional[Dict[str, Any]] = None
        self._nulls_registry: Optional[NullsMaskRegistry] = None
        self.last_nulls: Optional[Dict[str, Any]] = None

        d = self.config.d_model
        seed = self.config.seed
        # A single stem projects the incoming feature row into the model width.
        self.embed = _Rand(_component_seed(seed, "embed")).matrix(d, d)

        self.ssms: List[SelectiveSSM] = []
        self.attentions: List[AttentionBlock] = []
        self.moes: List[SparseMoE] = []
        self.ffns: List[FFNBlock] = []
        self.plan: List[List[str]] = []

        for _layer in range(self.config.n_layers):
            layer_plan: List[str] = []
            if self.config.use_ssm:
                self.ssms.append(SelectiveSSM(d, self.config.d_state, seed))
                layer_plan.append("ssm")
            if self.config.use_attention:
                self.attentions.append(AttentionBlock(d, seed))
                layer_plan.append("attention")
            if self.config.use_moe:
                self.moes.append(SparseMoE(d, self.config.n_experts, self.config.top_k, seed))
                layer_plan.append("moe")
            if self.config.use_ffn:
                if self.use_nulls:
                    sizing = self.nulls_sizing()
                    self.ffns.append(NullsFFNBlock(d, self.config.d_ff, seed, sizing))
                else:
                    self.ffns.append(FFNBlock(d, self.config.d_ff, seed))
                layer_plan.append("ffn")
            self.plan.append(layer_plan)

        self.last_ops: Dict[str, Any] = {}

    # -- introspection ----------------------------------------------------
    def active_components(self) -> Tuple[str, ...]:
        return self.config.active_components()

    def config_fingerprint(self) -> str:
        return self.config.fingerprint()

    def component_fingerprint(self, component: str) -> str:
        """Digest of one component's parameters, for non-interference checks."""
        parts: List[Any] = []
        if component == "ssm":
            parts = [[s.param_count(), s.delta_w, s.d_skip] for s in self.ssms]
        elif component == "attention":
            parts = [[a.wq, a.wv] for a in self.attentions]
        elif component == "moe":
            parts = [[m.gate, [e.b2 for e in m.experts]] for m in self.moes]
        elif component == "ffn":
            parts = [[f.w1, f.w2] for f in self.ffns]
        else:
            raise KeyError("unknown component %r (expected one of %r)" % (component, COMPONENTS))
        return _fingerprint(parts)

    def param_count(self) -> int:
        total = len(self.embed) * len(self.embed[0])
        total += sum(s.param_count() for s in self.ssms)
        total += sum(a.param_count() for a in self.attentions)
        total += sum(m.param_count() for m in self.moes)
        total += sum(f.param_count() for f in self.ffns)
        return total

    def describe(self) -> Dict[str, Any]:
        """Machine-readable architecture description (feeds the spec document)."""
        return {
            "version": self.VERSION,
            "config": self.config.to_dict(),
            "config_fingerprint": self.config_fingerprint(),
            "active_components": list(self.active_components()),
            "plan": [list(layer) for layer in self.plan],
            "param_count": self.param_count(),
            "blocks": {
                "moe": {"count": len(self.moes), "experts": self.config.n_experts,
                        "top_k": self.config.top_k, "aux_loss_coef": self.config.aux_loss_coef},
                "ssm": {"count": len(self.ssms), "d_state": self.config.d_state,
                        "complexity": "O(n) linear in sequence length"},
                "attention": {"count": len(self.attentions),
                              "complexity": "O(n^2) quadratic in sequence length"},
                "ffn": {"count": len(self.ffns), "d_ff": self.config.d_ff},
            },
        }

    # -- forward ----------------------------------------------------------
    def forward(self, features: Sequence[Sequence[float]],
                source_id: Any = None,
                source_ids: Optional[Sequence[Any]] = None,
                exclude_source_ids: Sequence[Any] = ()) -> Dict[str, Any]:
        """Run the stack over a batch of feature rows.

        ``features`` is the ``List[List[float]]`` half of the
        ``SchemaAwareFeatureExtractor.extract`` contract; a 1-D row is accepted
        and treated as a single-token batch.

        ``source_id`` / ``source_ids`` name the corpus this batch came from and
        ``exclude_source_ids`` forgets sources (task #519). All three are ignored
        when NULLs is off, so a caller can pass them unconditionally: with the
        flag off, this forward is byte-for-byte the pre-NULLs one.
        """
        rows = self._as_rows(features)
        if not rows:
            raise ValueError("forward() needs at least one feature row")
        mask = self._nulls_forward_mask(source_id, source_ids, exclude_source_ids)

        width = self.config.d_model
        h: List[List[float]] = []
        for row in rows:
            v = self._fit_row(row, width)
            h.append([_dot(w_row, v) for w_row in self.embed])

        aux_total = 0.0
        ops_total: Dict[str, Any] = {"linear": 0, "quadratic": 0, "n": len(h)}
        moe_stats: List[Dict[str, Any]] = []
        ssm_index = 0
        attn_index = 0
        moe_index = 0
        ffn_index = 0

        for layer_plan in self.plan:
            for block_name in layer_plan:
                if block_name == "ssm":
                    h, ops = self.ssms[ssm_index].forward(h)
                    ssm_index += 1
                elif block_name == "attention":
                    h, ops = self.attentions[attn_index].forward(h)
                    attn_index += 1
                elif block_name == "moe":
                    h, aux, stats = self.moes[moe_index].forward(h)
                    aux_total += aux
                    moe_stats.append(stats)
                    moe_index += 1
                    ops = {"linear": 0, "quadratic": 0}
                else:
                    ffn = self.ffns[ffn_index]
                    if mask is None:
                        h, ops = ffn.forward(h)
                    else:
                        h, ops = cast(NullsFFNBlock, ffn).forward(h, mask=mask)
                    ffn_index += 1
                ops_total["linear"] += int(ops.get("linear", 0))
                ops_total["quadratic"] += int(ops.get("quadratic", 0))

        self.last_ops = ops_total
        result = {
            "output": h,
            "shape": (len(h), width),
            "aux_loss": aux_total,
            "aux_loss_scaled": aux_total * self.config.aux_loss_coef,
            "active_components": list(self.active_components()),
            "ops": dict(ops_total),
            "moe_stats": moe_stats,
        }
        if self.use_nulls:
            # Reported only when the feature is on, so an off model's result dict
            # keeps exactly the keys it had before NULLs existed.
            result["nulls"] = dict(self.last_nulls or {})
        return result

    def forward_sequence(self, seq: Sequence[Sequence[float]]) -> Dict[str, Any]:
        """Forward for the complexity measurement: returns the op split per block."""
        rows = self._as_rows(seq)
        result = self.forward(rows)
        return {"result": result, "ops": dict(self.last_ops)}

    # -- ablation ---------------------------------------------------------
    def ablate(self, component: str, enabled: bool = False) -> "CuttingEdgeModel":
        """Return a NEW model with ``component`` switched off (or on).

        A new object rather than an in-place edit: sharing one model across an
        ablation sweep is how a study ends up comparing a state it already
        mutated.
        """
        if component not in COMPONENTS:
            raise KeyError("unknown component %r (expected one of %r)" % (component, COMPONENTS))
        return CuttingEdgeModel(self.config.variant(**{_USE_FLAG[component]: bool(enabled)}))

    def ablation_matrix(self) -> List[Dict[str, Any]]:
        """The one-at-a-time sweep #507 req. 7 asks for, as data.

        Baseline first, then each single-component removal, so a caller can run
        the sweep without re-deriving which combinations are interesting.
        """
        matrix: List[Dict[str, Any]] = [{"name": "full", "switches": self.config.ablation_switches(),
                   "config_fingerprint": self.config_fingerprint()}]
        for component in self.active_components():
            variant = self.config.variant(**{_USE_FLAG[component]: False})
            model = CuttingEdgeModel(variant)
            matrix.append({
                "name": "without_%s" % component,
                "switches": variant.ablation_switches(),
                "config_fingerprint": variant.fingerprint(),
                "param_count": model.param_count(),
            })
        return matrix

    # -- integration: feature extractor -----------------------------------
    @staticmethod
    def _as_rows(features: Any) -> List[List[float]]:
        if features is None:
            return []
        if isinstance(features, (list, tuple)) and features and isinstance(features[0], (int, float)):
            return [[float(x) for x in features]]
        return [[float(x) for x in row] for row in features]

    def _fit_row(self, row: Sequence[float], width: int) -> List[float]:
        """Pad or truncate a feature row to the model width, then normalise."""
        v = list(row[:width])
        if len(v) < width:
            v = v + [0.0] * (width - len(v))
        return _rms_norm(v)

    def from_schema_features(self, features: Sequence[Sequence[float]],
                             meta: Optional[Dict[str, Any]] = None,
                             source_id: Any = None,
                             source_ids: Optional[Sequence[Any]] = None,
                             exclude_source_ids: Sequence[Any] = ()) -> Dict[str, Any]:
        """Consume ``(features, meta)`` from ``SchemaAwareFeatureExtractor``.

        The extractor owns its feature width; the model projects it. ``meta`` is
        passed through so ``feature_dim`` / ``shapes_used`` stay attached to the
        result instead of being silently dropped at the boundary.

        ``source_id`` / ``exclude_source_ids`` are forwarded to ``forward``: this
        is the ONE hook point the pipeline needs to plumb a corpus id through, and
        they are inert while NULLs is off.
        """
        rows = self._as_rows(features)
        if not rows:
            raise ValueError("from_schema_features() needs at least one feature row")
        result = self.forward(rows, source_id=source_id, source_ids=source_ids,
                              exclude_source_ids=exclude_source_ids)
        widths = sorted({len(r) for r in rows})
        result["feature_meta"] = {
            "rows": len(rows),
            "input_widths": widths,
            "projected_to": self.config.d_model,
            "extractor_meta": dict(meta) if meta else {},
        }
        return result

    # -- integration: pool + DLQ + versioner ------------------------------
    #: NULLs lives here: sizing, the ledger, the mask a batch runs under, the
    #: one-source training step, and a probe that reports accuracy rather than a
    #: loss (a loss can improve while the thing being measured is forgetfulness).

    def nulls_sizing(self) -> Dict[str, Any]:
        """backbone | pool | n_source for THIS config's FFN intermediate width."""
        if self._nulls_sizing is None:
            self._nulls_sizing = nulls_sizing(self.config.d_ff,
                                              self.nulls_pool_fraction,
                                              self.nulls_source_fraction)
        return dict(self._nulls_sizing)

    @property
    def nulls_registry(self) -> NullsMaskRegistry:
        """The source -> mask ledger, built on first use."""
        if self._nulls_registry is None:
            self._nulls_registry = NullsMaskRegistry(self.nulls_sizing(),
                                                     seed=self.config.seed)
        return self._nulls_registry

    def _nulls_forward_mask(self, source_id: Any, source_ids: Optional[Sequence[Any]],
                            exclude_source_ids: Sequence[Any]) -> Optional[List[int]]:
        """The mask this batch runs under, or ``None`` when NULLs is off.

        ``None`` is not "an empty mask": it is the absence of masking, i.e. the
        pre-NULLs forward exactly. With the feature ON and no source named, the
        active set is every source in the ledger; an empty ledger therefore runs
        backbone-only (every memorization sink off), which is the maximally
        forgetful reading and is reported rather than guessed at.
        """
        if not self.use_nulls:
            self.last_nulls = None
            return None
        registry = self.nulls_registry
        if source_ids is not None:
            active = [s for s in source_ids]
        elif source_id is not None:
            active = [source_id]
        else:
            active = list(registry.sources)
        excluded = [s for s in (exclude_source_ids or ())]
        excluded_keys = {registry.source_key(s) for s in excluded}
        kept = [s for s in active if registry.source_key(s) not in excluded_keys]
        active_keys = {registry.source_key(s) for s in active}
        sizing = self.nulls_sizing()
        pool_mask = registry.active_mask(kept)
        mask = [1] * int(sizing["backbone"]) + pool_mask
        self.last_nulls = {
            "active": [str(s) for s in kept],
            "excluded": [str(s) for s in excluded],
            "excluded_applied": sorted(active_keys & excluded_keys),
            "backbone": int(sizing["backbone"]),
            "pool": int(sizing["pool"]),
            "pool_active": int(sum(pool_mask)),
            "mask_width": len(mask),
            "ledger_fingerprint": registry.fingerprint(),
        }
        return mask

    def nulls_report(self) -> Dict[str, Any]:
        """What NULLs is (or is not) doing in this model -- including the gap."""
        sizing = self.nulls_sizing()
        blocks = [b for b in self.ffns if isinstance(b, NullsFFNBlock)]
        sink_params = sum(b.pool * b.d_model for b in blocks)
        report: Dict[str, Any] = {
            "enabled": bool(self.use_nulls),
            "active": bool(blocks),
            "sizing": sizing,
            "ffn_blocks": len(self.ffns),
            "nulls_ffn_blocks": len(blocks),
            "sink_columns_per_block": int(sizing["pool"]),
            "sink_params": sink_params,
            "param_count": self.param_count(),
            "expert_intermediate_width": (max(1, 2 * self.config.d_model)
                                           if self.moes else 0),
            "partition": "[backbone ones | per-source sink mask]",
        }
        if not self.use_nulls:
            report["reason"] = "nulls is off: this is the pre-NULLs architecture"
        elif not blocks:
            report["reason"] = (
                "use_ffn is False, so this config has no FFN intermediate to partition. "
                "The MoE expert gap is deliberately NOT partitioned: an expert's only "
                "learning surface here is its OUTPUT bias, which sits after the "
                "intermediate, so masking it would gate activations without localizing "
                "what the expert memorized")
        return report

    def _run_until_last_ffn(self, rows: List[List[float]],
                            mask: Optional[Sequence[int]]) -> List[List[float]]:
        """Activations entering the LAST FFN block.

        Mirrors ``forward``'s dispatch for the prefix only. It exists because the
        NULLs step needs the readout-side block's own input: training its sink
        columns does not change that input, so the gradient computed from it is
        exact rather than an approximation.
        """
        width = self.config.d_model
        h: List[List[float]] = []
        for row in rows:
            v = self._fit_row(row, width)
            h.append([_dot(w_row, v) for w_row in self.embed])
        flat = [(i, name) for i, layer in enumerate(self.plan) for name in layer]
        stop = None
        for index in range(len(flat) - 1, -1, -1):
            if flat[index][1] == "ffn":
                stop = index
                break
        if stop is None:
            return h
        ssm_index = attn_index = moe_index = ffn_index = 0
        for index, (_layer, name) in enumerate(flat):
            if index >= stop:
                break
            if name == "ssm":
                h, _ops = self.ssms[ssm_index].forward(h)
                ssm_index += 1
            elif name == "attention":
                h, _ops = self.attentions[attn_index].forward(h)
                attn_index += 1
            elif name == "moe":
                h, _aux, _stats = self.moes[moe_index].forward(h)
                moe_index += 1
            else:
                if mask is None:
                    h, _ops = self.ffns[ffn_index].forward(h)
                else:
                    h, _ops = cast(NullsFFNBlock, self.ffns[ffn_index]).forward(h, mask=mask)
                ffn_index += 1
        return h

    @staticmethod
    def _sink_mse(block: "NullsFFNBlock", gated: Sequence[Sequence[float]],
                  targets: Sequence[Sequence[float]]) -> float:
        """Readout MSE against ``targets`` for already-gated activations."""
        total = 0.0
        for g, t in zip(gated, targets):
            out = [_dot(row, g) for row in block.w2]
            total += sum((o - v) ** 2 for o, v in zip(out, t))
        return total / max(1, len(gated))

    @staticmethod
    def _solve_linear(matrix: List[List[float]],
                      rhs: List[List[float]]) -> List[List[float]]:
        """Solve ``A X = B`` for several right-hand sides (partial-pivot elimination).

        Stdlib only, and it reports nothing: a singular column is skipped rather
        than faked, because a ridge term has already made the system invertible by
        the time this is called.
        """
        n = len(matrix)
        if n == 0:
            return []
        augmented = [list(row) + list(b) for row, b in zip(matrix, rhs)]
        # A RELATIVE skip threshold: an absolute 1e-12 would discard legitimate
        # pivots of a small-activation gram (and quietly turn a relative ridge of
        # 1e-9 into no ridge at all), while a large-scale matrix still needs a
        # threshold well above its rounding noise.
        floor = 1e-12 * max(max((abs(v) for row in matrix for v in row), default=1.0),
                            1e-300)
        for col in range(n):
            pivot = max(range(col, n), key=lambda r: abs(augmented[r][col]))
            if abs(augmented[pivot][col]) < floor:
                continue
            augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
            inv = 1.0 / augmented[col][col]
            augmented[col] = [v * inv for v in augmented[col]]
            for r in range(n):
                if r == col:
                    continue
                factor = augmented[r][col]
                if factor == 0.0:
                    continue
                augmented[r] = [rv - factor * cv
                                for rv, cv in zip(augmented[r], augmented[col])]
        return [row[n:] for row in augmented]

    def _fit_sink_least_squares(self, block: "NullsFFNBlock",
                                gated: Sequence[Sequence[float]],
                                targets: Sequence[Sequence[float]],
                                mask: Sequence[int],
                                ridge: float = 1e-6) -> Dict[str, Any]:
        """Set the sink columns to the exact minimiser of the readout MSE.

        The readout is LINEAR in the sink activations, so this is the standard
        loss minimised for that parameter block rather than stepped toward -- i.e.
        the same objective, solved instead of approximated. It matters here: a
        partial gradient step (the first version of this prototype) left the
        all-mode accuracy at 0.0, which would have read as "the mask cannot hold
        the fact" when the truth was "the optimiser had not converged". The mask
        is the thing under test, so the optimiser must not be the limiting factor.

        The solved objective is the MODEL's readout, i.e. ``F X + c`` where ``c``
        is the contribution of every non-sink column (the backbone, and any pool
        column this batch's mask leaves off). ``c`` is fixed by the parameters, so
        minimising over the sink columns means a right-hand side of
        ``F^T (T - c)``, not ``F^T T``.

        That distinction is a measured defect, not a nicety. With ``F^T T`` the
        solver still satisfied its own normal equations to 7e-14 while the readout
        it was measured on sat at 0.176446, and -- the tell -- that number did not
        move when the ridge went 1e-6 -> 0, or when the design was made
        well-conditioned. A residual that ignores the objective and the
        conditioning is a wrong objective: the model was being scored on
        ``F X + c - T`` while the fit minimised ``F X - T``.
        """
        offset = int(block.pool_offset)
        columns = [j for j in range(int(block.pool)) if mask[offset + j]]
        if not columns:
            return {"columns": 0, "ridge": float(ridge), "rank": 0}
        rows = len(gated)
        d_model = int(block.d_model)
        design = [[g[offset + j] for j in columns] for g in gated]
        # What the sink columns must reproduce: the target minus everything the
        # sink does NOT own. Measured against this block's own readout, so the
        # quantity subtracted is the one the probe will later score.
        residual: List[List[float]] = []
        for r, g in enumerate(gated):
            fixed = [_dot(row, g) for row in block.w2]
            for i, j in enumerate(columns):
                value = g[offset + j]
                for d in range(d_model):
                    fixed[d] -= block.w2[d][offset + j] * value
            residual.append([targets[r][d] - fixed[d] for d in range(d_model)])
        # Normal equations for every output dimension at once:
        # (F^T F + ridge I) X = F^T (T - c)
        gram = [[sum(design[r][a] * design[r][b] for r in range(rows))
                 for b in range(len(columns))] for a in range(len(columns))]
        # The ridge is RELATIVE to the gram's own scale, which the activations
        # set: an absolute lambda is a different amount of regularisation for
        # every d_ff and every corpus. Measured on the patterned fixture, the
        # residual floor IS the ridge -- 9.6e-7 at 1e-6, 4.3e-14 at 0 -- so it is
        # scaled to the matrix it regularises rather than left in activation units.
        lam = float(ridge) * max(max(gram[i][i] for i in range(len(columns))), 1e-300)
        for i in range(len(columns)):
            gram[i][i] += lam
        rhs = [[sum(design[r][i] * residual[r][d] for r in range(rows))
                for d in range(d_model)] for i in range(len(columns))]
        solution = self._solve_linear(gram, rhs)
        for i, j in enumerate(columns):
            for d in range(d_model):
                block.w2[d][offset + j] = solution[i][d]
        return {"columns": len(columns), "ridge": float(ridge),
                "ridge_applied": lam, "rank": len(columns),
                "gram_diagonal": [round(gram[i][i], 8) for i in range(len(columns))],
                "objective": "readout_mse_with_fixed_contribution_removed"}

    def memorize(self, source_id: Any, rows: Sequence[Sequence[float]],
                 targets: Sequence[Sequence[float]],
                 ridge: float = 1e-9) -> Dict[str, Any]:
        """The NULLs training step: fit ONE source into its sink columns.

        ``ridge`` is RELATIVE to the fit's own gram scale (see
        ``_fit_sink_least_squares``), so the default means the same amount of
        regularisation at any ``d_ff``; it exists so a rank-deficient batch is
        still solvable, and it is what a residual floor on an exact fixture is
        made of.

        Mean-squared error against ``targets``, with this batch's mask applied --
        the mask is the ONLY change from ordinary training. The readout-side
        FFN's sink columns (the pool slice of ``w2``'s input columns) are updated
        in place. Its input does not depend on them, so the least-squares solution
        for those columns is exact, and the gated activations are computed once.

        With ``n_layers > 1`` only the LAST block's sink is trained, and
        ``sinks_trained`` names which blocks moved: the same limitation
        ``train_parallel`` states for the experts' first layer. It is reported,
        not hidden.
        """
        if not self.use_nulls:
            raise RuntimeError("memorize() needs NULLs: construct the model with nulls=True")
        blocks = [b for b in self.ffns if isinstance(b, NullsFFNBlock)]
        if not blocks:
            return {"skipped": True,
                    "reason": "no FFN block in this config (use_ffn=False): nothing to train"}
        block = blocks[-1]  # same object: use_nulls ⇒ every ffns entry is a NullsFFNBlock
        mask = self._nulls_forward_mask(source_id, None, ())
        if mask is None:
            raise RuntimeError(
                "memorize(): _nulls_forward_mask returned None "
                "despite use_nulls being on (invariant violated)")
        inputs = self._run_until_last_ffn(self._as_rows(rows), mask)
        target_rows = self._as_rows(targets)
        if len(target_rows) != len(inputs):
            raise ValueError("memorize() needs one target row per input row, got %d vs %d"
                             % (len(target_rows), len(inputs)))
        pool = int(block.pool)
        gated: List[List[float]] = []
        for token in inputs:
            hidden = [_gelu(_dot(row, token)) for row in block.w1]
            gated.append([h * m for h, m in zip(hidden, mask)])

        loss_before = self._sink_mse(block, gated, target_rows)
        fit = self._fit_sink_least_squares(block, gated, target_rows, mask, ridge=ridge)
        loss_after = self._sink_mse(block, gated, target_rows)
        offset = int(block.pool_offset)
        return {
            "source_id": str(source_id),
            "rows": len(gated),
            "solver": "ridge_least_squares",
            "ridge": float(ridge),
            "sinks_trained": [len(self.ffns) - 1],
            "sink_columns": pool,
            "sink_columns_fitted": int(fit.get("columns", 0)),
            "sink_params": pool * int(block.d_model),
            "mask_active": int(sum(mask)),
            "fit": fit,
            "loss_before": loss_before,
            "loss_after": loss_after,
            "loss_delta": loss_after - loss_before,
            "nulls": dict(self.last_nulls or {}),
        }

    def probe_source(self, source_id: Any, rows: Sequence[Sequence[float]],
                     targets: Sequence[Sequence[float]],
                     exclude_source_ids: Sequence[Any] = (),
                     tol: float = 0.1) -> Dict[str, Any]:
        """How well does this batch match its targets -- with sources excluded?

        Accuracy, not loss: the claim under test is that a fact is retrievable,
        and a mean squared error can look respectable while no row is right.
        """
        out = self.forward(rows, source_id=source_id,
                           exclude_source_ids=exclude_source_ids)["output"]
        target_rows = self._as_rows(targets)
        accurate = 0
        error_total = 0.0
        n = 0
        for o, t in zip(out, target_rows):
            diffs = [abs(a - b) for a, b in zip(o, t)]
            error_total += sum(diffs) / max(1, len(diffs))
            n += 1
            if diffs and max(diffs) <= tol:
                accurate += 1
        return {
            "source_id": str(source_id),
            "rows": n,
            "accurate": accurate,
            "accuracy": (accurate / n) if n else 0.0,
            "mean_abs_error": (error_total / n) if n else 0.0,
            "tolerance": float(tol),
            "excluded": sorted(str(s) for s in (exclude_source_ids or ())),
        }

    def nulls_design_matrix(self, source_id: Any,
                            rows: Sequence[Sequence[float]]) -> List[List[float]]:
        """The gated activations at this source's ACTIVE sink columns.

        The sink pool's own basis for a batch: this is what the sink can hold, so
        a synthetic fact built from it is exactly representable and a failed fit
        therefore means the mask or the solver is wrong, never that the target was
        out of reach.
        """
        mask = self._nulls_forward_mask(source_id, None, ())
        if mask is None:
            raise RuntimeError("nulls_design_matrix() needs NULLs enabled")
        block = self.ffns[-1]
        if not isinstance(block, NullsFFNBlock):
            raise RuntimeError("this config has no FFN block to partition")
        offset = int(block.pool_offset)
        columns = [j for j in range(int(block.pool)) if mask[offset + j]]
        inputs = self._run_until_last_ffn(self._as_rows(rows), mask)
        design: List[List[float]] = []
        for token in inputs:
            hidden = [_gelu(_dot(row, token)) for row in block.w1]
            gated = [h * m for h, m in zip(hidden, mask)]
            design.append([gated[offset + j] for j in columns])
        return design

    def nulls_fact_targets(self, source_id: Any, rows: Sequence[Sequence[float]],
                           scale: float = 5.0) -> List[List[float]]:
        """A synthetic fact for one source that its own sink can hold EXACTLY.

        ``target = current output + scale * sink basis``. A probe fixture rather
        than a claim: because the target is exactly representable, the fit must
        reach machine-precision error, which makes a low all-mode accuracy a
        defect signal instead of a tuning problem. Two sources get different
        scales (and signs), so one source's fact leaking into the other is visible
        rather than averaged away.
        """
        design = self.nulls_design_matrix(source_id, rows)
        out = self.forward(rows, source_id=source_id)["output"]
        width = self.config.d_model
        targets: List[List[float]] = []
        for r, produced in enumerate(out):
            row = list(produced)
            for j, value in enumerate(design[r]):
                row[j % width] += float(scale) * value
            targets.append(row)
        return targets

    def save_nulls_registry(self, path: Any = None) -> Dict[str, Any]:
        """Persist the ledger (seed + sizing + sources; NOT the masks)."""
        registry = self.nulls_registry
        written = registry.save(path)
        return {"path": written, "fingerprint": registry.fingerprint(),
                "sources": registry.sources, "sizing": registry.sizing,
                "seed": registry.seed}

    def load_nulls_registry(self, path: Any = None,
                            adopt: bool = True) -> NullsMaskRegistry:
        """Load a ledger and adopt it, after checking it describes THIS model.

        A registry built for a different intermediate width or a different seed
        would produce different masks, so it is refused rather than adopted: an
        exclusion that silently maps to the wrong neurons is worse than an error.
        """
        registry = NullsMaskRegistry.load(path)
        if registry.sizing != self.nulls_sizing():
            raise ValueError("registry sizing %r does not match this model's %r"
                             % (registry.sizing, self.nulls_sizing()))
        if registry.seed != int(self.config.seed):
            raise ValueError("registry seed %r does not match this model's config seed %r"
                             % (registry.seed, self.config.seed))
        if adopt:
            self._nulls_registry = registry
        return registry

    def adopt_nulls_registry(self, registry: NullsMaskRegistry) -> NullsMaskRegistry:
        """Adopt an already-built ledger (e.g. one a caller constructed itself)."""
        if registry.sizing != self.nulls_sizing():
            raise ValueError("registry sizing %r does not match this model's %r"
                             % (registry.sizing, self.nulls_sizing()))
        self._nulls_registry = registry
        return registry

    # -- integration: pool + DLQ + versioner ------------------------------
    def _expert_local_loss(self, index: int, tokens: Sequence[Sequence[float]],
                           bias2: Optional[Sequence[float]] = None) -> float:
        """Autoencoder-style local objective for one expert (read-only)."""
        expert = self.moes[0].experts[index]
        total = 0.0
        for token in tokens:
            out = expert.forward(token, bias2=bias2)
            total += sum((o - t) ** 2 for o, t in zip(out, token))
        return total / max(1, len(tokens))

    def _expert_gradient_job(self, index: int, tokens: Sequence[Sequence[float]],
                             lr: float, eps: float) -> Dict[str, Any]:
        """Finite-difference gradient for expert ``index``'s output bias.

        Deliberately READ-ONLY on shared state: the perturbed bias is passed to
        ``forward`` explicitly rather than written into the expert, so N of these
        can run concurrently under 3.15t free-threading without a race. The
        caller applies the returned gradients after the whole group completes.
        """
        base_bias = list(self.moes[0].experts[index].b2)
        before = self._expert_local_loss(index, tokens, bias2=base_bias)
        grad: List[float] = []
        probe = list(base_bias)
        for j in range(len(probe)):
            original = probe[j]
            probe[j] = original + eps
            up = self._expert_local_loss(index, tokens, bias2=list(probe))
            probe[j] = original
            grad.append((up - before) / eps)
        return {
            "expert": index,
            "loss_before": before,
            "grad": grad,
            "grad_norm": math.sqrt(sum(g * g for g in grad)),
            "lr": lr,
        }

    def train_parallel(self, pool: Any, tokens: Optional[Sequence[Sequence[float]]] = None,
                       lr: float = 0.05, eps: float = 1e-3,
                       timeout: Optional[float] = 10.0,
                       max_gradient_tokens: Optional[int] = DEFAULT_GRADIENT_TOKENS,
                       balance: bool = True) -> Dict[str, Any]:
        """One training step over every expert of the FIRST MoE block, in ONE group.

        ``n_layers > 1`` trains the first layer's experts only: the finite
        difference below reads one expert set, and quietly spreading a partial
        update across layers would report a step that did not happen. The
        limitation is stated here rather than hidden in the arithmetic.

        The finite-difference gradient costs ``d_model`` full-batch evaluations
        per expert, so its cost is O(len(tokens)) and a production batch cannot
        fit a pool timeout -- at 3,765 tokens every job missed the timeout and
        ``experts_updated`` stayed 0. The sample is therefore bounded by
        ``max_gradient_tokens`` AND reported, and the before/after pair is
        measured on the SAME sampled rows: measuring one side on the sample and
        the other on the full batch would compare two different objectives. The
        prefix is deterministic rather than random, so two runs of one config
        stay comparable.

        A step that updated nothing returns ``loss_before`` / ``loss_after`` /
        ``loss_delta`` of ``None`` rather than a delta: with no job returned the
        before-total is a sum over an EMPTY set, so a delta from it is a number
        about nothing. That is what let a production run report ``0.0 -> 1.4491``
        -- a "regression" -- while nothing had been updated at all.

        Independent branches are submitted as a single schedulable unit
        (``submit_group``), which is the contract ``WorkStealingPool`` exposes for
        exactly this shape. Gradient estimation runs in the pool; the parameter
        update is applied afterwards, serially, so concurrent readers never see a
        half-applied step. When ``balance`` is set, the MoE gate's bias is nudged
        toward an even load in the same locked section; that runs even when no
        expert updated, because the gate's imbalance is independent of whether the
        gradient fit inside the timeout, and it is reported in ``balancing``.
        """
        if not self.moes:
            return {"skipped": True, "reason": "moe component is disabled in this config"}
        if tokens is None:
            rand = _Rand(_component_seed(self.config.seed, "train.batch"))
            tokens = [rand.vector(self.config.d_model) for _ in range(8)]
        rows = self._as_rows(tokens)

        # Bound the finite-difference cost. A non-positive bound means "no bound",
        # so passing 0 or a negative value cannot silently train on nothing.
        probe = rows
        if (max_gradient_tokens is not None and int(max_gradient_tokens) > 0
                and len(rows) > int(max_gradient_tokens)):
            probe = rows[:int(max_gradient_tokens)]

        jobs = [(self._expert_gradient_job, (i, probe, lr, eps), {})
                for i in range(self.config.n_experts)]
        group_id = pool.submit_group(jobs)
        waited = pool.wait_group(group_id, timeout=timeout)
        aggregate = pool.group_results(group_id)

        before_total = 0.0
        applied = 0
        with self._lock:
            for row in aggregate.get("results", []):
                if not row.get("success"):
                    continue
                payload = row.get("result") or {}
                index = payload.get("expert")
                if index is None:
                    continue
                before_total += float(payload.get("loss_before", 0.0))
                bias = self.moes[0].experts[index].b2
                for j, g in enumerate(payload.get("grad", [])):
                    bias[j] -= lr * g
                applied += 1
            # Measured on `probe`, the same rows the gradient used, so the pair is
            # one objective on one input and the delta is interpretable.
            after_total = sum(self._expert_local_loss(i, probe)
                              for i in range(self.config.n_experts))
            # Balance on the activations the MoE actually routed (``last_inputs``)
            # rather than on ``probe``: the training tokens are the model's output
            # rows, so regulating them tunes the gate against a distribution it
            # never sees. Falls back to ``probe`` only when no forward has run.
            # ``rebalance``, not a single ``balance_step``: one 0.01 nudge left the
            # live ClusterWORMZ gate still collapsed at 2 of 8 experts (engram
            # #521/#524). The loop nudges until the load is even or its named cap
            # is reached, and reports which of the two happened.
            balance_rows = self.moes[0].last_inputs or probe
            balancing = self.moes[0].rebalance(balance_rows) if balance else None

        result = {
            "group_id": group_id,
            "waited_ok": bool(waited),
            "total": aggregate.get("total"),
            "succeeded": aggregate.get("succeeded"),
            "failed": aggregate.get("failed"),
            "errors": aggregate.get("errors", []),
            "experts_updated": applied,
            "tokens_seen": len(rows),
            "gradient_tokens": len(probe),
            "balancing": balancing,
            "lr": lr,
        }
        if applied == 0:
            # No job returned, so the before-total is a sum over an empty set and
            # there is no delta to report. Hand back the current loss separately
            # rather than dressing it up as an after-update measurement.
            result["loss_before"] = None
            result["loss_after"] = None
            result["loss_delta"] = None
            result["loss_current"] = after_total
        else:
            result["loss_before"] = before_total
            result["loss_after"] = after_total
            result["loss_delta"] = after_total - before_total
        return result

    def record_failure(self, dlq: Any, error: Dict[str, Any],
                       config: Optional[Dict[str, Any]] = None,
                       worm_name: Optional[str] = None) -> Any:
        """Route a failed run to the DeadLetterQueue instead of dropping it."""
        entry = dlq.enqueue(worm_name or self.__class__.__name__,
                            error=dict(error), config=dict(config or {}))
        return entry

    def register_version(self, versioner: Any, metrics: Dict[str, float],
                         name: str = "cutting_edge_model",
                         metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Register this exact configuration with the ModelVersioner.

        The version string carries the config fingerprint, so two ablations are
        never recorded under one version.
        """
        meta = dict(metadata or {})
        meta.setdefault("active_components", list(self.active_components()))
        meta.setdefault("config_fingerprint", self.config_fingerprint())
        meta.setdefault("param_count", self.param_count())
        return versioner.register_model(
            name, "%s+%s" % (self.VERSION, self.config_fingerprint()), dict(metrics), metadata=meta)

    # -- integration: inference APIs --------------------------------------
    def reward(self, signals: Sequence[float], provider_names: Optional[Sequence[str]] = None,
               limit: int = 6, allow_network: bool = False,
               prompt: str = "") -> Dict[str, Any]:
        """Reward shaping over up to ``limit`` of Azazael's inference providers.

        Reports which providers are actually usable on this host and refuses to
        pretend a call happened. With ``allow_network=False`` (the default) the
        local rubric is the source and ``providers_available`` names what a live
        run could use -- a test can therefore assert on the reported source
        without making an unrequested network call.
        """
        local = self._rubric_reward(signals)
        out: Dict[str, Any] = {
            "source": "local_rubric",
            "reward": local,
            "signals": [float(s) for s in signals],
            "providers_available": [],
            "providers_used": [],
            "attempted_network": False,
        }
        try:
            from CORE.inference import providers as _providers  # scanner:ignore
            names = list(provider_names) if provider_names else list(
                getattr(_providers, "PROVIDER_REGISTRY", {}).keys())
            usable: List[str] = []
            for name in names[:max(0, int(limit))]:
                try:
                    provider = _providers.get_provider(name)
                    if provider is not None and provider.has_valid_key():
                        usable.append(name)
                except Exception as exc:                  # one bad provider is not fatal
                    note_failure("cutting_edge_model:reward-provider", exc)
                    continue
            out["providers_available"] = usable
            if allow_network and usable:
                out["attempted_network"] = True
                shaped = self._provider_reward(_providers, usable[0], prompt)
                if shaped is not None:
                    out["source"] = "inference_api"
                    out["providers_used"] = [usable[0]]
                    out["reward"] = shaped
        except ImportError as exc:
            note_failure("cutting_edge_model:reward-import", exc)
            out["providers_unavailable"] = "CORE.inference.providers not importable here"
        return out

    @staticmethod
    def _rubric_reward(signals: Sequence[float]) -> float:
        """Deterministic local reward: mean of the provided signals."""
        vals = [float(s) for s in signals]
        return sum(vals) / len(vals) if vals else 0.0

    def _provider_reward(self, providers_mod: Any, name: str, prompt: str) -> Optional[float]:
        """Ask one provider to score the prompt. Returns None if it cannot.

        Returning None (rather than the local value) keeps the caller honest: it
        only relabels the source as an inference API when a number actually came
        back from one.
        """
        try:
            provider = providers_mod.get_provider(name)
            response = provider.query(prompt) if prompt else None
            if not response:
                return None
            text = response if isinstance(response, str) else str(response)
            digits = [float(m) for m in _NUMBER_RE.findall(text)]
            if not digits:
                return None
            return max(-1.0, min(1.0, sum(digits) / len(digits)))
        except Exception as exc:                          # a scored reward must not crash a run
            note_failure("cutting_edge_model:provider-reward", exc)
            return None

    @staticmethod
    def _export_dependency_state() -> Dict[str, List[str]]:
        """Which ONNX-export dependencies can actually be imported on this host.

        A class existing in a module is NOT a capability. ``deployment_plan``
        used to set ``available`` from ``hasattr`` on the converter class, which
        was True on a host with no ONNX exporter at all -- a false "available"
        that let the deploy path look closed when it was not.
        ``importlib.util.find_spec`` answers the question that matters: would
        this import succeed?

        Probes what the export path actually reaches for. ``convert()`` tries
        ``optimum.onnxruntime`` first, so ``optimum`` is the dependency that
        yields a real artifact. ``onnx`` and ``onnxscript`` are probed too
        because torch's dynamo exporter path is the one that fails FIRST
        (``No module named 'onnxscript'``), so a reader who installed only
        ``onnx`` would still not be able to export.
        """
        wanted = ("optimum", "onnx", "onnxscript", "torch", "transformers")
        present: List[str] = []
        missing: List[str] = []
        for name in wanted:
            try:
                found = importlib.util.find_spec(name) is not None
            except (ImportError, ValueError):   # a broken parent package counts as absent
                found = False
            (present if found else missing).append(name)
        return {"present": present, "missing": missing}

    # -- integration: deployment ------------------------------------------
    def deployment_plan(self, mem_gb: float = 16.0, has_gpu: bool = False,
                        env: str = "win-cpu") -> Dict[str, Any]:
        """ONNX -> quantize -> serve -> failover, resolved against the real tree.

        Resolves the classes that do the work and reports what is available here;
        it does not run an export, because exporting a model is a deliberate
        action with an artifact, not a side effect of asking for a plan.

        "Available" means an export could actually RUN, not that a converter
        class exists: the dependency probe above decides it. The class inventory
        is reported separately, because conflating the two is what produced a
        working-looking ONNX entry on a host with no exporter.
        """
        plan: Dict[str, Any] = {
            "steps": [
                "train  -> CuttingEdgeModel (this module) or an SB3Agent policy",
                "export -> ONNX via ORCHESTRATION.ml_platforms.universal_converter.ONNXConverter",
                "quantize -> GPTQ-4bit (GPU) / AWQ-4bit (GPU) / GGUF-Q4_K_M (CPU)",
                "serve  -> ModelShardServerWorm.publish(model, shard, env, fmt, quant)",
                "failover -> providers.get_provider across the provider registry",
            ],
            "env": env,
        }
        try:
            from CORE.WORMS.expansion.glia import OligodendrocyteWorm  # scanner:ignore
            plan["quant"] = OligodendrocyteWorm().recommend(mem_gb=mem_gb, has_gpu=has_gpu)
        except Exception as exc:                          # record, do not fabricate a format
            note_failure("cutting_edge_model:deploy-quant", exc)
            plan["quant"] = {"success": False, "reason": "glia recommender unavailable",
                             "error": str(exc)}

        converters = []
        try:
            from ORCHESTRATION.ml_platforms import universal_converter as _uc  # scanner:ignore
            # Inventory of the classes the module defines. This was never a lie;
            # it was simply never an answer to "can we export?" -- so it is kept
            # and the availability question is answered by the probe instead.
            converters = [n for n in ("ONNXConverter", "PyTorchConverter", "GGUFAnalyzer")
                          if hasattr(_uc, n)]
            deps = self._export_dependency_state()
            # optimum is the path that produces a real artifact, so it is what
            # availability turns on. The former fallback "succeeded" by exporting
            # a randomly initialised SimpleDecoder instead of the requested model;
            # it has been removed, so onnx+onnxscript alone is not enough either.
            can_export = "optimum" in deps["present"] and "ONNXConverter" in converters
            plan["onnx"] = {
                "available": bool(can_export),
                "converters": converters,
                "module": "ORCHESTRATION.ml_platforms.universal_converter",
                "dependencies_present": deps["present"],
                "dependencies_missing": deps["missing"],
            }
            if not can_export:
                absent_export = [m for m in deps["missing"]
                                 if m in ("optimum", "onnx", "onnxscript")]
                if "ONNXConverter" not in converters:
                    plan["onnx"]["reason"] = (
                        "ONNXConverter class is absent from %s"
                        % plan["onnx"]["module"])
                else:
                    plan["onnx"]["reason"] = (
                        "ONNXConverter exists but cannot export here: convert() "
                        "tries optimum first and it is missing (%s)"
                        % (", ".join(absent_export) or "no exporter dependency"))
                    if "onnxscript" in deps["missing"]:
                        plan["onnx"]["reason"] += (
                            "; torch's dynamo exporter fails first on onnxscript, "
                            "so installing onnx alone is not enough")
                plan["onnx"]["note"] = (
                    "a former fallback reported success by exporting a randomly "
                    "initialised SimpleDecoder instead of the requested model; it "
                    "has been removed, so this cannot go green falsely")
        except Exception as exc:
            note_failure("cutting_edge_model:deploy-onnx", exc)
            plan["onnx"] = {"available": False, "converters": [],
                            "reason": "ml_platforms converter not importable here",
                            "error": str(exc)}

        try:
            from ORCHESTRATION.ml_platforms import universal_converter as _uc2  # scanner:ignore
            _has_gguf = (hasattr(_uc2, "GGUFAnalyzer")
                         and hasattr(_uc2.GGUFAnalyzer, "convert"))
            _quantizer = (_uc2.GGUFAnalyzer.find_quantizer()
                          if _has_gguf else None)
            plan["quantize"] = {
                "available": bool(_has_gguf and _quantizer),
                "converters": ["GGUFAnalyzer.convert"] if _has_gguf else [],
                "module": "ORCHESTRATION.ml_platforms.universal_converter",
                "quantizer_binary": _quantizer,
            }
            if not plan["quantize"]["available"]:
                if not _has_gguf:
                    plan["quantize"]["reason"] = (
                        "GGUFAnalyzer.convert is absent; the quantize step "
                        "has no writer")
                else:
                    plan["quantize"]["reason"] = (
                        "GGUFAnalyzer.convert exists but no writer backend "
                        "here: llama-quantize not on PATH (the gguf pip "
                        "package is inspection-only)")
        except Exception as exc:
            note_failure("cutting_edge_model:deploy-quantize", exc)
            plan["quantize"] = {"available": False, "converters": [],
                                "reason": "ml_platforms converter not importable here",
                                "error": str(exc)}

        try:
            from CORE.WORMS.expansion.servemesh import ModelShardServerWorm  # scanner:ignore
            plan["serve"] = {
                "available": True,
                "class": ModelShardServerWorm.__name__,
                "contract": "publish(model, shard, env, fmt, quant)",
                # `available` here means the CLASS imports and is callable -- not
                # that serving is verified. publish() only writes a shard dict, and
                # a negative control with a NONEXISTENT model also returned
                # success:True, so a success from this stage is a record, not a
                # health check. Named here so the entry cannot be read as more.
                "caveat": ("publish() writes a dict and verifies no artifact, so "
                           "its success:True is not evidence of serving"),
            }
        except Exception as exc:
            note_failure("cutting_edge_model:deploy-serve", exc)
            plan["serve"] = {"available": False, "reason": "servemesh not importable here",
                             "error": str(exc)}
        return plan

    # -- 3.15t native capability probe ------------------------------------
    def capabilities(self) -> Dict[str, Any]:
        """Probe the interpreter for the 3.15t features #507 req. 4 names.

        Absence is reported as absence. ``has_tachyon`` is False on every host
        seen so far because ``sys.tachyon`` does not exist -- a plan that assumed
        a profiler here would be planning against a feature the runtime lacks.
        """
        soabi = sysconfig.get_config_var("SOABI") or ""
        gil_disabled = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
        try:
            from concurrent.futures import InterpreterPoolExecutor  # noqa: F401  # scanner:ignore
            has_interpreter_pool = True
        except ImportError as exc:
            note_failure("cutting_edge_model:cap-interpreter-pool", exc)
            has_interpreter_pool = False

        return {
            "interpreter": sys.version.split()[0],
            "gil_disabled": gil_disabled,
            "soabi": soabi,
            "has_frozendict": hasattr(builtins, "frozendict"),
            "has_sentinel": hasattr(builtins, "sentinel"),
            "has_lazy_imports": hasattr(sys, "set_lazy_imports"),
            "has_jit": hasattr(sys, "set_jit") or hasattr(sys, "_jit"),
            "has_tachyon": hasattr(sys, "tachyon"),
            "has_interpreter_pool": has_interpreter_pool,
            "has_abi3t_marker": "abi3t" in soabi,
            "has_numpy": importlib.util.find_spec("numpy") is not None,
            "has_torch": importlib.util.find_spec("torch") is not None,
            "requested": {
                "enable_lazy_imports": self.config.enable_lazy_imports,
                "enable_jit": self.config.enable_jit,
                "profile_hooks": self.config.profile_hooks,
            },
        }

    def enable_lazy_imports(self) -> Dict[str, Any]:
        """Apply ``sys.set_lazy_imports`` when the runtime has it (PEP 810)."""
        setter = getattr(sys, "set_lazy_imports", None)
        if not callable(setter):
            return {"applied": False, "reason": "sys.set_lazy_imports absent on %s"
                                                % sys.version.split()[0]}
        try:
            setter("all")                             # PEP 810 lazy imports, when present
            return {"applied": True, "mode": "all"}
        except Exception as exc:
            note_failure("cutting_edge_model:lazy-imports", exc)
            return {"applied": False, "reason": str(exc)}

    def apply_profile_hooks(self) -> Dict[str, Any]:
        """Attach Tachyon profiling when present; report when it is not."""
        if not hasattr(sys, "tachyon"):
            return {"applied": False, "profiler": None,
                    "reason": "sys.tachyon absent on this build"}
        return {"applied": True, "profiler": "tachyon"}

    # -- worm contract -----------------------------------------------------
    def get_summary(self) -> Dict[str, Any]:
        """Worm contract: side-effect-free summary (see BaseWormMixin).

        The registry smoke test instantiates every registered worm and calls
        this. It reports configuration only -- no forward pass, no
        LocalMemoryStore access -- so smoking this worm cannot train anything.
        """
        return {
            "success": True,
            "method": "get_summary",
            "result": {
                "version": self.VERSION,
                "config_fingerprint": self.config_fingerprint(),
                "active_components": list(self.active_components()),
                "param_count": self.param_count(),
                "n_experts": self.config.n_experts,
                "top_k": self.config.top_k,
                "d_model": self.config.d_model,
                "n_layers": self.config.n_layers,
            },
        }


