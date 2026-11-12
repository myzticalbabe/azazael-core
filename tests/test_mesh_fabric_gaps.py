# coding: utf-8
"""Regression tests for the 2026-09-15 neural-mesh architecture audit fixes.

Findings covered:
- #1  quarantine quorum was a dead gate (fresh voter per call + one local
      vote => `decided` unreachable). Now counted over DISTINCT ENVS via a
      persisted, gossiped ledger, so one env still cannot self-quarantine.
- #2  SpineCycleWorm's reentrancy guards were instance-scoped while the
      resources they protect are process-global, so a second instance (e.g.
      from a generic nature sweep) could run a concurrent cycle.
- #4  four divergent env-id implementations; only envid/envmesh knew about
      the 5-way wsl2-ubuntu / wsl2-kali split.
- #5  `_MESH_ENVS` was never updated for that split, so live 5-way targets
      reported `hops = -1` (unreachable).
- #7  no fabric operational state was persisted (routing tables, leader term,
      quorum tallies, shard catalogue, endpoint registry ...).
- #14 `mesh.describe_env()` hard-coded `virt = "hyperv_host"` on Windows.

Every test isolates spine state to a tmp dir so the developer's real
`~/.azazael/spine_state.json` is never read or written by the suite.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from CORE import spine_registry as sr


@pytest.fixture(autouse=True)
def isolated_spine_state(tmp_path, monkeypatch):
    """Keep the suite off the developer's real dot-directory."""
    monkeypatch.setattr(sr, "_STATE_FILE", tmp_path / "spine_state.json")
    monkeypatch.setattr(sr, "_GOSSIP_DIR", tmp_path / "gossip")
    sr.spine_reset()
    yield
    sr.spine_reset()


# ── #4 / #5 — canonical env identity ────────────────────────────────

def test_env_id_is_canonical_across_every_module():
    """All five former copies must return the SAME id."""
    from CORE.env_id import env_id
    from CORE.WORMS.expansion.mesh import _env_id
    from CORE.WORMS.expansion.spine import _local_env
    from CORE.WORMS.expansion.xenomorph import _local_env as xen_env
    from CORE.WORMS.expansion.envmesh import env_id_v2

    ids = {env_id(), _env_id(), _local_env(), xen_env(), env_id_v2(),
           sr._local_env_name()}
    assert len(ids) == 1, f"env id not unified across modules: {ids}"


def test_canonical_env_ids_are_the_five_known_buckets():
    from CORE.env_id import VALID_ENVS, env_id
    assert env_id() in VALID_ENVS
    assert set(VALID_ENVS) == {"windows", "wsl2-ubuntu", "wsl2-kali",
                               "bare", "kali"}


def test_is_known_env_accepts_legacy_and_split_wsl_ids():
    """The pre-split 'wsl2' and both split ids are all live targets."""
    from CORE.env_id import is_known_env
    assert is_known_env("wsl2")            # legacy spelling
    assert is_known_env("wsl2-ubuntu")
    assert is_known_env("wsl2-kali")
    assert is_known_env("windows")
    assert not is_known_env("bogus-env")
    assert not is_known_env("")


def test_route_hops_reports_split_wsl_targets_as_reachable():
    """#5 regression: these two used to report hops = -1."""
    from CORE.WORMS.expansion.mesh import MeshRouterWorm
    r = MeshRouterWorm()
    assert r.route("wsl2-ubuntu", {})["hops"] == 1
    assert r.route("wsl2-kali", {})["hops"] == 1
    assert r.route("all-down-nowhere", {})["hops"] == -1


def test_describe_env_reports_a_real_virtualisation():
    """#14 regression: Windows must not hard-code 'hyperv_host'."""
    from CORE.WORMS.expansion.mesh import describe_env
    d = describe_env()
    assert d["virt"] in {"hyperv_host", "windows_native", "wsl2", "vm",
                         "container", "bare_metal_or_unknown", "unknown"}
    assert "bare_likely" in d and "cpu" in d


