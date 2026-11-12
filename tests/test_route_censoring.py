# coding: utf-8
"""Route-outcome censoring must EXECUTE, and the cycle must not fake a verdict.

Forensic 2026-09-29. The censoring mechanism existed, was documented, had a
docstring explaining exactly why it existed -- and had never run once.

    entries with a censored counter: 0   (of 492, in the live ledger)
    route failures on the ledger:   180  (wsl2-ubuntu 117, wsl2-kali 63)

Three separate causes, one per test below:

1. ``MeshRouterWorm.send()`` routed its censored case AROUND its own
   ``record_route_result`` seam, calling ``record_outcome`` inline, so the
   censored half of the traffic was invisible to every hook on that method.
2. ``record_route_result`` had no ``censored`` parameter, so the seam could not
   express what it was being asked to record.
3. ``SpineCycleWorm._mesh_route_feedback`` phase 1 settled the route it had
   proposed the previous cycle from ``_nature_success_rate(dst)`` -- the
   health of the WORMS on the far side. Phase 2 always proposes to the
   LEAST-healthy nature and never delivers on it, so the cycle manufactured a
   standing -1 for routes it had never used. That is playing-field TODO G2's
   self-sustaining penalty, produced by the loop built to close it, and it was
   the dominant writer of the live route ledger.

A censored outcome is counted and never becomes a policy estimate. The cycle
reads the ledger; only ``send()`` writes to it.

Spine state is isolated to a tmp dir so the developer's real
~/.azazael/spine_state.json is never read or written by the suite.
"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from CORE import mesh_transport as mt
from CORE import spine_registry as sr
from CORE.WORMS.expansion.mesh import MeshRouterWorm


@pytest.fixture(autouse=True)
def isolated_spine_state(tmp_path, monkeypatch):
    """Keep the suite off the developer's real dot-directory."""
    monkeypatch.setattr(sr, "_STATE_FILE", tmp_path / "spine_state.json")
    monkeypatch.setattr(sr, "_GOSSIP_DIR", tmp_path / "gossip")
    sr.spine_reset()
    yield
    sr.spine_reset()


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


# ── key hygiene: a route key must be parseable back into a route ──────

@pytest.mark.parametrize("bad", ["", "   ", "route", "a>b", None])
def test_an_unparseable_destination_never_becomes_a_route_key(bad):
    """`route:<src>><dst>` is parsed by splitting on '>'.

    An empty or '>'-bearing dst mints a key that looks like a route and is not
    one. The live ledger carried `route:windows>route` (2 successes) from a
    model-ranked environment name reaching this seam unvalidated, so the guard
    belongs at the seam rather than only at the caller.
    """
    router = MeshRouterWorm()
    router.record_route_result(bad, False, duration_ms=1.0)
    route_keys = [k for k in sr._HOMEOSTAT if k.startswith("route:")]
    assert route_keys == [], f"malformed destination minted {route_keys}"


def test_a_wellformed_destination_still_records():
    """The guard must not swallow legitimate routes."""
    router = MeshRouterWorm()
    router.record_route_result("wsl2-ubuntu", True, duration_ms=12.0)
    key = f"route:{router._env}>wsl2-ubuntu"
    assert sr._HOMEOSTAT.get(key, {}).get("successes") == 1


def test_ebpf_telemetry_does_not_write_a_route_verdict():
    """Kernel syscall alerts are not observations of a route.

    `scripts/spine_cycle_apply.py` runs HOURLY and was writing an eBPF
    alert count into the shared `route:<src>><env>` key with the 8s collection
    window hardcoded as a route latency. It is the dominant writer of the live
    route ledger, and it is what produced the alternating [95ms, 8000ms]
    history and the standing -1 on both wsl2 routes.
    """
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parent.parent /
           "scripts" / "spine_cycle_apply.py").read_text(encoding="utf-8")
    assert 'record_outcome("ebpf"' in src, \
        "eBPF telemetry no longer records under its own namespace"
    assert "m.record_route_result(env, n_alerts == 0" not in src, \
        "eBPF alert counts are being written as a route verdict again"


def test_a_model_ranked_order_is_filtered_to_the_declared_candidates():
    """Model output must not name a destination.

    `rank_trials` returns an order that became a dst name verbatim. An
    off-candidate entry became a real ledger key.
    """
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parent.parent /
           "scripts" / "spine_cycle_apply.py").read_text(encoding="utf-8")
    assert "if e in cands" in src, \
        "the ranked order is no longer filtered to the declared candidates"


# ── 1 + 2: the censored outcome must actually reach the ledger ─────────

def test_a_censored_route_outcome_reaches_the_ledger(monkeypatch):
    """A refused connection is counted, and changes NOTHING else.

    This is the "never executed" pin. Before the fix the ledger's `censored`
    bucket stayed at zero forever, so nothing downstream could tell the
    difference between "this route was never tried" and "this route is fine".
    """
    router = MeshRouterWorm()
    key = f"route:{router._env}>dead-peer"
    dead = _free_port()
    monkeypatch.setattr(mt, "port_for", lambda env: dead)

    res = router.send("dead-peer", {"op": "probe"}, timeout=5)
    assert res["status"] == "env_error", "a closed port is an env_error"
    assert res["learned"] is False, "a missing listener is not a bad route"

    rec = sr._HOMEOSTAT.get(key)
    assert rec is not None, "the censored outcome left no trace in the ledger"
    assert rec.get("censored", 0) == 1, (
        "the censored bucket did not move -- the mechanism did not execute")
    # Censored means counted and excluded: no policy estimate from noise.
    assert rec.get("successes", 0) == 0
    assert rec.get("failures", 0) == 0
    assert rec.get("consec_fail", 0) in (0, None)
    # ...and no Q update, so route_weight() cannot be moved by a dead peer.
    assert sr.get_q_update_counts().get(key, 0) == 0


