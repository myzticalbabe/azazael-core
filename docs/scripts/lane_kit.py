#!/usr/bin/env python3
"""lane_kit.py — the smallest honest multi-lane work board: one file, stdlib only.

This is the runnable companion to
`docs/WHITEPAPER_MULTI_LANE_ORCHESTRATION_2026-11-02.md` and
`docs/TUTORIAL_MULTI_LANE_ORCHESTRATION_2026-11-02.md`.

It reproduces, in ~300 lines, the parts of the production board
(`src/CORE/LaneMessageBoard.py`, driven by `_board.py`) that a reader needs to
understand the method, including the three defences the production board did NOT
have when it was first measured on 2026-11-02:

  * an attempt cap — a row handed out `max_attempts` times is BLOCKED with a
    reason instead of being offered forever (production: 20 live rows at
    attempts>=3, one at 12);
  * release PRESERVES the attempt history (production release deleted the state
    row and with it the count);
  * retirement must be earned — `retire()` returns False unless a live claim was
    moved, so "done" cannot be printed for work nobody held.

Deliberately absent: the message bus, subprocesses, threads, MCP, memory. Those
are transport. The board is a table, a lease, and a rule.

Usage
    python docs/scripts/lane_kit.py --demo
    python docs/scripts/lane_kit.py --selftest
    python docs/scripts/lane_kit.py --db /tmp/k.db post freebuff fix_typo "docs/README.md:12"
    python docs/scripts/lane_kit.py --db /tmp/k.db claim freebuff
    python docs/scripts/lane_kit.py --db /tmp/k.db done 1 "edited line 12; pre-commit pass"
    python docs/scripts/lane_kit.py --db /tmp/k.db stats freebuff

Written 2026-11-02 against the live board's measurements; the numbers quoted in
the docstrings below were read from the reference board's SQLite store on that
date (`lane_task_state` joined to `memorybus_queue`), not estimated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time

LANES = ("opencode", "kilo", "freebuff", "crush", "agy")  # code, not config, in the reference build
LEASE_SECONDS = 1800                      # 30 min: a dead lane's work returns
MAX_ATTEMPTS = 3                          # the cap the reference build lacked
STATE = ("available", "claimed", "done", "blocked")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dst_lane   TEXT NOT NULL,
    task_id    TEXT,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    sig        TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
    task_row   INTEGER PRIMARY KEY,
    lane       TEXT NOT NULL,
    state      TEXT NOT NULL,
    claimed_by TEXT,
    claimed_at REAL,
    retired_at REAL,
    note       TEXT NOT NULL DEFAULT '',
    attempts   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS state_state ON state(state);
"""


