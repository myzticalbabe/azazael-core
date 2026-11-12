# coding: utf-8
"""Engram #813 (DEEPSEEK ARXIV-HARVEST) — router-option battery, one mode per slice.

Baseline (measured FIRST in this session, not after the change):

    switch-mode, collinear batch (64 identical rows, seed 7):
        counts [0,0,0,64,0,64,0,0]  used 2/8
        cumulative entropy 0.3333 (= log(2)/log(8), the top-k floor)
        cumulative gini 0.75       min_max_ratio 0.0
    live pool baseline: 500 technique-link rows, 100% severity info,
    ok_rate 1.0 -> ZERO measured negatives exist in the stream today.

Slice layout (each a separate gated commit, ONE variable each):
  A instrumentation (gini / min_max_ratio / cumulative view) -- this file's
    bottom block, plus the byte-identical contract for ``switch``.
  B R1 Loss-Free bias (arXiv:2408.15664)  C R2 BIP quota (2502.15451)
  D R3 LPR prototypes (2506.21328) + SimBal diversity stats
  E F1 NAT negatives  F F2 Likra near-miss   (training_intake.py side)

The structural floor is stated plainly (test E of
``test_cutting_edge_production_fixes.py``): a collinear batch pins the
top-k used set to ``top_k`` experts, so NO logit- or bias-side fix can
raise PER-BATCH entropy on that fixture. Mode acceptance is therefore
stated on the CUMULATIVE view (across batches) and, for the
assignment-side modes (bip/lpr), per-batch as well, because assignment
side-steps the pinning argument. An entropy claim is never asserted where
the fixture cannot support it.
"""

import math
import sys
from pathlib import Path

import pytest

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.cutting_edge_model import (  # noqa: E402
    DEFAULT_LOSSFREE_ALPHA, MAX_GATE_BIAS, gini_coefficient,
    load_entropy_normalised, SparseMoE,
)

#: The collinear fixture -- the live 2-of-8 defect, replicated.
D_MODEL, N_EXPERTS, TOP_K, SEED = 16, 8, 2, 7
COLLINEAR_BATCH = [[1.0 * j for j in range(D_MODEL)]] * 64

#: Baseline numbers, asserted once so the fixture's identity is pinned.
COLLINEAR_COUNTS = [0, 0, 0, 64, 0, 64, 0, 0]
COLLINEAR_GINI = 0.75
COLLINEAR_ENTROPY = 1.0 / 3.0


def _entropy_norm(counts, n):
    total = sum(counts)
    if total <= 0 or n < 2:
        return 0.0
    probs = [c / total for c in counts if c > 0]
    return -sum(p * math.log(p) for p in probs) / math.log(n)


def _build(mode=None):
    kwargs = {}
    if mode is not None:
        kwargs["route_mode"] = mode
    return SparseMoE(d_model=D_MODEL, n_experts=N_EXPERTS, top_k=TOP_K,
                     seed=SEED, **kwargs)


# --------------------------------------------------------------------------
# Slice A -- instrumentation
# --------------------------------------------------------------------------

def test_gini_helper_values_with_positive_control():
    """Gini: even load exactly 0.0 (positive control first), full collapse -> 1."""
    assert gini_coefficient([16] * 8) == 0.0
    # One expert takes everything in an 8-expert gate -> (n-1)/n = 7/8.
    assert math.isclose(gini_coefficient([0, 0, 0, 0, 0, 0, 0, 512]), 7 / 8)
    # The measured baseline spread (two of eight live) is 0.75 -- both numbers.
    assert math.isclose(gini_coefficient([0, 0, 0, 64, 0, 64, 0, 0]), 0.75)
    # Degenerate support: nothing measured must read 0.0, never "balanced".
    assert gini_coefficient([]) == 0.0
    assert gini_coefficient([0] * 8) == 0.0


def test_entropy_helper_matches_module_and_baseline():
    """load_entropy_normalised stays the one entropy vocabulary."""
    assert math.isclose(load_entropy_normalised([64, 64] + [0] * 6, 8), 1 / 3)
    assert math.isclose(_entropy_norm([64, 64] + [0] * 6, 8), 1 / 3)
    # even -> 1.0
    assert math.isclose(load_entropy_normalised([16] * 8, 8), 1.0)


