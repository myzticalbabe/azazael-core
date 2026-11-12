# coding: utf-8
"""Bounded filesystem predicates -- a probe must not hang its regulator.

A MISSING path answers instantly. An UNREACHABLE one does not: a disconnected
network drive, a stale SMB share, a spun-down disk or an offline archive volume
blocks *inside* the stat call for minutes, and neither ``os.path.exists`` nor
``Path.is_dir`` accepts a timeout.

``try/except`` cannot help either, because **a hang is not an exception**. That
is how five separate guards in this codebase came to look correct while
protecting nothing. All five were reached from ``SpineCycleWorm.run_cycle`` and
all five hung the test suite instead of failing a test:

1. ``mesh.py`` ``PartitionHealerWorm.detect`` -> ``os.path.exists(r"G:\\source-of-truth")``
2. ``mesh.py`` ``PartitionHealerWorm.detect`` -> ``subprocess(["wsl", "-l", "-v"])``
   piped into ``communicate()`` (a pipe deadlock rather than a stat -- same shape)
3. ``spine.py`` ``_publish_local_shards`` -> ``Path(cand).is_dir()`` over
   ``MODEL_ROOTS``
4. ``consensus.py`` ``LeaderElectorWorm.apply_placement`` -> ``open()`` of
   ``G:/source-of-truth/azazael_models/placement.json``
5. ``nerve.py`` module scope -> ``import torch``, blocking in
   ``platform.machine()`` during ``_load_dll_libraries`` (an import is a probe
   too, and it was the only one that had a ``try/except`` around it that could
   never have fired)

The rule this module exists to enforce: **a probe must not be able to hang the
caller that regulates it.** A regulator disabled by one of the things it
regulates is worse than no regulator, because it reports nothing at all.

Bounded predicates return ``None`` for "the probe did not answer", and every
caller must decide what unknown means for it. Silence must never be read as
health -- for a mount check, "unknown" is treated as unavailable, because the
fabric genuinely cannot use a path it cannot reach.

There is a third outcome, and it is not the same finding as the second: a probe
can be **denied by our own compliance guard** (``sentinel_enforcer`` refuses a
write-mode ``open`` on any thread without its thread-local permit). Denial used
to be indistinguishable from no-answer, which is how the whole backend came to
report "unknown" for reachable paths while the real cause was the guard refusing
the probe's own scratch file (Error #197). It is now named: the guard is
extended across the thread boundary the probe creates (``_carried_permit``),
and any denial that still happens is counted (``probe_backend_stats``) and
reported as ``denied`` -- evidence about the guard, never about the filesystem.

------

Why there is also a cache
-------------------------

Bounding the probe replaces an infinite hang with a *fixed latency floor*: each
unreachable root costs one full timeout on every single cycle. ``MODEL_ROOTS``
holds two dead ``G:`` paths and ``placement.json`` is a third, so one fabric
cycle paid 15s of pure waiting -- and that measured floor is what broke two
tests, not the hang:

* ``test_spine_heart_starts_and_stops`` polls for 15s; the first beat needed
  16.9s while the heartbeat thread held the process-global cycle gate.
* ``test_spine_cycle_drives_synapse_layer_with_real_data`` then received
  ``{"skipped": "cycle_already_running_global"}`` -- a valid cycle report with
  no ``synapse_routing`` key at all.

So an answer of "no answer" is remembered for ``UNREACHABLE_TTL_S``. The honest
trade-off: a mount that comes back is not noticed for up to that TTL. A dead
mount is worth optimising for and a returning mount is bounded and visible
(``unreachable_cache_stats()``), so the TTL is a deliberate choice rather than a
hidden one.

------

Why the first probe of a path leaves the process
------------------------------------------------
Bounding the wait was necessary and not sufficient. Measured 2026-09-17: with
``G:`` held by a USB card reader whose card had stopped answering, a pytest
session printed ``13 passed in 15.06s`` and then **could not exit at all** --
SIGKILL was the only way to end it, and the only live thread was the parked
``fs-exists`` probe. `bounded_call` abandons the *result*; it cannot abandon the
*thread*, which sits in the redirector's kernel wait. Daemon status stops CPython
joining that thread, but it does not stop Windows finishing DLL detach, which is
where the process then wedges. A thread cannot be killed; a child process can.

So the first probe of each path is delegated to a disposable child (see
``_child_probe``) and the path is afterwards remembered as responsive, which is a
property of the volume and not of the call -- a path that answered a stat in a
child will answer it in-process. ``probe_backend_stats()`` reports how many
delegations were needed.
"""

