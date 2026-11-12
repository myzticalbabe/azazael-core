# coding: utf-8
"""Engram #524: the bounded remediation LOOP around the SparseMoE gate.

#521 made the routing collapse VISIBLE -- a live ClusterWORMZ batch used 2 of 8
experts with ``entropy_normalised`` exactly 1/3, ``starved_experts`` 6 wide and
``max_skew`` 4.0 -- but a SINGLE ``balance_step`` nudge (gamma 0.01) did not fix
it, so the fix is a bounded loop: apply the nudge until the load is even, or
until a NAMED iteration cap, and report which of the two happened.

This file pins the part of that acceptance the sibling battery does not isolate:
the PREMISE. ``tests/test_cutting_edge_production_fixes.py`` already tests the
loop's converged branch, its ``top_k_pinned`` branch and its empty batch; what is
not pinned anywhere is the claim that ONE NUDGE IS NOT REMEDIATION -- that the
same fixture which the cap-1 loop cannot balance is balanced by the default loop.
Without that pair, a ``converged`` flag that is a constant would satisfy either
run on its own, which is what makes this file falsifiable rather than a restating
of the other one.

Four measured facts are asserted and each has a negative direction:

  * a cap of 1 nudge leaves the fixture UNBALANCED, while the same fixture at
    ``DEFAULT_BALANCE_MAX_ITERS`` converges -- so ``converged`` cannot be a
    constant and the loop is not the nudge;
  * the cap is a REPORTED PARAMETER: at cap 1 the report says ``iterations ==
    max_iters == 1`` and ``cap_hit``, rather than inferring a bound;
  * a collinear batch sits ON its structural floor, ``log(top_k)/log(n)`` = 1/3
    for top-k 2 over 8 experts, and must NOT be reported as ``load_balanced``;
  * the ``top_k_pinned`` name fires EXACTLY when every probe token chose the same
    top-k set, and on a fixture it names no admissible bias can widen the used set
    -- the trigger is an equivalence and the name is a verified claim, not a
    symptom read off the load (see
    ``test_top_k_pinned_fires_exactly_when_one_choice_set_is_shared``).

Imports happen INSIDE each function on purpose, and that is load-bearing:
importing ``CORE.WORMS.expansion.cutting_edge_model`` registers a worm as a
decorator side effect, and ``tests/test_neural_pipeline_cutting_edge.py`` asserts
that the default path leaves the module unimported. A module-level import here
would fire that side effect at COLLECTION time and make that sibling assertion
unfalsifiable. The ``scanner:ignore`` marker on those lines is the sanctioned
fallback for a rule whose intent (top-level imports) a real constraint
contradicts -- the alternative is breaking a sibling test.

Hermetic: no network, no ClusterWORMZ batch, no memory store. Every fixture is
built from the model's own deterministic gate, and the entropy assertions use a
local recomputation so they cannot pass by reading the module's number back.
"""

import math
import random
import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _normalised_entropy(counts, n_experts: int) -> float:
    """Independent recomputation of the normalised load entropy.

    Deliberately NOT the module's ``load_entropy_normalised``: an assertion that
    compares a function to itself cannot fail, so the reported value is checked
    against a second implementation here.
    """
    total = sum(counts)
    if total <= 0 or n_experts < 2:
        return 0.0
    probs = [c / total for c in counts if c > 0]
    return -sum(p * math.log(p) for p in probs) / math.log(n_experts)


def _floor(top_k: int, n_experts: int) -> float:
    """The entropy no top-k router can go below: log(top_k)/log(n_experts).

    For the live 2-of-8 collapse that is exactly ``log 2 / log 8`` = 1/3, which
    is why an entropy of 1/3 must be read as the structural floor rather than as
    an unfinished loop.
    """
    return math.log(top_k) / math.log(n_experts)


