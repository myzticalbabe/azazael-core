"""Regression tests for the operator-task scoring instrument (azazael_operator_task.py).

Three defects this locks down, all found by measurement on 2026-09-19:

1. A FAIL had one label. `wall_clock_cap`, a decoy-tree loss and a genuine wrong
   answer all read as "the model failed", so a task defect looked like a capability
   verdict. qwopus read the worm count correctly (231) on one run and quit at 793 s
   on a decoy `worm_registry.py` on the next -- same model, same flags.

2. The measuring device moved with the thing it measured: `ground_truth()` resolved
   `azazael/src` from the cwd, and the model's cwd was the home directory, which
   holds three `worm_registry.py` copies.

3. A reasoning model spent its whole token cap thinking, so `content` came back
   empty and the turn scored as "emitted no code". Measured: qwopus needs ~127
   tokens of thinking before its first content token (326 for a one-function
   answer) while driver.py defaults to max_tokens=96.

The FIRST attempt at the classifier was itself wrong and measurably so: it flagged
any path outside the workdir, which called qwopus's *successful* navigation a
navigation failure. The paired positive/negative cases below exist to stop that
class of mistake returning -- a one-sided test is passed by a no-op classifier.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# Tree-habitat root: the operator instrument (azazael_operator_task.py)
# lives next to its tree -- C:\Users\clint for the hub tree, C:\Azazael
# for the flat tree -- and the nesting depth differs (home vs drive
# root), so no fixed parent-count works for both. Walk up to the first
# ancestor holding the instrument instead. Fixed 2026-10-04: a fixed
# grandparent resolved to C:\ for the flat tree and the suite could only
# pass on hub.
def _habitat():
    for cand in Path(__file__).resolve().parents:
        if (cand / "azazael_operator_task.py").exists():
            return cand
    return None

_HABITAT = _habitat()
if _HABITAT is not None and str(_HABITAT) not in sys.path:
    sys.path.insert(0, str(_HABITAT))

ot = importlib.import_module("azazael_operator_task")
ROOT = ot.AZAZAEL_ROOT

CORRECT = os.path.join(ROOT, "src", "CORE", "worm_registry.py")
# Doubled separators: this is how paths are actually stored in records.
CORRECT_ESCAPED = CORRECT.replace("\\", "\\\\")
DECOY = r"C:\Users\clint\azazael-dev\src\worm_registry.py"
# Any out-of-repo path works: the scoring must recognise a report path that is
# not the task's own target. It deliberately no longer lives inside the retired
# 3.14t venv, so archiving that venv cannot change this test's meaning.
OPS_OUT = str(Path.home() / "azazael-ops" / "lbl" / "report.txt")


def _tail(text: str, n: int = 8) -> list[dict]:
    """A message list whose final turns contain `text`."""
    return [{"role": "assistant", "content": text} for _ in range(n)]


# --- 1. reason -> class mapping -------------------------------------------------

@pytest.mark.parametrize("reason,expected", [
    ("completed", "none"),
    ("wall_clock_cap", "budget"),
    ("no_code_emitted", "budget"),
    ("report_written_but_incorrect", "capability"),
    ("ended_turn_without_code", "capability"),
    ("ended_before_report", "capability"),
])
def test_reason_maps_to_class(reason, expected):
    assert ot.classify_failure(reason, [], ROOT)[0] == expected


def test_navigation_beats_budget_when_clock_ran_out_while_lost():
    """Burning the clock while lost is a navigation loss. If the ordering is
    wrong it is reported as a budget loss and the task defect is never fixed."""
    msgs = _tail("Could not find azazael/src with importable worm_registry")
    assert ot.classify_failure("wall_clock_cap", msgs, ROOT)[0] == "navigation"


def test_wrong_report_is_capability_not_navigation():
    """qwythos completed the loop and got every number wrong (1982 vs 231).
    Completing and being correct are independent; this must not read as
    navigation just because it wrote files."""
    assert ot.classify_failure(
        "report_written_but_incorrect", _tail(OPS_OUT), ROOT)[0] == "capability"


# --- 2. the controls that the first, wrong classifier failed ---------------------

@pytest.mark.parametrize("label,text,expected", [
    ("correct registry path", CORRECT, "capability"),
    ("correct path, escaped", CORRECT_ESCAPED, "capability"),
    ("task's own output path", OPS_OUT, "capability"),
    ("decoy registry path", DECOY, "navigation"),
    ("decoy tree by name", r"searching C:\Users\clint\Desktop\atlas", "navigation"),
])
def test_path_signals_are_not_over_broad(label, text, expected):
    assert ot.classify_failure(
        "report_written_but_incorrect", _tail(text), ROOT)[0] == expected


def test_decoy_outside_tail_window_is_not_flagged():
    """Only the tail is inspected: an early miss the model repaired itself is not
    why the run ended. The decoy must genuinely scroll out of the window."""
    msgs = _tail(r"searching C:\Users\clint\azazael-dev", 30) + _tail("imported CORE")
    assert ot.classify_failure(
        "report_written_but_incorrect", msgs, ROOT)[0] == "capability"


def test_escaped_separators_still_match_the_root():
    """Records store Windows paths with doubled backslashes. The first normalizer
    did .replace('\\\\', '/'), turning c:\\\\\\\\users into c://users, so the CORRECT
    path stopped matching the root and was reported as a decoy."""
    assert ot._norm_path(CORRECT_ESCAPED) == ot._norm_path(CORRECT)
    assert ot._norm_path(CORRECT).startswith(ot._norm_path(ROOT) + "/")


# --- 3. the instrument must not be cwd-dependent --------------------------------

def test_paths_are_pinned_to_the_file_not_the_cwd():
    assert Path(ot.AZAZAEL_ROOT).is_absolute()
    assert Path(ot.OUTDIR_DEFAULT).is_absolute()
    assert Path(ot.OUTDIR_DEFAULT).is_dir()


def test_reasoning_overhead_resolves_for_a_real_local_alias():
    """The pad is what stops a thinking model's cap being spent before content."""
    overhead, provider = ot.provider_reasoning_overhead(
        "http://localhost:8083/v1", "qwopus3.5-9b-v3")
    assert provider == "llama_qwopus"
    assert overhead > 0


