#!/usr/bin/env python
# coding: utf-8
"""Regression tests for verify_supervisor_load.py (load-guard semantics).

Fixture provenance (VERIFY ORACLES):
  OPENCODE_CONNECT / OPENCODE_PLUGIN / FREEBUFF_LOAD  transcribed verbatim
      from producer logs captured 2026-10-18 (see guard docstring).
  FREEBUFF_LOAD_SUP  flagged minimal mutation of FREEBUFF_LOAD (supervisor
      appended to the captured server list); no real post-restart event
      exists yet, so the expected state is constructed, not captured.
  DECOY / ECHO lines  condensed from real observations (an AGENTS.md content
      echo carries the marker phrase verbatim; command echoes carry it too).

Run: C:/Azazael/.venvs/py315t/Scripts/python.exe tools/test_supervisor_load.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_supervisor_load as guard  # noqa: E402
import session_start  # noqa: E402  (module level is path constants + stdio
#                                      reconfigure; nothing executes on import)

OPENCODE_CONNECT = (
    'timestamp=2026-10-18T18:05:32.358Z level=INFO run=5614db05 '
    'message="mcp connected" server=supervisor tools=11 http.span=46347 '
    'role=server server=supervisor directory="C:\\\\Azazael" '  # scanner:ignore (fixture transcribed verbatim from producer log 2026-10-18)
    'connectionID=e83cae88-9cf9-4fa8-a36a-e16c9b67a86a'
)
OPENCODE_PLUGIN = (
    'timestamp=2026-10-18T18:03:30.784Z level=INFO run=a2cc16c0 '
    'message="engram plugin loaded | gate=tool.execute.before-throw-on-NO-GO '
    '| bin_ok=true | dir=C:\\\\Azazael"'  # scanner:ignore (fixture transcribed verbatim from producer log 2026-10-18)
)
ECHO_OC = (
    'timestamp=2026-10-18T18:20:00.000Z level=INFO run=echo01 '
    'message="spawning process" command="echo supervisor consulted"'
)
FREEBUFF_LOAD = (
    r'{"level":"DEBUG","timestamp":"2026-10-17T09:30:50.358Z","pid":4056,'
    r'"hostname":"LAPTOP-8U5MSH3G","data":{"mcpServers":["codegraph","pams",'
    r'"pmb","engram"],"source":"C:\\Users\\clint\\.agents\\mcp.json"},'  # scanner:ignore (fixture transcribed verbatim from captured producer log 2026-10-18)
    r'"msg":"[agents] Loaded MCP servers from mcp.json"}'
)

FREEBUFF_LOAD_SUP = FREEBUFF_LOAD.replace(
    '"pmb","engram"', '"pmb","engram","supervisor"')
DECOY = json.dumps({
    "level": "DEBUG", "msg": "file changed",
    "data": {"path": "C:/Azazael/AGENTS.md",
             "newContent": "(condensed) [agents] Loaded MCP servers from "
                           "mcp.json ... supervisor gate notes"}})
ECHO_FB = json.dumps({
    "level": "DEBUG",
    "msg": ("spawning process: echo '[agents] Loaded MCP servers from "
            "mcp.json' ... supervisor consulted")})
FB_CFG = {"mcpServers": {"supervisor": {}}}
OC_CFG = {"mcp": {"supervisor": {}}}
KILO_REL = ("AppData/Roaming/Code/User/globalStorage/kilocode.kilo-code"
            "/settings/mcp_settings.json")


def _w(p: Path, text: str, mtime=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def _cfg(home: Path, rel: str, obj):
    _w(home / rel, json.dumps(obj))


def _oc(home: Path, *lines: str):
    _w(home / ".local" / "share" / "opencode" / "log" / "opencode.log",
       "\n".join(lines) + "\n")


def _fb(home: Path, sid: str, *lines: str, mtime=None):
    _w(home / ".config" / "manicode" / "projects" / "azazael" / "chats"
       / sid / "log.jsonl", "\n".join(lines) + "\n", mtime)

def main() -> int:
    failures = []
    total = 0

    def expect(name, got, want):
        nonlocal total
        total += 1
        if got == want:
            print("ok   %s" % name)
        else:
            failures.append(name)
            print("FAIL %s\n     got:  %r\n     want: %r" % (name, got, want))

    with tempfile.TemporaryDirectory(prefix="sup-load-tests-") as td:
        root = Path(td)

        # --- opencode ------------------------------------------------------
        h = root / "oc-connect"
        _cfg(h, ".config/opencode/opencode.json", OC_CFG)
        _oc(h, OPENCODE_CONNECT)
        expect("opencode: captured connect line -> LOADED",
               guard.check_opencode(home=h),
               ("LOADED", "1 structured MCP connect(s)"))

        h = root / "oc-echo"
        _cfg(h, ".config/opencode/opencode.json", OC_CFG)
        _oc(h, ECHO_OC)
        expect("opencode: 'supervisor consulted' echo is not evidence",
               guard.check_opencode(home=h),
               ("CONFIG-ONLY", "no markers in opencode.log"))

        h = root / "oc-plugin"
        _cfg(h, ".config/opencode/opencode.json", OC_CFG)
        _oc(h, ECHO_OC, OPENCODE_PLUGIN)
        expect("opencode: plugin load only -> CONFIG-ONLY diagnostic",
               guard.check_opencode(home=h),
               ("CONFIG-ONLY",
                "gate plugin loaded; no supervisor connect observed yet"))

        h = root / "oc-broken"
        _cfg(h, ".config/opencode/opencode.json", {"mcp": {}})
        expect("opencode: missing supervisor in config -> BROKEN",
               guard.check_opencode(home=h),
               ("BROKEN", "opencode.json missing supervisor or unreadable"))

        # --- freebuff ------------------------------------------------------
        h = root / "fb-no-sup"
        _cfg(h, ".agents/mcp.json", FB_CFG)
        _fb(h, "sess-A", FREEBUFF_LOAD)
        expect("freebuff: captured load event lacks supervisor -> CONFIG-ONLY",
               guard.check_freebuff(home=h),
               ("CONFIG-ONLY", "sess-A load event lacks supervisor"))

        h = root / "fb-sup"
        _cfg(h, ".agents/mcp.json", FB_CFG)
        _fb(h, "sess-B", FREEBUFF_LOAD_SUP)
        expect("freebuff: load event with supervisor -> LOADED",
               guard.check_freebuff(home=h),
               ("LOADED", "sess-B loads [codegraph,pams,pmb,engram,supervisor]"))

        h = root / "fb-decoy"
        _cfg(h, ".agents/mcp.json", FB_CFG)
        _fb(h, "sess-new", DECOY)
        expect("freebuff: marker phrase inside AGENTS.md echo -> not a load",
               guard.check_freebuff(home=h),
               ("CONFIG-ONLY", "no load event found in newest sessions"))

        h = root / "fb-fallthrough"
        _cfg(h, ".agents/mcp.json", FB_CFG)
        _fb(h, "sess-old", FREEBUFF_LOAD_SUP, mtime=1000.0)
        _fb(h, "sess-new", DECOY, mtime=2000.0)
        expect("freebuff: falls through a newer log with no load event",
               guard.check_freebuff(home=h),
               ("LOADED", "sess-old loads [codegraph,pams,pmb,engram,"
                          "supervisor] (fell through newer logs)"))

        h = root / "fb-echo"
        _cfg(h, ".agents/mcp.json", FB_CFG)
        _fb(h, "sess-echo", ECHO_FB)
        expect("freebuff: command echo is skipped",
               guard.check_freebuff(home=h),
               ("CONFIG-ONLY", "no load event found in newest sessions"))

        h = root / "fb-broken"
        expect("freebuff: config missing supervisor -> BROKEN",
               guard.check_freebuff(home=h),
               ("BROKEN", "mcp.json missing supervisor or unreadable"))

        # --- kilo ----------------------------------------------------------
        h = root / "kilo-ok"
        _cfg(h, KILO_REL, {"mcpServers": {"supervisor": {}}})
        expect("kilo: config present -> CONFIG-ONLY, never LOADED",
               guard.check_kilo(home=h),
               ("CONFIG-ONLY",
                "no observable load log; CONFIG-ONLY never gates exit"))

        h = root / "kilo-broken"
        expect("kilo: config missing supervisor -> BROKEN",
               guard.check_kilo(home=h),
               ("BROKEN", "mcp_settings.json missing supervisor or unreadable"))

        # --- exit semantics (pure) -----------------------------------------
        expect("exit: observable loaded, kilo config-only -> 0",
               guard.compute_exit({"ts": 1, "freebuff": ("LOADED", "x"),
                                   "opencode": ("LOADED", "x"),
                                   "kilo": ("CONFIG-ONLY", "x")}), 0)
        expect("exit: restart pending -> 1",
               guard.compute_exit({"ts": 1, "freebuff": ("CONFIG-ONLY", "x"),
                                   "opencode": ("LOADED", "x"),
                                   "kilo": ("CONFIG-ONLY", "x")}), 1)
        expect("exit: any BROKEN -> 2",
               guard.compute_exit({"ts": 1, "freebuff": ("BROKEN", "x"),
                                   "opencode": ("LOADED", "x"),
                                   "kilo": ("CONFIG-ONLY", "x")}), 2)
        expect("exit: kilo BROKEN still -> 2",
               guard.compute_exit({"ts": 1, "freebuff": ("LOADED", "x"),
                                   "opencode": ("LOADED", "x"),
                                   "kilo": ("BROKEN", "x")}), 2)

        # --- --strict gating on the guard's exit (session_start) -----------
        # The control that matters: exit=1 (restart pending) is the COMMON
        # state and must stay non-blocking, or --strict would fail every
        # session that has not restarted its client yet.
        broken = session_start._load_config_broken
        expect("strict: guard exit=2 (broken config) gates",
               broken("exit=2"), True)
        expect("strict: guard exit=1 (restart pending) does NOT gate",
               broken("exit=1"), False)
        expect("strict: guard exit=0 does not gate", broken("exit=0"), False)
        expect("strict: unrunnable guard does not gate",
               broken("unrunnable"), False)
        expect("strict: missing guard does not gate",
               broken("guard-missing"), False)
        expect("strict: whitespace-tolerant", broken(" exit=2\n"), True)

    print()
    if failures:
        print("FAILED: %d of %d checks" % (len(failures), total))
        return 1
    print("all %d load-guard checks passed" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
