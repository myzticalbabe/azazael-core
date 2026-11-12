#!/usr/bin/env python
# coding: utf-8
"""Engram MCP restart guard: MAKE IT LOAD, and LOG IT WHEN IT DOES NOT.

WHY THIS EXISTS
    The Freebuff client resolves MCP servers ONCE at process start. It reads
    `mcp.json` from exactly three `.agents` directories:

        qC$ = () => [ cwd/.agents, cwd/../.agents, homedir/.agents ]

    and it records the outcome in that session's log:

        ~/.config/manicode/projects/<proj>/chats/<session>/log.jsonl
        {"level":"DEBUG","timestamp":...,"pid":...,
         "data":{"mcpServers":["codegraph","pams","pmb"],
                 "source":"<the file it read>"},
         "msg":"[agents] Loaded MCP servers from mcp.json"}

    That event only fires when the resulting server set is NON-EMPTY, and a
    schema/parse failure is reported with console.error ONLY under a debug flag:

        if (!P.success) { if (A) console.error("Invalid mcp.json at ..."); continue }

    So the realistic failure is SILENT: a malformed config removes servers and
    says nothing. This guard exists to make that impossible to miss.

WHAT IT DOES
    1. Validates the config strictly against the client's own zod shapes
       (both union members are strictObject: UNKNOWN KEYS ARE REJECTED).
    2. REPAIRS the engram entry if it is missing, malformed, or the file is
       invalid JSON - backing up the previous copy first. This runs at session
       start, so the file is guaranteed healthy before the NEXT client start.
    3. Reads the client's newest session log - merged across EVERY project dir
       holding that session id, because two dirs can share one start and only
       one of them carries the load event - and reports what actually loaded.
    4. Appends a durable record - including every error - to
       azazael/state/engram_mcp_restart_log.jsonl.
    5. NAMES ANY INTERPRETER PIN. An entry whose command/args point at a venv
       interpreter (`.venvs`, `python.exe`, `py315t`, `Python314`) instead of a
       compiled binary is a pin on an absolute path: invisible while the path
       exists, and fatal to EVERY pinned server at once when that interpreter is
       upgraded - silently, because the load event only fires on a non-empty
       server set. A pin whose absolute path still exists is reported as a
       warning; a pin whose absolute path is GONE is reported as an error,
       because that server cannot spawn. Re-pin procedure:
       `azazael/tools/repoint_315t.py` (dry run by default, --apply backs up) -
       a sibling of this file, and UNTRACKED as of 2026-10-11, so the report
       names it as unavailable when it is absent rather than printing a command
       a fresh clone cannot run.
       This file only DETECTS - it never rewrites a pin.

USAGE
    python azazael/tools/verify_engram_mcp.py [--no-repair] [--log-only]
    python azazael/tools/verify_engram_mcp.py --mark-restart

    --mark-restart writes a `restart_pending` record (config mtime + sha256)
    IMMEDIATELY BEFORE the client restart. The first post-restart guard run
    then compares the new session's start against that marker: a start that
    happened after the marker but still lacks engram is reported as
    ERROR/exit 2 (the restart happened and engram did NOT load), not the
    misleading NOT_LOADED/exit 1.

EXIT
    0 = engram loaded in the newest session (or marker written)
    1 = not loaded yet (usually: the client has not been restarted)
    2 = config could not be made loadable, OR a server cannot spawn: either the
        guarded entry's handshake failed, or a server is pinned to an ABSOLUTE
        interpreter path that no longer exists (M-4). A pin whose path still
        exists is a WARNING and does NOT change the exit code.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

CWD = Path(__file__).resolve().parent.parent.parent          # C:/Azazael
HOME = Path.home()
SERVER = "engram"

SCAN = [CWD / ".agents", CWD.parent / ".agents", HOME / ".agents"]
CONFIG = HOME / ".agents" / "mcp.json"                       # homedir entry wins

LOG_DIR = CWD / "azazael" / "state"
LOG = LOG_DIR / "engram_mcp_restart_log.jsonl"

# Canonical, known-good entry. Recreated verbatim if the live one drifts.
EXPECTED = {
    "type": "stdio",
    "command": "C:/Users/clint/go/bin/engram.exe",
    "args": ["mcp", "--tools=agent"],
}

# Transcribed from freebuff.exe. The union members are strictObject, so an
# unknown key is a hard failure rather than a silent strip.
STDIO_KEYS = {"type", "command", "args", "env"}
REMOTE_KEYS = {"type", "url", "params", "headers"}

# An MCP entry may name an INTERPRETER rather than a compiled binary. Bare
# fragments only: a literal drive path in this tuple would itself be a
# hardcoded_win_path anti-pattern finding, and `.venvs` matches both separators.
INTERPRETER_MARKERS = (".venvs", "python.exe", "pythonw.exe",
                       "py314", "py315t", "Python314", "Python315")
REPIN_REFERENCE = "azazael/tools/repoint_315t.py"

# Resolve the reference against the repo root this file already computes (CWD,
# line 80), so the label and the existence probe CANNOT drift apart: the string
# the report prints is the same one that gets probed. Deriving the path a second
# way - e.g. `Path(__file__).parent / "repoint_315t.py"` - would work today and
# silently disagree the moment either form is edited.
REPIN_TOOL = CWD / REPIN_REFERENCE


def repin_reference_note():
    """One line naming the re-pin procedure, or saying it is NOT AVAILABLE.

    A gate that instructs the operator to run a tool which is not in git gives
    advice a fresh clone cannot follow. MEASURED 2026-10-11: the reference below
    is correct, but the tool is UNTRACKED, so this guard used to end its pin
    report with a command that need not exist on the machine reading it.
    Presence is now reported instead of assumed. `Path.is_file()` swallows
    OSError internally, exactly like the `os.path.isfile` used in
    `report_pins`, so probing the path adds no bare handler.
    """
    if REPIN_TOOL.is_file():
        return ("    re-pin  : %s  (dry run by default; --apply writes with backups)"
                % REPIN_REFERENCE)
    return ("    re-pin  : %s is NOT PRESENT on this checkout - the re-pin "
            "procedure is UNAVAILABLE here; re-pin by hand and re-run this guard"
            % REPIN_REFERENCE)


def hdr(t):
    print()
    print("=" * 64)
    print(t)
    print("=" * 64)


# ---------------------------------------------------------------- validation

def validate_entry(entry):
    """Mirror the client's zod union. Returns a list of error strings."""
    errs = []
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    keys = set(entry)
    t = entry.get("type", "stdio")          # type has .default("stdio")
    if t == "stdio":
        extra = keys - STDIO_KEYS
        if extra:
            errs.append("strictObject rejects unknown key(s): %s" % sorted(extra))
        if not isinstance(entry.get("command"), str) or not entry.get("command"):
            errs.append("command must be a non-empty string")
        if "args" in entry and not (isinstance(entry["args"], list)
                                    and all(isinstance(a, str) for a in entry["args"])):
            errs.append("args must be string[]")
        if "env" in entry and not isinstance(entry["env"], dict):
            errs.append("env must be a record<string,string>")
    elif t in ("http", "sse"):
        extra = keys - REMOTE_KEYS
        if extra:
            errs.append("strictObject rejects unknown key(s): %s" % sorted(extra))
        if not isinstance(entry.get("url"), str) or not entry.get("url"):
            errs.append("url must be a non-empty string")
    else:
        errs.append("type %r matches neither union member" % t)
    return errs


