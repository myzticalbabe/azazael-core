"""LogicLoc (EP183) localizes a failing verification to its smallest region.

REACHABILITY CHAIN (this is what makes the row adopted rather than EXERCISE-ONLY):

    SMTVerifier.worker_verifier_judge()      # the documented JUDGE step
        -> SMTVerifier.verify_constraints()  # VERIFIER
        -> SMTVerifier.judge_result()        # <-- localize_violations() is called HERE

`worker_verifier_judge` is reached by the module-level convenience entry
`cd2_verifier.wvj()`, and `judge_result` is also called directly by
`tests/test_cross_domain_integration.py:253,257`. So the localizer runs on the
host's own production path (WORKER -> VERIFIER -> JUDGE -> RECORD), not in a new
standalone module -- a standalone module with no consumer would be EXERCISE-ONLY
by construction and would change no behaviour.

The technique: a verification reports WHICH check failed as a flat string. That
is enough to reject and not enough to repair. LogicLoc maps each violation back
onto the region responsible, ranks value-level checks ahead of shape-level ones,
and reports the DECLARED constraint it contradicted plus a repair hint.

Both halves are asserted, because a call that fires but changes nothing is not an
integration:
  * a REJECT now names the smallest region, and that region reaches the caller
    through the returned `localization` AND the rationale text it already read;
  * the ranking really discriminates -- the fixture produces a bounds violation
    and a missing-field violation at once, and `smallest` must be the BOUNDS one;
  * and the localizer refuses to invent a declaration when the caller supplied
    only the result (no constraint set), reporting `declared is None` instead.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

#: Transcribed from `PurePythonVerifier.verify`, which is the producer of these
#: strings. If that method's wording changes, the parser must be updated with it
#: -- these are the real formats, not invented ones.
FIXTURE_CONSTRAINTS = {
    "name": "logicloc_fixture",
    "required_fields": {"name": "str", "age": "int", "active": "bool"},
    "numeric_bounds": {"age": (0, 150)},
    # age is out of bounds AND active is missing: two kinds at once, which is
    # what makes the narrowest-first ranking testable.
    "values": {"name": "Bob", "age": 200},
}


def _localized():
    from CORE.cd2_verifier import SMTVerifier

    verifier = SMTVerifier(auto_record=False)
    result = verifier.verify_constraints(dict(FIXTURE_CONSTRAINTS))
    assert result["result"] == "UNSAT", (
        "fixture must fail verification, or this test proves nothing: "
        f"{result['result']}")
    judge = verifier.judge_result(result, FIXTURE_CONSTRAINTS)
    return result, judge


def test_every_producer_format_is_parsed_to_its_region():
    """The parser is pinned to the producer's four exact violation shapes."""
    from CORE.cd2_verifier import localize_violation

    missing = localize_violation("Required field 'active' (bool) missing")
    assert missing["region"] == "active"
    assert missing["kind"] == "required-field"
    assert missing["expected"] == "bool"

    mismatch = localize_violation(
        "Field 'age' type mismatch: expected int, got str")
    assert (mismatch["region"], mismatch["kind"]) == ("age", "type")
    assert (mismatch["expected"], mismatch["observed"]) == ("int", "str")

    bounds = localize_violation("Field 'age' = 200 out of bounds [0, 150]")
    assert (bounds["region"], bounds["kind"]) == ("age", "bounds")
    assert bounds["observed"] == "200"
    assert bounds["expected"] == ("0", "150"), bounds["expected"]

    logical = localize_violation("Field 'count' = 5, expected > 10")
    assert (logical["region"], logical["kind"]) == ("count", "logical")
    assert logical["observed"] == "5"
    assert logical["expected"] == (">", "10")


def test_reject_names_the_smallest_region_and_reaches_the_caller():
    """The JUDGE step reports the implicated region, in text AND in the dict."""
    _, judge = _localized()

    assert judge["verdict"] == "REJECT"
    localization = judge.get("localization")
    assert localization is not None, "a REJECT must carry its localization"
    smallest = localization["smallest"]
    # Both violations are present, so the ranking is the discriminating part:
    # the bounds check (value-level) must outrank the missing field.
    assert smallest["region"] == "age", (smallest, localization["regions"])
    assert smallest["kind"] == "bounds"
    # The DECLARED constraint came from the set the pipeline still held.
    assert smallest["declared"] == (0, 150), smallest["declared"]
    assert "age" in smallest["repair"]
    # And it reaches the caller through the field it already read.
    assert "age" in judge["rationale"]
    assert judge["rationale"].startswith("Smallest implicated region")


def test_ranking_prefers_value_checks_over_presence_checks():
    """`smallest` is narrowest-first, and ties keep the reported order."""
    from CORE.cd2_verifier import localize_violations

    violations = ["Required field 'active' (bool) missing",
                  "Field 'age' = 200 out of bounds [0, 150]"]
    out = localize_violations(FIXTURE_CONSTRAINTS, violations)
    assert [r["kind"] for r in out["regions"]] == ["bounds", "required-field"]
    assert out["smallest"]["region"] == "age"
    assert out["n"] == 2
    assert out["unlocated"] == []


def test_an_unrecognised_violation_is_reported_not_guessed():
    """An unknown format is surfaced in `unlocated`, never silently localized."""
    from CORE.cd2_verifier import localize_violations

    out = localize_violations({}, ["something the producer never emits"])
    assert out["unlocated"] == ["something the producer never emits"]
    assert out["smallest"]["located"] is False
    assert out["smallest"]["region"] == ""


def test_without_the_constraint_set_the_declaration_is_absent_not_invented():
    """`judge_result(result)` alone still localizes the region, and says so."""
    from CORE.cd2_verifier import SMTVerifier

    verifier = SMTVerifier(auto_record=False)
    result = verifier.verify_constraints(dict(FIXTURE_CONSTRAINTS))
    judge = verifier.judge_result(result)          # no constraints passed

    smallest = judge["localization"]["smallest"]
    assert smallest["region"] == "age"             # from the violation string
    assert smallest["kind"] == "bounds"
    assert smallest["declared"] is None, (
        "the declared constraint was not supplied; it must not be invented")


def test_accept_is_unchanged_and_carries_no_localization():
    """The ACCEPT path is untouched: no localization, same verdict."""
    from CORE.cd2_verifier import SMTVerifier

    verifier = SMTVerifier(auto_record=False)
    ok = {
        "name": "logicloc_sat",
        "required_fields": {"name": "str", "age": "int"},
        "numeric_bounds": {"age": (0, 150)},
        "values": {"name": "Alice", "age": 30},
    }
    result = verifier.verify_constraints(ok)
    assert result["result"] == "SAT", result
    judge = verifier.judge_result(result, ok)
    assert judge["verdict"] == "ACCEPT"
    assert judge["confidence"] == "high"
    assert "localization" not in judge
