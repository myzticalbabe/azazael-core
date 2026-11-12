# coding: utf-8
"""Fixes A-E for the defect a live-store run exposed at production batch size.

The defect, measured on the real LocalMemoryStore: with 3,765 train rows the
training step updated **0 of 8** experts and the run reported `status: success`,
with no `trained_reason` and a `loss_delta` of `+1.4491` -- a "regression" -- even
though nothing had been updated. Four separate faults produced that single
misleading report, and each gets its own assertion here:

  * **A** a pool-group TIMEOUT set no reason. `trained_reason` was written only for
    a *skipped* step (MoE disabled), so the one state a monitor most needs
    explained was the one that reported nothing.
  * **B** the date was a number about nothing. With no job returned, `loss_before`
    is a sum over an EMPTY set, so `loss_after - loss_before` compares a real loss
    against zero. Both are `None` now, and the measured value is reported
    separately as `loss_current`.
  * **C** a run that trained nothing still filed a "model version", under a
    timestamped generic name, so "did this produce a model?" answered yes.
  * **D** the finite-difference gradient costs `d_model` full-batch evaluations per
    expert, so at 3,765 tokens every job missed the timeout. The sample is now
    bounded AND reported, and the before/after pair is measured on the same rows.
  * **E** nothing ever trained the MoE gate, so 2 of 8 experts could absorb every
    token forever. `balance_step` is the gate's only learning path.

A later pass (engram #524) added the remediation LOOP around that learning path:
one nudge left the live gate still collapsed, so `SparseMoE.rebalance` nudges until
the load is even or a named iteration cap is reached, and reports before/after plus
which of the two happened. Its test is two-sided on purpose -- see
`test_rebalance_loop_remediates_and_names_the_bound_it_cannot_cross`.

Two things this file is deliberately careful about. Every "it is None" claim is
paired with a POSITIVE control in the same test, because a function that always
returns None would satisfy the negative half alone. And test E states plainly
where an entropy claim CANNOT hold: for a collinear batch, top-k pins the used
set to two experts however the bias is set, so per-batch entropy is `log(2)/log(8)`
= 1/3 no matter what balancing does. Asserting an increase there would assert
something false, so the entropy claim is made on a batch whose load can move.

Conventions (SRC insertion, the fixed data pipe, temp DLQ, in-memory versioner)
mirror `tests/test_neural_pipeline_cutting_edge.py`; the small fast config
mirrors `tests/test_cutting_edge_model.py`.
"""

import math
import sys
import tempfile
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

# The #528 battery below imports at MODULE scope, deliberately. The imports sit
# after the sys.path insert, and they are safe for the sibling suite:
# `test_neural_pipeline_cutting_edge.py`'s "the default path must be untouched"
# test observes `pipeline._cutting_edge_model is None`, not `sys.modules`, and an
# import here does not build any pipeline's model. Module scope also means these
# tests add no `import_inside_function` finding -- the earlier tests in this file
# need function-level imports (to keep a decorator side effect out of collection
# time) and pay for that with a marker; nothing here requires the same trade.
from CORE.WORMS.expansion.cutting_edge_model import (  # noqa: E402
    DEFAULT_BALANCE_MAX_ITERS, MAX_GATE_BIAS, CuttingEdgeModel,  # noqa: F401
)


#: Real record shape the pipeline extracts, in the four keys
#: SchemaAwareFeatureExtractor reads: event_type, severity, source, data.
RECORDS = [
    {"event_type": "cluster_execution_started", "severity": "info",
     "source": "CLUSTERWORMS",
     "data": {"cluster_type": "alpha", "worm_count": 3, "parallel": 1}},
    {"event_type": "cluster_execution_started", "severity": "error",
     "source": "CLUSTERWORMS",
     "data": {"cluster_type": "beta", "worm_count": 7, "parallel": 0}},
    {"event_type": "unknown_event_xyz", "severity": "warning", "source": "Test",
     "data": {"cpu": 42}},
] * 3


class _FixedDataPipe:
    """Stands in for DataPipelineWorm; the memory store is not what this tests."""

    def __init__(self, records):
        self.records = list(records)

    def split_memories(self, **kwargs):
        return {"train": list(self.records), "test": list(self.records),
                "stats": {"total": len(self.records)}}

    def extract_from_memory(self, **kwargs):
        return list(self.records)


def _pipeline(tmpdir, **kwargs):
    """A pipeline wired to injected storage, with the real components intact."""
    from CORE.NeuralPipeline import NeuralPipeline
    from CORE.WORMS.dead_letter_queue import DeadLetterQueue

    pipeline = NeuralPipeline(max_workers=4, llm_augmentation=False, **kwargs)
    pipeline.data_pipe = _FixedDataPipe(RECORDS)
    # Per-test queue file: the default is shared and pre-populated, so a count
    # taken against it could not distinguish a new letter from an old one.
    pipeline.dlq = DeadLetterQueue(storage_path=Path(tmpdir) / "dead_letters.json")
    # In-memory versioning: the durable path writes to the shared memory
    # database, and a test must not add rows to production memory.
    pipeline.versioner._store = False
    pipeline._persist_run = lambda metrics: None
    return pipeline


def _cfg(**overrides):
    """Small, fast config; mirrors tests/test_cutting_edge_model.py's fixture."""
    from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeConfig

    base = dict(d_model=16, n_layers=1, d_state=4, d_ff=8, n_experts=8, top_k=2,
                seed=7)
    base.update(overrides)
    return CuttingEdgeConfig(**base)


def _counts(moe, tokens):
    """Per-expert load from the block's own routing, not a re-implementation."""
    chosen, _weights, _probs = moe.route(tokens)
    counts = [0] * moe.n_experts
    for top in chosen:
        for expert_id in top:
            counts[expert_id] += 1
    return counts


def _normalised_entropy(counts, n_experts):
    """Load entropy, normalised so 0.0 = one expert and 1.0 = perfectly even."""
    total = sum(counts)
    if total <= 0:
        return 0.0
    probs = [c / total for c in counts if c > 0]
    return -sum(p * math.log(p) for p in probs) / math.log(n_experts)


# -- A + B: a timed-out step is explained, and reports no delta --------------

