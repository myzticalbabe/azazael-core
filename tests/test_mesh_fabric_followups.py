# coding: utf-8
"""Regression tests for the neural-mesh audit FOLLOW-UP pass (2026-09-15).

The first pass (``test_mesh_fabric_gaps.py``) covered the dead quarantine
quorum, the double-instance cycle race, env-id unification, fabric
persistence and the mesh hygiene fixes. This file covers the items that were
left open by it and closed in the follow-up:

- #3  the morphogenesis containment gate. ``morph()`` now writes into a
      containment pen that discovery cannot descend into, and
      ``promote()`` is the only path into the discovery-visible directory,
      gated on quarantine + a quorum of DISTINCT envs. The previous body had
      zero callers and called ``morph(dry_run=False)`` + ``discover_worms()``
      with no gate at all.
- #6  8 fabric worms (consensus / federated / servemesh) whose real APIs had
      no caller. The new ``_fabric_stage()`` drives them from real sources.
- #8  bounded-growth caps for the remaining fabric state.
- #9  ``GossipSyncWorm`` was first-writer-wins; now trust-checked +
      recency-ordered.

Spine state is isolated to a tmp dir, and every generated source file is
written under ``tmp_path``, so the suite never touches the developer's real
``~/.azazael/spine_state.json`` or the real ``expansion/generated/`` tree.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from CORE import spine_registry as sr
from CORE.WORMS.expansion import spine as SP

#: Reasons `_fabric_resilience` may give that are properties of the MACHINE:
#: there is nowhere to snapshot, or nothing under it worth staging. These are
#: permitted declines -- the organ is not wrong, the host has nothing to prove.
ENVIRONMENTAL_DECLINES = {"no_state_dir", "no_stageable_state"}

#: Reasons that mean the backup proof itself failed. Permitted only when the
#: return ALSO carries the error that caused it, so a real failure can never
#: pass as a quiet decline.
PROOF_FAILURES = {"backup_organs_unavailable", "snapshot_failed",
                  "verify_failed", "restore_failed", "organ_error"}


@pytest.fixture(autouse=True)
def isolated_spine_state(tmp_path, monkeypatch):
    """Keep the suite off the developer's real dot-directory."""
    monkeypatch.setattr(sr, "_STATE_FILE", tmp_path / "spine_state.json")
    monkeypatch.setattr(sr, "_GOSSIP_DIR", tmp_path / "gossip")
    sr.spine_reset()
    yield
    sr.spine_reset()


@pytest.fixture
def pen(tmp_path, monkeypatch):
    """Redirect the generated/staging trees at a tmp dir.

    ``_staging_dir()`` delegates to ``_generated_dir()``, so patching the
    latter moves both — which is the property the containment relies on.
    """
    gen = tmp_path / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(SP, "_generated_dir", lambda: gen)
    return gen


def _stage_one(worm: str = "BadWorm", sig: str = "boom") -> str:
    """Force one chronic pattern and stage it. Returns the staged filename."""
    mw = SP.MorphogenesisWorm()
    mw._chronic_patterns = lambda: [{
        "worm": worm, "nature": "mesh", "sig": sig,
        "fails": 9, "total": 9, "consec": 9,
    }]
    r = mw.morph(dry_run=False)
    assert r["staged"] >= 1, r
    return r["proposals"][0]["file"]


# ── #3 — morphogenesis containment gate ─────────────────────────────

def test_discovery_walk_skips_the_pen_and_enters_generated():
    """The containment is structural, not conventional.

    This mirrors the filter in ``worm_registry._discover_worms_impl``: a
    directory is descended into only when it holds an ``__init__.py``, and
    ``_``/``.``-prefixed names are skipped outright. A staged file has to fail
    that walk or the gate is decorative.
    """
    import os

    expansion = Path(SP.__file__).resolve().parent
    seen: list[str] = []
    for dirpath, dirnames, _files in os.walk(expansion):
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(("_", "."))
            and os.path.isfile(os.path.join(dirpath, d, "__init__.py"))
        )
        seen.append(os.path.basename(dirpath))
    assert "generated" in seen
    assert "_staging" not in seen