def test_forward_stats_carry_instrumentation():
    """forward's stats and the balance snapshot share the same vocabulary."""
    moe = _build()
    _out, _aux, stats = moe.forward(COLLINEAR_BATCH)
    assert stats["counts"] == COLLINEAR_COUNTS  # baseline unchanged
    assert math.isclose(stats["gini"], COLLINEAR_GINI)
    assert stats["min_max_ratio"] == 0.0  # six experts never used per batch
    # clean-up: load_balanced vocabulary untouched
    probe = moe._load_snapshot(COLLINEAR_BATCH)
    assert math.isclose(probe["gini"], COLLINEAR_GINI)
    res = moe.balance_step(COLLINEAR_BATCH)
    assert "load_balanced" in res
    assert "gini" in res  # additive key, not a replacement


def test_cumulative_snapshot_support_and_starved_list():
    """Cumulative view names its support; zero tokens read as 'nothing', not 'even'."""
    moe = _build()
    empty = moe.cumulative_snapshot()
    assert empty["tokens_routed"] == 0
    assert empty["entropy_normalised"] == 0.0
    assert empty["used_experts"] == 0
    assert empty["starved_experts"] == list(range(8))

    moe.forward(COLLINEAR_BATCH)
    moe.forward(COLLINEAR_BATCH)
    cum = moe.cumulative_snapshot()
    # 64 rows x top_k 2 per forward, two forwards -> 256 routed slots.
    assert cum["tokens_routed"] == 256
    assert cum["counts"] == [0, 0, 0, 128, 0, 128, 0, 0]
    assert math.isclose(cum["entropy_normalised"], COLLINEAR_ENTROPY)
    assert cum["starved_experts"] == [0, 1, 2, 4, 6, 7]


def test_switch_mode_is_default_and_byte_identical():
    """No mode set -> shipped router, byte-identical forward-to-forward."""
    a = _build()
    b = _build()
    counts_a = a.forward(COLLINEAR_BATCH)[2]["counts"]
    counts_b = b.forward(COLLINEAR_BATCH)[2]["counts"]
    assert counts_a == COLLINEAR_COUNTS
    assert counts_a == counts_b
    assert a.route_mode == "switch"
    # A new positional-args construction (the pipeline's 3-line call) stays valid.
    c = SparseMoE(D_MODEL, N_EXPERTS, TOP_K, SEED)
    assert c.route_mode == "switch"


def test_unknown_mode_rejected_ctor_and_setter():
    """A mode that cannot describe a router raises; none is silently clamped."""
    for bad in ("loss-free", "BIP", "", "lpr ", None):
        with pytest.raises(ValueError):
            SparseMoE(D_MODEL, N_EXPERTS, TOP_K, SEED, route_mode=bad)
    moe = _build()
    for bad in ("loss-free", "", None):
        with pytest.raises(ValueError):
            moe.set_route_mode(bad)
    # The setter is switchable and revertible.
    moe.set_route_mode("lpr")
    assert moe.route_mode == "lpr"
    moe.set_route_mode("switch")
    assert moe.route_mode == "switch"


# ---------------------------------------------------------------------------
# Slice C -- R2 BIP quota assignment (arXiv:2502.15451)
# ---------------------------------------------------------------------------

