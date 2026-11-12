# coding: utf-8
"""
2026-09-14 remediation, fourth pass — F9 (stop serialising the hot path).

`record_outcome()` is the hottest write in the mesh: every worm execution in
every nature lands here. It used to take ONE process-wide `RLock`, and inside
that lock it did a `from CORE.worm_registry import WORM_REGISTRY` — an import
plus a foreign subsystem's dict access. The audit filed that as F9 and as a
*scale* defect ("at 224 worms this is currently fine; it is the first thing
that will break at 10x"), which is exactly the kind of finding that gets
"fixed" by making the numbers fast and subtly different.

So this file's job is not to prove it is faster. It is to prove it is **the
same**:

* the learned values are bit-identical to the documented recurrence,
* no update is lost under real concurrency,
* the public ring bound and history depth are unchanged,
* the reflex still fires at the same threshold (moved out of the lock, not
  removed),
* and readers can never deadlock against writers.

Stripe assignment is a hash, so these tests deliberately never assert *which*
stripe a key lands on — only that the observable ledger is unchanged.
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE import spine_registry as sr


# ── helpers ──────────────────────────────────────────────────────────

def _expected_q(rewards, alpha_base=sr._Q_ALPHA_BASE):
    """The Q recurrence re-derived from the docstring, independently.

    q <- q + alpha * (r - q), with adaptive alpha scaled by the sqrt of the
    Welford running variance, clamped to [0.05, 0.5]. Written out longhand
    here on purpose: if the module's version drifts, this stops agreeing.
    """
    q = 0.0
    n = 0
    var = 0.0
    for r in rewards:
        var_est = var if n else 0.25
        alpha = alpha_base * (1.0 + min(2.0, (var_est ** 0.5) * 2.0))
        alpha = max(0.05, min(0.5, alpha))
        n += 1
        delta = r - q
        var = var + (delta * delta - var) / n
        q = q + alpha * (r - q)
        q = max(sr._Q_MIN, min(sr._Q_MAX, q))
    return q


def _reset():
    sr.homeostat_reset()
    with sr._SCAR_LOCK:
        sr._SCARS.clear()


# ── 1. learned values are identical to the documented recurrence ─────

def test_q_values_match_the_recurrence_exactly():
    _reset()
    rewards = [1.0, -1.0, 0.5, 1.0, -0.25, 0.75, -1.0]
    for r in rewards:
        sr.record_outcome("stripetest", "AlphaWorm", success=(r > 0), reward=r)

    got = sr.get_q_weight("stripetest", "AlphaWorm")
    want = _expected_q(rewards)
    assert abs(got - want) < 1e-12, f"Q drifted: got {got!r}, recurrence says {want!r}"


def test_counters_and_history_depth_are_exact():
    _reset()
    for i in range(sr._HIST_MAX + 12):
        sr.record_outcome("stripetest", "BetaWorm", success=(i % 4 != 0),
                          duration_ms=float(i))
    view = sr.get_homeostat_view()["stripetest:BetaWorm"]
    n_fail = sum(1 for i in range(sr._HIST_MAX + 12) if i % 4 == 0)
    assert view["failures"] == n_fail
    assert view["successes"] == (sr._HIST_MAX + 12) - n_fail
    # Rolling duration history stays capped at _HIST_MAX, newest last.
    assert len(view["hist_ms"]) == sr._HIST_MAX
    assert view["hist_ms"][-1] == float(sr._HIST_MAX + 11)


def test_route_keys_reinforce_like_any_other_key():
    """Routes are just ledger keys; striping must not single them out."""
    _reset()
    for _ in range(3):
        sr.record_outcome("route", "windows>fresh", success=False)
    w = sr.get_q_weight("route", "windows>fresh")
    assert w < 0.0
    usage = sr.get_route_usage()
    assert "windows>fresh" in usage
    assert usage["windows>fresh"]["failures"] == 3


# ── 2. no lost updates under real concurrency ────────────────────────

def test_concurrent_writers_lose_no_updates():
    _reset()
    threads_n, per_thread = 8, 60
    barrier = threading.Barrier(threads_n)
    errors = []

    def worker(tid):
        try:
            barrier.wait(timeout=10)
            for i in range(per_thread):
                # Disjoint keys: proves striping, not just locking.
                sr.record_outcome("conc", f"W{tid}", success=(i % 2 == 0),
                                  duration_ms=float(i))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    ts = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
        assert not t.is_alive(), "writer thread deadlocked"
    assert not errors, errors

    view = sr.get_homeostat_view()
    total = 0
    for tid in range(threads_n):
        rec = view[f"conc:W{tid}"]
        assert rec["successes"] == per_thread // 2, rec
        assert rec["failures"] == per_thread // 2, rec
        assert len(rec["hist_ms"]) == min(per_thread, sr._HIST_MAX)
        total += rec["successes"] + rec["failures"]
    assert total == threads_n * per_thread


def test_same_key_from_many_threads_stays_consistent():
    """The one case that must still serialise: a single key's read-modify-write."""
    _reset()
    threads_n, per_thread = 8, 50
    barrier = threading.Barrier(threads_n)

    def worker():
        barrier.wait(timeout=10)
        for _ in range(per_thread):
            sr.record_outcome("conc", "SharedWorm", success=True)

    ts = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
        assert not t.is_alive()

    rec = sr.get_homeostat_view()["conc:SharedWorm"]
    assert rec["successes"] == threads_n * per_thread
    assert rec["failures"] == 0