import contextlib
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, List
import json
import subprocess

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: How long a probe waits before declaring "no answer". Deliberately short:
#: a mount that cannot answer within a couple of seconds is unusable for the
#: work being scheduled, and the cost is paid on the cold probe of every dead
#: path. At 5.0s, three dead paths put a 15s floor under the first heartbeat
#: and broke two tests whose deadlines were 15s -- the timeout was the bug.
DEFAULT_TIMEOUT_S = 2.5

#: How long "the probe did not answer" is remembered before retrying the path.
UNREACHABLE_TTL_S = 30.0

__all__ = ["bounded_call", "bounded_exists", "bounded_first_dir",
           "bounded_is_dir", "bounded_path_probe", "bounded_read_text",
           "bounded_subprocess_output", "is_denied", "probe_backend_stats",
           "unreachable_cache_stats", "clear_unreachable_cache"]

# ── Three outcomes, three representations ────────────────────────────
# A bounded probe can end in exactly three ways, and collapsing any two of them
# has already cost this codebase a wrong verdict:
#
#   answered     -> the value (or the documented False for a predicate)
#   unanswered   -> ``None`` / ``_UNANSWERED`` (the deadline passed)
#   DENIED       -> our OWN compliance guard refused the probe's own scratch write
#
# The third one used to be indistinguishable from the second. Measured
# 2026-09-22 (Error #197): `sentinel_enforcer` arms a process-global audit hook
# that denies any write-mode ``open`` on a thread without the thread-local
# permit, and `bounded_subprocess_output` opens ``tempfile.TemporaryFile()`` on
# its worker -- so in one process every delegated probe reported
# ``ok: false`` / ``_UNANSWERED`` and the backend said "unknown" for paths that
# were plainly reachable. "We refused to look" is a finding about the GUARD; it
# must never be reported as a finding about the FILESYSTEM.

_denied_probes = 0
_denied_lock = threading.Lock()


def _note_denied() -> None:
    global _denied_probes
    with _denied_lock:
        _denied_probes += 1


def _carried_permit() -> Callable[[], Any]:
    """Return a factory of context managers covering ONE worker each.

    Called on the STARTER thread, so the permit is read where it was granted;
    each manager is entered on a worker (see
    ``sentinel_enforcer.worker_permit``, which restores it and nothing more --
    a worker started with no permit is still denied).

    A factory rather than a manager because ``bounded_first_dir`` starts N
    threads and a context-manager instance can only be entered once.

    Deliberately keyed off ``sys.modules`` rather than an import: importing
    ``sentinel_enforcer`` ARMS the audit hook as a side effect, and a probe must
    not be able to arm the guard in a process that chose not to have it.
    """
    guard = sys.modules.get("CORE.sentinel_enforcer")
    if guard is None:
        return contextlib.nullcontext
    try:
        held = bool(guard.permit_active())
        factory = guard.worker_permit
    except Exception as exc:
        note_failure("fs_probe:L159", exc)
        return contextlib.nullcontext

    def _make():
        return factory(held)

    return _make


def is_denied(result: Any) -> bool:
    """True when a probe result records that OUR OWN guard denied the probe."""
    return isinstance(result, dict) and bool(result.get("denied"))

_unreachable_until: Dict[str, float] = {}
_unreachable_hits = 0

# ── Out-of-process FIRST probe ──────────────────────────────────────
# Measured 2026-09-17 against `G:` (a USB card reader whose card had stopped
# answering): `bounded_call` fixed the WAIT but not the PROCESS. The worker
# thread stayed parked inside the Windows redirector, and a thread parked in a
# native call prevents clean interpreter shutdown -- a pytest session printed
# "13 passed in 15.06s" and then could not exit at all; only SIGKILL ended it,
# because daemon status stops Python joining a thread but not Windows finishing
# DLL detach. **A thread cannot be killed. A child process can.**
#
# So the FIRST probe of each path runs in a disposable child, and a path is
# remembered as RESPONSIVE once that child answers (with a value *or* with an
# error -- either way the volume answered). A path that answered a stat in a
# child answers it in-process, so later probes are ordinary in-process stats
# and the cost is one child per distinct path per process. `MODEL_ROOTS`,
# `mesh.py` and `consensus.py` probe a handful of fixed paths, so this is one
# interpreter start per process, not one per cycle.
#
# Paths that stay silent are NOT remembered; they take the existing
# `UNREACHABLE_TTL_S` route, so the retry cadence is unchanged.
_RESPONSIVE_PATHS: set = set()
_responsive_lock = threading.Lock()
_child_probes = 0