def signature(task_id, action, detail) -> str:
    """Identity of a piece of work: what it is, not which row carries it.

    Measured failure this prevents: the same `mcp_config_fix` task was posted 75
    times by re-running a smoke-test entry point, and each copy was claimable
    work. Row id is transport; (task_id, action, detail) is identity.
    """
    blob = json.dumps([task_id or "", action or "", detail or ""], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class Board:
    def __init__(self, path=":memory:", max_attempts=MAX_ATTEMPTS,
                 lease_seconds=LEASE_SECONDS):
        self.max_attempts = int(max_attempts)
        self.lease_seconds = int(lease_seconds)
        # timeout: a second writer must wait for the lock, not fail instantly.
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ── write side ────────────────────────────────────────────────────────
    def post(self, dst_lane, action, detail="", task_id=None, now=None) -> int:
        if dst_lane not in LANES:
            raise ValueError("lane must be one of %s, got %r" % (LANES, dst_lane))
        now = time.time() if now is None else float(now)
        sig = signature(task_id, action, detail)
        # A re-post of work that is retired OR currently in hand is a duplicate,
        # not new work. Recorded as retired with a pointer, so the post is neither
        # lost nor worked twice. Measured failure this prevents: one smoke-test
        # entry point posted mcp_config_fix 75 times, and every copy was claimable.
        prior = self.conn.execute(
            "SELECT s.task_row, s.state FROM state s JOIN tasks t ON t.id = s.task_row "
            "WHERE t.dst_lane = ? AND t.sig = ? "
            "      AND s.state IN ('done','blocked','claimed') "
            "ORDER BY s.task_row DESC LIMIT 1", (dst_lane, sig)).fetchone()
        cur = self.conn.execute(
            "INSERT INTO tasks (dst_lane, task_id, action, detail, sig, created_at) "
            "VALUES (?,?,?,?,?,?)", (dst_lane, task_id, action, detail, sig, now))
        row = cur.lastrowid
        if prior:
            note = ("duplicate of task_row=%d" % prior["task_row"]
                    if prior["state"] != "claimed"
                    else "duplicate: task_row=%d is already claimed" % prior["task_row"])
            self.conn.execute(
                "INSERT INTO state (task_row, lane, state, retired_at, note) "
                "VALUES (?,?,'done',?,?)", (row, dst_lane, now, note))
        else:
            self.conn.execute(
                "INSERT INTO state (task_row, lane, state, note) VALUES (?,?,'available','')",
                (row, dst_lane))
        self.conn.commit()
        return row

    def claim(self, lane, limit=10, agent="", now=None):
        """Claim up to `limit` rows for `lane`. One transaction; oldest first.

        Three things happen here that are easy to leave out and expensive to omit:
          * an expired lease is reclaimable (a lane that dies must not strand work);
          * a row is never handed out twice *live* (compare-and-set, not read-then-write);
          * a row that has now been handed out more than `max_attempts` times is
            BLOCKED with a reason instead of being re-offered. Without this the
            queue reports "available" work that no lane can ever finish, forever.
        """
        if lane not in LANES:
            raise ValueError("lane must be one of %s, got %r" % (LANES, lane))
        now = time.time() if now is None else float(now)
        cutoff = now - self.lease_seconds
        out, blocked = [], []
        self.conn.isolation_level = None
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            # live claims held by anyone on this lane: their signatures are taken
            live = set()
            for r in self.conn.execute(
                    "SELECT t.sig FROM state s JOIN tasks t ON t.id = s.task_row "
                    "WHERE s.lane = ? AND s.state = 'claimed' AND s.claimed_at >= ?",
                    (lane, cutoff)):
                live.add(r["sig"])
            rows = self.conn.execute(
                "SELECT t.*, s.state AS st, s.attempts, s.claimed_at "
                "FROM tasks t JOIN state s ON s.task_row = t.id "
                "WHERE t.dst_lane = ? AND (s.state = 'available' OR "
                "      (s.state = 'claimed' AND s.claimed_at < ?)) "
                "ORDER BY t.id ASC LIMIT ?", (lane, cutoff, int(limit))).fetchall()
            for r in rows:
                if r["sig"] in live:
                    continue                       # duplicate of work in hand
                attempts = int(r["attempts"]) + 1
                if attempts > self.max_attempts:
                    self.conn.execute(
                        "UPDATE state SET state='blocked', claimed_by=NULL, claimed_at=NULL, "
                        "retired_at=?, note=? WHERE task_row=?",
                        (now, "attempt cap reached (%d) -- needs a human decision"
                         % self.max_attempts, r["id"]))
                    blocked.append(r["id"])
                    continue
                self.conn.execute(
                    "UPDATE state SET state='claimed', claimed_by=?, claimed_at=?, "
                    "retired_at=NULL, attempts=? WHERE task_row=?",
                    (agent or lane, now, attempts, r["id"]))
                live.add(r["sig"])
                out.append({"row": r["id"], "task_id": r["task_id"],
                            "action": r["action"], "detail": r["detail"],
                            "attempts": attempts})
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        finally:
            self.conn.isolation_level = ""
        if blocked:
            print("[lane_kit] blocked %d row(s) at the attempt cap: %s"
                  % (len(blocked), blocked))
        return out

    def retire(self, row, state="done", note="", now=None) -> bool:
        """Move a *claimed* row to done/blocked. False means nothing moved.

        The False is the point: a caller that reports completion without checking
        it is reporting a memory, not a measurement.
        """
        if state not in ("done", "blocked"):
            raise ValueError("state must be 'done' or 'blocked'")
        now = time.time() if now is None else float(now)
        cur = self.conn.execute(
            "UPDATE state SET state=?, retired_at=?, note=? "
            "WHERE task_row=? AND state='claimed'", (state, now, note, int(row)))
        self.conn.commit()
        return cur.rowcount == 1

    def release(self, row, now=None) -> bool:
        """Return a claimed row to the queue WITHOUT forgetting its history.

        The reference build deleted the state row here, which erased `attempts`
        (a row claimed and released four times still read attempts=1) and left no
        record that a release happened at all.
        """
        now = time.time() if now is None else float(now)
        cur = self.conn.execute(
            "UPDATE state SET state='available', claimed_by=NULL, claimed_at=NULL, "
            "note=note || ? WHERE task_row=? AND state='claimed'",
            (" [released %.0f]" % now, int(row)))
        self.conn.commit()
        return cur.rowcount == 1

    # ── read side ─────────────────────────────────────────────────────────
    def stats(self, lane=None, now=None):
        now = time.time() if now is None else float(now)
        cutoff = now - self.lease_seconds
        q = "SELECT state, COUNT(*) n FROM state %s GROUP BY state"
        args = ()
        if lane:
            q = q % "WHERE lane = ?"
            args = (lane,)
        else:
            q = q % ""
        counts = {r["state"]: r["n"] for r in self.conn.execute(q, args)}
        # A claim older than the lease is NOT live work; counting it as claimed is
        # how a stalled queue reports itself as busy. The expiry count is filtered
        # by lane too -- without that, one lane's abandoned claim is subtracted
        # from another lane's live count and `claimed` can read negative.
        expired_sql = "SELECT COUNT(*) FROM state WHERE state='claimed' AND claimed_at < ?"
        expired = self.conn.execute(
            expired_sql + (" AND lane = ?" if lane else ""),
            ((cutoff, lane) if lane else (cutoff,))).fetchone()[0]
        live = max(0, counts.get("claimed", 0) - expired)
        return {"available": counts.get("available", 0) + expired,
                "claimed": live,
                "done": counts.get("done", 0),
                "blocked": counts.get("blocked", 0),
                "expired_claim": expired,
                "open": counts.get("available", 0) + expired + live}


def demo(verbose=True):
    """A four-minute transcript of the loop and its three classic failures."""
    board = Board(":memory:")
    t0 = 1_700_000_000.0
    lines = []

    def say(s):
        lines.append(s)
        if verbose:
            print(s)

    say("== 1. two lanes, three pieces of work ==")
    a = board.post("freebuff", "doc-fix", "README line 12", task_id="doc-1", now=t0)
    b = board.post("freebuff", "test-gap", "no test for parse()", task_id="test-1", now=t0)
    c = board.post("kilo", "perf", "startup 3.1s", task_id="perf-1", now=t0)
    say("   posted rows %s; stats freebuff=%s" % ([a, b, c], board.stats("freebuff", now=t0)))

    say("\n== 2. a lane claims what is oldest, and only its own lane ==")
    got = board.claim("freebuff", limit=5, agent="freebuff", now=t0)
    say("   claimed %s" % [(g["row"], g["action"], "attempt %d" % g["attempts"]) for g in got])
    say("   kilo still holds: %s" % board.stats("kilo", now=t0))

    say("\n== 3. a second worker cannot take work already in a hand ==")
    again = board.claim("freebuff", limit=5, agent="second-worker", now=t0 + 60)
    say("   second claim returned %d row(s) (0 expected) -- no live row is handed out twice"
        % len(again))

    say("\n== 4. the lane dies mid-batch; the lease expires and the work returns ==")
    later = t0 + LEASE_SECONDS + 1
    say("   stats at t+%ds: %s" % (LEASE_SECONDS + 1, board.stats("freebuff", now=later)))
    got = board.claim("freebuff", limit=5, agent="freebuff-restarted", now=later)
    say("   reclaimed %s -- attempt counter now proves it was abandoned once"
        % [(g["row"], "attempt %d" % g["attempts"]) for g in got])

    say("\n== 5. a duplicate post is not new work ==")
    dup = board.post("freebuff", "doc-fix", "README line 12", task_id="doc-1", now=later)
    say("   re-posted the same task as row %d; state=%s"
        % (dup, board.conn.execute(
            "SELECT state || ' / ' || note FROM state WHERE task_row=?", (dup,)
        ).fetchone()[0]))
    say("   stats freebuff=%s" % board.stats("freebuff", now=later))

    say("\n== 6. retirement must be earned ==")
    first = got[0]["row"]
    say("   retiring a row nobody holds (row 999): %s" % board.retire(999))
    say("   retiring the row in hand (%d): %s" % (first, board.retire(
        first, note="edited line 12; pre-commit pass", now=later + 10)))

    say("\n== 7. the retry storm, and the cap that ends it ==")
    # First, clear the lane so the storm is one row and the reading is clean:
    # a cap that fires on a batch tells you nothing about which row caused it.
    for t in board.claim("kilo", limit=5, agent="kilo", now=t0):
        board.retire(t["row"], note="startup measured 3.1s; budget met", now=t0)
    say("   kilo cleared: %s" % board.stats("kilo", now=t0))
    # Now one task nobody can finish: it is claimed and abandoned over and over.
    storm = board.post("kilo", "flaky", "needs a decision, not a retry", task_id="flaky-1", now=t0)
    t = t0
    for i in range(1, 5):
        t += LEASE_SECONDS + 1
        taken = board.claim("kilo", limit=5, agent="kilo", now=t)
        say("   round %d at t+%dm: claimed=%s" % (i, (t - t0) // 60, [x["row"] for x in taken]))
    say("   stats kilo=%s" % board.stats("kilo", now=t))
    say("   row %d note: %s" % (storm, board.conn.execute(
        "SELECT state || ' / ' || note FROM state WHERE task_row=?", (storm,)).fetchone()[0]))

    say("\n== 8. totals ==")
    say("   %s" % board.stats())
    return board, lines


def selftest():
    board, _ = demo(verbose=False)
    assert board.stats()["blocked"] == 1, "the retry storm must end in exactly one block"
    assert board.stats()["done"] >= 1, "the retired row must be done"
    assert board.stats()["claimed"] == 0, "nothing may be left under a live claim"
    row = board.post("freebuff", "x", "y", task_id="x-1")
    assert board.retire(row) is False, "retiring an unclaimed row must fail"
    taken = board.claim("freebuff", limit=10)
    assert row in [t["row"] for t in taken], "the new row must be offered"
    assert board.retire(row) is True, "a claimed row retires"
    assert board.retire(row) is False, "a row cannot be retired twice"
    for bad in ("agy", "", "Freebuff"):
        refused = False
        try:
            board.claim(bad)
        except ValueError:
            refused = True
        assert refused, "lane %r should have been refused" % bad
    print("lane_kit selftest: OK")


def main(argv=None):
    try:
        return _main(argv)
    except ValueError as exc:
        # A typo is a refusal with a reason, not a stack trace. The exit code is
        # what a caller can act on; the traceback only teaches the reader that
        # the tool is broken when they mistyped a lane name.
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 1


def _main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="lane_kit.db")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("verb", nargs="?", default="stats",
                    choices=["post", "claim", "release", "done", "block", "stats", "rows"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    if a.demo:
        demo()
        return 0
    if a.selftest:
        selftest()
        return 0
    board = Board(a.db)
    if a.verb == "post":
        dst, action = a.args[0], a.args[1]
        print(board.post(dst, action, " ".join(a.args[2:])))
    elif a.verb == "claim":
        lane = a.args[0]
        limit = int(a.args[1]) if len(a.args) > 1 else 10
        for t in board.claim(lane, limit=limit):
            print("CLAIMED row=%d attempts=%d action=%s  %s"
                  % (t["row"], t["attempts"], t["action"], t["detail"]))
    elif a.verb in ("release", "done", "block"):
        row = int(a.args[0])
        note = " ".join(a.args[1:])
        ok = board.release(row) if a.verb == "release" else board.retire(row, a.verb, note)
        print("%s %d: %s" % (a.verb.upper(), row, "moved" if ok else "NOT moved (no live claim)"))
        return 0 if ok else 1
    elif a.verb == "rows":
        for r in board.conn.execute(
                "SELECT t.id, t.dst_lane, t.action, s.state, s.attempts, s.note "
                "FROM tasks t JOIN state s ON s.task_row = t.id ORDER BY t.id"):
            print("row=%d lane=%s action=%s state=%s attempts=%d note=%s"
                  % (r[0], r[1], r[2], r[3], r[4], r[5]))
    else:
        print(board.stats(a.args[0] if a.args else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
