# coding: utf-8
"""NeuralPipeline's CUTTING_EDGE backend actually RUNS the #507 adapters.

engram #509 (DeepSeek Task #508). #507 shipped
`CORE/WORMS/expansion/cutting_edge_model.py` with seven integration adapters and
zero production callers: only its own tests imported it. A module that nothing
calls is a validated skeleton, not a capability, so this suite asserts the
dispatcher really reaches the adapters -- and, just as importantly, that it does
not reach them when the flag is off.

The discriminating facts, and why each is the one worth asserting:

  * With `use_cutting_edge=False` (the default) the model is not built and the
    module is not imported. Building it registers a worm as a side effect of its
    decorator, so "not built" is the evidence that the default path is untouched
    rather than merely quiet.
  * `agent_type=None` resolves to "SB3" while the flag is off and to
    "CUTTING_EDGE" while it is on -- observed through a spy on the dispatch
    argument, so the assertion is about what was dispatched, not about a
    docstring. An explicitly requested "SB3" still wins with the flag on: the
    flag chooses a default, it does not override a caller.
  * A real run's `feature_meta["extractor_meta"]` is compared against the
    pipeline's own `feature_dim`. Both come from the same real
    `SchemaAwareFeatureExtractor` call, so equality proves the extractor's
    metadata crossed the boundary instead of being dropped and re-made.
  * Version registration is checked through `ModelVersioner.list_versions`, and
    the version string must carry the config fingerprint -- that is what stops
    two ablations from being filed under one version.
  * A raised training step must leave exactly one dead letter naming
    `CuttingEdgeModel`. The queue file is per-test because the default file is
    shared and already holds letters from earlier sessions, which would make
    "one letter was added" unfalsifiable.

Two injections keep this hermetic, and neither weakens an assertion: the DLQ
gets a temp file, and the versioner is put in its own in-memory mode
(`_store = False`, the code's own "no store" sentinel) so a test run cannot write
model rows into the shared memory database. Pipeline-run persistence is stubbed
for the same reason; it is not one of Task #508's requirements.
"""

import sys
import tempfile
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

# Module scope, deliberately: neither of these imports has a collection-time
# side effect, which was checked rather than assumed. IMPORTANT: this was a real
# fork in the road, not laziness -- a module-scope import of a *worm* module
# fires its `@register_worm` decorator during collection, and the sibling suite
# records that these imports are kept function-level for exactly that reason.
# These two are safe: `dead_letter_queue` declares no worm (`class
# DeadLetterQueue` only) and `CORE/WORMS/__init__.py` imports no worm module at
# module scope -- its `_discover_worm_modules()` is called from functions, not at
# import -- so nothing registers; and importing `NeuralPipeline` does not build
# the cutting-edge model, because that import is itself lazy inside it. The
# default-path test below asserts `_cutting_edge_model is None`, which still
# holds. The expansion-barrel imports further down (`learning_rl`,
# `cutting_edge_model`) stay function-level for the opposite reason: importing
# anything under `CORE.WORMS.expansion` executes that package's `__init__`, which
# eagerly re-exports ~29 worm modules, each with a registration decorator.
from CORE.NeuralPipeline import NeuralPipeline  # noqa: E402 - after sys.path
from CORE.WORMS.dead_letter_queue import DeadLetterQueue  # noqa: E402 - after sys.path


#: Real record shape the pipeline extracts, in the four keys
#: SchemaAwareFeatureExtractor reads: event_type, severity, source, data.
#: Repeated so the batch has enough rows to be a batch.
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


class _FakeDataPipe:
    """Stands in for DataPipelineWorm; the memory store is not what this tests.

    The real feature extractor, versioner, DLQ and model are NOT faked, so the
    boundaries under test are the real ones.
    """

    def __init__(self, records):
        self.records = list(records)

    def split_memories(self, **kwargs):
        return {"train": list(self.records), "test": list(self.records),
                "stats": {"total": len(self.records)}}

    def extract_from_memory(self, **kwargs):
        return list(self.records)