def test_a_timed_out_step_reports_a_reason_and_no_delta(tmpdir):
    """A timeout must be distinguishable from a quiet no-op, and claim no delta.

    The deadline is REAL, not a mock: `timeout` flows through `run_end_to_end` to
    the pool's `wait_group`, and the step's cost at the pipeline's own
    `d_model`=64 is about 1.3 s for this fixture, so a 50 ms deadline is missed and
    no expert returns. A small POSITIVE value is required rather than `0.0`:
    `freethreading.py` computes `deadline = time.time() + timeout if timeout else
    float("inf")`, so `0.0` is falsy and means wait FOREVER -- the opposite of what
    a 0.0 timeout reads as, and it is why this test originally saw
    `waited_ok: True, experts_updated: 8`. The weaker assertion -- "trained is
    False" -- was already true before this fix and is what let the production run
    look green, so the discriminating assertions are the reason string and the
    three None losses.
    """
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True)
        run = pipeline.run_end_to_end(source="CLUSTERWORMS", timeout=0.05,
                                      register_model=True)

        assert run["trainer"] == "CuttingEdgeModel", run
        assert run["trained"] is False, run
        assert run["experts_updated"] == 0, run

        reason = run.get("trained_reason")
        assert isinstance(reason, str) and reason, (
            "a step that updated nothing reported no reason: %r" % (reason,))
        assert "pool" in reason, reason
        assert "waited_ok" in reason, reason

        # B: a delta whose before-half is a sum over an empty set is a number
        # about nothing, so it must not be reported at all.
        assert run["loss_before"] is None, run
        assert run["loss_after"] is None, run
        assert run["loss_delta"] is None, run

        # Deliberately pinned: `status` is "success" whenever the run raised
        # nothing. That is PRE-EXISTING pipeline semantics and this fix does not
        # change it, so a future change to it should be a decision, not an
        # accident. (A `_reward_signals` that raised on the None delta would
        # surface here as a real defect in the reward path, not a test problem.)
        assert run["status"] == "success", run


def test_b_a_zero_update_step_returns_no_delta_and_a_current_loss(tmpdir):
    """No update means no delta -- and the positive control proves that.

    Reporting `None` is only honest if the same call WITH a working timeout still
    reports real numbers. Without that half, a `train_parallel` that always
    returned `None` would pass.

    The batch is 16 rows at the PRODUCTION width (`d_model`=64), so the step costs
    seconds and a 50 ms deadline misses it by a wide margin. It is deliberately not
    the small `d_model`=16 fixture: at that width the whole step is a few
    milliseconds of work, and `wait_group` only tests its deadline after a 10 ms
    `sleep`, so a short timeout there would race the work rather than miss it and
    the result would depend on machine speed. A positive value is required because
    `0.0` is falsy in `freethreading.py` (`timeout if timeout else float("inf")`)
    and therefore means wait forever.
    """
    from CORE.freethreading import WorkStealingPool
    from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeModel

    tokens = [[0.5] * 64 for _ in range(16)]

    timed_out = CuttingEdgeModel(_cfg(seed=3, d_model=64))
    with WorkStealingPool(num_workers=4) as pool:
        dead = timed_out.train_parallel(pool, tokens=tokens, timeout=0.05)

    assert dead["experts_updated"] == 0, dead
    assert dead["waited_ok"] is False, dead
    assert dead["loss_before"] is None, dead
    assert dead["loss_after"] is None, dead
    assert dead["loss_delta"] is None, dead
    assert isinstance(dead["loss_current"], float), dead
    assert math.isfinite(dead["loss_current"]), dead

    stepped = CuttingEdgeModel(_cfg(seed=3, d_model=64))
    with WorkStealingPool(num_workers=4) as pool:
        good = stepped.train_parallel(pool, tokens=tokens, timeout=60.0)

    assert good["experts_updated"] == 8, good
    assert isinstance(good["loss_before"], float), good
    assert isinstance(good["loss_after"], float), good
    assert good["loss_delta"] < 0.0, good
    # `loss_current` is for the no-update case only; a stepped run has a real
    # after-loss and must not also carry an ambiguous "current" figure.
    assert "loss_current" not in good, good


# -- D: the gradient sample is bounded, reported, and a deterministic prefix --

def test_d_the_gradient_sample_is_bounded_and_reported(tmpdir):
    """The production fix: a large batch must still update every expert.

    The measurable claim is that a batch LARGER than the bound still updates every
    expert, because the gradient's cost is now set by the bound rather than by the
    batch length. The production evidence is separate and at the real width: at
    3,765 rows with `d_model`=64 the unbounded step updated 0 of 8 inside the 10 s
    timeout, while a 64-row sample updates 8 of 8 in about 6.8 s. That is why this
    test uses the small fixture (the bound is what is under test here, not the
    clock) and the prefix assertion is what makes the bound reproducible: an
    identical `loss_before` between two runs would ALSO be produced by a random
    draw of the same size, so the sampled rows are recomputed directly and
    compared to `rows[:max_gradient_tokens]`.
    """
    from CORE.freethreading import WorkStealingPool
    from CORE.WORMS.expansion.cutting_edge_model import (
        DEFAULT_GRADIENT_TOKENS, CuttingEdgeModel,
    )

    big = [[0.25] * 16 for _ in range(1000)]

    bounded = CuttingEdgeModel(_cfg(seed=5))
    with WorkStealingPool(num_workers=4) as pool:
        result = bounded.train_parallel(pool, tokens=big, max_gradient_tokens=64,
                                        timeout=120.0)

    assert result["tokens_seen"] == 1000, result
    assert result["gradient_tokens"] == 64, result
    assert result["experts_updated"] == 8, (
        "a 1000-token batch still updated no expert: %r" % (result,))
    assert result["loss_delta"] < 0.0, result

    # The sample is the deterministic PREFIX, proven by recomputation.
    fresh = CuttingEdgeModel(_cfg(seed=5))
    prefix = fresh._as_rows(big)[:64]
    recomputed = sum(fresh._expert_local_loss(i, prefix)
                     for i in range(fresh.config.n_experts))
    assert abs(result["loss_before"] - recomputed) < 1e-9, (
        "the sampled rows are not rows[:64]: reported before=%r, recomputed=%r"
        % (result["loss_before"], recomputed))

    # A batch larger than the default is bounded to exactly the default.
    over = [[0.25] * 16 for _ in range(DEFAULT_GRADIENT_TOKENS + 50)]
    defaulted = CuttingEdgeModel(_cfg(seed=5))
    with WorkStealingPool(num_workers=4) as pool:
        got = defaulted.train_parallel(pool, tokens=over, timeout=180.0)

    assert got["gradient_tokens"] == DEFAULT_GRADIENT_TOKENS, got
    assert got["tokens_seen"] == DEFAULT_GRADIENT_TOKENS + 50, got
    assert got["experts_updated"] == 8, got


# -- C: an untrained run files no model version -----------------------------

