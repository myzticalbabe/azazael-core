#!/usr/bin/env python3
"""Deeper tests for Eve NL interface."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_intent_parser_keyword():
    from eve_unleashed.nl_interface import IntentParser
    p = IntentParser(bridge=None)
    i = p.parse("show status")
    assert i.action == "status"
    i2 = p.parse("approve abc12345")
    assert i2.action == "approve"
    assert i2.entities.get("decision_id") == "abc12345"
    i3 = p.parse("veto restart_service")
    assert i3.action == "veto"
    assert i3.entities.get("action_name") == "restart_service"
    i4 = p.parse("spawn a monitor worm")
    assert i4.action == "spawn"
    assert i4.entities.get("worm_type") == "monitor"
    i5 = p.parse("asdfghjkl")
    assert i5.action == "unknown"


def test_intent_parser_help():
    from eve_unleashed.nl_interface import IntentParser
    p = IntentParser(bridge=None)
    i = p.parse("help")
    assert i.action == "help"


def test_intent_confidence():
    from eve_unleashed.nl_interface import IntentParser
    p = IntentParser(bridge=None)
    i = p.parse("health check")
    assert 0.0 <= i.confidence <= 1.0


def test_proactive_suggestions():
    from eve_unleashed.nl_interface import ProactiveSuggestions
    from eve_unleashed.decision_engine import AutonomousDecisionEngine
    e = AutonomousDecisionEngine()
    d = e.propose("act", "r", confidence=0.9)
    e.approve(d.id, decided_by="human")  # stays? no, approve sets approved
    # make pending: propose another without approve
    e2 = AutonomousDecisionEngine()
    e2.propose("act2", "r", confidence=0.9)
    ps = ProactiveSuggestions(decision_engine=e2)
    sugs = ps.generate(5)
    assert len(sugs) >= 1
    assert sugs[0].source == "health"


def test_narrative_report():
    from eve_unleashed.nl_interface import NarrativeReport
    from eve_unleashed.consciousness import SelfModel
    from eve_unleashed.decision_engine import AutonomousDecisionEngine
    from eve_unleashed.consciousness import Reflection
    sm = SelfModel()
    sm.register("c", "worm")
    e = AutonomousDecisionEngine()
    r = Reflection()
    rep = NarrativeReport(decision_engine=e, self_model=sm, reflection=r)
    text = rep.generate()
    assert "Eve Unleashed" in text
    assert "Components tracked: 1" in text


def test_conversational_klink():
    from eve_unleashed.nl_interface import ConversationalKLINK, IntentParser
    from eve_unleashed.decision_engine import AutonomousDecisionEngine
    e = AutonomousDecisionEngine()
    k = ConversationalKLINK(decision_engine=e)
    assert "not sure" in k.respond("xyzzy nonsense").lower() or "understood" in k.respond("xyzzy nonsense").lower()
    assert "help" in k.respond("help").lower()
    assert "decisions tracked" in k.respond("status").lower()
    # approve flow
    d = e.propose("act", "r", confidence=0.5)
    resp = k.respond(f"approve {d.id[:8]}")
    assert "Approved" in resp or "approve" in resp.lower()


def test_suggestion_dataclass():
    from eve_unleashed.nl_interface import Suggestion
    s = Suggestion(id="1", title="t", body="b", action="a", confidence=0.5, source="health")
    assert s.id == "1"
