#!/usr/bin/env python
# coding: utf-8
"""mcp_startup_gate -- standing startup check for every lane's MCP stack.

WHY THIS EXISTS
On 2026-10-30 three separate failures were each invisible until something
broke in front of the user:

  * the floor proxy had a duplicate instance on 8767 that nothing managed;
  * the supervisor MCP children had been running since 10:34 while
    supervisor_mcp.py was edited at 13:19 -- so the relevance floor was in the
    FILE and not in the PROCESS, and a nonsense query still answered with
    0.28-score rows;
  * freebuff's lane still pointed pmb at the daemon directly (8765), bypassing
    the floor that Kilo had already been wired to.

None of those raise an error. Each one looks like a working stack. So the stack
is enumerated and exercised here, by behaviour, before any task starts.

WHAT IT CHECKS (behaviour first, config second)
  1  pmb-daemon        TCP 8765 + MCP initialize
  2  floor-proxy       TCP 8766 + initialize + a NONCE recall returns zero rows
                       (a fresh nonce cannot be in the corpus, so any row is
                       proof the floor is not being applied) + `stats` passes
  3  proxy-singleton   exactly one pmb_floor_proxy.py instance (both freshness and
                       duplication live here: one instance, started after the edit)
  4  floor-origin      that instance was started by a DURABLE starter (the logon
                       Startup item), not by a client session that happens to
                       still be warm. A session-scoped stack that works today is
                       absent after the next reboot
  5  supervisor-fresh  every supervisor_mcp.py process started after its edit
  6  pams-running      pams_server_launcher.py present and not stale
  7  lane-config       every lane config parses and routes pmb through :8766
  8  lane-credentials  every lane's BAKED pmb token still matches the live
                       daemon token. Added 2026-11-11: two lane configs held a
                       rotated-out token, so their pmb tools were dead while
                       this gate read PASS=9 -- `lane-config` only asked
                       whether the URL routes through :8766, never whether the
                       credential authenticates. A lane with NO baked token is
                       fine: the proxy resolves the live one at request time.
  9  lane-context      engram / codegraph / barta present (WARN only)

USAGE
  python mcp_startup_gate.py            check, exit 1 on any FAIL
  python mcp_startup_gate.py --fix      repair what is repairable, then check
  python mcp_startup_gate.py --json     machine-readable result
  python mcp_startup_gate.py --quiet    only FAIL/WARN lines

WHAT --fix WILL AND WILL NOT DO
  WILL   start the pmb daemon; kill duplicate/unmanaged floor proxies and spawn
         exactly one; nothing else touches a running process.
  WILL NOT kill or restart pams or supervisor. Those are stdio children owned by
         the MCP client, and killing one from here would break the live session.
         A stale child is reported as BLOCKED with the one remedy that works:
         restart the client. That is why the check exists.
"""
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import hmac
from datetime import datetime
from typing import Dict, List
from pathlib import Path

HOME = Path.home()
PMB_TOKEN = HOME / ".pmb" / "daemon.token"
PMB_VENV_PY = Path("C:/Users/clint/.pmb-venv/Scripts/python.exe")
PMB_EXE = HOME / ".pmb-venv" / "Scripts" / "pmb.exe"
PROXY_SCRIPT = Path("C:/Azazael/azazael/tools/pmb_floor_proxy.py")
SUPERVISOR_SCRIPT = Path("C:/Azazael/azazael/tools/supervisor_mcp.py")
PAMS_SCRIPT = HOME / "llama-server" / "unified" / "pams_server_launcher.py"
KILO_CONF = HOME / ".config" / "kilo" / "kilo.jsonc"
FREEBUFF_CONF = HOME / ".agents" / "mcp.json"
OPENCODE_CONF = HOME / ".config" / "opencode" / "opencode.json"
OPENCODE_KILO_CONF = HOME / ".config" / "opencode" / "kilo.jsonc"
ORDERS_SYNC = Path("C:/Azazael/azazael/tools/agent_orders_sync.py")
PROXY_STAMP = HOME / ".pmb" / "floor_proxy_runtime.json"
PROXY_STARTUP = (HOME / "AppData" / "Roaming" / "Microsoft" / "Windows" /
                 "Start Menu" / "Programs" / "Startup" / "pmb-floor-proxy.vbs")

#: Diagnostics from except paths that must not raise but must not be silent
#: either. The compliance scanner bars `except: return <constant>` because it
#: makes a fault indistinguishable from success; recording first and then
#: returning the same value keeps the caller's contract and the reason.
#:
#: Defined HERE, above `_mcp_stack_config`, because that function calls
#: `_note` when the config cannot be read -- a helper defined further down
#: would not exist yet at the moment it is needed.
NOTES: list[str] = []


def _note(message):
    """Record a handled exception without changing the caller's result."""
    NOTES.append(str(message))


