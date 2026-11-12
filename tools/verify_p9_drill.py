#!/usr/bin/env python3
# coding: utf-8
"""verify_p9_drill.py — mechanical verifier for the P1-P9 pre-flight P9 drill.

WHY THIS EXISTS
---------------
The 2026-10-28 memory forensic proved the loop dies on the reader side:
lesson_impact(30d) showed ~all lessons n_followed=0, 282 PMB goals sat at
0 progress, Engram search defaulted to the wrong project store. P9 answers
with a live-fire drill, and this script answers the next question: did the
lane actually run it? A coordinator reading a lane's prose ("P9 done") is
attestation, and attestation is how the loop died. This script re-derives
each of the three drill rows from the stores themselves. No row, no PASS.

READ-ONLY. This script never writes to any store. It opens the PMB sqlite
file read-only (immutable=1) and shells out to the engram CLI for search.
A verifier that mutated the thing it checks would be self-certifying.

KNOWN PMB DEFECT (2026-10-28, recorded): mark_lesson_followed returns
ok:true on `recall`-sourced surfaces but writes nothing — a false-OK.
Only `find_lessons` surfaces stick. The drill step therefore names
find_lessons as the mark channel, and --source scopes the check to it.
Unmarked non-find_lessons rows are reported by source so the lane sees
the defect instead of a mystery FAIL.

Usage:
    python tools/verify_p9_drill.py --lane beam --since <epoch> \\
        --goal <goal_ulid> --search "query words" --expect-id 1040 [--json]

Exit codes:
    0  PASS — all three drill rows re-derived from the stores
    1  FAIL — a claimed row is missing (names which check and why)
    2  ENVIRONMENT/NO_DATA — a store is unreachable; NEVER reported as PASS
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys

PMB_WORKSPACE = os.environ.get(
    "PMB_WORKSPACE_DIR",
    r"C:\Users\clint\.pmb\workspaces\04a151a73354")
DB = os.path.join(PMB_WORKSPACE, "events.sqlite")
ENGRAM_BIN = os.environ.get("ENGRAM_BIN", "engram")
DERIVED_PREFIX = "[auto:"


def _connect() -> sqlite3.Connection:
    if not os.path.exists(DB):
        raise SystemExit(
            "FATAL: PMB workspace not found at %s. Set PMB_WORKSPACE_DIR. "
            "Refusing to report PASS on a store that is not there." % DB)
    con = sqlite3.connect("file:%s?immutable=1" % DB, uri=True, timeout=15.0)
    con.row_factory = sqlite3.Row
    return con


def check_lessons(con: sqlite3.Connection, since: float,
                  session: str | None, source: str | None) -> dict:
    """Every surface in the drill window must carry an explicit mark."""
    q = ("SELECT lesson_ulid, surfaced_at, session_id, source, followed, "
         "follow_note FROM lesson_surfaces WHERE surfaced_at >= ?")
    args: list = [since]
    if session:
        q += " AND session_id = ?"
        args.append(session)
    if source:
        q += " AND source = ?"
        args.append(source)
    rows = [dict(r) for r in con.execute(q + " ORDER BY surfaced_at", args)]
    if not rows:
        tried = con.execute(
            "SELECT COUNT(*) FROM mcp_calls WHERE tool_name IN "
            "('find_lessons','prepare') AND timestamp >= ?",
            (since,)).fetchone()[0]
        if tried:
            return {"pass": True, "vacuous": True,
                    "detail": "zero surfaces but %d find/prepare call(s) in "
                    "window: lane queried, store had nothing new" % tried}
        return {"pass": False,
                "detail": "zero surfaces AND zero find_lessons/prepare calls "
                "in window: lane never queried memory"}
    bad = [r for r in rows
           if r["followed"] is None
           or (r.get("follow_note") or "").startswith(DERIVED_PREFIX)]
    if bad:
        by_src: dict = {}
        for r in bad:
            by_src[r.get("source")] = by_src.get(r.get("source"), 0) + 1
        detail = ("%d/%d surfaces unmarked (explicit mark_lesson_followed "
                  "required; [auto: rows do not count); by source %s" %
                  (len(bad), len(rows), by_src))
        if set(by_src) - {"find_lessons"} == set(by_src):
            detail += (" — all unmarked rows are non-find_lessons sources; "
                       "mark_lesson_followed false-OKs on those (PMB defect "
                       "2026-10-28). Drill step must use find_lessons.")
        return {"pass": False, "surfaced": len(rows), "unmarked": len(bad),
                "detail": detail,
                "sample": [r["lesson_ulid"] for r in bad[:5]]}
    return {"pass": True, "surfaced": len(rows),
            "detail": "all %d surfaces explicitly marked" % len(rows)}


def check_goal(con: sqlite3.Connection, goal_ulid: str,
               since: float) -> dict:
    """Confirmation goal exists, moved in-window, and is closed done/100."""
    g = con.execute(
        "SELECT ulid, metadata_json FROM events "
        "WHERE ulid = ? AND event_type = 'goal'",
        (goal_ulid,)).fetchone()
    if g is None:
        return {"pass": False, "detail": "goal %s not found" % goal_ulid}
    try:
        meta = json.loads(g["metadata_json"] or "{}")
    except ValueError:
        return {"pass": False,
                "detail": "goal %s metadata unparseable" % goal_ulid}
    ups = [dict(r) for r in con.execute(
        "SELECT metadata_json, timestamp FROM events "
        "WHERE event_type = 'goal_update' AND timestamp >= ? "
        "ORDER BY timestamp", (since,))]
    mine = []
    for r in ups:
        try:
            m = json.loads(r["metadata_json"] or "{}")
        except ValueError:
            continue
        if m.get("goal_ulid") == goal_ulid:
            mine.append(m)
    moved = any((m.get("new_progress") or 0) > 0
                or m.get("new_status") == "done" for m in mine)
    if not moved:
        return {"pass": False,
                "detail": "goal %s has no update_goal movement in window "
                "(%d update(s) total in window)" % (goal_ulid, len(mine))}
    if meta.get("goal_status") != "done" or meta.get("goal_progress") != 100:
        return {"pass": False,
                "detail": "goal %s not closed: status=%s progress=%s "
                "(P9 dogfood requires done/100)" %
                (goal_ulid, meta.get("goal_status"),
                 meta.get("goal_progress"))}
    return {"pass": True,
            "detail": "goal %s moved %d time(s) in window, closed done/100"
            % (goal_ulid, len(mine))}


def check_search(query: str, expect_id: int) -> dict:
    """Re-run the lane's search cross-project; the claimed hit must recur."""
    try:
        proc = subprocess.run(
            [ENGRAM_BIN, "search", query, "--all", "--limit", "20"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
    except FileNotFoundError:
        return {"pass": None,
                "detail": "engram CLI not found (%s). ENVIRONMENT, not PASS."
                % ENGRAM_BIN}
    except subprocess.TimeoutExpired:
        return {"pass": None,
                "detail": "engram search timed out. ENVIRONMENT, not PASS."}
    out = (proc.stdout or "") + (proc.stderr or "")
    if "Update available" in out and "Found" not in out and "#" not in out:
        pass  # fall through to the match test below
    if ("#%d" % expect_id) in out:
        return {"pass": True,
                "detail": "search re-run --all reproduces #%d" % expect_id}
    return {"pass": False,
            "detail": "search re-run --all does NOT reproduce #%d "
            "(query=%r)" % (expect_id, query)}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify a lane's P9 drill.")
    ap.add_argument("--lane", required=True)
    ap.add_argument("--since", required=True, type=float,
                    help="drill-start epoch; only rows at/after count")
    ap.add_argument("--session", default=None,
                    help="PMB session id to scope lesson surfaces")
    ap.add_argument("--source", default=None,
                    help="surface source to scope (drill uses find_lessons; "
                    "recall sources hit the PMB false-OK defect)")
    ap.add_argument("--goal", required=True, help="confirmation goal ulid")
    ap.add_argument("--search", required=True, help="drill search query")
    ap.add_argument("--expect-id", required=True, type=int,
                    help="Engram observation id the search must reproduce")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)

    try:
        con = _connect()
    except SystemExit as exc:
        print(str(exc))
        return 2
    with con:
        lessons = check_lessons(con, ns.since, ns.session, ns.source)
        goal = check_goal(con, ns.goal, ns.since)
    search = check_search(ns.search, ns.expect_id)

    parts = {"lane": ns.lane, "lessons": lessons, "goal": goal,
             "search": search}
    if search["pass"] is None:
        code = 2
        verdict = "ENVIRONMENT"
    elif lessons["pass"] and goal["pass"] and search["pass"]:
        code = 0
        verdict = "PASS"
    else:
        code = 1
        verdict = "FAIL"
    parts["verdict"] = verdict
    if ns.json:
        print(json.dumps(parts, indent=2))
    else:
        print("P9 drill verification lane=%s verdict=%s" % (ns.lane, verdict))
        for name in ("lessons", "goal", "search"):
            p = parts[name]
            mark = "ok" if p["pass"] else ("ENV" if p["pass"] is None
                                           else "MISSING")
            print("  [%s] %s: %s" % (mark, name, p["detail"]))
    return code


if __name__ == "__main__":
    sys.exit(main())