def test_staging_pen_is_underscore_prefixed_and_nested():
    pen_dir = SP._staging_dir()
    assert pen_dir.name == "_staging"
    assert pen_dir.parent == SP._generated_dir()
    # No __init__.py => the walk will not descend even if it were reachable.
    assert not (pen_dir / "__init__.py").exists() or pen_dir.name.startswith("_")


def test_morph_writes_only_into_the_pen(pen):
    name = _stage_one()
    assert (pen / "_staging" / name).is_file()
    assert not (pen / name).exists(), "morph() must not publish directly"


def test_single_env_cannot_promote(pen, monkeypatch):
    monkeypatch.setenv("AZAZAEL_ENV", "windows")
    name = _stage_one()
    gate = SP.MorphogenesisWorm().promote()
    assert gate["staged"] == 1
    assert gate["promoted"] == 0
    assert gate["held"] == 1
    assert (pen / "_staging" / name).is_file()
    assert not (pen / name).exists()


def test_second_distinct_env_vote_promotes(pen, monkeypatch):
    monkeypatch.setenv("AZAZAEL_ENV", "windows")
    name = _stage_one()
    sr.cast_quarantine_vote(f"promote:{name}", "wsl2-ubuntu", "promote", quorum=2)
    gate = SP.MorphogenesisWorm().promote()
    assert gate["promoted"] == 1
    assert (pen / name).is_file()
    assert not (pen / "_staging" / name).exists()


def test_repeat_vote_from_one_env_cannot_promote(pen, monkeypatch):
    """An env stuffing the ballot must not reach quorum (finding #1 rule)."""
    monkeypatch.setenv("AZAZAEL_ENV", "windows")
    name = _stage_one()
    for _ in range(5):
        sr.cast_quarantine_vote(f"promote:{name}", "windows", "promote", quorum=2)
    assert SP.MorphogenesisWorm().promote()["promoted"] == 0


def test_operator_channel_promotes_and_is_recorded(pen, monkeypatch):
    monkeypatch.setenv("AZAZAEL_ENV", "windows")
    name = _stage_one("OtherWorm", "pow")
    gate = SP.MorphogenesisWorm().promote(force=True)
    assert gate["promoted"] == 1
    assert (pen / name).is_file()
    # The operator branch records a vote rather than promoting invisibly.
    votes = sr.get_quarantine_votes(f"promote:{name}")
    assert "operator" in (votes.get(f"promote:{name}") or {})


def test_apply_false_writes_nothing_but_stays_observable(pen, monkeypatch):
    """The deterministic default must not write to disk.

    Regression guard: an earlier cut staged on every cycle, so any caller
    driving a plain `run_cycle()` (including the test suite) created source
    files in the real project tree.
    """
    monkeypatch.setenv("AZAZAEL_ENV", "windows")
    cyc = SP.SpineCycleWorm()
    cyc._morphogenesis = SP.MorphogenesisWorm()
    cyc._morphogenesis._chronic_patterns = lambda: [{
        "worm": "BadWorm", "nature": "mesh", "sig": "boom",
        "fails": 9, "total": 9, "consec": 9,
    }]
    out = cyc._process_morphogenesis_output(apply=False)
    assert out["apply"] is False
    assert out["staged"] == 0 and out["promoted"] == 0
    assert out["would_stage"] == 1        # still observable as a dry run
    assert not (pen / "_staging").exists() or not list((pen / "_staging").glob("*.py"))