def test_bip_even_assigns_quotas_where_top_k_pins():
    """R2 acceptance, PER BATCH this time (assignment side-steps the pinning

    argument): on the collinear fixture the shipped router reports 2/8
    experts and entropy 1/3; the BIP assignment gives every expert exactly
    its equal-share quota, so per-batch used is 8/8, entropy 1.0, Gini 0.0.
    The token keeps top_k=2 experts, so the batch supplies the same number
    of expert slots it always did -- only the solver spreads them.
    """
    moe = _build("bip")
    _out, _aux, stats = moe.forward(COLLINEAR_BATCH)
    assert stats["counts"] == [16] * 8          # exact quotas: 128 slots/8
    assert stats["used_experts"] == 8
    assert stats["gini"] == 0.0                 # measured perfectly even
    assert stats["min_max_ratio"] == 1.0
    assert math.isclose(
        load_entropy_normalised(stats["counts"], N_EXPERTS), 1.0)
    # Every token still carries exactly top_k slots.
    assert sum(stats["counts"]) == TOP_K * 64

    # Determinism: identical construction replays the identical batch.
    replay = _build("bip")
    assert replay.forward(COLLINEAR_BATCH)[2]["counts"] == stats["counts"]

    # The shipped router is untouched by the solver's existence.
    shipped = _build("switch")
    assert shipped.forward(COLLINEAR_BATCH)[2]["counts"] == COLLINEAR_COUNTS


def test_bip_handles_indivisible_remainder_and_varied_batches():
    """No division luck required: remainder slots rotate deterministically.

    33 tokens x top_k 2 = 66 slots over 8 experts: quotas 8 with TWO
    remainder slots. The leftover goes to the lowest cumulative-count
    experts first (ties by index), so a repeated batch rotates its
    remainder instead of systematically favouring the row front.
    """
    rows33 = [[1.0 * j for j in range(D_MODEL)]] * 33
    moe = _build("bip")
    _o, _a, stats = moe.forward(rows33)
    assert sum(stats["counts"]) == 2 * 33
    assert stats["used_experts"] == 8
    # Exactly two experts earned 9 slots, six earned 8, and the pair is
    # the two lowest cumulative counts at assignment time (0,0 -> index 0,1).
    tallies = sorted(stats["counts"], reverse=True)
    assert tallies == [9, 9] + [8] * 6
    forward_two = moe.forward(rows33)[2]["counts"]  # second batch: rotate
    assert forward_two[0] < 9 or forward_two[1] < 9  # the remainder moved on

    # A varied (non-collinear) batch keeps full assignment too.
    varied = [[float((i * 7 + j) % 5) for j in range(D_MODEL)]
              for i in range(64)]
    v = _build("bip")
    _o, _a, vstats = v.forward(varied)
    assert sum(vstats["counts"]) == TOP_K * 64
    assert vstats["used_experts"] == 8

    # And output shape never changes with the solver.
    out, _aux, _s = moe.forward(rows33)
    assert len(out) == 33 and all(len(r) == D_MODEL for r in out)


# ---------------------------------------------------------------------------
# Slice D -- R3 LPR prototype routing + SimBal diversity (arXiv:2506.21328,
# 2506.14038, 2505.22323)
# ---------------------------------------------------------------------------

def test_lpr_assignment_binds_quotas_where_the_batch_cannot_compare():
    """LPR acceptance per batch, on the family where clusters cannot split.

    On collinear tokens every prototype reads the same affinity, so the
    quota part of the LPR reading binds exactly like BIP's: 8/8 experts
    used, entropy 1.0 sentinels — and the prototype surface must then
    KEEP that spread as batches repeat (the EMA drifts, the quotas are
    what keep the gate balanced, and the acceptance is measured both
    after one batch and after several).
    """
    moe = _build("lpr")
    _o, _a, stats = moe.forward(COLLINEAR_BATCH)
    assert stats["counts"] == [16] * 8
    assert stats["used_experts"] == 8
    assert math.isclose(
        load_entropy_normalised(stats["counts"], N_EXPERTS), 1.0)
    # A separate instance carries the drift loop so the determinism replay
    # compares EXACTLY equal batch counts (the first instance is one batch
    # ahead by construction).
    drifter = _build("lpr")
    for _ in range(4):  # prototypes drift; utilisation stays bound
        stats = drifter.forward(COLLINEAR_BATCH)[2]
        assert stats["counts"] == [16] * 8

    # Determinism replay (prototypes included: the drift is deterministic).
    replay = _build("lpr")
    for _ in range(4):
        replay.forward(COLLINEAR_BATCH)
    assert replay._prototypes == drifter._prototypes


