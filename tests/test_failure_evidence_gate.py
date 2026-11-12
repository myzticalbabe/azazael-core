# coding: utf-8
"""The failure-evidence gate must be able to FAIL (2026-10-14).

`tools/failure_evidence_gate.py` first shipped with a defect that made it
useless and invisible at the same time: it CORRECTLY reported the one offending
site that existed and still exited 0, because `--check` printed "cannot fail"
whenever no baseline was recorded -- and the baseline lives in gitignored
`logs/`, so a fresh clone always had none. A detector whose zero cannot be told
apart from a broken scan is the same class of instrument this lane exists to
remove, so the contract is pinned here rather than merely described:

  * a real site FAILS the run -- the zero has to be load-bearing;
  * an unarmed `--check` is rc 2, never 0 (absence of evidence is not a pass);
  * a `--dir` that does not exist is rc 2, because an empty scan reads exactly
    like a clean tree;
  * a bare exemption marker does NOT silence a finding, while one carrying a
    reason becomes a REPORTED exemption;
  * the known false-positive class is pinned in both directions -- the scan is
    AST-based, so a multi-line call is judged by its keywords, not its lines.

Each test drives the real `main()` over a real temp tree.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import failure_evidence_gate as gate  # noqa: E402

#: Two findings at once: a blocking call with no bound, and text mode with no
#: encoding named. Both are statically visible and both must be counted.
DEFECT = ('import subprocess\n\n\ndef f():\n'
          '    return subprocess.run(["echo"], capture_output=True, text=True)\n')


def _run(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["failure_evidence_gate"] + argv)
    rc = gate.main()
    return rc, capsys.readouterr().out


def test_an_unbounded_wait_fails_the_run(tmp_path, monkeypatch, capsys):
    (tmp_path / "bad.py").write_text(DEFECT, encoding="utf-8")
    rc, out = _run(monkeypatch, capsys, ["--dir", str(tmp_path)])
    assert rc == 1, "a detected site must fail the run, not merely be printed"
    assert "no_timeout=1" in out and "no_encoding=1" in out, out


def test_bare_marker_does_not_silence_a_finding(tmp_path, monkeypatch, capsys):
    (tmp_path / "bad.py").write_text(
        'import subprocess\n\n\ndef g():\n'
        '    return subprocess.run(["echo"])  # gate:allow-no-timeout:\n',
        encoding="utf-8")
    rc, out = _run(monkeypatch, capsys, ["--dir", str(tmp_path)])
    assert rc == 1, "a marker with no reason must not hide a real finding"
    assert "no_timeout=1" in out, out


def test_marker_with_a_reason_is_a_reported_exemption(tmp_path, monkeypatch, capsys):
    (tmp_path / "ok.py").write_text(
        'import subprocess\n\n\ndef g():\n'
        '    return subprocess.run(["echo"])'
        '  # gate:allow-no-timeout: deliberate, see the design note\n',
        encoding="utf-8")
    rc, out = _run(monkeypatch, capsys, ["--dir", str(tmp_path)])
    assert rc == 0
    assert "EXEMPT" in out and "deliberate" in out, \
        "an exemption must be listed, or it cannot be told from a blind spot"


def test_unarmed_check_is_not_a_silent_pass(tmp_path, monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys,
                   ["--check", "--baseline-file", str(tmp_path / "absent.json"),
                    "--dir", str(tmp_path)])
    assert rc == 2, "a gate that cannot fail must not report success"
    assert "NO BASELINE" in out, out


def test_check_blocks_a_file_absent_from_the_baseline(tmp_path, monkeypatch, capsys):
    base = tmp_path / "base.json"
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "good.py").write_text(
        'import subprocess\n\n\ndef g():\n'
        '    return subprocess.run(["echo"], timeout=5)\n', encoding="utf-8")
    rc, _ = _run(monkeypatch, capsys,
                 ["--baseline", "--baseline-file", str(base), "--dir", str(tree)])
    assert rc == 0
    rc, _ = _run(monkeypatch, capsys,
                 ["--check", "--baseline-file", str(base), "--dir", str(tree)])
    assert rc == 0, "an unchanged tree must not regress"
    (tree / "new.py").write_text(DEFECT, encoding="utf-8")
    rc, out = _run(monkeypatch, capsys,
                   ["--check", "--baseline-file", str(base), "--dir", str(tree)])
    assert rc == 1, "a new offending file is a regression"
    assert "NEW file" in out, out


def test_a_missing_dir_is_refused_not_reported_as_clean(tmp_path, monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys, ["--dir", str(tmp_path / "nope")])
    assert rc == 2, "an empty scan reads exactly like a clean tree"
    assert "do not exist" in out, out


def test_multiline_calls_are_judged_by_keywords_not_lines(tmp_path, monkeypatch, capsys):
    """The repo has been bitten by a line-based check on a multi-line call."""
    multi = tmp_path / "multi.py"
    multi.write_text(
        'import subprocess\n\n\ndef bounded():\n'
        '    return subprocess.run(\n'
        '        ["echo"],\n'
        '        capture_output=True,\n'
        '        text=True,\n'
        '        encoding="utf-8",\n'
        '        errors="replace",\n'
        '        timeout=30,\n'
        '    )\n', encoding="utf-8")
    rc, out = _run(monkeypatch, capsys, ["--dir", str(tmp_path)])
    assert rc == 0, "a bound on a later line is still a bound: %s" % out
    multi.write_text(
        'import subprocess\n\n\ndef unbounded():\n'
        '    return subprocess.run(\n'
        '        ["echo"],\n'
        '        capture_output=True,\n'
        '    )\n', encoding="utf-8")
    rc, out = _run(monkeypatch, capsys, ["--dir", str(tmp_path)])
    assert rc == 1, "a multi-line call with no bound must still be caught: %s" % out


def test_the_live_tree_holds_the_zero_invariant(monkeypatch, capsys):
    """The rollout drove this to zero; a new site reopens it."""
    rc, out = _run(monkeypatch, capsys, [])
    assert rc == 0, out
    assert "findings: 0 site(s)" in out, out