#: Distinguishes "the child answered None" from "the child never answered".
_UNANSWERED = object()

_CHILD_PROBE_SRC = r'''
import json, os, sys
op, target, outp = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    if op == "exists":
        out = {"ok": True, "v": bool(os.path.exists(target))}
    elif op == "isdir":
        out = {"ok": True, "v": bool(os.path.isdir(target))}
    else:
        with open(target, "r", encoding="utf-8") as fh:
            out = {"ok": True, "v": fh.read()}
except Exception as exc:
    out = {"ok": False, "err": type(exc).__name__, "msg": str(exc)[:200]}
with open(outp, "w", encoding="utf-8") as fh:
    json.dump(out, fh)
'''
_unreachable_lock = threading.Lock()


def _recently_unreachable(key: str) -> bool:
    """True while a previous probe of ``key`` is still known unanswered."""
    global _unreachable_hits
    now = time.monotonic()
    with _unreachable_lock:
        expiry = _unreachable_until.get(key)
        if expiry is None:
            return False
        if now >= expiry:
            del _unreachable_until[key]
            return False
        _unreachable_hits += 1
        return True


def _note_unreachable(key: str, ttl_s: float = UNREACHABLE_TTL_S) -> None:
    with _unreachable_lock:
        _unreachable_until[key] = time.monotonic() + ttl_s


def unreachable_cache_stats() -> Dict[str, Any]:
    """Observability for the latency cache -- what is being suppressed, and
    how many probes were answered from memory instead of from a dead mount."""
    with _unreachable_lock:
        now = time.monotonic()
        live = {k: round(v - now, 1) for k, v in _unreachable_until.items()
                if v > now}
    return {"ttl_s": UNREACHABLE_TTL_S, "suppressed_paths": len(live),
            "seconds_left": live, "served_from_cache": _unreachable_hits}


def clear_unreachable_cache() -> None:
    """Forget every remembered non-answer (used by tests and by a manual
    'the drive is back, re-probe now' action).

    The responsive-path set is cleared with it, so a re-probe after a drive
    comes back is a fresh out-of-process probe rather than an in-process stat
    against a volume that may have been remounted or replaced.
    """
    with _unreachable_lock:
        _unreachable_until.clear()
    with _responsive_lock:
        _RESPONSIVE_PATHS.clear()


def probe_backend_stats() -> Dict[str, Any]:
    """Observability for the probe backend: how many probes had to be
    delegated to a child, and how many paths proved responsive enough to be
    statted in-process afterwards."""
    with _responsive_lock:
        responsive = len(_RESPONSIVE_PATHS)
    with _denied_lock:
        denied = _denied_probes
    return {"child_probes": _child_probes, "responsive_paths": responsive,
            "denied_probes": denied,
            "default_timeout_s": DEFAULT_TIMEOUT_S,
            "delegating": bool(getattr(sys, "executable", ""))
                          and not getattr(sys, "frozen", False)}


def bounded_call(fn: Callable[[], Any],
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 default: Any = None,
                 label: str = "fs-call") -> Any:
    """Run ``fn`` on a daemon thread and give up after ``timeout_s``.

    Two outcomes are deliberately kept distinct, because collapsing them would
    hide real bugs:

    * ``fn`` raises  -> the exception is re-raised here, so a caller's existing
      ``except`` block keeps working exactly as it did.
    * ``fn`` hangs   -> ``default`` is returned. This is the case that has no
      other representation in the language.

    A thread blocked inside a native call cannot be cancelled, so it is left
    as a daemon: bounded and reported, never joined.
    """
    box: dict = {}
    done = threading.Event()
    permit = _carried_permit()  # read HERE, on the caller's thread

    def _run() -> None:
        with permit():
            try:
                box["value"] = fn()
            except PermissionError as exc:  # our own guard, not the caller's bug
                note_failure("fs_probe:L305", exc)
                _note_denied()
                box["denied"] = True
                box["error"] = exc
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                note_failure("fs_probe:L309", exc)
                box["error"] = exc
            finally:
                done.set()

    worker = threading.Thread(target=_run, name=label, daemon=True)
    worker.start()
    if not done.wait(timeout_s):
        return default
    if "error" in box:
        raise box["error"]
    return box.get("value")


