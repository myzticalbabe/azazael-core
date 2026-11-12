# coding: utf-8
"""
Comprehensive Analysis of ALL Python Modules on the System Drive
- Complete module discovery and analysis
- Full dependency mapping
- Error detection and fixes
- Prerequisites checking
- Statistical analysis
"""

import json
import os
import re
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime
import ast
import time

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class FullModuleInfo:
    """Complete information about a Python module."""

    path: str
    name: str
    size: int
    directory: str
    imports: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    test_status: str = "UNTESTED"
    last_tested: str = ""
    line_count: int = 0
    has_docstring: bool = False
    complexity_score: float = 0.0


@dataclass
class DirectoryStats:
    """Statistics for a directory."""

    path: str
    module_count: int = 0
    total_size: int = 0
    total_lines: int = 0
    error_count: int = 0
    top_imports: list[str] = field(default_factory=list)


class CompleteModuleFinder:
    """Find ALL Python modules on C:\\ drive."""

    def __init__(self):
        self.modules: dict[str, FullModuleInfo] = {}
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
            "onedrive",
            "recovery",
            "$recycle.bin",
            "system volume information",
        }
        self.scan_stats = {
            "directories_scanned": 0,
            "files_found": 0,
            "errors": 0,
            "start_time": None,
            "end_time": None,
        }

    def find_all_modules(self, limit: int | None = None) -> dict[str, FullModuleInfo]:
        """Find all Python modules with progress tracking."""
        self.scan_stats["start_time"] = datetime.now()
        print("=" * 70)
        print("COMPLETE PYTHON MODULE DISCOVERY")
        print("=" * 70)
        print("\nScanning " + os.environ.get("SystemDrive", "C:") + "\\ drive for Python modules...")
        print("This may take several minutes for 5000+ modules...\n")

        count = 0
        for root, dirs, files in os.walk(os.environ.get("SystemDrive", "C:") + "\\"):
            # Update progress
            self.scan_stats["directories_scanned"] += 1
            if self.scan_stats["directories_scanned"] % 1000 == 0:
                print(
                    f"  Scanned {self.scan_stats['directories_scanned']} directories... "
                    f"{count} modules found"
                )

            # Skip excluded directories
            dirs[:] = [
                d
                for d in dirs
                if not any(
                    exc.lower() in os.path.join(root, d).lower()
                    for exc in self.exclude_patterns
                )
            ]

            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    try:
                        module_name = file[:-3]
                        size = os.path.getsize(full_path)

                        module_info = FullModuleInfo(
                            path=full_path, name=module_name, size=size, directory=root
                        )

                        self.modules[full_path] = module_info
                        count += 1
                        self.scan_stats["files_found"] += 1

                        if limit and count >= limit:
                            print(f"\nReached limit of {limit} modules")
                            break

                    except (PermissionError, OSError) as exc:
                        note_failure("complete_module_analysis:L138", exc)
                        self.scan_stats["errors"] += 1
                        continue

            if limit and count >= limit:
                break

        self.scan_stats["end_time"] = datetime.now()
        duration = (
            self.scan_stats["end_time"] - self.scan_stats["start_time"]
        ).total_seconds()

        print(f"\n{'=' * 70}")
        print("DISCOVERY COMPLETE")
        print(f"{'=' * 70}")
        print(f"Total Python modules found: {len(self.modules)}")
        print(f"Directories scanned: {self.scan_stats['directories_scanned']}")
        print(f"Errors: {self.scan_stats['errors']}")
        print(f"Scan duration: {duration:.1f} seconds")

        return self.modules


