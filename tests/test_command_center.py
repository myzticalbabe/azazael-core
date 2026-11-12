"""Contracts for the unified one-screen live command center."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "tools", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import command_center as cc  # noqa: E402


def _panel(key: str, status: str) -> dict:
    return {
        "key": key,
        "title": key.upper(),
        "status": status,
        "headline": f"{key} headline",
        "metrics": [{"label": "Signal", "value": "1", "detail": "live"}],
        "evidence": [f"{key} evidence"],
        "action": f"repair {key}",
        "raw": {"ok": status == "ok"},
    }


def test_snapshot_rollup_has_all_causal_panels(monkeypatch):
    """Five panels since the algorithm tier landed (2026-09-24).

    Every PANEL_ORDER collector must get a fake status here, or the rollup
    counts a REAL collector's live status -- which is how this test asserted
    "four" panels against a five-panel order and read ok:2 instead of ok:1
    (fixed 2026-09-27). The set-equality guard keeps it from drifting again.
    """
    statuses = {
        "azazael": "critical",
        "aoa": "warn",
        "exmachina": "critical",
        "machine": "ok",
        "algorithms": "warn",
    }
    assert set(statuses) == set(cc.PANEL_ORDER), \
        "every PANEL_ORDER collector needs a fake status in this test"
    for key, status in statuses.items():
        monkeypatch.setattr(cc, f"collect_{key}", lambda key=key: _panel(key, statuses[key]))
    data = cc.collect_snapshot()
    assert tuple(data["panels"]) == cc.PANEL_ORDER
    assert data["overall"] == "critical"
    assert data["counts"] == {"unknown": 0, "ok": 1, "warn": 2, "critical": 2}
    assert len(data["actions"]) == 4
    assert data["errors"] == []


def test_aoa_contract_count_is_numeric(monkeypatch):
    payload = {
        "init": {"azazael": True},
        "startup_elapsed": 1.0,
        "workflow": {"completed": 1, "total": 1, "passed": True},
        "routing": {"caps": 2, "pairs": 3, "route_status": "ok", "route_arm": "azazael",
                    "learned": 1, "contract": {"violations": 2, "by_kind": {"stale_after_s_missing": 2}}},
        "files": {"sessions_n": 1, "ms_newest_ts": 0},
    }
    result = type("Result", (), {
        "returncode": 0,
        "stdout": json.dumps(payload) + "\n",
        "stderr": "",
    })()
    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: result)
    panel = cc.collect_aoa()
    assert panel["status"] == "warn"
    assert any("Contract: 2 violation(s)" in row for row in panel["evidence"])


def test_page_is_self_contained_and_has_no_duplicate_freshness_panel():
    data = {
        "schema": "azazael.command_center.v1",
        "generated_at": "2026-09-24T00:00:00+00:00",
        "overall": "critical",
        "headline": "evidence is stale",
        "counts": {"unknown": 0, "ok": 0, "warn": 0, "critical": 4},
        "panels": {key: _panel(key, "critical") for key in cc.PANEL_ORDER},
        "actions": ["restore evidence"],
        "errors": [],
    }
    page = cc.render_page(30, data)
    assert page.count('id="fresh"') == 0
    assert "raw evidence" in page
    assert "grid-template-columns:1fr 1fr" in page
    assert "AZAZAEL <span>COMMAND CENTER</span>" in page
    assert "https://" not in page
    assert '"overall":"critical"' in page


def test_web_cache_reuses_one_snapshot(monkeypatch):
    path = ROOT / "scripts" / "live_monitor_web.py"
    spec = importlib.util.spec_from_file_location("command_center_web_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    calls = []

    def fake_collect():
        calls.append(1)
        return {"schema": "test", "generated_at": "now", "overall": "ok",
                "headline": "ok", "counts": {}, "panels": {}, "actions": [], "errors": []}

    monkeypatch.setattr(module, "collect_snapshot", fake_collect)
    module._CACHE.update({"at": 0.0, "data": None})
    first = module.snapshot_cached(ttl=60)
    second = module.snapshot_cached(ttl=60)
    assert first == second
    assert calls == [1]