def bounded_subprocess_output(argv, timeout_s: float = 10.0) -> Dict[str, Any]:
    """Run ``argv`` and return its output, or report that it did not answer.

    Three separate hazards, all of which have burned this codebase:

    1. **Pipe deadlock.** ``capture_output=True`` is wrong for ``wsl.exe`` on
       Windows: it is a launcher, so a grandchild (``wslhost.exe`` /
       ``wslrelay.exe``) inherits the stdout pipe. When the timeout fires,
       ``subprocess.run`` kills the direct child and then reaps it with
       ``communicate()`` **without a timeout** -- which blocks while the
       grandchild still holds that pipe. The caller never returns. Routing
       stdout/stderr to TEMP FILES removes the pipe entirely, so there is
       nothing to drain to EOF.
    2. **Unkillable wait.** The whole call runs on a DAEMON thread, so even if
       the OS ignores the kill the interpreter can still exit. (Measured:
       a killed pytest run reported ``13 passed in 15.06s`` and then hung at
       shutdown, because a live ``wsl.exe`` child kept the process alive.)
    3. **Encoding.** ``wsl.exe`` writes UTF-16LE when its output is redirected,
       so a utf-8 decode yields NUL-interleaved text and every substring check
       silently fails to match -- which looks exactly like "both distros fine".

    Returns ``{"ok", "out", "timeout", "error"}``. ``ok`` False means the
    probe did not answer; the caller must decide what unknown means for it.
    """
    box: Dict[str, Any] = {}
    permit = _carried_permit()  # read HERE, on the caller's thread

    def _run() -> None:
        import tempfile
        with permit():
            try:
                with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                    proc = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL,
                                            stdout=out, stderr=err)
                    try:
                        proc.wait(timeout=timeout_s)
                    except Exception as exc:
                        note_failure("fs_probe:L360", exc)
                        box["timeout"] = True
                        try:
                            proc.kill()
                        except Exception as exc:
                            note_failure("fs_probe:L364", exc)
                        try:
                            proc.wait(timeout=1.0)
                        except Exception as exc:
                            note_failure("fs_probe:L368", exc)
                    out.seek(0)
                    err.seek(0)
                    raw = out.read() + err.read()
                if b"\x00" in raw:
                    box["out"] = raw.decode("utf-16-le", "replace")
                else:
                    box["out"] = raw.decode("utf-8", "replace")
            except PermissionError as exc:
                # Our own sentinel denied this probe's scratch write. Record it as
                # a distinct outcome: it is evidence about the guard, not about
                # the path being probed.
                # Our own sentinel denied this probe's scratch write. Record it as
                # a distinct outcome: it is evidence about the guard, not about
                # the path being probed.
                note_failure("fs_probe:L377", exc)
                _note_denied()
                box["denied"] = True
                box["error"] = str(exc)[:120]
            except Exception as exc:  # last resort: never let the probe raise here
                note_failure("fs_probe:L384", exc)
                box["error"] = str(exc)[:120]

    worker = threading.Thread(target=_run, name="bounded-subprocess", daemon=True)
    worker.start()
    worker.join(timeout_s + 1.0)
    alive = worker.is_alive()
    timed_out = bool(box.get("timeout")) or alive
    return {"ok": (not alive) and not timed_out and not box.get("error"),
            "out": box.get("out", ""),
            "timeout": timed_out,
            "denied": bool(box.get("denied")),
            "error": box.get("error", "")}


