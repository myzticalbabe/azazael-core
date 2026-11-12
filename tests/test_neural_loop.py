"""Tests for the canonical neural loop.

Two layers, deliberately separate:

* ENGINE tests inject all thirteen stages, so the ordering, the verdict logic
  and the closure can be tested without the substrate -- and a change to the
  substrate cannot silently change what the engine is asserted to do.
* SUBSTRATE tests call the real read-only adapters (`load`, `perceive`,
  `credit`, `route`) once, and assert the shapes the write stages depend on.
  These are the tests that would have caught the `category`/`nature` defect:
  the first version of `_stage_credit` resolved 0 of 234 organs while every
  engine test passed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from CORE import neural_loop as nl  # noqa: E402


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _stages(**overrides):
    """Thirteen stages that record their call and change nothing by default."""
    calls = []

    def make(name):
        def _fn(ctx):
            calls.append(name)
            return nl.StageResult(name, True, changed=False)
        return _fn

    stages = {name: make(name) for name in nl.STAGES}
    stages.update(overrides)
    return stages, calls


def test_stages_are_run_in_the_declared_causal_order():
    stages, calls = _stages()
    nl.Loop(stages).run(nl.LoopContext(apply=False))
    assert calls == list(nl.STAGES)


def test_every_stage_name_has_an_adapter_and_vice_versa():
    real = nl.default_stages()
    assert set(real) == set(nl.STAGES), "STAGES and the adapter table disagree"


def test_a_missing_stage_is_a_construction_error_not_a_silent_gap():
    stages, _ = _stages()
    del stages["verify"]
    with pytest.raises(ValueError) as e:
        nl.Loop(stages)
    assert "verify" in str(e.value)


def test_an_unknown_stage_is_a_construction_error():
    stages, _ = _stages()
    stages["teleport"] = lambda ctx: nl.StageResult("teleport", True)
    with pytest.raises(ValueError) as e:
        nl.Loop(stages)
    assert "teleport" in str(e.value)


def test_an_apply_cycle_that_changes_nothing_is_INERT_not_OK():
    """THE property. Before this, "ran" and "learned" were the same report."""
    stages, _ = _stages()
    rep = nl.Loop(stages).run(nl.LoopContext(apply=True))
    assert rep["verdict"] == "INERT", rep["verdict"]
    assert rep["ok"] is False


def test_a_dry_cycle_that_changes_nothing_is_DRY_not_a_failure():
    stages, _ = _stages()
    rep = nl.Loop(stages).run(nl.LoopContext(apply=False))
    assert rep["verdict"] == "DRY"


def test_an_apply_cycle_that_changes_something_is_OK():
    def _write(ctx):
        return nl.StageResult("plasticity", True, changed=True, evidence={"updates": 3})

    stages, _ = _stages(plasticity=_write, verify=lambda ctx: nl.StageResult("verify", True, changed=True))
    rep = nl.Loop(stages).run(nl.LoopContext(apply=True))
    assert rep["verdict"] == "OK"
    assert "plasticity" in rep["changed_stages"]


def test_a_raise_inside_a_stage_is_captured_not_propagated():
    def _boom(ctx):
        raise RuntimeError("stage exploded")

    stages, _ = _stages(perceive=_boom)
    rep = nl.Loop(stages).run(nl.LoopContext(apply=False))
    assert rep["verdict"] == "FAIL"
    row = next(s for s in rep["stages"] if s["stage"] == "perceive")
    assert "stage exploded" in row["error"]


def test_a_stage_that_fails_after_another_changed_still_reports_the_change():
    """A late failure must not erase the evidence an earlier stage produced."""
    def _write(ctx):
        return nl.StageResult("plasticity", True, changed=True)

    stages, _ = _stages(plasticity=_write,
                        l2=lambda ctx: nl.StageResult("l2", False, error="nope"))
    rep = nl.Loop(stages).run(nl.LoopContext(apply=True))
    assert rep["verdict"] == "FAIL"
    assert "plasticity" in rep["changed_stages"]


def test_blocked_write_stages_are_reported_with_their_reason():
    stages, _ = _stages(act=lambda ctx: nl.StageResult("act", True, blocked="dry-run"))
    rep = nl.Loop(stages).run(nl.LoopContext(apply=True))
    assert rep["blocked_stages"].get("act") == "dry-run"


def test_verify_alone_failing_means_INERT_not_FAIL():
    """The closure's failure IS the inert verdict; it is not a crash."""
    stages, _ = _stages(verify=lambda ctx: nl.StageResult(
        "verify", False, error="inert cycle: no stage changed the substrate"))
    rep = nl.Loop(stages).run(nl.LoopContext(apply=True))
    assert rep["verdict"] == "INERT"


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_is_empty_safe():
    fp = nl.state_fingerprint({})
    assert fp["edges"] == 0 and fp["l2"] == 0.0