def test_c_an_untrained_run_files_no_model_version(tmpdir):
    """No training, no model version -- and the generic trainers keep theirs.

    Half of the sixteen switch combinations have no MoE, and the experts' output
    bias is the model's only learning surface, so those configurations cannot
    train. Filing one as `cutting_edge_model` claims a model state that was never
    produced. The second half is the regression guard: a trainer with no `trained`
    key at all (the SB3/GRPO/QLEARN shape) must STILL take the fallback
    registration, or this fix would quietly stop those paths registering.
    """
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True,
                             cutting_edge_config={"use_moe": False})
        run = pipeline.run_end_to_end(source="CLUSTERWORMS", register_model=True)

        assert run["status"] == "success", run.get("error")
        assert run["trained"] is False, run
        assert not run.get("version_registered"), (
            "an untrained configuration reported a registered model version")
        assert pipeline.versioner.list_versions("cutting_edge_model") == [], (
            "an untrained configuration was filed under the model's own name")
        assert run.get("registration_skipped"), (
            "the skipped registration was not reported: %r" % (run,))
        assert run.get("model_name") != "cutting_edge_model", run.get("model_name")

        # The fallback path, exercised with a trainer that reports no `trained`
        # key -- the shape every pre-existing trainer returns.
        plain = _pipeline(td, use_cutting_edge=True)
        plain._train_agent = lambda **kw: {"stub": True}
        other = plain.run_end_to_end(source="CLUSTERWORMS", register_model=True)

        name = other.get("model_name")
        assert name and name != "cutting_edge_model", other
        assert len(plain.versioner.list_versions(name)) == 1, (
            "the generic trainers lost their fallback registration")


# -- E: the gate's only learning path ---------------------------------------

def test_e_balancing_moves_the_gate_toward_balance(tmpdir):
    """Balancing pushes load off the over-used experts, and says where it can't.

    Two facts, and one honest limit:

      * The all-zero bias must be INERT -- if adding the term changed routing on
        its own, every pre-existing routing test would silently retarget.
      * On a batch where the load genuinely CAN move, removing an injected skew
        must raise the load entropy, strictly.
      * The limit, asserted rather than hidden: for a COLLINEAR batch, top-k pins the
        COUNT of experts to two however the bias is set, so per-batch entropy is
        `log(2)/log(8)` = 1/3 and CANNOT rise. That is the measured production case
        (counts `[0,19,0,0,0,0,19,0]`). The honest effect there is WHICH experts are
        chosen, so that is what gets asserted: the nudge signs against the load it
        MEASURED on every step, and the set of experts receiving load across steps
        grows past the two the gate started on. That second claim is falsified by
        the same batch with `balance=False`, which stays pinned -- so the rotation is
        attributable to balancing rather than to the batch. An entropy assertion on
        this batch would assert something false.
    """
    from CORE.WORMS.expansion.cutting_edge_model import (
        DEFAULT_BALANCE_GAMMA, MAX_GATE_BIAS, CuttingEdgeModel,
    )

    model = CuttingEdgeModel(_cfg(seed=7))
    moe = model.moes[0]
    n_experts = moe.n_experts

    # (1) Zero bias is inert: routing must equal the raw gate argmax.
    assert moe.gate_bias == [0.0] * n_experts, moe.gate_bias
    identical = [[1.0] * 16 for _ in range(8)]
    chosen_zero, _w, _p = moe.route(identical)
    manual = []
    for token in identical:
        scored = sorted(
            range(n_experts),
            key=lambda i: (-sum(a * b for a, b in zip(moe.gate[i], token)), i))
        manual.append(scored[:moe.top_k])
    assert chosen_zero == manual, (
        "an all-zero gate bias changed routing; the new term must be inert")

    # (2) The production collapse, reproduced: collinear tokens use 2 experts.
    counts0 = _counts(moe, identical)
    assert sum(1 for c in counts0 if c > 0) == 2, counts0
    assert abs(_normalised_entropy(counts0, n_experts) - 1.0 / 3.0) < 1e-9, counts0

    # The empty batch is reported, not silently treated as balanced.
    empty = moe.balance_step([], gamma=DEFAULT_BALANCE_GAMMA)
    assert empty["balanced"] is False and empty["reason"], empty
    assert moe.gate_bias == [0.0] * n_experts, "an empty batch moved the bias"

    # (3) The mechanism on that batch: the nudge must sign against the load it
    # MEASURED on each step, not against the pair chosen before the loop. With
    # collinear tokens the load ROTATES -- once the favoured pair is biased down far
    # enough a different pair wins -- so an expert can be over-target early and
    # under-target later and legitimately end up positive. The previous
    # "the initially used pair ends negative" assertion encoded exactly that false
    # premise and failed on a correct implementation.
    used_at_start = [i for i, c in enumerate(counts0) if c > 0]
    assert len(used_at_start) == 2, counts0
    seen = set(used_at_start)
    for _ in range(40):
        before_bias = list(moe.gate_bias)
        info = moe.balance_step(identical)
        assert info["balanced"] is True, info
        counts = info["counts"]
        target = info["target"]
        seen.update(i for i, c in enumerate(counts) if c > 0)
        for i in range(n_experts):
            nudge = (-DEFAULT_BALANCE_GAMMA if counts[i] > target
                     else DEFAULT_BALANCE_GAMMA)
            expected = max(-MAX_GATE_BIAS,
                           min(MAX_GATE_BIAS, before_bias[i] + nudge))
            assert abs(moe.gate_bias[i] - expected) < 1e-12, (
                "expert %d load=%d target=%r: bias %r -> %r, expected %r"
                % (i, counts[i], target, before_bias[i], moe.gate_bias[i], expected))

    # The gate is no longer pinned to the pair it started on: balancing widened the
    # set of experts that carry load. THIS is the production-relevant effect -- not
    # a per-batch entropy gain, which collinear input makes impossible.
    assert len(seen) > len(used_at_start), (
        "balancing never moved the load off %r (gate_bias=%r)"
        % (used_at_start, moe.gate_bias))

    # The falsification control: the same batch with `balance=False` stays pinned to
    # the same two experts, with an untouched bias. Without this, "the load rotated"
    # would not be attributable to balancing.
    from CORE.freethreading import WorkStealingPool
    unb = CuttingEdgeModel(_cfg(seed=7))
    with WorkStealingPool(num_workers=4) as pool:
        off = unb.train_parallel(pool, tokens=identical, balance=False, timeout=30.0)
    assert off.get("balancing") is None, off
    assert unb.moes[0].gate_bias == [0.0] * n_experts, unb.moes[0].gate_bias
    assert {i for i, c in enumerate(_counts(unb.moes[0], identical)) if c > 0} == set(
        used_at_start), (
        "the load moved without balancing, so the rotation is not attributable to it")

    # (4) Entropy on a batch where the load CAN move. A gate-aligned batch routes
    # over all eight experts; an injected bias on one expert skews that, and
    # balancing must take the skew back out.
    spread = [list(row) for row in moe.gate] * 8
    skewed = CuttingEdgeModel(_cfg(seed=7))
    skewed.moes[0].gate_bias[0] = MAX_GATE_BIAS
    before = _normalised_entropy(_counts(skewed.moes[0], spread), n_experts)
    used_before = sum(1 for c in _counts(skewed.moes[0], spread) if c > 0)
    assert before < 1.0 - 1e-9, (
        "the injected skew did not skew anything, so the entropy claim would be "
        "vacuous: %.6f" % before)

    last = None
    for _ in range(300):
        last = skewed.moes[0].balance_step(spread)
    after = _normalised_entropy(_counts(skewed.moes[0], spread), n_experts)
    used_after = sum(1 for c in _counts(skewed.moes[0], spread) if c > 0)

    assert last is not None and last["balanced"] is True
    assert skewed.moes[0].gate_bias[0] < MAX_GATE_BIAS, (
        "an over-target expert's bias was not pushed down")
    assert after > before, (
        "balancing must raise the load entropy: before=%.6f after=%.6f"
        % (before, after))
    # Comparative, not an absolute: balancing may not REDUCE how many experts
    # carry load. An absolute `== n_experts` would depend on this seed's gate
    # geometry rather than on the property being asserted.
    assert used_after >= used_before, (used_before, used_after)

    # (5) Deterministic and clamped, or two runs of one config are not comparable.
    twin = CuttingEdgeModel(_cfg(seed=7))
    twin.moes[0].gate_bias[0] = MAX_GATE_BIAS
    for _ in range(300):
        twin.moes[0].balance_step(spread)
    assert twin.moes[0].gate_bias == skewed.moes[0].gate_bias, (
        "balancing is not deterministic across two identical runs")
    assert all(abs(b) <= MAX_GATE_BIAS for b in skewed.moes[0].gate_bias), (
        "a bias escaped the clamp: %r" % (skewed.moes[0].gate_bias,))


