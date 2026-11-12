# coding: utf-8
"""PHASE P — property-based tests (Hypothesis) over PURE functions.

Engram #784, phase P. Targets were chosen because they are pure *by
construction*, which is what makes a property testable at all: the gate matrix
is drawn ONCE at construction from a component seed, so there is no clock, no
store and no RNG live at call time. That is what makes ``route(x) == route(x)``
a real invariant rather than a hope, and it is why the same technique applied to
a store-backed function would prove nothing.

The pre-registered baseline for this phase said "PBT files 0". Measured on the
live tree before this file was written: 1 file used ``@given`` and 2 imported
hypothesis. The baseline was wrong and is corrected in the lane report rather
than quietly satisfied by writing the first file.

Each property below is chosen to FAIL a plausible defect rather than to
restate the implementation:

  * a gate that does not normalise            -> weights must sum to 1
  * a router that repeats an expert in top-k  -> indices must be distinct
  * a load snapshot reporting a stale count   -> counts must sum to top_k * n
  * an aux loss detached from its own routing -> aux is RE-DERIVED here from
    ``route()`` and compared, the Switch-Transformer form being the oracle
  * a softmax that overflows on large logits  -> shift invariance

Conventions mirror ``tests/test_cutting_edge_production_fixes.py``: tests insert
``src/`` themselves (``tests/conftest.py`` does not), and the imports are at
module scope so no ``import_inside_function`` finding is created.
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
    MAX_GATE_BIAS,
    SparseMoE,
    _softmax,
    load_entropy_normalised,
)
from CORE.WORMS.expansion.exmachina_operators import SparseMoE as NexusMoE  # noqa: E402

#: Fixed expert geometry. 8 experts with top-k 2 is the production shape named in
#: the model's own defect notes, so the algebra is exercised where it has failed.
N_EXPERTS = 8
TOP_K = 2
D_MODEL = 8

#: Integer coordinates only: a float token list is already covered, and keeping
#: the geometry small keeps 100 examples cheap enough to run on both interpreters.
TOKENS = st.lists(
    st.lists(st.floats(-1e2, 1e2, allow_nan=False, allow_infinity=False),
             min_size=D_MODEL, max_size=D_MODEL),
    min_size=1, max_size=12,
)


def _moe(seed=11, n_experts=N_EXPERTS, top_k=TOP_K, d_model=D_MODEL):
    return SparseMoE(d_model=d_model, n_experts=n_experts, top_k=top_k, seed=seed)


# ── routing is a probability distribution, per token ────────────────────────

@given(tokens=TOKENS)
@settings(max_examples=60, deadline=None)
def test_route_returns_a_distribution_without_repeating_an_expert(tokens):
    """Renormalised weights and full probabilities are both distributions.

    The distinctness assertion is the one that catches a real router bug: a
    top-k that returns the same expert twice silently halves routed capacity and
    no shape check would notice.
    """
    moe = _moe()
    chosen, weights, prob_rows = moe.route(tokens)

    assert len(chosen) == len(weights) == len(prob_rows) == len(tokens)
    for t in range(len(tokens)):
        assert len(chosen[t]) == min(TOP_K, N_EXPERTS)
        assert len(set(chosen[t])) == len(chosen[t]), "expert repeated inside one top-k"
        assert all(0 <= e < N_EXPERTS for e in chosen[t])
        assert all(w >= 0.0 for w in weights[t])
        assert all(p >= 0.0 for p in prob_rows[t])
        assert sum(weights[t]) == pytest.approx(1.0, abs=1e-9)
        assert sum(prob_rows[t]) == pytest.approx(1.0, abs=1e-9)


@given(tokens=TOKENS)
@settings(max_examples=40, deadline=None)
def test_route_is_deterministic_so_two_runs_stay_comparable(tokens):
    """A router that re-randomises per call makes every ablation unattributable."""
    moe = _moe()
    first = moe.route(tokens)
    second = moe.route([list(row) for row in tokens])
    assert first == second


# ── forward: the counts partition the top-k slots, and aux matches its oracle ──

def test_forward_of_an_empty_batch_is_an_explicit_zero_not_a_silent_one():
    out, aux, stats = _moe().forward([])
    assert out == []
    assert aux == 0.0
    assert stats == {"tokens": 0, "aux_loss": 0.0, "used_experts": 0}


@given(tokens=TOKENS)
@settings(max_examples=60, deadline=None)
def test_forward_counts_partition_the_top_k_slots(tokens):
    out, _aux, stats = _moe().forward(tokens)
    assert len(out) == len(tokens)
    assert all(len(row) == D_MODEL for row in out)
    assert sum(stats["counts"]) == min(TOP_K, N_EXPERTS) * len(tokens)
    assert stats["used_experts"] == sum(1 for c in stats["counts"] if c > 0)


@given(tokens=TOKENS)
@settings(max_examples=60, deadline=None)
def test_aux_loss_equals_the_switch_transformer_formula_rederived(tokens):
    """The aux loss must describe the routing that actually happened.

    ``forward`` returns the number; this test re-derives ``f`` and ``p`` from a
    fresh ``route()`` call and recomputes ``n * sum(f_i * p_i)``. A drift between
    the reported loss and the routing it names is invisible to a bounds check
    alone, which is why the equality is asserted and not just ``0 <= aux <= n``.
    """
    moe = _moe()
    _out, aux, stats = moe.forward(tokens)
    chosen, _weights, prob_rows = moe.route(tokens)

    n_tokens = len(tokens)
    counts = [0] * N_EXPERTS
    for top in chosen:
        for expert_id in top:
            counts[expert_id] += 1

    f = [c / float(TOP_K * n_tokens) for c in counts]
    p = [sum(row[i] for row in prob_rows) / n_tokens for i in range(N_EXPERTS)]
    expected = N_EXPERTS * sum(fi * pi for fi, pi in zip(f, p))

    assert stats["counts"] == counts
    assert aux == pytest.approx(expected, abs=1e-9)
    assert 0.0 <= aux <= float(N_EXPERTS + 1e-9)


def test_param_count_accounts_for_the_gate_bias_and_every_expert():
    """A parameter count that forgets a term under-reports what training owns."""
    moe = _moe()
    expected = (len(moe.gate) * len(moe.gate[0]) + len(moe.gate_bias)
                + sum(e.param_count() for e in moe.experts))
    assert moe.param_count() == expected


# ── the load snapshot is self-consistent with its own counts ────────────────

@given(tokens=TOKENS)
@settings(max_examples=60, deadline=None)
def test_load_snapshot_agrees_with_the_counts_it_reports(tokens):
    moe = _moe()
    snap = moe._load_snapshot(tokens)
    counts = snap["counts"]

    assert sum(counts) == min(TOP_K, N_EXPERTS) * len(tokens)
    assert snap["starved_experts"] == [i for i, c in enumerate(counts) if c == 0]
    assert snap["used_experts"] == sum(1 for c in counts if c > 0)
    assert snap["target"] == pytest.approx(len(tokens) * min(TOP_K, N_EXPERTS) / float(N_EXPERTS))
    assert snap["max_skew"] == pytest.approx(max(counts) / snap["target"])
    assert 0.0 <= snap["entropy_normalised"] <= 1.0 + 1e-12


@given(tokens=TOKENS)
@settings(max_examples=60, deadline=None)
def test_load_is_balanced_is_exactly_its_documented_predicate(tokens):
    """``load_balanced`` claims "no starved expert and no expert above 1.25x".

    Asserting the equivalence rather than the value pins the DOCUMENTED meaning:
    a future change that loosens the threshold to make a monitor go green would
    have to change this test too, in the open.
    """
    moe = _moe()
    snap = moe._load_snapshot(tokens)
    expected = (bool(snap["counts"]) and not snap["starved_experts"]
                and snap["max_skew"] <= 1.25)
    assert moe._load_is_balanced(snap) == expected


# ── balance_step: the gate's only learning path ────────────────────────────

def test_balance_step_on_an_empty_batch_refuses_instead_of_nudging():
    moe = _moe()
    before = list(moe.gate_bias)
    result = moe.balance_step([])
    assert result["balanced"] is False
    assert result["reason"] == "no tokens to route"
    assert moe.gate_bias == before, "a refused nudge must not move the gate"


@given(tokens=TOKENS, gamma=st.floats(1e-4, 0.5, allow_nan=False, allow_infinity=False))
@settings(max_examples=60, deadline=None)
def test_balance_step_is_reproducible_and_clamps_every_bias(tokens, gamma):
    """Determinism is the whole basis of an ablation, and the clamp is the only
    thing stopping a long one-sided run from driving a logit to infinity."""
    left = _moe(seed=5)
    right = _moe(seed=5)
    a = left.balance_step(tokens, gamma)
    b = right.balance_step(tokens, gamma)

    assert a == b, "the nudge must be reproducible for a run to be attributable"
    assert a["balanced"] is True
    assert all(abs(bias) <= MAX_GATE_BIAS for bias in a["gate_bias"])
    assert a["load_balanced"] == (not a["starved_experts"]
                                  and max(a["counts"]) <= a["target"] * 1.25)


# ── load_entropy_normalised: the measurable form of "collapsed" ─────────────

@given(counts=st.lists(st.integers(0, 10_000), min_size=2, max_size=16))
@settings(max_examples=100, deadline=None)
def test_load_entropy_is_bounded_for_any_count_vector(counts):
    value = load_entropy_normalised(counts, len(counts))
    assert 0.0 <= value <= 1.0 + 1e-12
    if sum(counts) <= 0:
        assert value == 0.0
    elif sum(1 for c in counts if c > 0) == 1:
        assert value == 0.0, "one expert taking every token is the collapse case"


def test_load_entropy_pins_all_three_of_its_documented_edges():
    assert load_entropy_normalised([7, 7, 7, 7], 4) == pytest.approx(1.0)
    assert load_entropy_normalised([0, 28, 0, 0], 4) == 0.0
    assert load_entropy_normalised([], 4) == 0.0
    assert load_entropy_normalised([1, 1], 1) == 0.0, "n_experts < 2 has no scale"


# ── softmax: normalised AND shift invariant ─────────────────────────────────

@given(xs=st.lists(st.floats(-1e2, 1e2, allow_nan=False, allow_infinity=False),
                   min_size=2, max_size=12),
       shift=st.floats(-50, 50, allow_nan=False, allow_infinity=False))
@settings(max_examples=100, deadline=None)
def test_softmax_is_normalised_and_invariant_under_a_constant_shift(xs, shift):
    base = _softmax(xs)
    shifted = _softmax([x + shift for x in xs])

    assert sum(base) == pytest.approx(1.0, abs=1e-12)
    assert all(p >= 0.0 for p in base)
    assert base == pytest.approx(shifted, rel=1e-9, abs=1e-12)


# ── NEXUS (exmachina_operators): clamps, distributions, entropy bounds ─────


@st.composite
def nexus_fit_inputs(draw, min_rows=2, max_rows=8, dims=2):
    """Equal-length (X, y) drawn together, so ``fit`` is never handed a short y.

    Drawing them as two independent lists would make the shorter one the only
    legal input, which is a fixture accident rather than a property.
    """
    n_rows = draw(st.integers(min_rows, max_rows))
    X = [draw(st.lists(st.floats(-1e2, 1e2, allow_nan=False, allow_infinity=False),
                       min_size=dims, max_size=dims))
         for _ in range(n_rows)]
    y = [draw(st.floats(-1e2, 1e2, allow_nan=False, allow_infinity=False))
         for _ in range(n_rows)]
    return X, y


@given(n_experts=st.integers(-3, 12), top_k=st.integers(-4, 20),
       temperature=st.floats(-5.0, 5.0, allow_nan=False, allow_infinity=False))
@settings(max_examples=60, deadline=None)
def test_nexus_constructor_clamps_to_its_documented_bounds(n_experts, top_k, temperature):
    model = NexusMoE(n_experts=n_experts, top_k=top_k, temperature=temperature)
    assert model.n_experts == max(1, int(n_experts))
    assert 1 <= model.top_k <= model.n_experts
    assert model.temperature >= 1e-3


@given(inputs=nexus_fit_inputs())
@settings(max_examples=60, deadline=None)
def test_nexus_gate_is_a_distribution_before_and_after_fitting(inputs):
    X, y = inputs
    model = NexusMoE(n_experts=3, top_k=2, seed=3)

    before = model.gate([0.0, 0.0])
    assert before == pytest.approx([1.0 / 3.0] * 3), "unfitted gate is uniform"
    assert sum(before) == pytest.approx(1.0)

    model.fit(X, y)
    for row in X:
        probs = model.gate(list(map(float, row)))
        assert len(probs) == 3
        assert all(p >= 0.0 for p in probs)
        assert sum(probs) == pytest.approx(1.0, abs=1e-9)


@given(inputs=nexus_fit_inputs())
@settings(max_examples=60, deadline=None)
def test_nexus_report_usage_partitions_the_batch_and_entropy_is_bounded(inputs):
    X, y = inputs
    model = NexusMoE(n_experts=3, top_k=2, seed=3).fit(X, y)
    report = model.report(X)

    assert sum(report["usage"]) == len(X), "every row is assigned to exactly one expert"
    assert len(report["usage"]) == 3
    assert 0.0 <= report["usage_entropy"] <= report["max_entropy"] + 1e-12
    # ``report`` rounds both entropies to 4 decimals, so the ceiling is the
    # ROUNDED constant. Comparing against the unrounded log2 was this test's own
    # bug: it asserted the contract the source does not offer (found on the
    # py315t run, fixed here rather than by loosening the source).
    assert report["max_entropy"] == pytest.approx(round(math.log2(3), 4))
    assert 0 <= report["trained"] <= 3


@given(inputs=nexus_fit_inputs())
@settings(max_examples=60, deadline=None)
def test_nexus_predict_returns_one_finite_value_per_row(inputs):
    X, y = inputs
    model = NexusMoE(n_experts=3, top_k=2, seed=3).fit(X, y)
    preds = model.predict(X)

    assert len(preds) == len(X)
    assert all(math.isfinite(v) for v in preds)


def test_nexus_fit_of_an_empty_batch_is_a_no_op_rather_than_a_crash():
    model = NexusMoE(n_experts=3, top_k=2, seed=3)
    assert model.fit([], []) is model
    assert model.centroids == []
    assert model.experts == [None, None, None]


def test_nexus_is_reproducible_for_a_seed():
    """Two models built from one seed must agree, or a comparison measures noise."""
    X = [[0.0, 0.0], [0.1, 0.0], [0.0, 0.1], [5.0, 5.0], [5.1, 5.0], [5.0, 5.1]]
    y = [0.0, 0.1, 0.2, 1.0, 1.1, 1.2]

    left = NexusMoE(n_experts=2, top_k=1, seed=7).fit(X, y)
    right = NexusMoE(n_experts=2, top_k=1, seed=7).fit(X, y)

    assert left.centroids == right.centroids
    assert left.predict(X) == right.predict(X)
    assert left.report(X) == right.report(X)


# ── mutation-pilot closeouts (phase M survivors, one named test each) ───────
# The pilot on this class scored 14/16 (0.875). A surviving mutant is a test gap,
# so each survivor gets the test that kills it rather than a note that it existed.


def test_closes_mutant_413_the_expert_hidden_width_is_twice_d_model():
    """Survivor ``arith-flip Mult @ 413:21`` — ``2 * self.d_model`` -> ``2 / d_model``.

    Nothing asserted the expert GEOMETRY, so an expert built with a hidden width
    of 1 passed every other test in this file. ``param_count`` cannot catch it: it
    is computed FROM the shapes, so it agrees with whatever width was built.
    """
    for d_model in (1, 4, 8, 16):
        moe = SparseMoE(d_model=d_model, n_experts=3, top_k=1, seed=11)
        expected_gap = max(1, 2 * d_model)
        assert len(moe.gate[0]) == d_model, "the gate projects onto d_model, not the gap"
        for expert in moe.experts:
            assert len(expert.b1) == expected_gap, "expert hidden width is not max(1, 2 * d_model)"
            assert len(expert.w1) == expected_gap
            assert len(expert.w1[0]) == d_model


def test_closes_mutant_561_the_nudge_moves_the_gate_toward_balance():
    """Survivor ``compare-flip Gt @ 561:29`` — ``counts[i] > target`` -> ``<``.

    Nothing measured the SIGN of the learning signal. ``load_balanced`` in the
    returned dict is derived from the PRE-nudge snapshot, so an inverted nudge
    reported identical numbers to a corrective one -- the direction was simply
    unobserved. Inverted, this is worse than no balancing: a collapsed gate is
    pushed further into collapse every step. The relation tested is therefore
    one-sided and measured AFTER the nudges, on a fixture that starts collapsed.
    """
    moe = SparseMoE(d_model=8, n_experts=8, top_k=2, seed=13)
    rows = [[float((i % 8) == j) for j in range(8)] for i in range(32)]

    # Collapse on purpose: expert 0 at the clamp ceiling, every other at the floor.
    moe.gate_bias = [-MAX_GATE_BIAS] * 8
    moe.gate_bias[0] = MAX_GATE_BIAS
    before = moe._load_snapshot(rows)
    assert before["max_skew"] > 1.25, \
        "the fixture must START collapsed, or the direction claim is vacuous"

    for _ in range(40):
        moe.balance_step(rows, gamma=0.5)
    after = moe._load_snapshot(rows)

    assert after["max_skew"] < before["max_skew"], \
        "40 nudges moved the load AWAY from balance -- the signal is inverted"
    assert after["entropy_normalised"] >= before["entropy_normalised"]
