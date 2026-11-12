# coding: utf-8
"""Tests for `tools/type_check_gate.py` — the basedpyright baseline gate.

Why these tests exist
---------------------
The gate replaced a mypy step that could not run on this tree, and the failure
mode of a quality gate is never "it crashes" -- it is "it reports OK". So the
tests pin the ways this one could reassure while wrong:

* a checker that could not run must not read as a clean tree,
* output that is not JSON must not read as a clean tree,
* a missing baseline must not read as "nothing is wrong",
* rewriting a diagnostic's MESSAGE must not read as a fix,
* and the committed baseline must actually agree with the committed report.

The runner is injected rather than invoked: a real run is ~1 minute and would
make this file's result depend on the tree it is measuring.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import type_check_gate as gate  # noqa: E402


def _diag(line, char, rule, message, severity="error"):
    return {"file": "src/CORE/x.py", "severity": severity,
            "rule": rule, "message": message,
            "range": {"start": {"line": line, "character": char},
                      "end": {"line": line, "character": char + 1}}}


def _diag_at(path, line, char, rule, severity="error"):
    """A diagnostic on a REAL file, for the tests about identity anchoring."""
    return {"file": str(path), "severity": severity, "rule": rule,
            "message": "m",
            "range": {"start": {"line": line, "character": char},
                      "end": {"line": line, "character": char + 1}}}


PAYLOAD = {
    "summary": {"filesAnalyzed": 690, "errorCount": 2, "warningCount": 1,
                "informationCount": 0, "timeInSec": 51.6},
    "generalDiagnostics": [
        _diag(10, 4, "reportUndefinedVariable", "'foo' is not defined"),
        _diag(20, 0, "reportArgumentType", 'Argument of type "int" ...'),
        _diag(30, 8, "reportOptionalMemberAccess", '"_x" is not a known '
              'attribute', severity="warning"),
    ],
}


def test_identity_ignores_the_message_so_rewording_is_not_a_fix():
    """A reworded message is the same diagnostic, not a repaired one."""
    reworded = json.loads(json.dumps(PAYLOAD))
    reworded["generalDiagnostics"][0]["message"] = "'foo' is not defined.  Did you mean 'food'?"
    assert gate.diagnostic_ids(PAYLOAD) == gate.diagnostic_ids(reworded)


def test_identity_separates_rule_and_position():
    ids = gate.diagnostic_ids(PAYLOAD)
    assert len(ids) == 3
    # `src/CORE/x.py` has no source line at position 11, so the anchor falls back
    # to an explicitly-marked position rather than a bare number: a silent
    # fallback would be the instability this scheme exists to remove, for every
    # file the gate cannot read.
    assert "src/CORE/x.py:reportUndefinedVariable:@pos:11:5" in ids
    # Same position, different rule -> a different diagnostic.
    other = {"generalDiagnostics": [_diag(10, 4, "reportAssignmentType", "m")]}
    assert gate.diagnostic_ids(other) != {i for i in ids if "@pos:11:5" in i}


def test_identity_survives_lines_inserted_above(tmp_path, monkeypatch):
    """The defect Error #203 measured: a positional id is invalidated by any edit.

    Six diagnostics read as "15 new and 6 gone" after a session that introduced
    no type defect, because inserting lines moves them. Anchoring on the source
    line means the same finding reports the same id -- and lines inserted ABOVE a
    finding do not change the text of the line it points at.
    """
    monkeypatch.setattr(gate, "REPO", tmp_path)
    gate._LINE_CACHE.clear()
    probe = tmp_path / "probe.py"
    probe.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")

    # `y = 2` is source line 2, i.e. 0-based index 1.
    before = gate.diagnostic_ids({"generalDiagnostics": [
        _diag_at(probe, 1, 0, "reportAssignmentType")]})
    assert before == {"probe.py:reportAssignmentType:y = 2"}, before

    # A blank line is inserted at the top. Nothing about the finding changed; its
    # REPORTED position moved by one.
    probe.write_text("\nx = 1\ny = 2\nz = 3\n", encoding="utf-8")
    gate._LINE_CACHE.clear()
    after = gate.diagnostic_ids({"generalDiagnostics": [
        _diag_at(probe, 2, 0, "reportAssignmentType")]})
    assert after == before, (
        "inserting a blank line above a diagnostic changed its id, so the gate "
        "would report it as a regression: %r vs %r" % (after, before))

    # ...and the anchor still tracks a real change to the line it names, so a
    # finding cannot be renamed out of the baseline.
    probe.write_text("\nx = 1\ny = 3\nz = 3\n", encoding="utf-8")
    gate._LINE_CACHE.clear()
    changed = gate.diagnostic_ids({"generalDiagnostics": [
        _diag_at(probe, 2, 0, "reportAssignmentType")]})
    assert changed != before, "a rewritten line kept the old id"


def test_two_findings_of_one_rule_on_identical_text_stay_two(tmp_path, monkeypatch):
    """No silent collapsing: identical text is disambiguated by an ordinal."""
    monkeypatch.setattr(gate, "REPO", tmp_path)
    gate._LINE_CACHE.clear()
    probe = tmp_path / "same.py"
    probe.write_text("return None\nreturn None\n", encoding="utf-8")
    ids = gate.diagnostic_ids({"generalDiagnostics": [
        _diag_at(probe, 0, 0, "reportReturnType"),
        _diag_at(probe, 1, 0, "reportReturnType")]})
    assert ids == {"same.py:reportReturnType:return None",
                   "same.py:reportReturnType:return None#2"}, ids


def test_the_baseline_records_its_identity_scheme():
    """A scheme change must be refusable, not reported as a thousand regressions."""
    recorded = gate.baseline_payload(PAYLOAD)["identity_scheme"]
    assert recorded == gate.IDENTITY_SCHEME
    assert recorded != "positional-v0", (
        "the scheme must not read as the positional one, or an old baseline "
        "would be diffed against the new ids without a word")


def test_severity_counts_and_top_rules_are_reported():
    assert gate.severity_counts(PAYLOAD) == {"error": 2, "warning": 1,
                                             "information": 0}
    assert gate.rule_histogram(PAYLOAD) == {
        "reportUndefinedVariable": 1, "reportArgumentType": 1,
        "reportOptionalMemberAccess": 1}


def test_diff_splits_new_from_fixed():
    delta = gate.diff_against_baseline(
        ["a:1:0:r1", "a:2:0:r2"], ["a:2:0:r2", "a:3:0:r3"])
    assert delta["new"] == ["a:1:0:r1"]
    assert delta["fixed"] == ["a:3:0:r3"]
    assert delta["unchanged"] == ["a:2:0:r2"]


def test_a_new_diagnostic_blocks_and_a_known_one_does_not(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "base.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "rep.json")
    monkeypatch.setattr(gate, "run_checker",
                        lambda *a, **k: (1, json.dumps(PAYLOAD)))

    assert gate.main(["--baseline"]) == 0
    assert gate.main(["--check"]) == 0
    assert "OK: no new diagnostics" in capsys.readouterr().out

    grown = json.loads(json.dumps(PAYLOAD))
    grown["generalDiagnostics"].append(
        _diag(99, 0, "reportUndefinedVariable", "'bar' is not defined"))
    monkeypatch.setattr(gate, "run_checker",
                        lambda *a, **k: (1, json.dumps(grown)))
    assert gate.main(["--check"]) == 1
    assert ("NEW DIAGNOSTIC: src/CORE/x.py:reportUndefinedVariable:@pos:100:1"
            in capsys.readouterr().out)


def test_a_missing_checker_is_a_failure_not_a_clean_tree(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "base.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "rep.json")
    monkeypatch.setattr(gate, "run_checker", lambda *a, **k: (125, ""))
    assert gate.main(["--check"]) == 1
    assert "NO CHECKER" in capsys.readouterr().err


def test_non_json_output_is_a_failure_not_a_clean_tree(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "base.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "rep.json")
    monkeypatch.setattr(gate, "run_checker",
                        lambda *a, **k: (1, "Traceback (most recent call last)"))
    assert gate.main(["--check"]) == 1
    assert "no JSON" in capsys.readouterr().err


def test_no_baseline_refuses_to_reassure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "absent.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "rep.json")
    monkeypatch.setattr(gate, "run_checker",
                        lambda *a, **k: (0, json.dumps(PAYLOAD)))
    assert gate.main(["--check"]) == 1
    assert "no baseline" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# ADR-012: the path follows the scope it measured
# ---------------------------------------------------------------------------
# Measured 2026-09-22 on the sibling aggregate gate: a one-file smoke test wrote
# `1 passed in 1s` over a 301-test profile run, under the filename that still
# claimed to describe the profile (Error #195). This gate has the same shape --
# `--only` narrows the check, the paths stayed pinned -- so `--baseline --only
# src/CORE/` would have replaced the whole-tree baseline with one subtree's
# diagnostics and called every other diagnostic in the tree a regression.

def test_the_configured_scope_is_read_rather_than_assumed():
    assert gate.configured_targets() == ["src"], (
        "pyrightconfig.json's include is the default scope; if it changed, the "
        "narrowing rule below is measuring against a stale set")


def test_a_narrowed_baseline_does_not_replace_the_whole_tree_baseline(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "basedpyright_baseline.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "basedpyright_report.json")
    monkeypatch.setattr(gate, "run_checker",
                        lambda *a, **k: (1, json.dumps(PAYLOAD)))

    assert gate.main(["--baseline", "--only", "src/CORE/x.py"]) == 0
    assert (tmp_path / "basedpyright_baseline.partial.json").exists()
    assert not (tmp_path / "basedpyright_baseline.json").exists()
    assert not (tmp_path / "basedpyright_report.json").exists()
    assert "a narrowed run is not" in capsys.readouterr().out


def test_a_narrow_check_refuses_instead_of_borrowing_a_baseline(
        tmp_path, monkeypatch, capsys):
    """A subtree compared against the whole tree's baseline is not a gate."""
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "basedpyright_baseline.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "basedpyright_report.json")
    monkeypatch.setattr(gate, "run_checker",
                        lambda *a, **k: (0, json.dumps(PAYLOAD)))
    assert gate.main(["--check", "--only", "src/CORE/x.py"]) == 1
    assert "no baseline" in capsys.readouterr().err