# MCP stack configuration from config/defaults.json (mcp_stack section)
def _mcp_stack_config() -> dict:
    try:
        from tools.defaults_profile import read_default
        return {
            "daemon_port": read_default("mcp_stack.daemon_port", 8765),
            "floor_port": read_default("mcp_stack.floor_port", 8766),
            "floor_enabled": read_default("mcp_stack.floor_enabled", True),
            "floor_upstream_wait_s": read_default("mcp_stack.floor_upstream_wait_s", 30),
            "floor_origin": read_default("mcp_stack.floor_origin", "logon"),
            "lanes_route_through_floor": read_default("mcp_stack.lanes_route_through_floor", True),
        }
    except Exception as exc:
        _note("_mcp_stack_config: config unreadable (%s: %s) -- using hardcoded defaults" %
              (type(exc).__name__, exc))
        return {
            "daemon_port": 8765, "floor_port": 8766, "floor_enabled": True,
            "floor_upstream_wait_s": 30, "floor_origin": "logon",
            "lanes_route_through_floor": True,
        }

_cfg = _mcp_stack_config()
DAEMON_PORT = _cfg["daemon_port"]
PROXY_PORT = _cfg["floor_port"]
PROXY_URL = "http://127.0.0.1:%d/mcp" % PROXY_PORT
FLOOR_ENABLED = _cfg["floor_enabled"]
FLOOR_UPSTREAM_WAIT = _cfg["floor_upstream_wait_s"]
DURABLE_ORIGIN = _cfg["floor_origin"]
LANES_ROUTE_THROUGH_FLOOR = _cfg["lanes_route_through_floor"]

PASS, FAIL, WARN, BLOCKED, FIXED = "PASS", "FAIL", "WARN", "BLOCKED", "FIXED"


# --------------------------------------------------------------------------
# instruments
# --------------------------------------------------------------------------
def port_open(port, host="127.0.0.1", timeout=2.0):
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def _ps_json(script):
    """Run PowerShell and parse its JSON. Returns [] on any failure."""
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120).stdout.strip()
    except Exception:
        return []
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


_EPOCH = re.compile(r"/Date\((\d+)")


def _created(value):
    """WMI CreationDate -> datetime. Handles both /Date(ms)/ and ISO strings."""
    if isinstance(value, str):
        hit = _EPOCH.search(value)
        if hit:
            return datetime.fromtimestamp(int(hit.group(1)) / 1000.0)
        try:
            return datetime.fromisoformat(value[:19])
        except ValueError as exc:
            _note("_created: timestamp %r will not parse (%s)" % (value, exc))
            return None
    return None


def processes():
    """All processes with a readable command line, filtered in PYTHON.

    The filter is deliberately NOT in the PowerShell query: a query string
    naming a script appears verbatim in powershell.exe's own command line, so
    it matches itself and inflates every count. Measured: a
    'CommandLine -match pmb_floor_proxy' filter returned 7 processes for one.
    """
    rows = _ps_json(
        "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,"
        "CreationDate,Name,CommandLine | ConvertTo-Json -Compress")
    out = []
    for row in rows:
        cmd = row.get("CommandLine") or ""
        if not cmd:
            continue
        out.append({"pid": row.get("ProcessId"),
                    "ppid": row.get("ParentProcessId"),
                    "name": row.get("Name") or "",
                    "cmd": cmd,
                    "created": _created(row.get("CreationDate"))})
    return out


def _ancestors(procs):
    """PIDs from this process up to the root, so no check can kill its caller.

    Learned the hard way: a command line that merely NAMES the script is not
    the process that runs it. `python -m py_compile pmb_floor_proxy.py && gate
    --fix` put the script name in the calling shell's own command line, the
    duplicate sweep matched it, and the gate killed the shell it was running
    in (exit 255). Two guards: the executable must be a python, and no
    ancestor of this process is ever eligible.
    """
    by_pid = {p["pid"]: p for p in procs}
    seen = set()
    pid = os.getpid()
    while pid and pid not in seen:
        seen.add(pid)
        parent = by_pid.get(pid)
        pid = parent["ppid"] if parent else None
    return seen


def procs_running(script_path, procs):
    """Processes that actually EXECUTE this script.

    Requires a python executable AND the script name in the command line AND
    the process is not an ancestor of this one. A command line that merely
    mentions the script (a compile step, a grep, this gate's own caller) no
    longer counts -- see _ancestors.
    """
    name = Path(script_path).name.lower()
    skip = _ancestors(procs)
    return [p for p in procs
            if p["pid"] not in skip
            and str(p["name"]).lower().startswith("python")
            and name in p["cmd"].lower()]


def file_mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path))
    except OSError as exc:
        _note("file_mtime: %s (%s)" % (path, exc))
        return None


def _token():
    try:
        return PMB_TOKEN.read_text(encoding="utf-8").strip()
    except OSError as exc:
        _note("_token: cannot read %s (%s)" % (PMB_TOKEN, exc))
        return ""


def _live_daemon_token():
    """The daemon's CURRENT token, or "" when it cannot be read.

    Read at call time, never cached: the token can be rotated under us, and a
    cached copy would make this check report on a stale world -- the exact
    defect this check exists to catch.
    """
    return _token()