class CompleteModuleAnalyzer:
    """Analyze all Python modules in detail."""

    def __init__(self, modules: dict[str, FullModuleInfo]):
        self.modules = modules
        self.analyze_stats: dict[str, float] = {
            "modules_analyzed": 0,
            "syntax_errors": 0,
            "analysis_time": 0,
        }

    def analyze_module(self, path: str) -> FullModuleInfo:
        """Analyze a single module in detail."""
        module = self.modules[path]

        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")
            module.line_count = len(lines)

            # Check for docstring
            module.has_docstring = bool(
                re.search(r'"""[\s\S]*?"""', content)
                or re.search(r"'''[\s\S]*?'''", content)
            )

            # Parse AST
            tree = ast.parse(content)

            # Extract imports
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name not in module.imports:
                            module.imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module not in module.imports:
                        module.imports.append(node.module)

            # Extract functions
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    module.functions.append(node.name)

            # Extract classes
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    module.classes.append(node.name)

            # Calculate complexity (simplified)
            complexity = 0
            for node in ast.walk(tree):
                if isinstance(node, (ast.If, ast.For, ast.While, ast.Try)):
                    complexity += 1
            module.complexity_score = complexity / max(1, module.line_count / 50)

        except SyntaxError as e:
            note_failure("complete_module_analysis:L217", e)
            module.errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
            self.analyze_stats["syntax_errors"] += 1
        except Exception as e:
            note_failure("complete_module_analysis:L220", e)
            module.errors.append(f"Analysis error: {str(e)[:800]}")

        return module

    def analyze_all(self, batch_size: int = 100) -> dict[str, FullModuleInfo]:
        """Analyze all modules with progress."""
        print("\n" + "=" * 70)
        print("MODULE ANALYSIS")
        print("=" * 70)
        print("\nAnalyzing modules in batches...\n")

        start_time = datetime.now()
        keys = list(self.modules.keys())

        for i, path in enumerate(keys):
            if i % batch_size == 0:
                print(f"  Analyzed {i}/{len(keys)} modules...")

            self.analyze_module(path)
            self.analyze_stats["modules_analyzed"] += 1

        self.analyze_stats["analysis_time"] = (
            datetime.now() - start_time
        ).total_seconds()

        print(f"\nAnalysis complete in {self.analyze_stats['analysis_time']:.1f}s")
        print(f"Syntax errors found: {self.analyze_stats['syntax_errors']}")

        return self.modules


class ModuleTester:
    """Test Python module imports."""

    def __init__(self, modules: dict[str, FullModuleInfo]):
        self.modules = modules
        self.results: dict[str, dict] = {}

    def test_module(self, path: str) -> dict:
        """Test module import."""
        module = self.modules[path]
        result = {"path": path, "passed": False, "error": None, "duration_ms": 0}

        start = time.time()
        try:
            # Try basic syntax check first
            compile(Path(path).read_text(encoding="utf-8"), path, "exec")
            result["passed"] = True
        except SyntaxError as e:
            note_failure("complete_module_analysis:L269", e)
            result["error"] = f"Syntax: {e.msg}"
        except Exception as e:
            note_failure("complete_module_analysis:L271", e)
            result["error"] = str(e)[:200]
        finally:
            result["duration_ms"] = (time.time() - start) * 1000

        module.test_status = "PASS" if result["passed"] else "FAIL"
        self.results[path] = result
        return result

    def test_all(self, sample_size: int = 500) -> dict[str, dict]:
        """Test all modules or a sample."""
        print("\n" + "=" * 70)
        print("MODULE TESTING")
        print("=" * 70)

        if len(self.modules) > sample_size:
            print(
                f"\nTesting sample of {sample_size} modules from {len(self.modules)} total..."
            )
            paths = list(self.modules.keys())[:sample_size]
        else:
            paths = list(self.modules.keys())

        passed = 0
        failed = 0

        for i, path in enumerate(paths):
            if i % 100 == 0:
                print(f"  Testing {i}/{len(paths)}...")
            result = self.test_module(path)
            if result["passed"]:
                passed += 1
            else:
                failed += 1

        print(f"\nTest Results: {passed} passed, {failed} failed")

        return self.results


class DependencyAnalyzer:
    """Complete dependency analysis."""

    def __init__(self, modules: dict[str, FullModuleInfo]):
        self.modules = modules
        self.dependency_graph = defaultdict(set)
        self.import_counts = defaultdict(int)
        self.module_to_path = {}

    def build_complete_graph(self) -> dict[str, Any]:
        """Build complete dependency graph."""
        print("\n" + "=" * 70)
        print("DEPENDENCY ANALYSIS")
        print("=" * 70)

        # Build mapping
        for path, module in self.modules.items():
            self.module_to_path[module.name.lower()] = path

        # Count imports
        all_imports = []
        for module in self.modules.values():
            all_imports.extend(module.imports)

        for imp in all_imports:
            self.import_counts[imp] += 1

        print(f"\nUnique imports found: {len(self.import_counts)}")

        # Find most common imports
        print("\nTop 30 Most Common Imports:")
        for i, (imp, count) in enumerate(
            sorted(self.import_counts.items(), key=lambda x: -x[1])[:30]
        ):
            print(f"  {i + 1:2}. {imp}: {count}")

        return {
            "import_counts": dict(self.import_counts),
            "total_modules": len(self.modules),
            "unique_imports": len(self.import_counts),
        }