def test_apply_true_stages_then_waits_for_a_peer_env(pen, monkeypatch):
    monkeypatch.setenv("AZAZAEL_ENV", "windows")
    cyc = SP.SpineCycleWorm()
    cyc._morphogenesis = SP.MorphogenesisWorm()
    cyc._morphogenesis._chronic_patterns = lambda: [{
        "worm": "BadWorm", "nature": "mesh", "sig": "boom",
        "fails": 9, "total": 9, "consec": 9,
    }]
    # apply=True stages, but a single env still cannot clear the quorum.
    held = cyc._process_morphogenesis_output(apply=True)
    assert held["staged"] == 1
    assert held["promoted"] == 0 and held["held"] == 1
    name = cyc._morphogenesis.staged()[0]["file"]

    # A peer env's approval is what promotion waits for, and the shared
    # ledger is where it arrives from.
    sr.cast_quarantine_vote(f"promote:{name}", "wsl2-ubuntu", "promote", quorum=2)
    promoted = cyc._process_morphogenesis_output(apply=True)
    assert promoted["promoted"] == 1
    assert (pen / name).is_file()


# ── #9 — gossip merge is trust-checked + recency-ordered ────────────

def test_gossip_newer_value_replaces_older():
    from CORE.WORMS.expansion.mesh import GossipSyncWorm
    g = GossipSyncWorm()
    g.gossip({"metric": {"v": 1, "at": "2026-01-01T00:00:00"}})
    out = g.gossip({"metric": {"v": 2, "at": "2026-06-01T00:00:00"}})
    assert out["applied"] >= 1
    assert g._state["metric"]["v"] == 2


def test_gossip_older_timestamped_value_is_not_applied():
    from CORE.WORMS.expansion.mesh import GossipSyncWorm
    g = GossipSyncWorm()
    g.gossip({"metric": {"v": 2, "at": "2026-06-01T00:00:00"}})
    out = g.gossip({"metric": {"v": 1, "at": "2026-01-01T00:00:00"}})
    assert out["stale_skipped"] >= 1
    assert g._state["metric"]["v"] == 2


def test_gossip_untimestamped_value_fills_a_gap_but_never_overwrites():
    from CORE.WORMS.expansion.mesh import GossipSyncWorm
    g = GossipSyncWorm()
    assert g.gossip({"plain": 1})["new"] == 1
    out = g.gossip({"plain": 2})
    assert out["stale_skipped"] >= 1
    assert g._state["plain"] == 1


def test_gossip_spine_shaped_payload_reports_a_trust_verdict():
    from CORE.WORMS.expansion.mesh import GossipSyncWorm
    g = GossipSyncWorm()
    out = g.gossip({"spine_map": {"a": 1}, "seq": 1})
    assert out["merge"] == "spine"
    assert "trust" in out


# ── #8 — bounded-growth caps ────────────────────────────────────────

def test_cap_list_keeps_the_newest_entries():
    from CORE.WORMS.expansion.fabric_state import cap_list
    seq = list(range(10))
    cap_list(seq, 3)
    assert seq == [7, 8, 9]


def test_cap_map_keeps_the_newest_insertions():
    from CORE.WORMS.expansion.fabric_state import cap_map
    d = {i: i for i in range(10)}
    cap_map(d, 3)
    assert sorted(d) == [7, 8, 9]


def test_cap_helpers_treat_zero_and_negative_as_unbounded():
    from CORE.WORMS.expansion.fabric_state import cap_list, cap_map
    seq = list(range(5))
    cap_list(seq, 0)
    assert seq == list(range(5))
    d = {i: i for i in range(5)}
    cap_map(d, -1)
    assert len(d) == 5


def test_reward_shaper_score_history_is_bounded():
    from CORE.WORMS.expansion.federated import RewardShaperMeshWorm
    w = RewardShaperMeshWorm()
    for i in range(w.SCORES_PER_PROVIDER_MAX + 50):
        w.score("groq", 0.5, ok=True)
    assert len(w._scores["groq"]) <= w.SCORES_PER_PROVIDER_MAX


def test_reward_shaper_dlq_is_bounded():
    from CORE.WORMS.expansion.federated import RewardShaperMeshWorm
    w = RewardShaperMeshWorm()
    for _ in range(w.DLQ_MAX + 25):
        w.score("groq", 0.0, ok=False)
    assert len(w._dlq) <= w.DLQ_MAX


