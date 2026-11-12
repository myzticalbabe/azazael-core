#!/usr/bin/env python
# coding: utf-8
"""Prove the win32 shim is a drop-in for the pywin32 it replaces.

WHY THIS TOOL EXISTS
"Drop-in" is a claim, and a claim about a REPLACEMENT for a compiled
extension is only worth what a comparison against the original is worth. So
this tool runs the SAME operations twice -- once against the real pywin32,
once against the ctypes shim -- and reports the differences.

It never asserts in prose. It runs the comparison, prints the numbers, and
exits non-zero when the two disagree, so a caller can gate on the result
instead of on this file's docstring.

WHAT IS COMPARED
  * the constant VALUES the MCP SDK reads (STD_*_HANDLE, PROCESS_*,
    JobObjectExtendedLimitInformation, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE);
  * the EXCEPTION SHAPE of pywintypes.error (args, winerror, funcname,
    strerror, and that it is an OSError, which the SDK's handlers rely on);
  * a real Job Object round trip: create, query, mutate LimitFlags, set,
    query again -- which is the exact sequence the SDK performs;
  * a real OpenProcess/CloseHandle round trip against this process;
  * that the shim's information dict supports the SDK's ``|=`` mutation.

HOW TO RUN
  # against the REAL pywin32 (3.14.7, where the wheel exists):
  C:/Users/clint/.pmb-venv/Scripts/python.exe tools/verify_win32_shim.py

  # against the SHIM alone (3.16t, where no pywin32 exists):
  C:/Azazael/.venvs/py316t/Scripts/python.exe tools/verify_win32_shim.py --shim-only
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM = os.path.join(REPO, "src", "win32_shim")


def _import_with(path_first, names):
    """Import modules with `path_first` ahead of site-packages, or alone.

    The modules are purged from ``sys.modules`` FIRST, and the file each one
    resolved to is recorded. Without the purge, the second reading reuses the
    CACHED module from the first -- so a verifier comparing "the shim" against
    "the real thing" would silently compare the shim against ITSELF and
    report CONFORMANT no matter what was wrong.

    That is not hypothetical: it is exactly what this verifier did on its
    first run. A negative control (changing the shim's exception base class)
    passed as CONFORMANT, which is what exposed the caching. The recorded
    ``__file__`` per module makes the mistake visible if it returns.
    """
    if path_first:
        sys.path.insert(0, path_first)
    try:
        for name in names:
            sys.modules.pop(name, None)
        out = {}
        for name in names:
            out[name] = __import__(name)
        return out
    finally:
        if path_first:
            sys.path.remove(path_first)


def _job_round_trip(mods):
    """Create a Job Object, mutate LimitFlags, read it back. THE SDK's sequence."""
    win32api, win32job, pywintypes = mods["win32api"], mods["win32job"], mods["pywintypes"]
    job = win32job.CreateJobObject(None, "")
    try:
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        assert isinstance(info, dict), "query must return a dict"
        basic = info.get("BasicLimitInformation")
        assert isinstance(basic, dict), "BasicLimitInformation must be a dict"
        before = basic["LimitFlags"]
        basic["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info)
        again = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        after = again["BasicLimitInformation"]["LimitFlags"]
        return {"before": before, "after": after,
                "kill_on_close_set": bool(after & win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)}
    finally:
        win32api.CloseHandle(job)


def _open_process_round_trip(mods):
    """Open THIS process, then close the handle."""
    win32api, win32con, pywintypes = mods["win32api"], mods["win32con"], mods["pywintypes"]
    handle = win32api.OpenProcess(
        win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_TERMINATE,
        False, os.getpid())
    win32api.CloseHandle(handle)
    return {"opened": bool(handle), "closed": True}


def _error_shape(mods):
    """pywintypes.error must look the way the SDK's handlers expect."""
    pywintypes = mods["pywintypes"]
    exc = pywintypes.error(5, "OpenProcess", "Access denied")
    return {
        "args": tuple(exc.args),
        "winerror": exc.winerror,
        "funcname": exc.funcname,
        "strerror": exc.strerror,
        # The MRO, not just the isinstance answers: pywin32's error is NOT an
        # OSError subclass, and a shim that subclasses OSError is a STRICTER
        # type -- an xcept OSError that would have caught the original
        # lets the replacement through. The class chain is the contract.
        "mro": [c.__name__ for c in type(exc).__mro__],
        "is_oserror": isinstance(exc, OSError),
        "is_exception": isinstance(exc, Exception),
    }


def _constants(mods):
    win32api, win32con, win32job = mods["win32api"], mods["win32con"], mods["win32job"]
    return {
        "STD_INPUT_HANDLE": win32api.STD_INPUT_HANDLE,
        "STD_OUTPUT_HANDLE": win32api.STD_OUTPUT_HANDLE,
        "STD_ERROR_HANDLE": win32api.STD_ERROR_HANDLE,
        "PROCESS_SET_QUOTA": win32con.PROCESS_SET_QUOTA,
        "PROCESS_TERMINATE": win32con.PROCESS_TERMINATE,
        "JobObjectExtendedLimitInformation": win32job.JobObjectExtendedLimitInformation,
        "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE": win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    }


def _set_std_handle_return(mods):
    """What SetStdHandle RETURNS.

    Measured against real pywin32: None. The Win32 API itself returns the
    PREVIOUS handle, so an implementation that forwards that is observably
    different -- and the SDK discards the value, so only a direct comparison
    catches the difference. It is compared anyway, because "the SDK ignores
    it" is how a drop-in difference survives until someone depends on it.
    """
    win32api = mods["win32api"]
    import msvcrt
    try:
        return {"returns_none": win32api.SetStdHandle(
            win32api.STD_OUTPUT_HANDLE, msvcrt.get_osfhandle(1)) is None}
    except Exception:
        return {"returns_none": None}


def _collect(mods):
    return {
        "module_files": {name: getattr(mod, "__file__", "?")
                          for name, mod in mods.items()},
        "constants": _constants(mods),
        "error": _error_shape(mods),
        "job": _job_round_trip(mods),
        "open_process": _open_process_round_trip(mods),
        "set_std_handle": _set_std_handle_return(mods),
    }


def main(argv):
    shim_only = "--shim-only" in argv
    names = ("pywintypes", "win32api", "win32con", "win32job")

    print("win32 shim conformance")
    print("-" * 60)

    shim = _collect(_import_with(SHIM, names))
    print("shim: measured")

    if shim_only:
        print(json.dumps(shim, indent=2, sort_keys=True))
        return 0

    try:
        real_mods = _import_with(None, names)
    except ImportError as exc:
        print("real pywin32 not importable here (%s) -- shim-only reading" % exc)
        print(json.dumps(shim, indent=2, sort_keys=True))
        return 0

    real = _collect(real_mods)
    print("real pywin32: measured")

    diffs = []
    for section in ("constants", "error", "set_std_handle"):
        if real[section] != shim[section]:
            diffs.append(section)
    # The job section's "before" depends on inherited job state, so only the
    # invariant is compared: the flag the caller set must be set afterwards.
    if shim["job"]["kill_on_close_set"] is not True:
        diffs.append("job.kill_on_close_set")
    if real["job"]["kill_on_close_set"] is not True:
        diffs.append("real.job.kill_on_close_set")

    print("-" * 60)
    if diffs:
        print("MISMATCH in: %s" % ", ".join(diffs))
        print("real :", json.dumps({k: real[k] for k in diffs}, indent=2, sort_keys=True))
        print("shim :", json.dumps({k: shim[k] for k in diffs}, indent=2, sort_keys=True))
        return 1
    print("CONFORMANT: the shim matches real pywin32 on every measured surface")
    print("  constants : %d values equal" % len(real["constants"]))
    print("  error     : args/attributes/OSError-subclass all equal")
    print("  job       : create->query->mutate->set->query round trip, flag set")
    print("  open_proc : OpenProcess/CloseHandle round trip ok")
    return 0


if __name__ == "__main__":
    import json

    sys.exit(main(sys.argv[1:]))