def test_fingerprint_counts_distinct_values_separately_from_edges():
    """31 edges with 3 distinct values cannot route; `edges` alone said healthy."""
    fp = nl.state_fingerprint({"a": 0.0909, "b": 0.0909, "c": 0.5})
    assert fp["edges"] == 3
    assert fp["distinct"] == 2


def test_signature_sees_a_nudge_that_edge_count_cannot():
    """The false alarm the monitor produced on its first cycle."""
    a = {"a:x": 0.5, "b:y": 0.4}
    b = {"a:x": 0.5000001, "b:y": 0.4}
    assert len(a) == len(b), "edge count cannot distinguish these"
    assert nl.weights_signature(a) != nl.weights_signature(b)
    # same values, different insertion order -> same signature
    assert nl.weights_signature({"b:y": 0.4, "a:x": 0.5}) == nl.weights_signature(a)


def test_fingerprint_survives_a_non_numeric_weight():
    fp = nl.state_fingerprint({"a": 0.5, "b": "not-a-number"})
    assert fp["edges"] == 2          # key present
    assert fp["distinct"] == 1       # only the parseable one is counted


def test_fingerprint_moved_detects_a_change_only_in_the_norm():
    a = nl.state_fingerprint({"a": 0.5})
    b = nl.state_fingerprint({"a": 0.5000001})
    assert nl._fingerprint_moved(a, b) is False   # below the tolerance
    c = nl.state_fingerprint({"a": 0.9})
    assert nl._fingerprint_moved(a, c) is True


# ---------------------------------------------------------------------------
# The real adapters (read-only)
# ---------------------------------------------------------------------------

def test_category_of_reads_category_from_a_registry_dict():
    """The exact defect: entries are dicts whose field is `category`."""
    assert nl._category_of({"category": "expansion", "module": "x"}) == "expansion"
    assert nl._category_of({"nature": "fallback-shape"}) == "fallback-shape"
    assert nl._category_of(None) is None
    assert nl._category_of({}) is None


def test_real_load_fingerprints_the_live_weights():
    ctx = nl.LoopContext()
    res = nl._stage_load(ctx)
    assert res.ok is True
    assert set(ctx.state_before) >= {"edges", "distinct", "l2"}
    # The read path must be the one that restores: a fresh process that reads
    # the private dict sees {} and would report amnesia the system lacks.
    from CORE import spine_registry as sr
    assert len(sr.get_q_weights()) == ctx.state_before.get("edges")


def test_real_credit_resolves_categories_and_drops_the_unknown():
    """The rewards are injected; the REGISTRY is real.

    Injecting the reward vector is deliberate: `perceive` reads the live memory
    store, and the repo's root conftest redirects that store to a temp dir, so
    a test that asserted on live counts would pass for the wrong reason (an
    empty store resolves 0 and 0 categories consistently). This test is about the
    resolution rule, so it supplies the input and lets the real registry answer.
    """
    ctx = nl.LoopContext()
    ctx.rewards = {"MetaCluster": 0.5, "NmapWorm": 0.7, "SystemZ": 0.9,
                   "DefinitelyNotAWorm": 0.3}
    res = nl._stage_credit(ctx)
    assert res.ok is True
    ev = res.evidence
    assert ev["resolved"] >= 2, ev
    assert "DefinitelyNotAWorm" in ev["unresolved_sample"]
    assert ev["resolved"] + ev["unresolved"] == len(ctx.rewards)
    assert ev["nonzero"] == ev["resolved"]
    for name, category in ctx.categories.items():
        assert category, name


