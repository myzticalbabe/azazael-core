#!/usr/bin/env python3
"""Deeper tests for Eve TerenceV2."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_propose_fix_low_risk():
    from eve_unleashed.terence_v2 import TerenceV2
    t = TerenceV2(router=None, bridge=None)
    p = t.propose_fix("f.py", 1, "syntax", "x=1", suggested_type="syntax")
    assert p.risk == "low"
    assert p.auto_apply is True
    assert p.file == "f.py"
    assert t.stats.fixes_proposed == 1
    assert t.stats.fixes_applied == 1
    assert len(t.review_queue()) == 0


def test_propose_fix_high_risk():
    from eve_unleashed.terence_v2 import TerenceV2
    t = TerenceV2(router=None, bridge=None)
    p = t.propose_fix("f.py", 2, "logic", "if x:", suggested_type="logic")
    assert p.risk == "high"
    assert p.auto_apply is False
    assert len(t.review_queue()) == 1


def test_approve_reject():
    from eve_unleashed.terence_v2 import TerenceV2
    t = TerenceV2(router=None, bridge=None)
    t.propose_fix("f.py", 2, "logic", "x", suggested_type="logic")
    assert t.approve(0) is True
    assert len(t.review_queue()) == 0
    t.propose_fix("f.py", 3, "logic", "y", suggested_type="logic")
    assert t.reject(0) is True
    assert t.stats.fixes_rejected == 1
    assert t.approve(5) is False
    assert t.reject(5) is False


def test_record_outcome():
    from eve_unleashed.terence_v2 import TerenceV2
    t = TerenceV2(router=None, bridge=None)
    t.propose_fix("f.py", 1, "syntax", "x", suggested_type="syntax")
    t.record_outcome("syntax", True)
    t.record_outcome("syntax", False)
    snap = t.snapshot()
    assert snap["successes"] == 1
    assert snap["failures"] == 1
    assert snap["review_pending"] == 0


def test_fix_proposal_dataclass():
    from eve_unleashed.terence_v2 import FixProposal
    fp = FixProposal(file="a", line=1, error_type="e", risk="low",
                     original="o", fixed="f", description="d", auto_apply=True)
    assert fp.auto_apply is True


def test_heal_stats_defaults():
    from eve_unleashed.terence_v2 import HealStats
    h = HealStats()
    assert h.files_scanned == 0
    assert h.by_pattern == {}
