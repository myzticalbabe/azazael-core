# coding: utf-8
"""Tests for `docs/scripts/lane_kit.py` — the runnable minimum in the tutorial.

Each test here pins one property the *production* board did not have when it was
measured on 2026-11-02, so the kit cannot regress into the defect it was written
to demonstrate the fix for:

  * an abandoned row is offered again, but only up to the attempt cap;
  * release preserves the attempt count (production erased it);
  * retirement of a row nobody holds returns False (production's `done` also
    returned False, but nothing in the docs told a caller to check it);
  * a duplicate post is recorded as retired with a pointer, not offered as work;
  * a lane's expiry count is not charged to another lane.

The last test is the false-positive control: the healthy path must still retire
a claimed row. Without it, a kit that refused everything would pass the rest.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docs" / "scripts"))

import lane_kit as lk  # noqa: E402

T0 = 1_700_000_000.0
LEASE = lk.LEASE_SECONDS


def board(**kw):
    return lk.Board(":memory:", **kw)


class TestClaims:
    def test_claim_is_oldest_first_and_lane_scoped(self):
        b = board()
        a = b.post("freebuff", "a", "", task_id="a", now=T0)
        c = b.post("freebuff", "c", "", task_id="c", now=T0)
        b.post("kilo", "k", "", task_id="k", now=T0)
        assert [t["row"] for t in b.claim("freebuff", now=T0)] == [a, c]

    def test_live_claim_is_not_offered_twice(self):
        b = board()
        b.post("freebuff", "a", "", task_id="a", now=T0)
        assert len(b.claim("freebuff", agent="one", now=T0)) == 1
        assert b.claim("freebuff", agent="two", now=T0) == []

    def test_expired_lease_returns_work_and_increments_attempts(self):
        b = board()
        b.post("freebuff", "a", "", task_id="a", now=T0)
        b.claim("freebuff", now=T0)
        got = b.claim("freebuff", now=T0 + LEASE + 1)
        assert len(got) == 1 and got[0]["attempts"] == 2

    def test_attempt_cap_blocks_instead_of_offering_forever(self):
        b = board(max_attempts=2)
        row = b.post("freebuff", "a", "", task_id="a", now=T0)
        t = T0
        for _ in range(2):                       # two leases, both abandoned
            assert b.claim("freebuff", now=t)
            t += LEASE + 1
        assert b.claim("freebuff", now=t) == []   # third offer is refused
        state, note = b.conn.execute(
            "SELECT state, note FROM state WHERE task_row=?", (row,)).fetchone()
        assert state == "blocked"
        assert "cap" in note

    @pytest.mark.parametrize("bad", ["staging", "Freebuff", ""])
    def test_unknown_lane_is_refused(self, bad):
        with pytest.raises(ValueError, match="lane must be one of"):
            board().claim(bad)

    def test_kit_lane_set_matches_production(self):
        """Two copies of one lane set is how a reader and a writer drift apart.

        Measured 2026-11-05: _board.py was documented for crush and agy and
        refused both, while this kit and the production board each held their
        own three-name tuple and neither noticed the other. Pinning set
        equality here means a lane added to one and not the other fails a test
        instead of failing a delivery.
        """
        from CORE.LaneMessageBoard import LaneMessageBoard

        import lane_kit

        assert set(lane_kit.LANES) == set(LaneMessageBoard.VALID_LANES), (
            "lane_kit.LANES and LaneMessageBoard.VALID_LANES must accept the "
            "same lanes; a reader that names a lane the writer refuses has no "
            "queue"
        )


class TestRetirement:
    def test_retire_requires_a_live_claim(self):
        b = board()
        row = b.post("freebuff", "a", "", task_id="a", now=T0)
        assert b.retire(row) is False
        b.claim("freebuff", now=T0)
        assert b.retire(row, note="evidence", now=T0) is True
        assert b.retire(row) is False, "a row cannot be retired twice"

    def test_release_preserves_attempt_history(self):
        b = board()
        row = b.post("freebuff", "a", "", task_id="a", now=T0)
        b.claim("freebuff", now=T0)
        assert b.release(row, now=T0) is True
        again = b.claim("freebuff", now=T0 + 1)
        assert again[0]["attempts"] == 2, "release must not erase the count"
        assert b.release(row) is True
        assert b.release(row) is False, "release needs a live claim"


class TestDuplicates:
    def test_repost_of_retired_work_is_recorded_not_offered(self):
        b = board()
        first = b.post("freebuff", "fix", "line 12", task_id="t1", now=T0)
        b.claim("freebuff", now=T0)
        b.retire(first, note="done", now=T0)
        second = b.post("freebuff", "fix", "line 12", task_id="t1", now=T0 + 5)
        state, note = b.conn.execute(
            "SELECT state, note FROM state WHERE task_row=?", (second,)).fetchone()
        assert state == "done" and ("duplicate" in note and str(first) in note)
        assert b.claim("freebuff", now=T0 + 10) == []

    def test_repost_while_claimed_is_a_duplicate(self):
        b = board()
        first = b.post("freebuff", "fix", "line 12", task_id="t1", now=T0)
        b.claim("freebuff", now=T0)
        second = b.post("freebuff", "fix", "line 12", task_id="t1", now=T0 + 5)
        state = b.conn.execute(
            "SELECT state FROM state WHERE task_row=?", (second,)).fetchone()[0]
        assert state == "done", "a copy of work in hand is not new work"
        assert str(first) in b.conn.execute(
            "SELECT note FROM state WHERE task_row=?", (second,)).fetchone()[0]


class TestStats:
    def test_expiry_is_charged_to_its_own_lane(self):
        b = board()
        b.post("freebuff", "a", "", task_id="a", now=T0)
        b.post("kilo", "k", "", task_id="k", now=T0)
        b.claim("freebuff", now=T0)
        s = b.stats("kilo", now=T0 + LEASE + 1)
        assert s["expired_claim"] == 0 and s["claimed"] == 0 and s["available"] == 1
        assert s["claimed"] >= 0, "a lane's claim count may never read negative"

    def test_healthy_path_still_retires(self):
        """False-positive control: none of the guards above may block real work."""
        b = board()
        row = b.post("freebuff", "a", "", task_id="a", now=T0)
        taken = b.claim("freebuff", now=T0)
        assert [t["row"] for t in taken] == [row]
        assert b.retire(row, note="evidence: pytest 1 passed", now=T0) is True
        s = b.stats("freebuff", now=T0)
        assert s == {"available": 0, "claimed": 0, "done": 1, "blocked": 0,
                     "expired_claim": 0, "open": 0}


def test_demo_runs_and_ends_clean():
    b, lines = lk.demo(verbose=False)
    assert lines, "the demo must produce a transcript"
    s = b.stats()
    assert s["blocked"] == 1, "the storm ends in exactly one blocked row"
    assert s["claimed"] == 0, "the demo must not leave work in a hand"