def test_balance_step_reports_the_load_truthfully_not_just_that_it_ran():
    """#511's live run reported `balanced: True` while 5 of 8 experts were starved.

    `balanced` is True for ANY non-empty batch. Measured on the live store it came
    back True alongside counts `[0, 4586, 0, 0, 0, 3531, 1055, 0]` with target
    1146.5 -- one expert at 4x its share and five experts taking nothing. A monitor
    reading only that flag concludes routing is healthy, so the added fields must
    be DERIVED from the measured counts. This pins the derivation, not the value:
    a flag that is hardcoded True cannot pass it.
    """
    from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeModel

    model = CuttingEdgeModel(_cfg(seed=7))
    moe = model.moes[0]
    rows = [[float((i * 7 + j) % 11) / 11.0 for j in range(5)] for i in range(24)]

    res = moe.balance_step(rows)
    counts = res["counts"]
    target = res["target"]

    # The historical meaning is preserved, so existing consumers are unaffected.
    assert res["balanced"] is True

    assert res["starved_experts"] == [i for i, c in enumerate(counts) if c == 0], res
    assert abs(res["max_skew"] - max(counts) / target) < 1e-12, res
    assert res["load_balanced"] is (not res["starved_experts"]
                                    and max(counts) <= target * 1.25), (
        "load_balanced must be derived from the measured counts; got %r for "
        "counts=%r target=%s" % (res["load_balanced"], counts, target))

    # The fixture must actually be imbalanced, or the falsifiability this test
    # exists for is not exercised at all.
    assert res["starved_experts"] or res["max_skew"] > 1.25, (
        "the fixture no longer produces an imbalanced load, so load_balanced "
        "is not being falsified: counts=%r" % (counts,))

    # An empty batch is the ONE case where `balanced` is legitimately False.
    empty = moe.balance_step([])
    assert empty["balanced"] is False, empty
    assert empty["counts"] == [], empty


# -- #524: the bounded remediation LOOP around the gate's learning path -------