def discover_daemon_port() -> int:
    """Discover the live daemon port from servers.json registry.
    Falls back to 8765 if registry is unreadable or empty."""
    registry = Path.home() / ".pmb" / "servers.json"
    default_port = 8765
    try:
        entries = json.loads(registry.read_text(encoding="utf-8"))
    except Exception:
        return default_port
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") not in (None, "daemon"):
            continue
        if entry.get("pid_alive") is False:
            continue
        port = entry.get("port")
        if port:
            return int(port)
    return 8765


def _parse_frames(raw, ctype):
    if "event-stream" in (ctype or ""):
        frames = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    try:
                        frames.append(json.loads(chunk))
                    except ValueError as exc:
                        _note("_parse_frames: bad SSE frame %r (%s)"
                              % (chunk[:80], exc))
        return frames
    try:
        return [json.loads(raw)]
    except ValueError:
        return []


class Mcp:
    """Minimal MCP-over-HTTP client. No SDK, so no version drift."""

    def __init__(self, url, token=None):
        self.url = url
        self.token = token if token is not None else _token()
        self.session = None

    def _post(self, payload, timeout=45):
        headers = {"Authorization": "Bearer " + self.token,
                   "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                sid = resp.headers.get("Mcp-Session-Id")
                return raw, sid, resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            # A 4xx/5xx is a finding about the stack, not a crash. The
            # historical defect: :8766 returned `400 Missing session ID` and
            # the gate died in a traceback instead of reporting FAIL, so a
            # broken lane read as "the gate is broken".
            hdrs = exc.headers
            raw = exc.read().decode("utf-8", "replace") or ""
            if not raw.strip():
                raw = json.dumps({"error": {"code": exc.code,
                                            "message": str(exc.reason)}})
            return (raw,
                    hdrs.get("Mcp-Session-Id") if hdrs else None,
                    (hdrs.get("Content-Type", "") if hdrs else ""))

    def initialize(self, timeout=45):
        raw, sid, ctype = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "mcp-startup-gate", "version": "1"}}},
            timeout)
        self.session = sid or self.session
        frames = _parse_frames(raw, ctype)
        for frame in frames:
            if isinstance(frame.get("result"), dict):
                return True, frame["result"].get("serverInfo", {})
        return False, str(frames)[:200]

    def call(self, name, args, timeout=60):
        raw, _, ctype = self._post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": name, "arguments": args}}, timeout)
        for frame in _parse_frames(raw, ctype):
            result = frame.get("result")
            if isinstance(result, dict):
                if isinstance(result.get("content"), list) and result["content"]:
                    text = result["content"][0].get("text", "")
                    try:
                        return json.loads(text)
                    except ValueError:
                        return {"_text": text}
                return result
            if "error" in frame:
                return {"_error": frame["error"]}
        return {"_error": "unparsable response"}


