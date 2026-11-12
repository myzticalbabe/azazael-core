#!/usr/bin/env python
# coding: utf-8
"""Selective native-stack capture (Windows only).

faulthandler cannot unwind native C stacks on Windows (no unwinder wired
into CPython) -- see PMB lesson. This module fills that gap ON DEMAND
(default off everywhere; dumps are GB-scale with torch loaded):

  live_dump(pid, path, preset)  -- MiniDumpWriteDump the LIVE process via
      ctypes+DbgHelp. Presets: 'mini' (stacks/handles, KBs), 'normal'
      (stacks+data-segs+indirect memory, MBs), 'full' (everything, GBs).
  watch(cmd, dump_dir, timeout) -- run a command; on TIMEOUT (hang) dump
      the still-live process, then kill it. For crashes (process already
      gone) use tools/capture_crash.ps1 (procdump -e monitor) instead --
      a dead process cannot be dumped after the fact.

Symbol setup for WinDbg analysis is printed by symbol_advice().
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

_PRESETS = {
    # MiniDumpNormal | WithDataSegs | WithHandleData
    "mini": 0x00000000 | 0x00000001 | 0x00000004,
    # + WithProcessThreadData | WithPrivateReadWriteMemory |
    #   WithIndirectlyReferencedMemory | WithFullMemoryInfo
    "normal": 0x00000001 | 0x00000004 | 0x00000100 | 0x00000002 | 0x00000040 | 0x00000800,
    # + WithFullMemory | WithHandleData(full) : GB-scale, complete
    "full": 0x00000002 | 0x00000004 | 0x00000800,
}


def _dbghelp():
    return ctypes.WinDLL("DbgHelp.dll")


def live_dump(pid: int, path, preset: str = "normal") -> Path:
    """Write a minidump of a LIVE process. Raises OSError on failure."""
    if os.name != "nt":
        raise OSError("live_dump is Windows-only")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    PROCESS_QUERY = 0x0400
    PROCESS_VM_READ = 0x0010
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY | PROCESS_VM_READ,
                                           False, pid)
    if not h:
        raise OSError(f"OpenProcess({pid}) failed: {ctypes.get_last_error()}")
    try:
        import msvcrt
        f = open(path, "wb")
        try:
            # NOTE: fileno() is a CRT descriptor, NOT a Windows HANDLE --
            # MiniDumpWriteDump needs the real handle via get_osfhandle.
            ok = _dbghelp().MiniDumpWriteDump(
                h, pid, msvcrt.get_osfhandle(f.fileno()),
                _PRESETS[preset], None, None, None)
            if not ok:
                raise OSError(f"MiniDumpWriteDump failed: {ctypes.get_last_error()}")
        finally:
            f.close()
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    return path


def watch(cmd, dump_dir="state/crashdumps", timeout: float = 600.0,
          preset: str = "normal"):
    """Run cmd; on timeout, dump the LIVE process, kill it, report.

    Returns (returncode, dump_path_or_None). Crash exits (AV) cannot be
    dumped here -- the process is already gone; use capture_crash.ps1.
    """
    dump_dir = Path(dump_dir)
    dump_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(cmd)
    try:
        proc.wait(timeout=timeout)
        return proc.returncode, None
    except subprocess.TimeoutExpired:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = dump_dir / f"hang_{stamp}_{proc.pid}.dmp"
        try:
            live_dump(proc.pid, dest, preset)
        except OSError as e:
            dest = None
            print(f"live_dump failed: {e}")
        finally:
            proc.kill()
            proc.wait()
        return "timeout", (str(dest) if dest else None)


def symbol_advice() -> str:
    return (
        "WinDbg analysis:\n"
        "  .sympath+ srv*C:\\symbols*https://msdl.microsoft.com/download/symbols\n"  # scanner:ignore -- string is a WinDbg .sympath command shown as advice, not a repo path
        "  .sympath+ cache*C:\\symbols;srv*https://pythonsymbols.github.io (CPython)\n"  # scanner:ignore -- string is a WinDbg .sympath command shown as advice, not a repo path
        "  !analyze -v ; .ecxr ; kP 100 ; ~*e!pystack (with PyExt loaded)\n"
        "TTD (heisenbugs): record with WinDbg Preview Time Travel, replay backwards."
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("pid", type=int)
    d.add_argument("path")
    d.add_argument("--preset", default="normal", choices=list(_PRESETS))
    w = sub.add_parser("watch")
    w.add_argument("--timeout", type=float, default=600.0)
    w.add_argument("--preset", default="normal", choices=list(_PRESETS))
    # REMAINDER (not "+"): commands routinely start with flags (-c, -m).
    w.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if a.cmd == "dump":
        p = live_dump(a.pid, a.path, a.preset)
        print(f"wrote {p} ({p.stat().st_size / 1048576:.1f} MB)")
    else:
        rc, dump = watch(a.command, timeout=a.timeout, preset=a.preset)
        print(f"rc={rc} dump={dump}")
        print(symbol_advice())
