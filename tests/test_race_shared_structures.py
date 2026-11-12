# coding: utf-8
"""PHASE C — concurrency race tests.

Engram #784, phase C. A race test that passes ONCE proves nothing: on a
free-threaded build (py315t, GIL off) the interleaving that loses a write is a
matter of probability, so every test here is barrier-synchronized — the threads
collide at the same instant instead of drifting apart — and repeated.

The pre-registered baseline for this phase said "race-test files 0". Measured
before this file existed: 15 files mentioned ``ThreadPoolExecutor`` or
``Barrier``, and 4 used a ``Barrier``. The baseline was wrong; it is corrected in
the lane report rather than quietly satisfied by writing the first file.

Targets are shared structures with a real write path:

  * ``failure_ledger`` — the process-wide ledger every fail-open handler records
    into. A lost update here under-reports degradation, which is the exact
    failure the module's docstring exists to prevent.
  * ``LocalMemoryStore`` on a TEMP database — the technique store's substrate.
    Never the live store: this file writes hundreds of rows.
  * ``SparseMoE.route`` — read-only after construction. Included because "no
    shared mutable state" is a claim worth testing rather than asserting.
"""

import sqlite3
import sys
import threading
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import CORE.failure_ledger as ledger_mod  # noqa: E402
from CORE.WORMS.expansion.cutting_edge_model import SparseMoE  # noqa: E402
from CORE.failure_ledger import failure_notes, note_failure, reset_notes  # noqa: E402
from CORE.local_memory import LocalMemoryStore  # noqa: E402

@pytest.fixture(autouse=True)
# start of the recorder guard release
#
# The recorder guard is a production flood policy -- 8 rows/s, burst 200 -- held
# process-wide. A race test asserts "every concurrent write landed", which is a
# claim about the store's locking, not about the throttle: with the bucket empty
# from an earlier test the writes are dropped silently and the assertion fails on
# the guard's budget. Released for the duration of each test; the throttle's own
# behaviour is exercised by the guard's own tests.
def _recorder_guard_released(monkeypatch):
    from CORE import recorder_guard
    monkeypatch.setattr(recorder_guard, "_DISABLED", True, raising=False)
    recorder_guard.guard().reset()


#: Repetitions. One pass proves nothing about a race; this is the whole point of
#: the phase, so the count is named rather than inlined.
REPEATS = 3