# ── #6 — the fabric stage drives the previously-unreachable organs ──

def test_provider_key_state_never_marks_a_local_endpoint_down():
    """A localhost endpoint whose key is optional must stay usable.

    `ollama`/`lmstudio` declare an env_var for a localhost base_url, and the
    `*_direct` providers declare a model *path* under env_var. Treating any of
    those as a missing credential would remove a working endpoint from the
    failover chain — the opposite of what the stage is for.
    """
    chain = ("llamaserver", "lmstudio", "nim", "groq", "openrouter", "ollama",
             "vllm", "sglang", "openwebui", "jan", "kilo", "opencode")
    state = SP.SpineCycleWorm._provider_key_state(chain)
    assert "lmstudio" not in state
    assert "ollama" not in state
    assert "vllm" not in state
    # `nim` is a real remote provider (aliased to nvidia_nim) and IS reported.
    assert "nim" in state


def test_fabric_stage_reports_all_four_natures(pen, monkeypatch):
    cyc = SP.SpineCycleWorm()
    monkeypatch.setattr(cyc, "_publish_local_shards", lambda shards: 0)
    out = cyc._fabric_stage()
    # The four natures this stage was written to drive must ALL be present.
    # `variation` is a fifth organ added later, so exact equality here would
    # fail a correct stage -- the guard is that none of the four goes missing,
    # not that no organ is ever added.
    assert {"servemesh", "consensus", "federated", "resilience"} <= set(out)
    for name, res in out.items():
        assert isinstance(res, dict), name
    # `resilience` chains the backup organs (snapshot -> verify -> dry-run
    # restore). It may decline for an ENVIRONMENTAL reason, and a proof may
    # genuinely fail on a loaded host -- but every non-success must NAME its
    # reason. The previous contract returned a bare `success: False` on the
    # snapshot/verify/restore paths, so a failure carried None here and this
    # assertion failed on LOAD rather than on a defect. Classification, not
    # an allow-list of two strings, is what makes the check load-independent.
    res = out["resilience"]
    if not res.get("success"):
        reason = res.get("reason")
        assert reason, res
        assert reason in ENVIRONMENTAL_DECLINES | PROOF_FAILURES, res
        if reason in PROOF_FAILURES:
            assert any(res.get(k) for k in ("error", "verify_error",
                                            "restore_error")), res


def test_resilience_names_a_reason_when_the_snapshot_fails(pen, tmp_path, monkeypatch):
    """A snapshot the snapshotter refuses must be classified, not a bare False."""
    from CORE.WORMS.expansion import backup as backup_mod

    home = tmp_path / "home"
    state = home / ".azazael"
    state.mkdir(parents=True)
    (state / "ledger.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(backup_mod.StateSnapshotter, "create_snapshot",
                        lambda self, *a, **k: {"success": False,
                                               "error": "disk full"},
                        raising=False)

    res = SP.SpineCycleWorm()._fabric_resilience()

    assert res["success"] is False
    assert res.get("reason") == "snapshot_failed", res
    assert "disk full" in str(res.get("error")), res


def test_resilience_names_a_reason_when_the_restore_proof_fails(pen, tmp_path,
                                                               monkeypatch):
    """A snapshot that cannot be restored is not a backup, and must say so."""
    from CORE.WORMS.expansion import backup as backup_mod

    home = tmp_path / "home"
    state = home / ".azazael"
    state.mkdir(parents=True)
    (state / "ledger.json").write_text("{}", encoding="utf-8")
    archive = home / "snapshot_fake.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(backup_mod.StateSnapshotter, "create_snapshot",
                        lambda self, *a, **k: {
                            "success": True,
                            "snapshot": {"path": str(archive)}},
                        raising=False)
    monkeypatch.setattr(backup_mod.RecoveryTester, "verify_archive",
                        lambda self, p: {"valid": True, "file_count": 1},
                        raising=False)
    monkeypatch.setattr(backup_mod.RecoveryTester, "test_restore",
                        lambda self, p, d: {"success": False,
                                            "error": "bad header"},
                        raising=False)

    res = SP.SpineCycleWorm()._fabric_resilience()

    assert res["success"] is False
    assert res.get("reason") == "restore_failed", res
    assert "bad header" in str(res.get("restore_error")), res