class StatisticalAnalyzer:
    """Comprehensive statistical analysis."""

    def __init__(self, modules: dict[str, FullModuleInfo]):
        self.modules = modules

    def get_directory_stats(self) -> list[DirectoryStats]:
        """Get statistics per directory."""
        dir_stats = defaultdict(lambda: DirectoryStats(path=""))

        for path, module in self.modules.items():
            dir_path = os.path.dirname(path)
            stats = dir_stats[dir_path]
            stats.path = dir_path
            stats.module_count += 1
            stats.total_size += module.size
            stats.total_lines += module.line_count
            stats.error_count += len(module.errors)

        # Sort by module count
        return sorted(dir_stats.values(), key=lambda x: -x.module_count)

    def generate_complete_report(self) -> dict[str, Any]:
        """Generate complete statistical report."""
        print("\n" + "=" * 70)
        print("STATISTICAL ANALYSIS")
        print("=" * 70)

        sizes = [m.size for m in self.modules.values()]
        lines = [m.line_count for m in self.modules.values()]
        complexities = [m.complexity_score for m in self.modules.values()]

        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_modules": len(self.modules),
                "total_size_bytes": sum(sizes),
                "total_lines": sum(lines),
                "avg_lines_per_module": sum(lines) / max(1, len(lines)),
                "avg_size_per_module": sum(sizes) / max(1, len(sizes)),
            },
            "size_distribution": {
                "min_bytes": min(sizes) if sizes else 0,
                "max_bytes": max(sizes) if sizes else 0,
                "median_bytes": sorted(sizes)[len(sizes) // 2] if sizes else 0,
            },
            "line_distribution": {
                "min_lines": min(lines) if lines else 0,
                "max_lines": max(lines) if lines else 0,
                "median_lines": sorted(lines)[len(lines) // 2] if lines else 0,
            },
            "complexity": {
                "avg_complexity": sum(complexities) / max(1, len(complexities)),
                "max_complexity": max(complexities) if complexities else 0,
            },
            "documentation": {
                "with_docstring": sum(
                    1 for m in self.modules.values() if m.has_docstring
                ),
                "without_docstring": sum(
                    1 for m in self.modules.values() if not m.has_docstring
                ),
            },
            "testing": {
                "passed": sum(
                    1 for m in self.modules.values() if m.test_status == "PASS"
                ),
                "failed": sum(
                    1 for m in self.modules.values() if m.test_status == "FAIL"
                ),
                "untested": sum(
                    1 for m in self.modules.values() if m.test_status == "UNTESTED"
                ),
            },
        }

        print(f"\nTotal Modules: {report['summary']['total_modules']:,}")
        print(
            f"Total Size: {report['summary']['total_size_bytes']:,} bytes "
            f"({report['summary']['total_size_bytes'] / 1024 / 1024:.1f} MB)"
        )
        print(f"Total Lines: {report['summary']['total_lines']:,}")
        print(f"Avg Lines/Module: {report['summary']['avg_lines_per_module']:.1f}")

        print(
            f"\nLines per module: {report['line_distribution']['min_lines']} "
            f"- {report['line_distribution']['max_lines']} "
            f"(median: {report['line_distribution']['median_lines']})"
        )

        print("\nDocumentation:")
        print(f"  With docstring: {report['documentation']['with_docstring']:,}")
        print(f"  Without docstring: {report['documentation']['without_docstring']:,}")

        print("\nTesting:")
        print(f"  Passed: {report['testing']['passed']:,}")
        print(f"  Failed: {report['testing']['failed']:,}")
        print(f"  Untested: {report['testing']['untested']:,}")

        return report


class PrerequisiteChecker:
    """Check prerequisites for all modules."""

    def __init__(self, modules: dict[str, FullModuleInfo]):
        self.modules = modules
        self.common_packages = {
            "numpy": "pip install numpy",
            "pandas": "pip install pandas",
            "torch": "pip install torch torchvision torchaudio",
            "tensorflow": "pip install tensorflow",
            "scikit-learn": "pip install scikit-learn",
            "matplotlib": "pip install matplotlib",
            "scipy": "pip install scipy",
            "requests": "pip install requests",
            "flask": "pip install flask",
            "django": "pip install django",
            "networkx": "pip install networkx",
            "pandas": "pip install pandas",
            "pytest": "pip install pytest",
            "mlflow": "pip install mlflow",
            "onnx": "pip install onnx onnxruntime",
            "ray": "pip install ray",
            "jax": "pip install jax jaxlib",
            "opencv": "pip install opencv-python",
            "pillow": "pip install pillow",
            "beautifulsoup4": "pip install beautifulsoup4",
            "sqlalchemy": "pip install sqlalchemy",
        }
        self.missing_by_package = defaultdict(list)

    def check_all(self) -> dict[str, Any]:
        """Check prerequisites for all modules."""
        print("\n" + "=" * 70)
        print("PREREQUISITE CHECK")
        print("=" * 70)

        checked = set()
        package_usage = defaultdict(int)

        for path, module in self.modules.items():
            for imp in module.imports:
                if imp in checked:
                    continue

                checked.add(imp)
                imp_lower = imp.lower()

                for pkg in self.common_packages:
                    if pkg.lower() in imp_lower or pkg.replace("-", "_") == imp_lower:
                        try:
                            __import__(pkg)
                        except ImportError as exc:
                            note_failure("complete_module_analysis:L507", exc)
                            self.missing_by_package[pkg].append(path)
                            package_usage[pkg] += 1

        print("\nPackage Requirements (Top 20):")
        for pkg, count in sorted(package_usage.items(), key=lambda x: -x[1])[:20]:
            status = "MISSING" if pkg in self.missing_by_package else "OK"
            print(f"  {pkg}: {count} modules, Status: {status}")

        return {
            "package_usage": dict(package_usage),
            "missing_packages": {k: len(v) for k, v in self.missing_by_package.items()},
            "modules_missing_pkgs": dict(self.missing_by_package),
        }


def main():
    """Main execution for complete analysis."""
    print("\n" + "#" * 70)
    print("#" + " " * 20 + "COMPLETE PYTHON MODULE ANALYSIS" + " " * 21 + "#")
    print("#" * 70 + "\n")

    # Step 1: Find all modules
    finder = CompleteModuleFinder()
    modules = finder.find_all_modules()

    # Step 2: Analyze all modules
    analyzer = CompleteModuleAnalyzer(modules)
    modules = analyzer.analyze_all()

    # Step 3: Test modules (sample)
    tester = ModuleTester(modules)
    test_results = tester.test_all(sample_size=1000)

    # Step 4: Dependency analysis
    dep_analyzer = DependencyAnalyzer(modules)
    dep_results = dep_analyzer.build_complete_graph()

    # Step 5: Statistical analysis
    stats = StatisticalAnalyzer(modules)
    stats_report = stats.generate_complete_report()

    # Step 6: Prerequisite check
    prereq = PrerequisiteChecker(modules)
    prereq_results = prereq.check_all()

    # Step 7: Generate comprehensive JSON
    print("\n" + "=" * 70)
    print("GENERATING OUTPUT FILES")
    print("=" * 70)

    # Create summary data
    summary = {
    "timestamp": datetime.now().isoformat(),
    "scan_stats": finder.scan_stats,
    "analysis_stats": analyzer.analyze_stats,
    "total_modules": len(modules),
    "statistics": stats_report,
    "dependencies": dep_results,
    "prerequisites": prereq_results,
    }

    # Save JSON
    json_path = "COMPLETE_MODULE_ANALYSIS.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

        print(f"\nJSON saved: {json_path}")

        # Save top modules by size
        top_modules = sorted(
        [
        (m.path, m.name, m.size, m.line_count, len(m.errors))
        for m in modules.values()
        ],
        key=lambda x: -x[2],  # Sort by size (index 2)
        )[:800]

        with open("TOP_100_MODULES.txt", "w", encoding="utf-8") as f:
            f.write("TOP 100 PYTHON MODULES BY SIZE\n")
            f.write("=" * 70 + "\n\n")
            for i, (path, name, size, lines, errors) in enumerate(top_modules, 1):
                f.write(f"{i:3}. {name}\n")
                f.write(f"    Path: {path}\n")
                f.write(f"    Size: {size:,} bytes ({size / 1024:.1f} KB)\n")
                f.write(f"    Lines: {lines:,}\n")
                f.write(f"    Errors: {errors}\n")
                f.write("\n")

                print("TXT saved: TOP_100_MODULES.txt")

                # Print final summary
                print("\n" + "=" * 70)
                print("ANALYSIS COMPLETE - FINAL SUMMARY")
                print("=" * 70)
                print("\nTotal Python modules on " + os.environ.get("SystemDrive", "C:") + "\\: " + f"{len(modules):,}")
                print(
                f"Total size: {summary['statistics']['summary']['total_size_bytes'] / 1024 / 1024:.1f} MB"
                )
                print(f"Total lines: {summary['statistics']['summary']['total_lines']:,}")
                print(f"Unique imports: {summary['dependencies']['unique_imports']}")
                print("\nOutput files:")
                print("  - COMPLETE_MODULE_ANALYSIS.json")
                print("  - TOP_100_MODULES.txt")

                return summary


if __name__ == "__main__":
    main()
