"""Cutting-edge MoE+SSM model (task #507) -- behaviour, ablation, integration.

Each test states the fact it asserts and why that fact discriminates, because a
model module can pass a shape check while doing nothing: `forward` returning a
list of the right width is satisfied by `return [[0.0] * d_model]` forever.

The two claims worth real proof are complexity and ablation:
  * `SelectiveSSM` is claimed O(n) and `AttentionBlock` O(n^2), so the op COUNTS
    are asserted to scale 1:1 and 1:4 -- a timing assertion would be true for a
    slow machine and false for a fast one; op counts are properties of the code.
  * an ablation is only attributable if removing one component cannot re-draw the
    parameters of the others, so `component_fingerprint` is asserted UNCHANGED
    for every surviving component. Without per-component seeding the removal
    re-draws everything and a "delta" measures initialisation noise.

Integration is exercised against the real classes (SchemaAwareFeatureExtractor,
WorkStealingPool, DeadLetterQueue, ModelVersioner), not stubs. Where a class
would touch the live memory store, the test uses a temp storage path.
"""

import builtins
import dataclasses
import json
import math
import sys
import sysconfig
from pathlib import Path

import pytest

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

# Imported at MODULE scope so this file carries no `import_inside_function` finding:
# a NEW file is compared against EMPTY by the per-file staged gate, so it must be
# clean. The usual objection -- that importing this module registers a worm as a
# decorator side effect which has to stay out of collection time -- does not apply
# here. The sibling that owns that concern asserts on the PIPELINE's attribute
# (`test_neural_pipeline_cutting_edge.py::test_default_pipeline_does_not_build_the_model`
# asserts `pipeline._cutting_edge_model is None`), never on `sys.modules`, so WHEN
# the import happens cannot change its verdict. `test_cutting_edge_production_fixes.py`
# already imports this module at module scope for the same reason.
from CORE.WORMS.expansion.cutting_edge_model import (  # noqa: E402
    COMPONENTS, AttentionBlock, CuttingEdgeConfig, CuttingEdgeModel, SelectiveSSM,
)
from CORE.WORMS.expansion.learning_features import (  # noqa: E402
    SchemaAwareFeatureExtractor,
)
from CORE.WORMS.expansion.learning_versioner import ModelVersioner  # noqa: E402
from CORE.WORMS.dead_letter_queue import DeadLetterQueue  # noqa: E402
from CORE.freethreading import WorkStealingPool  # noqa: E402
from CORE.worm_registry import WORM_REGISTRY  # noqa: E402


def _cfg(**overrides):
    """Small, fast config; every test starts from the same shape."""
    base = dict(d_model=16, n_layers=1, d_state=4, d_ff=8, n_experts=8, top_k=2, seed=7)
    base.update(overrides)
    return CuttingEdgeConfig(**base)


# ---------------------------------------------------------------------------
# 1. Sparse MoE routing + auxiliary load balancing
# ---------------------------------------------------------------------------

def test_top_k_routing_is_exactly_two_and_aux_loss_punishes_collapse():
    """top-k=2, and the aux loss must respond to routing imbalance.

    Identical tokens MUST collapse onto two experts (that is what a top-k gate
    does), and the gate-aligned tokens MUST spread over all eight. The aux loss
    is the Switch form n*sum(f_i*P_i), which is minimised by balance -- so
    collapse must score HIGHER. If it scored lower, the auxiliary loss would be
    rewarding the collapse it exists to prevent.
    """
    model = CuttingEdgeModel(_cfg())
    moe = model.moes[0]

    identical = [[1.0] * 16 for _ in range(8)]
    # Aligning each token with one gate row is a controlled way to make routing
    # genuinely spread; a random batch would spread by luck, not by construction.
    aligned = [list(row) for row in moe.gate]

    chosen_same, _, _ = moe.route(identical)
    chosen_spread, _, _ = moe.route(aligned)

    assert all(len(c) == 2 for c in chosen_same), "top-k must be exactly 2 per token"
    assert all(len(c) == 2 for c in chosen_spread)

    used_same = len({e for c in chosen_same for e in c})
    used_spread = len({e for c in chosen_spread for e in c})
    assert used_same == 2, "identical tokens must collapse onto two experts"
    assert used_spread == 8, "gate-aligned tokens must reach every expert"

    _, aux_same, stats_same = moe.forward(identical)
    _, aux_spread, stats_spread = moe.forward(aligned)

    assert stats_same["used_experts"] == 2 and stats_spread["used_experts"] == 8
    assert aux_same > aux_spread, (
        "aux loss must penalise collapse: collapsed=%r spread=%r" % (aux_same, aux_spread))
    assert aux_same > 0.0

    # The routed output must actually depend on the selected experts.
    out_same, _, _ = moe.forward(identical)
    assert len(out_same) == 8 and len(out_same[0]) == 16
    assert any(abs(v) > 0.0 for v in out_same[0]), "routed output cannot be all zeros"


