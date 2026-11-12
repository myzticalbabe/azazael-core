# coding: utf-8
"""`timeout=0.0` must mean "do not wait", not "wait forever" (task #510, item 4).

The defect, at `src/CORE/freethreading.py` in both `wait_group` and `wait_all`:

    deadline = time.time() + timeout if timeout else float('inf')

That is a TRUTHINESS test, and `0.0` is falsy -- so the one value a caller would
reach for to mean "do not block, just tell me if it is already done" silently meant
the strongest possible wait. `timeout=0.0` and `timeout=None` were indistinguishable.

Found while executing task #511: a fixture used `timeout=0.0` to force a pool-group
timeout, and instead waited for the whole batch. The test's premise was wrong, not
the code under test -- which is exactly how a footgun like this hides.

The discriminating case is therefore not "does 0.0 return quickly" alone (a pool with
nothing submitted also returns quickly) but that **0.0 and None now differ** on
identical work. Under the broken code they could not.
"""

import sys
import time
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

#: Long enough that a 0.0 wait cannot succeed by luck, short enough to keep the
#: suite quick. The pool is joined on context exit, so teardown pays for it once.
WORK_SECONDS = 1.5


def _pool(workers=2):
    from CORE.freethreading import WorkStealingPool

    return WorkStealingPool(num_workers=workers)


def test_zero_timeout_on_a_group_returns_immediately_and_false():
    """It must not block, and it must not claim the unit completed."""
    with _pool() as pool:
        gid = pool.submit_group([(time.sleep, (WORK_SECONDS,), {})])
        t0 = time.time()
        got = pool.wait_group(gid, timeout=0.0)
        elapsed = time.time() - t0

        assert got is False, "a 0.0 wait reported the group as done"
        assert elapsed < WORK_SECONDS / 2, (
            "timeout=0.0 blocked for %.2fs; it is still testing truthiness"
            % elapsed)


def test_zero_timeout_on_the_pool_returns_immediately_and_false():
    """`wait_all` carried the identical bug, so it gets its own assertion."""
    with _pool() as pool:
        for _ in range(2):
            pool.submit(time.sleep, WORK_SECONDS)
        t0 = time.time()
        got = pool.wait_all(timeout=0.0)
        elapsed = time.time() - t0

        assert got is False, "a 0.0 wait reported every task as complete"
        assert elapsed < WORK_SECONDS / 2, (
            "wait_all(timeout=0.0) blocked for %.2fs" % elapsed)


def test_zero_timeout_and_no_timeout_are_now_distinguishable():
    """The heart of the fix: 0.0 must NOT behave like None, because it used to.

    Same work, same shape, two calls. Under the falsy check both returned True
    after the full wait; now 0.0 returns False immediately and None waits it out.
    """
    with _pool() as pool:
        gid_slow = pool.submit_group([(time.sleep, (WORK_SECONDS,), {})])
        assert pool.wait_group(gid_slow, timeout=0.0) is False, (
            "timeout=0.0 behaved as an unbounded wait")

    with _pool() as pool:
        gid_none = pool.submit_group([(time.sleep, (0.15,), {})])
        assert pool.wait_group(gid_none, timeout=None) is True, (
            "timeout=None must wait for completion; it no longer does")


def test_a_generous_timeout_still_completes():
    """Positive control: the fix must not break ordinary waiting."""
    with _pool() as pool:
        gid = pool.submit_group([(time.sleep, (0.05,), {})])
        assert pool.wait_group(gid, timeout=30.0) is True
        assert pool.wait_all(timeout=30.0) is True