def _pipeline(tmpdir, **kwargs):
    """A pipeline wired to injected storage, with the real components intact."""
    pipeline = NeuralPipeline(max_workers=4, llm_augmentation=False, **kwargs)
    pipeline.data_pipe = _FakeDataPipe(RECORDS)
    # Per-test queue file: the default one is shared and pre-populated, so a
    # count taken against it could not distinguish a new letter from an old one.
    pipeline.dlq = DeadLetterQueue(storage_path=Path(tmpdir) / "dead_letters.json")
    # In-memory versioning: the durable path writes to the shared memory
    # database, and a test must not add rows to production memory.
    pipeline.versioner._store = False
    pipeline._persist_run = lambda metrics: None
    return pipeline


# -- the default path must be untouched ------------------------------------

def test_default_pipeline_does_not_build_the_model(tmpdir):
    """Off by default, and off means the module is never imported."""
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td)
        assert pipeline.use_cutting_edge is False
        assert pipeline._cutting_edge_model is None, (
            "constructing a pipeline built the model; building it imports the "
            "module, whose decorator registers a worm as a construction side effect")
        assert pipeline.cutting_edge_switches == {}


def test_run_end_to_end_defaults_to_sb3_when_the_flag_is_off(tmpdir):
    """No flag: the dispatcher is handed the pre-existing default."""
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td)
        seen = []
        pipeline._train_agent = lambda **kw: (seen.append(kw["agent_type"]),
                                              {"stub": True})[1]
        pipeline.run_end_to_end(source="SystemZ", register_model=False)
        assert seen == ["SB3"], "the unflagged default no longer dispatches SB3"


def test_flag_on_routes_the_default_but_never_overrides_a_caller(tmpdir):
    """The flag selects the default; an explicit agent_type still wins."""
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True)
        seen = []
        pipeline._train_agent = lambda **kw: (seen.append(kw["agent_type"]),
                                              {"stub": True})[1]

        pipeline.run_end_to_end(source="SystemZ", register_model=False)
        assert seen == ["CUTTING_EDGE"], (
            "use_cutting_edge=True did not route the default path to the "
            "CuttingEdgeModel backend (req. 3)")

        pipeline.run_end_to_end(source="SystemZ", agent_type="SB3",
                                register_model=False)
        assert seen == ["CUTTING_EDGE", "SB3"], (
            "the flag overrode an explicit agent_type; it must only choose a "
            "default, or existing callers would change behaviour")


def test_unknown_agent_type_still_reports_the_same_error(tmpdir):
    """The else-branch contract is unchanged by the new dispatch arm."""
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td)
        result = pipeline._train_agent("NOPE", "s", None, [], [], [], [], {}, {})
        assert result == {"error": "Unknown agent_type: NOPE"}


# -- the CUTTING_EDGE path must reach every adapter -------------------------

def test_run_end_to_end_executes_the_feature_boundary_and_the_pool_step(tmpdir):
    """Reqs. 2, 3 and 4, measured on a real run through run_end_to_end."""
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True)
        result = pipeline.run_end_to_end(source="CLUSTERWORMS", register_model=True)

        assert result["status"] == "success", result.get("error")
        assert result["trainer"] == "CuttingEdgeModel"

        # req. 2 -- the real extractor's metadata arrived at the model.
        assert result["feature_dim"] > 0, "no features were extracted, so the boundary proved nothing"
        assert result["feature_meta"]["extractor_meta"]["dim"] == result["feature_dim"], (
            "the meta the pipeline read for its own metric is not the meta the "
            "model received; the adapter boundary dropped it")
        assert result["feature_meta"]["rows"] == len(RECORDS)
        assert result["feature_meta"]["projected_to"] == result["forward_shape"][1]

        # req. 3 -- one group, every expert updated, objective improved.
        assert result["train_step"]["waited_ok"] is True
        assert result["experts_updated"] == 8
        assert result["train_step"]["group_id"]
        assert result["loss_after"] < result["loss_before"], (
            "the step did not improve the objective it measured")

        # req. 4 -- reward shaping stayed local and said so.
        assert result["reward"]["source"] == "local_rubric"
        assert result["reward"]["attempted_network"] is False

        # req. 5 -- registered through the model's own adapter.
        assert result["version_registered"] is True
        assert result["model_name"] == "cutting_edge_model"


