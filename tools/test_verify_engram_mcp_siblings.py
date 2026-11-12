#!/usr/bin/env python
# coding: utf-8
"""Regression tests for verify_engram_mcp.newest_session (session-id scoping).

THE DEFECT THIS PINS. Until 2026-10-31 the guard took the newest log FILE as
"the newest session" and called that one file's silence a dead stack. The
client writes one log.jsonl per project dir and two dirs can share a session
id: measured on 2026-10-31, projects/clint and projects/Desktop both held
2026-10-31T08-23-08.778Z, only Desktop logged the load event, and the guard
reported "no servers loaded at that start" while all six servers answered.
29 of 573 logged runs carry that false ERROR.

FIXTURE PROVENANCE (VERIFY ORACLES):
  LOAD       transcribed verbatim from the captured client load event
             (2026-10-31, projects/Desktop/.../log.jsonl).
  ECHO       condensed from the real echo shape: the phrase "Loaded MCP
             servers" appears inside a payload (data.newContent / fullResponse)
             rather than in a load event, so data.mcpServers is absent.
  SILENT     a real non-load log line (nothing to parse).

CONTROLS, in the order they run:
  NEGATIVE (must NOT fire) two dirs share the session id, the newer dir's log
               is silent/echo, the older dir logged the event -> loaded.
  POSITIVE (must fire)     no dir logged an event -> servers is None, so the
               caller's failure branch still reaches the error.
  UNCHANGED                one dir with a real event -> loaded, one log dir.
  NO EVIDENCE              no logs at all -> None (caller owns that branch).

Run: C:/Azazael/.venvs/py315t/Scripts/python.exe tools/test_verify_engram_mcp_siblings.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_engram_mcp as guard  # noqa: E402

SESS = "2026-10-31T08-23-08.778Z"

LOAD = json.dumps({
    "level": "DEBUG", "timestamp": "2026-10-31T08:23:54.091Z", "pid": 19864,
    "data": {"mcpServers": ["codegraph", "pams", "pmb", "engram",
                            "supervisor", "barta-scrape"],
             "source": "C:/Users/clint/.agents/mcp.json"},
    "msg": "[agents] Loaded MCP servers from mcp.json"})

# The phrase appears, but only inside a payload: no load event, no servers.
ECHO = json.dumps({
    "level": "DEBUG", "timestamp": "2026-10-31T08:24:25.269Z", "pid": 19864,
    "data": {"path": "C:/Azazael/AGENTS.md",
             "newContent": "(condensed) [agents] Loaded MCP servers from "
                           "mcp.json ... supervisor gate notes"}})

SILENT = json.dumps({
    "level": "DEBUG", "timestamp": "2026-10-31T08:25:05.623Z", "pid": 19864,
    "data": {"msg": "iteration complete", "iteration": 3}})

_FAILS = []


def expect(label, got, want):
    ok = got == want
    print("  [%s] %s" % ("PASS" if ok else "FAIL", label))
    if not ok:
        print("         got  %r" % (got,))
        print("         want %r" % (want,))
        _FAILS.append(label)


def _log(root, proj, text, mtime):
    p = Path(root) / proj / "chats" / SESS / "log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + "\n", encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def _logs(root):
    """Exactly what newest_sessions() returns, newest first."""
    out = []
    for proj in Path(root).iterdir():
        f = proj / "chats" / SESS / "log.jsonl"
        if f.is_file():
            out.append((f.stat().st_mtime, f, proj.name, SESS))
    out.sort(reverse=True, key=lambda x: x[0])
    return out


def run():
    root = tempfile.mkdtemp(prefix="engram_siblings_")
    try:
        # ---- NEGATIVE control: the condition that must NOT be a failure -----
        # clint/ is the NEWER file (as it is live) and says nothing; Desktop/
        # is older and carries the event. Before the fix this was ERROR.
        _log(root, "clint", SILENT + "\n" + ECHO, mtime=2000.0)
        _log(root, "Desktop", LOAD, mtime=1000.0)
        got = guard.newest_session(_logs(root), root=root)
        expect("two dirs, newer dir silent -> judge by the dir that logged",
               (got["proj"], got["servers"]), ("Desktop", json.loads(LOAD)["data"]["mcpServers"]))
        expect("two dirs, both named in log_dirs",
               sorted(got["log_dirs"]), ["Desktop", "clint"])
        expect("evidence dir's own source is used",
               got["source"], json.loads(LOAD)["data"]["source"])

        # ---- POSITIVE control: still a failure when NO dir logged ----------
        _log(root, "Desktop", SILENT, mtime=3000.0)
        got = guard.newest_session(_logs(root), root=root)
        expect("no dir logged an event -> servers None (failure reaches caller)",
               got["servers"], None)
        expect("failure still names both dirs it checked",
               sorted(got["log_dirs"]), ["Desktop", "clint"])

        # ---- an echo alone must not count as a load event ------------------
        _log(root, "Desktop", SILENT, mtime=3000.0)
        got = guard.newest_session(_logs(root), root=root)
        expect("phrase inside a payload is not a load event", got["servers"], None)

        # ---- UNCHANGED single-dir behaviour -------------------------------
        single = tempfile.mkdtemp(prefix="engram_single_")
        try:
            _log(single, "clint", LOAD, mtime=1000.0)
            got = guard.newest_session(_logs(single), root=single)
            expect("single dir with an event -> loaded", got["proj"], "clint")
            expect("single dir -> one log dir", got["log_dirs"], ["clint"])
        finally:
            shutil.rmtree(single, ignore_errors=True)

        # ---- no evidence at all -------------------------------------------
        expect("no logs -> None, caller owns that branch",
               guard.newest_session([]), None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    print("verify_engram_mcp.newest_session - session-id scoping")
    run()
    if _FAILS:
        print("\nFAILED: %d - %s" % (len(_FAILS), "; ".join(_FAILS)))
        sys.exit(1)
    print("\nOK - 9 assertions, negative control included")
