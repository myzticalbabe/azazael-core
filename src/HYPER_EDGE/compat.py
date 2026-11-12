"""Capability layer -- feature detection, never version sniffing.

Why this exists: every gate in this tree that branched on an interpreter version
was wrong at least once. A version answers "which interpreter am I", not "does
this work here", and the two diverge exactly when a new interpreter lands -- which
is the moment the branch is written. So each capability below answers one question
by probing for the thing itself.

The probe never raises. A probe that fails records its failure in
``_PROBE_FAULTS`` and answers conservatively (the capability is absent), so a
caller reading ``capabilities()`` cannot be told a comfortable lie by a broken
probe.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "ADOPTION",
    "capabilities",
    "default_workers",
    "faults",
    "gil_enabled",
    "parallel_map",
    "report",
    "supports",
]

_PROBE_FAULTS: List[str] = []


def _record(probe: str, fault: BaseException) -> None:
    """Record a probe failure. Never re-raise: a probe may not silence the report."""
    _PROBE_FAULTS.append("%s: %s: %s" % (probe, type(fault).__name__, fault))


def faults() -> Sequence[str]:
    """Probe failures seen so far, newest last. Empty is the normal reading."""
    return tuple(_PROBE_FAULTS)


def gil_enabled() -> bool:
    """True when a global interpreter lock is present.

    A GIL build has no ``sys._is_gil_enabled``. Where the symbol exists it is
    authoritative. An unavailable or failing probe answers True: assuming a lock
    is present costs parallelism, assuming it is absent costs correctness.
    """
    probe = getattr(sys, "_is_gil_enabled", None)
    if probe is None:
        return True
    try:
        return bool(probe())
    except BaseException as fault:  # probe failures are recorded, not propagated
        _record("gil_enabled", fault)
        return True


def _native_pool():
    """This tree's own pool, adopted rather than re-implemented.

    ``CORE.freethreading`` is the canonical source project-wide (its own docstring
    says so), it is stdlib-only, and importing it costs ~0.1 s. A private
    ThreadPoolExecutor here would be a second scheduler with a second notion of
    worker sizing -- the shadowing that HED-8 forbids.
    """
    try:
        from CORE.freethreading import FreeThreadPool  # type: ignore
        return FreeThreadPool
    except BaseException as fault:  # noqa: BLE001 - absence is recorded, never silent
        _record("native_pool", fault)
        return None


def _free_threading() -> bool:
    """Native detector first, then this module's own probe as the fallback."""
    try:
        from CORE.freethreading import is_free_threaded  # type: ignore
        return bool(is_free_threaded())
    except BaseException as fault:  # noqa: BLE001
        _record("native_free_threaded", fault)
        return hasattr(sys, "_is_gil_enabled") and not gil_enabled()


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except BaseException as fault:  # a broken path entry is a probe fault, not a crash
        _record("module:%s" % name, fault)
        return False


def _exception_groups() -> bool:
    try:
        compile("try:\n    pass\nexcept* ValueError:\n    pass\n", "<probe>", "exec")
    except SyntaxError as fault:
        _record("exception_groups", fault)
        return False
    return True


def _per_interpreter_gil() -> bool:
    """3.12+ exposes ``sys._is_gil_enabled``; a per-interpreter GIL build also
    exposes ``concurrent.interpreters`` (3.14+). Both must be present."""
    return _free_threading() and _module_present("concurrent.interpreters")


