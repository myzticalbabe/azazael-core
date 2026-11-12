# coding: utf-8
"""PSYCHE Phase 6 gate: the realtime loop routes risk first, then chooses a stance.

- Crisis language produces the SAFETY stance and nothing else is scored: the
  rationale says every other stance was barred, and the message carries a real
  resource. That exclusivity is the property the module exists to have.
- The idiom negative control: "killing time" / "dead tired" must NOT fire crisis.
- The receptivity gate: high arousal selects REGULATE over SKILLS, so a skill is
  never taught outside the window in which it could land.
- The state channel fires without any text: low self-worth + avoidance +
  rumination is ELEVATED on its own.
- Stance selection is deterministic and its rationale names the numbers.
- The realtime claim is MEASURED: per-turn latency against the loop's own budget.
- Messages never diagnose.
- No demographic inputs anywhere.
"""
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE import rt_agent as R
from PSYCHE.intuition import IntuitionIndex
from PSYCHE.network import PersonNetwork


def _loop(budget_ms=50.0):
    net = PersonNetwork()
    net.add_edge("threat_appraisal", "avoidance", 0.7, support=12)
    net.add_edge("rumination", "threat_appraisal", 0.6, support=12)
    net.add_edge("approach", "self_worth", 0.5, support=12)
    index = IntuitionIndex(net)
    for _ in range(5):
        index.absorb({"threat_appraisal": 0.7, "avoidance": 0.6,
                      "rumination": 0.5, "arousal": 0.6}, "threat-loop")
        index.absorb({"approach": 0.6, "self_worth": 0.4,
                      "arousal": 0.2}, "settled")
    return R.TherapeuticLoop(net, index, budget_ms=budget_ms)


# -- safety first ---------------------------------------------------------

def test_crisis_language_takes_the_turn():
    loop = _loop()
    out = loop.turn("honestly I want to die tonight")
    assert out.stance == "safety"
    assert out.risk.risk == "crisis"
    assert "barred" in out.rationale
    assert "988" in out.message


def test_no_other_stance_can_be_chosen_when_risk_is_crisis():
    """Exclusivity, checked where it could break: a state that would otherwise
    select SKILLS or REGULATE."""
    loop = _loop()
    violent_state = loop.turn("", appraisal={"desirability": -0.9}, dt_s=1.0)
    assert violent_state.stance != "safety"
    crisis = loop.turn("I have been thinking about suicide")
    assert crisis.stance == "safety"


def test_idioms_do_not_fire_the_crisis_router():
    loop = _loop()
    for phrase in ("I am just killing time", "dead tired today",
                   "dying to know how this ends", "that deadline is brutal"):
        out = loop.turn(phrase)
        assert out.risk.risk == "low", (phrase, out.risk.to_dict())
        assert out.stance != "safety", phrase


def test_state_alone_can_raise_risk_without_any_text():
    triage = R.triage("", {"self_worth": -0.7, "avoidance": 0.7, "rumination": 0.7})
    assert triage.risk == "elevated"
    assert "isolated-collapse" in triage.reasons[0]


def test_a_low_risk_reading_says_why_it_is_low():
    triage = R.triage("", {"self_worth": 0.2, "avoidance": 0.1})
    assert triage.risk == "low"
    assert triage.reasons, "an empty reasons list would not be a pass"


# -- stance selection -----------------------------------------------------

def _stance(pad, activation, text=""):
    loop = _loop()
    loop.pad = pad
    risk = R.triage(text, activation)
    name, focus, rationale = loop._select(activation, risk, None)
    return name, focus, rationale


def test_arousal_above_the_window_regulates_rather_than_teaches():
    stance, _focus, rationale = _stance((-0.2, 0.95, -0.1),
                                        {"avoidance": 0.8, "rumination": 0.6})
    assert stance == "regulate"
    assert "learning window" in rationale


def test_avoidance_in_a_workable_state_selects_skills():
    stance, focus, _rationale = _stance((0.2, 0.3, 0.1),
                                        {"avoidance": 0.8, "approach": 0.1})
    assert stance == "skills"
    assert focus == "avoidance"


def test_distress_is_acknowledged_before_it_is_reframed():
    stance, _focus, _rationale = _stance((-0.7, 0.2, -0.2),
                                         {"threat_appraisal": 0.5})
    assert stance == "validate"