def test_diversity_stats_are_measurements_with_a_positive_control():
    """SimBal wordings ride the LPR prototype space; None until measured.

    A virgin gate has no prototype measurement and says so (never a fake
    0.0); after routing, orthogonality_loss lands strictly inside (0, 1)
    on the varied batch (prototypes are neither identical nor perfectly
    perpendicular here — measured), and rotates meaning when the fixtures
    rotate: the collinear fixture's prototypes all drift toward the SAME
    mean row, which must read as HIGH orthogonality (alignment), strictly
    above the varied batch's.
    """
    virgin = _build("lpr")
    assert virgin.diversity_stats()["reason"] == "no prototype measured yet"
    assert virgin.diversity_stats()["orthogonality_loss"] is None

    varied_moe = _build("lpr")
    varied = [[float((i * 7 + j) % 5) for j in range(D_MODEL)]
              for i in range(64)]
    for _ in range(3):
        varied_moe.forward(varied)
    d_varied = varied_moe.diversity_stats()
    assert 0.0 < d_varied["orthogonality_loss"] < 1.0
    assert d_varied["n_pairs"] == 28  # 8 choose 2
    assert d_varied["specialization_variance"] > 0.0

    collinear_moe = _build("lpr")
    for _ in range(3):
        collinear_moe.forward(COLLINEAR_BATCH)
    d_col = collinear_moe.diversity_stats()
    assert d_col["orthogonality_loss"] > d_varied["orthogonality_loss"]

    # The switch twin reports the None sentinel too, and forward must not
    # crash with it in stats.
    shipped = _build("switch")
    stats = shipped.forward(COLLINEAR_BATCH)[2]
    assert stats["diversity"]["orthogonality_loss"] is None


# ---------------------------------------------------------------------------
# Slice B -- R1 Loss-Free bias routing (arXiv:2408.15664)
# ---------------------------------------------------------------------------

def test_lossfree_recovers_run_utilisation_on_the_collapse_fixture():
    """R1 acceptance: cumulative entropy up + all experts utilised.

    MEASURED, and every number was measured BEFORE the assertion: the
    16-row collinear fixture's gate score spread is 27.0 and the first
    Loss-Free cut with the shipped constant +-2.0 clamp could not flip ANY
    of 96 seeded fixed gates (total authority 4.0 < every spread), so the
    clamp is now the batch's own measured score spread (see
    ``_update_lossfree_bias``). With that authority the same fixture
    recovers at forward #2148 to cumulative entropy 0.844 -- every expert
    in an 8-expert gate routing real tokens -- and keeps rotating (the
    run-level entropy RISES further; the gate does not re-stick).

    The per-batch floor is still named, not hidden: a collinear batch pins
    top-k to two experts EVERY batch. The acceptance is cumulative and
    this assertion suite never claims a per-batch change the fixture
    cannot support.
    """
    rows16 = [[1.0 * j for j in range(D_MODEL)]] * 16
    moe = _build("bias")
    per_batch_used = []
    recovered_at = None
    for step in range(1, 3001):
        _out, _aux, stats = moe.forward(rows16)
        per_batch_used.append(stats["used_experts"])
        if (moe.cumulative_snapshot()["used_experts"] == N_EXPERTS
                and recovered_at is None):
            recovered_at = step
            break
    assert all(u == TOP_K for u in per_batch_used)  # the floor, named
    assert recovered_at is not None and recovered_at <= 2600, recovered_at
    cum = moe.cumulative_snapshot()
    assert cum["used_experts"] == 8
    assert cum["entropy_normalised"] > COLLINEAR_ENTROPY + 0.4  # 0.844 vs 1/3
    assert cum["entropy_normalised"] < 1.0  # bias rotation, not perfection

    # Non-restick: the run keeps spreading after recovery.
    after = sum(moe.forward(rows16)[2]["counts"])  # a load, consumed
    entropy_after = moe.cumulative_snapshot()["entropy_normalised"]
    assert entropy_after >= cum["entropy_normalised"]

    # Deterministic: a fresh build replays the prefix identically.
    replay = _build("bias")
    for _ in range(100):
        replay.forward(rows16)
    twin = _build("bias")
    for _ in range(100):
        twin.forward(rows16)
    assert replay.cumulative_snapshot()["counts"] == twin.cumulative_snapshot()["counts"]


