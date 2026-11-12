# coding: utf-8
"""TERENCE CLI contract tests.

Guards the two honesty defects fixed 2026-10-03:

  * ``preview()`` printed the raw pattern+fix for EVERY finding, including
    rules in ``UNSAFE_RULES`` that ``fix_all(apply=True)`` filters out before
    the loop. The ``Use tuple for isinstance`` rule therefore displayed
    ``isinstance(v, str)`` -> ``isinstance(x, (str,))`` - a variable RENAME (a
    NameError if ever applied) for a substitution that is a pure runtime no-op.
    A reader sized the run from the preview and saw 475 "fixes" that were not
    fixes; the real apply queue was 31. The preview must describe what WOULD
    be applied.

  * ``--status`` returned all zeros forever: stats live on the instance, were
    never persisted, and the CLI builds a fresh instance per invocation. Status
    must describe the last run.
"""
import importlib.util
import json
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_terence():
    """TERENCE lives outside the importable package root."""
    path = os.path.join(_PROJECT_ROOT, "src", "UTILITIES", "TERENCE",
                        "TERENCE.py")
    spec = importlib.util.spec_from_file_location("terence_cli_contract", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fix(mod, error_type, original, fixed):
    return mod.Fix(file="a.py", line=1, error_type=error_type,
                   original=original, fixed=fixed, description=error_type)


# ── preview(): describes the apply queue, never the advisory spans ─────────

def test_preview_shows_only_the_apply_queue(capsys):
    mod = _load_terence()
    t = mod.TERENCE()
    t.fixes = [
        _fix(mod, "Simplify list(d.keys())", "list(d.keys())", "list(d)"),
        _fix(mod, "Use tuple for isinstance",
             "isinstance(v, str)", "isinstance(x, (str,))"),
    ]

    count = t.preview()
    out = capsys.readouterr().out

    assert count == 1, "advisory findings must not count as applyable"
    assert "list(d.keys())" in out and "list(d)" in out
    # The misleading advisory span must NOT be presented as a fix.
    assert "isinstance(x, (str,))" not in out
    assert "isinstance(v, str)" not in out
    assert "WITHHELD" in out
    # Still reported - as a withheld category, not as a queued edit.
    assert "Use tuple for isinstance" in out


def test_preview_can_reveal_withheld_spans_on_request(capsys):
    mod = _load_terence()
    t = mod.TERENCE()
    t.fixes = [_fix(mod, "Use tuple for isinstance",
                    "isinstance(v, str)", "isinstance(x, (str,))")]

    t.preview(show_withheld=1)
    out = capsys.readouterr().out

    assert "audit only" in out
    assert "isinstance(x, (str,))" in out


# ── status(): reports the last run, not a fresh set of zeros ────────────────

def test_status_reports_persisted_last_run(tmp_path, monkeypatch):
    mod = _load_terence()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "terence_last_run.json").write_text(json.dumps({
        "version": "1.0.0",
        "errors_found": 7988,
        "apply_queue": 0,
        "advisory_withheld": 7988,
        "when": "2026-10-03T00:00:00",
    }), encoding="utf-8")
    monkeypatch.setenv("AZAZAEL_ROOT", str(tmp_path))

    st = mod.TERENCE().status()

    assert st["source"] == "last-run"
    assert st["errors_found"] == 7988


def test_status_prefers_this_instance_when_it_ran(monkeypatch):
    mod = _load_terence()
    monkeypatch.delenv("AZAZAEL_ROOT", raising=False)
    t = mod.TERENCE()
    t.stats.errors_found = 7

    st = t.status()

    assert st["source"] == "this-instance"
    assert st["errors_found"] == 7


# ── scan(): files_scanned is actually accumulated ──────────────────────────

def test_scan_accumulates_files_scanned(tmp_path, monkeypatch, capsys):
    mod = _load_terence()
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")
    (src / "b.py").write_text("y = 2\n", encoding="utf-8")
    monkeypatch.setenv("AZAZAEL_ROOT", str(tmp_path))

    stats = mod.TERENCE().scan()
    capsys.readouterr()  # swallow the scan report

    assert stats.files_scanned == 2