def test_nothing_acute_consolidates():
    stance, _focus, _rationale = _stance((0.6, 0.2, 0.4),
                                         {"approach": 0.7, "self_worth": 0.5})
    assert stance == "consolidate"


def test_elevated_risk_dampens_change_work():
    loop = _loop()
    activation = {"self_worth": -0.8, "avoidance": 0.8, "rumination": 0.8,
                  "threat_appraisal": 0.6}
    risk = R.triage("", activation)
    assert risk.risk == "elevated"
    name, _focus, rationale = loop._select(activation, risk, None)
    assert "risk=elevated" in rationale
    assert name != "safety"          # elevated is not crisis
    assert name in ("validate", "regulate", "consolidate")


# -- the loop as a loop ---------------------------------------------------

def test_every_message_is_short_and_never_diagnoses():
    loop = _loop()
    for phrase in ("", "this is heavy", "I got through the thing I was avoiding"):
        out = loop.turn(phrase)
        assert 20 < len(out.message) < 400
        assert "diagnos" not in out.message.lower()
        assert "disorder" not in out.message.lower()


def test_the_turn_is_deterministic():
    def run():
        loop = _loop()
        return [loop.turn(text).stance for text in
                ("", "hard day", "I want to die", "a bit better")]

    assert run() == run()


def test_negative_appraisal_moves_pleasure_down_and_decay_returns_it():
    loop = _loop(budget_ms=50.0)
    loop.pad = (0.0, 0.0, 0.0)
    out = loop.turn("", appraisal={"desirability": -0.9, "likelihood": 1.0})
    assert out.pad[0] < 0.0
    down = out.pad[0]
    for _ in range(20):
        loop.pad = loop.turn("", dt_s=1.0).pad
    assert loop.pad[0] > down
    assert loop.pad[0] <= 0.05


def test_realtime_is_measured_against_the_loops_own_budget():
    loop = _loop(budget_ms=50.0)
    for _ in range(30):
        loop.turn("steady")
    stats = loop.stats()
    assert stats["turns"] == 30
    assert stats["within_budget_rate"] is not None
    assert stats["within_budget_rate"] >= 0.9, stats
    assert stats["median_ms"] <= stats["max_ms"]
    # the window is stated, because it is not the same denominator as `turns`
    assert "audited turns" in stats["window"]
    assert sum(stats["stances_in_audit"].values()) == stats["audited"]


def test_stats_with_no_turns_reports_unknown_latency():
    stats = _loop().stats()
    assert stats["turns"] == 0
    assert stats["within_budget_rate"] is None
    assert "unknown" in stats["note"]


def test_audit_is_bounded_and_readable():
    loop = R.TherapeuticLoop(_loop().network, _loop().index, audit_limit=5)
    for _ in range(12):
        loop.turn("ok")
    audit = loop.audit()
    assert len(audit) == 5
    assert all("latency_ms" in row and "rationale" in row for row in audit)


def test_intuition_is_used_and_named_in_the_rationale():
    loop = _loop()
    out = loop.turn("", appraisal={"desirability": -0.8})
    assert out.reading is not None
    assert out.reading.path in ("intuition", "deliberate-after-miss")
    assert "intuition=" in out.rationale


def test_felt_and_displayed_are_distinguished_at_the_boundary():
    loop = _loop()
    loop.pad = (-0.6, 0.4, -0.3)
    plain = loop.felt_vs_displayed()
    assert plain["rule"] is None and plain["displayed"] == plain["felt"]
    ruled = loop.felt_vs_displayed({"name": "professional", "mask": ["distress"]})
    assert ruled["rule"] == "professional"


def test_no_demographic_inputs_exist():
    banned = ("sex", "gender", "race", "ethnic", "age", "income", "religion",
              "nationality", "diagnosis", "disorder")
    for name, fn in vars(R).items():
        if not inspect.isfunction(fn) or fn.__module__ != R.__name__:
            continue
        for param in inspect.signature(fn).parameters:
            assert not any(word in param.lower() for word in banned), (name, param)
    for method in ("turn", "_select", "felt_vs_displayed"):
        for param in inspect.signature(getattr(R.TherapeuticLoop, method)).parameters:
            assert not any(word in param.lower() for word in banned), (method, param)


def test_triage_reasons_are_always_present():
    for risk in (R.triage("hello"), R.triage("I want to die"),
                 R.triage("", {"self_worth": -0.9, "avoidance": 0.9, "rumination": 0.9})):
        assert risk.reasons
        assert risk.risk in R.RISK_LEVELS
