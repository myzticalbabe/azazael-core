"""Regression contracts for the original terminal monitor control banners."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_azazael_control_line_distinguishes_stale_from_unknown() -> None:
    monitor = _load("terminal_live_monitor", "scripts/live_monitor.py")
    stale = monitor._control_line({"freshness": [{"ok": False}, {"ok": True}]})
    unknown = monitor._control_line({"freshness": []})
    assert "DEGRADED" in stale
    assert "loops 1/2 current" in stale
    assert "next=refresh scheduler/producers" in stale
    assert "UNKNOWN" in unknown
    assert "loop evidence unknown" in unknown


def test_aoa_control_line_reports_degraded_contract() -> None:
    monitor = _load("terminal_aoa_monitor", "src/AOA/aoa_monitor.py")
    state = object.__new__(monitor.AOAState)
    state.init_results = {"a": True, "b": False}
    state.workflow = {"ran": True, "completed": 8, "total": 9}
    state.routing = {"contract": {"violations": 2}}
    line = monitor._control_line(state, {"ms_newest_ts": None})
    assert "DEGRADED" in line
    assert "arms 1/2" in line
    assert "workflow 8/9" in line
    assert "provenance violations 2" in line
    assert "repair routing contract" in line


def test_exmachina_control_line_fails_closed_on_stale_and_unknown() -> None:
    monitor = _load("terminal_exmachina_monitor", "scripts/exmachina_live_monitor.py")
    stale = monitor._control_line({"ages": {"oracle": 7200}, "error": None})
    unknown = monitor._control_line({"ages": {}, "error": None})
    assert "STALE" in stale
    assert "1/1 artifacts older than 1h" in stale
    assert "next=refresh evidence producers" in stale
    assert "UNKNOWN" in unknown
    assert "artifact evidence unknown" in unknown
