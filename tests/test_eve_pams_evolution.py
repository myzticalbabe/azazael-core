#!/usr/bin/env python3
"""Deeper tests for Eve PamSEvolution."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_score_importance():
    from eve_unleashed.pams_evolution import PamSEvolution, MemoryImportance
    p = PamSEvolution(recorder=None)
    mi = p.score_importance({"severity": "critical", "event_type": "anomaly_detected"})
    assert mi.importance == 1.0
    assert mi.severity == "critical"
    mi2 = p.score_importance({"severity": "debug", "event_type": "foo"})
    assert mi2.importance == 0.1
    mi3 = p.score_importance({"severity": "info"})
    assert mi3.importance == 0.3


def test_token_overlap_and_semantic_search_no_recorder():
    from eve_unleashed.pams_evolution import PamSEvolution
    p = PamSEvolution(recorder=None)
    p._get_recorder = lambda: None
    assert p._token_overlap("hello world", "hello there") > 0
    assert p._token_overlap("", "x") == 0.0
    assert p.semantic_search("q") == []
    assert p.tag_importance() == []


def test_set_retention_and_prune_no_recorder():
    from eve_unleashed.pams_evolution import PamSEvolution
    p = PamSEvolution(recorder=None)
    p._get_recorder = lambda: None
    p.set_retention_days(5)
    assert p._retention_days == 5
    p.set_retention_days(0)
    assert p._retention_days == 1
    assert p.prune() == 0


def test_distill_to_insights_failopen():
    from eve_unleashed.pams_evolution import distill_to_insights
    res = distill_to_insights(use_llm=False)
    assert isinstance(res, dict)
    assert "total_insights" in res
