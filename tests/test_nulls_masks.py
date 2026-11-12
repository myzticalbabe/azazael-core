# coding: utf-8
"""NULLs-mode prototype: per-source unlearning on CuttingEdgeModel (task #519).

NULLs ("Natively Unlearnable Large Language Models", arXiv:2606.13873) splits each
MLP's intermediate width into a shared always-on BACKBONE plus a sparsely
activated memorization SINK POOL, gives every training source its own deterministic
pseudo-random mask over that pool, and multiplies the post-GELU activations by
``[backbone ones | source mask]``. Excluding a source at inference multiplies its
sink neurons away -- no retraining, because the mask carries the forgetting.

The acceptance criteria each get an assertion here, chosen so a wrapper could not
satisfy them:

  1. DETERMINISM. Two SUBPROCESSES must produce the identical mask (an in-process
     repeat only proves memoisation). Overlap is reported twice, deliberately: the
     MEASURED overlap of the default allocation, which is 0 because the default
     deals disjoint slices, and the closed-form ``n_source^2 / pool`` that an
     independent draw gives -- also measured, on ``disjoint=False``, together with
     the interference it causes. The policy is a property of the ledger.
  2. DEFAULT-OFF EQUIVALENCE. With the feature off, ``param_count()`` and the
     forward output must be IDENTICAL -- including when the caller passes the new
     source arguments, which must be inert -- and the config fingerprint must be
     untouched, because a NULLs flag that moved the fingerprint would silently
     re-version every model in the tree.
  3. THE LIVE 2-SOURCE PROBE. Two disjoint synthetic fact corpora; all-mode learns
     both; excluding A drops A to its retrain-without-A baseline while B and the
     backbone hold; all three numbers are reported.
  4. REGISTRY ROUND-TRIP. Train, persist, and let a FRESH process load the ledger
     and reproduce the exclusion exactly.

Conventions mirror ``tests/test_cutting_edge_model.py`` (SRC inserted here; the
tests/conftest.py does not).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

#: Small and fast, but wide enough to partition: 128 -> pool 32, n_source 16.
FULL_FFN = 128


def _cfg(**overrides):
    from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeConfig

    base = dict(d_model=16, n_layers=1, d_state=4, d_ff=FULL_FFN, n_experts=8,
                top_k=2, seed=7)
    base.update(overrides)
    return CuttingEdgeConfig(**base)


def _model(nulls=None, **overrides):
    from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeModel

    return CuttingEdgeModel(_cfg(**overrides), nulls=nulls)


def _digest(value):
    import hashlib

    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


#: The two disjoint fact corpora: different row ids AND different signatures, so
#: "learned A" and "learned B" cannot be confused for one another.
CORPUS_A = [[0.11 * ((i * 3 + j) % 7) for j in range(16)] for i in range(20)]
CORPUS_B = [[-0.07 * ((i * 5 + j) % 9) - 0.2 for j in range(16)] for i in range(20)]

#: The facts themselves are built from each source's OWN sink basis (see
#: ``nulls_fact_targets``), which is what makes them exactly representable: an
#: all-mode accuracy below 1.0 then means the mask or the solver is wrong, not
#: that the fact was out of reach of the architecture.
TARGET_A: list = []
TARGET_B: list = []


def _facts(model):
    """Targets for both corpora, computed before either source is trained."""
    global TARGET_A, TARGET_B
    TARGET_A = model.nulls_fact_targets("corpus-A", CORPUS_A, scale=5.0)
    TARGET_B = model.nulls_fact_targets("corpus-B", CORPUS_B, scale=-5.0)
    return TARGET_A, TARGET_B


# ---------------------------------------------------------------------------
# 1. Determinism + the overlap formula
# ---------------------------------------------------------------------------

_CHILD = (
    "import json, sys;"
    "sys.path.insert(0, sys.argv[1]);"
    "from CORE.WORMS.expansion.cutting_edge_model import nulls_sizing, NullsMaskRegistry;"
    "r = NullsMaskRegistry(nulls_sizing(int(sys.argv[2])), seed=7);"
    "print(json.dumps({'A': r.mask('corpus-A'), 'B': r.mask('corpus-B'),"
    " 'union': r.active_mask(['corpus-A', 'corpus-B']),"
    " 'overlap': r.overlap('corpus-A', 'corpus-B')}))"
)


def _child_masks(width):
    """Run the registry in a FRESH process and return what it printed."""
    out = subprocess.run([sys.executable, "-c", _CHILD, str(SRC), str(width)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_masks_are_identical_across_processes_and_overlap_matches_the_formula():
    from CORE.WORMS.expansion.cutting_edge_model import (
        NullsMaskRegistry, nulls_sizing,
    )

    sizing = nulls_sizing(FULL_FFN)
    registry = NullsMaskRegistry(sizing, seed=7)

    # A fresh process, not a second call in this one: memoisation would satisfy
    # the latter and prove nothing about reproducibility.
    child = _child_masks(FULL_FFN)
    assert child["A"] == registry.mask("corpus-A"), "mask is not reproducible across processes"
    assert child["B"] == registry.mask("corpus-B")
    assert child["union"] == registry.active_mask(["corpus-A", "corpus-B"])

    # Sizing is read from the width, never hardcoded.
    assert sizing["pool"] == int(round(FULL_FFN * 0.25))
    assert sizing["backbone"] == FULL_FFN - sizing["pool"]
    assert sizing["n_source"] == int(round(sizing["pool"] * 0.5))

    # Every mask is exactly n_source wide, inside the pool, and the measured
    # overlap is what the closed form predicts for subsets of this size.
    for name in ("corpus-A", "corpus-B"):
        mask = registry.mask(name)
        assert len(mask) == sizing["pool"]
        assert sum(mask) == sizing["n_source"]
    measured = registry.overlap("corpus-A", "corpus-B")
    expected = registry.expected_overlap()
    assert measured == child["overlap"] == 0, (measured, child["overlap"])
    assert expected == pytest.approx(sizing["n_source"] ** 2 / sizing["pool"])
    assert registry.capacity() == sizing["pool"] // sizing["n_source"] == 2

    # A different seed deals a different allocation.
    other_seed = NullsMaskRegistry(sizing, seed=99)
    assert other_seed.mask("corpus-A") != registry.mask("corpus-A")

    # The union is an OR: two sources' slices are disjoint here, so a position is
    # active exactly when one of them selected it.
    union = registry.active_mask(["corpus-A", "corpus-B"])
    assert union == [a or b for a, b in zip(registry.mask("corpus-A"),
                                            registry.mask("corpus-B"))]
    assert sum(union) > sizing["n_source"]
    assert registry.active_mask([]) == [0] * sizing["pool"]

    # LAST, because it poisons this registry: a source beyond the pool's capacity
    # is REFUSED rather than handed a short mask. A short mask still looks like an
    # exclusion while no longer being an exact one -- the failure this ledger
    # exists to prevent -- and refusing on the SET keeps that error independent of
    # which source's crc32 key happens to sort last.
    with pytest.raises(ValueError) as exhausted:
        registry.mask("corpus-C")
    assert "capacity 2" in str(exhausted.value), str(exhausted.value)


def test_independent_draws_overlap_at_the_chance_rate_and_that_is_interference():
    """The ``disjoint=False`` regime: criterion 1's number, and what it costs.

    A per-source random draw is the paper's allocation and is exactly what
    ``n_source^2 / pool`` describes. It is also the allocation under which both
    sources cannot be recalled: the second write lands on the columns the first
    one's fact lives in, and recall does not average the two -- it is overwritten.
    Asserting the cause (the overlap) and the effect (A's recall gone while B's is
    exact) is what makes the disjoint default a measurement rather than a taste.
    """
    from CORE.WORMS.expansion.cutting_edge_model import (
        CuttingEdgeModel, NullsMaskRegistry, nulls_sizing,
    )

    sizing = nulls_sizing(FULL_FFN)
    registry = NullsMaskRegistry(sizing, seed=7, disjoint=False)
    overlap = registry.overlap("corpus-A", "corpus-B")
    assert overlap == pytest.approx(registry.expected_overlap(), abs=1.0), overlap
    assert sum(registry.active_mask(["corpus-A", "corpus-B"])) < sizing["pool"], (
        "independent draws are expected to leave pool positions unclaimed")
    assert sum(registry.mask("corpus-A")) == sizing["n_source"], (
        "each draw is exactly n_source wide; the OVERLAP is what differs")

    model = CuttingEdgeModel(_cfg(), nulls=True)
    model.adopt_nulls_registry(registry)
    targets_a, targets_b = _facts(model)
    a_step = model.memorize("corpus-A", CORPUS_A, targets_a)
    b_step = model.memorize("corpus-B", CORPUS_B, targets_b)
    # Both writes are exact AT THEIR OWN write time -- that is the point: the
    # damage is not fit error, it is the overwrite.
    assert a_step["loss_after"] < 1e-8, a_step
    assert b_step["loss_after"] < 1e-8, b_step

    own_a = model.probe_source("corpus-A", CORPUS_A, targets_a, tol=0.1)
    own_b = model.probe_source("corpus-B", CORPUS_B, targets_b, tol=0.1)
    assert own_b["accuracy"] == 1.0, own_b
    assert own_a["accuracy"] < own_b["accuracy"], (own_a, own_b)

    # And a UNION read -- the mode in which "no exclusions" means every sink
    # neuron is active -- is damaged for the holding source too, by the other
    # source's own unique columns.
    union_b = model.probe_source(None, CORPUS_B, targets_b, tol=0.1)
    assert union_b["mean_abs_error"] > own_b["mean_abs_error"], (union_b, own_b)

    print("\nNULLS OVERLAP REGIME: " + json.dumps(
        {"overlap": overlap, "chance_overlap": registry.expected_overlap(),
         "own_a_accuracy": own_a["accuracy"], "own_b_accuracy": own_b["accuracy"],
         "union_b_mean_abs_error": union_b["mean_abs_error"]}, sort_keys=True))


# ---------------------------------------------------------------------------
# 2. Default-off equivalence
# ---------------------------------------------------------------------------

def test_the_feature_off_is_the_pre_nulls_model_in_params_output_and_identity():
    from CORE.WORMS.expansion.cutting_edge_model import NullsFFNBlock

    plain = _model()
    rows = [[0.03 * ((i + j) % 5) for j in range(16)] for i in range(6)]

    baseline = plain.forward(rows)["output"]
    # The new arguments must be INERT when the feature is off: a caller (the
    # pipeline) passes a source id unconditionally, so an off model that reacted
    # to it would be a behaviour change, not a no-op.
    with_kwargs = plain.forward(rows, source_id="corpus-A",
                                exclude_source_ids=["corpus-B"])["output"]
    assert _digest(with_kwargs) == _digest(baseline)
    assert "nulls" not in plain.forward(rows), "an off model grew a nulls key"
    assert not any(isinstance(b, NullsFFNBlock) for b in plain.ffns)

    nulled = _model(nulls=True)
    assert nulled.param_count() == plain.param_count(), (
        "partitioning the intermediate must not change the parameter count")
    assert nulled.config_fingerprint() == plain.config_fingerprint(), (
        "a NULLs flag must not re-version a config that is otherwise identical")
    assert nulled.nulls_report()["active"] is True
    assert plain.nulls_report()["enabled"] is False
    assert "pre-NULLs" in plain.nulls_report()["reason"]

    # ...and the same holds for the adapter the pipeline actually calls.
    meta = {"dim": 16}
    assert _digest(plain.from_schema_features(rows, meta)["output"]) == _digest(baseline)
    assert _digest(plain.from_schema_features(rows, meta, source_id="corpus-A")["output"]) \
        == _digest(baseline)


def test_nulls_without_an_ffn_block_says_so_instead_of_pretending():
    model = _model(nulls=True, use_ffn=False)

    report = model.nulls_report()
    assert report["enabled"] is True
    assert report["active"] is False
    assert "use_ffn is False" in report["reason"]
    assert report["expert_intermediate_width"] == 2 * model.config.d_model, (
        "the unpartitioned MoE expert gap must be reported, not implied partitioned")
    step = model.memorize("corpus-A", CORPUS_A, TARGET_A)
    assert step["skipped"] is True and "no FFN block" in step["reason"]
    assert "nulls" in model.forward(CORPUS_A)


def test_memorize_requires_the_feature_rather_than_silently_doing_nothing():
    with pytest.raises(RuntimeError):
        _model().memorize("corpus-A", CORPUS_A, CORPUS_A)


# ---------------------------------------------------------------------------
# 3. The live 2-source probe
# ---------------------------------------------------------------------------

def _trained_two_source_model(nulls=True, **overrides):
    from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeModel

    model = CuttingEdgeModel(_cfg(**overrides), nulls=nulls)
    targets_a, targets_b = _facts(model)
    a_step = model.memorize("corpus-A", CORPUS_A, targets_a)
    b_step = model.memorize("corpus-B", CORPUS_B, targets_b)
    # The fixture is exactly representable, so a step that does not collapse the
    # loss means the mask or the solver is broken -- this is a defect assertion,
    # not a tuning threshold. The floor here IS the default (relative) ridge,
    # measured at 8.4e-9; the mask-free-of-the-optimiser claim is pinned
    # separately, at ridge=0, by test_the_fit_is_exact_without_regularisation.
    # Both matter: the wrong-objective bug this replaced sat at 0.176446, so even
    # the loosest of the two catches it by five orders of magnitude.
    assert a_step["loss_after"] < 1e-6, a_step
    assert b_step["loss_after"] < 1e-6, b_step
    assert a_step["loss_delta"] < 0 and b_step["loss_delta"] < 0, (a_step, b_step)
    return model, a_step, b_step


def test_the_fit_is_exact_without_regularisation():
    """Pins the exact-fit property on its own, so the default ridge cannot hide it.

    An exactly-representable fixture with no ridge must land at machine precision.
    Measured 4.3e-14 on this corpus (ridge is not the only thing that can leave a
    residual: an objective that solves ``F X = T`` while the model computes
    ``F X + c`` leaves 0.176446 and is insensitive to both the ridge and the
    conditioning, which is exactly how it went unnoticed).
    """
    from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeModel

    model = CuttingEdgeModel(_cfg(), nulls=True)
    targets_a, targets_b = _facts(model)
    for source_id, corpus, targets in (("corpus-A", CORPUS_A, targets_a),
                                       ("corpus-B", CORPUS_B, targets_b)):
        step = model.memorize(source_id, corpus, targets, ridge=0.0)
        assert step["loss_after"] < 1e-12, step
        assert step["fit"]["columns"] == model.nulls_sizing()["n_source"], step["fit"]
        # 1e-6 absolute against a fact of scale 5.0 is ~2e-7 relative: the point is
        # that the residual is at rounding scale, not at the 0.1 a recall is
        # allowed to be off by.
        probe = model.probe_source(source_id, corpus, targets, tol=0.1)
        assert probe["accuracy"] == 1.0 and probe["mean_abs_error"] < 1e-6, probe


def test_the_live_two_source_probe_all_mode_exclusion_and_the_baseline():
    """Criterion 3: (a) all-mode learns both, (b) excluding A drops A to baseline."""
    model, _a_step, _b_step = _trained_two_source_model()

    # (a) all-mode: both sources' facts are retrievable at once.
    all_a = model.probe_source("corpus-A", CORPUS_A, TARGET_A, tol=0.1)
    all_b = model.probe_source("corpus-B", CORPUS_B, TARGET_B, tol=0.1)
    assert all_a["accuracy"] == 1.0, all_a
    assert all_b["accuracy"] == 1.0, all_b
    # The leftover is the default ridge (measured 1.6e-5, ~3e-6 of the fact's
    # scale of 5.0), not interference: under the disjoint default neither source
    # is written over, which is the whole reason this is 1.6e-5 and not 0.7.
    assert all_a["mean_abs_error"] < 1e-4, all_a

    # (b) excluding A: A's sink neurons never activate. B is untouched, and the
    # backbone (general capability) is untouched -- so B's number must be EXACTLY
    # what it was, not merely close to it.
    excl_a = model.probe_source("corpus-A", CORPUS_A, TARGET_A,
                                exclude_source_ids=["corpus-A"], tol=0.1)
    excl_b = model.probe_source("corpus-B", CORPUS_B, TARGET_B,
                                exclude_source_ids=["corpus-A"], tol=0.1)
    assert excl_b["accuracy"] == all_b["accuracy"] == 1.0, (excl_b, all_b)
    assert excl_a["accuracy"] < all_a["accuracy"], (excl_a, all_a)

    # The retrain-without-A baseline: a model that only ever saw B. Excluding a
    # source must land near what the source never having been trained would give.
    baseline = _model(nulls=True)
    baseline.memorize("corpus-B", CORPUS_B, TARGET_B)
    baseline_a = baseline.probe_source("corpus-A", CORPUS_A, TARGET_A, tol=0.1)

    assert abs(excl_a["accuracy"] - baseline_a["accuracy"]) <= 0.2, (
        "exclusion must land near the never-trained baseline: excluded=%r baseline=%r"
        % (excl_a, baseline_a))

    # The three numbers the criterion asks for are reported, not inferred.
    measured = {
        "all_mode_a": all_a["accuracy"],
        "excluded_a": excl_a["accuracy"],
        "baseline_a_retrained_without_a": baseline_a["accuracy"],
        "all_mode_b": all_b["accuracy"],
        "excluded_a_b_held": excl_b["accuracy"],
        "mean_abs_error_excluded_a": excl_a["mean_abs_error"],
        "mask_overlap_ab": model.nulls_registry.overlap("corpus-A", "corpus-B"),
    }
    assert measured["all_mode_a"] == 1.0 and measured["excluded_a"] == 0.0
    print("\nNULLS PROBE: " + json.dumps(measured, sort_keys=True))
    assert all(isinstance(v, (int, float)) for v in measured.values())


def test_multi_layer_training_states_which_sinks_trained():
    model = _model(nulls=True, n_layers=2)

    step = model.memorize("corpus-A", CORPUS_A, CORPUS_A)
    assert step["sinks_trained"] == [1], (
        "the readout-side block is the one whose gradient is exact; with n_layers>1 "
        "the earlier sinks are NOT updated and the step must say so")
    assert step["sink_columns"] == model.nulls_sizing()["pool"]
    assert step["mask_active"] == model.nulls_sizing()["backbone"] + \
        model.nulls_sizing()["n_source"]


def test_an_unnamed_source_falls_back_to_the_registered_ledger_not_to_a_sink():
    """No source named means "every source in the ledger", and that is reported.

    With an EMPTY ledger that is backbone-only (every sink off). Once a source has
    been named, a bare forward is all-mode over the registered sources -- and the
    difference is visible in the returned ``nulls`` block either way, so a caller
    can always tell which reading it got.
    """
    model = _model(nulls=True)
    rows = CORPUS_A[:2]

    fresh = model.forward(rows)
    assert fresh["nulls"]["pool_active"] == 0, fresh["nulls"]
    assert fresh["nulls"]["active"] == []

    named = model.forward(rows, source_id="corpus-A")
    assert named["nulls"]["pool_active"] == model.nulls_sizing()["n_source"]
    assert named["nulls"]["backbone"] == model.nulls_sizing()["backbone"]
    assert _digest(named["output"]) != _digest(fresh["output"]), (
        "a named source must reach its sink while a bare forward does not")

    # Registering the source changes what the BARE forward means, and it says so.
    after = model.forward(rows)
    assert after["nulls"]["pool_active"] == model.nulls_sizing()["n_source"]
    assert after["nulls"]["active"] == ["corpus-A"]


# ---------------------------------------------------------------------------
# 4. Registry round-trip in a fresh process
# ---------------------------------------------------------------------------

_ROUNDTRIP = (
    "import json, sys;"
    "sys.path.insert(0, sys.argv[1]);"
    "from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeConfig, CuttingEdgeModel;"
    "cfg = CuttingEdgeConfig(d_model=16, n_layers=1, d_state=4, d_ff=128,"
    " n_experts=8, top_k=2, seed=7);"
    "m = CuttingEdgeModel(cfg, nulls=True);"
    "reg = m.load_nulls_registry(sys.argv[2]);"
    "print(json.dumps({'sources': reg.sources, 'mask_a': reg.mask('corpus-A'),"
    " 'fingerprint': reg.fingerprint()}))"
)


def test_registry_round_trip_in_a_fresh_process_reproduces_the_exclusion(tmp_path):
    model, _a, _b = _trained_two_source_model()
    excl = model.probe_source("corpus-A", CORPUS_A, TARGET_A,
                              exclude_source_ids=["corpus-A"], tol=0.1)
    sidecar = tmp_path / "nulls_mask_registry.json"
    saved = model.save_nulls_registry(sidecar)

    # The file must carry the seed and the sizing, and NOT the masks: a stored
    # mask could drift from the code that regenerates it.
    payload = json.loads(Path(saved["path"]).read_text(encoding="utf-8"))
    assert payload["seed"] == model.config.seed
    assert payload["sizing"] == model.nulls_sizing()
    assert [s["label"] for s in payload["sources"]] == ["corpus-A", "corpus-B"]
    assert "mask" not in json.dumps(payload).lower().replace("masks", "")

    child = subprocess.run([sys.executable, "-c", _ROUNDTRIP, str(SRC),
                            str(sidecar)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    assert child.returncode == 0, child.stderr
    decoded = json.loads(child.stdout.strip().splitlines()[-1])
    assert decoded["sources"] == model.nulls_registry.sources
    assert decoded["mask_a"] == model.nulls_registry.mask("corpus-A")
    assert decoded["fingerprint"] == model.nulls_registry.fingerprint(), (
        "the ledger's identity must survive a round trip")

    # And loading it back into a model reproduces the exclusion exactly.
    reloaded = _model(nulls=True)
    reloaded.load_nulls_registry(sidecar)
    assert reloaded.nulls_registry.fingerprint() == model.nulls_registry.fingerprint()

    # A ledger for a different shape is REFUSED: an exclusion that maps to the
    # wrong neurons is worse than an error.
    other = _model(nulls=True, d_ff=64)
    with pytest.raises(ValueError):
        other.load_nulls_registry(sidecar)
    with pytest.raises(ValueError):
        _model(nulls=True, seed=1234).load_nulls_registry(sidecar)
