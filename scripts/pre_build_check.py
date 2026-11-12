#!/usr/bin/env python3
"""
pre_build_check.py — Pre-Build Environment Validation

Validates the build environment before attempting Nuitka .exe builds.
Checks: Python version, free-threading status, critical imports, ffmpeg,
tkinter availability, project integrity, and Nuitka patch status.

Usage:
    python scripts/pre_build_check.py          # Full validation
    python scripts/pre_build_check.py --json    # JSON output for CI
    python scripts/pre_build_check.py --quick   # Fast checks only (skip import tests)

Exit codes:
    0 — All checks passed (ready to build)
    1 — Warnings (may build but with issues)
    2 — Errors (cannot build)

Author: Azazael Deployment
Version: 1.0.0
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PROJECT_ROOT / "src"
sys.path.insert(0, str(_SRC))

# ─── Check Results ───
results = []
warnings = []
errors = []


def check(name, passed, detail="", severity="error"):
    """Record a check result."""
    entry = {"name": name, "passed": passed, "detail": detail, "severity": severity}
    results.append(entry)
    if not passed:
        if severity == "error":
            errors.append(entry)
        else:
            warnings.append(entry)
    return passed


# ─── Python Version ───
def check_python_version():
    vi = sys.version_info
    ok = vi.major == 3 and vi.minor >= 14
    detail = f"Python {vi.major}.{vi.minor}.{vi.micro}"
    if not ok:
        detail += " (REQUIRED: Python 3.14+)"
    return check("Python >= 3.14", ok, detail)


def check_free_threading():
    try:
        ft = not sys._is_gil_enabled()
        detail = "Free-threaded (GIL disabled)" if ft else "Standard (GIL enabled — Nuitka builds may work but free-threading optimizations unavailable)"
        return check("Free-threading (3.14t)", ft, detail, "warning")
    except AttributeError:
        return check("Free-threading (3.14t)", False, "Cannot determine GIL status (not a 3.13+ build)", "warning")


# ─── Critical Imports ───
CRITICAL_IMPORTS = [
    ("PIL (Pillow)", "from PIL import Image"),
    ("psutil", "import psutil"),
    ("tkinter", "import tkinter"),
]


def check_critical_imports(quick=False):
    all_ok = True
    if quick:
        from importlib.util import find_spec
        for name, _ in CRITICAL_IMPORTS:
            pkg = name.split()[0]  # "PIL (Pillow)" -> "PIL"
            spec = find_spec(pkg)
            if spec is not None:
                check(f"Import: {name}", True, f"Available ({spec.origin or 'built-in'})")
            else:
                all_ok &= check(f"Import: {name}", False, "NOT INSTALLED", "error")
        return all_ok

    for name, stmt in CRITICAL_IMPORTS:
        try:
            exec(stmt)
            check(f"Import: {name}", True, "Available")
        except ImportError as e:
            all_ok &= check(f"Import: {name}", False, str(e), "error" if "tkinter" in name else "warning")
    return all_ok


# ─── FFmpeg ───
def check_ffmpeg():
    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not ffmpeg:
        # Check known paths
        candidates = [
            r"C:\Users\clint\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                ffmpeg = c
                break
    if ffmpeg:
        return check("FFmpeg available", True, ffmpeg)
    return check("FFmpeg available", False, "Not found — install with: winget install Gyan.FFmpeg", "warning")


# ─── Project Integrity ───
def check_project_integrity():
    required = [
        "src/CORE/azazael_ctl.py",
        "src/CORE/worm_registry.py",
        "src/CORE/WORMS/CLUSTERWORMS.py",
        "azazael.yaml",
        "pyproject.toml",
    ]
    all_ok = True
    for f in required:
        path = _PROJECT_ROOT / f
        if path.exists():
            check(f"Project file: {f}", True, f"{path.stat().st_size / 1024:.0f} KB")
        else:
            all_ok &= check(f"Project file: {f}", False, "MISSING", "error")
    return all_ok


# ─── Nuitka Status ───
def check_nuitka():
    # Nuitka is currently SUSPENDED on 3.14.5t
    try:
        import nuitka
        version = getattr(nuitka, "__version__", "unknown")
        detail = f"Nuitka {version} installed — NOTE: builds SUSPENDED on 3.14.5t (CompiledFrameType.c error)"
        return check("Nuitka available", True, detail, "warning")
    except ImportError:
        return check("Nuitka available", False, "Not installed — SUSPENDED on 3.14.5t anyway", "warning")


# ─── Worm Registry ───
def check_registry():
    try:
        from CORE.worm_registry import discover_worms, validate_registry
        t0 = time.time()
        discover_worms()
        report = validate_registry()
        elapsed = time.time() - t0
        detail = f"{report['total_worms']} worms, {report['categories']} categories, status={report['status']} ({elapsed:.1f}s)"
        ok = report["status"] != "critical"
        severity = "error" if report["status"] == "critical" else "warning" if report["status"] == "warn" else "info"
        return check("Worm registry", ok, detail, severity)
    except Exception as e:
        return check("Worm registry", False, str(e), "error")


# ─── Disk Space ───
def check_disk_space():
    try:
        free = shutil.disk_usage(_PROJECT_ROOT).free
        free_gb = free / (1024**3)
        ok = free_gb > 5
        detail = f"{free_gb:.1f} GB free"
        if not ok:
            detail += " (REQUIRED: 5+ GB for Nuitka builds)"
        return check("Disk space (>5 GB)", ok, detail, "warning")
    except Exception:
        return check("Disk space (>5 GB)", False, "Cannot determine", "warning")


# ─── Main ───
def main():
    json_output = "--json" in sys.argv
    quick_mode = "--quick" in sys.argv

    if not json_output:
        print("=" * 60)
        print(f"  Azazael Pre-Build Environment Check")
        print(f"  Project: {_PROJECT_ROOT}")
        print("=" * 60)
        print()

    checks = [
        check_python_version,
        check_free_threading,
        lambda: check_critical_imports(quick=quick_mode),
        check_ffmpeg,
        check_project_integrity,
        check_nuitka,
        check_registry,
        check_disk_space,
    ]

    for fn in checks:
        fn()
        if not json_output:
            r = results[-1]
            icon = "OK" if r["passed"] else "!!"
            print(f"  [{icon}] {r['name']}: {r['detail']}")

    if not json_output:
        print()
        print("=" * 60)
        total = len(results)
        passed = sum(1 for r in results if r["passed"])
        failed = total - passed
        e_count = len(errors)
        w_count = len(warnings)

        if e_count == 0 and w_count == 0:
            print(f"  RESULT: ALL {total} CHECKS PASSED — Build environment ready!")
            exit_code = 0
        elif e_count == 0:
            print(f"  RESULT: {passed}/{total} passed, {w_count} warning(s) — May build with issues")
            exit_code = 1
        else:
            print(f"  RESULT: {e_count} ERROR(S), {w_count} warning(s) — Cannot build")
            exit_code = 2
        print("=" * 60)
    else:
        report = {
            "project": str(_PROJECT_ROOT),
            "total": len(results),
            "passed": sum(1 for r in results if r["passed"]),
            "errors": len(errors),
            "warnings": len(warnings),
            "checks": results,
        }
        print(json.dumps(report, indent=2))
        exit_code = 0 if not errors else (1 if not [e for e in errors if e["severity"] == "error"] else 2)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
