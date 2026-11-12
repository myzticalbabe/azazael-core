#!/usr/bin/env python3
"""Deeper tests for Eve AutonomousDecisionEngine."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from eve_unleashed.decision_engine import (
    AutonomousDecisionEngine, DecisionStatus, AutonomyLevel,
)


def test_propose_clamps_confidence():
    e = AutonomousDecisionEngine()
    d = e.propose("act", "r", confidence=5.0, risk="low")
    assert d.confidence == 1.0
    d2 = e.propose("act2", "r", confidence=-1.0, risk="low")
    assert d2.confidence == 0.0
    assert d.status == DecisionStatus.PENDING


def test_recommend_only_no_auto():
    e = AutonomousDecisionEngine(autonomy_level=AutonomyLevel.RECOMMEND_ONLY)
    e.confidence_threshold = 0.5
    d = e.propose("a", "r", confidence=0.9, risk="low")
    assert d.status == DecisionStatus.PENDING


def test_supervised_auto_low():
    e = AutonomousDecisionEngine(autonomy_level=AutonomyLevel.SUPERVISED)
    e.confidence_threshold = 0.5
    d = e.propose("a", "r", confidence=0.9, risk="low")
    assert d.status == DecisionStatus.EXECUTED
    # medium risk stays pending
    d2 = e.propose("b", "r", confidence=0.9, risk="medium")
    assert d2.status == DecisionStatus.PENDING


def test_autonomous_auto_medium():
    e = AutonomousDecisionEngine(autonomy_level=AutonomyLevel.AUTONOMOUS)
    e.confidence_threshold = 0.5
    d = e.propose("a", "r", confidence=0.9, risk="medium")
    assert d.status == DecisionStatus.EXECUTED
    # execute on already-executed returns False
    assert e.execute(d.id) is False


def test_veto_blocks_action():
    e = AutonomousDecisionEngine()
    d = e.propose("danger", "r", confidence=0.9, risk="low")
    e.approve(d.id, decided_by="human")
    # veto existing
    assert e.veto(d.id, decided_by="human", reason="no") is True
    assert d.status == DecisionStatus.VETOED
    # future propose of same action auto-vetoed in _maybe_auto_act? vetoed_actions contains it
    assert "danger" in e.vetoed_actions()
    d2 = e.propose("danger", "r", confidence=0.9, risk="low")
    # approve should veto because action in vetoed set
    assert e.approve(d2.id, decided_by="human") is False or d2.status == DecisionStatus.VETOED


def test_reject():
    e = AutonomousDecisionEngine()
    d = e.propose("a", "r", confidence=0.5)
    assert e.reject(d.id) is True
    assert d.status == DecisionStatus.REJECTED
    assert e.reject(d.id) is False


def test_resolve_id():
    e = AutonomousDecisionEngine()
    d = e.propose("a", "r", confidence=0.5)
    full = e.resolve_id(d.id[:8])
    assert full == d.id
    assert e.resolve_id("zzzzzzzz") is None


def test_audit_trail_and_pending():
    e = AutonomousDecisionEngine()
    d = e.propose("a", "r", confidence=0.5)
    trail = e.audit_trail()
    assert len(trail) == 1
    assert e.pending() and e.pending()[0]["id"] == d.id


def test_stats_and_record_result():
    e = AutonomousDecisionEngine()
    d = e.propose("a", "r", confidence=0.5)
    e.approve(d.id, decided_by="human")
    e.execute(d.id)
    e.record_result(d.id, "done", success=True)
    assert d.result == "done"
    st = e.stats()
    assert st["total"] == 1


def test_setters():
    e = AutonomousDecisionEngine()
    e.set_autonomy_level(AutonomyLevel.AUTONOMOUS)
    assert e.autonomy_level == AutonomyLevel.AUTONOMOUS
    e.set_confidence_threshold(1.5)
    assert e.confidence_threshold == 1.0
    e.set_confidence_threshold(-0.5)
    assert e.confidence_threshold == 0.0


def test_to_dict():
    e = AutonomousDecisionEngine()
    d = e.propose("a", "r", confidence=0.5)
    td = d.to_dict()
    assert td["status"] == "pending"
    assert "autonomy_level" in td