#: Every caught-and-discarded failure lands here instead of vanishing. This tool's
#: contract is "never raise", so each handler still returns its documented
#: fallback -- but the loss is recorded AND reported, because a guard whose own
#: read failures are invisible cannot be trusted to report anything.
_LOSSES: list = []


def _note(where: str, exc: BaseException | None = None) -> None:
    """Record a discarded failure. Cannot raise: a list append is the sink."""
    _LOSSES.append("%s:%s" % (
        where, type(exc).__name__ if exc is not None else "unknown"))


def read_config(path):
    """Return (data, error)."""
    if not path.is_file():
        return None, "file does not exist"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as e:
        _note("read_config", e)
        return None, "invalid JSON: %s" % e


def is_absolute_path(value):
    """A leading slash or a `X:` drive letter. A bare name resolves via PATH."""
    if not isinstance(value, str) or not value:
        return False
    return value.startswith("/") or (len(value) > 1 and value[1] == ":")


def pinned_interpreters(data):
    """Every (server, where, value) whose command/args names an interpreter.

    Detection only. The guard must not rewrite a pin: choosing the replacement
    interpreter is a reviewed edit (`repoint_315t.py`), not something a session
    start should guess. `where` is `command` or `args[i]` so the message points
    at the exact key without re-reading the file.
    """
    out = []
    servers = (data or {}).get("mcpServers") or {}
    if not isinstance(servers, dict):
        return out
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        candidates = [("command", entry.get("command"))]
        args = entry.get("args")
        if isinstance(args, list):
            candidates += [("args[%d]" % i, a) for i, a in enumerate(args)]
        for where, value in candidates:
            if isinstance(value, str) and any(m in value for m in INTERPRETER_MARKERS):
                out.append((name, where, value))
    return out


