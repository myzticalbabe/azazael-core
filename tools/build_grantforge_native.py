"""Build `grantforge.exe` from the stdlib-only core, and prove it runs.

**Backend, decided by measurement, not preference.** The first attempt was Nuitka
on the production 3.15t interpreter. MSVC was found and compilation started, then
failed in Nuitka's own static C source:

    nuitka/allocator.h(906): error C2065: 'op': undeclared identifier
    nuitka/allocator.h(906): error C2198: '_Py_atomic_or_uint8(uint8_t *,uint8_t)': too few arguments

That is Nuitka 4.2rc2's header against Python 3.15rc3's changed `ob_gc_bits` /
atomic API — a toolchain mismatch, not a defect in the program being built. The
3.14 interpreter on this box already carries PyInstaller, which needs no C
compiler and no plugin list, so it is the default here. Nuitka remains selectable
(`--backend nuitka`) for a machine whose Nuitka matches its Python.

Why not `src/grantforge.nuitka.toml`: that config enables the `transformers`,
`torch`, `openvino` and `playwright` plugins for a package that imports pydantic,
typer, rich and lxml at import time. That build already failed
(`src/nuitka-crash-report.xml`, `completion="exception"`) and the disk cannot
hold it.

Usage:
    python tools/build_grantforge_native.py [--backend pyinstaller|nuitka]
                                            [--keep-build] [--skip-verify] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
# A build on this box measures 60-90 s; the ceiling exists so a wedged packer is a
# reported 124 instead of an unbounded hang.
BUILD_TIMEOUT_S = 900
DIST = ROOT / "dist-native"
WORK = ROOT / ".build" / "grantforge"
ENTRY = ROOT / "tools" / "grantforge_entry.py"
PY315 = Path("C:/Azazael/.venvs/py315t/Scripts/python.exe")

ENTRY_SOURCE = '''"""Build entry for grantforge.exe — the only file the packager compiles as a script."""
import sys

from grantforge_native.cli import main

if __name__ == "__main__":
    sys.exit(main())
'''


def _run(args, timeout=120):
    completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def has_module(python, module):
    code, _ = _run([python, "-c", "import %s" % module], timeout=60)
    return code == 0


def interpreter(backend):
    """The interpreter that can actually run `backend`, not the newest one."""
    candidates = [sys.executable, "python", str(PY315)]
    module = "PyInstaller" if backend == "pyinstaller" else "nuitka"
    for candidate in candidates:
        if not candidate:
            continue
        if has_module(candidate, module):
            return candidate
    raise SystemExit("no interpreter on this machine can run %s (looked at %s)"
                     % (backend, ", ".join(str(c) for c in candidates)))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def free_gb(path=ROOT):
    total, used, free = shutil.disk_usage(str(path))
    return {"total_gb": round(total / 2**30, 1), "free_gb": round(free / 2**30, 2)}


def command_for(backend, python, keep_build):
    if backend == "pyinstaller":
        command = [python, "-m", "PyInstaller", "--onefile", "--noconfirm",
                   "--name=grantforge",
                   "--distpath=%s" % DIST,
                   "--workpath=%s" % WORK,
                   "--specpath=%s" % WORK,
                   "--paths=%s" % SRC, str(ENTRY)]
        if not keep_build:
            command.insert(3, "--clean")
        return command
    command = [python, "-m", "nuitka", "--onefile", "--standalone",
               "--assume-yes-for-downloads", "--python-flag=no_site", "--no-pyi-file",
               # `=` forms: Nuitka rejects a space-separated --output-dir even when the
               # path has no spaces ("requires an argument with '--output-dir='").
               "--output-dir=%s" % DIST, "--output-filename=grantforge.exe", str(ENTRY)]
    if not keep_build:
        command.insert(3, "--remove-output")
    return command


def build(backend, keep_build=False):
    ENTRY.write_text(ENTRY_SOURCE, encoding="utf-8")
    DIST.mkdir(exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    python = interpreter(backend)
    command = command_for(backend, python, keep_build)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    log_path = DIST / "build.log"
    # Output to a FILE, never a pipe: the recorded failure of the older build was
    # BrokenPipeError inside optimisation, which reads as a compiler defect when it
    # is a shell defect.
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        log.write("backend: %s\ninterpreter: %s\ncommand: %s\n\n"
                  % (backend, python, " ".join(command)))
        log.flush()
        try:
            completed = subprocess.run(command, cwd=str(ROOT), env=env,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       timeout=BUILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # A wedged packer would otherwise hang the caller forever. 124 is the
            # conventional timeout status, so a caller reads it as "did not finish"
            # rather than as "compiled with errors".
            log.write("\n[build] TIMEOUT after %d s; see %s\n" % (BUILD_TIMEOUT_S, log_path))
            return 124, log_path, python, command
    return completed.returncode, log_path, python, command


def verify(exe):
    checks = []

    def run(args, timeout=180):
        completed = subprocess.run([str(exe)] + args, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=timeout)
        return completed.returncode, (completed.stdout or "")[:300], (completed.stderr or "")[:300]

    for name, args in (("version", ["--version"]),
                       ("demo", ["demo", "--offline"]),
                       ("doctor", ["doctor", "--json"])):
        code, out, err = run(args)
        checks.append({"check": name, "exit": code, "stdout": out.strip(),
                       "stderr": err.strip()})
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build and verify grantforge.exe")
    parser.add_argument("--backend", choices=("pyinstaller", "nuitka"),
                        default="pyinstaller")
    parser.add_argument("--keep-build", action="store_true",
                        help="keep the work directory for diagnosis")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    before = free_gb()
    code, log_path, python, command = build(args.backend, keep_build=args.keep_build)
    exe = DIST / "grantforge.exe"
    report = {"backend": args.backend, "interpreter": str(python), "exit": code,
              "log": str(log_path), "disk_before": before,
              "exe": str(exe) if exe.exists() else None, "ok": False}
    if exe.exists():
        report.update({"bytes": exe.stat().st_size,
                       "mb": round(exe.stat().st_size / 2**20, 1),
                       "sha256": sha256(exe)})
        if not args.skip_verify:
            report["checks"] = verify(exe)
            report["ok"] = code == 0 and all(c["exit"] == 0 for c in report["checks"])
        else:
            report["ok"] = code == 0
    report["disk_after"] = free_gb()
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for key in ("ok", "backend", "interpreter", "exit", "exe", "mb", "sha256"):
            if key in report:
                print("%-12s %s" % (key, report[key]))
        for check in report.get("checks", []):
            first = (check["stdout"] or check["stderr"]).splitlines()
            print("check        %-8s exit %s  %s"
                  % (check["check"], check["exit"], (first[0] if first else "")[:90]))
    if not report["ok"]:
        tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-12:]
        sys.stderr.write("build failed; last lines of %s:\n%s\n"
                         % (log_path, "\n".join(tail)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
