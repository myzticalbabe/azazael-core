#!/usr/bin/env python
# coding: utf-8
"""Supervisor load guard: config presence is not liveness, per client.

Checks, worst-first:
  freebuff  ~/.agents/mcp.json has supervisor + newest session log's load
            event lists it. Events are JSON-parsed and matched on the exact
            `msg` value: substring scans false-positive on AGENTS.md content
            echoes, which quote the marker phrase verbatim.
  opencode  opencode.json has supervisor + opencode.log shows the structured
            `message="mcp connected" server=supervisor` marker (or an organic
            `supervisor consulted` line). Command echoes are skipped.
  kilo      mcp_settings.json has supervisor (config-only: no observable
            load log from here; CONFIG-ONLY kilo never gates the exit code).

EXIT
  0 = loaded in every observable client (freebuff, opencode)
  1 = config healthy but not yet observed (usually: client not restarted)
  2 = a config is missing the entry or is unreadable (kilo included)

Appends every run, errors included, to state/supervisor_load_log.jsonl.

Marker provenance (captured from producers 2026-10-18):
  opencode.log 18:05:32Z run 5614db05: message="mcp connected" server=supervisor
  chats/2026-10-17T09-30-50.355Z/log.jsonl: {"data":{"mcpServers":[...]},
  "msg":"[agents] Loaded MCP servers from mcp.json"}
"""
import glob
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "state" / "supervisor_load_log.jsonl"
HOME = Path.home()

# Tool-call echo lines in both clients' logs; never evidence of an MCP load.
ECHO_MARKERS = ("spawning process", "evaluated permission")
FREEBUFF_LOAD_MSG = "[agents] Loaded MCP servers from mcp.json"
OBSERVABLE = ("freebuff", "opencode")


def load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"__error__": str(exc)[:120]}


def _is_echo(line: str) -> bool:
    return any(m in line for m in ECHO_MARKERS)

def check_freebuff(home: Path = HOME):
    cfg = load_json(home / ".agents" / "mcp.json")
    if "__error__" in cfg or "supervisor" not in cfg.get("mcpServers", {}):
        return "BROKEN", "mcp.json missing supervisor or unreadable"
    logs = sorted(glob.glob(str(
        home / ".config" / "manicode" / "projects" / "*" / "chats" / "*"
        / "log.jsonl")), key=os.path.getmtime, reverse=True)
    fell_through = False
    for lp in logs[:5]:
        last = None
        try:
            with open(lp, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if FREEBUFF_LOAD_MSG not in line or _is_echo(line):
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("msg") == FREEBUFF_LOAD_MSG:
                        last = obj  # newest load event in this file wins
        except OSError as exc:
            fell_through = True
            sys.stderr.write("warn: unreadable log %s: %s\n" % (lp, exc))
            continue
        if last is None:
            fell_through = True
            continue
        servers = (last.get("data") or {}).get("mcpServers") or []
        session = Path(lp).parent.name
        suffix = " (fell through newer logs)" if fell_through else ""
        if "supervisor" in servers:
            return "LOADED", "%s loads [%s]%s" % (
                session, ",".join(str(s) for s in servers), suffix)
        return "CONFIG-ONLY", "%s load event lacks supervisor%s" % (
            session, suffix)
    return "CONFIG-ONLY", "no load event found in newest sessions"

def check_opencode(home: Path = HOME):
    cfg = load_json(home / ".config" / "opencode" / "opencode.json")
    if "__error__" in cfg or "supervisor" not in cfg.get("mcp", {}):
        return "BROKEN", "opencode.json missing supervisor or unreadable"
    lp = home / ".local" / "share" / "opencode" / "log" / "opencode.log"
    connect = consult = plugin = 0
    try:
        with open(lp, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if _is_echo(line):
                    continue
                if ('message="mcp connected"' in line
                        and "server=supervisor" in line):
                    connect += 1
                if "supervisor consulted" in line:
                    consult += 1
                if 'message="engram plugin loaded' in line:
                    plugin += 1
    except OSError as exc:
        return "CONFIG-ONLY", "log unreadable: %s" % exc
    if connect:
        return "LOADED", "%d structured MCP connect(s)" % connect
    if consult:
        return "LOADED", "%d organic consult(s)" % consult
    if plugin:
        return "CONFIG-ONLY", (
            "gate plugin loaded; no supervisor connect observed yet")
    return "CONFIG-ONLY", "no markers in opencode.log"


def check_kilo(home: Path = HOME):
    cfg = load_json(home / "AppData" / "Roaming" / "Code" / "User"
                    / "globalStorage" / "kilocode.kilo-code" / "settings"
                    / "mcp_settings.json")
    if "__error__" in cfg or "supervisor" not in cfg.get("mcpServers", {}):
        return "BROKEN", "mcp_settings.json missing supervisor or unreadable"
    return "CONFIG-ONLY", "no observable load log; CONFIG-ONLY never gates exit"

def compute_exit(results: dict) -> int:
    # BROKEN anywhere (kilo included) -> 2. Kilo's CONFIG-ONLY never holds
    # the exit code: Kilo exposes no load log from this host.
    if any(v[0] == "BROKEN" for v in results.values()
           if isinstance(v, (list, tuple))):
        return 2
    if all(results[name][0] == "LOADED" for name in OBSERVABLE):
        return 0
    return 1


def main() -> int:
    results = {"ts": int(time.time()), "freebuff": check_freebuff(),
               "opencode": check_opencode(), "kilo": check_kilo()}
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(results) + "\n")
    except OSError as exc:
        sys.stderr.write("warn: load log append failed: %s\n" % exc)
        results["log_write_failed"] = str(exc)
    for name in ("freebuff", "opencode", "kilo"):
        print("%s: %s (%s)" % (name, results[name][0], results[name][1]))
    return compute_exit(results)


if __name__ == "__main__":
    sys.exit(main())
