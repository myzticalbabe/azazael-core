"""EP327 / EP382 / EP269 — the policy engine's three named SOTA gaps, closed additively.

The rows, and the actual gap each one names (from `SOTA_ROW_RESCORE.md`):

* **EP327** "Agent safety is action alignment" — the residue was that
  `structured_action.semantic_action` validates verb/target only and has no
  authority input to compare against. `PolicyEngine` already owned the
  comparison primitive (`check_ceiling`) but nothing consulted it during an
  evaluation, so an action *exercising* more authority than the subject *holds*
  was never refused. `evaluate(..., authority=...)` now consults it.
* **EP382** "ETAS effect-typed agent language" — `PolicyEffect` was literally
  `ALLOW`/`DENY`, so the vocabulary could not express a conditional grant or a
  duty. Two effects and a per-rule obligation map are added.
* **EP269** "Agent libOS" (policy-engine half) — authority existed as a ceiling
  predicate and as delegation attenuation, but a *task* could not hold a scoped
  authority at all.

Half of this file's job is the opposite of a new-feature test: it pins that the
OLD vocabulary still decides exactly as it did, because the whole point of the
change is that it is additive. A behavioural change to ALLOW/DENY, to the
fail-closed default, or to `default_policy_rules()` would be a regression.
"""


import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.compliance.policy import (  # noqa: E402
    GRANTING_EFFECTS,
    PolicyEffect,
    PolicyEngine,
    PolicyRule,
    default_policy_rules,
)


def _engine(*rules):
    engine = PolicyEngine()
    engine.add_rules(list(rules) if rules else default_policy_rules())
    return engine


# --------------------------------------------------------------- back-compat

def test_default_rules_decide_exactly_as_before():
    engine = _engine()
    assert engine.evaluate("str_replace", role="developer")[0] is True
    assert engine.evaluate("read_files", role="reviewer")[0] is True
    assert engine.evaluate("str_replace", role="reviewer")[0] is False
    assert engine.evaluate("write_file", role="reviewer")[0] is False
    assert engine.evaluate("read_files", role="auditor")[0] is True
    assert engine.evaluate("write_file", role="auditor")[0] is False
    assert engine.evaluate("read_files", role="guest")[0] is False


def test_fail_closed_default_is_unchanged():
    engine = PolicyEngine()  # no rules at all
    allowed, reason = engine.evaluate("anything", role="developer")
    assert allowed is False
    assert "DENIED (default)" in reason


def test_allow_and_deny_reasons_are_byte_identical():
    allow = _engine(PolicyRule("allow", "code_edit", role="developer"))
    allowed, reason = allow.evaluate("code_edit", role="developer")
    assert allowed is True
    assert reason.startswith("Rule allow:")

    deny = _engine(PolicyRule("deny", "code_edit", role="developer"))
    allowed, reason = deny.evaluate("code_edit", role="developer")
    assert allowed is False
    assert reason.startswith("DENIED by rule:")


def test_a_deny_rules_obligations_cannot_enter_the_decision():
    """Obligations are read for the conditional effect only.

    A deny rule that happens to carry an unmet obligation must still read as a
    plain denial -- otherwise the reason text would depend on rule furniture
    that has no meaning for DENY.
    """
    engine = _engine(PolicyRule("deny", "code_edit", role="developer",
                                obligations={"tests_green": False}))
    allowed, reason = engine.evaluate("code_edit", role="developer")
    assert allowed is False
    assert reason.startswith("DENIED by rule:")
    assert "obligation" not in reason


# ------------------------------------------------------------------- EP382

def test_allow_with_obligations_grants_only_when_discharged():
    engine = _engine(PolicyRule("allow_with_obligations", "release",
                                obligations={"tests_green": False,
                                             "changelog": False},
                                priority=50))

    allowed, reason = engine.evaluate("release")
    assert allowed is False
    assert "unmet obligation(s): tests_green, changelog" in reason

    allowed, _ = engine.evaluate("release", attributes={
        "obligations": {"tests_green": True, "changelog": True}})
    assert allowed is True


def test_a_partial_discharge_still_denies_and_names_only_what_is_owed():
    engine = _engine(PolicyRule("allow_with_obligations", "release",
                                obligations={"tests_green": False,
                                             "changelog": False}))
    allowed, reason = engine.evaluate("release", attributes={
        "obligations": {"tests_green": True, "changelog": False}})
    assert allowed is False
    assert "changelog" in reason
    assert "tests_green" not in reason


def test_conditional_effect_with_no_obligations_behaves_like_allow():
    engine = _engine(PolicyRule("allow_with_obligations", "read_files",
                                role="developer"))
    assert engine.evaluate("read_files", role="developer")[0] is True


def test_obligate_never_grants_and_names_the_duty():
    engine = _engine(PolicyRule("obligate", "publish",
                                obligations={"human_review": False}))
    allowed, reason = engine.evaluate("publish")
    assert allowed is False
    assert "obligate" in reason
    assert "human_review" in reason


def test_granting_effects_excludes_obligate():
    assert PolicyEffect.ALLOW in GRANTING_EFFECTS
    assert PolicyEffect.ALLOW_WITH_OBLIGATIONS in GRANTING_EFFECTS
    assert PolicyEffect.OBLIGATE not in GRANTING_EFFECTS


def test_obligations_survive_a_dict_round_trip():
    rule = PolicyRule("allow_with_obligations", "release", role="developer",
                      obligations={"tests_green": False}, priority=7)
    restored = PolicyRule.from_dict(rule.to_dict())
    assert restored.effect is PolicyEffect.ALLOW_WITH_OBLIGATIONS
    assert restored.obligations == {"tests_green": False}
    assert restored.priority == 7


