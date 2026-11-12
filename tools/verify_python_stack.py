#!/usr/bin/env python
"""Probe the import health of the third-party stack AZAZAEL depends on.

Why this exists
---------------
The project runs on the **free-threaded** interpreter (3.14t) while the plain
GIL interpreter (3.14) shares the *same* ``site-packages`` directory. That
sharing creates two failure modes which look like ordinary bugs but are not:

1. **Wrong-ABI binaries.** A free-threaded interpreter only looks for
   ``.cp314t-win_amd64.pyd`` (see ``_imp.extension_suffixes()``); a GIL
   interpreter only looks for ``.cp314-win_amd64.pyd``. Installing a package
   with one interpreter can therefore silently break the other, because pip
   uninstalls the previous build and drops in its own ABI.
2. **Hard crashes.** A GIL-built extension loaded by a 3.14t process can
   segfault at C level. A segfault cannot be caught by ``try/except``, which
   is why a documented ``faiss -> numpy -> stdlib`` fail-open chain did not
   save us: the process dies before any handler runs.

Both are invisible to ``pip list`` and to a normal ``import`` sweep that runs
everything in one process (the first crash takes the report with it). This tool
imports **one module per subprocess** so every failure is attributed to the
module that caused it, and distinguishes an exception from a crash.

Usage
-----
    python tools/verify_python_stack.py                # curated critical set
    python tools/verify_python_stack.py numpy torch    # explicit list
    python tools/verify_python_stack.py --all          # full curated catalogue
    python tools/verify_python_stack.py --json out.json

Exit code is 1 if any CRITICAL module failed, else 0.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------
# Curated module catalogue.
#
# "critical" modules are those a normal cycle cannot run without; a failure
# there fails the tool. "optional" modules are feature-gated in the codebase
# and are reported but do not fail the run.
# --------------------------------------------------------------------------
# A module is CRITICAL only if a normal cycle cannot run without it. The
# project imports numpy/scipy/pandas/sklearn/torch/faiss/pydantic directly;
# everything else is feature-gated somewhere in the tree and is reported
# without failing the run. psycopg2 in particular is used by exactly one
# module and should not be allowed to red-flag the whole stack.
CRITICAL = (
    "numpy", "scipy", "pandas", "sklearn", "torch", "psutil", "yaml",
    "requests", "rich", "PIL", "cryptography", "pydantic", "lxml",
    "faiss", "google.protobuf",
)

OPTIONAL = (
    "transformers", "safetensors", "stable_baselines3",
    "sb3_contrib", "gymnasium", "tensorflow", "tensorboard", "tf2onnx",
    "onnxruntime", "openvino", "optimum", "optuna", "ray",
    "pygame", "pyopencl", "r2pipe", "z3", "watchdog", "wrapt",
    "streamlit", "fastapi", "uvicorn", "flask", "openai", "anthropic",
    "opentelemetry", "win32com", "matplotlib", "networkx", "sympy",
    "joblib", "numba", "llvmlite", "mygrad", "plotly", "bs4", "git",
    "h5py", "pyarrow", "redis", "sqlalchemy", "httpx", "aiohttp",
    "cv2", "tokenizers", "kiwisolver", "contourpy", "fontTools",
    "rpds", "psycopg2", "starlette", "msgpack", "ormsgpack",
)


def _probe(module: str, python: str, timeout: float) -> dict:
    """Import one module in a fresh subprocess; classify the outcome."""
    code = (
        "import importlib, sys\n"
        f"m = importlib.import_module({module!r})\n"
        "print('OK', getattr(m, '__version__', ''))\n"
    )
    started = time.time()
    try:
        proc = subprocess.run(
            [python, "-c", code],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"module": module, "status": "TIMEOUT",
                "detail": f">{timeout:g}s", "seconds": timeout}

    elapsed = round(time.time() - started, 2)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()

    if proc.returncode == 0 and out.startswith("OK"):
        version = out[2:].strip()
        return {"module": module, "status": "OK", "detail": version,
                "seconds": elapsed}

    # A negative return code on Windows is an access violation (segfault).
    # CPython surfaces this as a very large unsigned exit code too.
    rc = proc.returncode
    crashed = rc < 0 or rc >= 0xC0000000 or rc in (3221225477, 3221225725)
    if crashed:
        return {"module": module, "status": "CRASH",
                "detail": f"exit {rc} (C-level fault — not catchable)",
                "seconds": elapsed}

    # Keep the exception type + final message, which is the actionable part.
    lines = [ln for ln in err.splitlines() if ln.strip()]
    detail = lines[-1] if lines else f"exit {rc}"
    for ln in reversed(lines):
        if ln and not ln.startswith(("  ", "\t")) and "Error" in ln:
            detail = ln.strip()
            break
    return {"module": module, "status": "IMPORT-ERROR", "detail": detail,
            "seconds": elapsed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modules", nargs="*", help="explicit module names to probe")
    ap.add_argument("--all", action="store_true",
                    help="probe the full curated catalogue (critical + optional)")
    ap.add_argument("--optional-too", action="store_true",
                    help="alias for --all, keeping CLI symmetry with other tools")
    ap.add_argument("--json", metavar="PATH", help="also write results as JSON")
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="per-module timeout in seconds (default 60)")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter to probe with (default: this one)")
    args = ap.parse_args()

    freethreaded = bool(getattr(sys, "_is_gil_enabled", None)
                        and not sys._is_gil_enabled())
    try:
        import sysconfig
        freethreaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    except Exception:
        pass

    if args.modules:
        targets = list(dict.fromkeys(args.modules))
        critical = set(targets)
    elif args.all or args.optional_too:
        targets = list(dict.fromkeys(CRITICAL + OPTIONAL))
        critical = set(CRITICAL)
    else:
        targets = list(CRITICAL)
        critical = set(CRITICAL)

    print(f"interpreter : {args.python}")
    print(f"version     : {sys.version.split()[0]}"
          f"{' free-threading build' if freethreaded else ' (GIL build)'}")
    print(f"extension suffixes: {__import__('_imp').extension_suffixes()}")
    print(f"probing {len(targets)} module(s), one subprocess each\n")

    results = []
    for name in targets:
        res = _probe(name, args.python, args.timeout)
        results.append(res)
        mark = {"OK": "ok  ", "IMPORT-ERROR": "FAIL",
                "CRASH": "CRASH", "TIMEOUT": "SLOW"}[res["status"]]
        print(f"  [{mark}] {name:<20} {res['detail'][:90]}")

    ok = [r for r in results if r["status"] == "OK"]
    bad = [r for r in results if r["status"] != "OK"]
    crit_bad = [r for r in bad if r["module"] in critical]

    print(f"\n{len(ok)}/{len(results)} import cleanly")
    if bad:
        print("\nfailures:")
        for r in bad:
            tag = "CRITICAL" if r["module"] in critical else "optional"
            print(f"  {r['status']:<12} {r['module']:<20} [{tag}] {r['detail'][:80]}")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"python": args.python, "freethreaded": freethreaded,
             "results": results}, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    if crit_bad:
        print(f"\n{len(crit_bad)} CRITICAL module(s) failed")
        return 1
    print("\nall critical modules healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
