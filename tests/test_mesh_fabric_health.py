# coding: utf-8
"""Regression tests for the third mesh pass (2026-09-15): lease, coverage, health.

Closes the three items the audit's "recommended next order" named after the
follow-up pass:

- **A — the leader lease (finding #7).** `Lilith._lease_until` was written and
  never read, `claim()` re-claimed unconditionally, and
  `PraetorianWorm.observe_term()` computed `moult_eligible` / `needs_quorum`
  that nothing consumed. The lease is now the authority (persisted +
  gossiped, so a peer's live lease is visible), a renewal keeps the term, and
  a moult is a real request through the shared distinct-env quorum.
- **B — probe coverage (finding #10).** The Red Queen probed only
  mesh/synapse/immune, so the natures with the most mutable state and the
  fewest tests were never adversarially probed.
- **C — the servemesh health layer (finding #12).** Load leaked (a caller that
  raised starved its endpoint forever), `fail` was only a tiebreak, and
  `mark_up()` had no caller so provider recovery never happened.

Spine state is isolated to a tmp dir, and the breaker registry — which is
process-global by design — is snapshotted per test so one test's health state
cannot leak into the next.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from CORE import spine_registry as sr
from CORE.WORMS.expansion import xenomorph as XEN


@pytest.fixture(autouse=True)
def isolated_spine_state(tmp_path, monkeypatch):
    """Keep the suite off the developer's real dot-directory."""
    monkeypatch.setattr(sr, "_STATE_FILE", tmp_path / "spine_state.json")
    monkeypatch.setattr(sr, "_GOSSIP_DIR", tmp_path / "gossip")
    sr.spine_reset()
    yield
    sr.spine_reset()


@pytest.fixture(autouse=True)
def isolated_breakers():
    """The breaker registry is process-global by design; isolate it per test.

    Health state is deliberately *not* durable (a restart should re-probe),
    but it IS deliberately shared across worm instances within a process — so
    a test that opens a breaker would otherwise be visible to every later
    test in the same process.
    """
    from CORE import circuit_breaker as cb
    with cb.breaker_registry._lock:
        saved = dict(cb.breaker_registry._breakers)
        cb.breaker_registry._breakers.clear()
    yield
    with cb.breaker_registry._lock:
        cb.breaker_registry._breakers.clear()
        cb.breaker_registry._breakers.update(saved)


def _as_env(monkeypatch, env: str) -> None:
    """Make the xenomorph module believe it is running in `env`."""
    monkeypatch.setattr(XEN, "_local_env", lambda: env)


# ── A — the leader lease ────────────────────────────────────────────

def test_claim_starts_a_lease_and_renewal_keeps_the_term(monkeypatch):
    _as_env(monkeypatch, "windows")
    lil = XEN.Lilith()
    first = lil.claim(peers=None)
    assert first["role"] == "queen_interim"
    assert first["renewed"] is False and first["term"] == 1

    second = lil.claim(peers=None)
    # A re-claim is a heartbeat, not a new election.
    assert second["renewed"] is True
    assert second["term"] == 1


def test_peer_stands_down_while_another_env_holds_a_live_lease(monkeypatch):
    """The line that stops a partitioned host becoming an independent queen."""
    _as_env(monkeypatch, "windows")
    XEN.Lilith().claim(peers=None)

    _as_env(monkeypatch, "wsl2-ubuntu")
    peer = XEN.Lilith()
    got = peer.claim(peers=None)
    assert got["role"] == "follower"
    assert got["reason"] == "lease_held_by_peer"
    assert got["lease"]["env"] == "windows"
    assert got["lease"]["valid"] is True


def test_peer_can_take_over_an_expired_lease_and_bumps_the_term(monkeypatch):
    sr.publish_fabric_state("Lilith", {
        "_lease_env": "bare", "_lease_until": "2020-01-01T00:00:00",
        "_term": 7, "_genome_version": 3,
    })
    _as_env(monkeypatch, "windows")
    got = XEN.Lilith().claim(peers=None)
    assert str(got["role"]).startswith("queen")
    assert got["renewed"] is False
    assert got["term"] == 8          # a claim that succeeds a foreign lease


def test_lease_is_published_so_peers_can_see_it(monkeypatch):
    _as_env(monkeypatch, "wsl2-kali")
    XEN.Lilith().claim(peers=None)
    state = sr.get_fabric_state("Lilith")
    assert state.get("_lease_env") == "wsl2-kali"
    assert state.get("_lease_until")
    assert state.get("_term") == 1


def test_lease_holder_reports_a_real_expiry(monkeypatch):
    _as_env(monkeypatch, "windows")
    lil = XEN.Lilith()
    lil.claim(peers=None)
    holder = lil.lease_holder()
    assert holder["valid"] is True and holder["env"] == "windows"
    assert holder["expires_in"] > 0
    # An unparseable expiry must read as EXPIRED, never as a permanent lease.
    lil._lease_until = "not-a-date"
    assert lil.lease_holder()["valid"] is False