def _skewed_gate_fixture():
    """Return (moe, rows): a gate-aligned batch with one expert at the clamp.

    The batch is the gate's OWN rows, repeated. With zero bias every expert wins
    its own row, so the load is even and the fixture's imbalance is caused ONLY
    by the injected skew -- which makes "it starts collapsed" a property of the
    construction rather than a hope about a random fixture. The skew is
    ``MAX_GATE_BIAS`` on expert 0: the gate matrix is scaled ``1/sqrt(d_model)``,
    so a self-match logit is ~1.0 against ~0.0 cross-matches, and a +2.0 bias
    therefore takes the top slot on every row.
    """
    from CORE.WORMS.expansion.cutting_edge_model import (  # scanner:ignore -- hoist would register worm at collection; sibling asserts module unimported
        MAX_GATE_BIAS, SparseMoE,
    )

    moe = SparseMoE(d_model=16, n_experts=8, top_k=2, seed=7)
    rows = [list(row) for row in moe.gate] * 8
    moe.gate_bias[0] = MAX_GATE_BIAS
    return moe, rows


def test_a_single_nudge_is_not_remediation_and_the_loop_converges():
    """The loop's reason to exist: same fixture, one nudge vs the full cap.

    Both halves run on an independently constructed copy of the SAME fixture, so
    the only difference between them is the cap. A ``converged`` flag that was a
    constant (always True or always False) fails one half, and a report whose
    numbers were hardcoded fails the before/after comparison, because the
    fixture's starting collapse is asserted rather than assumed.
    """
    from CORE.WORMS.expansion.cutting_edge_model import (  # scanner:ignore -- hoist would register worm at collection; sibling asserts module unimported
        DEFAULT_BALANCE_GAMMA, DEFAULT_BALANCE_MAX_ITERS, MAX_GATE_BIAS,
    )

    # (1) ONE nudge, the pre-#524 behaviour. `balance_step`'s own docstring is
    # honest that `balanced` only means "a nudge was applied"; the load is what
    # has to move, and one step at gamma does not move it.
    one_moe, one_rows = _skewed_gate_fixture()
    assert one_moe.gate_bias[0] == MAX_GATE_BIAS, one_moe.gate_bias
    single = one_moe.rebalance(one_rows, gamma=DEFAULT_BALANCE_GAMMA, max_iters=1)

    before = single["before"]
    # Guard: a fixture that stopped producing a collapse must FAIL, not pass
    # vacuously -- without this the remediation claim below proves nothing.
    assert before["max_skew"] > 1.25 or before["starved_experts"], before
    assert before["entropy_normalised"] < 1.0 - 1e-9, before

    assert single["iterations"] == 1, single
    assert single["cap_hit"] is True, single
    assert single["converged"] is False, (
        "ONE nudge must not be reported as remediation; got %s" % (single,))

    # (2) The SAME fixture at the default cap. This is the #524 loop.
    loop_moe, loop_rows = _skewed_gate_fixture()
    loop = loop_moe.rebalance(loop_rows)

    print("experts used: %d -> %d | entropy: %.6f -> %.6f | max_skew: %.4f -> %.4f"
          % (loop["before"]["used_experts"], loop["after"]["used_experts"],
             loop["before"]["entropy_normalised"],
             loop["after"]["entropy_normalised"],
             loop["before"]["max_skew"], loop["after"]["max_skew"]))

    assert loop["converged"] is True, loop
    assert loop["cap_hit"] is False, loop
    assert loop["max_iters"] == DEFAULT_BALANCE_MAX_ITERS, loop
    assert 1 < loop["iterations"] < DEFAULT_BALANCE_MAX_ITERS, (
        "the loop must need MORE than the one nudge it is replacing, and finish "
        "before the cap: %s" % (loop,))

    # The effect is a measurement on the same rows, in both directions.
    assert loop["after"]["entropy_normalised"] > loop["before"]["entropy_normalised"], (
        "rebalancing must raise load entropy: %.6f -> %.6f"
        % (loop["before"]["entropy_normalised"], loop["after"]["entropy_normalised"]))
    assert loop["after"]["used_experts"] >= loop["before"]["used_experts"], loop
    assert loop["after"]["max_skew"] < loop["before"]["max_skew"], loop
    assert loop["load_balanced"] is True, loop
    assert loop["after"]["starved_experts"] == [], loop["after"]

    # ...and the reported entropy is a real number, not the module's echo.
    assert abs(loop["after"]["entropy_normalised"]
               - _normalised_entropy(loop["after"]["counts"], loop_moe.n_experts)) < 1e-12, (
        loop["after"])
    assert loop["rows_used"] == loop["rows_available"] == len(loop_rows), loop

    # `balance_step`'s keys survive on the remediation report, so an existing
    # consumer of the nudge keeps working against the loop.
    for key in ("balanced", "reason", "counts", "target", "load_balanced",
                "max_skew", "starved_experts", "gate_bias"):
        assert key in loop, (key, sorted(loop))


