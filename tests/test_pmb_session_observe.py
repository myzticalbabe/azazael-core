# coding: utf-8
"""Tests for tools/pmb_session_observe.py -- the hostless-agent outcome observer.

The observer itself needs the `.pmb-venv` interpreter (the one with `pmb`), so
these tests cover the parts that are pure and would silently regress:

  * the pytest exit -> status mapping (`classify_actions` only counts a test run
    when status is "ok"/"0"/pass... or a non-zero number, so the mapping is the
    contract),
  * transcript extraction of (command, status) pairs,
  * that a recording failure never raises out of main() (a memory side-effect
    must never break a caller).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_OBSERVER = Path(__file__).resolve().parent.parent / "tools" / "pmb_session_observe.py"


def _load():
    spec = importlib.util.spec_from_file_location("pmb_session_observe", _OBSERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def observer():
    return _load()


def test_pytest_exit_zero_maps_to_ok(observer, monkeypatch):
    seen = {}

    def fake_record(tool, target, status, session_id=None):
        seen.update(tool=tool, target=target, status=status)
        return 1

    monkeypatch.setattr(observer, "_record", fake_record)
    observer.observe_pytest(0)
    assert seen == {"tool": "Bash", "target": "pytest", "status": "ok"}


def test_pytest_nonzero_maps_to_numeric_status(observer, monkeypatch):
    seen = {}
    monkeypatch.setattr(observer, "_record",
                        lambda t, tg, s, session_id=None: seen.update(status=s) or 1)
    observer.observe_pytest(1)
    assert seen["status"] == "1"  # non-zero string -> _status_failed() is True


def test_commit_failure_maps_to_nonzero(observer, monkeypatch):
    seen = {}
    monkeypatch.setattr(observer, "_record",
                        lambda t, tg, s, session_id=None: seen.update(status=s) or 1)
    observer.observe_commit(False)
    assert seen["status"] == "1"


def test_transcript_extraction_finds_commands_and_exit_codes(observer, tmp_path):
    transcript = {
        "events": [
            {"tool": "bash", "command": "pytest -q", "exit_code": 0},
            {"tool": "bash", "command": "git commit -m x", "result": {"exit_code": 1}},
            {"tool": "read", "path": "foo.py"},  # no command -> ignored
        ]
    }
    p = tmp_path / "t.json"
    p.write_text(json.dumps(transcript), encoding="utf-8")
    got = observer._parse_transcript(str(p))
    assert ("pytest -q", "ok") in got
    assert ("git commit -m x", "1") in got
    assert all("foo.py" not in c for c, _ in got)


def test_transcript_missing_file_is_empty(observer):
    assert observer._parse_transcript("does-not-exist.json") == []


def test_transcript_malformed_json_is_empty(observer, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert observer._parse_transcript(str(p)) == []


def test_main_never_raises_when_recording_fails(observer, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(observer, "observe_pytest", boom)
    # Must return 0 (a memory side-effect may never break the caller).
    assert observer.main(["--pytest-exit", "0", "--quiet"]) == 0


def test_main_reports_recorded_count(observer, monkeypatch, capsys):
    monkeypatch.setattr(observer, "observe_pytest", lambda *a, **k: 1)
    monkeypatch.setattr(observer, "observe_commit", lambda *a, **k: 2)
    rc = observer.main(["--pytest-exit", "0", "--commit-ok"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["recorded"] == 2


def test_observe_command_rejects_trivial(observer, monkeypatch):
    """A non-significant command (e.g. `ls`) must not create a row."""
    monkeypatch.setattr(observer, "_record",
                        lambda *a, **k: pytest.fail("should not record"))
    # `observe_command` imports is_significant_action from pmb; when pmb is
    # absent this raises inside main() and is swallowed. Call it directly only
    # if pmb is importable.
    pytest.importorskip("pmb")
    assert observer.observe_command("ls -la") is None