def test_censored_and_learnable_outcomes_share_one_seam(monkeypatch):
    """Both halves of the traffic go through `record_route_result`.

    The censored case used to call `record_outcome` inline, so any hook on the
    seam (instrumentation, a test, an alternate ledger) saw only half of it.

    The transport is stubbed rather than dialled: this is a test of which seam
    the outcome travels through, not of the wire, and binding a second real
    listener here made the OS hand `live-peer` to this file and to
    test_mesh_transport_e2e in the same run, so one test's payload landed in
    the other's inbox.
    """
    router = MeshRouterWorm()
    seen = []
    monkeypatch.setattr(
        router, "record_route_result",
        lambda dst, ok, duration_ms=0.0, censored=False, error=None:
            seen.append((dst, ok, censored)))

    def _fake_send(self, dst_env, payload, timeout=10.0):
        if dst_env == "live-peer":
            return {"success": True, "status": "delivered",
                    "learnable": True, "receipt": "r-1"}
        return {"success": False, "status": "env_error", "learnable": False,
                "error": "connection refused"}

    monkeypatch.setattr(mt.MeshTransport, "send", _fake_send)

    router.send("dead-peer", {"op": "probe"}, timeout=5)
    assert seen == [("dead-peer", False, True)], \
        "env_error must arrive censored, through the seam"

    router.send("live-peer", {"op": "probe"}, timeout=5)
    assert seen[-1] == ("live-peer", True, False), \
        "a delivered payload is learnable route signal"
    assert seen == [("dead-peer", False, True), ("live-peer", True, False)]
    assert router._env_errors == 1, "the unlearnable path is not counted"


def test_an_env_error_is_surfaced_as_a_spine_event(monkeypatch):
    """Censored is silent about policy but not about operations.

    The error text rides the event, so the censored bucket can stay a pure
    count and still leave a diagnostic trail.
    """
    router = MeshRouterWorm()
    events = []
    monkeypatch.setattr(sr, "record_spine_event",
                        lambda kind, detail: events.append((kind, detail)))
    monkeypatch.setattr(mt, "port_for", lambda env: _free_port())

    router.send("dead-peer", {"op": "probe"}, timeout=5)
    assert events, "an unlearnable failure produced no spine event"
    assert events[0][0] == "route_env_error"
    assert events[0][1]["dst"] == "dead-peer"


# ── 3: the cycle must read the ledger, never invent a verdict ──────────

def _cycle():
    from CORE.WORMS.expansion.spine import SpineCycleWorm
    return SpineCycleWorm(min_interval_seconds=0.0)


def test_the_cycle_reports_an_unused_route_instead_of_settling_it(monkeypatch):
    """No observation -> no verdict. A proposal is a prior, not a measurement."""
    cyc = _cycle()
    cyc._pending_route = {"dst": "never-used-nature", "ms": 0.0}
    # A catastrophically unhealthy nature, which is exactly what phase 2 picks
    # and what the old code turned into a route failure.
    monkeypatch.setattr(cyc, "_category_rates",
                        lambda: {"never-used-nature": 0.0})

    before = dict(sr.get_q_update_counts())
    out = cyc._mesh_route_feedback()

    assert out.get("settled", {}).get("skipped") == "no_observation", (
        "the cycle settled a route it had no observation of")
    assert out["settled"]["dst"] == "never-used-nature"
    after = sr.get_q_update_counts()
    for key, n in before.items():
        assert after.get(key, 0) == n, (
            f"the cycle wrote a route verdict it did not observe: {key}")


def test_a_category_health_rate_is_not_a_route_verdict(monkeypatch):
    """The specific conflation the forensic found.

    Phase 2 proposes to the least-healthy nature; phase 1 then declared the
    route failed because that nature was unhealthy. Since phase 2 never
    delivers, that produced a permanent -1 for a route never used -- a penalty
    no amount of good behaviour could clear, because nothing was ever measured.
    """
    cyc = _cycle()
    cyc._pending_route = {"dst": "sick-category", "ms": 0.0}
    monkeypatch.setattr(cyc, "_category_rates", lambda: {"sick-category": 0.0})
    monkeypatch.setattr(cyc, "_route_observation", lambda dst: None)

    out = cyc._mesh_route_feedback()
    assert "success" not in out.get("settled", {}), (
        "the cycle still converts a nature rate into a route verdict")
    assert out["settled"].get("fleet_rate") is None, (
        "a fleet rate is being reported as the route's outcome")


def test_a_observed_route_is_reported_from_the_ledger(monkeypatch):
    """When the ledger DOES hold observations, the cycle reports them.

    The other half of read-not-write: the report is still produced, and it is
    sourced from the ledger rather than from a proxy.
    """
    cyc = _cycle()
    cyc._pending_route = {"dst": "wsl2-ubuntu", "ms": 12.0}
    monkeypatch.setattr(cyc, "_route_observation", lambda dst: (30, 40))

    out = cyc._mesh_route_feedback()
    settled = out.get("settled", {})
    assert settled.get("source") == "route_ledger"
    assert settled.get("successes") == 30
    assert settled.get("evidence") == 40