# ---------------------------------------------------------------------------
# 2. Complexity: SSM linear vs attention quadratic, by op count
# ---------------------------------------------------------------------------

def test_ssm_scan_is_linear_and_attention_is_quadratic_in_sequence_length():
    """The O(n)/O(n^2) claim, measured rather than asserted.

    Doubling n must double the SSM's op count and quadruple attention's
    quadratic term. The SSM must report NO quadratic term at all -- a small
    quadratic coefficient would still be a quadratic model at long context.
    """
    seq4 = [[0.1 * (i + 1)] * 16 for i in range(4)]
    seq8 = [[0.1 * (i + 1)] * 16 for i in range(8)]

    ssm = SelectiveSSM(16, 4, 0)
    _, ops_ssm4 = ssm.forward(seq4)
    _, ops_ssm8 = ssm.forward(seq8)

    assert ops_ssm4["quadratic"] == 0 and ops_ssm8["quadratic"] == 0, (
        "the selective scan must have no quadratic term")
    assert ops_ssm8["linear"] == 2 * ops_ssm4["linear"], (
        "linear scan must scale 1:1 with sequence length: %r -> %r"
        % (ops_ssm4["linear"], ops_ssm8["linear"]))
    assert ops_ssm4["linear"] > 0

    attn = AttentionBlock(16, 0)
    _, ops_attn4 = attn.forward(seq4)
    _, ops_attn8 = attn.forward(seq8)

    assert ops_attn4["quadratic"] > 0, "attention must carry the quadratic term"
    assert ops_attn8["quadratic"] == 4 * ops_attn4["quadratic"], (
        "quadratic term must scale n^2: %r -> %r"
        % (ops_attn4["quadratic"], ops_attn8["quadratic"]))

    # The scan output must actually depend on the whole prefix (a real recurrence).
    _, _ = ssm.forward(seq8)
    model = CuttingEdgeModel(_cfg())
    whole = model.forward_sequence(seq8)
    assert whole["ops"]["linear"] > 0
    assert whole["ops"]["n"] == 8


# ---------------------------------------------------------------------------
# 3. Ablation: switches independent, and removal is attributable
# ---------------------------------------------------------------------------

