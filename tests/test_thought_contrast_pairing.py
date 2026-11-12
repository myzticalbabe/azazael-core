# coding: utf-8
"""DPO thought-pairing: the selector must be balanced, tiered and diagnosable.

Followup #4 (2026-09-28). Measured on the live store: of 147 `thought_process`
rows, 139 belonged to one family ("evolution promotion verdict") and there was
exactly ONE failure in the whole table. The old selector scanned only the newest
120 rows, so the window was saturated by a single failure-free family and the
pair was skipped with a bare "no same-family contrast" -- a correct verdict with
an undiagnosable reason.

`run_training_orchestrator.thought_contrast` is the extracted, pure selector:

  * it returns a CENSUS, so a skip names what was actually in the window;
  * it prefers the most recent failure;
  * when no exact family has a contrast it falls back to a same-verb tier and
    LABELS the pair, so a weak contrast is never mistaken for a strong one.
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_PROJECT_ROOT, "scripts"), _PROJECT_ROOT,
           os.path.join(_PROJECT_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_training_orchestrator as orch  # noqa: E402


def _row(mem_id, task, success):
    return (mem_id, {"task": task, "success": success})


def test_exact_family_contrast_is_found():
    rows = [
        _row(10, "fix flat-fitness false positive", True),
        _row(9, "fix flat-fitness false negative", False),
    ]
    sel = orch.thought_contrast(rows)
    ok, bad, fam = sel["pair"]
    assert sel["tier"] == "family"
    assert fam == "fix flat-fitness false"
    assert (ok[0], bad[0]) == (10, 9)


def test_a_saturated_window_does_not_hide_an_older_family():
    rows = [_row(i, "evolution promotion verdict", True)
            for i in range(200, 0, -1)]
    rows.append(_row(1, "fix flat-fitness false positive", True))
    rows.append(_row(0, "fix flat-fitness false negative", False))
    sel = orch.thought_contrast(rows)
    assert sel["pair"] is not None, "one loud family evicted the only contrast"
    assert sel["tier"] == "family"
    assert sel["census"]["families"] == 2


def test_the_newest_failure_wins_within_a_family():
    rows = [
        _row(5, "fix flat-fitness false negative", False),
        _row(4, "fix flat-fitness false positive", True),
        _row(3, "fix flat-fitness false older failure", False),
    ]
    _ok, bad, _fam = orch.thought_contrast(rows)["pair"]
    assert bad[0] == 5, "should pair against the most recent failure"


def test_verb_tier_is_used_and_labelled_when_no_family_matches():
    rows = [
        _row(2, "fix alpha thing", False),
        _row(1, "fix beta other", True),
    ]
    sel = orch.thought_contrast(rows)
    ok, bad, fam = sel["pair"]
    assert sel["tier"] == "verb", "a weak pair must be labelled as weak"
    assert fam == "fix"
    assert (ok[0], bad[0]) == (1, 2)


def test_no_contrast_returns_a_diagnosable_census():
    rows = [_row(i, "evolution promotion verdict", True)
            for i in range(5, 0, -1)]
    sel = orch.thought_contrast(rows)
    assert sel["pair"] is None and sel["tier"] == ""
    census = sel["census"]
    assert census["scanned"] == 5
    assert census["families"] == 1
    assert census["families_with_both"] == []
    assert census["top_families"][0] == {
        "family": "evolution promotion verdict", "success": 5, "fail": 0}


def test_rows_without_a_success_flag_are_ignored():
    rows = [(1, {"task": "x y z"}), (2, None), (3, "not-a-dict")]
    sel = orch.thought_contrast(rows)
    assert sel["pair"] is None
    assert sel["census"]["scanned"] == 0


def test_window_is_wider_than_the_saturating_one():
    assert orch._THOUGHT_WINDOW > 120