def _win32_shim() -> bool:
    """Can this host provide the Win32 API surface WITHOUT pywin32?

    Measured 2026-11-11: pywin32 publishes no free-threaded wheel for any
    version -- every wheel it has ever shipped carries a GIL ABI tag
    (cp39..cp315), and none carries a free-threaded one. The MCP Python SDK
    imports it at module level on Windows, so on a free-threaded interpreter
    ``import mcp`` fails before any application code runs.

    The replacement (``src/win32_shim``) rebuilds that surface on ctypes
    against kernel32, which needs no compiled extension and therefore works on
    any interpreter. The probe asks the REAL question -- can a Job Object be
    created and configured HERE -- rather than "is the module importable",
    because an import that cannot find kernel32 would import fine and fail at
    the first call.
    """
    try:
        import os.path

        shim_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "win32_shim")
        if not os.path.isdir(shim_dir):
            return False
        if shim_dir not in sys.path:
            sys.path.insert(0, shim_dir)
        import win32api  # type: ignore
        import win32con  # type: ignore
        import win32job  # type: ignore

        job = win32job.CreateJobObject(None, "")
        try:
            info = win32job.QueryInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            win32job.SetInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation, info)
            again = win32job.QueryInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation)
            return bool(again["BasicLimitInformation"]["LimitFlags"]
                        & win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        finally:
            win32api.CloseHandle(job)
    except BaseException as fault:  # noqa: BLE001 - absence is recorded, never silent
        _record("win32_shim", fault)
        return False


# capability -> what the RC does when it is present. Printed by ``report`` so an
# operator can see which paths are live instead of inferring them from a version.
ADOPTION: Dict[str, str] = {
    "free_threading": "parallel_map runs at os.cpu_count() workers",
    "gil": "parallel_map halves its worker budget; correctness unchanged",
    "tomllib": "overlays are read as TOML as well as JSON",
    "exception_groups": "multi-leg failures raise one ExceptionGroup (legs still recorded)",
    "per_interpreter_gil": "one evolution island per interpreter instead of one pool",
    "concurrent_interpreters": "islands may hold separate module state (true isolation)",
    "win32_shim": "the Win32 API is available without pywin32 (free-threaded safe)",
}


def capabilities() -> Dict[str, Any]:
    """Every capability this process actually has, plus the reading that proves it."""
    caps: Dict[str, Any] = {
        "free_threading": _free_threading(),
        "gil": gil_enabled(),
        "tomllib": _module_present("tomllib"),
        "exception_groups": _exception_groups(),
        "per_interpreter_gil": _per_interpreter_gil(),
        "concurrent_interpreters": _module_present("concurrent.interpreters"),
        "cpu_count": os.cpu_count() or 1,
        "implementation": sys.implementation.name,
        "python": "%d.%d.%d%s" % (
            sys.version_info.major, sys.version_info.minor, sys.version_info.micro,
            sys.version_info.releaselevel.replace("final", ""),
        ),
        "probe_faults": len(_PROBE_FAULTS),
    }
    caps["win32_shim"] = _win32_shim()
    caps["threading_mode"] = ("free-threaded" if caps["free_threading"]
                              else "GIL" if caps["gil"] else "unknown")
    return caps


def supports(capability: str) -> bool:
    """Ask about one capability by name. Unknown names are False, and said so."""
    value = capabilities().get(capability)
    if value is None:
        _record("supports:%s" % capability, KeyError(capability))
        return False
    return bool(value)


def default_workers(caps: Optional[Dict[str, Any]] = None) -> int:
    caps = caps or capabilities()
    cpu = int(caps.get("cpu_count") or 1)
    if caps.get("free_threading"):
        return max(1, cpu)
    return max(1, cpu // 2)


def parallel_map(fn: Callable[[Any], Any], items: Iterable[Any],
                 workers: Optional[int] = None, min_items: int = 2) -> List[Any]:
    """Ordered map over a thread pool.

    Bounded by construction: the pool has ``workers`` slots and the result is
    materialised in input order, so a caller cannot observe completion order as a
    side effect. Falls back to a serial loop when the pool would not help, which
    keeps small inputs deterministic and cheap.
    """
    work = list(items)
    if not work:
        return []
    size = workers if workers is not None else default_workers()
    if size <= 1 or len(work) < min_items:
        return [fn(item) for item in work]
    pool_cls = _native_pool()
    if pool_cls is not None:
        with pool_cls(max_workers=min(size, len(work))) as pool:
            return list(pool.map(fn, work))
    with ThreadPoolExecutor(max_workers=min(size, len(work))) as pool:
        return list(pool.map(fn, work))


def report() -> str:
    """One line an operator can quote, with the live paths named."""
    caps = capabilities()
    live = [name for name, _ in ADOPTION.items() if caps.get(name)]
    return ("HYPER_EDGE compat: python %s (%s) %s | cpu=%d workers=%d | pool=%s | "
            "live=%s | probe_faults=%d" % (
                caps["python"], caps["implementation"], caps["threading_mode"],
                caps["cpu_count"], default_workers(caps),
                "CORE.freethreading.FreeThreadPool" if _native_pool() else "threadpool-fallback",
                ",".join(live) or "none", caps["probe_faults"]))
