#!/usr/bin/env python3
"""Prove every MCP server a lane will spawn, one row per server.

WHY THIS EXISTS
---------------
The startup gate reports `lane-context  running: engram, codegraph, barta`.
Measured 2026-11-01: the Kilo lane configures **eight** servers --

    barta-scrape, codegraph, engram, pams, pmb, pmb_direct, scrapling, supervisor

-- and five of them had never been proven to answer a single request. A
process being present says a command started; it does not say the server
speaks MCP, and it says nothing at all about the entries that are not running.
A hand-copied path table is worse than no table: the first draft of this file
declared `scrapling` MISSING because the path had been transcribed by hand
while the config pointed somewhere else and was correct.

So this tool reads the lane's own config file, and for each entry proves one
of exactly five things:

  SERVING       an `initialize` and a `tools/list` came back within the deadline
  OWNED         a client-owned stdio singleton is running and is NEWER than its
                script (spawning a second copy would double-bind its port, so
                the proof is the live child, not a probe)
  DISABLED      `enabled: false` -- named, never counted as a pass
  CONFIG-ONLY   `--fast`: command/url resolves, nothing spawned
  FAIL          command missing, url closed, no answer, or an enabled entry
                aimed at the raw daemon on :8765 (it bypasses the floor)

NEGATIVE CONTROL
----------------
`--control` (on by default) runs the same stdio probe against a bogus command
and REQUIRES it to fail. A probe that cannot fail proves nothing, so a control
that passes is reported as UNVALIDATED and the exit code is 2.

    python tools/mcp_eight_verify.py                 # full proof, kilo lane
    python tools/mcp_eight_verify.py --lane freebuff
    python tools/mcp_eight_verify.py --fast          # config only, for a gate
    python tools/mcp_eight_verify.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

DAEMON_PORT = 8765
PROXY_PORT = 8766

#: Servers whose stdio endpoint is a singleton the CLIENT owns. Spawning a
#: second one is not a test, it is a second server: pams binds its own port
#: (`port_manager` + `mcp_server_port.json`) and marks the file inactive on
#: exit, so a probe copy would fight the live one for the same port.
CLIENT_OWNED = ("pams",)

#: Lane config paths come from the gate, which already owns them. Hard-coding
#: them here produced a freebuff path that does not exist on this host
#: (~/.config/freebuff/freebuff.json) while the gate was reading
#: ~/.agents/mcp.json -- a lane reported "unreadable" instead of verified.
LANES = {
    "kilo": Path.home() / ".config" / "kilo" / "kilo.jsonc",
    "freebuff": Path.home() / ".agents" / "mcp.json",
    "opencode": Path.home() / ".config" / "opencode" / "opencode.json",
    "opencode-kilo": Path.home() / ".config" / "opencode" / "kilo.jsonc",
}
try:                                            # prefer the gate's constants
    import mcp_startup_gate as _gate
    LANES.update({"kilo": Path(_gate.KILO_CONF),
                  "freebuff": Path(_gate.FREEBUFF_CONF),
                  "opencode": Path(_gate.OPENCODE_CONF),
                  "opencode-kilo": Path(_gate.OPENCODE_KILO_CONF)})
except Exception as exc:
    note_failure("tools/mcp_eight_verify.py", exc)
    pass

SERVING = "SERVING"
OWNED = "OWNED"
DISABLED = "DISABLED"
CONFIG_ONLY = "CONFIG-ONLY"
FAIL = "FAIL"


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
def read_jsonc(path: Path) -> Tuple[Optional[dict], str]:
    """Parse a JSONC lane config. A parse failure is a finding, not an error."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, "unreadable (%s)" % exc
    import re
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        return json.loads(text), ""
    except ValueError as exc:
        return None, "invalid JSON (%s)" % exc


def servers(doc: dict) -> Dict[str, dict]:
    for holder in ("mcp", "mcpServers"):
        block = doc.get(holder)
        if isinstance(block, dict):
            return dict(block)
    return {}


def command_of(entry: dict) -> List[str]:
    cmd = entry.get("command")
    if isinstance(cmd, str):
        return [cmd] + [str(a) for a in (entry.get("args") or [])]
    if isinstance(cmd, list):
        return [str(a) for a in cmd] + [str(a) for a in (entry.get("args") or [])]
    return []


