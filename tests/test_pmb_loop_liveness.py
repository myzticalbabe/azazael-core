# coding: utf-8
"""Regression gate: the PMB outcome loop must stay WIRED.

This is the gate for the defect class found on 2026-10-03: PMB's Earned-Memory
loop read a table (`agent_actions`) that nothing on this host ever wrote, and
reported a clean `n_outcome_turns: 0` forever. A metric that never moves is
invisible to ordinary tests, so this file asserts the WIRING itself -- the three
things whose silent removal re-breaks the loop:

  1. the observer exists and maps a test outcome to a status `classify_actions`
     actually counts (ok / non-zero),
  2. `conftest.py` records the outcome at `pytest_sessionfinish`,
  3. `.githooks/pre-commit` records the commit outcome.

If any of these is deleted, the loop goes quiet again and this test fails loudly.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OBSERVER = ROOT / "tools" / "pmb_session_observe.py"
CONFTEST = ROOT / "conftest.py"
HOOK = ROOT / ".githooks" / "pre-commit"

#: Statuses `pmb.hooks.autowrite._status_ok` / `_status_failed` accept, so a
#: recorded row is classified as a real test outcome rather than ignored.
_OK_STATUSES = {"ok", "0", "pass", "passed", "success", "done"}


def _load_observer():
    spec = importlib.util.spec_from_file_location("pmb_session_observe_live", OBSERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_observer_file_exists():
    assert OBSERVER.exists(), "the outcome observer was deleted -- the loop is dead again"


def test_conftest_wires_sessionfinish():
    text = CONFTEST.read_text(encoding="utf-8", errors="replace")
    assert "pytest_sessionfinish" in text, "conftest no longer hooks pytest_sessionfinish"
    assert "pmb_session_observe" in text, "conftest no longer calls the outcome observer"


def test_precommit_wires_the_observer():
    text = HOOK.read_text(encoding="utf-8", errors="replace")
    assert "pmb_session_observe" in text, "pre-commit no longer records the commit outcome"
    assert "--commit-ok" in text


def test_pytest_outcome_status_is_one_classify_actions_counts(monkeypatch):
    """A recorded test run must produce a status the classifier treats as an
    outcome, or the row exists but the turn still reads as outcome-less."""
    obs = _load_observer()
    seen = {}
    monkeypatch.setattr(obs, "_record",
                        lambda t, tg, s, session_id=None: seen.update(status=s) or 1)

    obs.observe_pytest(0)
    assert seen["status"].lower() in _OK_STATUSES

    obs.observe_pytest(3)
    s = seen["status"]
    assert s.isdigit() and s != "0", "non-zero exit must be a non-zero numeric status"


def test_observer_maps_only_understood_shapes():
    """Guard against a future edit that records a status the classifier ignores
    (e.g. 'failed' spelled 'FAIL'), which would silently re-empty the loop."""
    text = OBSERVER.read_text(encoding="utf-8", errors="replace")
    # The two mapping sites must produce "ok" / str(exit).
    assert re.search(r'status\s*=\s*"ok"\s+if\s+int\(exitstatus\)\s*==\s*0', text), (
        "pytest exit -> status mapping changed; re-check it against "
        "autowrite._status_ok/_status_failed")