def test_lilith_full_env_set_covers_both_wsl_flavours():
    """Unifying the env id must not silently downgrade a WSL queen lease."""
    from CORE.WORMS.expansion.xenomorph import Lilith
    assert "wsl2-ubuntu" in Lilith.FULL_ENVS
    assert "wsl2-kali" in Lilith.FULL_ENVS
    assert len(set(Lilith.FALLBACK_ORDER)) == len(Lilith.FALLBACK_ORDER)


# ── #1 — quarantine quorum is reachable, and still env-gated ────────

def test_quarantine_quorum_needs_two_distinct_envs():
    from CORE.WORMS.expansion.consensus import QuorumVoterWorm
    v = QuorumVoterWorm(quorum=2)
    first = v.vote("quarantine:SpecWorm", "windows", "quarantine")
    assert first["decided"] is False
    assert first["env_count"] == 1
    second = v.vote("quarantine:SpecWorm", "wsl2-ubuntu", "quarantine")
    assert second["decided"] is True
    assert set(second["envs"]) == {"windows", "wsl2-ubuntu"}


def test_same_env_cannot_stuff_the_ballot():
    from CORE.WORMS.expansion.consensus import QuorumVoterWorm
    v = QuorumVoterWorm(quorum=2)
    for _ in range(10):
        out = v.vote("quarantine:SpecWorm2", "windows", "quarantine")
    assert out["decided"] is False
    assert out["env_count"] == 1


def test_red_queen_single_env_still_stands_down():
    """The original safety intent is preserved: no lone self-quarantine."""
    from CORE.WORMS.expansion.spine import RedQueenWorm
    rq = RedQueenWorm()
    for _ in range(rq.QUARANTINE_MIN_FAILURES):
        sr.record_outcome("mesh", "ChronicWorm", False, 1.0, error="boom")
    assert rq.order_quarantine() == []


def test_red_queen_quarantines_once_a_peer_env_votes():
    """The gate is no longer dead: a second env's vote decides it."""
    from CORE.WORMS.expansion.spine import RedQueenWorm
    rq = RedQueenWorm()
    for _ in range(rq.QUARANTINE_MIN_FAILURES):
        sr.record_outcome("mesh", "ChronicWorm", False, 1.0, error="boom")
    sr.cast_quarantine_vote("quarantine:ChronicWorm", "wsl2-ubuntu",
                            "quarantine", quorum=rq.QUORUM)
    ordered = rq.order_quarantine()
    assert ordered, "cross-env quorum must be able to quarantine"
    assert ordered[0]["worm"] == "ChronicWorm"
    assert ordered[0]["env_count"] == 2


def test_quarantine_votes_survive_save_and_load():
    sr.cast_quarantine_vote("quarantine:PersistedWorm", "windows", "quarantine")
    assert sr.save_spine_state()["saved"] is True
    sr.spine_reset()
    assert sr.get_quarantine_votes() == {}
    restored = sr.load_spine_state()
    assert restored["quarantine_findings"] == 1
    assert "windows" in sr.get_quarantine_votes("quarantine:PersistedWorm")[
        "quarantine:PersistedWorm"]


# ── #2 — SpineCycleWorm cannot run concurrently with itself ─────────

def test_spine_cycle_global_gate_serializes_instances():
    from CORE.WORMS.expansion.spine import SpineCycleWorm, _GLOBAL_CYCLE_GATE
    first = SpineCycleWorm(min_interval_seconds=0.0)
    second = SpineCycleWorm(min_interval_seconds=0.0)
    _GLOBAL_CYCLE_GATE.acquire()
    try:
        skipped = second.run_cycle()
    finally:
        _GLOBAL_CYCLE_GATE.release()
    assert skipped.get("skipped") == "cycle_already_running_global"
    # and the gate is reusable afterwards
    assert first.run_cycle().get("success") is True