def test_rebalance_loop_remediates_and_names_the_bound_it_cannot_cross():
    """One nudge is not remediation; the loop must converge or name its bound.

    #521 made the collapse VISIBLE (2 of 8 experts, `entropy_normalised` exactly
    1/3, `max_skew` 4.0) but a SINGLE `balance_step` did not fix it -- the live
    batch was still collapsed after one nudge. `SparseMoE.rebalance` is the
    bounded loop: nudge until the load is even, or until a named cap, and report
    before/after so the effect is a measurement rather than a flag.

    The fixture is deliberately TWO-SIDED, which is what makes this test
    falsifiable. Branch (1) is a batch whose load CAN move, and it must converge
    with `cap_hit` False and `bound` None. Branch (2) is a collinear batch, where
    top-k pins the used set to exactly `top_k` experts so `entropy_normalised`
    cannot leave `log(top_k)/log(n_experts)`; it must NOT converge, must report
    `cap_hit` True, and must NAME the bound. A hardcoded answer (`converged`
    always True, or `cap_hit` always True, or `bound` always None) satisfies one
    branch and fails the other, so neither flag can be a constant. Both branches
    also assert that the fixture really does start imbalanced, so a fixture that
    stopped producing a collapse would fail rather than pass vacuously.
    """
    # Imported HERE, not at module level, on purpose: importing this module
    # registers a worm as a decorator side effect, and the sibling suite
    # (tests/test_neural_pipeline_cutting_edge.py) asserts the default path leaves
    # the module unimported. A module-level import would fire that side effect at
    # COLLECTION time and make that assertion unfalsifiable. `import_inside_function`
    # is therefore suppressed on this one line -- the rule's intent (top-level
    # imports) is contradicted by a real constraint here, so the marker is the
    # sanctioned fallback rather than a hoist that would break the sibling test.
    from CORE.WORMS.expansion.cutting_edge_model import (  # scanner:ignore -- hoist would register worm at collection; sibling asserts module unimported
        DEFAULT_BALANCE_MAX_ITERS, MAX_GATE_BIAS, CuttingEdgeModel,
    )

    def _floor_for_top_k(moe, n_experts):
        return math.log(moe.top_k) / math.log(n_experts)

    # (1) A batch whose load CAN move. The gate's own rows as tokens route over all
    # eight experts; injecting MAX_GATE_BIAS on expert 0 skews that, and the loop
    # must take the skew back out.
    movable = CuttingEdgeModel(_cfg(seed=7))
    moe = movable.moes[0]
    spread = [list(row) for row in moe.gate] * 8
    moe.gate_bias[0] = MAX_GATE_BIAS

    report = moe.rebalance(spread)
    before, after = report["before"], report["after"]

    # The fixture must actually START collapsed, or the remediation claim is vacuous.
    assert before["max_skew"] > 1.25 or before["starved_experts"], before
    assert before["entropy_normalised"] < 1.0 - 1e-9, before

    # Before/after measured on the SAME rows is the falsifiable effect.
    assert after["entropy_normalised"] > before["entropy_normalised"], (
        "rebalancing must raise load entropy: %.6f -> %.6f"
        % (before["entropy_normalised"], after["entropy_normalised"]))
    assert after["used_experts"] >= before["used_experts"], (before, after)
    assert after["max_skew"] < before["max_skew"], (before, after)

    # Converged means the load became even BEFORE the cap, not that the cap ran out.
    assert report["converged"] is True, report
    assert report["cap_hit"] is False and report["bound"] is None, report
    assert 0 <= report["iterations"] < DEFAULT_BALANCE_MAX_ITERS, report
    assert report["max_iters"] == DEFAULT_BALANCE_MAX_ITERS, report
    # A full sweep of the +/-MAX_GATE_BIAS clamp at DEFAULT_BALANCE_GAMMA is the
    # reason this cap is a computed number; if it ever drops below that, a real
    # skew would be misreported as a bound.
    assert DEFAULT_BALANCE_MAX_ITERS >= MAX_GATE_BIAS / 0.01, DEFAULT_BALANCE_MAX_ITERS

    # The reported entropy is an independent recomputation, not the module's own
    # number echoed back: the test's helper is a separate implementation.
    assert abs(report["after"]["entropy_normalised"]
               - _normalised_entropy(after["counts"], moe.n_experts)) < 1e-12, after

    # The sampling bound is reported, so a caller can tell what was measured.
    assert report["rows_used"] == len(spread), report
    assert report["rows_available"] == len(spread), report
    # ...and `balance_step`'s keys survive, so an existing consumer is unaffected.
    for key in ("balanced", "reason", "counts", "target", "load_balanced",
                "max_skew", "starved_experts", "gate_bias"):
        assert key in report, (key, sorted(report))

    # (2) The bound the loop CANNOT cross. Collinear tokens make every gate argmax
    # identical, so top-k pins the used set to exactly top_k experts: per-batch
    # entropy is stuck at log(top_k)/log(n_experts), which for the live 2-of-8
    # collapse is exactly 1/3. The loop must report the cap AND name the reason,
    # rather than pretending a collapsed batch converged.
    pinned = CuttingEdgeModel(_cfg(seed=7))
    pmoe = pinned.moes[0]
    identical = [[1.0] * 16 for _ in range(8)]
    bound_report = pmoe.rebalance(identical)

    assert bound_report["converged"] is False, bound_report
    assert bound_report["cap_hit"] is True, bound_report
    assert bound_report["bound"] == "top_k_pinned", bound_report
    assert bound_report["iterations"] == DEFAULT_BALANCE_MAX_ITERS, bound_report
    assert bound_report["max_iters"] == DEFAULT_BALANCE_MAX_ITERS, bound_report
    assert bound_report["after"]["used_experts"] == pmoe.top_k == 2, bound_report
    floor = _floor_for_top_k(pmoe, pmoe.n_experts)
    assert abs(bound_report["after"]["entropy_normalised"] - floor) < 1e-9, (
        "the pinned batch must sit ON the structural floor %.6f, got %.6f"
        % (floor, bound_report["after"]["entropy_normalised"]))
    # The collinear batch starts AND ends at the floor: that is the signature of a
    # bound rather than an unfinished loop, and it is what tells the two apart.
    assert abs(bound_report["before"]["entropy_normalised"] - floor) < 1e-9, (
        bound_report["before"])
    assert bound_report["starved_experts"], bound_report
    assert bound_report["after"]["starved_experts"], bound_report

    # (3) The empty batch reported, not silently "converged".
    empty = pmoe.rebalance([])
    assert empty["converged"] is False and empty["cap_hit"] is False, empty
    assert empty["iterations"] == 0 and empty["before"] is None, empty
    assert empty["reason"], empty


# -- #527: the probe is a SAMPLE -- did the remediation reach the full batch? --

def test_rebalance_reports_the_full_batch_load_when_the_probe_is_a_sample():
    """Round 4's gap: the loop moved its 256-row probe and the 4235-row report did not.

    `rebalance` bounded its probe to `DEFAULT_BALANCE_TOKENS`, and `rows_used` /
    `rows_available` made that bound visible -- but every before/after number it
    reported described the SAMPLE. "The accumulating bias never reached the rows
    outside the probe" and "nothing measured those rows after the loop" are then
    the SAME observation, which is why Round 4 could not tell them apart. The loop
    now measures the same before/after pair over EVERY available row.

    Falsifiable in both directions, which is what stops the new fields being a
    renamed copy of the probe:

      * a batch whose load CAN move must show `after_full` differ from
        `before_full` even though the loop only fitted the first 256 of 600 rows;
      * a COLLINEAR batch must stay pinned, and the collinear case asserts the two
        halves of "pinned" separately -- the used SET can move (the bias reorders
        near-ties) while the used COUNT cannot leave `top_k`. A counts-equality
        assertion would therefore have been wrong there, and the honest signature
        of the bound is the used set plus the entropy floor.

    `before_full` / `after_full` are also recomputed here by a separate helper, not
    read back from the module's own numbers, so a dict copied from `after` fails.
    """
    from CORE.WORMS.expansion.cutting_edge_model import (  # scanner:ignore -- hoist would register worm at collection; sibling asserts module unimported
        DEFAULT_BALANCE_TOKENS, MAX_GATE_BIAS, CuttingEdgeModel,
    )

    # 600 rows = 75 copies of the gate's own 8 rows: longer than one probe, and a
    # batch whose load genuinely CAN move once the gate is skewed.
    model = CuttingEdgeModel(_cfg(seed=7))
    moe = model.moes[0]
    spread = [list(row) for row in moe.gate] * 75
    assert len(spread) == 600 > DEFAULT_BALANCE_TOKENS, len(spread)

    # The skew is injected FIRST, because `before_full` is the load at the moment
    # the loop is entered -- a recomputation taken before the injection would be a
    # measurement of a different state and would only look like agreement.
    moe.gate_bias[0] = MAX_GATE_BIAS
    before_full_counts = _counts(moe, spread)
    before_entropy = _normalised_entropy(before_full_counts, moe.n_experts)

    report = moe.rebalance(spread)

    # The probe really was a SAMPLE of this batch, or the gap is not exercised.
    assert report["rows_used"] == DEFAULT_BALANCE_TOKENS, report
    assert report["rows_available"] == len(spread), report
    assert report["rows_full"] == len(spread), report
    # `before_full` is the PRE-loop load of every row, not a post-hoc inference:
    # it must equal the independent recomputation taken before the loop ran.
    assert report["before_full"]["counts"] == before_full_counts, report["before_full"]

    after_full_counts = report["after_full"]["counts"]
    assert after_full_counts == _counts(moe, spread), after_full_counts
    assert sum(after_full_counts) == moe.top_k * len(spread), after_full_counts

    # THE PROPAGATION CLAIM: a bias fitted to 256 rows moved the load of all 600.
    after_entropy = _normalised_entropy(after_full_counts, moe.n_experts)
    assert after_entropy > before_entropy, (before_entropy, after_entropy)
    assert report["full_balanced"] is True, report["after_full"]
    # ...and the full-batch pair is not the probe's pair: different row count,
    # different counts.
    assert report["rows_full"] != report["rows_used"], report
    assert after_full_counts != report["after"]["counts"], report

    # (2) The bound survives the new fields rather than being papered over by them.
    pinned = CuttingEdgeModel(_cfg(seed=7))
    pmoe = pinned.moes[0]
    identical = [[1.0] * 16 for _ in range(600)]
    bound_report = pmoe.rebalance(identical)

    assert bound_report["bound"] == "top_k_pinned", bound_report
    assert bound_report["full_balanced"] is False, bound_report["after_full"]
    assert bound_report["after_full"]["used_experts"] == pmoe.top_k, (
        bound_report["after_full"])
    floor = math.log(pmoe.top_k) / math.log(pmoe.n_experts)
    assert abs(bound_report["after_full"]["entropy_normalised"] - floor) < 1e-9, (
        "the pinned full batch must sit ON the structural floor %.6f, got %.6f"
        % (floor, bound_report["after_full"]["entropy_normalised"]))

    # (3) When the probe IS the whole batch the two pairs are one measurement, so
    # the added fields cannot invent a difference that was never measured.
    unsampled = pmoe.rebalance(identical[:8])
    assert unsampled["rows_used"] == unsampled["rows_available"] == 8, unsampled
    assert unsampled["before_full"] == unsampled["before"], unsampled
    assert unsampled["after_full"] == unsampled["after"], unsampled


