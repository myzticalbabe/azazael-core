#!/usr/bin/env python3
"""atomic_store.py — one implementation of the D1/D2 durability pattern.

Three modules grew their own copy of temp-file + os.replace writes and
flock/msvcrt read-modify-write locks (morphed_registry, sweep.json,
antibodies.json). Same shape, same comments, triple the drift surface:
a fix to one copy (e.g. the "a+b"-vs-"ab" binary-mode gate lesson) had
to be replayed by hand everywhere. This module is the single source;
the per-store functions remain thin wrappers that set timestamps and
defaults.

Stdlib-only like failure_ledger, so any layer (CORE or SHARDS) can
import it without cycles.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # POSIX file locking (absent on Windows)
except ImportError:
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt  # Windows file locking (absent on POSIX)
except ImportError:
    msvcrt = None  # type: ignore[assignment]

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def atomic_write_bytes(path: Path | str, payload: bytes, where: str) -> None:
    """Write payload atomically: temp file in same dir + os.replace.

    A crash mid-write leaves the old file intact — never a truncated
    JSON that a loader would silently reset. fsync before replace so
    the data (not just the directory entry) survives power loss.
    `where` names the caller for ledger notes (e.g. "morphed_trainers").
    """
    target = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                                prefix="." + target.stem + ".",
                                suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError as e:
                note_failure(where + ":fsync", e)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError as e:
            note_failure(where + ":tmp-cleanup", e)
        raise


def atomic_write_json(path: Path | str, store: dict, where: str) -> None:
    """JSON-serializing thin wrapper over atomic_write_bytes."""
    atomic_write_bytes(path, (json.dumps(store, indent=1) + "\n").encode("utf-8"),
                       where)


#: In-process mutexes keyed by resolved lock path. msvcrt.locking
#: serializes processes on Windows but NOT threads of one process
#: (locks are process-owned), and fcntl.flock needs separate opens
#: to conflict — so threads always serialize here first, processes
#: via the OS primitive second. RLock: same-thread nesting can't deadlock.
_THREAD_LOCKS: dict = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock_for(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def file_lock(lock_path: Path | str, where: str):
    """Exclusive lock for read-modify-write, cross-platform (D2 pattern).

    Two layers: an in-process RLock (threads — msvcrt can't do this
    alone), then fcntl.flock (POSIX) or msvcrt.locking (Windows) for
    cross-process exclusion. Yields the lock path. When neither OS
    mechanism exists the critical section still runs under the thread
    lock, but the absence is ledger-noted — never silent.
    """
    lock = Path(lock_path)
    thread_lock = _thread_lock_for(str(lock.resolve()))
    thread_lock.acquire()
    try:
        fh = open(str(lock), "ab")  # noqa: PTH123 -- lock handle, binary needs no encoding
        lock_kind = "none"
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                lock_kind = "flock"
            elif msvcrt is not None:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    lock_kind = "msvcrt"
                except OSError as e:
                    note_failure(where + ":msvcrt-lock", e)
                    lock_kind = "none"
            else:
                note_failure(where + ":no-lock-primitive", None)
                lock_kind = "none"
            yield lock
        finally:
            try:
                kind = lock_kind
                if kind == "flock" and fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                elif kind == "msvcrt" and msvcrt is not None:
                    fh.seek(0)
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError as e:
                        note_failure(where + ":unlock", e)
            finally:
                fh.close()
    finally:
        thread_lock.release()
