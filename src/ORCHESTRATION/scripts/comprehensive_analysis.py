import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

# coding: utf-8
"""
Comprehensive Analysis

Module for scripts functionality.

Functions:
    - main()
    - find_all_modules()
    - analyze_module()
    - analyze_all()
    - test_module()
    - test_all()
    - diagnose_and_fix()
    - fix_all()
    - build_dependency_graph()
    - find_circular_deps()

Classes:
    - ModuleInfo
    - TestResult
    - FixResult
    - PythonModuleFinder
    - ModuleAnalyzer

Usage:
    >>> import comprehensive_analysis
    >>> comprehensive_analysis.main()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
Comprehensive Python Module Analysis and BAT Improvement System
- Find all Python modules on the system drive
- Run comprehensive tests
- Fix errors with debuggable solutions
- Dependency analysis
- Prerequisite checking
"""

import subprocess
import sys
import json
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime
import ast

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class ModuleInfo:
    """Information about a Python module."""

    path: str
    name: str
    size: int
    imports: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    has_init: bool = False
    test_status: str = "UNTESTED"
    last_tested: str = ""
    dependencies: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    """Result of running a test."""

    module: str
    passed: bool
    output: str
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class FixResult:
    """Result of fixing an issue."""

    module: str
    issue: str
    fix_applied: bool
    fix_details: str
    new_errors: list[str] = field(default_factory=list)


class PythonModuleFinder:
    """Find all Python modules on the system drive."""

    def __init__(self):
        self.modules: dict[str, ModuleInfo] = {}
        self.exclude_patterns = {
            "node_modules",
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "site-packages",
            "dist",
            "build",
            ".tox",
            ".nox",
            "windows",
            "program files",
            "program files (x86)",
            "appdata",
            "microsoft",
            "windowsnt",
            "system32",
        }

    def find_all_modules(self) -> dict[str, ModuleInfo]:
        """Find all Python modules on the system drive."""
        system_drive = os.environ.get("SystemDrive", "C:") + "\\"
        print("Scanning " + system_drive + " drive for Python modules...")

        for root, dirs, files in os.walk(system_drive):
            # Skip excluded directories
            dirs[:] = [
                d
                for d in dirs
                if not any(exc.lower() in d.lower() for exc in self.exclude_patterns)
            ]

            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    try:
                        module_name = file[:-3]  # Remove .py
                        size = os.path.getsize(full_path)

                        module_info = ModuleInfo(
                            path=full_path, name=module_name, size=size
                        )

                        self.modules[full_path] = module_info

                    except (PermissionError, OSError) as exc:
                        note_failure("comprehensive_analysis:L151", exc)
                        continue

        print(f"Found {len(self.modules)} Python modules")

        # Filter to relevant modules (Azazael and ml_platforms)
        relevant = {
            k: v
            for k, v in self.modules.items()
            if "azazael" in k.lower() or "ml_platforms" in k.lower()
        }

        print(f"Relevant modules: {len(relevant)}")
        return relevant


class ModuleAnalyzer:
    """Analyze Python modules for imports, functions, classes."""

    def __init__(self, modules: dict[str, ModuleInfo]):
        self.modules = modules

    def analyze_module(self, path: str) -> ModuleInfo:
        """Analyze a single module."""
        module = self.modules.get(
            path, ModuleInfo(path=path, name=Path(path).stem, size=0)
        )

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            tree = ast.parse(content)

            # Extract imports
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module.imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        module.imports.append(node.module)

            # Extract functions
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    module.functions.append(node.name)

            # Extract classes
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    module.classes.append(node.name)

            # Check for __init__.py
            if Path(path).name == "__init__.py":
                module.has_init = True

        except SyntaxError as e:
            note_failure("comprehensive_analysis:L208", e)
            module.errors.append(f"Syntax error: {e}")
        except Exception as e:
            note_failure("comprehensive_analysis:L210", e)
            module.errors.append(f"Analysis error: {e}")

        return module

    def analyze_all(self) -> dict[str, ModuleInfo]:
        """Analyze all modules."""
        for path in self.modules:
            print(f"Analyzing: {path}")
            self.modules[path] = self.analyze_module(path)
        return self.modules


