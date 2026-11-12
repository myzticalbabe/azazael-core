"""task_shape_check.py -- judge whether a task is executable, not whether it is nice.

Written because the executor changed. `docs/METTHEW/tasks.json` is the queue a model
works through, and a task written for a careful reader fails differently under a
model that would rather write a paragraph than run a command. The failure is not
dishonesty, it is shape: "implement the shard validator" has no predicate, so any
output satisfies it, and the cheapest satisfying output is a summary claiming
success.

So the shape carries the refusals:

  T1 shape-complete     every field the executor must fill, filled.
  T2 predicate-runnable `acceptance` names a COMMAND and an observable outcome.
  T3 control-present    `control` names a mutation that MUST be refused (RULE 0B-C).
  T4 evidence-lands     `evidence` names a path or a command, never "TBD".
  T5 no-acceptance      acceptance language is refused, not discouraged: "looks
                        right", "should work", "good enough" decide nothing.

Legacy tasks are NOT failed for lacking the shape. They are counted and printed as
coverage (`%d of %d carry a predicate`), because a gate that retroactively fails 33
finished tasks teaches the executor to route around it.

Usage:
  python tools/task_shape_check.py --check
  python tools/task_shape_check.py --summary
  python tools/task_shape_check.py --selftest
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
TASKS_PATH = REPO / "docs" / "METTHEW" / "tasks.json"

#: The shape marker a task carries once it is written for a machine to execute.
SHAPE = "defensive-v1"

#: Fields a shaped task must carry. `depends_on` is allowed to be empty but must be
#: present: an absent dependency list and a deliberate "none" are different claims.
REQUIRED_FIELDS = ("id", "title", "owner", "deliverable", "acceptance", "control",
                   "refusal", "evidence", "depends_on", "shape")

#: The frame vocabulary, from Project_FLAMING_SWORD/tools/defender.py. A failure mode
#: may be caught there instead of in the task file -- "deletes the obstacle to go green"
#: is caught by `defends`/`must_not_break`, which live in the frame. Kept as its own
#: list so a mode caught by NEITHER vocabulary is refused rather than quietly believed.
FRAME_FIELDS = ("defends", "must_not_break", "posture")

MIN_PROSE = 40

#: Fields whose value is a name, not prose. Holding `id` to a sentence is the kind of
#: check that fires on correct data and trains the reader to ignore the gate.
_NAME_FIELDS = ("id", "owner", "shape")

#: A predicate names something that can actually be run. Kept deliberately loose --
#: a false positive costs one edit, a false negative lets "verify it" through.
_COMMAND_RE = re.compile(
    r"(python3?\s|pytest\s|git\s|tools/[\w/]+\.py|scripts/[\w/]+\.py|"
    r"`[^`]+`|\b(exit|rc|status)\s*(code|==|\bis\b)|--check|--selftest)", re.I)

#: Something observable: a number, a verdict word, or an exit status.
_OUTCOME_RE = re.compile(
    r"(\b\d+\b|PASS|FAIL|BLOCK|refus|exit\s*(code|status)?\s*\d?|zero|exactly|count)",
    re.I)

#: A control must describe a shape that is REJECTED -- otherwise it is a second
#: happy path wearing the word "test".
_CONTROL_RE = re.compile(r"(refus|reject|fail|block|must not|error|exit 1)", re.I)

#: Acceptance language. The estate's RULE 0B forbids it in prose; here it is refused
#: structurally, because a task whose finish line is an adjective has no finish line.
_ACCEPTANCE_PHRASES = (
    "good enough", "looks right", "looks good", "should work", "should be fine",
    "seems fine", "seems right", "works as expected", "is done", "we are done",
    "obviously", "clearly correct", "no issues",
)

_PLACEHOLDERS = ("tbd", "todo", "n/a", "later", "unknown")


def load(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("tasks file unreadable: %s" % exc) from exc


def shaped(tasks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [t for t in tasks if isinstance(t, dict) and t.get("shape") == SHAPE]


# --------------------------------------------------------------------------- #
# T1..T5
# --------------------------------------------------------------------------- #

def check_shape(task: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    identifier = task.get("id") or "<no id>"
    for field in REQUIRED_FIELDS:
        if field not in task:
            problems.append("T1 shape-complete: %s has no %r" % (identifier, field))
            continue
        value = task[field]
        if field == "depends_on":
            if not isinstance(value, list):
                problems.append("T1 shape-complete: %s depends_on is not a list"
                                % identifier)
            continue
        floor = 2 if field in _NAME_FIELDS else 12
        if not isinstance(value, str) or len(value.strip()) < floor:
            problems.append("T1 shape-complete: %s field %r is missing or too thin to "
                            "bind the executor" % (identifier, field))
    # An unrecognised shape marker is NOT checked here: `shaped()` selects on the marker,
    # so a task carrying a typo never reaches this function and the branch would be dead
    # code that reads like a control. It is caught in check_tasks instead, over every
    # task that declares a shape. (Found by a test that mutated the marker and got no
    # refusal -- the branch was unreachable, so the gate had a hole where it looked
    # strictest.)
    return problems


def check_predicate(task: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    identifier = task.get("id") or "<no id>"
    acceptance = str(task.get("acceptance") or "")
    if not _COMMAND_RE.search(acceptance):
        problems.append("T2 predicate-runnable: %s acceptance names no command, so any "
                        "output satisfies it" % identifier)
    if not _OUTCOME_RE.search(acceptance):
        problems.append("T2 predicate-runnable: %s acceptance names no observable "
                        "outcome (count, verdict or exit status)" % identifier)
    return problems


def check_control(task: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    identifier = task.get("id") or "<no id>"
    control = str(task.get("control") or "")
    if not _CONTROL_RE.search(control):
        problems.append("T3 control-present: %s has no negative control -- nothing in "
                        "its `control` must be refused" % identifier)
    refusal = str(task.get("refusal") or "")
    if not _CONTROL_RE.search(refusal):
        problems.append("T3 control-present: %s does not state what must BLOCK"
                        % identifier)
    if control.strip() and control.strip() == refusal.strip():
        problems.append("T3 control-present: %s uses one sentence as both the control "
                        "and the refusal" % identifier)
    return problems


def check_evidence(task: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    identifier = task.get("id") or "<no id>"
    evidence = str(task.get("evidence") or "").strip()
    lowered = evidence.lower()
    if not evidence:
        problems.append("T4 evidence-lands: %s names no evidence" % identifier)
        return problems
    for word in _PLACEHOLDERS:
        if word in lowered:
            problems.append("T4 evidence-lands: %s evidence says %r, which is a promise "
                            "to produce evidence later" % (identifier, word))
    if not (_COMMAND_RE.search(evidence) or "/" in evidence or "\\" in evidence):
        problems.append("T4 evidence-lands: %s evidence names neither a path nor a "
                        "command" % identifier)
    return problems


def check_language(task: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    identifier = task.get("id") or "<no id>"
    # `control` is deliberately NOT scanned: its job is to NAME the bad shape it
    # refuses, so a control reading "set acceptance to 'good enough' and require a
    # refusal" is doing exactly what T5 asks. Scanning it would refuse the only kind
    # of control that proves T5 works.
    blob = " ".join(str(task.get(field) or "")
                    for field in ("acceptance", "refusal", "evidence")).lower()
    for phrase in _ACCEPTANCE_PHRASES:
        if phrase in blob:
            problems.append("T5 no-acceptance: %s finishes on %r; only a measurement "
                            "closes a task" % (identifier, phrase))
    return problems


def check_tasks(data: Dict[str, Any]) -> Dict[str, Any]:
    tasks = [t for t in data.get("tasks") or [] if isinstance(t, dict)]
    problems: List[str] = []
    seen: Dict[str, int] = {}
    for task in tasks:
        identifier = str(task.get("id") or "")
        seen[identifier] = seen.get(identifier, 0) + 1
    for identifier, count in seen.items():
        if count > 1:
            problems.append("T1 shape-complete: task id %r appears %d times"
                            % (identifier, count))

    # Every declared shape must be the one a checker knows. A typo here would otherwise
    # make the task invisible to T1..T5 while still looking shaped to a reader.
    for task in tasks:
        declared = task.get("shape")
        if declared is not None and declared != SHAPE:
            problems.append("T1 shape-complete: %s declares shape %r, which no checker "
                            "knows -- it would pass unread"
                            % (task.get("id") or "<no id>", declared))

    for task in shaped(tasks):
        problems.extend(check_shape(task))
        problems.extend(check_predicate(task))
        problems.extend(check_control(task))
        problems.extend(check_evidence(task))
        problems.extend(check_language(task))

    contract = data.get("execution_contract")
    if not isinstance(contract, dict):
        problems.append("T1 shape-complete: there is no execution_contract object, so "
                        "nothing records who the tasks are written for")
    else:
        if contract.get("shape") != SHAPE:
            problems.append("T1 shape-complete: execution_contract.shape is %r, not %r"
                            % (contract.get("shape"), SHAPE))
        for key in ("executor", "known_failure_modes", "how_the_shape_defends"):
            if not isinstance(contract.get(key), (list, str)) or \
                    (isinstance(contract.get(key), (list, str))
                     and len(contract.get(key)) == 0):
                problems.append("T1 shape-complete: execution_contract.%s is missing"
                                % key)
        # A failure mode caught by a field that does not exist is a mode nothing
        # catches -- the table would read as coverage and deliver none.
        vocabulary = set(REQUIRED_FIELDS) | set(FRAME_FIELDS)
        for mode in contract.get("known_failure_modes") or []:
            if not isinstance(mode, dict):
                problems.append("T1 shape-complete: a known_failure_modes entry is not "
                                "an object")
                continue
            caught_by = str(mode.get("caught_by") or "")
            field = caught_by.split()[0] if caught_by else ""
            if field not in vocabulary:
                problems.append("T1 shape-complete: failure mode %r is caught by %r, "
                                "which is neither a task field nor a frame field"
                                % (mode.get("id"), caught_by))

    return {
        "problems": problems,
        "tasks_total": len(tasks),
        "tasks_shaped": len(shaped(tasks)),
        "unshaped": [str(t.get("id")) for t in tasks if t.get("shape") != SHAPE],
    }


def summary(result: Dict[str, Any]) -> List[str]:
    lines = [
        "tasks: %d shaped of %d total (%.0f%%)"
        % (result["tasks_shaped"], result["tasks_total"],
           100.0 * result["tasks_shaped"] / max(1, result["tasks_total"])),
        "unshaped: %d -- counted, not failed: a task written before the shape existed "
        "is legacy, not a lie" % len(result["unshaped"]),
    ]
    if result["unshaped"]:
        lines.append("  %s" % ", ".join(result["unshaped"]))
    lines.append("shape %s: acceptance must name a command AND an outcome; `control` "
                 "must name a shape that is refused; acceptance language is refused"
                 % SHAPE)
    return lines


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #

def _mutations() -> List[Tuple[str, Any, str]]:
    def acceptance_is_adjective(data: Dict[str, Any]) -> None:
        shaped(data["tasks"])[0]["acceptance"] = "the validator is good enough"

    def acceptance_has_no_command(data: Dict[str, Any]) -> None:
        shaped(data["tasks"])[0]["acceptance"] = \
            "twelve shards validate and seven hundred bytes are measured"

    def control_is_a_second_happy_path(data: Dict[str, Any]) -> None:
        shaped(data["tasks"])[0]["control"] = "also run it once with the real fixture"

    def evidence_defers(data: Dict[str, Any]) -> None:
        shaped(data["tasks"])[0]["evidence"] = "TBD"

    def field_dropped(data: Dict[str, Any]) -> None:
        shaped(data["tasks"])[0].pop("refusal", None)

    def shape_forgotten(data: Dict[str, Any]) -> None:
        data["execution_contract"].pop("shape", None)

    def unknown_shape_marker(data: Dict[str, Any]) -> None:
        shaped(data["tasks"])[0]["shape"] = "vibes-v2"

    def mode_caught_by_nothing(data: Dict[str, Any]) -> None:
        data["execution_contract"]["known_failure_modes"][0]["caught_by"] = \
            "wishful_thinking"

    def duplicate_id(data: Dict[str, Any]) -> None:
        twin = copy.deepcopy(shaped(data["tasks"])[0])
        data["tasks"].append(twin)

    return [
        ("acceptance is an adjective", acceptance_is_adjective, "T5 no-acceptance"),
        ("acceptance has no command", acceptance_has_no_command,
         "T2 predicate-runnable"),
        ("control is a second happy path", control_is_a_second_happy_path,
         "T3 control-present"),
        ("evidence defers", evidence_defers, "T4 evidence-lands"),
        ("required field dropped", field_dropped, "T1 shape-complete"),
        ("contract shape forgotten", shape_forgotten, "T1 shape-complete"),
        ("duplicate id", duplicate_id, "T1 shape-complete"),
        ("unknown shape marker", unknown_shape_marker, "T1 shape-complete"),
        ("mode caught by nothing", mode_caught_by_nothing, "T1 shape-complete"),
    ]


def selftest(path: Path) -> Tuple[int, List[str]]:
    lines: List[str] = []
    failures = 0
    try:
        data = load(path)
    except RuntimeError as exc:
        return 1, ["UNUSABLE: %s" % exc]

    healthy = check_tasks(data)["problems"]
    if healthy:
        failures += 1
        lines.append("FAIL healthy control: the shipped task file is refused:")
        lines.extend("     %s" % problem for problem in healthy[:6])
    else:
        lines.append("PASS healthy control: the shipped task file is clean")

    if not shaped(data.get("tasks") or []):
        failures += 1
        lines.append("FAIL no shaped task exists, so no control can be exercised")

    for name, mutate, expected in _mutations():
        mutated = copy.deepcopy(data)
        try:
            mutate(mutated)
        except (KeyError, IndexError, TypeError) as exc:
            failures += 1
            lines.append("FAIL %s: the mutation could not be applied (%s)" % (name, exc))
            continue
        problems = check_tasks(mutated)["problems"]
        if [p for p in problems if p.startswith(expected)]:
            lines.append("PASS %s -> %s" % (name, expected))
        else:
            failures += 1
            lines.append("FAIL %s: expected %r, got %s"
                         % (name, expected, problems[:2] or "no refusal"))

    # The healthy direction: a task that IS shaped must be accepted, or the gate is
    # only ever a refusal and nothing can be shipped through it.
    clean = copy.deepcopy(data)
    clean["tasks"].append({
        "id": "M-TEST", "track": "X", "title": "A task invented by the selftest",
        "owner": "selftest", "depends_on": [], "shape": SHAPE,
        "deliverable": "nothing; this task exists to be accepted",
        "acceptance": "`python tools/task_shape_check.py --check` exits 0 and prints "
                      "the shaped count",
        "control": "a task with acceptance 'looks right' must be REFUSED by T5",
        "refusal": "BLOCK when the acceptance names no command",
        "evidence": "stdout of tools/task_shape_check.py --selftest",
        "status": "pending",
    })
    leftover = [p for p in check_tasks(clean)["problems"] if "M-TEST" in p]
    if leftover:
        failures += 1
        lines.extend(["FAIL a correctly shaped task was refused:"] +
                     ["     %s" % problem for problem in leftover])
    else:
        lines.append("PASS a correctly shaped task is accepted")

    return failures, lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Judge whether a task can be executed.")
    parser.add_argument("--tasks", default=str(TASKS_PATH))
    parser.add_argument("--check", action="store_true", help="judge (default)")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.tasks)

    if args.selftest:
        failures, lines = selftest(path)
        if args.json:
            print(json.dumps({"failures": failures, "lines": lines}, indent=1))
        else:
            for line in lines:
                print(line)
            print("")
            print("SELFTEST %s (%d control(s), %d failure(s))"
                  % ("OK" if not failures else "FAILED", len(lines), failures))
        return 0 if not failures else 1

    try:
        data = load(path)
    except RuntimeError as exc:
        print("UNUSABLE: %s" % exc, file=sys.stderr)
        return 2

    result = check_tasks(data)
    if args.summary:
        if args.json:
            print(json.dumps(result, indent=1, sort_keys=True))
        else:
            for line in summary(result):
                print(line)
        return 0

    if args.json:
        print(json.dumps(result, indent=1, sort_keys=True))
    else:
        print(("PASS %s" if not result["problems"] else "FAIL %s") % path.name)
        for problem in result["problems"]:
            print("     %s" % problem)
        for line in summary(result):
            print("  %s" % line)
        print("  note: this gate judges the SHAPE of a task, not whether the work was "
              "done; the acceptance command is what decides that")
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