def resolve_executable(cmd: Sequence[str]) -> Tuple[Optional[str], str]:
    """Absolute path of the program, or the reason it cannot be resolved.

    `where` first: a bare name (`engram.exe`) is a PATH lookup, and reporting a
    bare name as "missing" is the transcription mistake this file exists to
    prevent. Windows will not run a bare name without .exe on the extension
    list the way bash would, so the extension is tried explicitly.
    """
    if not cmd:
        return None, "no command"
    head = cmd[0]
    direct = Path(head)
    if direct.is_file():
        return str(direct), ""
    found = shutil.which(head)
    if found:
        return found, ""
    if not os.path.splitext(head)[1]:
        found = shutil.which(head + ".exe")
        if found:
            return found, ""
    return None, "not found: %s" % head


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as exc:
        note_failure("tools/mcp_eight_verify.py", exc)
        return False


def url_parts(url: str) -> Tuple[str, int, str]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port, parsed.path or "/"


def is_unfloored(url: str) -> bool:
    """An enabled entry on the raw daemon bypasses the relevance floor."""
    return str(url).rstrip("/").endswith(":%d/mcp" % DAEMON_PORT)


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site


# --------------------------------------------------------------------------
# process table
# --------------------------------------------------------------------------
def processes() -> List[dict]:
    """Windows process list with command lines, filtered in Python.

    A PowerShell query that names a script matches its own command line, so the
    filter is never in the query (measured: it returned 7 processes for one).
    On a host without PowerShell this returns [] and every LIVE column reads
    "-" -- an absent reading, not a zero.

    Creation time is carried because a client-owned singleton can be running
    OLD code: the proof that a restart took effect is `created >= script mtime`.
    The gate already parses it (both the /Date(ms)/ and ISO forms), so it is
    reused rather than re-derived.
    """
    try:
        import mcp_startup_gate as gate

        rows = gate.processes()
        if rows:
            return [{"pid": r.get("pid"), "name": r.get("name") or "",
                     "cmd": r.get("cmd") or "", "created": r.get("created")}
                    for r in rows]
    except Exception as exc:
        note_failure("tools/mcp_eight_verify.py", exc)
        pass
    script = (
        "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,"
        "CommandLine | ConvertTo-Json -Compress"
    )
    try:
        # encoding= is not decoration: without it Windows decodes with the
        # platform codec, a non-ASCII command line raises inside subprocess's
        # reader thread, and the process list comes back EMPTY while looking
        # like "no servers are running" (tools/failure_evidence_gate.py counts
        # exactly this shape).
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=90)
    except Exception:
        return []
    out = (proc.stdout or "").strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    rows = data if isinstance(data, list) else [data]
    keep = []
    for row in rows:
        cmd = row.get("CommandLine") or ""
        if cmd:
            keep.append({"pid": row.get("ProcessId"),
                         "name": row.get("Name") or "",
                         "cmd": cmd})
    return keep


def live_count(needle: str, procs: Sequence[dict]) -> int:
    needle = needle.lower()
    return sum(1 for p in procs if needle in str(p["cmd"]).lower())


# --------------------------------------------------------------------------
# stdio probe
# --------------------------------------------------------------------------
@dataclass
class Probe:
    ok: bool
    detail: str
    tools: int = 0
    ms: int = 0
    stderr: str = ""


def _initialize_payload() -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "mcp-eight-verify", "version": "1"}}}


