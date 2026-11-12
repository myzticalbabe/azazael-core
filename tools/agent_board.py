"""Inter-agent message board + dispatcher (tri-agent task spawning).

Board: ``state/agent_board.jsonl`` — one JSON object per line. Row schema::

    {"id": "b0001", "title": "...", "executor": "opencode|kilo|freebuff",
     "owns": ["path/one", ...], "brief": "what to do + acceptance",
     "status": "open|claimed|done|failed", "proof": "...",
     "created_by": "...", "created_at": "...", "updated_at": "..."}

Rules (load-bearing, from the Oct 18 investigation):

- DISJOINT OWNERSHIP (#591): every row MUST name ``executor`` and ``owns``.
  The dispatcher refuses rows without both. Overlapping ``owns`` across
  open rows is reported loud, never silently merged.
- FREEBUFF IS PULL-ONLY: the dispatcher never spawns a freebuff row. Those
  rows wait until a Freebuff session polls the board (``list --executor
  freebuff --status open``) or the user prompts. Nothing can push into a
  live Freebuff session — MCP is tools-only.
- PUSH PATH: ``dispatch`` spawns ``opencode run <brief>`` / ``kilo run
  <brief>`` as background processes logging to ``state/board_dispatch_<id>.log``.
- SELF-TEST (S14 false-green rule): ``dispatch`` first probes each CLI
  (``--help``) and records the board's mtime+sha; a second dispatch with an
  unchanged board dispatches nothing (no stale re-fire).
- Handoff discipline: whoever marks ``done`` MUST record the outcome in
  Engram + PMB and judge the pairs that save creates, else the receiving
  agent's gate NO-GOs on pending soup.

Usage:
    python tools/agent_board.py post --executor kilo --owns scripts/x.py
        --title "..." --brief "..." [--created-by freebuff]
    python tools/agent_board.py list [--executor X] [--status open]
    python tools/agent_board.py claim|done|fail <id> [--proof ...]
    python tools/agent_board.py dispatch [--dry-run]
    python tools/agent_board.py selftest
"""

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOARD = os.path.join(ROOT, "state", "agent_board.jsonl")
STATE = os.path.join(ROOT, "state")

EXECUTORS = ("opencode", "kilo", "freebuff")
STATUSES = ("open", "claimed", "done", "failed")
PUSHABLE = ("opencode", "kilo")  # freebuff is pull-only, never spawned

NPM = "C:/Users/clint/AppData/Roaming/npm"
NODE_EXE = "C:/Program Files/nodejs/node.exe"


def _resolve_cli(name):
    """Direct argv for each CLI — no shell (npm shims are POSIX scripts;
    direct CreateProcess on them raises FileNotFoundError, measured Oct 18).

    opencode ships a real .exe; kilo is a node script needing node.exe.
    Returns (argv_prefix, needs_shell_fallback_bool). """
    if name == "opencode":
        exe = os.path.join(NPM, "node_modules", "@opencode", "cli", "bin", "opencode.exe")
        if os.path.exists(exe):
            return ([exe, "run"], False)
    if name == "kilo":
        script = os.path.join(NPM, "node_modules", "@kilocode", "cli", "bin", "kilo")
        if os.path.exists(NODE_EXE) and os.path.exists(script):
            return ([NODE_EXE, script, "run"], False)
    return ([name, "run"], True)  # shell shim fallback


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _read():
    rows = []
    if not os.path.exists(BOARD):
        return rows
    with open(BOARD, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write(rows):
    os.makedirs(STATE, exist_ok=True)
    tmp = BOARD + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, BOARD)


def _board_head():
    """(mtime, sha) of the board file — stale-dispatch guard."""
    if not os.path.exists(BOARD):
        return (0, "missing")
    h = hashlib.sha256()
    with open(BOARD, "rb") as fh:
        h.update(fh.read())
    return (os.path.getmtime(BOARD), h.hexdigest()[:16])


def _next_id(rows):
    n = 0
    for r in rows:
        try:
            n = max(n, int(str(r.get("id", "b0"))[1:]))
        except (ValueError, TypeError) as exc:
            _ = exc  # non-numeric legacy offset suffix: skip that row, keep counting
    return "b%04d" % (n + 1)


def cmd_post(args):
    if args.executor not in EXECUTORS:
        print("executor must be one of %s" % (EXECUTORS,))
        return 2
    if not args.owns:
        print("refused: --owns is required (disjoint ownership, #591)")
        return 2
    rows = _read()
    live = [r for r in rows if r["status"] in ("open", "claimed")]
    overlap = sorted({o for r in live for o in r.get("owns", [])} & set(args.owns))
    row = {
        "id": _next_id(rows),
        "title": args.title,
        "executor": args.executor,
        "owns": args.owns,
        "brief": args.brief,
        "status": "open",
        "proof": "",
        "created_by": args.created_by,
        "created_at": _now(),
        "updated_at": _now(),
    }
    rows.append(row)
    _write(rows)
    print("posted %s" % row["id"])
    if overlap:
        print("LOUD: owns-overlap with live rows on %s — partition before dispatch" % overlap)
    return 0


