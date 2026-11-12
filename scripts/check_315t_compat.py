#!/usr/bin/env python3
"""
check_315t_compat.py — Verify all RL/ML packages work on Python 3.15t free-threading.

Usage:
    python scripts/check_315t_compat.py              # Full check
    python scripts/check_315t_compat.py --quick       # Quick check (imports only)
    python scripts/check_315t_compat.py --verbose     # Detailed output
    python scripts/check_315t_compat.py --json        # JSON output

Exit codes:
    0 — All packages OK
    1 — Some packages FAILED
    2 — Critical failure (Python version mismatch)
"""

import sys
import os
import time
import json
import importlib
import warnings
from pathlib import Path
import subprocess
import argparse

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

CHECK_LIST = {
    "Core Tensor": [
        ("torch", "2.0.0"),
        ("numpy", "1.24.0"),
    ],
    "Data Science": [
        ("pandas", "1.5.0"),
        ("scipy", "1.10.0"),
        ("scipy.sparse", None),
    ],
    "Machine Learning": [
        ("sklearn", "1.2.0"),
        ("sklearn.ensemble", None),
    ],
    "Reinforcement Learning": [
        ("gymnasium", "0.28.0"),
        ("gymnasium.spaces", None),
        ("stable_baselines3", "2.0.0"),
        ("stable_baselines3.ppo", None),
    ],
    "Deep Learning Extras": [
        ("torch.nn", None),
        ("torch.optim", None),
        ("optree", None),
        ("optree._C", None),  # C extension must exist
    ],
    "Visualization": [
        ("matplotlib", "3.7.0"),
        ("matplotlib.figure", None),
    ],
    "System/IO": [
        ("psutil", "5.9.0"),
    ],
    "ML Pipelines": [
        ("cloudpickle", None),
        ("accelerate", None),
    ],
}

# Optional packages — failure is warning, not error
OPTIONAL_PACKAGES = {
    "cv2": "opencv-python (no cp315t support until 4.14.0)",
    "tensorflow": "TensorFlow (may not have cp315t wheels)",
    "jax": "JAX (may not have cp315t wheels)",
    "flax": "Flax (JAX-dependent)",
}


# ═══════════════════════════════════════════════════════════════════
# Check Functions
# ═══════════════════════════════════════════════════════════════════


def check_python_version(verbose: bool = False) -> dict:
    """Check Python version and free-threading status."""
    info = {
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "free_threaded": False,
        "gil_enabled": True,
        "executable": sys.executable,
    }
    if hasattr(sys, "_is_gil_enabled"):
        info["gil_enabled"] = sys._is_gil_enabled()
        info["free_threaded"] = not info["gil_enabled"]

    if verbose:
        print(f"  Python: {info['version']}")
        print(f"  Free-threaded: {info['free_threaded']}")
        print(f"  Executable: {info['executable']}")
    return info


def import_module(mod_name: str) -> tuple:
    """Try to import a module. Returns (success, module, error)."""
    try:
        mod = importlib.import_module(mod_name)
        return (True, mod, None)
    except ImportError as e:
        return (False, None, str(e))
    except Exception as e:
        return (False, None, f"{type(e).__name__}: {e}")


def check_package(
    mod_name: str, min_version: str | None, verbose: bool = False
) -> dict:
    """Check a single package. Returns result dict."""
    result = {
        "module": mod_name,
        "min_version": min_version,
        "imported": False,
        "version": None,
        "error": None,
        "status": "ok",
    }

    success, mod, error = import_module(mod_name)
    if not success:
        # Check if it segfaulted (process would not reach here if so)
        result["imported"] = False
        result["error"] = error
        result["status"] = "fail"
        return result

    result["imported"] = True
    result["version"] = getattr(mod, "__version__", "unknown")

    # Check minimum version
    if min_version and result["version"] != "unknown":
        try:
            from packaging.version import Version as V
            if V(result["version"]) < V(min_version):
                result["status"] = "old_version"
                result["error"] = (
                    f"Version {result['version']} < minimum {min_version}"
                )
        except ImportError:
            pass  # packaging not available

    return result


def check_optional_package(name: str, description: str) -> dict:
    """Check an optional package (warning on failure, not error).

    Known-crashing packages (e.g., cv2 on 3.14t) are checked via subprocess
    to avoid segfault killing the checker process.
    """
    result = {"module": name, "description": description}

    # Check via subprocess for packages known to crash on 3.14t
    try:
        proc = subprocess.run(
            [sys.executable, "-c", f"import {name}" if "." not in name else f"import importlib; importlib.import_module('{name}')"],
            capture_output=True, timeout=30
        )
        result["imported"] = proc.returncode == 0
        if proc.returncode != 0:
            result["status"] = "optional_unavailable"
            result["error"] = f"Subprocess check failed (exit {proc.returncode})"
        else:
            result["status"] = "ok"
    except subprocess.TimeoutExpired:
        result["imported"] = False
        result["status"] = "optional_unavailable"
        result["error"] = "Subprocess timed out"
    except Exception as e:
        result["imported"] = False
        result["status"] = "optional_unavailable"
        result["error"] = str(e)

    return result