def test_the_iteration_cap_is_a_reported_parameter_when_hit():
    """A cap too small to finish must SAY SO, not be inferred from the load.

    ``max_iters`` is a parameter, not a constant: at cap 1 the load cannot have
    been remediated (the fixture needs more than one nudge -- asserted in the
    test above), so ``cap_hit`` must be True, ``iterations`` must equal the cap
    it actually used, and the cap that bounded the run must be the one reported.
    A report that always claimed convergence, or that reported the DEFAULT cap
    regardless of the cap it was given, fails here.
    """
    from CORE.WORMS.expansion.cutting_edge_model import (  # scanner:ignore -- hoist would register worm at collection; sibling asserts module unimported
        DEFAULT_BALANCE_MAX_ITERS,
    )

    for cap in (1, 8):
        moe, rows = _skewed_gate_fixture()
        report = moe.rebalance(rows, max_iters=cap)

        assert report["max_iters"] == cap, report
        assert report["iterations"] == cap, (
            "an unfinished loop must report the cap it spent: %s" % (report,))
        assert report["cap_hit"] is True, report
        assert report["converged"] is False, report
        assert report["max_iters"] < DEFAULT_BALANCE_MAX_ITERS, report
        # The bound vocabulary is reported as a value (None when neither
        # structural reason applies) -- never omitted, so a reader can tell
        # "no bound" from "not reported".
        #
        # WHY THIS ASSERTS THE CAP AND NOT A BOUND NAME: on this fixture the used
        # set stays wide (every expert is a second pick), so no structural reason
        # applies and the honest report is the cap plus no name. The name-vs-cap
        # separation itself is pinned by
        # `test_top_k_pinned_fires_exactly_when_one_choice_set_is_shared`, which
        # also MEASURES why the obvious stricter discriminator must not be used
        # here (it would un-name the genuinely pinned fixture).
        assert "bound" in report, sorted(report)
        # The bias clamp is still respected at a small cap: the loop cannot
        # overshoot its own authority just because it stopped early.
        assert all(abs(b) <= 2.0 + 1e-12 for b in report["gate_bias"]), report