def report_pins(data):
    """Print every interpreter pin. Returns (pins, errors).

    A pin that is an absolute path which still exists is a WARNING: it works
    today and breaks on the next interpreter upgrade. A pin that is an absolute
    path which is GONE is an ERROR - the same class as `command does not exist
    on disk`, because that server cannot spawn at all. A bare name is a warning
    only, since it resolves through PATH.
    """
    pins = pinned_interpreters(data)
    errs = []
    if not pins:
        print("  interpreter pins : none - no server names an interpreter")
        return pins, errs
    print("  interpreter pins : %d - each breaks on an interpreter upgrade" % len(pins))
    for name, where, value in pins:
        # os.path.isfile swallows OSError/ValueError internally (genericpath), so
        # this needs no handler of its own - the swallow inventory counts bare
        # handlers, and this module must not add one just to probe a path.
        exists = is_absolute_path(value) and os.path.isfile(value)
        if exists:
            print("    ~ %s.%s = %s" % (name, where, value))
            print("      present now; re-pin it before upgrading that interpreter")
        elif is_absolute_path(value):
            msg = ("server %r is pinned to an ABSOLUTE interpreter path that no "
                   "longer exists (%s = %s) - that server cannot start"
                   % (name, where, value))
            print("    !! %s" % msg)
            errs.append(msg)
        else:
            print("    ~ %s.%s = %s  (bare name, resolves via PATH)"
                  % (name, where, value))
    print(repin_reference_note())
    return pins, errs