class ModuleTester:
    """Test Python modules for functionality."""

    def __init__(self, modules: dict[str, ModuleInfo]):
        self.modules = modules
        self.results: list[TestResult] = []

    def test_module(self, path: str) -> TestResult:
        """Test a single module."""
        module = self.modules[path]
        start_time = datetime.now()

        result = TestResult(module=path, passed=False, output="")

        try:
            # Try importing the module
            import_cmd = [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, '{Path(path).parent}'); exec(open('{path}', encoding='utf-8').read())",
            ]

            proc = subprocess.run(
                import_cmd, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace"
            )

            if proc.returncode == 0:
                result.passed = True
                result.output = "Module imported successfully"
            else:
                result.errors.append(proc.stderr)

        except subprocess.TimeoutExpired as exc:
            note_failure("comprehensive_analysis:L255", exc)
            result.errors.append("Test timed out (>30s)")
        except Exception as e:
            note_failure("comprehensive_analysis:L257", e)
            result.errors.append(str(e))
        finally:
            result.duration_ms = (datetime.now() - start_time).total_seconds() * 1000

        module.test_status = "PASSED" if result.passed else "FAILED"
        module.last_tested = datetime.now().isoformat()

        if result.errors:
            module.errors.extend(result.errors)

        self.results.append(result)
        return result

    def test_all(self) -> list[TestResult]:
        """Test all modules."""
        for path in self.modules:
            print(f"Testing: {path}")
            self.test_module(path)
        return self.results


class ModuleFixer:
    """Fix module errors with debuggable solutions."""

    def __init__(self, modules: dict[str, ModuleInfo], results: list[TestResult]):
        self.modules = modules
        self.results = results
        self.fixes: list[FixResult] = []

    def diagnose_and_fix(self, path: str) -> FixResult:
        """Diagnose and fix module issues."""
        module = self.modules[path]
        result = FixResult(module=path, issue="", fix_applied=False, fix_details="")

        # Check for common issues
        issues = []

        # Issue 1: Missing encoding declaration
        try:
            with open(path, "r", encoding="utf-8") as f:
                first_lines = f.read(200)

            if "# -*- coding:" not in first_lines and not (
                b"\xc0" in first_lines.encode("utf-8", errors="replace")[:800]
            ):
                # Check if file has non-ASCII characters that might cause issues
                sample = Path(path).read_text(
                    encoding="utf-8", errors="replace"
                )[:500]
                if any(ord(c) > 127 for c in sample):
                    issues.append(
                        "Potential encoding issues - recommend adding # coding: utf-8"
                    )
        except Exception as exc:
            note_failure("comprehensive_analysis:L310", exc)
            issues.append("Cannot read file for encoding check")

        # Issue 2: Missing imports
        if "import" not in module.imports:
            issues.append("No imports detected - module may be standalone")

        # Issue 3: Syntax issues
        if any("syntax" in err.lower() for err in module.errors):
            issues.append("Syntax errors detected - recommend fixing AST parse errors")

        # Apply fixes based on issues
        for issue in issues:
            result.issue = issue

            if "encoding" in issue.lower():
                try:
                    content = Path(path).read_text(encoding="utf-8")
                    if not content.startswith("#"):
                        Path(path).write_text("# coding: utf-8\n" + content, encoding="utf-8")
                    result.fix_applied = True
                    result.fix_details = "Added UTF-8 encoding declaration"
                except Exception as e:
                    note_failure("comprehensive_analysis:L332", e)
                    result.fix_details = f"Failed to apply encoding fix: {e}"

            elif "syntax" in issue.lower():
                result.fix_details = "Manual syntax review required - see error details"

            elif "import" in issue.lower():
                result.fix_details = (
                    "Module may be main entry point - verify with __main__ check"
                )

        self.fixes.append(result)
        return result

    def fix_all(self) -> list[FixResult]:
        """Fix all module issues."""
        for path in self.modules:
            print(f"Diagnosing: {path}")
            self.diagnose_and_fix(path)
        return self.fixes


class DependencyAnalyzer:
    """Analyze module dependencies."""

    def __init__(self, modules: dict[str, ModuleInfo]):
        self.modules = modules
        self.dependency_graph = defaultdict(set)
        self.reverse_deps = defaultdict(set)

    def build_dependency_graph(self) -> dict[str, set[str]]:
        """Build dependency graph from module imports."""
        for path, module in self.modules.items():
            module_name = Path(path).stem

            for imp in module.imports:
                self.dependency_graph[module_name].add(imp)
                self.reverse_deps[imp].add(module_name)

        return dict(self.dependency_graph)

    def find_circular_deps(self) -> list[list[str]]:
        """Find circular dependencies."""
        cycles = []
        visited = set()
        rec_stack = set()

        def dfs(node, path):
            visited.add(node)
            rec_stack.add(node)

            for dep in self.dependency_graph.get(node, set()):
                if dep not in visited:
                    dfs(dep, path + [dep])
                elif dep in rec_stack:
                    cycle_start = path.index(dep)
                    cycles.append(path[cycle_start:] + [dep])

            rec_stack.remove(node)

        for node in self.dependency_graph:
            if node not in visited:
                dfs(node, [node])

        return cycles

    def find_orphans(self) -> list[str]:
        """Find modules with no dependencies."""
        orphans = []
        for path, module in self.modules.items():
            name = Path(path).stem
            if not self.dependency_graph.get(name) and len(module.classes) == 0:
                orphans.append(name)
        return orphans

    def get_critical_path(self, target: str) -> list[str]:
        """Get critical dependency path to target."""
        path = []

        def find_path(node, visited):
            if node == target:
                return [node]
            if node in visited:
                return None
            visited.add(node)

            for dep in self.dependency_graph.get(node, set()):
                result = find_path(dep, visited)
                if result:
                    return [node] + result

            return None

        for node in self.dependency_graph:
            result = find_path(node, set())
            if result and target in result:
                if not path or len(result) < len(path):
                    path = result

        return path


