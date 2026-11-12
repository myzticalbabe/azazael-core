# coding: utf-8
"""Queue semantics for LaneMessageBoard.task claim/retire (2026-10-31).

Each test here pins a rule the batch-loop protocol in
`docs/agent_orders/03_lane_queue.md` depends on. The failure each one prevents
is named, because a test whose purpose is only "coverage" gets deleted the
first time it is inconvenient.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import CORE.LaneMessageBoard as lmb_mod  # noqa: E402
from CORE.LaneMessageBoard import LaneMessageBoard  # noqa: E402


@pytest.fixture()
def board(tmp_path, monkeypatch):
    """A board pointed at a throwaway store, seeded with real queue rows."""
    db = tmp_path / "telemetry_store.db"
    con = sqlite3.connect(str(db), timeout=5)
    con.execute(
        "CREATE TABLE memorybus_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "topic TEXT, content TEXT, metadata TEXT, source TEXT, session_id TEXT, "
        "timestamp TEXT, flushed INTEGER, event_uuid TEXT, trace_id TEXT, "
        "correlation_id TEXT, audit_hash TEXT, schema_version INTEGER)"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(lmb_mod, "_MEMORYBUS_DB_PATH", db, raising=False)
    return LaneMessageBoard(lane="opencode"), db


def _post(db, lane, task_id, action="do", detail="x", created="2026-10-31T00:00:00+00:00"):
    body = {"lane": lane, "task_id": task_id, "action": action, "detail": detail,
            "source_lane": "opencode", "created_at": created, "status": "pending"}
    con = sqlite3.connect(str(db), timeout=5)
    cur = con.execute(
        "INSERT INTO memorybus_queue (topic, content, timestamp) VALUES (?, ?, ?)",
        (f"lane.{lane}.tasks", json.dumps(body), created),
    )
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def test_claim_is_bounded_and_exclusive(board):
    """Two lanes polling at once must not both receive the same row."""
    b, db = board
    for i in range(5):
        _post(db, "freebuff", f"T{i}")
    first = b.claim_tasks("freebuff", limit=2, agent="laneA")
    second = b.claim_tasks("freebuff", limit=2, agent="laneB")
    assert len(first) == 2
    assert len(second) == 2
    assert {r["board_id"] for r in first}.isdisjoint({r["board_id"] for r in second})


def test_completed_rows_are_not_handed_out_again(board):
    """The whole point of the change: retired work leaves the query."""
    b, db = board
    rid = _post(db, "freebuff", "ONCE")
    [claimed] = b.claim_tasks("freebuff", limit=10, agent="laneA")
    assert claimed["board_id"] == rid
    assert b.complete_task(rid, state="done", note="evidence") is True
    assert b.claim_tasks("freebuff", limit=10, agent="laneA") == []


def test_a_reposted_task_id_is_retired_as_a_duplicate(board):
    """mcp_config_fix was posted 75 times; each copy must not become work."""
    b, db = board
    first = _post(db, "freebuff", "SAME")
    duplicate = _post(db, "freebuff", "SAME")
    [claimed] = b.claim_tasks("freebuff", limit=10, agent="laneA")
    assert claimed["board_id"] == first
    b.complete_task(first, state="done", note="done once")
    # The duplicate is retired during the next claim, not handed to the batch.
    assert b.claim_tasks("freebuff", limit=10, agent="laneA") == []
    con = sqlite3.connect(str(db), timeout=5)
    state, note = con.execute(
        "SELECT state, note FROM lane_task_state WHERE board_id=?", (duplicate,)
    ).fetchone()
    con.close()
    assert state == "done"
    assert f"duplicate of Board-{first}" == note


def test_an_abandoned_claim_is_reclaimed_after_the_lease(board):
    """A lane that dies mid-batch must not strand work forever."""
    b, db = board
    rid = _post(db, "freebuff", "ORPHAN")
    assert len(b.claim_tasks("freebuff", limit=10, agent="deadlane")) == 1
    # A live claim is not handed out again...
    assert b.claim_tasks("freebuff", limit=10, agent="other") == []
    # ...until the lease expires.
    reclaimed = b.claim_tasks("freebuff", limit=10, agent="other", lease_seconds=-1)
    assert [r["board_id"] for r in reclaimed] == [rid]


def test_release_returns_work_without_retiring_it(board):
    b, db = board
    rid = _post(db, "freebuff", "HAND-BACK")
    b.claim_tasks("freebuff", limit=10, agent="laneA")
    assert b.release_task(rid) is True
    [again] = b.claim_tasks("freebuff", limit=10, agent="laneB")
    assert again["board_id"] == rid


def test_release_refuses_a_retired_row(board):
    """Release must never resurrect completed work."""
    b, db = board
    rid = _post(db, "freebuff", "CLOSED")
    b.claim_tasks("freebuff", limit=10, agent="laneA")
    b.complete_task(rid, state="done", note="closed")
    assert b.release_task(rid) is False


def test_complete_task_reports_when_nothing_moved(board):
    """Silent success is the defect this guards: an id that was never claimed."""
    b, db = board
    rid = _post(db, "freebuff", "NEVER-CLAIMED")
    assert b.complete_task(rid, state="done", note="") is False


def test_archive_backlog_dry_run_changes_nothing_and_dumps(board, tmp_path):
    b, db = board
    old = _post(db, "freebuff", "OLD", created="2026-10-20T00:00:00+00:00")
    new = _post(db, "freebuff", "NEW", created="2026-10-31T05:00:00+00:00")
    dump = tmp_path / "dump.tsv"
    res = b.archive_backlog("2026-10-31", lane="freebuff", dry_run=True,
                            dump_path=str(dump))
    assert res["matched"] == 1 and res["retired"] == 0
    assert dump.exists() and "OLD" in dump.read_text(encoding="utf-8")
    # Both rows are still claimable: a dry run retired nothing.
    ids = {r["board_id"] for r in b.claim_tasks("freebuff", limit=10, agent="a")}
    assert ids == {old, new}


def test_archive_backlog_apply_retires_only_pre_cutover_rows(board, tmp_path):
    b, db = board
    old = _post(db, "freebuff", "OLD", created="2026-10-20T00:00:00+00:00")
    new = _post(db, "freebuff", "NEW", created="2026-10-31T05:00:00+00:00")
    res = b.archive_backlog("2026-10-31", lane="freebuff", dry_run=False,
                            dump_path=str(tmp_path / "d.tsv"))
    assert res["retired"] == 1
    ids = {r["board_id"] for r in b.claim_tasks("freebuff", limit=10, agent="a")}
    assert ids == {new}, old


def test_a_dict_detail_does_not_crash_the_claim(board):
    """Legacy rows carry detail as a dict; slicing one raised KeyError: slice."""
    b, db = board
    body = {"task_id": "DICT-DETAIL", "action": "legacy",
            "detail": {"note": "was an object"}, "created_at": "2026-10-31T01:00:00+00:00"}
    con = sqlite3.connect(str(db), timeout=5)
    con.execute("INSERT INTO memorybus_queue (topic, content) VALUES (?, ?)",
                ("lane.freebuff.tasks", json.dumps(body)))
    con.commit()
    con.close()
    [row] = b.claim_tasks("freebuff", limit=10, agent="a")
    assert "was an object" in row["detail"]


def test_stats_counts_open_claimed_and_retired(board):
    b, db = board
    a = _post(db, "freebuff", "A")
    _post(db, "freebuff", "B")
    b.claim_tasks("freebuff", limit=1, agent="laneA")
    b.complete_task(a, state="done", note="n")
    stats = b.task_stats("freebuff")
    assert stats["done"] == 1
    assert stats["open"] == 1


def _fake_bus(db_path):
    """A bus whose publish lands in THIS test's store.

    `post_task` publishes through MemoryBus and then reads the row back from
    `_MEMORYBUS_DB_PATH`. The fixture only redirects the latter, so the real
    bus wrote somewhere the readback could not see and every post_task test
    died with "published but no durable queue row could be read back". This
    stub writes into the same tmp store, which is what the real bus does when
    it is pointed at it.
    """
    def publish(topic, content, metadata=None, source=""):
        con = sqlite3.connect(str(db_path), timeout=5)
        try:
            con.execute(
                "INSERT INTO memorybus_queue (topic, content, metadata, source, "
                "timestamp, flushed) VALUES (?, ?, ?, ?, ?, 0)",
                (topic, json.dumps(content), json.dumps(metadata or {}), source,
                 "2026-10-31T00:00:00+00:00"),
            )
            con.commit()
        finally:
            con.close()
        return 1
    return publish


def test_post_task_collapses_an_identical_repost(board):
    """Prevention, where the claim path only cleans up afterwards.

    `claim_tasks` retires a duplicate when it finds one, so the board stayed
    correct -- but the ROWS stayed too: measured 2026-10-31, 764 task rows for
    370 task_ids, `1039` posted 75 times by an opencode poster firing every
    ~130 s for six days. Cleanup at claim time leaves the queue mostly copies
    and every reader pays for them. post_task now refuses to make the duplicate.
    """
    b, db = board
    b._bus.publish = _fake_bus(db)
    first = b.post_task(lane="freebuff", action="do", task_id="SAME", detail="x")
    again = b.post_task(lane="freebuff", action="do", task_id="SAME", detail="x")

    assert again == first, "an identical re-post created a second row"
    con = sqlite3.connect(str(db), timeout=5)
    n = con.execute("SELECT count(*) FROM memorybus_queue").fetchone()[0]
    con.close()
    assert n == 1, "a duplicate row reached the store anyway"


def test_post_task_still_posts_a_changed_instruction(board):
    """The guard must not swallow an update.

    Collapsing on task_id alone would drop a genuinely revised task and leave
    the lane working the old brief -- a worse failure than the duplicate.
    """
    b, db = board
    b._bus.publish = _fake_bus(db)
    first = b.post_task(lane="freebuff", action="do", task_id="SAME", detail="v1")
    changed = b.post_task(lane="freebuff", action="do", task_id="SAME", detail="v2")

    assert changed != first, "a changed detail was collapsed into the old row"


def test_post_task_posts_again_after_the_row_is_retired(board):
    """A retired row must not block the same task_id forever."""
    b, db = board
    b._bus.publish = _fake_bus(db)
    first = b.post_task(lane="freebuff", action="do", task_id="REUSE", detail="x")
    [claimed] = b.claim_tasks("freebuff", limit=10, agent="laneA")
    b.complete_task(claimed["board_id"], state="done", note="done")

    fresh = b.post_task(lane="freebuff", action="do", task_id="REUSE", detail="x")
    assert fresh != first, "a retired row blocked the legitimate re-post"


def test_a_revised_task_is_not_retired_as_a_duplicate(board):
    """A changed detail and the old one are not the same work.

    Measured 2026-10-31: post_task was taught to allow a revision (same
    task_id, new detail), and then claim_tasks retired that revision as
    "duplicate of Board-46849" -- because it keyed the duplicate check on
    task_id alone. The update was silently dropped and the lane would have
    worked the superseded brief. Both paths now key on
    (task_id, action, detail).
    """
    b, db = board
    b._bus.publish = _fake_bus(db)
    old = b.post_task(lane="freebuff", action="do", task_id="REV", detail="v1")
    new = b.post_task(lane="freebuff", action="do", task_id="REV", detail="v2")
    assert new != old

    claimed = b.claim_tasks("freebuff", limit=10, agent="laneA")
    ids = {c["board_id"] for c in claimed}
    assert new in ids, "the revised task was retired as a duplicate"
    assert old in ids, "the original was dropped from the same batch"

    con = sqlite3.connect(str(db), timeout=5)
    states = dict(con.execute("SELECT board_id, note FROM lane_task_state").fetchall())
    con.close()
    assert not any("duplicate" in (n or "") for n in states.values()), states


def test_poll_tasks_excludes_retired_rows(board):
    """The reader must ask the same question the writer does.

    Until 2026-10-31 poll_tasks did not join lane_task_state at all, so the
    command every lane instruction file names showed retired work as live:
    measured on the real board, kilo 340 rows for 25 actionable (313 of them
    'archived pre-cutover backlog'), freebuff 330 for 7, opencode 99 for 2.
    """
    b, db = board
    live = _post(db, "freebuff", "LIVE")
    done = _post(db, "freebuff", "DONE")
    gone = _post(db, "freebuff", "GONE")
    b.claim_tasks("freebuff", limit=10, agent="laneA")
    b.complete_task(done, state="done", note="finished")
    b.complete_task(gone, state="blocked", note="cannot proceed")
    # Hand the live row back: a row under a live lease is work in somebody's
    # hands, and the default poll drops those too -- so without this release the
    # test would be asserting on a lease, not on retirement.
    assert b.release_task(live) is True

    ids = {(t.metadata or {}).get("_row_id") for t in b.poll_tasks("freebuff", limit=50)}
    assert ids == {live}, "a retired row was presented as live work"

    audit = {(t.metadata or {}).get("_row_id")
             for t in b.poll_tasks("freebuff", limit=50, include_retired=True)}
    assert audit == {live, done, gone}, "the audit view lost rows"


def test_poll_tasks_excludes_live_claims_unless_asked(board):
    """A live lease is work in somebody's hands, not work on offer.

    Measured 2026-10-31 after the retirement filter landed: freebuff polled 12
    rows against 5 claimable, because a claimed row is not retired. The poll
    number has to equal what `claim` will return or the lane plans work it
    cannot get.
    """
    b, db = board
    rid = _post(db, "freebuff", "HELD")
    b.claim_tasks("freebuff", limit=10, agent="laneA")

    assert b.poll_tasks("freebuff", limit=50) == []
    held = b.poll_tasks("freebuff", limit=50, include_claimed=True)
    assert [t.metadata.get("_row_id") for t in held] == [rid]
    # The audit view must show every row, leases included. Measured 2026-10-31:
    # `--all` returned 328 of freebuff's 335 rows because the 7 live claims were
    # filtered out even with include_retired set.
    everything = b.poll_tasks("freebuff", limit=50, include_retired=True)
    assert [t.metadata.get("_row_id") for t in everything] == [rid]
    # And the number a lane reads agrees with what it would get.
    assert b.task_stats("freebuff")["available"] == 0
    assert b.task_stats("freebuff")["claimed"] == 1


def test_archived_is_its_own_state_not_blocked(board, tmp_path):
    """archive_backlog reused 'blocked', the documented cannot-proceed value.

    So stats reported kilo blocked=313 and the number could not be read without
    knowing which producer wrote it.
    """
    b, db = board
    _post(db, "freebuff", "OLD", created="2026-10-20T00:00:00+00:00")
    b.archive_backlog("2026-10-31", lane="freebuff", dry_run=False,
                      dump_path=str(tmp_path / "d.tsv"))
    stats = b.task_stats("freebuff")
    assert stats["archived"] == 1
    assert stats["blocked"] == 0, "archive wrote the cannot-proceed state"
    assert stats["available"] == 0
    # An archived row is not claimable and not 'blocked' to an auditor either.
    assert b.claim_tasks("freebuff", limit=10, agent="a") == []


def test_stats_separates_available_from_claimed(board):
    """open used to mean available+claimed, so a lane planned work it could not get.

    Measured on the real board: freebuff open=11 with claimed=7 while
    claim_tasks yielded 4.
    """
    b, db = board
    _post(db, "freebuff", "A")
    _post(db, "freebuff", "B")
    b.claim_tasks("freebuff", limit=1, agent="laneA")

    stats = b.task_stats("freebuff")
    assert stats["available"] == 1, "claimed work counted as available"
    assert stats["claimed"] == 1
    assert stats["open"] == 1, "open must mean claimable"
    assert stats["open_incl_claimed"] == 2
    assert len(b.claim_tasks("freebuff", limit=10, agent="laneB")) == 1


def test_post_status_returns_the_durable_row_id(board):
    """post_status returned the per-process bus counter (1), not the row id.

    Measured 2026-10-31: the status row landed at id 46858 while the call
    returned 1. post_task had already been fixed for this; its two sibling
    posters had not.
    """
    b, db = board
    b._bus.publish = _fake_bus(db)
    b.post_task(lane="freebuff", action="do", task_id="FIRST", detail="x")

    row = b.post_status("WORKING", "second write")
    con = sqlite3.connect(str(db), timeout=5)
    top = con.execute("SELECT MAX(id) FROM memorybus_queue "
                      "WHERE topic='lane.opencode.status'").fetchone()[0]
    con.close()

    assert row == top, "post_status did not return its own durable row id"
    assert row != 1, "post_status still returns the per-process bus counter"


def test_post_status_raises_when_the_row_cannot_be_read_back(board, monkeypatch):
    """A post you cannot read back did not land: say so, do not return a number."""
    b, db = board
    monkeypatch.setattr(b._bus, "publish", lambda *a, **k: 1)
    with pytest.raises(RuntimeError, match="NOT delivered"):
        b.post_status("LOST", "nothing was written")


def test_a_lapsed_lease_is_not_counted_as_claimed_and_available(board):
    """A lapsed lease was in both numbers, so `open_incl_claimed` added it twice.

    `claimed` was a raw `state='claimed'` count while `available` already
    returned lapsed leases to the pool (that is why a dead lane's work comes
    back). Measured on the real board 2026-11-02: freebuff claimed=9 of which
    7 had lapsed, and available already included them.
    """
    b, db = board
    rid = _post(db, "freebuff", "STALE")
    b.claim_tasks("freebuff", limit=1, agent="deadLane")
    # Backdate the lease past CLAIM_LEASE_SECONDS without waiting 30 minutes.
    con = sqlite3.connect(str(db), timeout=5)
    con.execute(
        "UPDATE lane_task_state SET claimed_at = ? WHERE board_id = ?",
        ("2026-10-01T00:00:00+00:00", rid),
    )
    con.commit()
    con.close()

    stats = b.task_stats("freebuff")
    assert stats["claimed_live"] == 0, "a lapsed lease was reported as held"
    assert stats["claimed"] == 0, "`claimed` still means the raw state count"
    assert stats["claimed_expired"] == 1, "the lapsed lease vanished instead of being named"
    assert stats["claimed_all"] == 1
    assert stats["available"] == 1, "claim_tasks would hand this out; stats denied it"
    assert stats["open_incl_claimed"] == 1, "the lapsed lease was counted twice"
    # And the number agrees with behaviour: the row really is claimable again.
    assert [t["task_id"] for t in b.claim_tasks("freebuff", limit=5, agent="liveLane")] == ["STALE"]


def test_churned_names_rows_handed_out_three_times_and_never_retired(board):
    """`available` grows forever when the same rows keep coming back.

    A row at attempts>=3 is not waiting on a lane, it is waiting on a decision.
    Counting it inside `available` makes a stuck queue look like a full one.
    """
    b, db = board
    rid = _post(db, "freebuff", "ANCHOR")
    for _ in range(3):
        b.claim_tasks("freebuff", limit=1, agent="laneA")
        b.release_task(rid)

    stats = b.task_stats("freebuff")
    assert stats["churned"] == 1, "a thrice-released row was not named as churn"
    assert stats["available"] == 1
    # Retiring it clears the count: churn is about unretired work.
    b.claim_tasks("freebuff", limit=1, agent="laneA")
    b.complete_task(rid, "done", "retired")
    assert b.task_stats("freebuff")["churned"] == 0