def _stampede(n_threads, worker, timeout=180):
    """Run ``worker(i)`` on ``n_threads`` threads that all start together.

    A ``Barrier`` is what makes this a race test rather than a load test: without
    it the first thread is usually finished before the last one starts, and the
    shared-state window is never entered. Errors are collected and re-raised in
    the main thread so a thread failure can never be swallowed by the join.
    """
    barrier = threading.Barrier(n_threads)
    errors = []

    def run(index):
        try:
            barrier.wait(timeout=timeout)
            worker(index)
        except Exception as exc:                     # noqa: BLE001 - reported below
            errors.append("thread %d: %r" % (index, exc))

    threads = [threading.Thread(target=run, args=(i,), daemon=True)
               for i in range(n_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout)

    assert not errors, "a raced worker raised: %s" % errors
    assert all(not t.is_alive() for t in threads), "a raced worker never finished"


# ── failure_ledger: no lost update ──────────────────────────────────────────

def test_failure_ledger_loses_no_note_under_a_barrier_stampede():
    """Every recorded failure must be counted, however the threads interleave.

    A lost update here is the quiet kind of wrong: the ledger still answers, and
    answers low.
    """
    n_threads, per_thread = 12, 200
    for _ in range(REPEATS):
        reset_notes()

        def worker(_index):
            for _ in range(per_thread):
                note_failure("race:stampede", ValueError("boom"))

        _stampede(n_threads, worker)
        notes = failure_notes()
        assert notes.get("race:stampede:ValueError") == n_threads * per_thread
        assert "__lost__:recorder" not in notes, "the recorder must not have degraded"


def test_a_concurrent_reader_never_observes_a_count_move_backwards():
    """A snapshot must be a snapshot: tearing would show a count decreasing."""
    reset_notes()
    total = 600
    stop = threading.Event()
    # One list PER reader. Sharing one list across readers would interleave their
    # appends and destroy the observation order, so a shared list cannot answer
    # "did any single reader see a decrease" -- an earlier draft of this test
    # made exactly that mistake and failed on its own fixture, not on the ledger.
    observed_per_reader = [[], []]

    def writer():
        for _ in range(total):
            note_failure("race:monotone", TypeError("boom"))

    def reader(slot):
        mine = observed_per_reader[slot]
        while not stop.is_set():
            mine.append(failure_notes().get("race:monotone:TypeError", 0))

    write_thread = threading.Thread(target=writer)
    read_threads = [threading.Thread(target=reader, args=(slot,), daemon=True)
                    for slot in range(len(observed_per_reader))]
    for thread in read_threads:
        thread.start()
    write_thread.start()
    write_thread.join(timeout=180)
    stop.set()
    for thread in read_threads:
        thread.join(timeout=30)

    assert failure_notes()["race:monotone:TypeError"] == total
    for slot, seen in enumerate(observed_per_reader):
        assert seen, "reader %d never sampled the ledger, so its pass is vacuous" % slot
        assert seen == sorted(seen), "reader %d saw the count go backwards" % slot
        assert max(seen) <= total, "reader %d saw more notes than were written" % slot


def test_a_lock_broken_recorder_reports_a_lost_note_instead_of_a_silent_zero(monkeypatch):
    """The recorder may never call itself; it may never be silently zero either.

    This is the module's own 2026-10-09 defect made executable: a write that
    fails must surface as ``__lost__:recorder`` so the reported count is known to
    be a floor rather than believed to be the truth.
    """
    reset_notes()

    class _ExplodingLock:
        def __enter__(self):
            raise RuntimeError("lock broken")

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(ledger_mod, "_FAILURE_LOCK", _ExplodingLock())
    note_failure("probe:broken", ValueError("boom"))     # must not raise

    # ``failure_notes()`` takes the same lock, so it cannot be called while the
    # lock is broken -- it would raise and the test would be measuring its own
    # probe. The counter is read directly here; the public view is checked after
    # the lock is restored.
    assert ledger_mod._LOST_NOTES[0] >= 1, "a recorder that could not record must say so"

    monkeypatch.undo()
    notes = failure_notes()
    assert notes.get("__lost__:recorder", 0) >= 1, "the lost count must reach the public view"
    assert "probe:broken:ValueError" not in notes

    reset_notes()
    assert "__lost__:recorder" not in failure_notes(), "a healthy ledger claims no lost notes"


# ── the technique store's substrate, on a temp database ─────────────────────

def test_a_temp_store_keeps_every_concurrent_write(tmp_path):
    """Every committed row must survive a concurrent writer stampede.

    Two distinct failures are caught here and they are not the same: a rowid that
    repeats would mean two events collapsed into one, and a rowid of 0 would mean
    the store silently refused a write while reporting success.
    """
    n_threads, per_thread = 8, 25

    # A FRESH database per repetition. Reusing one store would accumulate rows
    # across repeats and the count assertion would then be measuring the repeat
    # index rather than the race -- the failure mode an earlier draft of this
    # test hit (400 rows on pass 2) before the fixture was corrected.
    for repeat in range(REPEATS):
        store = LocalMemoryStore(db_path=str(tmp_path / ("race_store_%d.db" % repeat)))
        ids = []
        collected = threading.Lock()

        def worker(index, _store=store, _ids=ids, _lock=collected):
            mine = [_store.record_event("race-probe", "concurrent_write",
                                        {"thread": index, "n": n})
                    for n in range(per_thread)]
            with _lock:
                _ids.extend(mine)

        _stampede(n_threads, worker)

        assert len(ids) == n_threads * per_thread
        assert all(isinstance(value, int) and value > 0 for value in ids), \
            "a write returned 0, which is the store's own 'refused' signal"
        assert len(set(ids)) == len(ids), "two committed rows shared a rowid"

        conn = sqlite3.connect(store.db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE source = 'race-probe'").fetchone()
        finally:
            conn.close()
        assert row[0] == n_threads * per_thread


# ── "read-only after construction" is a claim, so test it ───────────────────

def test_route_under_concurrency_equals_the_serial_result():
    """Concurrent routing must not change an answer.

    ``route`` reads ``self.gate`` and writes nothing, so the serial result is a
    valid oracle. A divergence would mean a hidden cache or a shared buffer
    inside the router -- a real hazard under free-threading, and invisible to a
    single-threaded test.
    """
    moe = SparseMoE(d_model=8, n_experts=8, top_k=2, seed=11)
    tokens = [[float((i * 7 + j) % 13) for j in range(8)] for i in range(24)]
    expected = moe.route(tokens)

    results = []
    gathered = threading.Lock()

    def worker(_index):
        got = moe.route(tokens)
        with gathered:
            results.append(got)

    _stampede(8, worker)
    assert len(results) == 8
    assert all(result == expected for result in results), \
        "concurrent route diverged from the serial result"
