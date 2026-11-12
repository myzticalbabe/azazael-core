"""`CascadeRouter`'s SOTA-row seams: two rows share this module, so both halves
are pinned here rather than in the analyzer-level test beside this file.

EP266 (ALAR adaptive latent reasoning) needed a per-turn choice between the
explicit chain-of-thought answer and the escalated latent reasoner. That is the
decision `route()` already makes, so it is `select_mode()`: the latent reasoner
is invoked only on the escalation arm, which is what makes the mode a behaviour
change rather than a label. That half is already pinned by
`tests/test_cascade_mode_select.py`, and its host (`neural_loop.py`) is wired
elsewhere, so this file does not duplicate it.

EP186 (SGDe harness compilation) and EP223 (Uno-Orchestra routing) need the same
cheap-vs-expensive decision taken PER PLAN NODE rather than once per task, so the
expensive tier is spent only on the nodes that need it — `route_node()`. EP223
additionally needs an allocation policy to supply the gate, which is the per-CALL
`threshold` override.

The regression half matters as much as the new half: `route()` is a live
escalation path with a tuned threshold, an escalation ledger and two counters, so
the default path is asserted unchanged (0.65 inclusive, counters, exact dict
shape on each arm) before anything new is asserted about it.

No network, no API keys, no model: every tier is a local stub.
"""


import json
import sys
from pathlib import Path

import pytest

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _router(**kwargs):
    from CORE.cascade import CascadeRouter

    return CascadeRouter(**kwargs)


def _tiers(confidence, calls=None):
    """(triage_fn, big_fn) returning fixed confidences and recording tier calls."""
    seen = calls if calls is not None else []

    def triage_fn(task):
        seen.append(("triage", task))
        return "cheap answer", confidence

    def big_fn(task):
        seen.append(("big", task))
        return "expensive answer"

    return triage_fn, big_fn, seen


def test_default_gate_is_inclusive_at_the_tuned_threshold():
    """0.65 is the OOF-ECE-tuned gate; `>=` means it must NOT escalate."""
    router = _router()
    assert router.threshold == 0.65

    triage_fn, big_fn, seen = _tiers(0.65)
    result = router.route("task", triage_fn, big_fn)

    assert result == {"answer": "cheap answer", "via": "triage",
                      "confidence": 0.65, "escalated": False}
    assert [tier for tier, _ in seen] == ["triage"], seen
    assert (router.n_triage, router.n_escalated) == (1, 0)


def test_below_the_gate_escalates_once_and_keeps_the_triage_answer():
    router = _router()
    triage_fn, big_fn, seen = _tiers(0.649)
    result = router.route("task", triage_fn, big_fn)

    assert result == {"answer": "expensive answer", "via": "big",
                      "confidence": 0.649, "escalated": True,
                      "triage_answer": "cheap answer"}
    assert [tier for tier, _ in seen] == ["triage", "big"], seen
    assert (router.n_triage, router.n_escalated) == (1, 1)
    assert router.stats()["escalation_rate"] == 1.0


def test_a_none_confidence_is_treated_as_zero_and_escalates():
    """The pre-existing defensive branch, pinned so the gate refactor kept it."""
    router = _router()
    triage_fn, big_fn, _ = _tiers(None)
    result = router.route("task", triage_fn, big_fn)

    assert result["escalated"] is True
    assert result["confidence"] == 0.0


def test_per_call_threshold_overrides_without_mutating_the_instance():
    """EP223's seam: an allocation policy supplies the gate for one call only."""
    router = _router()
    triage_fn, big_fn, _ = _tiers(0.50)

    raised = router.route("task", triage_fn, big_fn, threshold=0.90)
    assert raised["escalated"] is True, "a stricter gate must escalate"

    lowered = router.route("task", triage_fn, big_fn, threshold=0.40)
    assert lowered["escalated"] is False, "a looser gate must accept the cheap tier"

    # The instance gate is untouched: the override is per call, not a mutation.
    assert router.threshold == 0.65
    assert router.route("task", triage_fn, big_fn)["escalated"] is True, (
        "0.50 against the instance gate 0.65 must still escalate")


def test_route_node_offloads_per_node_and_shares_the_counters():
    """EP186: the decision is taken per plan node, on the per-task path's state."""
    router = _router()
    calls = []

    def triage_fn(task):
        confidences = {"cheap-node": 0.90, "hard-node": 0.10}
        calls.append(("triage", task))
        return "cheap:" + str(task), confidences[task]

    def big_fn(task):
        calls.append(("big", task))
        return "expensive:" + str(task)

    easy = router.route_node({"id": "cheap-node", "deps": []}, triage_fn, big_fn)
    hard = router.route_node({"id": "hard-node", "deps": ["cheap-node"]}, triage_fn, big_fn)

    assert easy["node"] == "cheap-node" and easy["escalated"] is False
    assert hard["node"] == "hard-node" and hard["escalated"] is True
    assert hard["answer"] == "expensive:hard-node"

    # Both tiers are reached per node, and the shared accounting saw both nodes.
    assert calls == [("triage", "cheap-node"), ("triage", "hard-node"),
                     ("big", "hard-node")], calls
    assert (router.n_triage, router.n_escalated) == (2, 1)

    # A node's own `task` wins over its id when it carries one.
    labelled = router.route_node({"id": "n9", "task": "cheap-node"}, triage_fn, big_fn)
    assert labelled["node"] == "n9"
    assert calls[-1] == ("triage", "cheap-node"), calls


def test_route_node_rejects_a_node_with_neither_id_nor_task():
    router = _router()
    triage_fn, big_fn, _ = _tiers(0.9)
    # pytest.raises asserts both halves — that ValueError IS raised, and that
    # nothing is silently accepted. The previous try/except/else needed a bare
    # `pass` handler, which the repo's scanner flags as a silent swallow.
    with pytest.raises(ValueError):
        router.route_node({"deps": []}, triage_fn, big_fn)

    assert (router.n_triage, router.n_escalated) == (0, 0), (
        "a rejected node must not be counted")


def test_only_escalations_reach_the_ledger(tmp_path):
    """`ledger_path` is the future SFT corpus: one row per escalation, none else."""
    ledger = tmp_path / "escalations.jsonl"
    router = _router(ledger_path=str(ledger))

    triage_fn, big_fn, _ = _tiers(0.90)
    router.route("an easy one", triage_fn, big_fn)
    assert not ledger.exists(), "a non-escalation has nothing to learn"

    triage_fn, big_fn, _ = _tiers(0.10)
    router.route("a hard one", triage_fn, big_fn)

    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1, rows
    assert rows[0]["task"] == "a hard one"
    assert rows[0]["triage_answer"] == "cheap answer"
    assert rows[0]["big_answer"] == "expensive answer"
    assert rows[0]["confidence"] == 0.10