def test_reasoning_overhead_is_zero_for_an_unknown_endpoint():
    assert ot.provider_reasoning_overhead("http://example.invalid/v1", "nope") == (0, None)


# --- 4. prompts must name absolute, correct locations --------------------------

def test_prompts_name_the_pinned_root():
    for bridge in (False, True):
        prompt = ot.build_task(OPS_OUT, bridge=bridge)
        assert ot._norm_path(ROOT) in ot._norm_path(prompt), f"bridge={bridge}"
        assert "decoy" in prompt.lower(), f"bridge={bridge}"


def test_bridge_prompt_names_the_absolute_bridge_path():
    """The bridge lives next to this script, NOT in the narrowed workdir, so
    'in this directory' became false the moment cwd was pinned."""
    prompt = ot.build_task(OPS_OUT, bridge=True)
    assert "azazael_operator_bridge.py" in prompt
    assert ot._norm_path(ot.PROJECT_ROOT) in ot._norm_path(prompt)
    assert "in this directory" not in prompt


# --- 5. the summary instrument --------------------------------------------------

def _rec(label, **over):
    """A minimal operator record; overrides win."""
    base = {
        "label": label, "model": "test-model", "interface": "raw_api",
        "task": "worm_registry",
        "ground_truth": {"worms": 231, "top": [["security", 24]]},
        "verification": {"checks": {"worm_count_correct": True}, "all_ok": True},
        "messages": [{"role": "assistant", "content": "ok"}],
        "success": True, "termination": "completed", "failure_class": "none",
        "resident_count": 1, "code_blocks": 3,
    }
    base.update(over)
    return base


