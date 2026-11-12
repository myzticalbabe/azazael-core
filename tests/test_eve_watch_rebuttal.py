"""G1 tests for src/eve_unleashed/watch.py + rebuttal.py.

Watch: add/remove/list semantics plus snapshot diffing with a
stubbed poll (no network in tests). Rebuttal: the correction names
claims, carries receipts, and refuses non-results.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import watch as W
from eve_unleashed import rebuttal as B


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "WATCH_DIR", tmp_path)


def test_add_remove_watch(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert W.add_watch("lead pipes")["watches"] == 1
    assert W.add_watch("lead pipes")["watches"] == 1
    assert W.remove_watch("lead pipes")["watches"] == 0
    with pytest.raises(ValueError):
        W.add_watch("  ")


def test_check_diffs_new_dockets(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    W.add_watch("lead pipes")
    import eve_unleashed.redress as R
    monkeypatch.setattr(R, "search_dockets",
                        lambda term: [{"id": "d1", "title": "t1"},
                                      {"id": "d2", "title": "t2"}])
    first = W.check_watches()[0]
    assert first["seen"] == 2 and len(first["new"]) == 2
    monkeypatch.setattr(R, "search_dockets",
                        lambda term: [{"id": "d1", "title": "t1"},
                                      {"id": "d2", "title": "t2"},
                                      {"id": "d3", "title": "t3"}])
    second = W.check_watches()[0]
    assert second["new"] == ["d3"]


def test_draft_rebuttal_carries_receipts():
    res = {"claim": "The dam always holds.",
           "verdict": "REFUTED",
           "debunk": {"fallacy_flags": [
               {"flag": "absolute_plus_hedge", "matched": ["always"]}],
               "contradictions": ["The dam always holds except today."],
               "opinions": []},
           "entity_lookups": [{"topic": "Hoover Dam", "found": True,
                               "title": "Hoover Dam", "description": "dam"}],
           "receipts": [{"status": "ok", "fetched_utc": "t",
                         "bytes": 10, "sha256": "ab" * 32}],
           "opinion": ""}
    text = B.draft_rebuttal(res)
    assert "REFUTED" in text and "Hoover Dam" in text
    assert "ab" * 8 in text
    assert "under your own name" in text
    with pytest.raises(ValueError):
        B.draft_rebuttal({"nope": 1})