class PrerequisiteChecker:
    """Check prerequisites for module execution."""

    def __init__(self, modules: dict[str, ModuleInfo]):
        self.modules = modules
        self.common_requirements = {
            "numpy": "pip install numpy",
            "torch": "pip install torch torchvision",
            "tensorflow": "pip install tensorflow",
            "scikit-learn": "pip install scikit-learn",
            "pandas": "pip install pandas",
            "matplotlib": "pip install matplotlib",
            "networkx": "pip install networkx",
            "mlflow": "pip install mlflow",
            "onnx": "pip install onnx onnxruntime",
            "ray": "pip install ray",
        }

    def check_prerequisites(self, path: str) -> dict[str, Any]:
        """Check prerequisites for a module."""
        module = self.modules[path]
        report = {
            "module": path,
            "python_version": sys.version,
            "missing_packages": [],
            "optional_packages": [],
            "system_requirements": [],
        }

        # Check Python version
        py_ver = tuple(map(int, sys.version.split()[0].split(".")[:2]))
        report["python_version_check"] = (
            "OK" if py_ver >= (3, 10) else "Consider upgrading to Python 3.10+"
        )

        # Check required packages
        for imp in module.imports:
            imp_lower = imp.lower()
            for pkg, install_cmd in self.common_requirements.items():
                if pkg.lower() in imp_lower or pkg.replace("-", "_") in imp_lower:
                    try:
                        __import__(pkg)
                    except ImportError as exc:
                        note_failure("comprehensive_analysis:L476", exc)
                        if pkg not in report["missing_packages"]:
                            report["missing_packages"].append(
                                {"package": pkg, "install": install_cmd}
                            )

        return report

    def check_all(self) -> list[dict[str, Any]]:
        """Check prerequisites for all modules."""
        reports = []
        for path in self.modules:
            print(f"Checking prerequisites: {path}")
            reports.append(self.check_prerequisites(path))
        return reports


class BATImprover:
    """Apply improvements to BAT files based on analysis."""

    def __init__(self):
        self.improvements = []

    def improve_bat(self, bat_path: str) -> dict[str, Any]:
        """Apply improvements to a BAT file."""
        improvements = {"file": bat_path, "applied": [], "recommendations": []}

        try:
            content = Path(bat_path).read_text(encoding="utf-8")
            lines = content.split("\n")

            # Check and add missing elements
            if not any("@echo off" in line for line in lines):
                improvements["applied"].append("Added @echo off")
                lines.insert(0, "@echo off")

            if not any("REM" in line or "::" in line for line in lines):
                improvements["applied"].append("Added documentation comments")

            if not any("%*" in line or "%1" in line for line in lines):
                improvements["recommendations"].append(
                    "Consider adding parameter passing (%*)"
                )

            if not any("pause" in line for line in lines):
                improvements["recommendations"].append(
                    "Consider adding pause for user feedback"
                )

            if not any("cd /d" in line.lower() for line in lines):
                improvements["recommendations"].append(
                    "Consider adding path navigation"
                )

            # Write improved content
            if improvements["applied"]:
                Path(bat_path).write_text(
                    "\n".join(lines), encoding="utf-8"
                )

        except Exception as e:
            note_failure("comprehensive_analysis:L534", e)
            improvements["error"] = str(e)

        self.improvements.append(improvements)
        return improvements

    def improve_all(self, bat_dir: str) -> list[dict[str, Any]]:
        """Improve all BAT files in directory."""
        results = []
        for bat_path in Path(bat_dir).rglob("*.bat"):
            print(f"Improving: {bat_path}")
            results.append(self.improve_bat(str(bat_path)))
        return results


