"""Negative-control tests for the lane-config check in tools/mcp_startup_gate.py.

Why this file exists: `lane_config_status` only ever inspected the server key named
`pmb`, so an alias (`pmb_direct`) sent the kilo lane straight at the daemon on :8765 and
passed the control that exists to catch exactly that. The check was extended
(`unfloored_servers`) but nothing pinned it, and an unpinned control drifts back the
moment someone refactors the parse. These tests make BOTH directions mandatory: a config
that only routes pmb through the floor proxy must PASS, and a config carrying any enabled
alias aimed at :8765 must FAIL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import mcp_startup_gate as gate  # noqa: E402


def _cfg(tmp_path: Path, servers: dict) -> Path:
    p = tmp_path / "lane.json"
    p.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return p


def test_floored_only_config_passes(tmp_path):
    """The known-healthy fixture: pmb -> :8766 and nothing else. Must not fire."""
    p = _cfg(tmp_path, {"pmb": {"url": f"http://127.0.0.1:{gate.PROXY_PORT}/mcp"}})
    status, detail, _ = gate.lane_config_status(p, "fixture-clean")
    assert status == gate.PASS, detail


def test_alias_at_daemon_fails(tmp_path):
    """The defect this control exists for: an enabled alias pointed at the raw daemon."""
    p = _cfg(tmp_path, {
        "pmb": {"url": f"http://127.0.0.1:{gate.PROXY_PORT}/mcp"},
        "pmb_direct": {"url": f"http://127.0.0.1:{gate.DAEMON_PORT}/mcp"},
    })
    status, detail, remedy = gate.lane_config_status(p, "fixture-dirty")
    assert status == gate.FAIL
    assert "pmb_direct" in detail
    assert str(gate.DAEMON_PORT) in detail or ":8765" in detail
    assert remedy, "a FAIL must name its remedy"


def test_disabled_alias_at_daemon_does_not_fail(tmp_path):
    """A disabled entry cannot be called, so it must not fire the control."""
    p = _cfg(tmp_path, {
        "pmb": {"url": f"http://127.0.0.1:{gate.PROXY_PORT}/mcp"},
        "pmb_direct": {"url": f"http://127.0.0.1:{gate.DAEMON_PORT}/mcp", "enabled": False},
    })
    status, detail, _ = gate.lane_config_status(p, "fixture-disabled")
    assert status == gate.PASS, detail


def test_pmb_itself_at_daemon_fails(tmp_path):
    """The original route must still be caught: pmb pointed at :8765."""
    p = _cfg(tmp_path, {"pmb": {"url": f"http://127.0.0.1:{gate.DAEMON_PORT}/mcp"}})
    status, detail, remedy = gate.lane_config_status(p, "fixture-pmb-direct")
    assert status == gate.FAIL
    # the detail names what is wrong, the remedy names where it should point
    assert str(gate.DAEMON_PORT) in detail
    assert str(gate.PROXY_PORT) in remedy


@pytest.mark.parametrize("holder", ["mcp", "mcpServers"])
def test_both_holder_keys_are_scanned(tmp_path, holder):
    """kilo uses `mcp`, opencode uses `mcpServers`; both must be inspected."""
    p = tmp_path / "lane.json"
    p.write_text(json.dumps({holder: {
        "pmb": {"url": f"http://127.0.0.1:{gate.PROXY_PORT}/mcp"},
        "pmb_raw": {"url": f"http://127.0.0.1:{gate.DAEMON_PORT}/mcp"},
    }}), encoding="utf-8")
    status, detail, _ = gate.lane_config_status(p, f"fixture-{holder}")
    assert status == gate.FAIL
    assert "pmb_raw" in detail