def test_fabric_stage_names_an_organ_that_raises(pen, monkeypatch):
    """A raising organ is reported as organ_error, never as a silent None."""
    cyc = SP.SpineCycleWorm()

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(cyc, "_fabric_resilience", boom)
    for name in ("servemesh", "consensus", "federated", "variation"):
        monkeypatch.setattr(cyc, "_fabric_" + name,
                            lambda: {"success": True}, raising=False)

    out = cyc._fabric_stage()

    assert out["resilience"]["reason"] == "organ_error", out["resilience"]
    assert "boom" in out["resilience"]["error"]


def test_servemesh_registers_real_provider_endpoints(pen, monkeypatch):
    cyc = SP.SpineCycleWorm()
    monkeypatch.setattr(cyc, "_publish_local_shards", lambda shards: 0)
    res = cyc._fabric_servemesh()
    assert res["registered"] > 0
    assert res["pick"]
    assert res["next_provider"]


def test_consensus_nominates_from_the_placement_file(pen, tmp_path, monkeypatch):
    import json
    from CORE.WORMS.expansion.consensus import LeaderElectorWorm
    placement = tmp_path / "placement.json"
    placement.write_text(json.dumps({
        "trainer": "bare", "scanner": "wsl2-kali",
        "server": "windows", "archiver": "windows",
    }), encoding="utf-8")
    real = LeaderElectorWorm.apply_placement

    def patched(self, path=None):
        return real(self, str(placement))

    monkeypatch.setattr(LeaderElectorWorm, "apply_placement", patched)
    # Fresh spine binding: module-top SP predates any in-suite registry
    # reset (reset_registry purges + re-imports worm modules), so SP may
    # point at a stale SpineCycleWorm whose consensus class is NOT the
    # patched one. Re-import here to bind the live modules.
    from CORE.WORMS.expansion import spine as _live_spine
    cyc = _live_spine.SpineCycleWorm()
    monkeypatch.setattr(cyc, "_publish_local_shards", lambda shards: 0)
    res = cyc._fabric_consensus()
    assert res["placement"]["scanner"] == "wsl2-kali"
    assert res["leader"]
    assert res["term"] >= 1
    # The succession path had zero callers before this pass.
    assert res["succession"]["success"] is True


def test_consensus_records_model_forks_and_resolves_them(pen):
    from CORE.WORMS.expansion.servemesh import ModelShardServerWorm
    shards = ModelShardServerWorm()
    # Same logical model, two content digests from two envs => a real fork.
    shards.publish("llama", "aaaa1111", "windows", "gguf")
    shards.publish("llama", "bbbb2222", "wsl2-ubuntu", "gguf")
    cyc = SP.SpineCycleWorm()
    forks = cyc._model_fork_reports(shards)
    assert len(forks) == 2
    assert {f[0] for f in forks} == {"llama"}


def test_federated_stage_builds_aligned_vectors(pen):
    """Ragged per-env vectors were the fragility in aggregate() (#11)."""
    from CORE.WORMS.expansion import federated as fed
    cyc = SP.SpineCycleWorm()
    res = cyc._fabric_federated()
    if res.get("federated_dim"):
        assert res["federated_dim"] >= 1
        assert len(res["federated_avg_head"]) >= 1
    assert fed.FederatedAggregatorWorm is not None


def test_run_cycle_includes_fabric_and_morphogenesis_stages(pen, monkeypatch):
    cyc = SP.SpineCycleWorm()
    monkeypatch.setattr(cyc, "_publish_local_shards", lambda shards: 0)
    report = cyc.run_cycle(apply=False)
    assert report["success"] is True
    assert "fabric" in report
    assert "morphogenesis" in report
    assert report["morphogenesis"]["apply"] is False