def _tools_payload() -> dict:
    return {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def _wait_for(reader: "queue.Queue", wanted_id: int,
              deadline: float) -> Tuple[Optional[dict], str]:
    """Next frame whose id matches, or ('', reason). Non-matching frames skipped."""
    end = time.time() + deadline
    seen: List[str] = []
    while True:
        left = end - time.time()
        if left <= 0:
            return None, "timed out; last frame: %s" % (seen[-1][:160] if seen else "none")
        try:
            line = reader.get(timeout=left)
        except queue.Empty:
            return None, "timed out; last frame: %s" % (seen[-1][:160] if seen else "none")
        if line is None:
            return None, "stream closed; last frame: %s" % (seen[-1][:160] if seen else "none")
        line = line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        seen.append(line)
        try:
            frame = json.loads(line)
        except ValueError as exc:
            note_failure("tools/mcp_eight_verify.py", exc)
            continue
        if frame.get("id") == wanted_id:
            return frame, ""


def probe_stdio(cmd: Sequence[str], cwd: Optional[str] = None,
                init_deadline: float = 20.0,
                tools_deadline: float = 15.0) -> Probe:
    """Spawn the server, complete `initialize`, then `tools/list`, then stop it.

    The child is always terminated in `finally`: a probe that leaks a server
    would leave a second unfloored instance running and pass the next probe
    against its own leftovers.
    """
    started = time.time()
    proc = None
    try:
        proc = subprocess.Popen(
            list(cmd), cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return Probe(False, "could not start: %s" % exc, ms=int((time.time() - started) * 1000))

    frames: "queue.Queue" = queue.Queue()

    def pump() -> None:
        try:
            for line in proc.stdout:          # type: ignore[union-attr]
                frames.put(line)
        finally:
            frames.put(None)

    errs: List[str] = []

    def pump_err() -> None:
        try:
            for line in proc.stderr:          # type: ignore[union-attr]
                errs.append(line.rstrip())
                if len(errs) > 40:
                    break
        except Exception as exc:
            note_failure("tools/mcp_eight_verify.py", exc)
            pass

    threading.Thread(target=pump, daemon=True).start()
    threading.Thread(target=pump_err, daemon=True).start()

    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(_initialize_payload()) + "\n")
        proc.stdin.flush()
        frame, why = _wait_for(frames, 1, init_deadline)
        if frame is None:
            return Probe(False, "initialize: " + why,
                         ms=int((time.time() - started) * 1000),
                         stderr="\n".join(errs[-6:]))
        if "error" in frame:
            return Probe(False, "initialize error: %s" % json.dumps(frame["error"])[:160],
                         ms=int((time.time() - started) * 1000),
                         stderr="\n".join(errs[-6:]))
        info = (frame.get("result") or {}).get("serverInfo") or {}
        init_ms = int((time.time() - started) * 1000)

        proc.stdin.write(json.dumps({"jsonrpc": "2.0",
                                     "method": "notifications/initialized"}) + "\n")
        proc.stdin.write(json.dumps(_tools_payload()) + "\n")
        proc.stdin.flush()
        frame, why = _wait_for(frames, 2, tools_deadline)
        if frame is None:
            return Probe(False, "tools/list: " + why, ms=init_ms,
                         stderr="\n".join(errs[-6:]))
        tools = ((frame.get("result") or {}).get("tools")) or []
        name = info.get("name") or Path(str(cmd[0])).name
        return Probe(True, "%s, %d tool(s)" % (name, len(tools)),
                     tools=len(tools), ms=init_ms)
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception as exc:
            note_failure("tools/mcp_eight_verify.py", exc)
            pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception as exc:
                note_failure("tools/mcp_eight_verify.py", exc)
                pass


# --------------------------------------------------------------------------
# http probe
# --------------------------------------------------------------------------
def _parse_body(raw: str, ctype: str) -> List[dict]:
    if "event-stream" in (ctype or ""):
        out = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    try:
                        out.append(json.loads(chunk))
                    except ValueError as exc:
                        note_failure("tools/mcp_eight_verify.py", exc)
                        continue
        return out
    try:
        doc = json.loads(raw)
    except ValueError:
        return []
    return doc if isinstance(doc, list) else [doc]


def pmb_token() -> str:
    """The daemon's bearer token. Path measured: ~/.pmb/daemon.token.

    A wrong path is not a missing credential, it is a 401 reported as "the
    endpoint answered nothing usable" -- which is what happened on the first
    run here. Reuse the gate's constant so the two cannot drift.
    """
    candidates = [Path.home() / ".pmb" / "daemon.token",
                  Path.home() / ".pmb" / "token"]
    try:
        import mcp_startup_gate as gate          # same directory, same host
        candidates.insert(0, Path(gate.PMB_TOKEN))
    except Exception as exc:
        note_failure("tools/mcp_eight_verify.py", exc)
        pass
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            note_failure("tools/mcp_eight_verify.py", exc)
            continue
        if text:
            return text
    return ""


def probe_http(url: str, deadline: float = 20.0) -> Probe:
    started = time.time()
    token = pmb_token()
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = "Bearer " + token

    def post(payload: dict, session: Optional[str] = None) -> Tuple[List[dict], Optional[str], str]:
        hdrs = dict(headers)
        if session:
            hdrs["Mcp-Session-Id"] = session
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=deadline) as resp:
                return (_parse_body(resp.read().decode("utf-8", "replace"),
                                    resp.headers.get("Content-Type", "")),
                        resp.headers.get("Mcp-Session-Id"), "")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            return [], None, "HTTP %d: %s" % (exc.code, body[:140] or exc.reason)
        except Exception as exc:
            return [], None, str(exc)

    frames, session, err = post(_initialize_payload())
    if err and not frames:
        return Probe(False, "initialize: " + err, ms=int((time.time() - started) * 1000))
    init = next((f for f in frames if f.get("id") == 1), None)
    if init is None:
        return Probe(False, "initialize: no frame for id 1 (%d frame(s))" % len(frames),
                     ms=int((time.time() - started) * 1000))
    if "error" in init:
        return Probe(False, "initialize error: %s" % json.dumps(init["error"])[:160],
                     ms=int((time.time() - started) * 1000))
    init_ms = int((time.time() - started) * 1000)
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    frames, _, err = post(_tools_payload(), session)
    if err and not frames:
        return Probe(False, "tools/list: " + err, ms=init_ms)
    frame = next((f for f in frames if f.get("id") == 2), None)
    if frame is None:
        return Probe(False, "tools/list: no frame for id 2", ms=init_ms)
    tools = ((frame.get("result") or {}).get("tools")) or []
    info = (init.get("result") or {}).get("serverInfo") or {}
    return Probe(True, "%s, %d tool(s)" % (info.get("name") or "server", len(tools)),
                 tools=len(tools), ms=init_ms)