def test_the_run_reports_the_routing_it_measures_after_the_balance_loop(tmpdir):
    """`result["routing"]` is PRE-remediation; the run must also report the post.

    `result["routing"]` is summarised from the forward that runs BEFORE
    `train_parallel` nudges the gate, so on a run where the loop moves the gate it
    still prints the pre-loop collapse: Round 4 reported 2 of 8 experts over 4235
    rows while the same run's own 256-row probe had been remediated to 8 of 8. The
    fix is a measurement, so this test pins the PROVENANCE of two numbers rather
    than a value: the run's `routing` must equal the loop's pre-loop full-batch
    snapshot (same rows, one batch, two measurements), and
    `routing_after_balance` must equal the loop's post-loop full-batch snapshot.
    Together those say the reported verdict was sampled before the remediation and
    that the post-remediation number now exists.
    """
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True)
        run = pipeline.run_end_to_end(source="CLUSTERWORMS", register_model=False)

        balancing = run.get("balancing")
        assert balancing is not None, sorted(run)
        after_full = balancing["after_full"]
        assert after_full is not None, balancing

        summary = run["routing_after_balance"]
        assert summary is not None, sorted(run)
        # DERIVED from the loop's own full-batch measurement, not `routing` under a
        # second name.
        assert summary["counts"] == after_full["counts"], (summary, after_full)
        assert summary["used_experts"] == sum(1 for c in after_full["counts"] if c), (
            summary)
        # Both summaries describe ONE batch forwarded once, so the totals must
        # agree even though the distributions may not.
        assert sum(summary["counts"]) == sum(run["routing"]["counts"]), (
            summary, run["routing"])
        assert summary["n_experts"] == run["routing"]["n_experts"], run["routing"]

        # The root cause, pinned by the loop's own numbers: the verdict the run
        # reports is the PRE-loop level of the very batch the loop then moved.
        assert run["routing"]["counts"] == balancing["before_full"]["counts"], (
            "the run's routing verdict is not the pre-loop snapshot",
            run["routing"], balancing["before_full"])
        # ...so if the loop moved the full-batch load, the two verdicts differ, and
        # the pre-loop one is the one that cannot move.
        if after_full["counts"] != balancing["before_full"]["counts"]:
            assert summary["counts"] != run["routing"]["counts"], (
                "the loop moved the full-batch load but the run still reports the "
                "pre-loop verdict", summary, run["routing"])


# -- F: the accumulated bias outlives the run (the pipeline caches its model) --

def test_f_the_accumulated_bias_persists_into_the_next_run(tmpdir):
    """Where the propagation DOES surface, and why the live runs could not see it.

    The brief's thread 2 asked directly whether the accumulated ``gate_bias``
    persists into a later full-batch forward or is scoped/reset per probe. It
    persists, and its scope is the PIPELINE, not the run:

    * ``gate_bias`` is a ``SparseMoE`` instance attribute created once in
      ``__init__`` and mutated IN PLACE by ``balance_step`` -- no path resets it;
    * ``SparseMoE.route`` adds it on every forward, so any later forward sees it;
    * ``NeuralPipeline._cutting_edge`` builds the model ONCE and caches it on the
      pipeline, so a second ``run_end_to_end`` on the same pipeline forwards with
      the FIRST run's accumulated bias.

    So the remediation does reach the full batch. What hid it is that ``routing``
    is the PRE-loop level of the CURRENT run, and the Rounds 4-5 drivers built a
    fresh pipeline per run (bias back to zero). The proof is non-vacuous: run 1's
    own pre/post levels differ, and run 2's PRE level equals run 1's POST level
    exactly -- which a per-probe reset could not produce.
    """
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True,
                             cutting_edge_config=_cfg(seed=7))
        first_model = id(pipeline.cutting_edge_model)

        run1 = pipeline.run_end_to_end(source="CLUSTERWORMS", timeout=60.0,
                                       register_model=False)
        run2 = pipeline.run_end_to_end(source="CLUSTERWORMS", timeout=60.0,
                                       register_model=False)

        assert id(pipeline.cutting_edge_model) == first_model, (
            "the model is no longer cached across runs, so this fixture would "
            "measure something other than cross-run persistence")

        balancing = run1["balancing"]
        before1 = balancing["before_full"]["counts"]
        after1 = balancing["after_full"]["counts"]
        assert after1 is not None, balancing

        # Non-vacuous: this fixture really does move the full-batch load.
        assert before1 != after1, (
            "fixture no longer exercises a moved full-batch load", before1)
        # ...and the run's reported verdict is still the PRE-loop level.
        assert run1["routing"]["counts"] == before1, run1["routing"]

        # The persistence claim: the second run's pre-loop forward already sees
        # every nudge the first run's loop accumulated.
        assert run2["routing"]["counts"] == after1, (
            "the accumulated gate_bias did not survive into the next run",
            run2["routing"], after1)