def test_a_collinear_batch_is_reported_on_its_entropy_floor_not_as_convergence():
    """A pinned batch is a BOUND, not a success and not a mere cap hit.

    Collinear tokens make every gate argmax identical, so top-k pins the used
    set to exactly ``top_k`` experts and per-batch ``entropy_normalised`` cannot
    leave ``log(top_k)/log(n_experts)`` -- 1/3 for the live 2-of-8 gate. The loop
    must report that it did NOT converge, must name the bound rather than leave
    it for the caller to guess, and must not claim ``load_balanced``. The
    falsifiable direction is the second assertion: a report that named no bound,
    or that called this balanced, is the failure this test exists to catch.
    """
    from CORE.WORMS.expansion.cutting_edge_model import (  # scanner:ignore -- hoist would register worm at collection; sibling asserts module unimported
        DEFAULT_BALANCE_MAX_ITERS, SparseMoE,
    )

    moe = SparseMoE(d_model=16, n_experts=8, top_k=2, seed=7)
    identical = [[1.0] * 16 for _ in range(8)]
    report = moe.rebalance(identical)

    floor = _floor(moe.top_k, moe.n_experts)
    assert abs(floor - 1.0 / 3.0) < 1e-12, floor

    assert report["converged"] is False, report
    assert report["load_balanced"] is False, (
        "a pinned batch must never be reported as load_balanced: %s" % (report,))
    assert report["cap_hit"] is True, report
    assert report["bound"] == "top_k_pinned", (
        "the collinear floor must be NAMED, not left for the caller: %s" % (report,))
    assert report["iterations"] == DEFAULT_BALANCE_MAX_ITERS, report
    assert report["after"]["used_experts"] == moe.top_k == 2, report

    # The batch sits ON the floor before AND after: that is the signature of a
    # bound rather than an unfinished loop, and it is what tells the two apart.
    assert abs(report["before"]["entropy_normalised"] - floor) < 1e-9, report["before"]
    assert abs(report["after"]["entropy_normalised"] - floor) < 1e-9, report["after"]
    assert abs(report["after"]["entropy_normalised"]
               - _normalised_entropy(report["after"]["counts"], moe.n_experts)) < 1e-12, (
        report["after"])