# --------------------------------------------------------------------------
# per-server verdict
# --------------------------------------------------------------------------
@dataclass
class Row:
    name: str
    transport: str
    state: str
    detail: str
    live: str = "-"
    init_ms: str = "-"
    tools: str = "-"
    remedy: str = ""


_HEAD_CACHE: Dict[str, Optional[float]] = {}


def _head_time(directory: Path) -> Optional[float]:
    """Commit time of HEAD for the repo containing `directory`, or None."""
    key = str(directory)
    if key in _HEAD_CACHE:
        return _HEAD_CACHE[key]
    value: Optional[float] = None
    try:
        proc = subprocess.run(["git", "-C", str(directory), "log", "-1", "--format=%ct"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=20)
        if proc.returncode == 0 and proc.stdout.strip().isdigit():
            value = float(proc.stdout.strip())
    except Exception:
        value = None
    _HEAD_CACHE[key] = value
    return value


def artifact_note(cmd: Sequence[str], exe: str) -> str:
    """Age of any DATA file the server is handed, against HEAD.

    A server can answer perfectly and still be wrong: codegraph serves a graph
    file, and measured 2026-11-01 that file was 10.5 days old while the tree had
    commits from the same day. "Serving" would have been true and useless, which
    is the class of defect this estate keeps paying for -- a confident answer
    from a stale artifact. Reported, never failed: a stale data file is a
    rebuild, not a broken restart.
    """
    notes = []
    for arg in list(cmd)[1:]:
        path = Path(str(arg))
        if not path.is_file() or path.suffix.lower() in (".py", ".exe", ".dll", ".pyd"):
            continue
        try:
            age_h = (time.time() - path.stat().st_mtime) / 3600.0
        except OSError as exc:
            note_failure("tools/mcp_eight_verify.py", exc)
            continue
        note = "%s %.1f d old" % (path.name, age_h / 24.0)
        head = _head_time(path.parent)
        if head is not None and path.stat().st_mtime < head:
            note += " (STALE: HEAD is %.1f d newer)" % ((head - path.stat().st_mtime) / 86400.0)
        notes.append(note)
    return "; ".join(notes)


def _needle(cmd: Sequence[str], exe: str) -> str:
    """The file that identifies THIS server's process, not its interpreter."""
    for arg in list(cmd)[1:]:
        if str(arg).lower().endswith((".py", ".exe", ".cmd", ".bat")):
            return Path(str(arg)).name
    return Path(exe).name


def verify_entry(name: str, entry: dict, procs: Sequence[dict],
                 fast: bool, init_deadline: float) -> Row:
    if not isinstance(entry, dict):
        return Row(name, "?", FAIL, "entry is not an object",
                   remedy="every mcp entry must be an object with type/command/url")

    if entry.get("enabled", True) is False:
        note = "enabled: false"
        if entry.get("url") and is_unfloored(str(entry["url"])):
            note += " (:8765 raw daemon -- correct to keep disabled)"
        return Row(name, entry.get("type") or "?", DISABLED, note)

    url = entry.get("url")
    if url:
        host, port, path = url_parts(str(url))
        transport = "http %s:%d%s" % (host, port, path)
        if is_unfloored(str(url)):
            return Row(name, transport, FAIL,
                       "enabled entry on the raw daemon",
                       remedy="route pmb through :%d; :%d skips the relevance floor"
                              % (PROXY_PORT, DAEMON_PORT))
        if fast:
            open_now = port_open(host, port, timeout=1.0)
            return Row(name, transport, CONFIG_ONLY if open_now else FAIL,
                       "port open" if open_now else "%s:%d closed" % (host, port),
                       remedy="" if open_now else "start it: pmb daemon start / floor proxy")
        probe = probe_http(str(url), deadline=init_deadline)
        return Row(name, transport, SERVING if probe.ok else FAIL, probe.detail,
                   init_ms="%d" % probe.ms, tools=str(probe.tools),
                   remedy="" if probe.ok else "the endpoint answered nothing usable")

    cmd = command_of(entry)
    exe, why = resolve_executable(cmd)
    if exe is None:
        return Row(name, "stdio", FAIL, why,
                   remedy="fix the path in the lane config; this entry will not start")
    transport = "stdio"
    # The needle is the SCRIPT, not the interpreter. Measured 2026-11-01: a
    # needle of "python.exe" reported LIVE=25 for codegraph, barta and
    # supervisor alike -- it was counting every python on the box and would
    # have called a dead server live.
    needle = _needle(cmd, exe)
    count = live_count(needle, procs)
    live = "%d" % count

    if name in CLIENT_OWNED:
        if count == 0:
            return Row(name, transport + " (owned)", FAIL,
                       "client-owned singleton not running",
                       live=live,
                       remedy="RESTART THE CLIENT so it respawns %s" % name)
        script = next((a for a in cmd[1:] if str(a).lower().endswith(".py")), None)
        newest = max((p["created"] for p in procs
                      if needle.lower() in str(p["cmd"]).lower() and p.get("created")),
                     default=None)
        if script and newest is not None:
            try:
                mtime = Path(script).stat().st_mtime
                import datetime as _dt
                if newest.timestamp() < mtime:
                    return Row(name, transport + " (owned)", FAIL,
                               "child started %s, script edited %s" % (
                                   newest.strftime("%H:%M:%S"),
                                   _dt.datetime.fromtimestamp(mtime).strftime("%H:%M:%S")),
                               live=live, remedy="RESTART THE CLIENT")
            except OSError as exc:
                note_failure("tools/mcp_eight_verify.py", exc)
                pass
        return Row(name, transport + " (owned)", OWNED,
                   "live child%s; a probe copy would double-bind its port" % (
                       " started " + newest.strftime("%H:%M:%S") if newest else ""),
                   live=live)

    if fast:
        return Row(name, transport, CONFIG_ONLY, "command resolves: %s" % exe, live=live)

    cwd = str(Path(exe).parent) if Path(exe).is_file() else None
    if Path(exe).suffix.lower() in (".exe", ".bat", ".cmd"):
        cwd = None
    probe = probe_stdio(cmd, cwd=cwd, init_deadline=init_deadline)
    detail = probe.detail
    if not probe.ok and probe.stderr:
        detail += " | stderr: " + probe.stderr.splitlines()[-1][:120]
    artifacts = artifact_note(cmd, exe)
    if artifacts:
        detail += " | " + artifacts
    return Row(name, transport, SERVING if probe.ok else FAIL, detail, live=live,
               init_ms="%d" % probe.ms if probe.ok else "-",
               tools=str(probe.tools) if probe.ok else "-",
               remedy="" if probe.ok else "the server did not complete MCP initialize")


def control_probe(init_deadline: float) -> Tuple[bool, str]:
    """The negative control: a bogus command MUST fail the same probe."""
    bogus = str(Path(os.environ.get("TEMP", ".")) / "__no_such_mcp_server__.exe")
    probe = probe_stdio([bogus], init_deadline=min(init_deadline, 8.0))
    if probe.ok:
        return False, "UNVALIDATED: the probe accepted a command that does not exist"
    return True, "refused as required (%s)" % probe.detail.split("|")[0].strip()[:60]


def status_for(config: Path, procs: Optional[Sequence[dict]] = None,
               fast: bool = True, deadline: float = 20.0) -> List[Row]:
    """Rows for one lane config. The entry point a gate imports.

    `procs` is accepted so a caller that has already paid for the process
    table (the startup gate does) does not pay for it twice; with None it is
    read here, so a standalone caller never gets an empty LIVE column.
    """
    doc, err = read_jsonc(Path(config))
    if doc is None:
        return [Row("config", "?", FAIL, err,
                    remedy="fix the JSONC syntax; a parse failure discards every server")]
    procs = processes() if procs is None else procs
    return [verify_entry(name, entry, procs, fast, deadline)
            for name, entry in servers(doc).items()]


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def render(rows: List[Row], lane: str, path: Path, control: Tuple[bool, str],
           elapsed: float) -> str:
    lines = ["MCP SERVERS  lane=%s  (%s)" % (lane, path)]
    lines.append("%-14s %-22s %-5s %-7s %-5s %s"
                 % ("SERVER", "TRANSPORT", "LIVE", "INIT", "TOOLS", "STATE"))
    for r in rows:
        lines.append("%-14s %-22s %-5s %-7s %-5s %s%s"
                     % (r.name, r.transport, r.live, r.init_ms, r.tools, r.state,
                        "" if r.state in (SERVING, OWNED, CONFIG_ONLY) else "  <- " + r.detail))
    for r in rows:
        if r.detail and r.state in (SERVING, OWNED, CONFIG_ONLY):
            lines.append("    %-14s %s" % (r.name, r.detail))
        if r.remedy:
            lines.append("    %-14s -> %s" % (r.name, r.remedy))
    ok_control, note = control
    lines.append("control  %s  %s" % ("ok" if ok_control else "INVALID", note))
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r.state] = counts.get(r.state, 0) + 1
    lines.append("%d server(s): %s in %.1fs"
                 % (len(rows), ", ".join("%s=%d" % (k, counts[k]) for k in sorted(counts)),
                    elapsed))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    started = time.time()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lane", default="kilo", choices=sorted(LANES))
    parser.add_argument("--config", help="explicit lane config path")
    parser.add_argument("--fast", action="store_true",
                        help="config-level proof only: resolve command/url, spawn nothing")
    parser.add_argument("--deadline", type=float, default=20.0,
                        help="seconds allowed for initialize (default 20)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="only failures")
    args = parser.parse_args(list(argv) if argv is not None else None)

    path = Path(args.config) if args.config else LANES[args.lane]
    if not path.exists() and args.config is None:
        # A lane that is not installed on this host is not a defect, but it is
        # also not a pass: say which file is absent and verify nothing.
        print("MCP eight: lane %s is not present on this host (%s) -- 0 servers verified"
              % (args.lane, path))
        return 0
    doc, err = read_jsonc(path)
    if doc is None:
        print("MCP eight: %s: %s" % (path, err), file=sys.stderr)
        return 1

    entries = servers(doc)
    # The process table is read even in --fast: the CLIENT-OWNED verdict is a
    # statement about a live child, and skipping the read made pams report
    # "not running" on a host where it was running -- a false FAIL, which is
    # the same defect class as a false pass.
    procs = processes()
    rows = [verify_entry(name, entry, procs, args.fast, args.deadline)
            for name, entry in entries.items()]

    if args.fast:
        control = (True, "skipped in --fast: nothing was spawned")
    else:
        control = control_probe(args.deadline)

    failures = [r for r in rows if r.state == FAIL]
    payload = {
        "lane": args.lane,
        "config": str(path),
        "mode": "fast" if args.fast else "full",
        "ok": not failures,
        "control_ok": control[0],
        "servers": [{"name": r.name, "transport": r.transport, "state": r.state,
                     "live": r.live, "init_ms": r.init_ms, "tools": r.tools,
                     "detail": r.detail, "remedy": r.remedy} for r in rows],
        "elapsed_s": round(time.time() - started, 2),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if args.quiet:
            rows = [r for r in rows if r.state == FAIL] or rows
        print(render(rows, args.lane, path, control, time.time() - started))

    if not control[0]:
        return 2
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
