"""Tests for tools/task_shape_check.py -- the gate over the task file itself.

The property under test is narrow and load-bearing: a task whose finish line is an
adjective must not be executable. Both directions are asserted, because a refusal-only
gate cannot ship anything through itself, and a gate that never refuses is decoration.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import task_shape_check as tsc  # noqa: E402  (path set above, deliberately)


@pytest.fixture(scope="module")
def tasks() -> dict:
    return tsc.load(tsc.TASKS_PATH)


def one_shaped(tasks: dict) -> dict:
    return tsc.shaped(tasks["tasks"])[0]


def problems_after(tasks: dict, mutate) -> list[str]:
    mutated = copy.deepcopy(tasks)
    mutate(mutated)
    return tsc.check_tasks(mutated)["problems"]


# --------------------------------------------------------------------------- #
# The shipped file
# --------------------------------------------------------------------------- #

def test_the_shipped_task_file_passes(tasks: dict) -> None:
    assert tsc.check_tasks(tasks)["problems"] == []


def test_legacy_tasks_are_counted_not_failed(tasks: dict) -> None:
    result = tsc.check_tasks(tasks)
    assert result["tasks_total"] == result["tasks_shaped"] + len(result["unshaped"])
    assert not [p for p in result["problems"] if "legacy" in p]


def test_the_contract_names_the_executor_and_its_failure_modes(tasks: dict) -> None:
    contract = tasks["execution_contract"]
    assert contract["shape"] == tsc.SHAPE
    modes = contract["known_failure_modes"]
    assert len(modes) >= 5
    for mode in modes:
        assert mode["check_that_fires"], mode.get("id")


def test_every_failure_mode_points_at_a_field_that_exists(tasks: dict) -> None:
    """A mode caught by a field no task must carry is a mode nothing catches.

    The vocabulary is the task fields UNION the frame fields: 'deletes the obstacle to
    go green' is caught by `defends`, which lives in Project_FLAMING_SWORD's frame, not
    in the task file. Asserting the narrower set was wrong and the gate now checks it.
    """
    vocabulary = set(tsc.REQUIRED_FIELDS) | set(tsc.FRAME_FIELDS)
    for mode in tasks["execution_contract"]["known_failure_modes"]:
        assert mode["caught_by"].split()[0] in vocabulary, mode["caught_by"]


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

def test_adjectival_acceptance_is_refused(tasks: dict) -> None:
    def mutate(data: dict) -> None:
        one_shaped(data)["acceptance"] = "the validator is good enough"

    found = problems_after(tasks, mutate)
    assert any(p.startswith("T5 no-acceptance") for p in found)


def test_acceptance_without_a_command_is_refused(tasks: dict) -> None:
    def mutate(data: dict) -> None:
        one_shaped(data)["acceptance"] = "twelve shards validate and seven are measured"

    assert any("names no command" in p for p in problems_after(tasks, mutate))


def test_acceptance_without_an_outcome_is_refused(tasks: dict) -> None:
    def mutate(data: dict) -> None:
        one_shaped(data)["acceptance"] = "run python tools/thing.py and see"

    assert any("no observable outcome" in p for p in problems_after(tasks, mutate))


def test_control_that_refuses_nothing_is_refused(tasks: dict) -> None:
    def mutate(data: dict) -> None:
        one_shaped(data)["control"] = "also run the same command again to be sure"

    assert any(p.startswith("T3 control-present") for p in problems_after(tasks, mutate))


def test_evidence_promise_is_refused(tasks: dict) -> None:
    def mutate(data: dict) -> None:
        one_shaped(data)["evidence"] = "TBD"

    assert any(p.startswith("T4 evidence-lands") for p in problems_after(tasks, mutate))


def test_missing_field_is_refused(tasks: dict) -> None:
    def mutate(data: dict) -> None:
        one_shaped(data).pop("refusal", None)

    assert any(p.startswith("T1 shape-complete") for p in problems_after(tasks, mutate))


def test_duplicate_id_is_refused(tasks: dict) -> None:
    def mutate(data: dict) -> None:
        data["tasks"].append(copy.deepcopy(one_shaped(data)))

    assert any("appears 2 times" in p for p in problems_after(tasks, mutate))


def test_unknown_shape_marker_is_refused(tasks: dict) -> None:
    """Regression: the branch existed but was unreachable, because `shaped()` selects on
    the marker -- a task with a typo in `shape` skipped every T-check while looking
    shaped to a reader."""
    def mutate(data: dict) -> None:
        one_shaped(data)["shape"] = "vibes-v2"

    assert any("no checker knows" in p for p in problems_after(tasks, mutate))


def test_a_failure_mode_caught_by_nothing_is_refused(tasks: dict) -> None:
    def mutate(data: dict) -> None:
        data["execution_contract"]["known_failure_modes"][0]["caught_by"] = \
            "wishful_thinking"

    assert any("neither a task field nor a frame field" in p
               for p in problems_after(tasks, mutate))


# --------------------------------------------------------------------------- #
# Healthy direction
# --------------------------------------------------------------------------- #

def test_a_well_formed_task_is_accepted(tasks: dict) -> None:
    def mutate(data: dict) -> None:
        data["tasks"].append({
            "id": "M-TEST",
            "title": "A task added by the test suite",
            "owner": "pytest",
            "depends_on": [],
            "shape": tsc.SHAPE,
            "deliverable": "nothing: this task exists to be accepted",
            "acceptance": "`python tools/task_shape_check.py --check` exits 0 and prints "
                          "the shaped count",
            "control": "a task whose acceptance reads 'looks right' must be REFUSED",
            "refusal": "BLOCK when the acceptance names no command",
            "evidence": "stdout of tools/task_shape_check.py --selftest",
        })

    assert not [p for p in problems_after(tasks, mutate) if p.startswith("T")]


def test_the_control_field_may_quote_the_phrase_it_refuses(tasks: dict) -> None:
    """The regression T5 exists for: scanning `control` refused the honest control."""
    def mutate(data: dict) -> None:
        one_shaped(data)["control"] = ("set the acceptance to 'good enough' and require "
                                       "T5 to REFUSE that task")

    assert not [p for p in problems_after(tasks, mutate)
                if p.startswith("T5 no-acceptance")]


# --------------------------------------------------------------------------- #
# The tool's own controls
# --------------------------------------------------------------------------- #

def test_selftest_passes() -> None:
    failures, lines = tsc.selftest(tsc.TASKS_PATH)
    assert failures == 0, "\n".join(lines)


def test_every_check_has_a_control() -> None:
    covered = {target.split()[0] for _n, _m, target in tsc._mutations()}
    assert {"T1", "T2", "T3", "T4", "T5"} <= covered, "no control for %s" % (
        {"T1", "T2", "T3", "T4", "T5"} - covered)


def test_json_summary_is_parseable(tasks: dict) -> None:
    payload = json.dumps(tsc.check_tasks(tasks))
    assert json.loads(payload)["tasks_shaped"] >= 1