def test_a_reward_without_a_category_is_dropped_not_guessed():
    """Writing a real reward onto an invented edge is worse than not writing."""
    ctx = nl.LoopContext()
    ctx.rewards = {"DefinitelyNotAWorm": 0.9}
    res = nl._stage_credit(ctx)
    assert ctx.categories == {}
    assert res.evidence["resolved"] == 0
    assert res.evidence["unresolved"] == 1


def test_real_route_orders_the_resolved_organs_by_their_weights():
    ctx = nl.LoopContext()
    ctx.rewards = {"MetaCluster": 0.5, "NmapWorm": 0.7, "SystemZ": 0.9}
    nl._stage_credit(ctx)
    res = nl._stage_route(ctx)
    assert res.ok is True
    assert len(ctx.order) == len(ctx.categories)
    assert sorted(ctx.order) == sorted(ctx.categories)
    assert res.evidence["ordered"] == len(ctx.order)


# ---------------------------------------------------------------------------
# Plasticity: advantage over evidence
# ---------------------------------------------------------------------------

def _capture_writes(monkeypatch):
    from CORE import spine_registry as sr
    calls = []
    monkeypatch.setattr(sr, "record_outcome",
                        lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(sr, "get_q_weights", lambda: {})
    return calls


def test_plasticity_writes_the_advantage_not_the_absolute_reward(monkeypatch):
    """Absolute levels homogenise the table; the SIGN has to carry the signal."""
    calls = _capture_writes(monkeypatch)
    ctx = nl.LoopContext(apply=True)
    ctx.categories = {"Good": "x", "Bad": "x", "Mean": "x"}
    ctx.rewards = {"Good": 0.9, "Bad": 0.5, "Mean": 0.7}
    ctx.evidence = {k: 10 for k in ctx.categories}
    res = nl._stage_plasticity(ctx)
    assert res.changed is True
    deltas = {a[1]: k["delta"] for a, k in calls}
    assert deltas["Good"] > 0 > deltas["Bad"]
    # 0.7 IS the population mean, so its advantage is zero and it must not write.
    assert "Mean" not in deltas
    assert res.evidence["flat"] == 1
    assert res.evidence["baseline"] == pytest.approx(0.7, abs=1e-9)


def test_plasticity_refuses_an_organ_without_individual_evidence(monkeypatch):
    """A reward on one observation is a prior; it must not overwrite a weight."""
    calls = _capture_writes(monkeypatch)
    ctx = nl.LoopContext(apply=True)
    ctx.categories = {"Seen": "x", "Scarce": "x"}
    ctx.rewards = {"Seen": 0.9, "Scarce": 0.1}
    ctx.evidence = {"Seen": 10, "Scarce": 1}
    res = nl._stage_plasticity(ctx)
    written = {a[1] for a, _ in calls}
    assert written == {"Seen"}
    assert res.evidence["no_evidence"] == 1


def test_plasticity_reports_the_signal_spread(monkeypatch):
    """A near-constant reward must be visible, not silently written."""
    _capture_writes(monkeypatch)
    ctx = nl.LoopContext(apply=True)
    ctx.categories = {"A": "x", "B": "x"}
    ctx.rewards = {"A": 0.9500001, "B": 0.95}
    ctx.evidence = {k: 10 for k in ctx.categories}
    res = nl._stage_plasticity(ctx)
    assert res.evidence["spread"] < 1e-5
    assert res.changed is False, "a flat signal must not count as learning"


def test_plasticity_is_blocked_and_writes_nothing_in_a_dry_run(monkeypatch):
    calls = _capture_writes(monkeypatch)
    ctx = nl.LoopContext(apply=False)
    ctx.categories = {"A": "x"}
    ctx.rewards = {"A": 0.9}
    ctx.evidence = {"A": 10}
    res = nl._stage_plasticity(ctx)
    assert res.blocked and not calls


def test_verify_fails_when_the_learning_is_not_durable(monkeypatch):
    """"It moved" without "it stuck" is not progress.

    Measured: the first full cycle moved the table 56 -> 264 in memory while the
    on-disk copy still held 56, and a fresh process read 56. The closure must
    catch exactly that, so this test makes the disk read disagree with memory.
    """
    from CORE import spine_registry as sr
    monkeypatch.setattr(sr, "get_q_weights", lambda: {"a:x": 0.9, "b:y": 0.8})
    monkeypatch.setattr(nl, "_disk_weights", lambda: {"a:x": 0.1})
    ctx = nl.LoopContext(apply=True)
    ctx.state_before = nl.state_fingerprint({"a:x": 0.1})
    nl._stage_plasticity(ctx)          # (no-op: no categories resolved -> blocked)
    ctx.results.append(nl.StageResult("plasticity", True, changed=True))
    res = nl._stage_verify(ctx)
    assert res.ok is False
    assert "NOT durable" in res.error


def test_persist_is_a_write_stage_and_is_blocked_in_a_dry_run():
    assert "persist" in nl.WRITE_STAGES
    assert "persist" in nl.STAGES
    assert nl.STAGES.index("persist") < nl.STAGES.index("verify"), \
        "persistence must happen BEFORE the closure reads the disk"
    res = nl._stage_persist(nl.LoopContext(apply=False))
    assert res.ok is True and res.blocked and res.changed is False


def test_dry_run_stages_never_write_and_say_so():
    """Every write stage must be blocked-with-a-reason in a dry run."""
    ctx = nl.LoopContext(apply=False)
    for name in ("plasticity", "grade", "consolidate", "prune", "l2", "persist"):
        res = nl.REAL_STAGES[name](ctx)
        assert res.ok is True, name
        assert res.blocked == "dry-run (apply=False)", name
        assert res.changed is False, name


def test_act_in_a_dry_run_reports_what_it_would_have_run():
    ctx = nl.LoopContext(apply=False)
    ctx.order = ["A", "B", "C"]
    res = nl._stage_act(ctx)
    assert res.ok is True and res.blocked
    assert res.evidence["would_run"] == 3


def test_the_act_stage_and_the_executor_agree_on_the_summary_line():
    """`act` parses the fleet executor's stdout; pin the contract, not the fleet.

    `_stage_act` fires `scripts/run_mesh_cycle.py` as a subprocess and reads the
    executor's OWN summary line rather than re-deriving counts. That is the right
    design, and it has one silent failure mode, documented in the stage itself:
    "if the summary cannot be parsed the cycle is reported as having run nothing,
    which is the honest reading of an unreadable result". An honest zero and a
    broken parser look identical.

    Running the fleet is a side-effecting operation on a live system, so this
    test checks the two halves against each other instead: the executor's format
    string must still carry the tokens the stage scans for. Renaming either side
    fails here rather than silently reporting that zero worms ran.
    """
    executor = (ROOT / "scripts" / "run_mesh_cycle.py").read_text(encoding="utf-8")
    stage = (ROOT / "src" / "CORE" / "neural_loop.py").read_text(encoding="utf-8")

    assert '" cycle "' in executor or "' cycle '" in executor or " cycle " in executor, (
        "the executor no longer prints a line containing ' cycle ', so the stage "
        "cannot find its summary")
    assert "ok=%d" in executor, (
        "the executor's summary no longer carries ok=%d, so the stage's "
        "tok.startswith('ok=') scan can only ever read zero")
    assert 'tok.startswith("ok=")' in stage or "tok.startswith('ok=')" in stage, (
        "the stage no longer scans for the ok= token the executor prints")
    assert '" cycle " in ln' in stage or "' cycle ' in ln" in stage, (
        "the stage no longer selects the executor's summary line")