def test_reward_adapter_is_handed_the_pipelines_configured_providers(tmpdir):
    """Req. 4's wiring: the configured provider names reach the adapter.

    The adapter's own internals are #507's tests' job. What is new here is that
    the PIPELINE supplies its configured providers and withholds network access,
    so that is what gets spied on.
    """
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True)
        pipeline._augmentation_apis = ["groq", "openrouter"]
        pipeline._api_providers = {"groq": object()}

        captured = {}
        model = pipeline.cutting_edge_model
        inner = model.reward

        def spy(signals, **kwargs):
            captured["signals"] = list(signals)
            captured.update(kwargs)
            return inner(signals, **kwargs)

        model.reward = spy
        result = pipeline._train_agent(
            "CUTTING_EDGE", "SystemZ", None, [], [], [[0.2] * 4] * 4,
            [1.0] * 4, {"dim": 4}, {}, register_model=False)

        assert captured["provider_names"] == ["groq"], (
            "the pipeline's configured provider names did not reach the adapter")
        assert captured["allow_network"] is False, (
            "a pipeline run would have spent API calls it was not asked to spend")
        # Three bounded signals, so the adapter is not handed an empty list.
        assert len(captured["signals"]) == 3
        assert all(0.0 <= s <= 1.0 for s in captured["signals"])
        assert result["reward"]["attempted_network"] is False


def test_versioning_registers_under_the_config_fingerprint(tmpdir):
    """Req. 5 -- the fingerprint is what keeps two ablations apart."""
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True)
        first = pipeline.run_end_to_end(source="CLUSTERWORMS", register_model=True)

        versions = pipeline.versioner.list_versions("cutting_edge_model")
        assert len(versions) == 1, versions
        entry = versions[0]
        assert first["config_fingerprint"] in entry["version"], (
            "the version string does not carry the config fingerprint, so this "
            "model and an ablation of it would collide under one version")
        assert entry["metadata"]["config_fingerprint"] == first["config_fingerprint"]
        assert entry["metadata"]["active_components"] == first["active_components"]
        assert entry["metadata"]["ablation_switches"] == first["ablation_switches"]

        # The ablation registers as a DIFFERENT version, which is the point.
        pipeline.ablate_cutting_edge("ssm")
        second = pipeline.run_end_to_end(source="CLUSTERWORMS", register_model=True)
        assert second["config_fingerprint"] != first["config_fingerprint"]
        versions = pipeline.versioner.list_versions("cutting_edge_model")
        assert len(versions) == 2, "an ablation did not register as its own version"
        assert versions[1]["version"] != versions[0]["version"]


def test_a_failed_step_is_routed_to_the_dead_letter_queue(tmpdir):
    """Req. 3's failure half: recorded, reported, never swallowed."""
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True)
        assert pipeline.dlq.get_pending() == []

        def explode(*args, **kwargs):
            raise RuntimeError("gradient step exploded")

        pipeline.cutting_edge_model.train_parallel = explode
        result = pipeline._train_agent(
            "CUTTING_EDGE", "SystemZ", None, [], [], [[0.1] * 4] * 3,
            [1.0] * 3, {"dim": 4}, {}, register_model=False)

        assert result["error"] == "gradient step exploded", result
        assert result["error_type"] == "RuntimeError"
        assert "trained" not in result, (
            "a failed run still reports a training verdict; it must not claim "
            "a step it did not take")

        pending = pipeline.dlq.get_pending()
        assert len(pending) == 1, "the failure was not routed to the DLQ"
        assert pending[0].worm_name == "CuttingEdgeModel"
        assert "exploded" in pending[0].error_message


# -- reach: the production entry point and the RLTrainLoop route ------------

def test_opt_in_from_env_is_explicit_not_implied(tmpdir):
    """The helper gates on an exact opt-in and reads no global state itself."""
    for off in ({}, {"AZAZAEL_USE_CUTTING_EDGE": ""},
                {"AZAZAEL_USE_CUTTING_EDGE": "0"},
                {"AZAZAEL_USE_CUTTING_EDGE": "no"}):
        assert NeuralPipeline.opt_in_from_env(off) is False, off
    for on in ("1", "true", "TRUE", "yes", "on", " 1 "):
        assert NeuralPipeline.opt_in_from_env(
            {"AZAZAEL_USE_CUTTING_EDGE": on}) is True, on