def test_top_k_pinned_fires_exactly_when_one_choice_set_is_shared():
    """The pin's trigger is an EQUIVALENCE, and the name is a verified claim.

    A note carried out of engram #582 claimed ``rebalance`` derives
    ``top_k_pinned`` from ``after["used_experts"] <= top_k`` alone, a "symptom"
    shared by a genuinely pinned batch and a cap too small to move the load, and
    proposed the strict discriminator "every probe token chose the SAME top-k
    set" as a fix. MEASURED, that note is wrong in both halves, so this test pins
    the truth instead and the change was NOT made.

    (1) The two readings are the SAME reading. ``route()`` gives every token
    exactly ``top_k`` DISTINCT experts, so a union of size ``top_k`` from
    ``top_k``-sized sets forces every set to be equal, and a union of ``top_k + 1``
    is already wider than any single token's choice. Across 480 fixtures
    (40 seeds x 3 configs x 4 batch shapes) the two never disagreed; this test
    re-derives the two readings from ``route`` for a smaller sweep and fails if
    they ever separate. A fixture with more than one choice set is required, so
    the equivalence cannot pass by every batch being trivially singular.

    (2) The obvious "stricter" alternative is WRONG on the case the name exists
    for. Requiring the raw-logit margin to exceed the total bias authority
    (``2 * MAX_GATE_BIAS``) un-names the genuinely pinned collinear fixture:
    its measured margin is 0.176, because the pin comes from every token being
    IDENTICAL, not from a wide margin. Adopting it would fail the three sibling
    fixtures in ``tests/test_cutting_edge_production_fixes.py`` that pin this
    vocabulary. That rejection is the reason a test lives here at all.

    (3) The structural claim itself is checked rather than assumed. On fixtures
    the loop actually NAMES pinned, the used set must survive every admissible
    bias: both corners of the bias box (each bias at +/- ``MAX_GATE_BIAS``, one
    favouring widening and one favouring the current set) plus a mixed-sign
    corner. The name asserts "a floor no bias can move", which is a claim about
    every bias, not only the one the loop happened to accumulate.

    A small ``max_iters`` is used for the sweep because the pin is a property of
    the routing state, not of how long the loop ran, and the collinear fixture is
    measured to name the pin at cap 1 and cap 8 as well as at the default cap
    (the default-cap case is covered by
    ``test_a_collinear_batch_is_reported_on_its_entropy_floor_not_as_convergence``).
    """
    from CORE.WORMS.expansion.cutting_edge_model import (  # scanner:ignore -- hoist would register worm at collection; sibling asserts module unimported
        MAX_GATE_BIAS, SparseMoE,
    )

    def build(seed, n_experts, top_k, d_model):
        return SparseMoE(d_model=d_model, n_experts=n_experts, top_k=top_k,
                         seed=seed)

    sweep_cap = 8
    mismatches, widened = [], []
    pinned_names = wider_sets = 0
    for seed in (3, 7, 11, 23):
        for n_experts, top_k, d_model in ((8, 2, 16), (8, 3, 16), (16, 2, 24)):
            gate = build(seed, n_experts, top_k, d_model)
            rng = random.Random(seed)
            batches = {
                "collinear": [[1.0] * d_model for _ in range(8)],
                "gate_rows": [list(r) for r in gate.gate],
                "collapse": ([list(gate.gate[0])] * 24
                             + [list(gate.gate[i]) for i in range(1, n_experts)]),
                "random": [[rng.uniform(-1.0, 1.0) for _ in range(d_model)]
                           for _ in range(12)],
            }
            for label, rows in batches.items():
                moe = build(seed, n_experts, top_k, d_model)
                report = moe.rebalance(rows, max_iters=sweep_cap)

                # Recompute the observation the name is derived from, from
                # `route`, instead of reading the module's number back. The loop
                # is over, so this is the same biased state `after` describes.
                chosen, _weights, _probs = moe.route(rows)
                sets = [frozenset(c) for c in chosen]
                counts = [0] * n_experts
                for group in sets:
                    for expert_id in group:
                        counts[expert_id] += 1
                assert counts == report["after"]["counts"], (
                    "the fixture is not measuring the state the name reads: %s"
                    % ((seed, n_experts, top_k, label),))

                after_used = report["after"]["used_experts"]
                distinct = len(set(sets))
                if (after_used <= moe.top_k) != (distinct == 1):
                    mismatches.append(
                        (seed, n_experts, top_k, label, after_used, distinct))
                if distinct > 1:
                    wider_sets += 1

                if report["bound"] != "top_k_pinned":
                    continue
                pinned_names += 1
                # A named pin IS "one shared choice set" -- the equivalence's
                # forward direction, asserted where the name is actually used.
                assert distinct == 1, (
                    "a named pin must mean ONE shared choice set: %s"
                    % ((seed, n_experts, top_k, label, after_used, distinct),))
                used_set = {i for i, c in enumerate(counts) if c > 0}
                for vector in (
                        # every bias set to widen the used set as far as the
                        # clamp allows -- the most favourable admissible state
                        [MAX_GATE_BIAS if i not in used_set else -MAX_GATE_BIAS
                         for i in range(n_experts)],
                        # the opposite corner
                        [-MAX_GATE_BIAS if i not in used_set else MAX_GATE_BIAS
                         for i in range(n_experts)],
                        # mixed signs, so the check is not only the two uniform
                        # corners
                        [MAX_GATE_BIAS if (i not in used_set) == (i % 2 == 0)
                         else -MAX_GATE_BIAS for i in range(n_experts)]):
                    probe = build(seed, n_experts, top_k, d_model)
                    probe.gate_bias = list(vector)
                    probe_chosen, _pw, _pp = probe.route(rows)
                    probe_used = len(set().union(*[set(c) for c in probe_chosen]))
                    if probe_used > moe.top_k:
                        widened.append(
                            (seed, n_experts, top_k, label, probe_used))

    assert not mismatches, (
        "`used_experts <= top_k` must be EXACTLY 'every token chose the same "
        "top-k set'; these fixtures separate the two readings: %s" % (mismatches,))
    assert wider_sets, (
        "no fixture produced more than one choice set, so the equivalence was "
        "never exercised and would pass on a constant")
    assert pinned_names, (
        "no fixture was named top_k_pinned, so the structural half of this test "
        "is blind and proves nothing")
    assert not widened, (
        "a named pin must survive EVERY admissible bias, including the corners "
        "that maximally favour widening; these widened instead: %s" % (widened,))