def test_bare_preference_still_outranks_a_local_claim(monkeypatch):
    """The lease must not override the pre-existing bare preference."""
    _as_env(monkeypatch, "windows")
    got = XEN.Lilith().claim(peers=["wsl2-ubuntu", "bare"])
    assert got["role"] == "follower"
    assert got["reason"] == "bare_peer_present"


def test_moult_requires_a_quorum_and_has_an_operator_channel(monkeypatch):
    _as_env(monkeypatch, "windows")
    praetorian = XEN.PraetorianWorm()
    for _ in range(3):
        succ = praetorian.observe_term(term=5, queen_present=False)
    assert succ["moult_eligible"] is True
    assert succ["needs_quorum"] is True

    # One env can never moult alone.
    lone = XEN.Lilith().request_moult(term=5)
    assert lone["promoted"] is False
    assert lone["reason"] == "awaiting_quorum"

    # A peer's approval is what promotion waits for.
    sr.cast_quarantine_vote("moult:windows", "wsl2-kali", "moult", quorum=2)
    two = XEN.Lilith().request_moult(term=5)
    assert two["promoted"] is True
    assert two["envs"] == ["windows", "wsl2-kali"]


def test_moult_does_not_run_when_a_queen_is_present():
    praetorian = XEN.PraetorianWorm()
    for _ in range(3):
        succ = praetorian.observe_term(term=5, queen_present=True)
    assert succ["moult_eligible"] is False


def test_operator_force_promotes_a_moult(monkeypatch):
    _as_env(monkeypatch, "windows")
    got = XEN.Lilith().request_moult(term=9, force=True)
    assert got["promoted"] is True and got["forced"] is True
    votes = sr.get_quarantine_votes("moult:windows")
    assert "operator" in (votes.get("moult:windows") or {})


# ── B — probe coverage ──────────────────────────────────────────────

def test_red_queen_probes_the_fabric_natures():
    from CORE.WORMS.expansion.spine import RedQueenWorm
    covered = set(RedQueenWorm.PROBE_NATURES)
    assert {"consensus", "federated", "servemesh", "engram"} <= covered
    # The original three must not have been dropped in the process.
    assert {"mesh", "synapse", "immune"} <= covered


def test_execute_reports_per_category_coverage():
    from CORE.WORMS.expansion.spine import RedQueenWorm
    result = RedQueenWorm().execute(limit=1)["result"]
    if result["stood_down"]:
        # Stood down is a topology verdict (foreign lease / bare peer /
        # capability gate), NOT a hardware verdict — report which one so a
        # skip line is attributable instead of "not queen-capable".
        pytest.skip(
            "RedQueen stood down (role=%s reason=%s env=%s; host capable, see Lilith.claim)"
            % (result.get("queen_role", "?"), result.get("queen_reason", ""),
               result.get("queen_env", "?"))
        )
    assert set(result["coverage"]) == set(RedQueenWorm.PROBE_NATURES)
    assert result["natures_probed"] == sorted(RedQueenWorm.PROBE_NATURES)
    assert all(n >= 1 for n in result["coverage"].values())


def test_federated_execute_does_not_consume_submissions():
    """A probe must not destroy state now that `federated` is probed."""
    from CORE.WORMS.expansion.federated import FederatedAggregatorWorm
    agg = FederatedAggregatorWorm()
    agg.submit("windows", [1.0, 2.0], 4)
    before = (agg._rounds, len(agg._clients))
    agg.execute()
    agg.execute()
    assert (agg._rounds, len(agg._clients)) == before
    # The real aggregation is still reachable, and still consumes.
    assert agg.aggregate()["aggregated"] is True
    assert agg._rounds == 1 and agg._clients == {}


# ── C — servemesh health ────────────────────────────────────────────

def test_a_lease_is_reclaimed_when_the_caller_never_releases():
    from CORE.WORMS.expansion.servemesh import InferenceLoadBalancerWorm
    lb = InferenceLoadBalancerWorm()
    lb.register("epA", "http://localhost:1234/v1")
    lb.register("epB", "http://localhost:2345/v1")
    assert lb.pick()["picked"] == "epA"
    assert lb.health()["endpoints"]["epA"]["load"] == 1

    import time as _t
    out = lb.sweep(now=_t.time() + lb.LEASE_TTL_S + 1)
    assert out["reclaimed"] == 1
    assert lb.health()["endpoints"]["epA"]["load"] == 0


def test_release_recomputes_load_and_reports_the_breaker():
    from CORE.WORMS.expansion.servemesh import InferenceLoadBalancerWorm
    lb = InferenceLoadBalancerWorm()
    lb.register("epC", "http://localhost:1234/v1")
    lb.pick()
    out = lb.release("epC", ok=True)
    assert out["load"] == 0 and out["open"] is False
    assert lb.release("unknown-endpoint")["success"] is False


def test_repeated_failures_open_the_breaker_and_pick_prefers_the_healthy_one():
    from CORE.WORMS.expansion.servemesh import InferenceLoadBalancerWorm
    lb = InferenceLoadBalancerWorm()
    lb.register("epD", "http://localhost:1111/v1")
    lb.register("epE", "http://localhost:2222/v1")
    for _ in range(lb.FAILURE_THRESHOLD):
        lb.pick()
        lb.release("epD", ok=False)
    health = lb.health()
    assert health["endpoints"]["epD"]["open"] is True
    assert health["open"] == ["epD"]
    # The sick endpoint is no longer chosen while a healthy one exists.
    for _ in range(3):
        assert lb.pick()["picked"] == "epE"