def test_the_production_entry_point_consults_the_opt_in(tmpdir):
    """A source-level wiring assertion, and the only honest way to state reach.

    #508 exists because #507 had zero production callers. The training
    orchestrator is the production caller; running a whole training cycle here
    would be neither hermetic nor fast, so the check is that the opt-in is wired
    into its construction -- without it, the backend is reachable only from tests
    and the task has not been done.
    """
    source = (Path(__file__).resolve().parent.parent
              / "scripts" / "run_training_orchestrator.py").read_text(encoding="utf-8")
    assert "use_cutting_edge=NeuralPipeline.opt_in_from_env()" in source, (
        "the production training entry point does not consult the opt-in, so the "
        "CuttingEdgeModel backend remains reachable only from tests")


def test_rl_train_loop_routes_through_train_parallel(tmpdir):
    """#508's Where names RLTrainLoop as a backend route; verify it actually runs."""
    import random

    from CORE.WORMS.expansion.learning_rl import RLTrainLoop

    records = [{"source": "CLUSTERWORMS", "severity": "info",
                "event_type": "cluster_execution_started",
                "data": {"cluster_type": "alpha", "worm_count": 3}}] * 20

    off = RLTrainLoop().train_from_memory(records=records, n_episodes=2,
                                          max_steps=2, persist=False,
                                          rng=random.Random(3))
    assert "cutting_edge" not in off, (
        "the Q-learning path grew a cutting_edge key without being asked for one")

    on = RLTrainLoop().train_from_memory(records=records, n_episodes=2,
                                         max_steps=2, persist=False,
                                         use_cutting_edge=True,
                                         rng=random.Random(3))
    step = on["cutting_edge"]
    assert step["trained"] is True, step
    assert step["experts_updated"] == 8, step
    assert step["loss_after"] < step["loss_before"], step
    assert step["reward"]["attempted_network"] is False, step
    assert step["active_components"] == ["moe", "ssm", "attention", "ffn"], step


# -- req. 6: the ablation switches compose with pipeline config -------------

def test_every_ablation_switch_reaches_the_pipeline_report(tmpdir):
    """Each switch, flipped through the pipeline, changes what the run reports."""
    with tempfile.TemporaryDirectory() as td:
        for component in ("moe", "ssm", "attention", "ffn"):
            # 12, not the default 8: the module validates 8..64, and a non-default
            # value that shows up in the run proves the dict reached the config
            # rather than being ignored.
            pipeline = _pipeline(td, use_cutting_edge=True,
                                 cutting_edge_config={"n_experts": 12})
            before = pipeline.cutting_edge_model.config_fingerprint()

            info = pipeline.ablate_cutting_edge(component)
            assert component not in info["active_components"], component
            assert info["switches"][component] is False, component
            assert info["config_fingerprint"] != before, component

            run = pipeline.run_end_to_end(source="CLUSTERWORMS", register_model=False)
            assert run["status"] == "success", (component, run.get("error"))
            assert component not in run["active_components"], (
                "%s was ablated on the model but the run still reports it" % component)
            assert run["ablation_switches"][component] is False
            assert set(run["active_components"]) == set(info["active_components"])

            if component == "moe":
                # No experts means nothing to update. Reporting "trained" here
                # would claim arithmetic that never ran.
                assert run["trained"] is False, run
                assert "moe" in run["trained_reason"]
            else:
                assert run["trained"] is True, (component, run.get("trained_reason"))
                # The config dict reached the model, not just the pipeline.
                assert run["experts_updated"] == 12, (component, run.get("experts_updated"))


def test_configured_switches_are_reported_before_the_model_is_built(tmpdir):
    """Inspecting a pipeline must not be what registers the worm."""
    with tempfile.TemporaryDirectory() as td:
        pipeline = _pipeline(td, use_cutting_edge=True,
                             cutting_edge_config={"use_attention": False,
                                                  "use_ffn": False})
        assert pipeline._cutting_edge_model is None
        assert pipeline.cutting_edge_switches == {"use_attention": False,
                                                  "use_ffn": False}
        # ...and the built model honours them.
        assert pipeline.cutting_edge_model.active_components() == ("moe", "ssm")