# ── 3. readers cannot deadlock against writers ───────────────────────

def test_readers_and_writers_do_not_deadlock():
    """Exercises the lock-order invariant: writers hold H-then-Q stripes,
    readers hold the full barrier (every H then every Q), and no path ever
    waits on a stripe in the opposite order."""
    _reset()
    stop = threading.Event()
    barrier = threading.Barrier(3)
    reader_errors = []

    def reader(idx):
        barrier.wait(timeout=10)
        try:
            while not stop.is_set():
                sr.get_homeostat_view()
                sr.get_q_weights()
                sr.q_order("conc", ["R0", "R1", "R2"])
                sr.get_outcome_events()
                sr.is_idle(window_seconds=0.001)
                sr.get_scars()
        except Exception as exc:  # pragma: no cover
            reader_errors.append(exc)

    def writer():
        barrier.wait(timeout=10)
        for i in range(400):
            sr.record_outcome("conc", f"W{i % 3}", success=(i % 5 != 0),
                              duration_ms=1.0)

    ts = [threading.Thread(target=reader, args=(0,)),
          threading.Thread(target=reader, args=(1,)),
          threading.Thread(target=writer)]
    for t in ts:
        t.start()
    ts[2].join(timeout=60)
    assert not ts[2].is_alive(), "writer starved/deadlocked behind readers"
    stop.set()
    for t in ts:
        t.join(timeout=30)
        assert not t.is_alive(), "reader deadlocked"
    assert not reader_errors, reader_errors


# ── 4. the outcome ring: bound and depth unchanged ───────────────────

def test_ring_bound_and_depth_unchanged_single_thread():
    _reset()
    for i in range(sr._OUTCOME_EVENTS_MAX + 50):
        sr.record_outcome("ring", f"R{i % 2}", success=True, duration_ms=1.0)
    events = sr.get_outcome_events()
    # Exactly the cap, not merely "at most": depth must not silently shrink
    # just because the ring is now striped.
    assert len(events) == sr._OUTCOME_EVENTS_MAX


def test_ring_merges_across_threads_and_stays_capped():
    _reset()
    # Total events are chosen to equal the cap exactly (4 x 50 == 200), so
    # "every contributor is represented" is a guaranteed consequence of the
    # contract rather than a race outcome. With a total above the cap the
    # oldest events are dropped by design, and if one thread happened to
    # finish first its whole block could legitimately fall outside the
    # newest-N window -- asserting otherwise makes the test flaky under load.
    threads_n = 4
    per_thread = sr._OUTCOME_EVENTS_MAX // threads_n
    barrier = threading.Barrier(threads_n)

    def worker(tid):
        barrier.wait(timeout=10)
        for i in range(per_thread):
            sr.record_outcome("ring", f"M{tid}", success=True, duration_ms=1.0)

    ts = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
        assert not t.is_alive()

    events = sr.get_outcome_events()
    assert len(events) == sr._OUTCOME_EVENTS_MAX
    # Most-recent-first contract survives the merge.
    ats = [e["at"] for e in events]
    assert ats == sorted(ats, reverse=True)
    # Every contributor is represented in the merged view.
    assert {e["worm"] for e in events} == {f"M{t}" for t in range(threads_n)}