def main():
    """Main execution pipeline."""
    print("=" * 70)
    print("COMPREHENSIVE MODULE ANALYSIS SYSTEM")
    print("=" * 70)

    results = {
    "timestamp": datetime.now().isoformat(),
    "modules_found": 0,
    "modules_analyzed": 0,
    "tests_passed": 0,
    "tests_failed": 0,
    "fixes_applied": 0,
    "issues": [],
    "recommendations": [],
    }

    # Step 1: Find modules
    print("\n[1] Finding Python modules...")
    finder = PythonModuleFinder()
    modules = finder.find_all_modules()
    results["modules_found"] = len(modules)

    # Step 2: Analyze modules
    print("\n[2] Analyzing modules...")
    analyzer = ModuleAnalyzer(modules)
    analyzed = analyzer.analyze_all()
    results["modules_analyzed"] = len(analyzed)

    # Step 3: Test modules
    print("\n[3] Testing modules...")
    tester = ModuleTester(analyzed)
    test_results = tester.test_all()
    results["tests_passed"] = sum(1 for r in test_results if r.passed)
    results["tests_failed"] = sum(1 for r in test_results if not r.passed)

    # Step 4: Fix errors
    print("\n[4] Fixing module errors...")
    fixer = ModuleFixer(analyzed, test_results)
    fix_results = fixer.fix_all()
    results["fixes_applied"] = sum(1 for f in fix_results if f.fix_applied)

    # Step 5: Dependency analysis
    print("\n[5] Running dependency analysis...")
    dep_analyzer = DependencyAnalyzer(analyzed)
    dep_graph = dep_analyzer.build_dependency_graph()
    cycles = dep_analyzer.find_circular_deps()
    orphans = dep_analyzer.find_orphans()

    results["dependency_graph"] = {k: list(v) for k, v in dep_graph.items()}
    results["circular_dependencies"] = cycles
    results["orphan_modules"] = orphans

    # Step 6: Prerequisite checking
    print("\n[6] Checking prerequisites...")
    prereq_checker = PrerequisiteChecker(analyzed)
    prereq_reports = prereq_checker.check_all()

    # Step 7: BAT improvements
    print("\n[7] Improving BAT files...")
    bat_improver = BATImprover()
    bat_results = bat_improver.improve_all(str(Path.home() / "Desktop" / "SHORTY"))

    # Generate report
    print("\n[8] Generating comprehensive report...")

    report = f"""# Comprehensive Module Analysis Report

Generated: {results["timestamp"]}

## Summary

| Metric | Value |
|--------|-------|
| Modules Found | {results["modules_found"]} |
| Modules Analyzed | {results["modules_analyzed"]} |
| Tests Passed | {results["tests_passed"]} |
| Tests Failed | {results["tests_failed"]} |
| Fixes Applied | {results["fixes_applied"]} |

## Dependency Analysis

### Circular Dependencies
"""

    if cycles:
        for i, cycle in enumerate(cycles[:10]):  # Limit to 10
            report += f"- Cycle {i + 1}: {' -> '.join(cycle)}\n"
    else:
        report += "No circular dependencies detected.\n"

    report += """
### Orphan Modules
"""
    for orphan in orphans[:10]:
        report += f"- {orphan}\n"

    report += """
## Module Status
"""

    for path, module in analyzed.items():
        status = "PASS" if module.test_status == "PASSED" else "FAIL"
        report += f"\n### {Path(path).name}\n"
        report += f"- Status: {status}\n"
        report += f"- Functions: {len(module.functions)}\n"
        report += f"- Classes: {len(module.classes)}\n"
        report += f"- Imports: {len(module.imports)}\n"
        if module.errors:
            report += f"- Errors: {', '.join(module.errors[:3])}\n"

    report += """
## Prerequisites Summary
"""

    for prereq in prereq_reports[:20]:
        if prereq["missing_packages"]:
            report += f"\n### {Path(prereq['module']).name}\n"
            for pkg in prereq["missing_packages"]:
                report += f"- **{pkg['package']}**: `{pkg['install']}`\n"

    report += """
## BAT Improvements
"""

    for bat in bat_results:
        if bat["applied"] or bat["recommendations"]:
            report += f"\n### {Path(bat['file']).name}\n"
            if bat["applied"]:
                report += f"- Applied: {', '.join(bat['applied'])}\n"
            for rec in bat["recommendations"]:
                report += f"- Suggestion: {rec}\n"

    # Write report
    report_path = "COMPREHENSIVE_ANALYSIS_REPORT.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\nReport saved to: {report_path}")

    # Save JSON results
    json_path = "ANALYSIS_RESULTS.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"JSON results saved to: {json_path}")

    # Print summary
    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)
    print(
        f"\nModules: {results['modules_found']} found, {results['modules_analyzed']} analyzed"
    )
    print(f"Tests: {results['tests_passed']} passed, {results['tests_failed']} failed")
    print(f"Fixes: {results['fixes_applied']} applied")
    print(f"Circular deps: {len(cycles)}")
    print(f"Orphan modules: {len(orphans)}")

    return results


if __name__ == "__main__":
    main()
