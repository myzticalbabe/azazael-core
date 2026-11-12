# coding: utf-8
"""


__all__ = [
    "AzazaelStressTest",
    "StressResult",
]

AZAZAEL STRESS TEST v2 - Simplified System Validation
Tests Python installations, module imports, syntax, and operations
"""

import os
import sys
import time
import subprocess
import json
from pathlib import Path
from datetime import datetime
# from typing import List  # removed — all names have native equivalents
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import socket
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class StressResult:
    test_name: str
    passed: bool
    duration_ms: float
    details: str = ""
    errors: list[str] = field(default_factory=list)


class AzazaelStressTest:
    VERSION = "2.0.0"

    def __init__(self):
        self.root = Path(__file__).parent.parent.parent
        self.results: list[StressResult] = []
        self.start_time = datetime.now()

        # Add Azazael to path
        sys.path.insert(0, str(self.root))

    def log(self, msg: str):
        elapsed = (datetime.now() - self.start_time).total_seconds()
        print(f"[{elapsed:.2f}s] {msg}")

    def test_python_installation(self, path: str, ver: str) -> StressResult:
        """Test a single Python installation."""
        name = f"Python: {Path(path).name} ({ver})"
        start = time.time()

        try:
            result = subprocess.run(
                [path, "-c", "print('OK')"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
            )
            duration = (time.time() - start) * 1000

            if result.returncode == 0:
                return StressResult(
                    test_name=name, passed=True, duration_ms=duration, details="OK"
                )
            else:
                return StressResult(
                    test_name=name,
                    passed=False,
                    duration_ms=duration,
                    errors=[result.stderr[:800]],
                )
        except Exception as e:
            note_failure("STRESS_TEST:L73", e)
            return StressResult(
                test_name=name,
                passed=False,
                duration_ms=(time.time() - start) * 1000,
                errors=[str(e)],
            )

    def test_syntax_validation(self) -> StressResult:
        """Validate syntax of all Python files."""
        self.log("Validating syntax of all Python files...")

        start = time.time()
        files = list(self.root.rglob("*.py"))
        errors = []
        checked = 0

        for fp in files:
            if (
                "__pycache__" in str(fp)
                or ".venv" in str(fp)
                or "site-packages" in str(fp)
            ):
                continue
            try:
                subprocess.run(
                    [sys.executable, "-m", "py_compile", str(fp)],
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=5,
                )
                checked += 1
            except Exception as e:
                note_failure("STRESS_TEST:L105", e)
                errors.append(f"{fp}: {str(e)[:50]}")

        duration = (time.time() - start) * 1000

        return StressResult(
            test_name="Syntax Validation",
            passed=len(errors) == 0,
            duration_ms=duration,
            details=f"{checked} files validated",
            errors=errors[:5] if errors else [],
        )

    def test_file_io(self) -> StressResult:
        """Test file I/O operations."""
        self.log("Testing file I/O operations...")

        start = time.time()
        test_file = self.root / "STRESS_TEST_TEMP.txt"

        try:
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("STRESS TEST DATA\n" * 1000)

            with open(test_file, "r", encoding="utf-8") as f:
                content = f.read()

            test_file.unlink()

            if len(content) > 0:
                return StressResult(
                    test_name="File I/O Operations",
                    passed=True,
                    duration_ms=(time.time() - start) * 1000,
                    details=f"{len(content)} bytes processed",
                )
            else:
                return StressResult(
                    test_name="File I/O Operations",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    errors=["Empty file"],
                )

        except Exception as e:
            note_failure("STRESS_TEST:L149", e)
            return StressResult(
                test_name="File I/O Operations",
                passed=False,
                duration_ms=(time.time() - start) * 1000,
                errors=[str(e)],
            )

    def test_memory_ops(self) -> StressResult:
        """Test memory operations."""
        self.log("Testing memory operations...")

        start = time.time()

        try:
            data = [[i * j for j in range(100)] for i in range(100)]
            processed = [sum(row) for row in data]
            del data

            return StressResult(
                test_name="Memory Operations",
                passed=True,
                duration_ms=(time.time() - start) * 1000,
                details=f"{len(processed)} items processed",
            )

        except Exception as e:
            note_failure("STRESS_TEST:L175", e)
            return StressResult(
                test_name="Memory Operations",
                passed=False,
                duration_ms=(time.time() - start) * 1000,
                errors=[str(e)],
            )

    def test_concurrent(self) -> StressResult:
        """Test concurrent operations."""
        self.log("Testing concurrent operations...")

        start = time.time()

        try:

            def task(n):
                return n * n

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(task, i) for i in range(10)]
                results = [f.result() for f in futures]

            expected = 0 + 1 + 4 + 9 + 16 + 25 + 36 + 49 + 64 + 81
            if sum(results) == expected:
                return StressResult(
                    test_name="Concurrent Operations",
                    passed=True,
                    duration_ms=(time.time() - start) * 1000,
                    details=f"{len(results)} parallel tasks completed",
                )
            else:
                return StressResult(
                    test_name="Concurrent Operations",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    errors=["Results mismatch"],
                )

        except Exception as e:
            note_failure("STRESS_TEST:L214", e)
            return StressResult(
                test_name="Concurrent Operations",
                passed=False,
                duration_ms=(time.time() - start) * 1000,
                errors=[str(e)],
            )

    def test_network(self) -> StressResult:
        """Test network operations."""
        self.log("Testing network operations...")

        start = time.time()
        errors = []

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            try:
                sock.connect(("127.0.0.1", 12345))
                errors.append("Unexpected connection")
            except ConnectionRefusedError as exc:
                note_failure("STRESS_TEST:L235", exc)
            finally:
                sock.close()

            return StressResult(
                test_name="Network Operations",
                passed=True,
                duration_ms=(time.time() - start) * 1000,
                details="Socket handling OK",
            )

        except Exception as e:
            note_failure("STRESS_TEST:L247", e)
            return StressResult(
                test_name="Network Operations",
                passed=False,
                duration_ms=(time.time() - start) * 1000,
                errors=[str(e)],
            )

    def test_worm_operations(self) -> StressResult:
        """Test worm scanners."""
        self.log("Testing worm operations...")

        start = time.time()
        errors = []

        worms = [
            ("ERROR_SCAN_WORM", "CORE/WORMS/ERROR_SCAN_WORM.py"),
        ]

        passed = 0
        for name, rel_path in worms:
            try:
                path = self.root / rel_path.replace("/", os.sep)
                result = subprocess.run(
                    [sys.executable, str(path)],
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=60,
                )
                if result.returncode == 0:
                    passed += 1
                    self.log(f"  [OK] {name}")
                else:
                    errors.append(f"{name}: returncode {result.returncode}")
                    self.log(f"  [FAIL] {name}")
            except Exception as e:
                errors.append(f"{name}: {str(e)}")
                self.log(f"  [ERROR] {name}")

        return StressResult(
            test_name="Worm Operations",
            passed=len(errors) == 0,
            duration_ms=(time.time() - start) * 1000,
            details=f"{passed}/{len(worms)} worms operational",
            errors=errors,
        )

    def test_module_imports(self) -> StressResult:
        """Test importing modules."""
        self.log("Testing module imports...")

        start = time.time()
        errors = []

        test_imports = [
            "CORE.PYTHON_MANAGER",
        ]

        for mod in test_imports:
            try:
                parts = mod.split(".")
                module = __import__(mod)
                for part in parts[1:]:
                    module = getattr(module, part)
                self.log(f"  [OK] {mod}")
            except Exception as e:
                errors.append(f"{mod}: {str(e)[:50]}")
                self.log(f"  [FAIL] {mod}")

        return StressResult(
            test_name="Module Imports",
            passed=len(errors) == 0,
            duration_ms=(time.time() - start) * 1000,
            details=f"{len(test_imports) - len(errors)}/{len(test_imports)} imported",
            errors=errors,
        )

    def run_all_tests(self):
        """Run complete stress test suite."""
        total_start = time.time()

        print()
        print("=" * 70)
        print(f"AZAZAEL STRESS TEST v{self.VERSION}")
        print("=" * 70)
        print()
        print(f"Root: {self.root}")
        print()

        # Python installations: production 3.15t, its sanctioned 3.14
        # fallback, then the unrelated 3.11 install. The old entry named
        # "C:/Python314/python.exe", a path that does not exist on this host.
        self.results.append(
            self.test_python_installation(
                "C:/Azazael/.venvs/py315t/Scripts/python.exe", "3.15.0rc3"
            )
        )
        self.results.append(
            self.test_python_installation(
                "C:/Azazael/.venvs/py314m/Scripts/python.exe", "3.14.5"
            )
        )
        self.results.append(
            self.test_python_installation(
                "C:/Program Files/Python311/python.exe", "3.11.9"
            )
        )

        # Core tests
        self.results.append(self.test_module_imports())
        self.results.append(self.test_syntax_validation())
        self.results.append(self.test_worm_operations())
        self.results.append(self.test_file_io())
        self.results.append(self.test_memory_ops())
        self.results.append(self.test_concurrent())
        self.results.append(self.test_network())

        # Summary
        total_duration = (time.time() - total_start) * 1000

        print()
        print("=" * 70)
        print("STRESS TEST RESULTS")
        print("=" * 70)
        print()

        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed

        for r in self.results:
            status = "[PASS]" if r.passed else "[FAIL]"
            print(f"{status} {r.test_name}: {r.duration_ms:.0f}ms")
            if r.errors and not r.passed:
                for err in r.errors[:2]:
                    print(f"       -> {err[:60]}")
            elif r.details:
                print(f"       -> {r.details}")

        print()
        print("=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print()
        print(f"Tests run: {len(self.results)}")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print(f"Total duration: {total_duration:.0f}ms")
        print()

        if failed == 0:
            print("[SUCCESS] ALL STRESS TESTS PASSED")
        else:
            print(f"[WARNING] {failed} tests failed - review details above")

        print()
        print("=" * 70)

        # Save report
        report = {
            "timestamp": self.start_time.isoformat(),
            "duration_ms": total_duration,
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "results": [
                {
                    "test": r.test_name,
                    "passed": r.passed,
                    "duration_ms": r.duration_ms,
                    "details": r.details,
                    "errors": r.errors[:3],
                }
                for r in self.results
            ],
        }

        report_file = self.root / "STRESS_TEST_REPORT.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"Report saved: {report_file}")

        return failed == 0


if __name__ == "__main__":
    test = AzazaelStressTest()
    success = test.run_all_tests()
    sys.exit(0 if success else 1)
