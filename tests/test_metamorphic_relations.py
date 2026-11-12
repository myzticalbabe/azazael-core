# coding: utf-8
"""PHASE F — metamorphic relations, for the places where no oracle exists.

Engram #784, phase F. A metamorphic test does not need to know the right answer:
it states a relation between two runs and checks the relation holds. That makes
it the only technique that applies where a value cannot be predicted -- exactly
the situation for a routing distribution, an entropy measurement or a loss that
depends on a seeded gate matrix.

Every relation below is stated explicitly, and the result is recorded as
HELD or VIOLATED by the assertion itself rather than by prose. Where a relation
is deliberately NOT claimed, that is said in the docstring, because an
over-stated relation is how this technique turns into a second copy of the
implementation.
"""

import math
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.cutting_edge_model import (  # noqa: E402
    SparseMoE,
    _softmax,
    load_entropy_normalised,
)

N_EXPERTS = 8
TOP_K = 2
D_MODEL = 8

COUNT_VECTORS = st.lists(st.integers(0, 5_000), min_size=2, max_size=12)


# ── R1: the order of the counts carries no information ──────────────────────

@given(counts=COUNT_VECTORS, seed=st.integers(0, 10_000))
@settings(max_examples=80, deadline=None)
def test_r1_entropy_is_invariant_under_a_permutation_of_experts(counts, seed):
    """RELATION: permuting which expert holds which count cannot change how flat
    the load is. Expert index is a label, not a magnitude.

    Stated because it is a real hazard: an implementation that compares adjacent
    counts would pass every sorted input and fail this.
    """
    shuffled = list(counts)
    # A hand-rolled deterministic shuffle keeps the relation reproducible and
    # avoids consuming a hypothesis randomness stream inside the test.
    for i in range(len(shuffled) - 1, 0, -1):
        j = (seed + i * 7919) % (i + 1)
        shuffled[i], shuffled[j] = shuffled[j], shuffled[i]

    assert load_entropy_normalised(counts, len(counts)) == pytest.approx(
        load_entropy_normalised(shuffled, len(shuffled)), abs=1e-12)


# ── R2: uniform scaling of the counts is a no-op ────────────────────────────

@given(counts=COUNT_VECTORS, factor=st.integers(1, 50))
@settings(max_examples=80, deadline=None)
def test_r2_entropy_is_invariant_under_scaling_every_count(counts, factor):
    """RELATION: entropy is a function of the distribution, so multiplying every
    count by the same factor must change nothing. A count-based implementation
    that forgot to normalise would drift with the factor and fail here."""
    scaled = [c * factor for c in counts]
    assert load_entropy_normalised(counts, len(counts)) == pytest.approx(
        load_entropy_normalised(scaled, len(scaled)), abs=1e-12)


# ── R3: a zero-count expert adds nothing ───────────────────────────────────

@given(counts=COUNT_VECTORS)
@settings(max_examples=80, deadline=None)
def test_r3_appending_a_zero_count_expert_leaves_the_value_alone(counts):
    """RELATION: an expert that took no token contributes no term to the sum, and
    the number of experts is passed explicitly, so appending a zero must not move
    the value. This pins the ``n_experts`` argument as the scale rather than
    ``len(counts)``."""
    n_experts = len(counts)
    assert load_entropy_normalised(list(counts) + [0], n_experts) == pytest.approx(
        load_entropy_normalised(counts, n_experts), abs=1e-12)


# ── R4/R5: routing is per token, so permuting the batch permutes the outputs ──

def _tokens(n_rows):
    return [[float((i * 5 + j) % 11) for j in range(D_MODEL)] for i in range(n_rows)]


def test_r4_forward_is_token_order_equivariant():
    """RELATION: each token is routed independently, so reordering the batch must
    reorder the output rows the same way and change nothing else.

    NOT claimed: layer-norm-like coupling between tokens. There is none here, and
    if one were added this relation would fail -- which is the point.
    """
    moe = SparseMoE(d_model=D_MODEL, n_experts=N_EXPERTS, top_k=TOP_K, seed=17)
    rows = _tokens(9)
    order = [8, 0, 3, 6, 1, 7, 2, 5, 4]

    straight, straight_aux, straight_stats = moe.forward(rows)
    shuffled, shuffled_aux, shuffled_stats = moe.forward([rows[i] for i in order])

    assert [straight[i] for i in order] == shuffled
    assert straight_aux == pytest.approx(shuffled_aux, abs=1e-12)
    assert straight_stats["counts"] == shuffled_stats["counts"]


