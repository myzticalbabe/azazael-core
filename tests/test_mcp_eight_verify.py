"""Tests for tools/mcp_eight_verify.py.

The tool's job is to prove seven other servers work, so its own failure mode is
silent: a probe that accepts everything reports eight green servers and proves
nothing. Half of these tests exist to make sure the probe can FAIL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import mcp_eight_verify as eight  # noqa: E402

FAKE_SERVER = '''
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if msg.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"),
                          "result": {"protocolVersion": "2025-06-18",
                                     "capabilities": {},
                                     "serverInfo": {"name": "fake"}}}), flush=True)
    elif msg.get("method") == "tools/list":
        print(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"),
                          "result": {"tools": [{"name": "a"}, {"name": "b"}]}}),
              flush=True)
'''


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
def test_jsonc_comments_and_trailing_commas(tmp_path: Path):
    cfg = tmp_path / "kilo.jsonc"
    cfg.write_text(
        '{\n  // a comment naming a script must not break the parse\n'
        '  "mcp": {"a": {"type": "local", "command": ["x.exe"],},\n'
        '          "b": {"type": "remote", "url": "http://127.0.0.1:8766/mcp"}}\n}\n',
        encoding="utf-8")
    doc, err = eight.read_jsonc(cfg)
    assert err == ""
    assert set(eight.servers(doc)) == {"a", "b"}


def test_invalid_json_is_a_finding_not_an_exception(tmp_path: Path):
    cfg = tmp_path / "broken.json"
    cfg.write_text('{"mcp": ', encoding="utf-8")
    doc, err = eight.read_jsonc(cfg)
    assert doc is None
    assert "invalid JSON" in err


def test_missing_config_reports_unreadable(tmp_path: Path):
    doc, err = eight.read_jsonc(tmp_path / "nope.json")
    assert doc is None
    assert "unreadable" in err


def test_command_of_accepts_string_and_list():
    assert eight.command_of({"command": "a.exe", "args": ["-x"]}) == ["a.exe", "-x"]
    assert eight.command_of({"command": ["a.exe", "b.py"]}) == ["a.exe", "b.py"]
    assert eight.command_of({}) == []


def test_resolve_executable_finds_file_path_lookup_and_missing(tmp_path: Path):
    real = tmp_path / "server.exe"
    real.write_text("", encoding="utf-8")
    assert eight.resolve_executable([str(real)])[0] == str(real)

    found, why = eight.resolve_executable([sys.executable])
    assert found and why == ""

    missing, why = eight.resolve_executable(["__no_such_server__.exe"])
    assert missing is None
    assert "not found" in why
    assert eight.resolve_executable([])[1] == "no command"


def test_unfloored_only_matches_the_raw_daemon():
    assert eight.is_unfloored("http://127.0.0.1:8765/mcp")
    assert eight.is_unfloored("http://127.0.0.1:8765/mcp/")
    assert not eight.is_unfloored("http://127.0.0.1:8766/mcp")


def test_needle_names_the_script_not_the_interpreter():
    """The defect this guards: a needle of python.exe counted every python."""
    needle = eight._needle([sys.executable, "C:/x/codegraph_mcp.py", "--graph", "g.json"],
                           sys.executable)
    assert needle == "codegraph_mcp.py"
    assert eight._needle([r"C:/go/bin/engram.exe", "mcp"], r"C:/go/bin/engram.exe") == "engram.exe"


# --------------------------------------------------------------------------
# verdicts
# --------------------------------------------------------------------------
def _procs(*needles: str):
    return [{"pid": i + 1, "name": "python.exe", "cmd": "python %s" % n}
            for i, n in enumerate(needles)]


def test_disabled_entry_is_named_not_counted_as_a_pass():
    row = eight.verify_entry("pmb_direct",
                             {"type": "remote", "enabled": False,
                              "url": "http://127.0.0.1:8765/mcp"}, [], True, 5)
    assert row.state == eight.DISABLED
    assert ":8765" in row.detail


def test_enabled_entry_on_the_raw_daemon_fails():
    row = eight.verify_entry("pmb", {"type": "remote",
                                     "url": "http://127.0.0.1:8765/mcp"}, [], True, 5)
    assert row.state == eight.FAIL
    assert ":8766" in row.remedy


def test_missing_executable_fails_with_the_path_named(tmp_path: Path):
    row = eight.verify_entry("ghost", {"type": "local",
                                       "command": [str(tmp_path / "gone.exe")]},
                             [], True, 5)
    assert row.state == eight.FAIL
    assert "gone.exe" in row.detail


def test_client_owned_singleton_is_not_probed_but_must_be_live():
    entry = {"type": "local", "command": [sys.executable, "C:/x/pams_server_launcher.py"]}
    dead = eight.verify_entry("pams", entry, _procs("other.py"), True, 5)
    assert dead.state == eight.FAIL
    assert "RESTART THE CLIENT" in dead.remedy

    live = eight.verify_entry("pams", entry, _procs("pams_server_launcher.py"), True, 5)
    assert live.state == eight.OWNED


def test_artifact_note_is_silent_without_a_data_file():
    assert eight.artifact_note(["python.exe", "server.py"], "python.exe") == ""


def test_artifact_note_reports_the_age_of_the_file_it_serves(tmp_path: Path):
    """A server can answer correctly and still describe a ten-day-old tree."""
    graph = tmp_path / "graph.json"
    graph.write_text("{}", encoding="utf-8")
    import os
    import time
    old = time.time() - 5 * 86400
    os.utime(graph, (old, old))
    note = eight.artifact_note(["python.exe", "codegraph_mcp.py", "--graph", str(graph)],
                               "python.exe")
    assert "graph.json" in note
    assert "5.0 d old" in note


def test_fast_mode_resolves_without_spawning():
    row = eight.verify_entry("svc", {"type": "local",
                                     "command": [sys.executable, "C:/x/svc.py"]},
                             _procs("svc.py"), True, 5)
    assert row.state == eight.CONFIG_ONLY
    assert row.live == "1"


# --------------------------------------------------------------------------
# the probe itself, and its negative control
# --------------------------------------------------------------------------
def test_probe_reads_a_real_server(tmp_path: Path):
    server = tmp_path / "fake_server.py"
    server.write_text(FAKE_SERVER, encoding="utf-8")
    probe = eight.probe_stdio([sys.executable, str(server)], init_deadline=10, tools_deadline=10)
    assert probe.ok, probe.detail
    assert probe.tools == 2


def test_probe_refuses_a_bogus_command(tmp_path: Path):
    probe = eight.probe_stdio([str(tmp_path / "nothing_here.exe")], init_deadline=5)
    assert not probe.ok


def test_control_must_fail_or_the_probe_is_unvalidated():
    ok, note = eight.control_probe(8)
    assert ok, note
    assert "refused" in note


def test_probe_terminates_the_server_it_started(tmp_path: Path):
    """A leaked child would be a second server answering later probes."""
    server = tmp_path / "fake_server.py"
    server.write_text(FAKE_SERVER, encoding="utf-8")
    before = eight.live_count("fake_server.py", eight.processes())
    eight.probe_stdio([sys.executable, str(server)], init_deadline=10, tools_deadline=10)
    after = eight.live_count("fake_server.py", eight.processes())
    assert after <= before


# --------------------------------------------------------------------------
# end to end
# --------------------------------------------------------------------------
def _write_lane(tmp_path: Path, servers: dict) -> Path:
    cfg = tmp_path / "lane.json"
    cfg.write_text(json.dumps({"mcp": servers}), encoding="utf-8")
    return cfg


def test_status_for_reports_every_entry(tmp_path: Path):
    cfg = _write_lane(tmp_path, {
        "ok": {"type": "local", "command": [sys.executable, "C:/x/ok.py"]},
        "off": {"type": "remote", "enabled": False, "url": "http://127.0.0.1:8765/mcp"},
        "bad": {"type": "local", "command": [str(tmp_path / "missing.exe")]},
    })
    rows = eight.status_for(cfg, procs=_procs("ok.py"), fast=True)
    states = {r.name: r.state for r in rows}
    assert states == {"ok": eight.CONFIG_ONLY, "off": eight.DISABLED, "bad": eight.FAIL}


def test_main_exits_non_zero_when_an_entry_cannot_start(tmp_path: Path, capsys):
    cfg = _write_lane(tmp_path, {"bad": {"type": "local",
                                         "command": [str(tmp_path / "missing.exe")]}})
    code = eight.main(["--config", str(cfg), "--fast", "--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["servers"][0]["state"] == eight.FAIL


def test_main_exits_zero_when_every_entry_resolves(tmp_path: Path, capsys):
    cfg = _write_lane(tmp_path, {
        "ok": {"type": "local", "command": [sys.executable, "C:/x/ok.py"]},
        "off": {"type": "remote", "enabled": False, "url": "http://127.0.0.1:8765/mcp"},
    })
    code = eight.main(["--config", str(cfg), "--fast", "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_gate_check_fails_when_an_entry_cannot_start(tmp_path: Path, monkeypatch):
    """The gate check must be able to fail -- otherwise it is decoration."""
    import mcp_startup_gate as gate

    cfg = _write_lane(tmp_path, {"ghost": {"type": "local",
                                          "command": [str(tmp_path / "ghost.exe")]}})
    absent = tmp_path / "absent.json"
    monkeypatch.setattr(gate, "KILO_CONF", cfg)
    monkeypatch.setattr(gate, "FREEBUFF_CONF", absent)
    monkeypatch.setattr(gate, "OPENCODE_CONF", absent)
    monkeypatch.setattr(gate, "OPENCODE_KILO_CONF", absent)
    status, detail, remedy = gate.check_context_servers([])
    assert status == gate.FAIL
    assert "ghost" in detail
    assert "ghost.exe" in detail
    assert remedy


def test_gate_check_passes_when_every_entry_resolves(tmp_path: Path, monkeypatch):
    import mcp_startup_gate as gate

    cfg = _write_lane(tmp_path, {
        "ok": {"type": "local", "command": [sys.executable, "C:/x/ok.py"]},
        "off": {"type": "remote", "enabled": False, "url": "http://127.0.0.1:8765/mcp"},
    })
    absent = tmp_path / "absent.json"
    monkeypatch.setattr(gate, "KILO_CONF", cfg)
    monkeypatch.setattr(gate, "FREEBUFF_CONF", absent)
    monkeypatch.setattr(gate, "OPENCODE_CONF", absent)
    monkeypatch.setattr(gate, "OPENCODE_KILO_CONF", absent)
    status, detail, _ = gate.check_context_servers([])
    assert status == gate.PASS
    assert "2 entries" in detail


def test_main_on_unreadable_config_exits_non_zero(tmp_path: Path, capsys):
    code = eight.main(["--config", str(tmp_path / "absent.json"), "--fast"])
    assert code == 1
    assert "unreadable" in capsys.readouterr().err