def test_the_configured_scope_itself_is_not_a_narrowed_run(
        tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "basedpyright_baseline.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "basedpyright_report.json")
    monkeypatch.setattr(gate, "run_checker",
                        lambda *a, **k: (1, json.dumps(PAYLOAD)))
    assert gate.main(["--baseline", "--only"] + gate.configured_targets()) == 0
    assert (tmp_path / "basedpyright_baseline.json").exists()


def test_an_unreadable_config_counts_as_narrowed(tmp_path, monkeypatch, capsys):
    """No declared scope must not mean "anything goes".

    With nothing to compare against, an explicit selection is treated as
    narrowed: the safe direction is to write a `.partial` file, not to replace
    the recorded baseline of a scope nobody can name. (`evidence_paths` returns
    False for an empty expected set -- the subset test is vacuously true -- so
    the fail-closed branch has to live in the gate, and this is the only test
    that would notice if it were dropped.)
    """
    monkeypatch.setattr(gate, "configured_targets", lambda: [])
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "basedpyright_baseline.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "basedpyright_report.json")
    monkeypatch.setattr(gate, "run_checker",
                        lambda *a, **k: (1, json.dumps(PAYLOAD)))
    assert gate.main(["--baseline", "--only", "src/CORE"]) == 0
    assert (tmp_path / "basedpyright_baseline.partial.json").exists()
    assert "a narrowed run is not" in capsys.readouterr().out


def test_the_committed_baseline_agrees_with_the_committed_report():
    """A baseline that drifted from the run it claims to describe is fiction.

    Both files are committed, so this is the check that a stale one cannot
    survive review.
    """
    if not gate.BASELINE.exists():
        pytest.skip("no committed baseline")
    if not gate.REPORT.exists():
        pytest.skip("no committed report")
    base = json.loads(gate.BASELINE.read_text(encoding="utf-8"))
    report = json.loads(gate.REPORT.read_text(encoding="utf-8"))
    # Both files are written from the same payload, so every field they share
    # must agree. Compared field-to-field rather than by re-deriving from raw
    # diagnostics: the report is condensed deliberately, so that a 1.5 MB JSON
    # of prose is not what a reviewer has to diff.
    assert base["severity_counts"] == report["severity_counts"]
    assert base["summary"] == report["summary"]
    assert base["top_rules"] == report["top_rules"]
    assert set(base["diagnostic_ids"]) == set(report["diagnostic_ids"])
    assert base["diagnostic_ids"], "a baseline with no diagnostics proves nothing"
