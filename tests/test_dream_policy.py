# coding: utf-8
"""Unit tests for dream_policy_bridge gating.

Covers the B6 gap on the gate that blocks promotions. MetaCognitionWorm
and DirectMemory are stubbed — no live state touched, no rows written.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

import CORE.dream_policy_bridge as bridge
from CORE.dream_policy_bridge import MIN_CONFIDENCE, apply_dream_policy


class FakeMC:
    def __init__(self, *a, **k):
        self.adapted = []

    def get_current_strategy(self, worm):
        return "default"

    def adapt_strategy(self, worm, current, actions, recommended=None,
                       confidence=0.0):
        self.adapted.append((worm, recommended))
        return {"should_adapt": True, "recommended": recommended,
                "rationale": "test", "all_scores": {}}

    def save_state(self):
        pass


class FakeDM:
    instances = []

    def __init__(self, *a, **k):
        self.rows = []
        FakeDM.instances.append(self)

    def record(self, event_type, data=None, tags=None, **kw):
        self.rows.append((event_type, data))
        return len(self.rows)


@pytest.fixture
def stubbed(monkeypatch):
    import CORE.WORMS.expansion.meta_cognition as mcmod
    import CORE.direct_memory as dmmod
    monkeypatch.setattr(mcmod, "MetaCognitionWorm", FakeMC)
    monkeypatch.setattr(dmmod, "DirectMemory", FakeDM)
    FakeDM.instances.clear()
    # _governance_blocked imports CurativePool lazily; force the
    # fail-closed path off by stubbing the pool too.
    import META.MetaWormSpawner as spawnmod
    class OpenPool:
        def evaluate_action(self, *a, **k):
            return {"decision": "allow"}
    monkeypatch.setattr(spawnmod, "CurativePool", OpenPool)
    return FakeDM


class TestGating:
    def test_none_and_empty(self, stubbed):
        assert apply_dream_policy(None)["applied"] == []
        assert apply_dream_policy([])["applied"] == []

    def test_low_confidence_skipped(self, stubbed):
        r = apply_dream_policy([{"confidence": MIN_CONFIDENCE / 2,
                                 "strategies": ["aggressive"]}])
        assert r["applied"] == []
        assert r["skipped"] and r["skipped"][0]["reason"] == "low-confidence"

    def test_nondict_skipped(self, stubbed):
        r = apply_dream_policy(["not-a-dict"])
        assert r["applied"] == []
        assert r["skipped"][0]["reason"] == "low-confidence"

    def test_no_actionable_strategy(self, stubbed):
        r = apply_dream_policy([{"confidence": 0.9, "source": "global"}])
        assert r["applied"] == []
        assert r["skipped"][0]["reason"] == "no-actionable-strategy"

    def test_applies_good_suggestion(self, stubbed):
        r = apply_dream_policy([{"confidence": 0.9, "source": "global",
                                 "strategies": ["aggressive"]}])
        assert len(r["applied"]) == 1
        assert r["applied"][0]["recommended"] == "aggressive"
        assert r["applied"][0]["should_adapt"] is True

    def test_records_policy_row(self, stubbed):
        apply_dream_policy([{"confidence": 0.9, "source": "global",
                             "strategies": ["aggressive"]}])
        rows = [row for inst in FakeDM.instances for row in inst.rows]
        assert any(et == "policy_applied" for et, _ in rows)