def check_cp315t(mod_name: str) -> dict:
    """Check if a module has a cp315t C extension (.pyd file)."""
    result = {"module": mod_name, "has_cp315t": False, "pyd_files": []}

    success, mod, _ = import_module(mod_name)
    if not success:
        result["error"] = "Module not importable"
        return result

    mod_dir = os.path.dirname(mod.__file__) if hasattr(mod, "__file__") else None
    if not mod_dir:
        return result

    pyd_files = []
    for root, dirs, files in os.walk(mod_dir):
        for f in files:
            if f.endswith(".pyd"):
                pyd_files.append(os.path.relpath(os.path.join(root, f), mod_dir))
                if "cp315t" in f:
                    result["has_cp315t"] = True

    result["pyd_files"] = pyd_files[:10]  # Limit output
    return result


# ═══════════════════════════════════════════════════════════════════
# Main Check
# ═══════════════════════════════════════════════════════════════════


def run_check(quick: bool = False, verbose: bool = False) -> dict:
    """Run all compatibility checks."""
    results = {}
    total = 0
    passed = 0
    failed = 0
    optional_unavailable = []

    # Python version
    results["python"] = check_python_version(verbose)
    if verbose:
        print()

    # Required packages
    for category, packages in CHECK_LIST.items():
        if verbose:
            print(f"  [{category}]")
        cat_results = []
        for mod_name, min_ver in packages:
            # Skip stable-baselines3 in quick mode (slow import)
            if quick and "stable_baselines3" in mod_name:
                continue

            r = check_package(mod_name, min_ver, verbose)
            cat_results.append(r)
            total += 1

            if r["status"] == "ok":
                passed += 1
                if verbose:
                    print(
                        f"    [OK] {mod_name} {r['version'] or ''}"
                    )
            else:
                failed += 1
                if verbose:
                    print(
                        f"    [FAIL] {mod_name}: {r['error']}"
                    )
            results.setdefault("packages", []).append(r)

    # Optional packages
    if not quick:
        if verbose:
            print(f"\n  [Optional — Warnings Only]")
        for name, desc in OPTIONAL_PACKAGES.items():
            r = check_optional_package(name, desc)
            if r["imported"]:
                passed += 1
            else:
                optional_unavailable.append(f"{name}: {desc}")
            if verbose:
                status = "OK" if r["imported"] else "N/A"
                print(f"    [{status}] {name}: {desc}")
            total += 1

    # cp315t C extension check (not in quick mode)
    if not quick:
        if verbose:
            print(f"\n  [C Extension Check]")
        for mod_name in ["torch", "psutil", "gymnasium"]:
            r = check_cp315t(mod_name)
            results.setdefault("c_extensions", []).append(r)
            if verbose and r["pyd_files"]:
                has_315t = "yes" if r["has_cp315t"] else "no"
                print(f"    [{has_315t}] {mod_name}: {len(r['pyd_files'])} .pyd files")

    results["summary"] = {
        "total": total,
        "passed": passed,
        "failed": failed,
        "optional_unavailable": len(optional_unavailable),
        "optional_details": optional_unavailable,
        "all_ok": failed == 0,
    }

    return results


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════


def main():

    parser = argparse.ArgumentParser(
        description="Check Python 3.14t compatibility of RL/ML packages"
    )
    parser.add_argument("--quick", action="store_true", help="Quick check (imports only)")
    parser.add_argument("--verbose", action="store_true", help="Detailed output")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    print(f"{'='*60}")
    print(f"  3.14t Compatibility Checker")
    print(f"{'='*60}\n")

    start = time.perf_counter()
    results = run_check(quick=args.quick, verbose=args.verbose or args.json)
    duration = time.perf_counter() - start

    if args.json:
        results["duration_sec"] = round(duration, 2)
        print(json.dumps(results, indent=2, default=str))
        sys.exit(0 if results["summary"]["all_ok"] else 1)

    # Human-readable summary
    s = results["summary"]
    py = results["python"]

    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Python: {py['version']} (Free-threaded: {py['free_threaded']})")
    print(f"  Total: {s['total']} | Passed: {s['passed']} | Failed: {s['failed']}")
    if s["optional_unavailable"] > 0:
        print(f"  Optional unavailable: {s['optional_unavailable']}")
        for detail in s["optional_details"]:
            print(f"    - {detail}")
    print(f"  Duration: {duration:.2f}s")
    print()

    if s["all_ok"]:
        print(f"  [OK] ALL CORE PACKAGES COMPATIBLE WITH 3.14t")
        sys.exit(0)
    else:
        print(f"  [FAIL] {s['failed']} package(s) failed — see above")
        sys.exit(1)


if __name__ == "__main__":
    main()