def test_all_agents_includes_cutting_edge_only_when_opted_in(tmpdir):
    """`ALL` keeps its three agents unless the pipeline opted in."""
    with tempfile.TemporaryDirectory() as td:
        plain = _pipeline(td)
        plain._train_grpo = lambda records: {"trainer": "GRPOTrainer"}
        plain._train_qlearning = lambda *a, **kw: {"trainer": "RLTrainLoop"}
        plain._train_sb3 = lambda *a, **kw: {"trainer": "SB3Agent"}
        off = plain._train_agent("ALL", "s", None, [], [], [], [], {}, {})
        assert off["total_agents"] == 3, off
        assert "CUTTING_EDGE" not in off["agents_trained"], off

        opt_in = _pipeline(td, use_cutting_edge=True)
        opt_in._train_grpo = lambda records: {"trainer": "GRPOTrainer"}
        opt_in._train_qlearning = lambda *a, **kw: {"trainer": "RLTrainLoop"}
        opt_in._train_sb3 = lambda *a, **kw: {"trainer": "SB3Agent"}
        on = opt_in._train_agent("ALL", "s", None, [], [], [[0.5] * 4] * 3,
                                 [1.0] * 3, {"dim": 4}, {}, register_model=False)
        assert on["total_agents"] == 4, on
        assert "CUTTING_EDGE" in on["agents_trained"], on


# -- deliv. 2: the full factorial, at the level a suite can afford ----------

def test_all_sixteen_combinations_are_distinct_and_self_describing(tmpdir):
    """2**4 configs: every subset is its own identity, with no collisions.

    The 16-run sweep lives in ``tools/cutting_edge_ablation_matrix.py``, because
    it is an evidence artifact rather than a unit test. What is asserted here is
    the property that sweep depends on: all sixteen subsets build a config whose
    fingerprint is unique and whose active components are exactly its subset. Two
    colliding fingerprints would let two ablations share one version string,
    which is the single thing the fingerprint exists to prevent.
    """
    import itertools

    from CORE.WORMS.expansion.cutting_edge_model import COMPONENTS, CuttingEdgeConfig

    combos = [dict(zip(COMPONENTS, flags))
              for flags in itertools.product([True, False], repeat=len(COMPONENTS))]
    assert len(combos) == 16, combos

    base = CuttingEdgeConfig()
    fingerprints = {}
    for switches in combos:
        cfg = base.variant(**{("use_" + name): on for name, on in switches.items()})
        expected = tuple(n for n in COMPONENTS if switches[n])
        assert cfg.active_components() == expected, (switches, cfg.active_components())
        assert cfg.ablation_switches() == switches, switches
        fingerprints[cfg.fingerprint()] = switches

    assert len(fingerprints) == 16, (
        "two of the sixteen combinations share a config fingerprint: %r" % fingerprints)


def test_a_model_that_did_not_train_is_not_filed_under_the_model_name(tmpdir):
    """Registration is tied to training, and this checks BOTH directions.

    Half of the sixteen combinations have no MoE, and the experts' output bias is
    the model's only learning surface -- those configurations cannot train. Filing
    one under ``cutting_edge_model`` would claim a model state that was never
    produced, so the assertion is against the model's own identity.

    Deliberately NOT "nothing was registered at all": when the model declines to
    register, ``run_end_to_end`` step 5 files a generic pipeline-run record under a
    timestamped ``{agent_type}_{source}_{time}`` name. That is pre-existing
    pipeline behaviour and is not this model's version, so the two are checked
    apart rather than conflated.
    """
    with tempfile.TemporaryDirectory() as td:
        trained = _pipeline(td, use_cutting_edge=True)
        run = trained.run_end_to_end(source="CLUSTERWORMS", register_model=True)
        assert run["trained"] is True, run
        assert run["version_registered"] is True, run
        versions = trained.versioner.list_versions("cutting_edge_model")
        assert len(versions) == 1, versions
        assert run["config_fingerprint"] in versions[0]["version"]

        # MoE off: nothing to update, so nothing may be filed as this model.
        inference_only = _pipeline(td, use_cutting_edge=True,
                                   cutting_edge_config={"use_moe": False})
        run = inference_only.run_end_to_end(source="CLUSTERWORMS", register_model=True)
        assert run["status"] == "success", run.get("error")
        assert run["trained"] is False, run
        assert "moe" in run["trained_reason"], run.get("trained_reason")
        assert not run.get("version_registered"), (
            "an untrained configuration reported a registered model version")
        assert inference_only.versioner.list_versions("cutting_edge_model") == [], (
            "an untrained configuration was filed under the model's own name")
        # The generic pipeline-run record is a different thing; naming it here so a
        # future change that conflates the two fails this test rather than a review.
        assert run.get("model_name") != "cutting_edge_model", run.get("model_name")
