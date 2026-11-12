"""A plan's independent branches are ONE schedulable unit, not N unrelated tasks.

The SOTA rows this closes: EP130 (GAP graph planning -- `plan_dag`'s per-node
depth levels are "exactly the branch grouping a fan-out executor consumes"),
EP209 (SAGA -- the schedulable unit should be a workflow, not a bare task) and
EP248 (agent JIT -- compiled parallel execution). The rescore named
`CORE/freethreading.py::WorkStealingPool` as that fan-out executor, and the pool
could only accept unrelated tasks: a caller had to flatten a plan's levels into
separate `submit()` calls and then re-associate the results by hand, with no way
to ask "is this unit done" or "did any branch of it fail".

`submit_group` / `wait_group` / `group_results` are the additive path. The
assertions are chosen so that a mere wrapper could not satisfy them:

  * the ungrouped contract is unchanged -- `submit()` still returns sequential
    task ids and `results()` still carries its documented keys, so tagging a
    task as a group member did not alter the existing path;
  * a group aggregates in BRANCH order even when its branches finish in the
    opposite order -- branch 0 here blocks until branches 1 and 2 have both
    finished, so completion order is provably 2,1,0 while the aggregate must be
    0,1,2. Only an explicit sort by branch index produces that;
  * a raising branch is reported in `errors` with its error text, its siblings
    still complete, and the unit reports 2 succeeded / 1 failed -- a partial
    unit is visible rather than silently short.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _pool(workers=4):
    from CORE.freethreading import WorkStealingPool
    return WorkStealingPool(num_workers=workers)


def test_ungrouped_submit_still_returns_sequential_ids_and_the_documented_shape():
    with _pool() as pool:
        ids = [pool.submit(time.sleep, 0.0) for _ in range(4)]
        assert ids == [0, 1, 2, 3]
        assert pool.wait_all(timeout=10.0) is True
        rows = pool.results()

    assert len(rows) == 4
    for row in rows:
        assert {"task_id", "success", "result", "error", "elapsed_ms"} <= set(row)
        assert row["success"] is True
        # Ungrouped tasks carry no group tag, so nothing downstream can mistake
        # them for a member of a unit.
        assert row["group_id"] is None


def test_a_group_aggregates_in_branch_order_when_completion_order_is_reversed():
    branch_1_done = threading.Event()
    branch_2_done = threading.Event()

    def branch_0():
        """Blocks until the other two finished, so it completes last."""
        branch_1_done.wait(timeout=10.0)
        branch_2_done.wait(timeout=10.0)
        return "a"

    def branch_1():
        time.sleep(0.05)
        branch_1_done.set()
        return "b"

    def branch_2():
        branch_2_done.set()
        return "c"

    branches = [(branch_0, (), {}), (branch_1, (), {}), (branch_2, (), {})]
    with _pool() as pool:
        gid = pool.submit_group(branches)
        assert pool.wait_group(gid, timeout=15.0) is True
        aggregate = pool.group_results(gid)
        completion_order = [r["result"] for r in pool.results()]

    assert aggregate["total"] == 3
    assert aggregate["complete"] is True
    assert aggregate["pending"] == 0
    assert aggregate["succeeded"] == 3 and aggregate["failed"] == 0
    assert [r["result"] for r in aggregate["results"]] == ["a", "b", "c"]
    assert [r["branch"] for r in aggregate["results"]] == [0, 1, 2]
    # The premise of the two assertions above: the pool did NOT finish in order,
    # so branch order in the aggregate is produced by sorting, not by luck.
    assert completion_order != ["a", "b", "c"]


def test_a_raising_branch_is_reported_and_does_not_cancel_its_siblings():
    def boom():
        raise RuntimeError("branch exploded")

    branches = [(time.sleep, (0.0,), {}), (boom, (), {}), (time.sleep, (0.0,), {})]
    with _pool() as pool:
        pool.submit_group(branches, group_id="plan-levels")
        assert pool.wait_group("plan-levels", timeout=15.0) is True
        aggregate = pool.group_results("plan-levels")

    assert aggregate["group_id"] == "plan-levels"
    assert aggregate["total"] == 3 and aggregate["complete"] is True
    assert aggregate["succeeded"] == 2 and aggregate["failed"] == 1
    assert [r["branch"] for r in aggregate["errors"]] == [1]
    assert "branch exploded" in aggregate["errors"][0]["error"]
    assert [r["branch"] for r in aggregate["results"]] == [0, 1, 2]


def test_an_empty_group_is_reported_as_empty_not_treated_as_unknown():
    """A plan may have zero independent branches; that is not an error."""
    with _pool() as pool:
        gid = pool.submit_group([])
        aggregate = pool.group_results(gid)
        assert aggregate["total"] == 0
        assert aggregate["complete"] is True
        assert aggregate["pending"] == 0
        with pytest.raises(KeyError):
            pool.group_results("no-such-unit")


def test_a_second_unit_under_one_id_is_rejected_rather_than_merged():
    with _pool() as pool:
        pool.submit_group([(time.sleep, (0.0,), {})], group_id="unit-1")
        with pytest.raises(ValueError):
            pool.submit_group([(time.sleep, (0.0,), {})], group_id="unit-1")
        # The rejection left the first unit intact and submitted nothing new.
        assert pool.wait_group("unit-1", timeout=10.0) is True
        assert pool.group_results("unit-1")["total"] == 1
