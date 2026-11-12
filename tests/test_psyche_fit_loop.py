# coding: utf-8
"""Tests for the PSYCHE L4 loop: behavior-chain producer + hourly fit leg.

Producer (delegation rendezvous -> psyche-chain rows) and consumer
(heartbeat psyche_fit_step -> per-actor thetas + owner snapshot) proven
against a fake in-memory store — never the live one. Production-log
quarantine: chains are skipped under pytest unless PSYCHE_CHAIN_TEST=1.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SCRIPTS))

from CORE.WORMS.expansion import delegation as D
from CORE.WORMS.expansion.delegation import (
    answer_question,
    ask_owner,
    pending_count,
)
import technique_heartbeat as HB


class FakeStore:
    """Minimal stand-in for LocalMemoryStore (record + filtered query)."""

    def __init__(self):
        self.rows = []

    def record_event(self, source, event_type, data=None, severity="info",
                     tags=None, **kw):
        self.rows.append({"source": source, "event_type": event_type,
                          "data": data, "severity": severity,
                          "tags": tags or []})
        return len(self.rows)

    def query_memories(self, source=None, event_type=None, limit=50,
                       **kw):
        out = [r for r in self.rows
               if (source is None or r["source"] == source)
               and (event_type is None or r["event_type"] == event_type)]
        return out[:limit]


@pytest.fixture()
def fake_store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr("CORE.local_memory.get_memory_store",
                        lambda *a, **k: fake)
    monkeypatch.setenv("PSYCHE_CHAIN_TEST", "1")
    monkeypatch.delenv("PSYCHE_THETA", raising=False)
    return fake


@pytest.fixture()
def theta_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("PSYCHE_THETA", str(tmp_path / "person_theta.json"))
    return tmp_path


def _chains(fake):
    return [r for r in fake.rows if r["source"] == "psyche-chain"]


def test_chain_park_then_share(fake_store):
    box = {}

    def worker():
        box["answer"] = ask_owner("proceed?", decision_id="d-loop-1",
                                  worm="w", timeout_s=5.0, bus=False)

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.monotonic() + 5.0
    while pending_count() == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert answer_question("d-loop-1", {"ok": True}, bus=False) is True
    t.join(timeout=5)
    assert not t.is_alive()
    assert box.get("answer") == {"ok": True}
    chains = _chains(fake_store)
    # Order-insensitive: the park emission (worker thread) races the answer
    # emission (main thread), so arrival order is not contractual. Content is.
    got = sorted((c["data"]["actor"], c["data"]["kind"], c["data"]["action"])
                 for c in chains)
    assert got == [("owner", "fair", "share"), ("w", "persist", "defer")]
    defer = next(c for c in chains if c["data"]["action"] == "defer")
    assert defer["data"]["planned"] is True


def test_chain_timeout_withholds(fake_store):
    with pytest.raises(Exception):
        ask_owner("answer?", decision_id="d-loop-2", worm="w",
                  timeout_s=0.2, bus=False)
    chains = _chains(fake_store)
    assert [(c["data"]["actor"], c["data"]["action"]) for c in chains] == [
        ("w", "defer"), ("owner", "withhold")]
    assert chains[1]["data"]["planned"] is False


def test_chain_quarantined_under_pytest(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr("CORE.local_memory.get_memory_store",
                        lambda *a, **k: fake)
    monkeypatch.delenv("PSYCHE_CHAIN_TEST", raising=False)
    assert "PYTEST_CURRENT_TEST" in __import__("os").environ
    box = {}

    def worker():
        box["answer"] = ask_owner("q?", decision_id="d-loop-3",
                                  worm="w", timeout_s=5.0, bus=False)

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.monotonic() + 5.0
    while pending_count() == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert answer_question("d-loop-3", True, bus=False) is True
    t.join(timeout=5)
    assert box.get("answer") is True
    assert _chains(fake) == []


def _chain_row(actor, kind, action, planned=True):
    return {"source": "psyche-chain", "event_type": "behavior_action",
            "data": {"actor": actor, "kind": kind, "action": action,
                     "planned": planned}}


def test_fit_no_input_records_nothing(theta_tmp):
    fake = FakeStore()
    rep = HB.psyche_fit_step(store=fake)
    assert rep["verdict"] == "no-input"
    assert rep["ok"] is True  # healthy idle, not a failure
    assert rep["n_rows"] == 0
    assert rep["snapshot_written"] is False
    assert "report_recorded" not in rep  # heartbeat summary carries it
    assert fake.rows == []
    assert not (theta_tmp / "person_theta.json").exists()


def test_fit_abstains_below_threshold(theta_tmp):
    fake = FakeStore()
    for _ in range(2):
        fake.rows.append(_chain_row("owner", "fair", "share"))
    rep = HB.psyche_fit_step(store=fake)
    assert rep["verdict"] == "abstained-below-threshold"
    assert rep["actors"]["owner"]["H"] is None
    assert rep["snapshot_written"] is False
    assert rep["report_recorded"] is True
    assert not (theta_tmp / "person_theta.json").exists()  # never clobber
    assert fake.rows[-1]["source"] == "psyche-fit"


def test_fit_owner_and_worm(theta_tmp):
    fake = FakeStore()
    for _ in range(6):
        fake.rows.append(_chain_row("owner", "fair", "share"))
    for _ in range(6):
        fake.rows.append(_chain_row("w", "persist", "continue"))
    fake.rows.append({"source": "psyche-chain",
                      "event_type": "behavior_action",
                      "data": "not-a-dict"})
    rep = HB.psyche_fit_step(store=fake)
    assert rep["verdict"] == "fitted"
    assert rep["actors"]["owner"]["H"] == 5.0
    assert rep["actors"]["owner"]["C"] is None  # no persist rows: abstains
    assert rep["actors"]["w"]["C"] == 5.0
    assert rep["malformed"] == 1
    assert rep["snapshot_written"] is True
    snap = json.loads((theta_tmp / "person_theta.json").read_text(
        encoding="utf-8"))
    assert snap["domain_scores"] == {"H": 5.0}
    assert snap["source"] == "psyche-heartbeat"


def test_fit_abstention_never_clobbers_snapshot(theta_tmp):
    snap_path = theta_tmp / "person_theta.json"
    snap_path.write_text(json.dumps({"name": "old", "source": "test",
                                     "domain_scores": {"H": 4.0}}),
                         encoding="utf-8")
    fake = FakeStore()
    fake.rows.append(_chain_row("owner", "fair", "share"))
    rep = HB.psyche_fit_step(store=fake)
    assert rep["verdict"] == "abstained-below-threshold"
    assert json.loads(snap_path.read_text(encoding="utf-8"))["name"] == "old"