def test_residual_obligations_report_the_undischarged_duty():
    engine = _engine(PolicyRule("allow_with_obligations", "release",
                                obligations={"changelog": False}))
    residual = engine.residual_obligations("release")
    assert residual["discharged"] is False
    assert residual["unmet"] == ["changelog"]
    assert residual["obligations"] == {"changelog": False}

    engine = _engine(PolicyRule("allow", "read_files", role="developer"))
    residual = engine.residual_obligations("read_files", role="developer")
    assert residual["discharged"] is True
    assert residual["unmet"] == []


def test_residual_obligations_on_a_no_match_is_not_a_grant():
    engine = PolicyEngine()
    residual = engine.residual_obligations("anything")
    assert residual["effect"] is None
    assert residual["discharged"] is True


# ------------------------------------------------------------------- EP327

def test_authority_comparison_is_opt_in():
    """With no authority presented, the evaluation is the one it always was."""
    engine = _engine(PolicyRule("allow", "str_replace", role="developer"))
    assert engine.evaluate("str_replace", role="developer")[0] is True
    # even a registered requirement changes nothing until a caller presents
    # something to compare
    engine.require_authority("str_replace", "read-write")
    assert engine.evaluate("str_replace", role="developer")[0] is True


def test_exercising_more_authority_than_held_is_denied():
    engine = _engine(PolicyRule("allow", "*", role="developer"))
    allowed, reason = engine.evaluate(
        "str_replace", role="developer",
        authority={"granted": "read-only", "requires": "read-write"})
    assert allowed is False
    assert "DENIED (authority)" in reason
    assert "ceiling-deny" in reason


def test_authority_within_the_ceiling_still_follows_the_rules():
    engine = _engine(PolicyRule("allow", "*", role="developer"))
    allowed, _ = engine.evaluate(
        "str_replace", role="developer",
        authority={"granted": "admin", "requires": "read-write"})
    assert allowed is True

    # the ceiling passes, but no rule grants it -> the default still denies
    engine = _engine(PolicyRule("deny", "*", role="guest"))
    allowed, reason = engine.evaluate(
        "str_replace", role="guest",
        authority={"granted": "admin", "requires": "read-write"})
    assert allowed is False
    assert "DENIED by rule" in reason


def test_a_granted_only_caller_is_checked_against_the_registered_requirement():
    """The decorator path: `@enforce_gate(..., granted_authority=...)`.

    `enforce_gate`'s conditions become evaluation attributes, so a decorated
    call can present what the subject holds while the engine supplies what the
    action needs.
    """
    engine = _engine(PolicyRule("allow", "*", role="developer"))
    engine.require_authority("str_replace", "read-write")

    allowed, reason = engine.evaluate("str_replace", role="developer",
                                      authority="read-only")
    assert allowed is False
    assert "ceiling-deny" in reason

    allowed, _ = engine.evaluate("str_replace", role="developer",
                                 authority="read-write")
    assert allowed is True

    # same thing carried in attributes rather than the argument
    allowed, reason = engine.evaluate(
        "str_replace", role="developer",
        attributes={"granted_authority": "read-only"})
    assert allowed is False
    assert "ceiling-deny" in reason


def test_an_unresolvable_comparison_stays_silent_rather_than_guessing():
    """Presenting only `granted` with nothing registered is not a denial.

    There is nothing to compare against, so the engine falls through to the
    rules. Inventing a requirement here would refuse actions nobody declared.
    """
    engine = _engine(PolicyRule("allow", "*", role="developer"))
    allowed, _ = engine.evaluate("str_replace", role="developer",
                                 authority="read-only")
    assert allowed is True


# -------------------------------------------------------------------- EP269

def test_a_task_holds_a_scoped_authority():
    engine = PolicyEngine()
    record = engine.bind_task_authority("t-1", "admin", delegation_depth=0)
    assert record["authority"] == "admin"
    assert engine.task_authority("t-1")["authority"] == "admin"
    assert engine.task_authority("t-absent") is None


def test_delegation_attenuates_so_a_task_never_exceeds_what_it_was_handed():
    engine = PolicyEngine()
    assert engine.bind_task_authority("t-1", "admin", 1)["authority"] == "read-write"
    assert engine.bind_task_authority("t-2", "admin", 3)["authority"] == "none"


def test_task_authority_verdict_is_fail_closed_on_both_unknowns():
    engine = PolicyEngine()
    engine.require_authority("str_replace", "read-write")

    unbound = engine.task_authority_verdict("nobody", "str_replace")
    assert unbound["allowed"] is False
    assert unbound["reason"] == "no-authority-bound"

    engine.bind_task_authority("t-1", "admin")
    unregistered = engine.task_authority_verdict("t-1", "undeclared_action")
    assert unregistered["allowed"] is False
    assert "no-requirement-registered" in unregistered["reason"]


def test_task_authority_verdict_enforces_the_ceiling():
    engine = PolicyEngine()
    engine.require_authority("str_replace", "read-write")

    engine.bind_task_authority("powerful", "admin")
    assert engine.task_authority_verdict("powerful", "str_replace")["allowed"] is True

    engine.bind_task_authority("weak", "read-only")
    verdict = engine.task_authority_verdict("weak", "str_replace")
    assert verdict["allowed"] is False
    assert "ceiling-deny" in verdict["reason"]


def test_releasing_a_task_authority_revokes_it():
    engine = PolicyEngine()
    engine.require_authority("str_replace", "read-write")
    engine.bind_task_authority("t-1", "admin")
    assert engine.release_task_authority("t-1") is True
    assert engine.release_task_authority("t-1") is False
    assert engine.task_authority_verdict("t-1", "str_replace")["allowed"] is False