def _child_probe(op: str, path: str, timeout_s: float) -> Any:
    """Answer one path probe from a disposable child.

    Returns ``("ok", value)`` or ``("error", exc_name, message)``, or
    :data:`_UNANSWERED` when the child never answered. A child that hangs is
    killed by `bounded_subprocess_output`, so nothing is left parked in this
    process -- which is the entire point of the delegation.
    """
    global _child_probes
    exe = getattr(sys, "executable", "") or ""
    if not exe or getattr(sys, "frozen", False):
        return _UNANSWERED  # no interpreter to delegate to (frozen/Nuitka build)
    import tempfile
    fd, outp = tempfile.mkstemp(prefix="fsprobe_", suffix=".json")
    os.close(fd)
    try:
        _child_probes += 1
        res = bounded_subprocess_output(
            [exe, "-c", _CHILD_PROBE_SRC, op, path, outp],
            timeout_s=max(float(timeout_s), 1.0) + 2.0)
        if not res.get("ok"):
            return _UNANSWERED
        try:
            with open(outp, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as exc:
            note_failure("fs_probe:L425", exc)
            return _UNANSWERED
        if not isinstance(payload, dict):
            return _UNANSWERED
        if not payload.get("ok"):
            return ("error", str(payload.get("err") or "OSError"),
                    str(payload.get("msg") or ""))
        return ("ok", payload.get("v"))
    finally:
        try:
            os.unlink(outp)
        except OSError as exc:
            note_failure("fs_probe:L436", exc)


def _bounded_io(path, fn: Callable[[], Any], timeout_s: float,
                label: str, op: str = "exists") -> Any:
    """Shared cache-aware wrapper.

    Never re-probes a path known unanswered, and never performs the FIRST
    probe of a path in this process (see the out-of-process note above).
    """
    key = os.fspath(path)
    if _recently_unreachable(key):
        return None
    with _responsive_lock:
        proven = key in _RESPONSIVE_PATHS
    if not proven:
        first = _child_probe(op, key, timeout_s)
        if first is _UNANSWERED:
            _note_unreachable(key)
            return None
        with _responsive_lock:
            _RESPONSIVE_PATHS.add(key)
        if first[0] == "ok":
            return first[1]
        # The volume DID answer -- it answered with an error. Preserve the
        # in-process contract exactly: predicates are False, a read re-raises.
        if op == "read":
            import builtins
            exc = getattr(builtins, first[1], OSError)
            raise exc(first[2])
        return False
    result = bounded_call(fn, timeout_s=timeout_s, default=None, label=label)
    if result is None:
        _note_unreachable(key)
    return result


def bounded_path_probe(fn: Callable[[], bool],
                       timeout_s: float = DEFAULT_TIMEOUT_S,
                       label: str = "fs-probe") -> Optional[bool]:
    """Run a boolean filesystem probe with a deadline.

    Returns True/False when the probe answers, and ``None`` when it does not.
    Unlike :func:`bounded_call`, a raising probe is reported as False: this is
    a predicate, and "the probe threw" is not a third truth value.
    """
    def _predicate() -> bool:
        try:
            return bool(fn())
        except Exception as exc:
            note_failure("fs_probe:L486", exc)
            return False

    return bounded_call(_predicate, timeout_s=timeout_s, default=None,
                        label=label)


def bounded_exists(path, timeout_s: float = DEFAULT_TIMEOUT_S) -> Optional[bool]:
    """``os.path.exists`` with a deadline. ``None`` = the probe did not answer."""
    return _bounded_io(path, lambda: os.path.exists(os.fspath(path)),
                       timeout_s, "fs-exists", op="exists")


def bounded_is_dir(path, timeout_s: float = DEFAULT_TIMEOUT_S) -> Optional[bool]:
    """``Path.is_dir`` with a deadline. ``None`` = the probe did not answer."""
    return _bounded_io(path, lambda: Path(os.fspath(path)).is_dir(),
                       timeout_s, "fs-isdir", op="isdir")


def bounded_first_dir(candidates, timeout_s: float = DEFAULT_TIMEOUT_S):
    """Return the first candidate that IS a directory, probing them in
    PARALLEL, or ``None`` if none answers.

    This is the right shape for a candidate list like ``MODEL_ROOTS``, where
    the paths are alternatives for one decision rather than a sequence of
    dependencies. Probing them one after another charges the caller the sum of
    the timeouts; probing them together charges the maximum. Order is still
    respected when choosing among the paths that answer.
    """
    ordered = list(candidates or ())
    if not ordered:
        return None
    answers: Dict[str, Optional[bool]] = {}
    lock = threading.Lock()
    permit = _carried_permit()  # read HERE, on the caller's thread

    def _probe(item):
        # The permit has to cross THIS hop too: the probe below starts its own
        # worker, and it reads the permit from the thread it is called on.
        with permit():
            result = bounded_is_dir(item, timeout_s=timeout_s)
        with lock:
            answers[os.fspath(item)] = result

    workers = [threading.Thread(target=_probe, args=(c,), daemon=True,
                                name="fs-firstdir") for c in ordered]
    for w in workers:
        w.start()
    for w in workers:
        # Budget for the out-of-process first probe: one interpreter start plus
        # its own kill timeout, not just the in-process timeout.
        w.join(timeout_s + 3.0)
    for c in ordered:
        if answers.get(os.fspath(c)) is True:
            return c
    return None


def bounded_read_text(path, timeout_s: float = DEFAULT_TIMEOUT_S,
                      label: str = "fs-read") -> Optional[str]:
    """Read a UTF-8 text file with a deadline; ``None`` = no answer in time.

    ``open()`` is a filesystem probe like any other: against an unreachable
    network path it blocks for minutes inside the syscall (hang #4).
    """
    return _bounded_io(path,
                       lambda: Path(os.fspath(path)).read_text(encoding="utf-8"),
                       timeout_s, label, op="read")
