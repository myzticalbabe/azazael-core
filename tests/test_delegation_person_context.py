# coding: utf-8
"""Tests for the PSYCHE person-context edge on ask_owner.

The delegation channel enriches its question announcement with the fitted
person theta when a snapshot exists (producer: tools/psyche_live_fit.py
--emit-theta), and behaves byte-identically when it does not. Absent or
broken snapshots are normal states, never failures: the rendezvous below
is identical either way (fail-open).
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion import delegation as D
from CORE.WORMS.expansion.delegation import (
    answer_question,
    ask_owner,
    pending_count,
)


def _snapshot(path: Path, scores: dict, name="owner", source="test") -> None:
    path.write_text(json.dumps({"name": name, "source": source,
                                "domain_scores": scores}),
                    encoding="utf-8")


def test_person_context_absent_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("PSYCHE_THETA", str(tmp_path / "missing.json"))
    assert D._person_context() == {}


def test_person_context_present_returns_ranged_scores(monkeypatch, tmp_path):
    p = tmp_path / "theta.json"
    _snapshot(p, {"O": 4.5, "C": 2.0, "E": 3.0, "A": 3.0, "N": 3.0,
                  "H": 3.0, "X": 9.0})
    monkeypatch.setenv("PSYCHE_THETA", str(p))
    got = D._person_context()
    assert got["theta"] == {"O": 4.5, "C": 2.0, "E": 3.0, "A": 3.0,
                            "N": 3.0, "H": 3.0}
    assert "X" not in got["theta"]  # unknown + out-of-range dropped
    assert got["name"] == "owner"


def test_person_context_broken_file_is_empty(monkeypatch, tmp_path):
    p = tmp_path / "theta.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("PSYCHE_THETA", str(p))
    assert D._person_context() == {}


def test_person_context_empty_scores_is_empty(monkeypatch, tmp_path):
    p = tmp_path / "theta.json"
    _snapshot(p, {})
    monkeypatch.setenv("PSYCHE_THETA", str(p))
    assert D._person_context() == {}


def _roundtrip(question="proceed?", did="d-person-1", timeout_s=5.0):
    box = {}

    def worker():
        box["answer"] = ask_owner(question, decision_id=did,
                                  worm="w", timeout_s=timeout_s, bus=False)

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.monotonic() + 5.0
    while pending_count() == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert answer_question(did, {"proceed": True}, bus=False) is True
    t.join(timeout=5)
    assert not t.is_alive()
    return box.get("answer")


def test_announce_omits_person_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("PSYCHE_THETA", str(tmp_path / "missing.json"))
    seen = {}
    monkeypatch.setattr(D, "_announce",
                        lambda topic, payload, bus=None: seen.update(payload))
    assert _roundtrip(did="d-person-absent") == {"proceed": True}
    assert "person" not in seen
    assert seen["question"] == "proceed?"


def test_announce_carries_person_when_present(monkeypatch, tmp_path):
    p = tmp_path / "theta.json"
    _snapshot(p, {d: 3.5 for d in ("O", "C", "E", "A", "N", "H")})
    monkeypatch.setenv("PSYCHE_THETA", str(p))
    seen = {}
    monkeypatch.setattr(D, "_announce",
                        lambda topic, payload, bus=None: seen.update(payload))
    assert _roundtrip(did="d-person-present") == {"proceed": True}
    assert seen["person"]["theta"]["O"] == 3.5
    assert seen["decision_id"] == "d-person-present"


def test_default_theta_path_is_live_store_not_checkout():
    # Guard companion: the canonical theta path must live under the shared
    # live store dir (.azazael), never the stale home checkout (azazael).
    from PSYCHE.traits import THETA_SNAPSHOT_FILENAME, default_theta_path
    assert THETA_SNAPSHOT_FILENAME == "person_theta.json"
    assert default_theta_path().name == "person_theta.json"
    assert default_theta_path().parent.name == ".azazael"
    assert "azazael" not in [part for part in default_theta_path().parts
                             if part != ".azazael"]