def test_ring_overflow_drops_oldest_not_a_whole_stripe(monkeypatch):
    """Above the cap the ring keeps the newest N, whichever thread wrote them.

    This is the case the capped test above deliberately avoids: one thread's
    entire early block is allowed to age out. What must NOT happen is the
    merge keeping a stale event while discarding a newer one, or the bound
    being applied per stripe instead of to the merged view.
    """
    _reset()
    cap = 60
    monkeypatch.setattr(sr, "_OUTCOME_EVENTS_MAX", cap)
    threads_n, per_thread = 4, cap // 2   # 2x the cap in total
    barrier = threading.Barrier(threads_n)

    def worker(tid):
        barrier.wait(timeout=10)
        for _ in range(per_thread):
            sr.record_outcome("ring", f"O{tid}", success=True, duration_ms=1.0)

    ts = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
        assert not t.is_alive()

    # A sentinel written after the threads must survive and be newest: it
    # proves the merge keeps the LATEST events rather than an arbitrary
    # slice, which a per-shard cap would not guarantee.
    sr.record_outcome("ring", "SENTINEL", success=True, duration_ms=1.0)

    events = sr.get_outcome_events()
    # Bound applies to the merged view, not per shard (4 shards x 30 = 120).
    assert len(events) == cap
    assert events[0]["worm"] == "SENTINEL", "newest event was dropped"
    ats = [e["at"] for e in events]
    assert ats == sorted(ats, reverse=True)
    # Overflow must actually have dropped something, or this proves nothing.
    assert sum(1 for e in events if e["worm"] != "SENTINEL") == cap - 1


def test_reset_clears_every_stripe():
    _reset()
    def worker(tid):
        for _ in range(20):
            sr.record_outcome("ring", f"X{tid}", success=True)

    ts = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    assert sr.get_outcome_events()
    _reset()
    assert sr.get_outcome_events() == []
    assert sr.get_homeostat_view() == {}


# ── 5. the reflex moved, it did not vanish ───────────────────────────

def test_reflex_still_fires_at_the_threshold():
    _reset()
    from CORE.worm_registry import WORM_REGISTRY, discover_worms
    discover_worms()  # ensure the reflex's registry lookup has a target
    if not WORM_REGISTRY:
        return  # registry unavailable in this checkout; nothing to assert

    worm = sorted(WORM_REGISTRY)[0]
    before = float(WORM_REGISTRY[worm].get("cooldown_seconds", 30.0))

    for _ in range(sr._REFLEX_FAIL_THRESHOLD):
        sr.record_outcome("reflex", worm, success=False, error="unit-test")

    after = float(WORM_REGISTRY[worm].get("cooldown_seconds", 30.0))
    assert after > before, "reflex backoff no longer fires"
    assert f"reflex:{worm}" in sr.get_scars()


def test_scar_heals_gradually_after_the_reflex():
    _reset()
    from CORE.worm_registry import WORM_REGISTRY, discover_worms
    discover_worms()
    if not WORM_REGISTRY:
        return

    worm = sorted(WORM_REGISTRY)[0]
    for _ in range(sr._REFLEX_FAIL_THRESHOLD):
        sr.record_outcome("reflex", worm, success=False)
    assert f"reflex:{worm}" in sr.get_scars()

    # G7: three consecutive successes to fully heal.
    for _ in range(2):
        sr.record_outcome("reflex", worm, success=True)
    assert f"reflex:{worm}" in sr.get_scars(), "scar healed early"
    sr.record_outcome("reflex", worm, success=True)
    assert f"reflex:{worm}" not in sr.get_scars(), "scar never healed"


# ── 6. the stripe substrate itself ───────────────────────────────────

def test_stripe_helpers_are_consistent_and_bounded():
    assert sr._STRIPE_COUNT == len(sr._HOMEOSTAT_STRIPES) == len(sr._Q_STRIPES)
    # Same key -> same stripe, every time.
    for key in ("mesh:SpineMapperWorm", "synapse:HomeostatWorm", "route:a>b"):
        assert sr._homeostat_stripe(key) is sr._homeostat_stripe(key)
        assert sr._q_stripe(key) is sr._q_stripe(key)
    idx = sr._stripe_index("mesh:SpineMapperWorm")
    assert 0 <= idx < sr._STRIPE_COUNT


def test_distinct_keys_are_not_all_pinned_to_one_stripe():
    """If striping degenerated to a single lock the change bought nothing."""
    keys = [f"nature{i}:Worm{i}" for i in range(120)]
    used = {sr._stripe_index(k) for k in keys}
    assert len(used) >= sr._STRIPE_COUNT // 2, f"only {used} stripes used"


def test_barrier_is_reentrant_safe_and_nests():
    """`with _HOMEOSTAT_LOCK:` is still the spelling readers use, and code
    inside it may take it again (RLock stripes) without deadlocking."""
    with sr._HOMEOSTAT_LOCK:
        with sr._HOMEOSTAT_LOCK:
            sr.get_q_weights()