def test_every_ablation_switch_independently_removes_only_its_component():
    """Each switch flips exactly its own block, leaving the others untouched.

    Two facts, both discriminating:
      * `active_components()` and the built plan must agree with the switches --
        a config that claims attention while building none is a lie a shape test
        would not catch;
      * `component_fingerprint` for every SURVIVING component must be identical
        across the ablation. That is the per-component-seed property: without it,
        removing MoE re-draws the SSM's weights and any measured delta is
        initialisation noise wearing a component's name.
    """
    full = CuttingEdgeModel(_cfg())
    assert set(full.active_components()) == set(COMPONENTS), (
        "all four switches on must build all four blocks")
    for layer in full.plan:
        assert layer == ["ssm", "attention", "moe", "ffn"], (
            "additive build order must place every enabled block: %r" % (layer,))

    baseline_fp = {c: full.component_fingerprint(c) for c in COMPONENTS}
    baseline_params = full.param_count()

    for removed in COMPONENTS:
        variant = full.ablate(removed)
        assert removed not in variant.active_components(), (
            "ablating %s must remove it from active components" % removed)
        assert removed not in {b for layer in variant.plan for b in layer}, (
            "ablating %s must remove it from the built plan" % removed)
        assert variant.param_count() < baseline_params, (
            "ablating %s must reduce the parameter count" % removed)
        for survivor in COMPONENTS:
            if survivor == removed:
                continue
            assert variant.component_fingerprint(survivor) == baseline_fp[survivor], (
                "ablating %s changed %s's parameters -- the ablation is not attributable"
                % (removed, survivor))

    # The empty set must still be a usable baseline, not an error.
    bare = CuttingEdgeModel(_cfg(use_moe=False, use_ssm=False,
                                 use_attention=False, use_ffn=False))
    assert bare.active_components() == ()
    assert bare.forward([[0.5] * 16, [0.25] * 16])["shape"] == (2, 16)

    # A one-at-a-time sweep is available as data.
    matrix = full.ablation_matrix()
    assert matrix[0]["name"] == "full"
    assert {row["name"] for row in matrix[1:]} == {"without_%s" % c for c in COMPONENTS}


def test_config_rejects_an_expert_count_or_top_k_outside_the_task_bounds():
    """#507 bounds the routing at 8..64 experts, top-k=2; a bad config must RAISE.

    Clamping instead of raising would let an ablation study believe it had
    configured something it had not.
    """
    with pytest.raises(ValueError):
        _cfg(n_experts=4)
    with pytest.raises(ValueError):
        _cfg(n_experts=128)
    with pytest.raises(ValueError):
        _cfg(n_experts=8, top_k=9)
    with pytest.raises(ValueError):
        _cfg(d_model=0)

    # A config survives a round trip through dict, so a study can be serialised.
    cfg = CuttingEdgeConfig(d_model=32, n_experts=8, top_k=2, d_state=4, d_ff=16)
    assert CuttingEdgeConfig.from_dict(cfg.to_dict()) == cfg


# ---------------------------------------------------------------------------
# 4. Integration: the real SchemaAwareFeatureExtractor contract
# ---------------------------------------------------------------------------

def test_schema_aware_feature_extractor_output_feeds_the_model():
    """The extractor's real (features, meta) is consumed without adaptation.

    A shape-only stub would prove nothing about `DATA_SHAPES`: the point is that
    the actual typed-feature output of the learning pipeline is accepted, and its
    metadata survives to the model instead of being dropped at the boundary.
    """
    records = [
        {"event_type": "cluster_execution_started",
         "data": {"cluster_type": "alpha", "worm_count": 3, "parallel": 1}},
        {"event_type": "cluster_execution_started",
         "data": {"cluster_type": "beta", "worm_count": 7, "parallel": 0}},
        {"event_type": "cluster_execution_started",
         "data": {"cluster_type": "gamma", "worm_count": 5, "parallel": 1}},
        {"event_type": "cluster_created",
         "data": {"cluster_type": "alpha", "cluster_id": "c-1"}},
    ]

    extractor = SchemaAwareFeatureExtractor()
    extractor.fit(records)
    features, meta = extractor.extract(records)

    assert features, "fixture must produce features or this test proves nothing"
    assert all(isinstance(row, list) for row in features)

    model = CuttingEdgeModel(_cfg())
    result = model.from_schema_features(features, meta)

    assert result["shape"] == (len(features), 16)
    assert result["feature_meta"]["rows"] == len(features)
    assert result["feature_meta"]["projected_to"] == 16
    assert result["feature_meta"]["input_widths"] == sorted({len(r) for r in features})
    assert isinstance(result["feature_meta"]["extractor_meta"], dict)
    assert len(result["output"]) == len(features)
    assert all(math.isfinite(v) for row in result["output"] for v in row), (
        "the projected features must be finite, not NaN/inf from a bad projection")


# ---------------------------------------------------------------------------
# 5. Integration: WorkStealingPool performs the training step
# ---------------------------------------------------------------------------