def test_pick_falls_back_to_the_pool_rather_than_refusing_to_route():
    """All-open must degrade, not return nothing — 'no endpoint' is worse."""
    from CORE.WORMS.expansion.servemesh import InferenceLoadBalancerWorm
    lb = InferenceLoadBalancerWorm()
    lb.register("epF", "http://localhost:1111/v1")
    for _ in range(lb.FAILURE_THRESHOLD):
        lb.pick()
        lb.release("epF", ok=False)
    picked = lb.pick()
    assert picked["picked"] == "epF"
    assert picked["degraded_pool"] is True


def test_reprobe_recovers_a_provider_through_the_half_open_path():
    import time as _t
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    fo = ProviderFailoverMeshWorm()
    fo.mark_down("kilo", error="unreachable")
    # Not due yet: the cooldown has not elapsed.
    assert "kilo" not in fo.probe_due()
    fo._breaker("kilo").last_failure_time = _t.time() - 10_000
    assert "kilo" in fo.probe_due()

    out = fo.reprobe(probe=lambda p: p == "kilo")
    assert out["recovered"] == ["kilo"]
    assert "kilo" not in fo.stats()["down"]
    assert fo.stats()["fail_streak"].get("kilo") is None


def test_reprobe_that_fails_keeps_the_provider_down_and_backs_off():
    import time as _t
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    fo = ProviderFailoverMeshWorm()
    fo.mark_down("jan", error="unreachable")
    before = fo.stats()["cooldowns"]["jan"]
    fo._breaker("jan").last_failure_time = _t.time() - 10_000
    out = fo.reprobe(probe=lambda p: False)
    assert out["still_down"] == ["jan"]
    assert "jan" in fo.stats()["down"]
    assert fo.stats()["cooldowns"]["jan"] >= before
    assert fo.stats()["fail_streak"]["jan"] == 2


def test_probe_due_skips_credential_failures():
    """A network probe cannot supply a missing API key, so do not spend one.

    Also prevents a 401 from the probe being misread as 'recovered'.
    """
    import time as _t
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    fo = ProviderFailoverMeshWorm()
    fo.mark_down("groq", error="credential_missing")
    fo._breaker("groq").last_failure_time = _t.time() - 10_000
    assert fo.probe_due() == []


def test_backoff_grows_exponentially_then_caps():
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    fo = ProviderFailoverMeshWorm()
    seen = []
    for _ in range(10):
        fo.mark_down("vllm", error="unreachable")
        seen.append(fo.stats()["cooldowns"]["vllm"])
    assert seen[0] == fo.RECOVERY_BASE_S
    assert seen[1] > seen[0]
    assert max(seen) <= fo.RECOVERY_MAX_S


def test_mark_up_resets_the_backoff_and_streak():
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    fo = ProviderFailoverMeshWorm()
    for _ in range(4):
        fo.mark_down("sglang", error="unreachable")
    assert fo.stats()["fail_streak"]["sglang"] == 4
    out = fo.mark_up("sglang")
    assert out["was_down"] is True
    assert "sglang" not in fo.stats()["down"]
    assert "sglang" not in fo.stats()["fail_streak"]
    # ... and the cooldown is back to its base on the next failure.
    fo.mark_down("sglang", error="unreachable")
    assert fo.stats()["cooldowns"]["sglang"] == fo.RECOVERY_BASE_S


def test_default_network_probe_can_be_disabled(monkeypatch):
    import time as _t
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    monkeypatch.setenv(ProviderFailoverMeshWorm.REPROBE_ENV, "0")
    fo = ProviderFailoverMeshWorm()
    fo.mark_down("openwebui", error="unreachable")
    fo._breaker("openwebui").last_failure_time = _t.time() - 10_000
    out = fo.reprobe()
    assert out["skipped"] == "probe_disabled"
    assert out["attempted"] == 0


def test_next_provider_skips_a_provider_whose_breaker_is_open():
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    fo = ProviderFailoverMeshWorm()
    assert fo.next_provider()["provider"] == "llamaserver"
    for _ in range(fo.FAILURE_THRESHOLD):
        fo.mark_down("llamaserver", error="unreachable")
    # The local model tier fronts the tail (mythos/v2/qwopus/ornith inserted
    # between llamaserver and lmstudio), so the next hand-off is the first
    # local entry, not lmstudio.
    assert fo.next_provider()["provider"] == "llama_mythos"


def test_servemesh_stage_reports_sweep_and_reprobe(monkeypatch):
    from CORE.WORMS.expansion import spine as SP
    cyc = SP.SpineCycleWorm()
    monkeypatch.setattr(cyc, "_publish_local_shards", lambda shards: 0)
    res = cyc._fabric_servemesh()
    assert "swept" in res
    assert res["reprobe"]["success"] is True
    assert res["provider_health"]