def test_r5_a_repeated_token_contributes_its_mass_twice():
    """RELATION: doubling a token in the batch must double its per-expert count
    and leave the aux loss unchanged, because f and p are both means over tokens
    and duplicating a token scales f and p by the same factor in the same term.

    Checked on the counts, which is the half that is exactly predictable.
    """
    moe = SparseMoE(d_model=D_MODEL, n_experts=N_EXPERTS, top_k=TOP_K, seed=19)
    single = _tokens(5)
    doubled = single + single

    _out, aux_one, stats_one = moe.forward(single)
    _out2, aux_two, stats_two = moe.forward(doubled)

    assert stats_two["counts"] == [2 * c for c in stats_one["counts"]]
    assert aux_two == pytest.approx(aux_one, abs=1e-9)


# ── R6: softmax is permutation-equivariant ─────────────────────────────────

@given(xs=st.lists(st.floats(-1e2, 1e2, allow_nan=False, allow_infinity=False),
                   min_size=2, max_size=10),
       seed=st.integers(0, 10_000))
@settings(max_examples=100, deadline=None)
def test_r6_softmax_permutation_equivariance(xs, seed):
    """RELATION: permuting the input permutes the output identically. It is not
    INVARIANCE -- softmax is a function of position -- which is why the expected
    value here is the permuted output, not the original."""
    order = list(range(len(xs)))
    for i in range(len(order) - 1, 0, -1):
        j = (seed + i * 104_729) % (i + 1)
        order[i], order[j] = order[j], order[i]

    base = _softmax(xs)
    permuted = _softmax([xs[i] for i in order])

    assert permuted == pytest.approx([base[i] for i in order], rel=1e-9, abs=1e-12)


# ── R7: a zero-gamma nudge is the identity ─────────────────────────────────

def test_r7_balance_step_with_zero_gamma_is_the_identity_on_the_gate():
    """RELATION: ``gate_bias`` moves by ``-gamma`` or ``+gamma``, so gamma 0 must
    leave it bit-identical. This is the degenerate case a ``max(eps, gamma)``
    style guard would silently break, and it is invisible to any test that only
    checks the bias stays inside the clamp."""
    moe = SparseMoE(d_model=D_MODEL, n_experts=N_EXPERTS, top_k=TOP_K, seed=23)
    before = list(moe.gate_bias)
    result = moe.balance_step(_tokens(6), gamma=0.0)

    assert moe.gate_bias == before
    assert result["gate_bias"] == before


# ── R8: the whole report is idempotent (a refresh, not a mutation) ──────────

def test_r8_repeated_route_and_load_measurement_are_idempotent():
    """RELATION: ``route`` and ``_load_snapshot`` are pure measurements, so
    calling them repeatedly must not drift. This is the refresh-idempotence
    relation: a measurement that changes its own subject on second read would
    make every monitor in the tree unreliable."""
    moe = SparseMoE(d_model=D_MODEL, n_experts=N_EXPERTS, top_k=TOP_K, seed=29)
    rows = _tokens(7)

    first_route = moe.route(rows)
    first_snapshot = moe._load_snapshot(rows)
    for _ in range(3):
        assert moe.route(rows) == first_route
        assert moe._load_snapshot(rows) == first_snapshot


# ── R9: the documented reachable floor of a top-k router ───────────────────

def test_r9_top_k_routers_cannot_beat_their_own_entropy_floor():
    """RELATION: the model's own docstring says the reachable FLOOR for a top-k
    router is ``log(top_k)/log(n_experts)``, because a top-k router can touch at
    most ``top_k`` experts per token.

    Stated as a floor, and checked in the direction that can be checked: an
    entropy produced by a real top-k over 8 experts with k=2 can never exceed 1.0
    (uniform needs all 8 experts), so 1.0 is an unattainable ceiling for this
    geometry rather than a target.
    """
    moe = SparseMoE(d_model=D_MODEL, n_experts=N_EXPERTS, top_k=TOP_K, seed=31)
    rows = [[float((i % 8) == j) for j in range(D_MODEL)] for i in range(64)]
    snapshot = moe._load_snapshot(rows)

    floor = math.log(TOP_K) / math.log(N_EXPERTS)
    assert 0.0 <= snapshot["entropy_normalised"] <= 1.0 + 1e-12
    if snapshot["used_experts"] > 1:
        assert snapshot["entropy_normalised"] >= floor - 1e-9, \
            "a top-k router fell below the floor its own docstring names"
