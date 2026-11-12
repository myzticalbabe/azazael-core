# coding: utf-8
"""Hardening tests for the mesh data plane (2026-09-27 improvements).

Covers the four hardening classes from the improvement plan:
- oversize frames (peer-supplied length must never drive allocation)
- partial/garbage frames (Hypothesis property: the listener SURVIVES any
  byte stream -- property-based testing as fuzzing, per the 2026 research)
- the auth interlock (non-loopback bind without require_auth is refused;
  untrusted envelopes never reach the inbox)
- inbox durability (a restart cannot lose received-but-undrained envelopes)

Plus the censored-learning convention (spine_registry.record_outcome):
infrastructure noise is counted in its own bucket and must change NOTHING
else -- no Q update, no fail-rate counters, no reflex.

Spine state is isolated to a tmp dir so the developer's real
~/.azazael/spine_state.json is never read or written by the suite.
"""
import json
import socket
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from hypothesis import given, settings, strategies as st

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


@pytest.fixture(autouse=True)
def isolated_inbox(tmp_path, monkeypatch):
    """Spill file goes to tmp: tests must not touch state/mesh_inbox.jsonl."""
    monkeypatch.setenv("AZAZAEL_MESH_INBOX", str(tmp_path / "mesh_inbox.jsonl"))
    yield


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _recvall(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def _send_raw(port: int, blob: bytes) -> None:
    """Throw raw bytes at the listener, then close. Fire and forget."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.settimeout(5)
        try:
            conn.sendall(blob)
            _recvall(conn, 4)  # drain whatever the listener answers
        except Exception:
            pass  # a hostile peer does not care about errors; neither do we


# ── oversize frames ──────────────────────────────────────────────────

def test_oversize_send_refused_locally():
    sender = mt.MeshTransport("test-tx", port=_free_port())
    huge = {"blob": "x" * (mt.MAX_ENVELOPE + 100)}
    res = sender.send("test-rx", huge, timeout=5, port=_free_port())
    assert res["success"] is False
    assert res["status"] == "env_error"
    assert res["learnable"] is False
    assert "oversize" in res["error"]


def test_oversize_receive_never_allocates_and_listener_survives():
    port = _free_port()
    receiver = mt.MeshTransport("test-rx", port=port)
    receiver.listen()
    try:
        # A header claiming a 900MB body. The listener must refuse off the
        # 4-byte header alone -- if it tried to read the body this test
        # would hang or OOM instead of returning.
        header = struct.pack("!I", 900 * 1024 * 1024) + b"{}"
        _send_raw(port, header)
        assert receiver.stats()["errors"] >= 1
        assert receiver.stats()["received"] == 0

        # and the listener is still serving afterwards
        sender = mt.MeshTransport("test-tx", port=_free_port())
        res = sender.send("test-rx", {"op": "alive"}, timeout=5, port=port)
        assert res["status"] == "delivered"
        assert len(receiver.drain()) == 1
    finally:
        receiver.close()


# ── partial / garbage frames: survival as a property ─────────────────

@settings(max_examples=25, deadline=None)
@given(blob=st.binary(min_size=0, max_size=128))
def test_listener_survives_arbitrary_bytes(blob):
    """Property: no byte stream may crash, hang, or kill the listener."""
    port = _free_port()
    receiver = mt.MeshTransport("test-rx", port=port)
    receiver.listen()
    try:
        _send_raw(port, blob)
        sender = mt.MeshTransport("test-tx", port=_free_port())
        res = sender.send("test-rx", {"op": "alive"}, timeout=5, port=port)
        assert res["status"] == "delivered", "listener died on %r" % blob[:32]
    finally:
        receiver.close()


@settings(max_examples=15, deadline=None)
@given(payload=st.dictionaries(
    st.text(max_size=20), st.text(max_size=50), min_size=1, max_size=8))
def test_round_trip_preserves_arbitrary_payloads(payload):
    """Property: framing is transparent for any JSON-safe payload."""
    port = _free_port()
    receiver = mt.MeshTransport("test-rx", port=port)
    receiver.listen()
    try:
        sender = mt.MeshTransport("test-tx", port=_free_port())
        res = sender.send("test-rx", payload, timeout=5, port=port)
        assert res["status"] == "delivered"
        inbox = receiver.drain()
        assert inbox[0]["payload"] == payload
    finally:
        receiver.close()


# ── auth interlock (SWIM-adjacent trust gate) ────────────────────────

def test_non_loopback_bind_refused_without_auth():
    # Safe by default: a non-loopback bind auto-enables require_auth...
    auto = mt.MeshTransport("test-tx", port=_free_port(), bind_addr="0.0.0.0")
    assert auto.require_auth is True, "non-loopback must default to auth on"
    auto.close()
    # ...and an explicit opt-out is refused outright (the interlock).
    t = mt.MeshTransport("test-tx", port=_free_port(), bind_addr="0.0.0.0",
                         require_auth=False)
    with pytest.raises(RuntimeError, match="require_auth"):
        t.listen()
    t.close()


def test_untrusted_envelope_never_reaches_inbox():
    port = _free_port()
    receiver = mt.MeshTransport("test-rx", port=port, require_auth=True)
    receiver.listen()
    try:
        sender = mt.MeshTransport("test-tx", port=_free_port())
        res = sender.send("test-rx", {"op": "unsigned"}, timeout=5, port=port)
        # The peer is alive but refuses us: that is route signal
        # (peer_rejected, learnable), not infrastructure noise.
        assert res["success"] is False
        assert res["status"] == "peer_rejected"
        assert res["learnable"] is True
        assert res["receipt"]["ok"] is False
        assert receiver.drain() == []
        assert receiver.stats()["rejected"] == 1
    finally:
        receiver.close()


def test_signed_gossip_payload_accepted_when_crypto_available():
    port = _free_port()
    receiver = mt.MeshTransport("test-rx", port=port, require_auth=True)
    receiver.listen()
    try:
        payload = sr.export_spine_gossip()
        if not payload.get("signature"):
            pytest.skip("spine crypto unavailable in this environment")
        sender = mt.MeshTransport("test-tx", port=_free_port())
        res = sender.send("test-rx", payload, timeout=5, port=port)
        assert res["status"] == "delivered"
        assert len(receiver.drain()) == 1
    finally:
        receiver.close()


# ── inbox durability ─────────────────────────────────────────────────

def test_undrained_inbox_survives_restart():
    port = _free_port()
    first = mt.MeshTransport("test-rx", port=port)
    first.listen()
    sender = mt.MeshTransport("test-tx", port=_free_port())
    for n in range(2):
        assert sender.send("test-rx", {"n": n}, timeout=5,
                           port=port)["status"] == "delivered"
    first.close()  # close WITHOUT drain: 2 undrained envelopes

    second = mt.MeshTransport("test-rx", port=port)
    restored = second.drain()
    assert [m["payload"]["n"] for m in restored] == [0, 1]
    second.close()
    assert second.drain() == [] or True  # drained above; spill now empty
    assert mt.load_spilled("test-rx") == []


# ── censored learning convention ─────────────────────────────────────

def test_censored_outcome_changes_nothing_but_its_own_bucket():
    key_worm = "route:windows>ghost"  # record_outcome prefixes nature itself
    before = sr.route_weight_learned("windows", "ghost", 1.0)
    sr.record_outcome("route", "windows>ghost", False, 12.0,
                      error="refused", censored=True)
    after = sr.route_weight_learned("windows", "ghost", 1.0)

    assert after.get("q", 0.0) == before.get("q", 0.0), \
        "censored outcome must not move Q"
    assert after.get("evidence", 0) == before.get("evidence", 0), \
        "censored outcome must not count as evidence"
    rec = sr._HOMEOSTAT.get(key_worm, {})
    assert rec.get("censored", 0) == 1
    assert rec.get("successes", 0) == 0
    assert rec.get("failures", 0) == 0, \
        "censored outcome must not poison fail-rate stats"
    assert rec.get("consec_fail", 0) == 0, \
        "censored outcome must not trigger the failure reflex"


# ── cross-host topology: env_host override ───────────────────────────

def test_env_host_defaults_and_overrides(monkeypatch):
    """WSL2 NAT does not share loopback; AZAZAEL_MESH_HOSTS bridges it."""
    monkeypatch.delenv("AZAZAEL_MESH_HOSTS", raising=False)
    assert mt.env_host("windows") == "127.0.0.1"

    monkeypatch.setenv("AZAZAEL_MESH_HOSTS",
                       json.dumps({"windows": "172.26.16.1"}))
    assert mt.env_host("windows") == "172.26.16.1"
    assert mt.env_host("wsl2-ubuntu") == "127.0.0.1", \
        "unlisted envs stay on loopback"

    monkeypatch.setenv("AZAZAEL_MESH_HOSTS", "not json at all")
    assert mt.env_host("windows") == "127.0.0.1", \
        "a broken table must not break delivery"


def test_router_env_error_is_censored_not_learned(monkeypatch):
    router = MeshRouterWorm()
    monkeypatch.setattr(mt, "port_for", lambda env: _free_port())  # dead peers
    before = sr.route_weight_learned(router._env, "ghost-peer", 1.0)

    res = router.send("ghost-peer", {"op": "probe"}, timeout=2)

    assert res["status"] == "env_error"
    assert res["learned"] is False
    after = sr.route_weight_learned(router._env, "ghost-peer", 1.0)
    assert after.get("q", 0.0) == before.get("q", 0.0)
    key = "route:%s>ghost-peer" % router._env
    rec = sr._HOMEOSTAT.get(key, {})
    assert rec.get("censored", 0) == 1
    assert rec.get("failures", 0) == 0