# -- #528 (Round 6): name the clamp-exhausted bound, and let the reward see it --


def _clamp_rows(moe):
    """A batch that reaches the clamp: three experts' own rows repeated, rest once.

    Found by measurement, not derivation (the loop's fixed point depends on how
    the batch's dot products interact): the load moves off the pinned pair --
    every one of the 8 experts ends up carrying tokens, so this is NOT the
    `top_k_pinned` shape -- yet one expert cannot be brought to its share even
    after its bias saturates, so the loop exhausts the cap with a bias ON the
    clamp. That is the case the vocabulary had no name for.
    """
    gate = [list(row) for row in moe.gate]
    return ([list(gate[0])] * 8 + [list(gate[1])] * 8 + [list(gate[2])] * 8
            + [list(gate[i]) for i in range(3, 8)])


def _live_shaped_rows(moe):
    """A batch shaped like the live one at the shipped cap: skewed, NOT clamped.

    Measured analogue of the production shape (live: 7 experts used, skew 3.59,
    zero clamped biases at cap 256, `bound=None`): one expert's row dominates and
    the loop runs the cap out with every bias still inside the clamp, so the
    clamp condition is false and the report must stay unnamed exactly as every
    Rounds 1-5 shipped-config report did.
    """
    gate = [list(row) for row in moe.gate]
    return [list(gate[0])] * 24 + [list(gate[i]) for i in range(1, 8)]


def test_round6_the_clamp_exhausted_bound_is_named():
    """The third case: out of iterations AND out of authority (engram #528).

    Round 5's live run reported `bound=null` while the loop had spent every nudge
    and one bias sat at exactly 2.0 of `MAX_GATE_BIAS`; `cap_hit=True` alone
    misattributes that to the iteration cap, and cap 512/1024/2048 were
    byte-identical, so more iterations provably cannot help. This names it.
    """
    moe = CuttingEdgeModel(_cfg()).moes[0]
    report = moe.rebalance(_clamp_rows(moe))

    assert report["cap_hit"] is True, report
    assert report["converged"] is False, report
    assert report["bound"] == "bias_clamp_exhausted", report
    # The named case really is the clamp case...
    assert any(abs(b) >= MAX_GATE_BIAS for b in moe.gate_bias), moe.gate_bias
    # ...and it is NOT the pinned case: a used set wider than top_k is what makes
    # the two conditions distinguishable instead of one shadowing the other.
    assert report["after"]["used_experts"] > moe.top_k, report["after"]
    assert report["max_iters"] == DEFAULT_BALANCE_MAX_ITERS, report


def test_round6_top_k_pinned_keeps_precedence_over_a_clamped_bias():
    """A pinned used set is a floor no bias can move, so it outranks the clamp.

    The collinear fixture satisfies BOTH conditions -- measured, the loop drives
    two biases onto the clamp while the argmax stays pinned -- so this asserts the
    PRECEDENCE rather than re-confirming the old outcome. It is also where #528's
    falsifiability control lives: an implementation that tested the clamp first
    would report the new value and fail the `top_k_pinned` assertion, one that
    hardcoded `converged = True` fails the next line, and one that hardcoded
    `bound` fails one of the two. (The pre-existing control at this fixture, the
    "mutated converged=True must fail" assertion, still holds and is exercised by
    this same report.)
    """
    moe = CuttingEdgeModel(_cfg()).moes[0]
    report = moe.rebalance([[1.0] * 16 for _ in range(8)])

    assert sum(1 for b in moe.gate_bias if abs(b) >= MAX_GATE_BIAS) >= 1, (
        "fixture no longer clamps a bias, so precedence is untested", moe.gate_bias)
    assert report["bound"] == "top_k_pinned", report
    assert report["bound"] != "bias_clamp_exhausted", report
    assert report["converged"] is False, report
    assert report["cap_hit"] is True, report


def test_round6_a_converged_report_names_no_bound():
    """Converged before the cap means no bound at all, whichever one exists."""
    moe = CuttingEdgeModel(_cfg()).moes[0]
    rows = [list(row) for row in moe.gate] * 8
    report = moe.rebalance(rows)

    assert report["converged"] is True, report
    assert report["cap_hit"] is False, report
    assert report["bound"] is None, report
    assert report["iterations"] < DEFAULT_BALANCE_MAX_ITERS, report


def test_round6_the_shipped_cap_live_shape_stays_unnamed():
    """Compatibility guard: the shipped cap names nothing new on a live shape.

    Rounds 1-5 compare on `bound` for the shipped config, so the new condition
    must not fire there. At the shipped cap the live batch has no clamped bias
    (Round 5's H4 measurement: clamped=0), which this fixture reproduces -- the
    report stays `None` even though the loop is capped and unconverged.
    """
    moe = CuttingEdgeModel(_cfg()).moes[0]
    report = moe.rebalance(_live_shaped_rows(moe))

    assert report["cap_hit"] is True, report
    assert report["converged"] is False, report
    assert max(abs(b) for b in moe.gate_bias) < MAX_GATE_BIAS, moe.gate_bias
    assert report["bound"] is None, report
    # The un-named third SHAPE still exists and is still unnamed: a capped,
    # unconverged, clamped-free report is a real observation the vocabulary
    # deliberately does not label (naming it would move a field the reach
    # document compares across rounds).
    assert report["after"]["used_experts"] > moe.top_k, report["after"]


def test_round6_a_named_bound_always_implies_a_cap_hit():
    """Invariant across every fixture shape: a name means the cap ran out.

    Cheaper than it looks and it is the one claim that must hold for all four
    fixtures at once, including the two whose bound is None.
    """
    for label, build in (("clamp", _clamp_rows), ("live-shaped", _live_shaped_rows),
                         ("collinear", lambda moe: [[1.0] * 16 for _ in range(8)]),
                         ("movable", lambda moe: [list(r) for r in moe.gate] * 8)):
        moe = CuttingEdgeModel(_cfg()).moes[0]
        report = moe.rebalance(build(moe))
        assert report["bound"] is None or report["cap_hit"] is True, (label, report)
        assert report["cap_hit"] is False or report["iterations"] == report["max_iters"], (
            label, report["iterations"], report["max_iters"])


