"""Contract tests for the EP235 read->write join (registry row D-1 residuals).

Two measured defects are pinned here so neither can silently return:

1. `CORE.technique_runtime.recall_with_feedback` read `recall_state()` for
   `total`/`abstained` but dropped the `last_scores` it ALREADY had in hand, so
   every consumer payload carried `last_scores: []` forever. The recorded cause
   ("never reads `stats()`") was wrong: `RecallRouterWorm.recall_state()` exposes
   `last_scores` too (CORE/WORMS/expansion/engram.py:544) -- the data was simply
   not surfaced, so the fix is to surface it rather than take a second reading.
2. `_l1_memory.ep235()` hard-coded `"verified": True`, so a call that returned
   `{"routed": False, ...}` still reported a verified join -- exactly what
   `_l1_memory.py`'s own header rule 1 forbids.

The failure-case test deliberately distinguishes the honest value from `_safe`'s
fail-open shape (`{"verified": False, "reason": ...}`, which carries NO
`reader_to_writer` key), so a bare `verified is False` cannot pass merely because
an exception was swallowed.
"""
from __future__ import annotations

import os
import sys

# The repo's own per-file bootstrap (same idiom as tests/test_agent.py): the
# rootdir conftest redirects the stores but never puts `src/` on sys.path, so a
# test importing `CORE.*` must do it itself or die at collection with
# `ModuleNotFoundError: No module named 'CORE'`.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from typing import Any  # noqa: E402

from CORE.technique_runtime import recall_with_feedback  # noqa: E402
from CORE.technique_wiring import _l1_memory as l1  # noqa: E402


def test_recall_with_feedback_surfaces_last_scores_as_a_list():
    """The key must EXIST and be a list; its contents may legitimately be empty."""
    out = recall_with_feedback("probe")
    assert isinstance(out, dict)
    assert "routed" in out
    assert "last_scores" in out, "the scores already fetched must not be dropped"
    assert isinstance(out["last_scores"], list)


def test_ep235_verified_follows_the_route_outcome(monkeypatch):
    """A non-routed recall must report verified False -- and not via _safe."""
    monkeypatch.setattr(l1, "recall_with_feedback",
                        lambda *a, **k: {"routed": False, "reason": "probe"})
    out = l1.ep235()

    assert out["verified"] is False
    # `_safe` fails open with {"verified": False, "reason": ...} and no
    # `reader_to_writer` key, so requiring that key proves the value came from
    # the route outcome rather than from a swallowed exception.
    assert "reader_to_writer" in out
    assert out["reader_to_writer"] == "unrouted"


def test_ep235_write_call_is_schema_identical(monkeypatch):
    """The write keeps its EP id, event type, payload keys and tags."""
    seen: dict[str, Any] = {}

    def _spy(*args: Any, **kwargs: Any) -> dict:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {}

    monkeypatch.setattr(l1, "_write", _spy)
    monkeypatch.setattr(l1, "recall_with_feedback",
                        lambda *a, **k: {"routed": True, "mode": "semantic",
                                         "route": "shard-1", "total_reads": 7,
                                         "last_scores": [0.9, 0.5]})
    out = l1.ep235()

    assert out["verified"] is True
    assert out["reader_to_writer"] == "joined"
    assert seen["args"][0] == "EP235"
    assert seen["args"][1] == "recall_feedback"
    payload = seen["args"][2]
    assert set(payload) == {"mode", "route", "hits", "last_scores"}
    assert payload["mode"] == "semantic"
    assert payload["route"] == "shard-1"
    assert payload["hits"] == 7
    assert payload["last_scores"] == [0.9, 0.5]
    assert seen["kwargs"].get("tags") == ["sage", "recall-feedback"]