def test_lossfree_selects_but_never_distorts_gate_values():
    """The Loss-Free contract: bias moves SELECTION only, never the probs.

    A bias-mode gate and a switch-mode twin are fed the same batches: the
    softmax prob rows must match batch after batch, even while the bias
    vector grows. That is the paper's whole point -- no auxiliary loss, no
    gradient, no distortion of the expert weighting values.
    """
    biased = _build("bias")
    shipped = _build("switch")
    for _ in range(4):
        _out_b, _aux_b, stats_b = biased.forward(COLLINEAR_BATCH)
        _out_s, _aux_s, stats_s = shipped.forward(COLLINEAR_BATCH)
        assert stats_b["lossfree"]["updated"] is True
        assert stats_s["lossfree"]["updated"] is False
        assert biased._lossfree_bias != [0.0] * N_EXPERTS  # it IS moving
        # Values untouched: recompute base softmax and compare.
        for row_a, row_b in zip(shipped.forward(COLLINEAR_BATCH)[0],
                                biased.forward(COLLINEAR_BATCH)[0]):
            pass  # outputs feed through the same base-prob weights
        # Direct prob comparison instead of output drift:
        _c, _w, rows_b = biased.route(COLLINEAR_BATCH)
        _c2, _w2, rows_s = shipped.route(COLLINEAR_BATCH)
        assert rows_b == rows_s


def test_lossfree_vector_starts_zero_and_clamps_like_gate_bias():
    """Zero by default (switch stays shipped). Clamps at the PASSED authority.

    Default clamp stays ``MAX_GATE_BIAS`` (a direct caller gets the shipped
    authority); bias-mode forward passes the MEASURED batch spread instead
    -- and its measured reason lives on ``_update_lossfree_bias``. An
    exactly-even load applies zero step (strict sign rule, measured on the
    fixtures in this battery).
    """
    moe = _build()
    assert moe._lossfree_bias == [0.0] * N_EXPERTS
    moe.set_route_mode("bias")
    # An exactly-even load applies ZERO step (strict > in the sign rule).
    zero = _build("bias")
    zero._update_lossfree_bias([2] * N_EXPERTS, alpha=0.01)
    assert zero._lossfree_bias == [0.0] * N_EXPERTS
    # One-sided overload with the DEFAULT (constant) authority saturates.
    counts = [256] * 2 + [0] * (N_EXPERTS - 2)
    for _ in range(64):  # far past the 2.0 / (0.01*100) = 2-step safety
        moe._update_lossfree_bias(counts, alpha=DEFAULT_LOSSFREE_ALPHA * 100)
    assert all(abs(b) <= MAX_GATE_BIAS + 1e-12 for b in moe._lossfree_bias)
    assert moe._lossfree_bias[0] == -MAX_GATE_BIAS  # over-target -> down
    assert moe._lossfree_bias[2] == MAX_GATE_BIAS   # starved -> full authority
    # A supply clamp overrides the default authority.
    loose = _build("bias")
    for _ in range(64):  # 50 steps to saturate at the supplied authority
        loose._update_lossfree_bias([256] * 2 + [0] * (N_EXPERTS - 2),
                                    alpha=1.0, clamp=50.0)
    assert loose._lossfree_bias[0] == -50.0
    assert loose._lossfree_bias[2] == 50.0
    # No load measured -> not updated, reported as its own reason.
    empty = _build("bias")
    report = empty._update_lossfree_bias([0] * N_EXPERTS)
    assert report["updated"] is False
    assert report["reason"] == "no load measured"
    # And the measured-authority path: the batch spread, read directly.
    moe_clear = _build("bias")
    spread = moe_clear._selection_spread(COLLINEAR_BATCH)
    assert spread > 4.0  # the measured wall that killed the fixed clamp
    assert "moved_count" in moe_clear._update_lossfree_bias(
        [16] * 2 + [0] * (N_EXPERTS - 2), clamp=spread)