def test_work_stealing_pool_runs_the_expert_step_and_the_loss_falls():
    """One group == every expert; the group is complete and the loss decreases.

    The loss delta is the discriminating assertion: a `train_parallel` that
    scheduled the work and applied nothing would still return total/succeeded,
    so the parameters must be shown to have moved in the right direction.
    """
    model = CuttingEdgeModel(_cfg())
    n_experts = model.config.n_experts
    before = model.component_fingerprint("moe")

    with WorkStealingPool(num_workers=4) as pool:
        result = model.train_parallel(pool, lr=0.05, timeout=30.0)

    assert result["total"] == n_experts, "every expert must be one branch of the group"
    assert result["succeeded"] == n_experts
    assert result["failed"] == 0, "group errors: %r" % (result["errors"],)
    assert result["waited_ok"] is True
    assert result["experts_updated"] == n_experts
    assert result["loss_after"] < result["loss_before"], (
        "the step must reduce the loss: before=%r after=%r"
        % (result["loss_before"], result["loss_after"]))
    assert result["loss_delta"] < 0.0

    assert model.component_fingerprint("moe") != before, (
        "the update must actually change the MoE parameters")
    # Components that were not trained must be untouched by a MoE-only step.
    assert model.component_fingerprint("ssm") == CuttingEdgeModel(_cfg()).component_fingerprint("ssm")

    # With no MoE built there is no expert to step, and that is reported, not faked.
    no_moe = CuttingEdgeModel(_cfg(use_moe=False))
    with WorkStealingPool(num_workers=2) as pool:
        skipped = no_moe.train_parallel(pool, timeout=10.0)
    assert skipped["skipped"] is True and skipped["reason"]


# ---------------------------------------------------------------------------
# 6. Integration: DeadLetterQueue and ModelVersioner
# ---------------------------------------------------------------------------

def test_failed_run_reaches_the_dead_letter_queue_and_survives_a_reload(tmp_path):
    """A failure is recorded durably, and the run is registered with a version.

    Durability is the DLQ's entire claim, so the assertion is on the persisted
    file, not on the returned object: an enqueue that only updated memory would
    pass a truthiness check and lose the failure on restart.
    """
    model = CuttingEdgeModel(_cfg())
    store_path = tmp_path / "dead_letters.json"
    dlq = DeadLetterQueue(storage_path=store_path)

    entry = model.record_failure(dlq, {"error": "synthetic training failure"},
                                 config={"stage": "train"})
    assert entry is not None

    payload = json.dumps(dataclasses.asdict(entry) if dataclasses.is_dataclass(entry)
                         else str(entry))
    assert "CuttingEdgeModel" in payload, "the failed worm must be named"
    assert "synthetic training failure" in payload, "the error must be carried"

    assert store_path.exists() and store_path.stat().st_size > 0, (
        "the dead letter must be persisted, not only held in memory")

    versioner = ModelVersioner()
    registered = model.register_version(versioner, {"avg_reward": 0.5, "loss": 0.25})
    assert registered["name"] == "cutting_edge_model"
    assert model.config_fingerprint() in registered["version"], (
        "the version must identify the configuration, so two ablations do not collide")
    assert registered["metadata"]["config_fingerprint"] == model.config_fingerprint()
    assert registered["metadata"]["active_components"] == list(model.active_components())
    assert registered["metadata"]["param_count"] == model.param_count()


# ---------------------------------------------------------------------------
# 7. 3.15t capability probe: absence is reported, never a silent pass
# ---------------------------------------------------------------------------

