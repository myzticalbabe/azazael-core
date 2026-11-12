# coding: utf-8
"""End-to-end tests for the neural-mesh data plane (audit 2026-09-27).

The 390-test mesh suite was green while the wire itself had ZERO coverage:
no test imported CORE.mesh_transport, no process listened on the canonical
ports, and MeshRouterWorm.send() fed refused connections into the route
Q-table as if a missing listener were a bad route. These tests own the seam
itself:

- round trip: envelope + matching delivery receipt + inbox + counters
- typed failure: a refused connection is `env_error` and is never learnable
- peer_rejected: a wrong receipt IS learnable route signal
- port_for determinism across processes (the PYTHONHASHSEED trap)
- MeshRouterWorm.send() learning gate (the poison-loop regression)

Spine state is isolated to a tmp dir so the developer's real
~/.azazael/spine_state.json is never read or written by the suite.
"""
import json
import socket
import struct
import subprocess
import sys
import threading
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
    """Ask the OS for a port nobody holds right now."""
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


# ── the seam: delivery proof ─────────────────────────────────────────

def test_round_trip_delivers_with_matching_receipt_and_inbox():
    port = _free_port()
    receiver = mt.MeshTransport("test-rx", port=port)
    receiver.listen()
    try:
        sender = mt.MeshTransport("test-tx", port=_free_port())
        res = sender.send("test-rx", {"op": "probe"}, timeout=5, port=port)

        assert res["success"] is True
        assert res["status"] == "delivered"
        assert res["learnable"] is True
        assert res["receipt"]["id"] == res["id"]
        assert res["receipt"]["ok"] is True

        inbox = receiver.drain()
        assert len(inbox) == 1
        assert inbox[0]["payload"] == {"op": "probe"}
        assert inbox[0]["src"] == "test-tx"
        assert inbox[0]["dst"] == "test-rx"
        assert sender.stats()["sent"] == 1
        assert receiver.stats()["received"] == 1
    finally:
        receiver.close()


# ── typed failures: signal vs noise ──────────────────────────────────

def test_refused_connection_is_env_error_and_not_learnable():
    port = _free_port()  # verified free, so the connect must refuse
    sender = mt.MeshTransport("test-tx", port=_free_port())
    res = sender.send("test-rx", {"op": "probe"}, timeout=5, port=port)

    assert res["success"] is False
    assert res["status"] == "env_error"
    assert res["learnable"] is False
    assert res["error"]
    assert sender.stats()["errors"] == 1


def test_wrong_receipt_is_peer_rejected_and_learnable():
    port = _free_port()
    ready = threading.Event()

    def bogus_server():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        ready.set()
        conn, _ = srv.accept()
        with conn:
            raw = _recvall(conn, 4)
            (ln,) = struct.unpack("!I", raw)
            _recvall(conn, ln)
            receipt = json.dumps({"id": "not-the-id", "ok": True}).encode()
            conn.sendall(struct.pack("!I", len(receipt)) + receipt)
        srv.close()

    thread = threading.Thread(target=bogus_server, daemon=True)
    thread.start()
    assert ready.wait(5)
    sender = mt.MeshTransport("test-tx", port=_free_port())
    res = sender.send("test-rx", {"op": "probe"}, timeout=5, port=port)
    thread.join(5)

    assert res["success"] is False
    assert res["status"] == "peer_rejected"
    assert res["learnable"] is True


# ── port identity: the PYTHONHASHSEED trap ───────────────────────────

def test_port_for_is_stable_across_processes():
    """Regression: `abs(hash(env)) % 100` gave every process a different
    port for the same unknown env (str.__hash__ is randomized), so two
    processes could never form a seam. crc32 is deterministic."""
    assert mt.port_for("wsl2-ubuntu") == mt.ENV_PORTS["wsl2-ubuntu"]

    src_dir = str(Path(__file__).resolve().parent.parent / "src")
    code = ("import sys; sys.path.insert(0, sys.argv[1]);"
            "from CORE.mesh_transport import port_for;"
            "print(port_for('probe-unknown-env'))")
    out = subprocess.run([sys.executable, "-c", code, src_dir],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert out.returncode == 0, out.stderr
    child_port = int(out.stdout.strip())

    assert child_port == mt.port_for("probe-unknown-env")
    assert mt.BASE_PORT <= child_port < mt.BASE_PORT + 100


# ── the learning gate: no policy estimate from environment noise ─────

def test_router_learns_only_learnable_outcomes(monkeypatch):
    """Poison-loop regression: a refused connection must NOT enter the
    route Q-table. It is counted and surfaced as a spine event instead --
    a missing listener is not a bad route."""
    router = MeshRouterWorm()
    learned = []
    events = []
    monkeypatch.setattr(
        router, "record_route_result",
        lambda dst, ok, duration_ms=0.0, censored=False:
            learned.append((dst, ok, censored)))
    monkeypatch.setattr(
        sr, "record_spine_event",
        lambda kind, detail: events.append((kind, detail)))
    ports = {"dead-peer": _free_port(), "live-peer": _free_port()}
    monkeypatch.setattr(mt, "port_for", lambda env: ports.get(env, 18299))

    res = router.send("dead-peer", {"op": "probe"}, timeout=5)
    assert res["delivered"] is False
    assert res["status"] == "env_error"
    assert res["learned"] is False
    assert learned == [("dead-peer", False, True)], \
        "env_error must arrive CENSORED (counted), never as policy outcome"
    assert router._env_errors == 1
    assert events and events[0][0] == "route_env_error"

    receiver = mt.MeshTransport("live-peer", port=ports["live-peer"])
    receiver.listen()
    try:
        res = router.send("live-peer", {"op": "probe"}, timeout=5)
        assert res["delivered"] is True
        assert res["status"] == "delivered"
        assert res["learned"] is True
        assert learned == [("dead-peer", False, True), ("live-peer", True, False)]
        assert len(receiver.drain()) == 1
    finally:
        receiver.close()