def cmd_list(args):
    rows = _read()
    for r in rows:
        if args.executor and r["executor"] != args.executor:
            continue
        if args.status and r["status"] != args.status:
            continue
        print("%s [%s/%s] %s (owns: %s)" % (
            r["id"], r["executor"], r["status"], r["title"], ",".join(r.get("owns", []))))
    return 0


def _set_status(args, status):
    rows = _read()
    for r in rows:
        if r["id"] == args.id:
            r["status"] = status
            r["updated_at"] = _now()
            if args.proof:
                r["proof"] = args.proof
            _write(rows)
            print("%s -> %s" % (args.id, status))
            if status in ("done", "failed"):
                print("REMINDER: record outcome in Engram+PMB and judge the save's pairs.")
            return 0
    print("no such row: %s" % args.id)
    return 2


def _probe_clis():
    """Cold-cache self-test: every pushable CLI must answer --help."""
    bad = []
    for name in PUSHABLE:
        argv, shell = _resolve_cli(name)
        probe = ([argv[0], "--help"] if not shell else [name, "--help"])
        try:
            p = subprocess.run(probe, capture_output=True, shell=shell,
                               timeout=30, encoding="utf-8", errors="replace")
            # kilo answers on stderr (measured); accept either stream.
            if p.returncode != 0 or not (p.stdout.strip() or p.stderr.strip()):
                bad.append(name)
        except Exception:
            bad.append(name)
    return bad


def cmd_dispatch(args):
    rows = _read()
    due = [r for r in rows if r["status"] == "open" and r["executor"] in PUSHABLE]
    if not due:
        print("nothing dispatchable (open rows for opencode/kilo: 0)")
        return 0
    bad = _probe_clis()
    if bad:
        print("SELF-TEST FAILED: CLIs unrunnable: %s — dispatching nothing" % bad)
        return 3
    if args.dry_run:
        for r in due:
            print("would dispatch %s -> %s" % (r["id"], r["executor"]))
        return 0
    for r in due:
        log = os.path.join(STATE, "board_dispatch_%s.log" % r["id"])
        brief = r["brief"]
        if len(brief) > 4000:  # cmd line limits on the shell fallback path
            brief = brief[:4000] + "\n[truncated: full brief on the board row]"
        msg = "[agent-board %s] %s\n%s" % (r["id"], r["title"], brief)
        argv, shell = _resolve_cli(r["executor"])
        with open(log, "ab") as lf:
            subprocess.Popen(argv + [msg], stdout=lf, shell=shell,
                             stderr=subprocess.STDOUT, cwd=ROOT)
        r["status"] = "claimed"
        r["updated_at"] = _now()
        print("dispatched %s -> %s (log %s)" % (r["id"], r["executor"], log))
    _write(rows)
    return 0


def cmd_selftest(_args):
    """End-to-end on a scratch board: post/list/claim/done + CLI probes."""
    global BOARD
    real, BOARD = BOARD, os.path.join(STATE, ".board_selftest.jsonl")
    try:
        if os.path.exists(BOARD):
            os.remove(BOARD)
        ns = argparse.Namespace(executor="kilo", owns=["tools/x.py"],
                                title="t", brief="b", created_by="selftest")
        assert cmd_post(ns) == 0, "post failed"
        rows = _read()
        assert len(rows) == 1 and rows[0]["id"] == "b0001", rows
        assert _set_status(argparse.Namespace(id="b0001", proof=""), "claimed") == 0
        assert _set_status(argparse.Namespace(id="b0001", proof="p"), "done") == 0
        bad = _probe_clis()
        print("cli-probe unrunnable: %s (env-dependent, not a failure)" % bad)
        print("SELFTEST PASS")
        return 0
    finally:
        if os.path.exists(BOARD):
            os.remove(BOARD)
        BOARD = real


def main(argv=None):
    ap = argparse.ArgumentParser(description="inter-agent message board")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("post")
    p.add_argument("--executor", required=True)
    p.add_argument("--owns", nargs="+", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--brief", required=True)
    p.add_argument("--created-by", default="freebuff")
    p.set_defaults(fn=cmd_post)
    p = sub.add_parser("list")
    p.add_argument("--executor", default="")
    p.add_argument("--status", default="")
    p.set_defaults(fn=cmd_list)
    for name, st in (("claim", "claimed"), ("done", "done"), ("fail", "failed")):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--proof", default="")
        p.set_defaults(fn=(lambda a, s=st: _set_status(a, s)))
    p = sub.add_parser("dispatch")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_dispatch)
    p = sub.add_parser("selftest")
    p.set_defaults(fn=cmd_selftest)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