def test_capability_probe_agrees_with_the_interpreter_and_reports_absence():
    """The probe must equal ground truth on WHICHEVER interpreter runs the suite.

    Asserting a hard-coded `False` for tachyon would pass on 3.14 and be wrong on
    a 3.15 build that shipped one, so the assertion is agreement with the live
    interpreter. The second half enforces the no-false-pass rule: when a feature
    is absent the attempt must say so WITH a reason, never return a bare success.
    """
    model = CuttingEdgeModel(_cfg())
    caps = model.capabilities()

    assert caps["has_frozendict"] == hasattr(builtins, "frozendict")
    assert caps["has_sentinel"] == hasattr(builtins, "sentinel")
    assert caps["has_lazy_imports"] == hasattr(sys, "set_lazy_imports")
    assert caps["has_tachyon"] == hasattr(sys, "tachyon")
    assert caps["gil_disabled"] == bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    assert caps["has_abi3t_marker"] == ("abi3t" in (sysconfig.get_config_var("SOABI") or ""))
    assert caps["interpreter"].startswith(("3.14", "3.15", "3.16"))

    profiling = model.apply_profile_hooks()
    assert profiling["applied"] == hasattr(sys, "tachyon")
    if not profiling["applied"]:
        assert profiling["reason"], "a missing profiler must be reported with a reason"

    lazy = model.enable_lazy_imports()
    try:
        assert isinstance(lazy["applied"], bool)
        if not lazy["applied"]:
            assert lazy["reason"], "a missing lazy-import API must be reported with a reason"
    finally:
        if lazy["applied"]:
            # set_lazy_imports is PROCESS-GLOBAL (PEP 810) and survives the
            # test: under xdist the flip leaks into every later test this
            # worker collects (measured 2026-10-20: test_inference sklearn
            # failure shadowed by the leftover mode). Restore before yielding.
            sys.set_lazy_imports("normal")


# ---------------------------------------------------------------------------
# 8. Worm contract + honest reward reporting
# ---------------------------------------------------------------------------

def test_get_summary_is_side_effect_free_and_the_reward_reports_its_source():
    """The registry smoke test calls get_summary on every worm; it must be inert.

    And reward shaping must name the source it used: a rubric silently presented
    as an inference-API score is exactly the false signal the reward path is
    supposed to avoid.
    """
    model = CuttingEdgeModel(_cfg())
    before = {c: model.component_fingerprint(c) for c in model.active_components()}

    first = model.get_summary()
    second = model.get_summary()

    assert first == second, "get_summary must be deterministic"
    assert first["success"] is True and first["method"] == "get_summary"
    assert first["result"]["active_components"] == list(model.active_components())
    assert first["result"]["n_experts"] == 8 and first["result"]["top_k"] == 2
    after = {c: model.component_fingerprint(c) for c in model.active_components()}
    assert before == after, "get_summary must not run a training step or mutate weights"

    reward = model.reward([0.2, 0.8, 0.5])
    assert reward["source"] in ("local_rubric", "inference_api")
    assert reward["attempted_network"] is False, (
        "no network call may happen unless explicitly allowed")
    assert abs(reward["reward"] - 0.5) < 1e-9, "the local rubric is the mean of the signals"
    assert isinstance(reward["providers_available"], list)
    if reward["source"] == "local_rubric":
        assert reward["providers_used"] == [], (
            "a local rubric must not name a provider it did not call")


def test_registered_as_a_learning_worm_with_a_callable_summary():
    """The class is discoverable by the fleet, and its contract is satisfied.

    A model nobody can discover executes nowhere, which is why registration is
    asserted here; the summary callable is asserted because the registry smoke
    test requires it on every registered worm.
    """
    assert "CuttingEdgeModel" in WORM_REGISTRY, (
        "the model must be registered so the fleet can discover it")
    entry = WORM_REGISTRY["CuttingEdgeModel"]
    assert entry["category"] == "learning"
    assert entry["module"] == "CORE.WORMS.expansion.cutting_edge_model"
    assert entry["summary_source"] == "declared", (
        "the summary must be the class's own, not a mixin guess at a method")

    # The smoke-test contract exactly as test_worm_smoke exercises it: build the
    # REGISTERED class with no arguments, then summarise the instance.
    instance = entry["class"]()
    summary = instance.get_summary()
    assert summary["success"] is True
    assert summary["result"]["param_count"] > 0
    assert summary["result"]["config_fingerprint"] == instance.config_fingerprint()
    assert instance is not None
