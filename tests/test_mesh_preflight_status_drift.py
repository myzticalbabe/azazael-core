# coding: utf-8
"""`--check` must be able to fail on a status move, and must say when it cannot.

Why this file exists. The gate printed `statuses unchanged` while comparing only
the KEY SET of `NEURAL_MESH_WIRE_OR_CUT.json`: `changed` was initialised to `[]`
and never appended. A row flipped from `wired` to `retired` passed silently, and
the sentence a reader trusts -- "statuses unchanged" -- was never earned
(measured 2026-10-10: flipping a status still printed it).

The fix compares against GIT, never against the in-memory prior:
`wire_or_cut(funcs, committed)` takes the committed map as prior so hand-set
decisions survive a regeneration, which makes any comparison THROUGH it
self-fulfilling -- it can only read the file back. HEAD is fixed, so a
difference is a real edit.

The split the gate makes, and each clause here pins one side:
  * a status move WITH its justification (`reason`, `decider`,
    `decider_evidence`, `disposition`, `triage`) is a recorded decision -- it
    passes with a note, so adjudicating rows stays committable;
  * a bare move leaves the row arguing for a status it no longer declares --
    it fails and names both values;
  * when HEAD is unreadable the run says SKIPPED and why, and never prints the
    word "unchanged" over a comparison that did not happen.

Not covered here, deliberately: `wire_or_cut()` itself and the file-level
`main()`. Those need the live tree; `tests/test_mesh_wire_or_cut.py` owns them.
This file is the decision logic, which is why every clause calls a pure
function.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import mesh_function_preflight as mesh  # noqa: E402 - after sys.path


def _row(status, reason="only a string literal reaches it", **extra):
    row = {"status": status, "reason": reason}
    row.update(extra)
    return row


LEDGER_ROW = "consolidation:EngramIndexerWorm.lookup"


def _maps(current_row, head_row, key=LEDGER_ROW):
    return ({key: current_row}, {key: head_row}, {key: head_row})


# -- clause 1: unchanged means unchanged ------------------------------------


def test_an_unchanged_ledger_is_fresh_and_says_so():
    current, committed, head = _maps(_row("wired"), _row("wired"))
    verdict = mesh.assess_ledger(committed, current, head)
    assert verdict["ok"] is True, verdict
    assert verdict["undocumented"] == [] and verdict["documented"] == []
    lines = mesh.report_ledger(verdict, current)
    assert any("statuses unchanged against HEAD" in line for line in lines), lines
    assert not any("SKIPPED" in line for line in lines), lines


# -- clause 2: a bare flip is the defect the gate missed ---------------------


def test_a_bare_status_flip_fails_and_names_both_values():
    current, committed, head = _maps(_row("retired"), _row("wired"))
    verdict = mesh.assess_ledger(committed, current, head)
    assert verdict["ok"] is False, verdict
    assert verdict["undocumented"] == [
        {"id": LEDGER_ROW, "old": "wired", "new": "retired"}], verdict
    lines = mesh.report_ledger(verdict, current)
    joined = "\n".join(lines)
    assert "ledger STALE" in joined, joined
    assert f"! {LEDGER_ROW}: wired -> retired" in joined, joined
    assert "reason unchanged" in joined, joined
    # The honest count, not a reassuring phrase.
    assert "1 undocumented status move(s)" in joined, joined


def test_carry_evidence_alone_does_not_document_a_flip():
    """Regeneration rewrites `carry_evidence` without any decision moving."""
    old = _row("wired", carry_evidence="bare token 'execute' in 105 file(s)")
    new = _row("retired", carry_evidence="bare token 'execute' in 106 file(s)")
    current, committed, head = _maps(new, old)
    verdict = mesh.assess_ledger(committed, current, head)
    assert verdict["ok"] is False, verdict
    assert [m["id"] for m in verdict["undocumented"]] == [LEDGER_ROW], verdict
    assert verdict["documented"] == [], verdict


# -- clause 3: a recorded decision still passes ------------------------------


def test_a_documented_status_flip_passes_with_a_note():
    old = _row("wired", reason="statically called from the EP235 loop")
    new = _row("retired", reason="the EP235 loop was removed; no caller remains")
    current, committed, head = _maps(new, old)
    verdict = mesh.assess_ledger(committed, current, head)
    assert verdict["ok"] is True, verdict
    assert [m["id"] for m in verdict["documented"]] == [LEDGER_ROW], verdict
    assert verdict["undocumented"] == [], verdict
    lines = mesh.report_ledger(verdict, current)
    joined = "\n".join(lines)
    assert "1 documented status move(s)" in joined, joined
    assert f"~ {LEDGER_ROW}: wired -> retired" in joined, joined
    assert "justification updated" in joined, joined


# -- clause 4: the pre-existing key-set check must not regress --------------


def test_an_added_row_fails_on_the_key_set_check():
    committed = {LEDGER_ROW: _row("wired")}
    current = {LEDGER_ROW: _row("wired"), "axon:FreshWorm.execute": _row("wired")}
    verdict = mesh.assess_ledger(committed, current, dict(committed))
    assert verdict["ok"] is False, verdict
    assert verdict["added"] == ["axon:FreshWorm.execute"], verdict
    lines = mesh.report_ledger(verdict, current)
    assert any("+ axon:FreshWorm.execute" in line for line in lines), lines


def test_a_removed_row_fails_on_the_key_set_check():
    committed = {LEDGER_ROW: _row("wired"), "axon:Gone.execute": _row("wired")}
    current = {LEDGER_ROW: _row("wired")}
    verdict = mesh.assess_ledger(committed, current, dict(committed))
    assert verdict["ok"] is False, verdict
    assert verdict["removed"] == ["axon:Gone.execute"], verdict


# -- clause 5: the comparison is load-bearing --------------------------------


def test_the_status_comparison_is_load_bearing(monkeypatch):
    """Stub the comparison and the flipped fixture stops failing.

    Without this clause the flip test could be passing on the fixture rather
    than on the gate -- which is exactly how the old `changed = []` survived a
    passing negative control.
    """
    current, committed, head = _maps(_row("retired"), _row("wired"))
    monkeypatch.setattr(mesh, "status_drift", lambda cur, hd: ([], []))
    verdict = mesh.assess_ledger(committed, current, head)
    assert verdict["ok"] is True, verdict
    assert verdict["undocumented"] == [], verdict


# -- clause 6: no HEAD means no claim ---------------------------------------


def test_an_unreadable_head_is_reported_not_assumed_fresh():
    current, committed, _ = _maps(_row("wired"), _row("wired"))
    verdict = mesh.assess_ledger(committed, current, None)
    assert verdict["head_available"] is False, verdict
    assert verdict["ok"] is True, verdict
    lines = mesh.report_ledger(verdict, current, "git show exited 128 (not a git repository)")
    joined = "\n".join(lines)
    assert "status comparison SKIPPED" in joined, joined
    assert "not a git repository" in joined, joined
    assert "statuses NOT compared" in joined, joined
    assert "statuses unchanged" not in joined, joined


def test_head_ledger_reports_a_reason_instead_of_raising(tmp_path):
    outside = tmp_path / "NEURAL_MESH_WIRE_OR_CUT.json"
    outside.write_text("{}", encoding="utf-8")
    declared, reason = mesh.head_ledger(outside)
    assert declared is None, declared
    assert "outside the repository" in reason, reason


def test_head_ledger_survives_a_missing_git(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(mesh.subprocess, "run", _boom)
    declared, reason = mesh.head_ledger()
    assert declared is None, declared
    assert "git unavailable" in reason, reason