def test_summary_counts_by_cause_and_ignores_foreign_records(tmp_path):
    """The whole point of the summary is that a FAIL says WHY. If it collapses the
    classes, or counts a machine-info record as an operator result, it reports a
    number that means nothing."""
    import io
    from contextlib import redirect_stdout

    rows = [
        _rec("pass1"),
        _rec("nav1", success=False, termination="ended_before_report",
             failure_class="navigation"),
        _rec("budget1", success=False, termination="wall_clock_cap",
             failure_class="budget"),
        _rec("capab1", success=False, termination="report_written_but_incorrect",
             failure_class="capability"),
        # A DIFFERENT task from an earlier harness. Must not be scored here.
        _rec("foreign", task="system_info", ground_truth={"os": "Windows"}),
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(__import__("json").dumps(r) for r in rows) + "\n",
                 encoding="utf-8")

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ot.summarize(str(tmp_path))
    out = buf.getvalue()

    assert rc == 0
    assert "4 scored, 1 skipped" in out, out
    assert "nav" in out and "budget" in out and "capab" in out


def test_summary_classifies_records_missing_the_field(tmp_path):
    """Records written before failure_class existed must be classified on read.
    Counting an unlabelled FAIL as clean is the silent-blank failure."""
    import io
    import json
    from contextlib import redirect_stdout

    r = _rec("legacy", success=False, termination="wall_clock_cap")
    del r["failure_class"]
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(r) + "\n", encoding="utf-8")

    buf = io.StringIO()
    with redirect_stdout(buf):
        ot.summarize(str(tmp_path))
    assert "budget" in buf.getvalue()


def test_summary_flags_the_gpu_confound(tmp_path):
    """Three resident models cost qwopus two thirds of its turns. A run scored with
    a shared GPU must be reported as confounded, not averaged in."""
    import io
    import json
    from contextlib import redirect_stdout

    rows = [_rec("shared", success=False, termination="wall_clock_cap",
                 failure_class="budget", resident_count=3),
            _rec("alone", success=True, resident_count=1)]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    buf = io.StringIO()
    with redirect_stdout(buf):
        ot.summarize(str(tmp_path))
    out = buf.getvalue()
    assert "more than one model resident" in out, out


def test_bare_directory_name_is_not_a_decoy_path():
    """A live run printed the bare line `graphify-out` from `ls`, and the first
    marker rule ('graphify' as a bare word) recorded that as a navigation loss --
    while azazael/graphify-out is a real directory INSIDE the workdir. Only a
    drive-qualified path segment may count."""
    bare = r"graphify-out"
    assert ot.classify_failure(
        "wall_clock_cap", _tail(bare), ROOT)[0] == "budget"
    # A directory listing that mentions it must also stay clean.
    assert ot.classify_failure(
        "wall_clock_cap", _tail("src\nCORE\ngraphify-out\ndocs"), ROOT)[0] == "budget"


@pytest.mark.parametrize("path", [
    r"C:/Users/clint/Desktop/Azazael/src/worm_registry.py",
    r"C:\Users\clint\azazael-dev\src\CORE\worm_registry.py",
    r"C:\Users\clint\Desktop\AZAZAEL_Standalone\x.py",
])
def test_real_decoy_trees_are_still_flagged(path):
    """Strictness must not become blindness: the paths that actually caused the
    original failure have to keep flagging."""
    assert ot.classify_failure(
        "wall_clock_cap", _tail(path), ROOT)[0] == "navigation"


@pytest.mark.parametrize("results, wins, verdict", [
    ([True, True, True], 3, True),
    ([True, True, False], 2, True),
    ([True, False, False], 1, False),
    ([False, False, False], 0, False),
    ([True, True], 2, True),
    ([True, False], 1, False),  # ties are not proven
    ([True], 1, True),
    ([], 0, False),  # no evidence defaults to False, never True
])
def test_majority_verdict_counts(results, wins, verdict):
    out = ot.majority_verdict([{"success": b} for b in results])
    assert out["n"] == len(results)
    assert out["wins"] == wins
    assert out["verdict"] is verdict


def test_majority_verdict_unanimity():
    assert ot.majority_verdict([True, True])["unanimous"] is True
    assert ot.majority_verdict([False, False])["unanimous"] is True
    assert ot.majority_verdict([True, False])["unanimous"] is False
    assert ot.majority_verdict([])["unanimous"] is False


def test_majority_accepts_bare_bools():
    assert ot.majority_verdict([True, False, True])["verdict"] is True
