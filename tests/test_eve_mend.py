"""G1 tests for src/eve_unleashed/mend.py.

Mend reads the real .pytest_cache and the real bake lane, so the
tests run both against a temp root (deterministic) and use the
repo's own lane only for the shape assertions.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import mend as M


def _root(monkeypatch, tmp_path):
    (tmp_path / "state").mkdir()
    lane = tmp_path / "state" / "bake_harvest_freebuff_2099-01-01.jsonl"
    lane.write_text("", encoding="utf-8")
    monkeypatch.setattr(M, "REPO", tmp_path)
    return lane


def test_scan_reads_lastfailed(monkeypatch, tmp_path):
    lane = _root(monkeypatch, tmp_path)
    lane.write_text(json.dumps({"source": "x", "item_id": "i1",
                                "title": "t", "body": "b" * 50,
                                "kind": "error_fix"}) + "\n",
                   encoding="utf-8")
    cache = tmp_path / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "lastfailed").write_text(
        json.dumps({"tests/test_x.py::test_y": True}), encoding="utf-8")
    issues = M.scan()
    # Recorded fixes are history, never open issues; only live failures
    # count, so a green tree with a long fix log reads zero.
    assert {i["kind"] for i in issues} == {"test_failure"}
    assert issues[0]["turf"] == "tests"


def test_record_fix_enforces_body(monkeypatch, tmp_path):
    lane = _root(monkeypatch, tmp_path)
    row = M.record_fix("locker resolves", "x" * 45)
    assert row["kind"] == "error_fix"
    assert lane.read_text(encoding="utf-8").count("error_fix") == 1
    with pytest.raises(ValueError):
        M.record_fix("t", "too short")
    with pytest.raises(ValueError):
        M.record_fix("", "x" * 45)


def test_turf_classifies_own_and_foreign():
    assert M.turf_of("tests/test_eve_mend.py::test_x") == "own"
    assert M.turf_of("packages/azazael-core/tests/t.py::test_x") == "packages"
    plan = M.plan([{"turf": "own", "kind": "test_failure"},
                   {"turf": "packages", "kind": "test_failure"}])
    assert plan["own_count"] == 1
    assert plan["foreign"] == {"packages": [plan["foreign"]["packages"][0]]}


def test_scan_without_bake_lane_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "REPO", tmp_path)
    assert M.scan() == []
    with pytest.raises(ValueError):
        M._bake_lane()
