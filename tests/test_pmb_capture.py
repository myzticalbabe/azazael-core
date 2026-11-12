# coding: utf-8
"""Tests for tools/pmb_capture.py -- the record-at-the-moment helper.

The recording half needs the `.pmb-venv` interpreter, so these cover the item
builder (which decides WHAT gets recorded) and the CLI contract (a capture must
never raise into the caller's task).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_CAPTURE = Path(__file__).resolve().parent.parent / "tools" / "pmb_capture.py"


def _load():
    spec = importlib.util.spec_from_file_location("pmb_capture", _CAPTURE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def capture():
    return _load()


class _Args:
    def __init__(self, **kw):
        self.lesson = kw.get("lesson")
        self.fact = kw.get("fact")
        self.decision = kw.get("decision")
        self.activity = kw.get("activity")
        self.goal = kw.get("goal")
        self.project = kw.get("project", "azazael")
        self.importance = kw.get("importance", 0.8)


def test_lesson_carries_the_project_tag(capture):
    items = capture.build_items(_Args(lesson="x"))
    assert items == [{"type": "lesson", "content": "x", "project": "azazael"}]


def test_lesson_without_project_omits_the_key(capture):
    items = capture.build_items(_Args(lesson="x", project=None))
    assert "project" not in items[0]


def test_fact_and_importance(capture):
    items = capture.build_items(_Args(fact="f", importance=0.5))
    assert items[0] == {"type": "fact", "content": "f", "importance": 0.5}


def test_goal_uses_title_not_content(capture):
    items = capture.build_items(_Args(goal="g"))
    assert items[0] == {"type": "goal", "title": "g"}


def test_all_types_combine(capture):
    items = capture.build_items(_Args(lesson="l", fact="f", decision="d",
                                      activity="a", goal="g"))
    assert [i["type"] for i in items] == ["lesson", "fact", "decision",
                                          "activity", "goal"]


def test_no_items_is_an_error(capture):
    with pytest.raises(SystemExit):
        capture.main([])


def test_recording_failure_does_not_raise(capture, monkeypatch):
    """A capture is a side-effect; it must never break the caller's task."""
    import sys
    sys.path.insert(0, str(_CAPTURE.parent))

    def boom(*a, **k):
        raise RuntimeError("daemon down")

    monkeypatch.setitem(sys.modules, "pmb_client",
                        type("m", (), {"call_tool": boom}))
    assert capture.main(["--fact", "x", "--quiet"]) == 1