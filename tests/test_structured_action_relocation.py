"""EP44 relocation — the ReAct driver's hand-off to its real host.

`structured_action.drive()` owns the thought->action->observation loop and the
plan repair; `workflow.py :: WorkflowEngine` / `DependencyGraph` owns the graph
those repairs must land in. The rescore calls the remaining work a *relocation*
whose size is S because the reasoning already exists — only the call path is
missing. These tests pin the hand-off surface that call path uses: the
engine-appliable insertions, the recorded-not-raised behaviour on a malformed
row, and the fact that exposing it did not invert the module edge.
"""

import ast
import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE import structured_action as sa  # noqa: E402

CONTRACT = {"verbs": ["read"], "targets": ["file"]}


def _thoughts(*proposals):
    """A thought_fn yielding the given proposals, then a terminating falsy one."""
    it = iter(proposals)

    def fn(_state):
        return next(it, None)

    return fn


def test_a_gap_extends_the_plan_and_hands_the_engine_an_insertion():
    result = sa.drive_for_workflow_step(
        "demo", ["step-1"], CONTRACT, ["file"],
        _thoughts({"thought": "look", "action": {"verb": "read", "target": "file"},
                   "state": {"file": "x"}},
                  {"thought": "type", "action": {"verb": "types", "target": "file"}}),
        cite=False)

    assert result["gaps"], "the unknown verb must be recorded as a gap"
    mutations = result["mutations"]
    assert mutations, "a repaired plan must hand the engine an insertion"
    first = mutations[0]
    assert first["insert"] == "repair:unknown-verb:types"
    assert first["because"] == "unknown-verb:types"
    assert first["after"] == 1
    assert first["step"]
    # the insertion is the same step the repaired plan carries
    assert first["insert"] in result["plan"]
    assert result["citation"] == "", "cite=False must not touch the ledger"


def test_a_clean_run_hands_the_engine_nothing_to_insert():
    result = sa.drive("demo", [], CONTRACT, ["file"],
                      _thoughts({"thought": "read it",
                                 "action": {"verb": "read", "target": "file"},
                                 "state": {"file": "x"}}))

    assert result["gaps"] == []
    assert result["closed"] is True
    assert sa.plan_mutations(result) == []


def test_plan_mutations_records_a_malformed_update_instead_of_raising():
    mutations = sa.plan_mutations({"updates": [
        {"at": "not-an-int", "inserted": "repair:x", "because": "g"},
        {"at": 0},          # no `inserted` -> recorded and skipped
        "not-a-dict",       # recorded and skipped
    ]})

    assert len(mutations) == 1, "only the salvageable row survives"
    assert mutations[0]["insert"] == "repair:x"
    assert mutations[0]["after"] == -1, "an unusable index degrades, never raises"


def test_exposing_the_hand_off_does_not_import_the_engine():
    """The edge stays one-way: the host imports us, never the reverse."""
    tree = ast.parse(Path(sa.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module or ''}.{a.name}" for a in node.names)

    offenders = sorted(m for m in imported
                       if "workflow" in m or m.endswith("dispatch_plan"))
    assert not offenders, (
        "EP44 is a relocation: the driver must stay reachable from the host "
        "without importing it. Found: %s" % offenders)