# --------------------------------------------------------------------------
# repairs
# --------------------------------------------------------------------------
def floor_start_via_startup():
    """Start the proxy the way a reboot does: through the logon Startup item."""
    if not PROXY_STARTUP.exists():
        return "missing %s" % PROXY_STARTUP
    try:
        subprocess.run(["cscript.exe", "//nologo", str(PROXY_STARTUP)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except Exception as exc:
        return "startup item failed: %s" % exc
    for _ in range(40):
        time.sleep(0.25)
        if port_open(PROXY_PORT):
            return "started by the logon item"
    return "startup item ran but 127.0.0.1:%d did not open" % PROXY_PORT


def start_proxy():
    """Start one floor proxy, preferring the DURABLE path.

    A direct spawn works, but the instance it leaves is session-scoped: nothing
    recreates it at the next boot, which is the state check_floor_origin exists
    to refuse. So repairs go through the logon item whenever there is one, and
    fall back to a direct spawn only on a host without it.
    """
    if not (PMB_VENV_PY.exists() and PROXY_SCRIPT.exists()):
        return "missing %s or %s" % (PMB_VENV_PY, PROXY_SCRIPT)
    note = floor_start_via_startup()
    if port_open(PROXY_PORT):
        return note
    flags = 0
    for attr in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, attr, 0)
    subprocess.Popen([str(PMB_VENV_PY), str(PROXY_SCRIPT)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=flags)
    for _ in range(20):
        time.sleep(0.25)
        if port_open(PROXY_PORT):
            return "started"
    return "spawned but %s:%d did not open" % ("127.0.0.1", PROXY_PORT)


def start_daemon():
    if not PMB_EXE.exists():
        return "missing %s" % PMB_EXE
    try:
        subprocess.run([str(PMB_EXE), "daemon", "start"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=90)
    except Exception as exc:
        return "start failed: %s" % exc
    daemon_port = discover_daemon_port()
    for _ in range(40):
        if port_open(daemon_port):
            return "started"
        time.sleep(0.5)
    return "started but 127.0.0.1:%d did not open" % daemon_port


def kill(pid):
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        "Stop-Process -Id %d -Force" % int(pid)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return True
    except Exception as exc:
        _note("kill(%s) failed (%s)" % (pid, exc))
        return False


# --------------------------------------------------------------------------
# lane config
# --------------------------------------------------------------------------
def _strip_jsonc(text):
    return re.sub(r"(?m)^\s*//.*$", "", text)


def read_config(path):
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, "unreadable (%s)" % exc
    try:
        return json.loads(_strip_jsonc(text)), ""
    except ValueError as exc:
        return None, "invalid JSON (%s)" % exc


def pmb_entry(doc):
    """(url, enabled) for a pmb-ish entry, or (None, None)."""
    if not isinstance(doc, dict):
        return None, None
    for holder in ("mcp", "mcpServers"):
        servers = doc.get(holder)
        if isinstance(servers, dict):
            for key in ("pmb", "pmb-mcp", "pmb_mcp"):
                entry = servers.get(key)
                if isinstance(entry, dict):
                    return entry.get("url"), entry.get("enabled", True)
    return None, None


def pmb_baked_token(doc):
    """The Authorization token a lane's config BAKES into its pmb entry.

    Returns the bearer value (without the "Bearer " prefix) or None when the
    entry carries no credential at all.

    WHY THIS IS HERE
    Measured 2026-11-11: every lane config carried `Authorization: Bearer
    <token>` for pmb, and the daemon's token had been rotated out from under
    two of them. The daemon was healthy the whole time -- 30 tools, 62 ms --
    and `opencode mcp list` reported `pmb needs authentication`, so every pmb
    tool vanished for that session. THIS GATE read PASS=9 throughout,
    because `lane-config` only asked whether the pmb URL routes through
    :8766 and never asked whether the baked credential still authenticates.

    A copy of a token the server can rotate is a promise the server never
    made, so the copy has to be CHECKED, not trusted.
    """
    if not isinstance(doc, dict):
        return None
    for holder in ("mcp", "mcpServers"):
        servers = doc.get(holder)
        if isinstance(servers, dict):
            for key in ("pmb", "pmb-mcp", "pmb_mcp"):
                entry = servers.get(key)
                if not isinstance(entry, dict):
                    continue
                headers = entry.get("headers")
                if not isinstance(headers, dict):
                    continue
                raw = headers.get("Authorization") or headers.get("authorization")
                if not isinstance(raw, str):
                    continue
                value = raw.strip()
                if value.lower().startswith("bearer "):
                    value = value[7:].strip()
                return value or None
    return None


def check_lane_credentials():
    """Does every lane's BAKED pmb token still match the live daemon token?

    This is the check whose absence let the 2026-11-11 outage pass a green
    gate. The distinction it draws is the one that matters:

      * a lane with NO baked token is fine -- it relies on the proxy to
        resolve the live token (see tools/pmb_token.py), which is the shape
        upstream pmb documents and the shape that cannot go stale;
      * a lane with a token that MATCHES the live one is fine;
      * a lane with a token that DIFFERS is BROKEN -- that lane's pmb tools
        are dead until the token is refreshed, and only that lane's.

    Reported per lane, because "some lane is broken" is not actionable and
    "kilo's pmb entry holds a token the daemon no longer accepts" is.
    """
    live = _live_daemon_token()
    if not live:
        return WARN, "no daemon token on disk to compare against", ""
    stale, absent, ok = [], [], []
    for path, label in ((KILO_CONF, "kilo"), (FREEBUFF_CONF, "freebuff"),
                         (OPENCODE_CONF, "opencode"),
                         (OPENCODE_KILO_CONF, "opencode-kilo")):
        if not path.exists():
            continue
        doc, _err = read_config(path)
        if doc is None:
            continue
        baked = pmb_baked_token(doc)
        if baked is None:
            absent.append(label)
        elif not hmac.compare_digest(baked, live):
            stale.append(label)
        else:
            ok.append(label)
    if stale:
        return FAIL, ("baked pmb token does not match the live daemon token "
                      "for: %s (those lanes' pmb tools are dead until the "
                      "token is refreshed; the proxy resolves the live token "
                      "for clients that carry none)" % ", ".join(stale)), \
            ("refresh the token in %s, or remove the Authorization header so "
             "the proxy resolves it at runtime" % ", ".join(stale))
    detail = "%d lane(s) with a live token: %s" % (len(ok), ", ".join(ok) or "-")
    if absent:
        detail += "; %d relying on the proxy: %s" % (len(absent),
                                                     ", ".join(absent))
    return PASS, detail, ""


def unfloored_servers(doc):
    """[(key, url)] for every ENABLED entry aimed at the raw daemon.

    pmb_entry only recognises keys named pmb / pmb-mcp / pmb_mcp, so an alias
    such as `pmb_direct` (measured in kilo.jsonc, 2026-10-31) sent a lane
    straight at the daemon port and passed this gate untouched. Any enabled entry
    whose url is the daemon bypasses the relevance floor, whatever it is called.
    A disabled entry is ignored: it cannot be called.
    """
    daemon_port = discover_daemon_port()
    if not isinstance(doc, dict):
        return []
    found = []
    for holder in ("mcp", "mcpServers"):
        servers = doc.get(holder)
        if not isinstance(servers, dict):
            continue
        for key, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("enabled", True) is False:
                continue
            url = entry.get("url")
            if not url:
                continue
            if str(url).rstrip("/").endswith(":%d/mcp" % daemon_port):
                found.append((key, str(url)))
    return found


def lane_config_status(path, label):
    """Check one lane's config routes pmb through the floor proxy."""
    daemon_port = discover_daemon_port()
    if not path.exists():
        return WARN, "%s: not present at %s" % (label, path), ""
    doc, err = read_config(path)
    if doc is None:
        return FAIL, "%s: %s" % (label, err), \
            "fix the syntax; a failed parse discards the WHOLE file and no server loads"
    url, enabled = pmb_entry(doc)
    if not url:
        return WARN, "%s: no pmb entry" % label, ""
    if enabled is False:
        return FAIL, "%s: pmb disabled" % label, "set enabled true or remove the entry"
    if not FLOOR_ENABLED:
        return WARN, "%s: floor proxy disabled in config" % label, ""
    if not str(url).rstrip("/").endswith(":%d/mcp" % PROXY_PORT):
        return FAIL, "%s: pmb -> %s" % (label, url), \
            "point it at %s (the floor proxy), not the daemon at :%d" % (PROXY_URL, daemon_port)
    if not LANES_ROUTE_THROUGH_FLOOR:
        return WARN, "%s: lanes_route_through_floor is false" % label, ""
    unfloored = unfloored_servers(doc)
    if unfloored:
        named = ", ".join("%s -> %s" % (k, u) for k, u in unfloored)
        return FAIL, "%s: unfloored pmb endpoint (%s)" % (label, named), \
            "route every pmb entry through %s; :%d is the raw daemon and skips the floor" % (
                PROXY_URL, daemon_port)
    return PASS, "%s: pmb -> :%d" % (label, PROXY_PORT), ""


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
def check_daemon(fix):
    daemon_port = discover_daemon_port()
    daemon_url = "http://127.0.0.1:%d/mcp" % daemon_port
    if not port_open(daemon_port):
        if not fix:
            return FAIL, "127.0.0.1:%d closed" % daemon_port, "run --fix or `pmb daemon start`"
        note = start_daemon()
        if not port_open(daemon_port):
            return FAIL, note, "`pmb daemon start` then re-run"
        return FIXED, "was down; %s" % note, ""
    t0 = time.time()
    ok, info = Mcp(daemon_url).initialize()
    ms = int((time.time() - t0) * 1000)
    if not ok:
        return FAIL, "port open, initialize failed: %s" % info, \
            "check the bearer token in %s" % PMB_TOKEN
    return PASS, "initialize %s in %d ms" % (
        info.get("name", "?") if isinstance(info, dict) else "ok", ms), ""


def check_proxy(fix):
    daemon_port = discover_daemon_port()
    daemon_url = "http://127.0.0.1:%d/mcp" % daemon_port
    if not port_open(PROXY_PORT):
        if not fix:
            return FAIL, "127.0.0.1:%d closed" % PROXY_PORT, "run --fix to start the floor proxy"
        note = start_proxy()
        if not port_open(PROXY_PORT):
            return FAIL, note, "start it by hand: %s %s" % (PMB_VENV_PY, PROXY_SCRIPT)
        return FIXED, "was down; %s" % note, ""
    client = Mcp(PROXY_URL)
    ok, info = client.initialize()
    if not ok:
        return FAIL, "initialize failed: %s" % info, "the proxy is up but not speaking MCP"
    # DIFFERENTIAL, not threshold-based. Ask the daemon and the proxy the same
    # noise query: if the proxy returns FEWER rows, filtering is live, whatever
    # the thresholds happen to be. A threshold assertion would false-fail the
    # day a nonce happens to score high (measured: 1 nonce in 15 reached 0.836)
    # and would say nothing at all about whether rows are being filtered.
    nonce = "zzq" + secrets.token_hex(6)
    proxy_payload = client.call("recall", {"query": nonce, "top_k": 5})
    if proxy_payload.get("_error"):
        return FAIL, "recall failed: %s" % str(proxy_payload["_error"])[:160], \
            "the proxy passes non-recall tools through; a recall failure is upstream"
    # The daemon leg needs its OWN session: streamable HTTP rejects a tools/call
    # from a client that never initialized with `400 Missing session ID`, and a
    # differential against that is not a comparison of two stores.
    daemon_client = Mcp(daemon_url)
    d_ok, d_info = daemon_client.initialize()
    if not d_ok:
        return FAIL, "daemon initialize failed: %s" % str(d_info)[:140], \
            "the daemon leg must hold a session before the differential is comparable"
    daemon_payload = daemon_client.call("recall", {"query": nonce, "top_k": 5})
    p_rows = proxy_payload.get("results") or []
    d_rows = daemon_payload.get("results") or []
    if daemon_payload.get("_error"):
        return FAIL, "daemon recall failed: %s" % str(
            daemon_payload["_error"])[:120], "fix the daemon leg first"
    d_best = max([r.get("score") or 0.0 for r in d_rows if isinstance(r, dict)] or [0.0])
    if d_rows and len(p_rows) >= len(d_rows):
        return FAIL, ("floor NOT applied: daemon %d row(s) (best %.2f) -> proxy %d"
                      % (len(d_rows), d_best, len(p_rows))), \
            "is 8766 really the proxy, and is PMB_RECALL_FLOOR non-zero?"
    stat = client.call("stats", {})
    if stat.get("_error"):
        return FAIL, "recall floored, but `stats` passthrough failed: %s" % \
            str(stat["_error"])[:120], "the proxy must not alter unscored tools"
    return PASS, ("filtering live: daemon %d row(s) (best %.2f) -> proxy %d; "
                  "stats passes through" % (len(d_rows), d_best, len(p_rows))), ""


def check_proxy_processes(fix, procs):
    """Exactly ONE instance, counted by instance and not by process.

    Every interpreter on this host re-executes the script as a CHILD (measured:
    py315t gives python.exe -> python3.15t.exe, .pmb-venv gives python.exe ->
    python.exe, both with the same argv). So one instance is reliably TWO
    processes, and "exactly one process" would fail on a perfectly healthy
    stack. An instance's identity is its ROOT: the process whose parent is not
    itself running the script.
    """
    running = procs_running(PROXY_SCRIPT, procs)
    pid_set = {p["pid"] for p in running}
    roots = [p for p in running if p["ppid"] not in pid_set]
    if not running:
        if not port_open(PROXY_PORT):
            return FAIL, "no floor proxy process and port closed", "run --fix"
        return FAIL, "port open but no pmb_floor_proxy.py process", \
            "something else owns :%d" % PROXY_PORT
    if len(roots) > 1:
        if not fix:
            return (FAIL, "%d floor proxy instances: %s" % (
                len(roots), [p["pid"] for p in roots]),
                ("run --fix; only one may own :%d (two would split traffic "
                 "across different thresholds)" % PROXY_PORT))
        for proc in running:
            kill(proc["pid"])
        time.sleep(0.8)
        note = start_proxy()
        return FIXED, "killed %d process(es) / %d instance(s), %s" % (
            len(running), len(roots), note), ""
    proc = roots[0]
    mtime = file_mtime(PROXY_SCRIPT)
    if mtime and proc["created"] and proc["created"] < mtime:
        if not fix:
            return FAIL, "pid %s started %s, script edited %s" % (
                proc["pid"], proc["created"].strftime("%H:%M:%S"),
                mtime.strftime("%H:%M:%S")), "run --fix to reload it"
        kill(proc["pid"])
        time.sleep(0.8)
        note = start_proxy()
        return FIXED, "stale pid reloaded: %s" % note, ""
    return PASS, "1 instance (pid %s, %d process(es) with its re-exec child), " \
        "started %s" % (proc["pid"], len(running),
                        proc["created"].strftime("%H:%M:%S") if proc["created"] else "?"), ""


def read_proxy_stamp():
    try:
        return json.loads(PROXY_STAMP.read_text(encoding="utf-8")), ""
    except OSError:
        return None, "no provenance stamp at %s" % PROXY_STAMP
    except ValueError as exc:
        return None, "unreadable provenance stamp (%s)" % exc


def check_floor_origin(fix, procs):
    """Is the stack warm because a DURABLE starter ran, or because a session did?

    Every other check answers "does it work now". This one answers "who keeps
    it warm", and the two can disagree for hours. Measured 2026-10-31: a
    previous session's pams spawned the floor proxy, so :8766 answered
    initialize and the differential passed, while nothing on this host would
    ever start it again -- and the next cold client start marked pmb failed for
    the whole session (opencode gives an unreachable endpoint three attempts in
    1.65 s). So a stack that is warm only because a session happened to start it
    is reported as a FAIL, not a pass.

    The proxy cannot infer its starter, so each starter declares it in
    PMB_FLOOR_ORIGIN and the proxy stamps origin+pid to PROXY_STAMP on bind.
    """
    running = procs_running(PROXY_SCRIPT, procs)
    if not running:
        return WARN, "no proxy process to attribute", ""
    live = {p["pid"] for p in running}
    stamp, err = read_proxy_stamp()
    if stamp is None:
        return FAIL, "%s (pid(s) %s predate PMB_FLOOR_ORIGIN)" % (
            err, sorted(live)), "reload it from the durable path: run --fix"
    origin = str(stamp.get("origin") or "?")
    if stamp.get("pid") not in live:
        return FAIL, ("stamp names pid %s (%s) but the live proxy is %s: the "
                      "running instance was started outside the declared path"
                      % (stamp.get("pid"), origin, sorted(live))), \
            "run --fix to restart it from the logon item"
    if origin != DURABLE_ORIGIN:
        detail = ("started by %s: warm only because that session started it; "
                  "nothing recreates it after a reboot" % origin)
        if not fix:
            return FAIL, detail, ("run `%s` (or --fix) so a reboot recreates "
                                  ":%d" % (PROXY_STARTUP.name, PROXY_PORT))
        for proc in running:
            kill(proc["pid"])
        time.sleep(0.8)
        note = floor_start_via_startup()
        deadline = time.time() + 6
        while time.time() < deadline:
            again, _ = read_proxy_stamp()
            if (again and str(again.get("origin")) == DURABLE_ORIGIN
                    and again.get("pid") not in live and port_open(PROXY_PORT)):
                return FIXED, "was %s; %s" % (origin, note), ""
            time.sleep(0.4)
        return FAIL, "was %s; %s did not produce a logon-stamped proxy" % (
            origin, note), ("start it by hand from %s" % PROXY_STARTUP)
    if not PROXY_STARTUP.exists():
        return FAIL, ("stamp says logon but %s is gone: nothing would recreate "
                      "this proxy" % PROXY_STARTUP.name), \
            "restore the Startup item, or accept a session-scoped :%d" % PROXY_PORT
    return PASS, "started by the logon Startup item (pid %s, %s)" % (
        stamp.get("pid"), stamp.get("started") or "?"), ""


def check_supervisor(fix, procs):
    running = procs_running(SUPERVISOR_SCRIPT, procs)
    if not running:
        return WARN, "no supervisor_mcp.py process in this lane", ""
    mtime = file_mtime(SUPERVISOR_SCRIPT)
    stale = [p for p in running
             if mtime and p["created"] and p["created"] < mtime]
    if not stale:
        startup = running[0]["created"]
        return PASS, "%d process(es), newest %s >= edit %s" % (
            len(running), startup.strftime("%H:%M:%S") if startup else "?",
            mtime.strftime("%H:%M:%S") if mtime else "?"), ""
    first = stale[0]
    return BLOCKED, ("%d of %d process(es) predate the edit: pid %s started %s, "
                     "script edited %s" % (
                         len(stale), len(running), first["pid"],
                         first["created"].strftime("%H:%M:%S"),
                         mtime.strftime("%H:%M:%S"))), \
        ("RESTART THE CLIENT. Its stdio children are running old code; a file fix "
         "is not a live fix. Do not kill them from here.")


def check_pams(fix, procs):
    running = procs_running(PAMS_SCRIPT, procs)
    if not running:
        return BLOCKED, "pams_server_launcher.py is not running", \
            ("RESTART THE CLIENT so it respawns pams. Until then nothing keeps "
             "the floor proxy warm on :%d." % PROXY_PORT)
    mtime = file_mtime(PAMS_SCRIPT)
    stale = [p for p in running if mtime and p["created"] and p["created"] < mtime]
    if stale:
        return BLOCKED, "pid %s started %s, launcher edited %s" % (
            stale[0]["pid"], stale[0]["created"].strftime("%H:%M:%S"),
            mtime.strftime("%H:%M:%S")), "RESTART THE CLIENT"
    return PASS, "pid %s started %s" % (
        running[0]["pid"], running[0]["created"].strftime("%H:%M:%S")), ""


def check_lane_configs(fix):
    results = []
    for path, label in ((KILO_CONF, "kilo"), (FREEBUFF_CONF, "freebuff"),
                        (OPENCODE_CONF, "opencode"),
                        (OPENCODE_KILO_CONF, "opencode-kilo")):
        results.append(lane_config_status(path, label))
    fails = [r for r in results if r[0] == FAIL]
    if fails:
        return FAIL, "; ".join(r[1] for r in fails), fails[0][2]
    detail = ", ".join(r[1] for r in results if r[0] == PASS) or "no lane config found"
    return PASS, detail, ""


def check_agent_orders(fix):
    """Every generated standing-order block must match its single source.

    Why this is a gate check and not a convention: on 2026-10-30 the MCP gate
    standing order lived in four instruction files, the gate underneath it was
    rewritten the same day, and all four copies went stale within hours -- so
    every session opened on instructions describing a check that no longer
    existed. Drift in a pushed rule file is the same class of defect as a stale
    stdio child: nothing raises, the answer is just wrong.
    """
    if not ORDERS_SYNC.exists():
        return WARN, "agent_orders_sync.py not found", ""
    try:
        # --wsl: measured 2026-10-31, this check reported "24 target(s) in sync"
        # while the Ubuntu and Kali lanes carried 10 blocks the flag was the only
        # way to see. A startup gate that verifies the half it can reach tells a
        # lane its orders are current when they are absent from two environments.
        proc = subprocess.run([sys.executable, "-X", "utf8", str(ORDERS_SYNC),
                               "--check", "--wsl"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except Exception as exc:                       # recorded, not swallowed
        return WARN, "could not run sync check: %s" % exc, ""
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        last = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        return PASS, last[0].replace("agent-orders: ", ""), ""
    detail = "; ".join(ln.strip() for ln in out.splitlines() if ln.startswith("DRIFT")) or out.strip()[:200]
    if fix:
        subprocess.run([sys.executable, "-X", "utf8", str(ORDERS_SYNC), "--apply", "--wsl"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        again = subprocess.run([sys.executable, "-X", "utf8", str(ORDERS_SYNC),
                                "--check", "--wsl"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if again.returncode == 0:
            return FIXED, "drift rewritten from source", ""
    return FAIL, detail, "python C:/Azazael/azazael/tools/agent_orders_sync.py --apply --wsl"


def check_context_servers(procs):
    """Every server the lane CONFIGURES must resolve, not three names that are up.

    Measured 2026-11-01: the previous version reported "running: engram,
    codegraph, barta" -- three of the eight entries in kilo.jsonc, chosen by
    hand. scrapling, pams, supervisor and both pmb endpoints were never
    examined, so an entry pointing at an executable that does not exist (the
    state a restart turns into a dead server) passed this gate untouched.
    The check now reads the lane's own config through mcp_eight_verify in
    --fast mode: no process is spawned here, and an enabled entry whose
    command does not resolve, or whose endpoint is closed or aimed at the raw
    daemon on the discovered port, is a FAIL with the path named.
    """
    try:
        import mcp_eight_verify as eight
    except Exception as exc:                       # recorded, not swallowed
        return WARN, "mcp_eight_verify.py unavailable (%s)" % exc, ""
    lanes = [(KILO_CONF, "kilo"), (FREEBUFF_CONF, "freebuff"),
             (OPENCODE_CONF, "opencode"), (OPENCODE_KILO_CONF, "opencode-kilo")]
    rows = []
    for path, label in lanes:
        if not path.exists():
            continue
        rows.extend((label, row) for row in eight.status_for(path, procs=procs, fast=True))
    if not rows:
        return WARN, "no lane config found to read", ""
    bad = [(label, row) for label, row in rows if row.state == eight.FAIL]
    if bad:
        label, row = bad[0]
        detail = "%s/%s: %s" % (label, row.name, row.detail)
        if len(bad) > 1:
            detail += " (+%d more)" % (len(bad) - 1)
        return FAIL, detail, row.remedy or "fix the entry in the lane config"
    per_lane: Dict[str, List[str]] = {}
    for label, row in rows:
        per_lane.setdefault(label, []).append(row.state)
    parts = []
    for label in sorted(per_lane):
        states = per_lane[label]
        counts: Dict[str, int] = {}
        for state in states:
            counts[state] = counts.get(state, 0) + 1
        parts.append("%s %d entr%s (%s)" % (
            label, len(states), "y" if len(states) == 1 else "ies",
            ", ".join("%s %d" % (state.lower(), n) for state, n in sorted(counts.items()))))
    return PASS, "; ".join(parts), ""


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def run(fix=False):
    results = []
    results.append(("pmb-daemon",) + check_daemon(fix))
    results.append(("floor-proxy",) + check_proxy(fix))
    procs = processes()
    results.append(("proxy-singleton",) + check_proxy_processes(fix, procs))
    if fix:
        # killing a duplicate changes the process table; re-read once
        procs = processes()
    results.append(("floor-origin",) + check_floor_origin(fix, procs))
    results.append(("supervisor-fresh",) + check_supervisor(fix, procs))
    results.append(("pams-running",) + check_pams(fix, procs))
    results.append(("lane-config",) + check_lane_configs(fix))
    results.append(("lane-credentials",) + check_lane_credentials())
    results.append(("orders-in-sync",) + check_agent_orders(fix))
    results.append(("lane-context",) + check_context_servers(procs))
    return results


def _label(status):
    """Fixed-width status label. Padded, so columns line up and no prefix hack."""
    return "[%-7s]" % status


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    fix = "--fix" in args
    quiet = "--quiet" in args
    as_json = "--json" in args

    started = time.time()
    results = run(fix)

    if as_json:
        print(json.dumps({"ok": not any(r[1] == FAIL for r in results),
                          "elapsed_s": round(time.time() - started, 2),
                          "checks": [{"name": r[0], "status": r[1], "detail": r[2],
                                      "remedy": r[3]} for r in results]},
                         indent=2))
    else:
        daemon_port = discover_daemon_port()
        print("MCP STARTUP GATE  (floor proxy :%d -> daemon :%d)" % (PROXY_PORT, daemon_port))
        for name, status, detail, remedy in results:
            if quiet and status == PASS:
                continue
            print("%s %-17s %s" % (_label(status), name, detail))
            if remedy:
                print("    %-17s -> %s" % ("", remedy))
        counts = {}
        for _, status, _, _ in results:
            counts[status] = counts.get(status, 0) + 1
        print("gate: " + " ".join("%s=%d" % (k, v) for k, v in sorted(counts.items()))
              + " in %.1fs" % (time.time() - started))

    for name, status, detail, remedy in results:
        if status in (FAIL, BLOCKED):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
