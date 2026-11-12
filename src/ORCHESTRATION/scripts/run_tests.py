import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

# Add src/ to path for CORE imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from CORE.paths import get_project_root, get_desktop_dir

_PROJECT_ROOT = get_project_root()
_SHORTY_DIR = get_desktop_dir() / "SHORTY"

# coding: utf-8
"""
Comprehensive Test Suite for All Modules
"""

from pathlib import Path
# from typing import Dict  # removed — all names have native equivalents
import subprocess
import sys
import json
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def test_module_syntax(path: str) -> dict:
    """Test if module has valid Python syntax."""
    result = {"path": path, "passed": False, "error": None}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            compile(f.read(), path, "exec")
        result["passed"] = True
    except SyntaxError as e:
        note_failure("run_tests:L32", e)
        result["error"] = f"Syntax: {e.msg}"
    except Exception as e:
        note_failure("run_tests:L34", e)
        result["error"] = str(e)[:800]
    return result


def test_module_import(path: str) -> dict:
    """Test if module can be imported."""
    result = {"path": path, "passed": False, "error": None}
    try:
        cmd = [
            sys.executable,
            "-c",
            f'import sys; sys.path.insert(0, "{Path(path).parent}"); exec(open("{path}", encoding="utf-8").read())',
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace")
        result["passed"] = proc.returncode == 0
        if proc.returncode != 0:
            result["error"] = proc.stderr[:200]
    except subprocess.TimeoutExpired as exc:
        note_failure("run_tests:L52", exc)
        result["error"] = "Timeout"
    except Exception as e:
        note_failure("run_tests:L54", e)
        result["error"] = str(e)[:800]
    return result


def test_documentation(path: str) -> dict:
    """Test if module has documentation."""
    result = {
        "path": path,
        "has_docstring": False,
        "has_functions": False,
        "has_classes": False,
    }
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Check for docstring
        result["has_docstring"] = '"""' in content or "'''" in content

        # Check for functions (simple pattern)
        result["has_functions"] = "def " in content

        # Check for classes
        result["has_classes"] = "class " in content

    except Exception as e:
        note_failure("run_tests:L80", e)
        result["error"] = str(e)

    return result


def test_bat_file(path: str) -> dict:
    """Test if BAT file is valid."""
    result = {"path": path, "passed": False, "checks": {}}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        result["checks"]["has_echo_off"] = "@echo off" in content
        result["checks"]["has_rem_header"] = "REM" in content[:200]
        result["checks"]["has_error_handling"] = (
            "ERROR" in content or "errorlevel" in content.lower()
        )
        result["checks"]["has_logging"] = "echo" in content
        result["checks"]["has_path_validation"] = "if not exist" in content

        result["passed"] = all(
            [
                result["checks"]["has_echo_off"],
                result["checks"]["has_rem_header"],
                result["checks"]["has_error_handling"],
            ]
        )

    except Exception as e:
        note_failure("run_tests:L109", e)
        result["error"] = str(e)

    return result


def run_all_tests() -> dict:
    """Run all tests."""
    print("=" * 70)
    print("COMPREHENSIVE TEST SUITE")
    print("=" * 70)

    results = {
        "timestamp": str(__import__("datetime").datetime.now()),
        "python_modules": {"total": 0, "passed": 0, "failed": 0, "details": []},
        "bat_files": {"total": 0, "passed": 0, "failed": 0, "details": []},
        "documentation": {"total": 0, "has_docstring": 0, "missing_docstring": 0},
    }

    # Test Python modules
    print("\n[1] Testing Python Modules...")
    for py_file in _PROJECT_ROOT.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue

        results["python_modules"]["total"] += 1

        syntax_test = test_module_syntax(str(py_file))

        if syntax_test["passed"]:
            results["python_modules"]["passed"] += 1
        else:
            results["python_modules"]["failed"] += 1
            results["python_modules"]["details"].append(
                {"path": str(py_file), "error": syntax_test["error"]}
            )

    print(
        f"   Modules: {results['python_modules']['passed']}/{results['python_modules']['total']} passed"
    )

    # Test BAT files
    print("\n[2] Testing BAT Files...")
    for bat_file in _SHORTY_DIR.rglob("*.bat"):
        results["bat_files"]["total"] += 1

        bat_test = test_bat_file(str(bat_file))

        if bat_test["passed"]:
            results["bat_files"]["passed"] += 1
        else:
            results["bat_files"]["failed"] += 1
            results["bat_files"]["details"].append(
                {"path": str(bat_file), "checks": bat_test["checks"]}
            )

    print(
        f"   BAT Files: {results['bat_files']['passed']}/{results['bat_files']['total']} passed"
    )

    # Test documentation
    print("\n[3] Testing Documentation...")
    for py_file in _PROJECT_ROOT.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue

        results["documentation"]["total"] += 1

        doc_test = test_documentation(str(py_file))

        if doc_test["has_docstring"]:
            results["documentation"]["has_docstring"] += 1
        else:
            results["documentation"]["missing_docstring"] += 1

    print(
        f"   With Docstrings: {results['documentation']['has_docstring']}/{results['documentation']['total']}"
    )

    # Save results
    print("\n[4] Saving Results...")

    # JSON
    with open("TEST_RESULTS.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    # Text report
    report = f"""# Test Results Report

Generated: {results["timestamp"]}

## Summary

| Category | Total | Passed | Failed |
|----------|-------|--------|--------|
| Python Modules | {results["python_modules"]["total"]} | {results["python_modules"]["passed"]} | {results["python_modules"]["failed"]} |
| BAT Files | {results["bat_files"]["total"]} | {results["bat_files"]["passed"]} | {results["bat_files"]["failed"]} |

## Documentation

| Metric | Value |
|--------|-------|
| With Docstrings | {results["documentation"]["has_docstring"]} |
| Missing Docstrings | {results["documentation"]["missing_docstring"]} |
| Coverage | {results["documentation"]["has_docstring"] / max(1, results["documentation"]["total"]) * 100:.1f}% |

## Failed Modules

"""

    for detail in results["python_modules"]["details"][:20]:
        report += f"- {detail['path']}\n"
        report += f"  Error: {detail['error']}\n\n"

    with open("TEST_RESULTS.md", "w", encoding="utf-8") as f:
        f.write(report)

    print("   JSON: TEST_RESULTS.json")
    print("   MD: TEST_RESULTS.md")

    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    module_pass_rate = (
        results["python_modules"]["passed"]
        / max(1, results["python_modules"]["total"])
        * 100
    )
    bat_pass_rate = (
        results["bat_files"]["passed"] / max(1, results["bat_files"]["total"]) * 100
    )
    doc_rate = (
        results["documentation"]["has_docstring"]
        / max(1, results["documentation"]["total"])
        * 100
    )

    print(f"\nPython Modules: {module_pass_rate:.1f}% pass rate")
    print(f"BAT Files: {bat_pass_rate:.1f}% pass rate")
    print(f"Documentation: {doc_rate:.1f}% coverage")

    overall_score = (module_pass_rate + bat_pass_rate + doc_rate) / 3
    print(f"\nOVERALL SCORE: {overall_score:.1f}%")

    if overall_score >= 90:
        print("Status: EXCELLENT")
    elif overall_score >= 70:
        print("Status: GOOD")
    else:
        print("Status: NEEDS IMPROVEMENT")

    return results


if __name__ == "__main__":
    results = run_all_tests()