def test_spine_cycle_worm_excluded_from_generic_cluster_sweep():
    """A generic sweep must not drive the self-improvement cycle (U3: flat)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_mesh_cycle_under_test",
        Path(__file__).resolve().parents[1] / "scripts" / "run_mesh_cycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "SpineCycleWorm" in mod._NO_EXECUTE


# ── #7 — fabric operational state is persisted ─────────────────────

def test_route_weights_survive_an_instance_swap():
    from CORE.WORMS.expansion.mesh import MeshRouterWorm
    MeshRouterWorm().add_route("windows", "wsl2-ubuntu", 2.5)
    fresh = MeshRouterWorm()
    assert fresh.route("wsl2-ubuntu", {})["weight"] == 2.5


def test_leader_term_survives_an_instance_swap():
    from CORE.WORMS.expansion.consensus import LeaderElectorWorm
    first = LeaderElectorWorm()
    first.nominate("windows", "server", priority=8)
    term = first.leader()["term"]
    assert LeaderElectorWorm().leader()["term"] == term


def test_endpoint_registry_persists_but_inflight_load_does_not():
    from CORE.WORMS.expansion.servemesh import InferenceLoadBalancerWorm
    lb = InferenceLoadBalancerWorm()
    lb.register("lmstudio", "http://127.0.0.1:1234")
    lb.pick()  # leaves load at 1
    loads = InferenceLoadBalancerWorm().execute()["result"]["loads"]
    assert list(loads) == ["lmstudio"]
    assert loads["lmstudio"] == 0, "transient gauge must not be restored"


def test_provider_down_set_is_not_persisted():
    """Reachability is host-local; persisting it would make outages permanent."""
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    ProviderFailoverMeshWorm().mark_down("groq")
    assert ProviderFailoverMeshWorm()._down == set()


def test_fabric_state_survives_a_disk_roundtrip():
    from CORE.WORMS.expansion.servemesh import ModelShardServerWorm
    ModelShardServerWorm().publish("gemma", "s0", "windows", "gguf")
    assert sr.save_spine_state()["fabric_providers"] >= 1
    sr.spine_reset()
    assert sr.get_fabric_view() == {}
    restored = sr.load_spine_state()
    assert restored["fabric_providers"] >= 1
    assert ModelShardServerWorm().locate("gemma")["shards"]


def test_fabric_state_merges_by_recency():
    """Recency decides the winner — inside the drift window.

    Timestamps are relative to now (not year 2999, the shorthand this test
    originally used) because a far-future stamp is now refused rather than
    honoured: see tests/test_mesh_clock_poison.py.
    """
    from datetime import datetime, timedelta

    sr.publish_fabric_state("DemoWorm", {"n": 1})
    older = {"provider": "DemoWorm",
             "updated_at": (datetime.now() - timedelta(seconds=30)).isoformat(),
             "state": {"n": 0}}
    newer = {"provider": "DemoWorm",
             "updated_at": (datetime.now() + timedelta(seconds=2)).isoformat(),
             "state": {"n": 9}}
    sr._merge_fabric({"DemoWorm": older})
    assert sr.get_fabric_state("DemoWorm")["n"] == 1  # stale loses
    sr._merge_fabric({"DemoWorm": newer})
    assert sr.get_fabric_state("DemoWorm")["n"] == 9  # fresh wins
    # ...and a year-2999 stamp does not get to win, in this or any format.
    sr._merge_fabric({"DemoWorm": {"provider": "DemoWorm",
                                   "updated_at": "2999-01-01T00:00:00",
                                   "state": {"n": 42}}})
    assert sr.get_fabric_state("DemoWorm")["n"] == 9


# ── cross-env transport ────────────────────────────────────────────

def test_quarantine_and_fabric_travel_in_the_gossip_payload(monkeypatch):
    """A peer's vote + fabric state must arrive over the signed payload."""
    monkeypatch.setenv("AZAZAEL_GOSSIP_POLICY", "off")
    sr.cast_quarantine_vote("quarantine:NetWorm", "windows", "quarantine")
    sr.publish_fabric_state("NetWorm", {"seats": 3})
    payload = sr.export_spine_gossip()
    assert "quarantine_votes" in payload and "fabric_state" in payload

    sr.spine_reset()
    payload["gossip_from"] = "peer-env"
    payload["seq"] = int(payload.get("seq") or 0) + 10
    stats = sr.merge_spine_gossip(payload)
    assert stats.get("merged") is True, stats
    assert "NetWorm" in sr.get_fabric_view()
    assert sr.get_quarantine_votes("quarantine:NetWorm")["quarantine:NetWorm"]
