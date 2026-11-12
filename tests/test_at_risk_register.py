"""Tests for tools/at_risk_register.py -- the guardian's register and its gate.

The point of these tests is direction, not coverage-by-count. A gate that only ever
says PASS is indistinguishable from no gate, so every E-number here is exercised
by a mutation the register must refuse, and at least one healthy shape it must
accept. E8 (the frontier) gets that treatment explicitly: an emptied, hidden,
wishful or untiered frontier is refused, while the shipped eight-cohort frontier
is accepted -- otherwise "all the systems threatened" would be a sample pretending
to be a whole.

No pytest.ini magic is needed: the tool is imported as a module (not run), so a bad
register never turns into a confusing subprocess error.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import at_risk_register as arr  # noqa: E402  (path set above, deliberately)


@pytest.fixture(scope="module")
def register() -> dict:
    return arr.load_register(arr.REGISTER_PATH)


def problems_after(register: dict, mutate) -> list[str]:
    mutated = copy.deepcopy(register)
    mutate(mutated)
    return arr.check_register(mutated)["problems"]


# --------------------------------------------------------------------------- #
# The shipped register is the healthy control
# --------------------------------------------------------------------------- #

def test_shipped_register_is_clean(register: dict) -> None:
    assert arr.check_register(register)["problems"] == []


def test_every_dying_entry_names_an_act_and_a_gate(register: dict) -> None:
    dying = [e for e in register["entries"] if e["status"] in arr.DYING]
    assert dying, "no dying entries -- the register has stopped registering"
    for entry in dying:
        assert len(entry["guardian"].strip()) >= arr.MIN_PROSE
        assert len(entry["gate"].strip()) >= arr.MIN_PROSE
        assert entry["guardian"] != entry["gate"]


# --------------------------------------------------------------------------- #
# E8 -- the frontier, refusals
# --------------------------------------------------------------------------- #

def test_frontier_hidden_is_refused(register: dict) -> None:
    def mutate(data: dict) -> None:
        data.pop("frontier", None)

    assert any(p.startswith("E8 frontier-declared")
               for p in problems_after(register, mutate))


def test_frontier_emptied_is_refused(register: dict) -> None:
    def mutate(data: dict) -> None:
        data["frontier"]["open_cohorts"] = []

    found = problems_after(register, mutate)
    assert any("fewer than the" in p for p in found)


def test_frontier_wish_is_refused(register: dict) -> None:
    def mutate(data: dict) -> None:
        data["frontier"]["open_cohorts"][0]["next_sweep"] = \
            "someone should probably look into this eventually"

    assert any("must begin with a verb" in p
               for p in problems_after(register, mutate))


def test_frontier_cohort_without_tier_is_refused(register: dict) -> None:
    def mutate(data: dict) -> None:
        data["frontier"]["open_cohorts"][0]["evidence"].pop("source_tier", None)

    assert any("no tiered evidence" in p
               for p in problems_after(register, mutate))


def test_frontier_duplicate_cohort_is_refused(register: dict) -> None:
    def mutate(data: dict) -> None:
        cohorts = data["frontier"]["open_cohorts"]
        cohorts.append(copy.deepcopy(cohorts[0]))

    assert any("listed twice" in p for p in problems_after(register, mutate))


# --------------------------------------------------------------------------- #
# E8 -- the frontier, healthy direction
# --------------------------------------------------------------------------- #

def test_frontier_accepts_a_well_formed_cohort(register: dict) -> None:
    def mutate(data: dict) -> None:
        data["frontier"]["open_cohorts"].append({
            "id": "test-cohort",
            "name": "A cohort invented by the test suite",
            "population": "unknown, which is why it is on the frontier",
            "why_not_yet": "no instrument lands here yet",
            "next_sweep": "ask the archive for its holdings and register the gap",
            "evidence": {"source": "test", "url": None,
                         "source_tier": "D", "evidence": "prior-knowledge-unfetched"},
        })

    assert arr.check_register(copy.deepcopy(register))["problems"] == []
    assert not [p for p in problems_after(register, mutate)
                if p.startswith("E8")]


def test_frontier_count_reaches_the_result(register: dict) -> None:
    result = arr.check_register(register)
    assert result["frontier_cohorts"] == len(register["frontier"]["open_cohorts"])
    assert result["frontier_cohorts"] >= arr.MIN_FRONTIER


# --------------------------------------------------------------------------- #
# E6 -- append-only, both directions
# --------------------------------------------------------------------------- #

def test_removal_without_tombstone_is_refused(register: dict) -> None:
    trimmed = copy.deepcopy(register)
    gone = trimmed["entries"].pop()
    trimmed["declared_entry_count"] = len(trimmed["entries"])
    found = arr.check_register(trimmed, register)["problems"]
    assert any(gone["id"] in p and "no tombstone" in p for p in found)


def test_removal_with_tombstone_is_accepted(register: dict) -> None:
    retired = copy.deepcopy(register)
    gone = retired["entries"].pop()
    retired["retired"] = [{"id": gone["id"],
                           "reason": "superseded by a finer-grained entry"}]
    retired["declared_entry_count"] = len(retired["entries"])
    found = arr.check_register(retired, register)["problems"]
    assert not [p for p in found if p.startswith("E6")]


# --------------------------------------------------------------------------- #
# The tool's own controls run, and they run against the real register
# --------------------------------------------------------------------------- #

def test_selftest_passes() -> None:
    failures, lines = arr.selftest(arr.REGISTER_PATH)
    assert failures == 0, "\n".join(lines)


def test_selftest_has_a_control_for_every_gate(register: dict) -> None:
    expected = {f"E{n}" for n in range(1, 9)}
    covered = set()
    for _name, _mutate, target, _mode in arr._mutations():
        covered.add(target.split()[0])
    assert expected <= covered, "no control for %s" % (expected - covered)


# --------------------------------------------------------------------------- #
# The register's shape, not just its verdict
# --------------------------------------------------------------------------- #

def test_classes_are_not_empty(register: dict) -> None:
    used = {e["class"] for e in register["entries"]}
    declared = {c["id"] for c in register["classes"]}
    assert used <= declared
    assert declared - used == set(), "a declared class holds nothing"


def test_evidence_never_cites_a_template_url(register: dict) -> None:
    for entry in register["entries"]:
        for evidence in entry["evidence"] if isinstance(entry["evidence"], list) \
                else [entry["evidence"]]:
            url = evidence.get("url")
            if url:
                assert "example.com" not in url
                assert not url.endswith("/...")


def test_register_is_valid_json_on_disk() -> None:
    raw = arr.REGISTER_PATH.read_text(encoding="utf-8")
    assert json.loads(raw)["entries"]
