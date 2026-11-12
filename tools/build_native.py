"""build_native -- compile one .pyx into a GIL-free .pyd on this box.

The recipe the _replay_cy kernel evolved into, bottled so ANY package can
use it. Two facts do all the work, and both were earned by a failed build,
not assumed:

1. cl needs //DPy_GIL_DISABLED=1 on the command line. The define comes from
   the build system, never from pyconfig.h (which only *tests* it). Without
   it the pragma in pyconfig.h pulls python316.lib -- which does not exist
   -- and a module that somehow still links refuses to import with
   ``SystemError: invalid PyModuleDef`` on a free-threaded interpreter.
2. Flags are single-slash (/I, /D, /OUT:) with backslash values (C:\\...).
   The double-slash form only survives when bash mangles it first; passed
   straight from subprocess, cl rejects it (D9002). Forward slashes in the
   *values* also die under MSYS path conversion; backslashes never do.

Measured paths below are this box (MSVC 14.51, SDK 10.0.26100.0, the
self-built 3.16.0a1t tree at C:\\Azazael\\.py316t, Cython 3.3.1 in the
py316t venv). Override with env vars for other boxes; fail loudly when a
path is missing instead of emitting a half-linked binary.

Usage:
    python tools/build_native.py src/eve_unleashed/_replay_cy.pyx
"""
from __future__ import annotations

import os
import subprocess
import sys

MSVC = os.environ.get("AZAZAEL_MSVC",
                       r"C:\Program Files\Microsoft Visual Studio\18"
                       r"\Community\VC\Tools\MSVC\14.51.36231")
SDK = os.environ.get("AZAZAEL_SDK", r"C:\Program Files (x86)\Windows Kits\10")
SDK_VER = os.environ.get("AZAZAEL_SDK_VER", "10.0.26100.0")
PYT = os.environ.get("AZAZAEL_PYT", r"C:\Azazael\.py316t")


def run(cmd, cwd):
    print("+ " + " ".join(cmd))
    # Bounded: a wedged compiler must fail loudly, never hang the caller.
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", timeout=600)
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0:
        if proc.stderr.strip():
            print(proc.stderr.strip())
        raise SystemExit("build step failed rc=%d: %s"
                         % (proc.returncode, cmd[0]))
    elif proc.stderr.strip():
        print(proc.stderr.strip())


def main(argv):
    if len(argv) != 2 or not argv[1].endswith(".pyx"):
        raise SystemExit("usage: build_native.py <module>.pyx")
    pyx = os.path.abspath(argv[1])
    here = os.path.dirname(pyx)
    base = os.path.basename(pyx)[:-4]
    tag = os.environ.get("AZAZAEL_EXT_SUFFIX", ".cp316t-win_amd64.pyd")
    for path in (MSVC, SDK, PYT, pyx):
        if not os.path.exists(path):
            raise SystemExit("missing (set AZAZAEL_* override): " + path)
    c_file = os.path.join(here, base + ".c")
    obj = os.path.join(here, base + ".obj")
    pyd = os.path.join(here, base + tag)
    run([sys.executable, "-m", "cython", "-3", pyx], here)
    cl = os.path.join(MSVC, "bin", "Hostx64", "x64", "cl.exe")
    run([cl, "/nologo", "/c", c_file, "/Fo" + obj, "/DPy_GIL_DISABLED=1",
         "/I" + PYT + r"\Include",
         "/I" + MSVC + r"\include",
         "/I" + SDK + r"\Include\%s\ucrt" % SDK_VER,
         "/I" + SDK + r"\Include\%s\um" % SDK_VER,
         "/I" + SDK + r"\Include\%s\shared" % SDK_VER], here)
    link = os.path.join(MSVC, "bin", "Hostx64", "x64", "link.exe")
    run([link, "/nologo", "/DLL", "/OUT:" + pyd, obj,
         "/LIBPATH:" + PYT + r"\libs",
         "/LIBPATH:" + MSVC + r"\lib\x64",
         "/LIBPATH:" + SDK + r"\Lib\%s\um\x64" % SDK_VER,
         "/LIBPATH:" + SDK + r"\Lib\%s\ucrt\x64" % SDK_VER,
         "kernel32.lib"], here)
    size = os.path.getsize(pyd)
    if size == 0:
        raise SystemExit("linker wrote an empty module: " + pyd)
    print("built %s (%d bytes)" % (pyd, size))


if __name__ == "__main__":
    main(sys.argv)