def test_round6_the_reward_input_prefers_the_post_balance_used_fraction(tmpdir):
    """#528: the reward must see the REMEDIATION, not the state it was fitted on.

    `_reward_signals` returns `[progress, coverage, load]` and the load term IS
    the `used_fraction` the run chose, so the reward INPUT is observable by
    capturing the signals the model is handed -- no need to invert the adapter's
    output. Two halves, because "prefers the new value" means nothing without a
    fallback: with the loop's full-batch snapshot present the load must be the
    POST value, and with no balancing reported it must be the pre-loop value
    exactly as before. The fixture makes the two values differ, so a preference
    for the wrong one cannot pass.
    """
    def _capture_on(model, sink):
        original = model.reward

        def _capture(signals, **kwargs):
            sink["signals"] = list(signals)
            return original(signals, **kwargs)

        model.reward = _capture

    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True, cutting_edge_config=_cfg())
        sink = {}
        _capture_on(pipeline.cutting_edge_model, sink)
        run = pipeline.run_end_to_end(source="CLUSTERWORMS", timeout=60.0,
                                      register_model=False)

        pre = run["routing"]["used_fraction"]
        post = (run["routing_after_balance"] or {}).get("used_fraction")
        assert post is not None, (sorted(run), run["routing_after_balance"])
        assert post != pre, (
            "fixture must make the two values differ, or the preference is "
            "unfalsifiable", pre, post)
        assert sink["signals"][2] == post, (
            "the reward's load term is not the post-remediation used_fraction",
            sink["signals"], pre, post)
        assert sink["signals"][2] != pre, sink["signals"]
        # Both keys keep their own meanings: this change feeds the reward and does
        # NOT rewrite the pre-loop summary Rounds 1-5 compare on.
        assert run["routing"]["used_fraction"] == pre, run["routing"]

    # Fallback half: no `balancing` reported at all -> the pre-loop value.
    with tempfile.TemporaryDirectory() as td2:
        pipeline2 = _pipeline(td2, use_cutting_edge=True, cutting_edge_config=_cfg())
        model2 = pipeline2.cutting_edge_model
        sink2 = {}
        _capture_on(model2, sink2)
        # A skipped step reports no `balancing`, which is exactly the state a path
        # without the loop produces -- so this half tests the fallback rather than
        # a second copy of the preference.
        model2.train_parallel = lambda *a, **kw: {"skipped": True,
                                                  "reason": "stub: no update"}
        run2 = pipeline2.run_end_to_end(source="CLUSTERWORMS", timeout=60.0,
                                        register_model=False)

        assert run2.get("routing_after_balance") is None, run2.get(
            "routing_after_balance")
        assert sink2["signals"][2] == run2["routing"]["used_fraction"], (
            "the fallback did not preserve the pre-loop used_fraction",
            sink2["signals"], run2["routing"])


# -- the third bound's TRIGGER is an equivalence, not a symptom --


def test_bias_clamp_exhausted_fires_exactly_when_out_of_authority():
    """The clamp bound is named iff the gate is out of AUTHORITY, not iterations.

    `bias_clamp_exhausted` is the one name in this vocabulary that asserts a
    MECHANISM rather than an outcome: the loop spent every nudge AND at least one
    bias sits on the `MAX_GATE_BIAS` clamp, so no further iteration can move the
    load. #528 added the name with a single fixture; what is pinned here is the
    TRIGGER, as an equivalence that has to hold in both directions:

        bound == "bias_clamp_exhausted"
            iff cap_hit
            and after.used_experts > top_k
            and any(abs(b) >= MAX_GATE_BIAS for b in gate_bias)

    The `used_experts > top_k` term is load-bearing, and it is why the statement
    is not merely "capped plus clamped": the collinear fixture is ALSO capped
    with a clamped bias (measured --
    `test_round6_top_k_pinned_keeps_precedence_over_a_clamped_bias` asserts
    exactly that), yet it must report `top_k_pinned`, because a pinned used set is
    a floor no bias can move, clamp or not. Drop that term and the two names
    collapse into one.

    Three falsifiable directions, one per fixture below: an implementation that
    named the clamp whenever `cap_hit` fails the collinear fixture; one that named
    it whenever any bias was clamped fails the live-shaped fixture, which is
    capped, wider than `top_k`, and clamped-free (the shipped-cap live shape,
    `bound=None`); one that never named it fails the clamp fixture. The three
    non-vacuity guards at the end fail this test if the fixtures ever stop
    exercising those cases, so it cannot pass by testing nothing.
    """
    mismatch, observed = [], {}
    for label, build in (
            ("clamp", _clamp_rows),
            ("live-shaped", _live_shaped_rows),
            ("collinear", lambda moe: [[1.0] * 16 for _ in range(8)]),
            ("movable", lambda moe: [list(row) for row in moe.gate] * 8)):
        moe = CuttingEdgeModel(_cfg()).moes[0]
        report = moe.rebalance(build(moe))

        # Re-derive the trigger from the module's OWN readings rather than
        # reading the name back: these are the three terms the condition uses,
        # so `expected` is independent of what the code decided.
        clamped = any(abs(b) >= MAX_GATE_BIAS for b in moe.gate_bias)
        expected = bool(report["cap_hit"]
                        and report["after"]["used_experts"] > moe.top_k
                        and clamped)
        actual = report["bound"] == "bias_clamp_exhausted"
        if actual != expected:
            mismatch.append((label, report["bound"], report["cap_hit"],
                             report["after"]["used_experts"], moe.top_k, clamped))
        observed[label] = {"actual": actual, "report": report,
                           "clamped": clamped, "top_k": moe.top_k}

    assert not mismatch, (
        "`bias_clamp_exhausted` must fire exactly when the loop is capped, the "
        "used set is wider than top_k, and a bias is on the clamp; these fixtures "
        "separate the trigger from the name: %s" % (mismatch,))

    # (1) The name is reached at all, or the equivalence above proves nothing.
    fired = [label for label, entry in observed.items() if entry["actual"]]
    assert fired, (
        "no fixture reaches `bias_clamp_exhausted`, so the equivalence is "
        "untested: %s" % (observed,))

    # (2) The unnamed, wider-than-top_k case EXISTS and stays unnamed. That is
    # precisely what makes this name mean "out of AUTHORITY" rather than "out of
    # iterations": the loop still had authority left and simply ran out of cap.
    unnamed_wider = [label for label, entry in observed.items()
                     if entry["report"]["bound"] is None
                     and not entry["clamped"]
                     and entry["report"]["after"]["used_experts"] > entry["top_k"]]
    assert unnamed_wider, (
        "no fixture is capped with a used set wider than top_k and no clamped "
        "bias, so `bias_clamp_exhausted` is not distinguished from `bound=None`")

    # (3) The precedence discriminator exists: a capped fixture that IS clamped
    # and is NOT named the clamp bound, because its used set is pinned.
    pinned_clamped = [label for label, entry in observed.items()
                      if entry["clamped"] and not entry["actual"]
                      and entry["report"]["bound"] == "top_k_pinned"]
    assert pinned_clamped, (
        "no fixture is simultaneously clamped and pinned, so the precedence the "
        "equivalence depends on is untested")