def ensure_config(repair=True):
    """Validate the config and repair the engram entry. Returns (entry, messages, repairs)."""
    msgs, repairs = [], []
    snapshot_taken = False   # never write a KNOWN-BAD file into the .bak- namespace,
    data, err = read_config(CONFIG)   # because .bak-* is what recovery reads

    if err is not None:
        msgs.append("config unusable: %s" % err)
        if not repair:
            return None, msgs, repairs
        if CONFIG.is_file():
            bak = CONFIG.with_name(CONFIG.name + ".broken-" + time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(CONFIG, bak)
            snapshot_taken = True
            msgs.append("backed up the broken file to %s" % bak.name)
        # IMPORTANT: recreating from scratch would silently DESTROY every other
        # server in this file - pams, pmb, codegraph, and pmb's bearer token.
        # Recover from the newest readable backup instead; only fall back to an
        # empty skeleton when no usable backup exists.
        candidates = sorted(CONFIG.parent.glob(CONFIG.name + ".bak-*"), reverse=True)
        recovered = None
        for c in candidates:
            d2, e2 = read_config(c)
            if e2 is None and isinstance(d2, dict) and isinstance(d2.get("mcpServers"), dict):
                recovered, src = d2, c.name
                break
        if recovered is not None:
            data = recovered
            repairs.append("repaired unusable config, recovered from backup %s" % src)
            msgs.append("recovered %d server(s) from %s"
                        % (len(data.get("mcpServers") or {}), src))
        else:
            data = {"mcpServers": {}}
            repairs.append("recreated mcp.json from scratch (no usable backup)")
            msgs.append("!! no usable backup: recreating from scratch, other servers are lost")

    servers = data.setdefault("mcpServers", {})
    entry = servers.get(SERVER)
    errs = validate_entry(entry) if entry is not None else ["entry is missing"]

    if errs:
        msgs.append("engram entry invalid: %s" % "; ".join(errs))
        if repair:
            servers[SERVER] = dict(EXPECTED)
            repairs.append("restored the canonical engram entry")
            entry = servers[SERVER]
        else:
            return None, msgs, repairs
    else:
        msgs.append("engram entry validates against the client schema")

    if repairs:
        # Only snapshot when the current file is READABLE but wrong. If we already
        # set it aside as .broken-*, a second copy would put unparseable JSON into
        # the .bak-* namespace that recovery scans - a corrupt "last known good".
        if CONFIG.is_file() and not snapshot_taken:
            bak = CONFIG.with_name(CONFIG.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(CONFIG, bak)
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8", newline="") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        msgs.append("wrote repairs: %s" % ", ".join(repairs))

    # re-verify after any repair
    back, err2 = read_config(CONFIG)
    if err2:
        msgs.append("!! post-repair read failed: %s" % err2)
        return None, msgs, repairs
    entry = (back.get("mcpServers") or {}).get(SERVER)
    post = validate_entry(entry)
    if post:
        msgs.append("!! post-repair validation failed: %s" % "; ".join(post))
        return None, msgs, repairs

    if not Path(entry["command"]).exists():
        msgs.append("!! command does not exist on disk: %s" % entry["command"])
    else:
        msgs.append("command present on disk")
    return entry, msgs, repairs


# ------------------------------------------------------------ client session

def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception as e:
        _note("parse_ts", e)
        return None


def newest_sessions(limit=8):
    """Newest first: ~/.config/manicode/projects/*/chats/*/log.jsonl"""
    root = HOME / ".config" / "manicode" / "projects"
    out = []
    if not root.is_dir():
        return out
    for proj in root.iterdir():
        chats = proj / "chats"
        if not chats.is_dir():
            continue
        for sess in chats.iterdir():
            f = sess / "log.jsonl"
            if f.is_file():
                out.append((f.stat().st_mtime, f, proj.name, sess.name))
    out.sort(reverse=True, key=lambda x: x[0])
    return out[:limit]


def session_logs(sess, root=None):
    """(mtime, path, proj) for ONE session id, across every project dir.

    The client writes one log.jsonl per project dir, and two dirs can share a
    session id: measured 2026-10-31, projects/clint and projects/Desktop both
    held 2026-10-31T08-23-08.778Z while only Desktop logged the load event.
    Newest first.
    """
    root = Path(root) if root is not None else (HOME / ".config" / "manicode" / "projects")
    out = []
    if not root.is_dir():
        return out
    for proj in root.iterdir():
        f = proj / "chats" / sess / "log.jsonl"
        if f.is_file():
            try:
                out.append((f.stat().st_mtime, f, proj.name))
            except OSError as e:
                _note("session_logs/stat", e)
    out.sort(reverse=True, key=lambda x: x[0])
    return out


def newest_session(logs, root=None, reader=None):
    """The newest SESSION's load evidence, merged across project dirs.

    `logs` is newest_sessions() output ((mtime, path, proj, sess), newest
    first). Scoping by the newest FILE is wrong: two project dirs can share a
    session id, and the dir whose log happens to be newer is not necessarily
    the dir that logged the load event. Measured 2026-10-31, that reported a
    loaded stack (six servers answering that session) as "no servers loaded"
    on 29 of 573 runs, each persisting for the whole session.

    Returns the evidence dir's own values so classify_restart compares the
    start that actually loaded, plus `log_dirs` naming every dir holding a log
    for that session id. `servers` stays None only when NO dir logged an event
    - the one case the caller may call a failure. None when there are no logs.
    """
    if not logs:
        return None
    reader = reader or read_load_event
    sess = logs[0][3]
    siblings = session_logs(sess, root=root) or [logs[0][:3]]
    dirs = []
    for _m, _p, proj in siblings:
        if proj not in dirs:
            dirs.append(proj)
    for _mtime, path, proj in siblings:
        servers, source, started, pid = reader(path)
        if servers is not None:
            return {"proj": proj, "sess": sess, "path": str(path),
                    "servers": servers, "source": source,
                    "started": started, "pid": pid,
                    "lines": count_log_lines(path), "log_dirs": dirs}
    return {"proj": siblings[0][2], "sess": sess, "path": str(siblings[0][1]),
            "servers": None, "source": None, "started": None, "pid": None,
            "lines": count_log_lines(siblings[0][1]), "log_dirs": dirs}


def read_load_event(path):
    """(servers, source, started, pid) or (None,None,None,None) when absent.

    Use the event's OWN timestamp, never the file mtime: the client keeps
    appending to log.jsonl, so the mtime drifts forward past the config mtime
    and a pre-fix session would look post-fix.
    """
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            if "Loaded MCP servers" not in line:
                continue
            d = json.loads(line)
            data = d.get("data") or {}
            return (data.get("mcpServers"), data.get("source"),
                    parse_ts(d.get("timestamp")), d.get("pid"))
    except Exception as e:
        _note("session-log/read", e)
    return None, None, None, None


def count_log_lines(path):
    try:
        return sum(1 for _ in open(path, encoding="utf-8", errors="replace"))
    except Exception as e:
        _note("count_log_lines", e)
        return 0


def config_sha():
    """sha256 of the live config, or None. Used to prove no destructive write."""
    try:
        return hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    except Exception as e:
        _note("config_sha", e)
        return None


def latest_marker():
    """Newest `restart_pending` record from the durable log, or None.

    Never raises. A marker is written by --mark-restart immediately BEFORE
    the client restart; the post-restart guard compares the new session's
    start timestamp against it.
    """
    try:
        if not LOG.is_file():
            return None
        mark = None
        for line in open(LOG, encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except Exception as e:
                _note("json-line", e)
                continue
            if isinstance(d, dict) and d.get("event") == "restart_pending":
                mark = d
        return mark
    except Exception as e:
        _note("latest_marker", e)
        return None


def classify_restart(*, newest, cfg_mtime, marker_ts, config_path):
    """Pure decision: a start happened, engram is missing - error or not?

    Returns an error string when a client start demonstrably happened AFTER
    our baseline (the restart marker if present, else the config mtime) but
    engram is still absent from the loaded set, or when the client read a
    DIFFERENT mcp.json than the one holding the engram entry. Otherwise None
    (plain NOT_LOADED, or a case handled by the caller's other branches).

    Pure (no I/O) so it is testable with synthetic values without touching
    the client's authority log or the durable restart log.
    """
    if not newest or newest.get("servers") is None:
        return None  # no-evidence / silent-failure branches own these cases
    servers = newest.get("servers") or []
    if SERVER in [str(s).lower() for s in servers]:
        return None  # loaded: the PASS path
    src = newest.get("source")
    if src:
        try:
            same = Path(src).resolve() == Path(config_path).resolve()
        except Exception as e:
            _note("classify_restart/resolve", e)
            same = False
        if not same:
            return ("newest session read a DIFFERENT mcp.json (%s), not the "
                    "guarded %s that holds the engram entry - fix or remove "
                    "the shadowing file, then restart" % (src, config_path))
    started = newest.get("started")
    baselines = [(t, n) for t, n in ((marker_ts, "restart marker"),
                                     (cfg_mtime, "config mtime")) if t]
    if started and baselines:
        base, name = max(baselines, key=lambda x: x[0])
        if started > base:
            return ("newest session started AFTER the %s but engram is still "
                    "not in the loaded set %s - the restart HAPPENED and "
                    "engram did NOT load; read that session's log for "
                    "schema/parse errors" % (name, servers))
    return None


def mark_restart(repair=True):
    """Write the pre-restart marker. Run immediately BEFORE restarting."""
    hdr("RESTART MARKER - run this immediately BEFORE restarting the client")
    entry, msgs, repairs = ensure_config(repair=repair)
    for m in msgs:
        print("  " + m)
        if m.startswith("!!"):
            print("  " + m)
    if not entry:
        append_log({"event": "restart_pending", "verdict": "MARK_FAILED",
                    "config": str(CONFIG),
                    "errors": ["config could not be made loadable"]})
        print("  NOT MARKED - config not loadable; fix that first")
        return 2
    rec = {
        "event": "restart_pending",
        "verdict": "PENDING",
        "config": str(CONFIG),
        "config_mtime": CONFIG.stat().st_mtime if CONFIG.is_file() else None,
        "config_sha": config_sha(),
        "entry": entry,
        "repairs": repairs,
        "errors": [],
        "ts": datetime.now().astimezone().isoformat(),
        "note": ("restart the client NOW; the first post-restart guard run "
                 "compares the new session start against this marker and "
                 "reports PASS or ERROR/exit 2"),
    }
    if append_log(rec):
        print("  MARKED at %s (sha=%s)"
              % (rec["ts"], (rec["config_sha"] or "?")[:12]))
        print("  now restart the client; a restart that still lacks engram "
              "will be logged as ERROR/exit 2, not NOT_LOADED")
        return 0
    print("  !! could not write the marker to %s" % LOG)
    return 2


# -------------------------------------------------------------------- logging

def append_log(record):
    """Durable restart log. Never raises."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        record = dict(record)
        record.setdefault("ts", datetime.now().astimezone().isoformat())
        with open(LOG, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        _note("append_log", e)
        return False


# ------------------------------------------------------------------- handshake

def handshake(entry, timeout=30):
    """Spawn the configured argv and complete an MCP initialize."""
    argv = [entry["command"]] + list(entry.get("args") or [])
    req = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "verify_engram_mcp", "version": "2.0"}},
    }) + "\n"
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (entry.get("env") or {}).items()})
    t0 = time.time()
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, bufsize=1,
                             cwd=str(CWD), env=env, encoding="utf-8", errors="replace")
    except Exception as e:
        _note("handshake/spawn", e)
        return None, "spawn failed: %s" % e
    # The request MUST be written or the server waits forever and we just time
    # out - omitting this was a real bug in the first version of this file.
    try:
        p.stdin.write(req)
        p.stdin.flush()
    except Exception as e:
        try:
            p.kill()
        except Exception as ke:
            _note("handshake/kill-after-write-failure", ke)
        return None, "failed to send initialize: %s" % e

    got = {}

    def rd():
        for line in p.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except Exception as e:
                _note("json-line", e)
                continue
            if d.get("id") == 1:
                got["d"] = d
                got["t"] = time.time() - t0
                return

    th = threading.Thread(target=rd, daemon=True)
    th.start()
    th.join(timeout)
    try:
        p.kill()
    except Exception as e:
        _note("handshake/kill-final", e)
    if not got:
        return None, "no MCP initialize response within %ss" % timeout
    return got, None


# ------------------------------------------------------------------------ main

def main():
    if "--mark-restart" in sys.argv:
        return mark_restart(repair=("--no-repair" not in sys.argv))
    repair = "--no-repair" not in sys.argv
    log_only = "--log-only" in sys.argv
    errors, repairs = [], []
    ok = True

    hdr("1. CONFIG - make it loadable (the client reads this at next start)")
    entry, msgs, repairs = ensure_config(repair=repair)
    for m in msgs:
        print("  " + m)
        if m.startswith("!!"):
            errors.append(m)
    print("  config : %s" % CONFIG)
    if entry:
        print("  entry  : %s" % json.dumps(entry))
    else:
        ok = False
        errors.append("config could not be made loadable")

    print()
    print("  scan order (later wins): %s" % " < ".join(str(p) for p in SCAN))
    for p in SCAN:
        f = p / "mcp.json"
        note = "PRESENT" if f.is_file() else "absent"
        if f.is_file():
            d, e = read_config(f)
            names = sorted((d or {}).get("mcpServers", {}).keys()) if not e else ["<unreadable>"]
            note += "  servers=%s%s" % (names, "  !! %s" % e if e else "")
        print("    %-8s %s" % (note.split()[0], f))

    # M-4: an interpreter pin is the one config defect that is invisible today and
    # takes down every pinned server at once on an upgrade. Name it here, in the
    # section that already owns "will this config load".
    print()
    cfg_data, _ = read_config(CONFIG)
    pins, pin_errs = report_pins(cfg_data)
    for e in pin_errs:
        errors.append(e)
        ok = False

    hdr("2. WHAT THE CLIENT ACTUALLY LOADED (its own log - the authority)")
    logs = newest_sessions()
    if not logs:
        # No evidence is NOT success. Without a session log the guard cannot see
        # the client at all; reporting PASS/exit 0 here would be a false green.
        print("  no session log found; cannot confirm from the client side")
        errors.append("no session log found")
        ok = False

    newest = newest_session(logs)
    newest_sess = newest["sess"] if newest else None
    for mtime, path, proj, sess in logs:
        servers, source, started, pid = read_load_event(path)
        is_newest = sess == newest_sess
        loaded = bool(servers) and SERVER in [s.lower() for s in servers]
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)) if started else "?"
        print("  [%s] %s" % (proj, sess))
        print("      started     : %s" % stamp)
        if servers is None:
            print("      load event  : ABSENT")
            print("      servers     : <none - the event only fires when servers load>")
            print("      ^ this is the SILENT-failure signal: mcp.json was missing,")
            print("        invalid JSON, or failed schema validation at that start.")
        else:
            print("      source      : %s" % source)
            print("      servers     : %s" % servers)
        print("      engram?     : %s" % ("YES" if loaded else "NO"))
        if is_newest and servers is None and newest.get("servers") is not None:
            # A silent log in ONE dir is not a dead stack: the same session id
            # logged its load event in another project dir. Say which.
            print("      ^ this dir logged no load event; the same session id "
                  "logged one in [%s]" % newest["proj"])
        print()

    if newest is not None:
        # The verdict belongs to the SESSION, not to one dir's file: a load
        # event anywhere for that session id means the servers loaded.
        dirs = [d for d in (newest.get("log_dirs") or [])]
        if len(dirs) > 1:
            print("  newest session %s spans %d project-dir log(s): %s"
                  % (newest["sess"], len(dirs), ", ".join(dirs)))
        if newest.get("servers") is None:
            errors.append(
                "no project-dir log for the newest session (%s) logged an MCP "
                "load event - no servers loaded at that start (dirs checked: "
                "%s)" % (newest["sess"], ", ".join(dirs) or "<none>"))
        elif SERVER not in [str(s).lower() for s in newest["servers"]]:
            ok = False
        print()

    hdr("3. WILL THE CONFIGURED ENTRY START?")
    hs_err = None
    if log_only:
        print("  skipped (--log-only)")
    elif entry:
        argv = [entry["command"]] + list(entry.get("args") or [])
        print("  argv : %s" % argv)
        got, hs_err = handshake(entry)
        if got:
            res = got.get("d", {}).get("result", {})
            ins = res.get("instructions") or ""
            print("  initialize   : OK in %.2fs" % got["t"])
            print("  serverInfo   : %s" % res.get("serverInfo", {}))
            print("  instructions : %d chars  (stored by the SDK but never read -" % len(ins))
            print("                 the client never calls getInstructions(), so this")
            print("                 payload does NOT reach the system prompt)")
        else:
            print("  !! %s" % hs_err)
            errors.append(hs_err)
            ok = False
    else:
        print("  skipped: config not loadable")

    hdr("4. RESTART TIMING")
    cfg_mtime = CONFIG.stat().st_mtime if CONFIG.is_file() else None
    if cfg_mtime:
        print("  config mtime        : %s" % time.strftime("%Y-%m-%d %H:%M:%S",
                                                          time.localtime(cfg_mtime)))
    if newest and newest.get("started"):
        print("  newest session start: %s" % time.strftime("%Y-%m-%d %H:%M:%S",
                                                           time.localtime(newest["started"])))
        print("  newest session pid  : %s" % newest.get("pid"))
        if cfg_mtime and newest["started"] < cfg_mtime:
            print()
            print("  >> The newest session STARTED BEFORE the config was written.")
            print("     MCP servers resolve at process start, so a client restart")
            print("     is REQUIRED before engram can appear.")

    hdr("RESULT")
    if newest is not None:
        # A start AFTER our baseline that still lacks engram is a real
        # failure (the restart happened and engram did NOT load), not a
        # "restart pending" - without this the guard would report the
        # misleading NOT_LOADED/exit 1 forever.
        marker = latest_marker()
        r_err = classify_restart(
            newest=newest, cfg_mtime=cfg_mtime,
            marker_ts=parse_ts((marker or {}).get("ts")),
            config_path=str(CONFIG))
        if r_err is not None:
            print("  !! %s" % r_err)
            errors.append(r_err)
    if repairs:
        print("  REPAIRED: %s" % ", ".join(repairs))
    if _LOSSES:
        # A PASS that silently swallowed read failures is not the same claim as
        # a PASS that read everything it needed to. Print them above the verdict.
        print("  CAUGHT-AND-DISCARDED: %d failure(s) - this verdict rests on an "
              "incomplete read:" % len(_LOSSES))
        for loss in _LOSSES:
            print("    - %s" % loss)
    if ok:
        print("  PASS - engram is loaded. Tools appear prefixed e.g. engram__mem_save.")
        verdict = "PASS"
    elif errors:
        print("  ATTENTION - %d error(s):" % len(errors))
        for e in errors:
            print("    - %s" % e)
        verdict = "ERROR"
    else:
        print("  NOT YET LOADED. Most likely: the client has not been restarted")
        print("  since mcp.json changed. Restart, then run this again.")
        verdict = "NOT_LOADED"

    wrote = append_log({
        "event": "restart_check",
        "verdict": verdict,
        "config": str(CONFIG),
        "config_mtime": cfg_mtime,
        "entry": entry,
        "repairs": repairs,
        "errors": errors,
        "pinned_interpreters": pins,
        "losses": list(_LOSSES),
        "newest_session": newest,
    })
    print()
    print("  log : %s%s" % (LOG, "" if wrote else "  (!! could not write)"))

    return 0 if ok else (2 if errors else 1)


if __name__ == "__main__":
    sys.exit(main())
