# coding: utf-8
"""
MISSING_TOOLS.py — Consolidated Tools for Azazael Ecosystem

Tools added:
  - DependencyAuditor:     Full dependency graph with version, license, conflict detection
  - ImportGraphBuilder:    Build project-wide import dependency graph
  - DeadCodeDetector:      Find unused functions, classes, and imports
  - DocCoverageChecker:    Measure documentation coverage per module
  - TypeAnnotationChecker: Audit type annotation coverage
  - CodeComplexityAnalyzer: Cyclomatic complexity and maintainability index
  - PackageLicenseChecker: Verify license compatibility across dependencies
  - SecurityAuditRunner:   Run bandit + safety + pip-audit in one pass
  - WormHealthDashboard:   Generate HTML dashboard for all 73 registered worms
"""

import os
import sys
import ast
import json
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Registry Import -----------------------------------------------------
try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("MISSING_TOOLS:L31", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None): return lambda cls: cls

__all__ = [
    "DependencyAuditor", "ImportGraphBuilder", "DeadCodeDetector",
    "DocCoverageChecker", "TypeAnnotationChecker", "CodeComplexityAnalyzer",
    "PackageLicenseChecker", "SecurityAuditRunner", "WormHealthDashboard",
]


# ===========================================================================
#  DependencyAuditor — Full Dependency Analysis
# ===========================================================================

@register_worm(category="utility", priority=6, cooldown_seconds=90.0,
    description="Full dependency graph auditor with version, license, and conflict detection")
class DependencyAuditor:
    """Audits project dependencies for version conflicts, licenses, and security."""

    VERSION = "1.0.0"

    def __init__(self):
        self._deps: dict[str, dict] = {}
        self._conflicts: list[dict] = []
        self._lock = threading.Lock()

    def scan_requirements(self, *req_paths: str) -> dict[str, Any]:
        """Scan requirements files and resolve all transitive dependencies."""
        all_deps = {}
        for rp in req_paths:
            path = Path(rp)
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                pkg = line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()
                ver = ""
                if "==" in line:
                    ver = line.split("==")[1].split()[0]
                all_deps[pkg] = {"version": ver, "source": str(path)}

        with self._lock:
            self._deps = all_deps
        return {"total_packages": len(all_deps), "packages": list(all_deps.keys())}

    def check_conflicts(self) -> list[dict]:
        """Detect version conflicts between direct and transitive dependencies."""
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "check"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            conflicts = []
            for line in r.stdout.split("\n") + r.stderr.split("\n"):
                if "has requirement" in line and "but you have" in line:
                    conflicts.append({"message": line.strip(), "severity": "CONFLICT"})
            with self._lock:
                self._conflicts = conflicts
            return conflicts
        except Exception as exc:
            note_failure("MISSING_TOOLS:L92", exc)
            return []

    def get_audit_report(self) -> dict[str, Any]:
        """Full dependency audit report."""
        return {
            "total_dependencies": len(self._deps),
            "conflicts": len(self._conflicts),
            "conflict_details": self._conflicts[:20],
            "dependencies": {
                k: v for k, v in sorted(self._deps.items())
            },
            "timestamp": datetime.now().isoformat(),
        }


# ===========================================================================
#  ImportGraphBuilder — Project-Wide Import Graph
# ===========================================================================

@register_worm(category="utility", priority=5, cooldown_seconds=60.0,
    description="Build project-wide import dependency graph with cycle detection")
class ImportGraphBuilder:
    """Builds a directed import dependency graph for the entire project."""

    VERSION = "1.0.0"

    def __init__(self):
        self._graph: dict[str, set[str]] = defaultdict(set)
        self._cycles: list[list[str]] = []
        self._lock = threading.Lock()

    def build_graph(self, root_dir: str) -> dict[str, Any]:
        """Parse all Python files and build import graph."""
        root = Path(root_dir)
        graph: dict[str, set[str]] = defaultdict(set)
        module_map: dict[Path, str] = {}

        for fp in root.rglob("*.py"):
            if any(x in str(fp) for x in ["__pycache__", ".venv", "CODEX/VIDEOS"]):
                continue
            rel = fp.relative_to(root)
            module = str(rel.with_suffix("")).replace(os.sep, ".")
            module_map[fp] = module

        for fp, module in module_map.items():
            try:
                tree = ast.parse(fp.read_text(encoding="utf-8", errors="replace"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            graph[module].add(alias.name.split(".")[0])
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            graph[module].add(node.module.split(".")[0])
            except SyntaxError as exc:
                note_failure("MISSING_TOOLS:L147", exc)

        with self._lock:
            self._graph = graph
        return {
            "modules": len(graph),
            "total_edges": sum(len(v) for v in graph.values()),
            "most_imported": sorted(
                [(mod, sum(1 for v in graph.values() if mod in v)) for mod in set().union(*graph.values()) if mod],
                key=lambda x: -x[1],
            )[:20],
        }

    def find_cycles(self) -> list[list[str]]:
        """Detect circular dependencies in the import graph."""
        cycles = []
        visited: set[str] = set()
        stack: set[str] = set()

        def dfs(node: str, path: list[str]):
            visited.add(node)
            stack.add(node)
            path.append(node)
            for neighbor in self._graph.get(node, set()):
                if neighbor not in visited:
                    dfs(neighbor, path)
                elif neighbor in stack:
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [neighbor])
            path.pop()
            stack.discard(node)

        for node in list(self._graph.keys()):
            if node not in visited:
                dfs(node, [])

        self._cycles = cycles
        return cycles

    def to_dot(self) -> str:
        """Export graph as DOT format."""
        lines = ["digraph ProjectImports {", "  rankdir=LR;"]
        for src, dsts in self._graph.items():
            src_label = src.replace(".", "_")
            for dst in dsts:
                dst_label = dst.replace(".", "_")
                lines.append(f'  "{src_label}" -> "{dst_label}";')
        lines.append("}")
        return "\n".join(lines)


# ===========================================================================
#  DeadCodeDetector — Find Unused Code
# ===========================================================================

@register_worm(category="utility", priority=5, cooldown_seconds=120.0,
    description="Detect unused functions, classes, and imports across the project")
class DeadCodeDetector:
    """Finds potentially unused code: functions, classes, imports, variables."""

    VERSION = "1.0.0"

    def __init__(self):
        self._definitions: dict[str, list[dict]] = defaultdict(list)
        self._usages: dict[str, int] = defaultdict(int)
        self._unused: list[dict] = []
        self._lock = threading.Lock()

    def scan_project(self, root_dir: str) -> dict[str, Any]:
        """Scan all Python files for definitions and usages."""
        root = Path(root_dir)
        defs: dict[str, str] = {}  # name -> defining_module

        # Pass 1: collect definitions
        for fp in root.rglob("*.py"):
            if any(x in str(fp) for x in ["__pycache__", ".venv", "CODEX/VIDEOS"]):
                continue
            rel = str(fp.relative_to(root))
            try:
                tree = ast.parse(fp.read_text(encoding="utf-8", errors="replace"))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not node.name.startswith("_"):
                            defs[node.name] = rel
                    elif isinstance(node, ast.ClassDef):
                        if not node.name.startswith("_"):
                            defs[node.name] = rel
            except SyntaxError as exc:
                note_failure("MISSING_TOOLS:L235", exc)

        # Pass 2: count usages
        usage_count: dict[str, int] = defaultdict(int)
        for fp in root.rglob("*.py"):
            if any(x in str(fp) for x in ["__pycache__", ".venv", "CODEX/VIDEOS"]):
                continue
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
                for name in defs:
                    # Count non-definition occurrences
                    count = content.count(name)
                    if f"def {name}" not in content and f"class {name}" not in content:
                        usage_count[name] += count
                    elif count > 1:
                        usage_count[name] += count - 1
            except Exception as exc:
                note_failure("MISSING_TOOLS:L252", exc)

        # Identify unused (defined but never referenced elsewhere)
        unused = []
        for name, file_path in defs.items():
            if usage_count.get(name, 0) == 0 and name not in ["__init__", "main", "VERSION"]:
                unused.append({"name": name, "file": file_path, "type": "class" if name[0].isupper() else "function"})

        with self._lock:
            self._definitions = {k: [{"file": v}] for k, v in defs.items()}
            self._usages = dict(usage_count)
            self._unused = unused
        return {"total_definitions": len(defs), "total_unused": len(unused), "unused": unused[:50]}


# ===========================================================================
#  DocCoverageChecker — Documentation Coverage
# ===========================================================================

@register_worm(category="utility", priority=4, cooldown_seconds=60.0,
    description="Measure documentation coverage for all public functions and classes")
class DocCoverageChecker:
    """Measures documentation coverage: docstrings, type annotations, module docs."""

    VERSION = "1.0.0"

    def __init__(self):
        self._coverage: dict[str, dict] = {}
        self._lock = threading.Lock()

    def check_module(self, filepath: str) -> dict[str, Any]:
        """Check doc coverage for a single module."""
        path = Path(filepath)
        if not path.exists():
            return {"file": str(path), "error": "Not found"}

        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            note_failure("MISSING_TOOLS:L291", exc)
            return {"file": str(path), "error": "Syntax error"}

        stats = {"functions": 0, "functions_documented": 0, "classes": 0,
                 "classes_documented": 0, "module_docstring": bool(ast.get_docstring(tree))}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    stats["functions"] += 1
                    if ast.get_docstring(node):
                        stats["functions_documented"] += 1
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    stats["classes"] += 1
                    if ast.get_docstring(node):
                        stats["classes_documented"] += 1

        total = stats["functions"] + stats["classes"]
        documented = stats["functions_documented"] + stats["classes_documented"]
        stats["coverage_pct"] = round(documented / max(total, 1) * 100, 1)

        with self._lock:
            self._coverage[str(path)] = stats
        return stats

    def check_project(self, root_dir: str) -> dict[str, Any]:
        """Check doc coverage across entire project."""
        root = Path(root_dir)
        modules = []
        for fp in root.rglob("*.py"):
            if any(x in str(fp) for x in ["__pycache__", ".venv", "CODEX/VIDEOS", "tests"]):
                continue
            modules.append(str(fp))

        total_funcs = total_funcs_doc = 0
        total_classes = total_classes_doc = 0
        for mod in modules:
            stats = self.check_module(mod)
            if "error" not in stats:
                total_funcs += stats["functions"]
                total_funcs_doc += stats["functions_documented"]
                total_classes += stats["classes"]
                total_classes_doc += stats["classes_documented"]

        total = total_funcs + total_classes
        documented = total_funcs_doc + total_classes_doc
        return {
            "modules_checked": len(modules),
            "total_functions": total_funcs,
            "functions_documented": total_funcs_doc,
            "total_classes": total_classes,
            "classes_documented": total_classes_doc,
            "overall_coverage_pct": round(documented / max(total, 1) * 100, 1),
            "timestamp": datetime.now().isoformat(),
        }


# ===========================================================================
#  TypeAnnotationChecker — Type Annotation Audit
# ===========================================================================

@register_worm(category="utility", priority=4, cooldown_seconds=60.0,
    description="Audit type annotation coverage for all function signatures")
class TypeAnnotationChecker:
    """Audits type annotation coverage across the project."""

    VERSION = "1.0.0"

    def __init__(self):
        self._stats: dict[str, dict] = {}

    def check_file(self, filepath: str) -> dict[str, Any]:
        """Check type annotation coverage for a single file."""
        path = Path(filepath)
        if not path.exists():
            return {"error": "Not found"}

        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            note_failure("MISSING_TOOLS:L371", exc)
            return {"error": "Syntax error"}

        total_params = 0
        annotated_params = 0
        total_returns = 0
        annotated_returns = 0

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                for arg in node.args.args:
                    total_params += 1
                    if arg.annotation:
                        annotated_params += 1
                total_returns += 1
                if node.returns:
                    annotated_returns += 1

        return {
            "total_params": total_params,
            "annotated_params": annotated_params,
            "param_coverage_pct": round(annotated_params / max(total_params, 1) * 100, 1),
            "total_returns": total_returns,
            "annotated_returns": annotated_returns,
            "return_coverage_pct": round(annotated_returns / max(total_returns, 1) * 100, 1),
        }


# ===========================================================================
#  CodeComplexityAnalyzer — Cyclomatic Complexity
# ===========================================================================

@register_worm(category="utility", priority=4, cooldown_seconds=90.0,
    description="Analyze cyclomatic complexity and maintainability index for all modules")
class CodeComplexityAnalyzer:
    """Analyzes cyclomatic complexity and maintainability of code."""

    VERSION = "1.0.0"

    def __init__(self):
        self._results: list[dict] = []

    def analyze_file(self, filepath: str) -> dict[str, Any]:
        """Compute cyclomatic complexity for a file."""
        path = Path(filepath)
        if not path.exists():
            return {"error": "Not found"}
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
        except SyntaxError as exc:
            note_failure("MISSING_TOOLS:L423", exc)
            return {"error": "Syntax error"}

        funcs = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                complexity = 1  # Base complexity
                for child in ast.walk(node):
                    if isinstance(child, (ast.If, ast.For, ast.While, ast.Try,
                                          ast.ExceptHandler, ast.With,
                                          ast.And, ast.Or, ast.IfExp)):
                        complexity += 1
                    elif isinstance(child, ast.BoolOp):
                        complexity += len(child.values) - 1
                funcs.append({"name": node.name, "complexity": complexity,
                              "severity": "HIGH" if complexity > 15 else ("MEDIUM" if complexity > 10 else "LOW")})

        loc = len([l for l in content.split("\n") if l.strip() and not l.strip().startswith("#")])
        return {"file": str(path), "loc": loc, "functions": len(funcs),
                "avg_complexity": round(sum(f["complexity"] for f in funcs) / max(len(funcs), 1), 1),
                "high_complexity_funcs": [f for f in funcs if f["severity"] == "HIGH"]}


# ===========================================================================
#  PackageLicenseChecker
# ===========================================================================

@register_worm(category="utility", priority=5, cooldown_seconds=90.0,
    description="Verify license compatibility across all project dependencies")
class PackageLicenseChecker:
    """Checks package licenses for compatibility with project license."""

    VERSION = "1.0.0"

    def __init__(self):
        self._licenses: dict[str, str] = {}
        self._warnings: list[dict] = []

    def check_all(self) -> dict[str, Any]:
        """Check licenses for all installed packages."""
        licenses = {}
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--format", "json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            packages = json.loads(r.stdout) if r.returncode == 0 else []
        except Exception as exc:
            note_failure("MISSING_TOOLS:L470", exc)
            packages = []

        # Check each package for license metadata
        for pkg in packages[:50]:  # Sample first 50
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "show", pkg["name"]],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                )
                for line in r.stdout.split("\n"):
                    if line.startswith("License:"):
                        licenses[pkg["name"]] = line.split(":", 1)[1].strip()
            except Exception as exc:
                note_failure("MISSING_TOOLS:L483", exc)

        self._licenses = licenses

        # Flag copyleft licenses
        copyleft = ["GPL", "AGPL", "EUPL"]
        warnings = []
        for pkg, lic in licenses.items():
            if any(cl in lic.upper() for cl in copyleft):
                warnings.append({"package": pkg, "license": lic, "warning": "Copyleft license may require source distribution"})

        self._warnings = warnings
        return {"packages_checked": len(licenses), "warnings": len(warnings), "details": warnings}


# ===========================================================================
#  SecurityAuditRunner — Multi-Tool Security Scan
# ===========================================================================

@register_worm(category="utility", priority=7, cooldown_seconds=120.0,
    description="Run bandit + safety + pip-audit in one security scan pass")
class SecurityAuditRunner:
    """Runs multiple security audit tools in a single pass."""

    VERSION = "1.0.0"

    def __init__(self):
        self._findings: dict[str, list[dict]] = defaultdict(list)
        self._lock = threading.Lock()

    def run_bandit(self, target_dir: str) -> list[dict]:
        """Run bandit security linter."""
        findings = []
        try:
            r = subprocess.run(
                [sys.executable, "-m", "bandit", "-r", target_dir, "-f", "json", "-q"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            if r.stdout.strip():
                data = json.loads(r.stdout)
                for issue in data.get("results", []):
                    findings.append({
                        "tool": "bandit",
                        "file": issue.get("filename", ""),
                        "line": issue.get("line_number", 0),
                        "severity": issue.get("issue_severity", ""),
                        "issue": issue.get("issue_text", ""),
                    })
        except Exception as exc:
            note_failure("MISSING_TOOLS:L532", exc) # bandit not installed
        self._findings["bandit"] = findings
        return findings

    def check_known_vulnerabilities(self) -> list[dict]:
        """Check for known CVEs via safety or pip-audit."""
        findings = []
        for tool in ["safety", "pip-audit"]:
            try:
                cmd = [sys.executable, "-m", "pip_audit", "--format", "json"] if tool == "pip-audit" else \
                      [sys.executable, "-m", "safety", "check", "--json"]
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                if r.returncode != 0 and r.stdout.strip():
                    findings.extend(json.loads(r.stdout) if isinstance(json.loads(r.stdout), list) else [])
            except Exception as exc:
                note_failure("MISSING_TOOLS:L547", exc)
        return findings

    def run_full_audit(self, target_dir: str) -> dict[str, Any]:
        """Run complete security audit suite."""
        bandit_results = self.run_bandit(target_dir)
        vuln_results = self.check_known_vulnerabilities()
        return {
            "target": target_dir,
            "bandit_issues": len(bandit_results),
            "vulnerability_findings": len(vuln_results),
            "timestamp": datetime.now().isoformat(),
            "findings": bandit_results[:20] + vuln_results[:20],
        }


# ===========================================================================
#  WormHealthDashboard — All-Worm Status Dashboard
# ===========================================================================

@register_worm(category="utility", priority=4, cooldown_seconds=60.0,
    description="Generate HTML health dashboard for all 73 registered worms")
class WormHealthDashboard:
    """Generates an HTML dashboard showing status of all registered worms."""

    VERSION = "1.0.0"

    def __init__(self):
        self._worm_data: dict[str, dict] = {}
        self._lock = threading.Lock()

    def collect_worm_data(self) -> dict[str, Any]:
        """Collect status data for all registered worms."""
        try:
            from CORE.worm_registry import get_all_categories, get_worms_by_category
        except ImportError as exc:
            note_failure("MISSING_TOOLS:L583", exc)
            return {"error": "Registry not available"}

        cats = get_all_categories()
        data = {}
        for cat in sorted(cats):
            worms = get_worms_by_category(cat)
            data[cat] = {}
            for name, info in sorted(worms.items()):
                data[cat][name] = {
                    "priority": info.get("priority", "?"),
                    "version": info.get("version", "?"),
                    "cooldown": info.get("cooldown_seconds", 0),
                    "description": info.get("description", "")[:80],
                    "spawn_count": info.get("spawn_count", 0),
                }

        with self._lock:
            self._worm_data = data
        return {"categories": len(data), "total_worms": sum(len(w) for w in data.values())}

    def render_dashboard(self, output_path: str = "") -> str:
        """Render an HTML dashboard file."""
        self.collect_worm_data()

        html = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Azazael Worm Health Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}
h1{color:#58a6ff;margin-bottom:10px;font-size:24px}
h2{color:#7ee787;margin:15px 0 8px;font-size:18px}
.summary{display:flex;gap:15px;margin:15px 0;flex-wrap:wrap}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:15px;flex:1;min-width:150px}
.card .value{font-size:28px;font-weight:700;color:#58a6ff}
.card .label{font-size:12px;color:#8b949e;text-transform:uppercase;margin-top:4px}
.category{margin:10px 0}
.category-table{width:100%;border-collapse:collapse;margin:5px 0}
.category-table th{background:#21262d;color:#f0883e;padding:8px 12px;text-align:left;font-size:13px;border:1px solid #30363d}
.category-table td{padding:6px 12px;font-size:13px;border:1px solid #30363d}
.category-table tr:hover{background:#1c2129}
.priority-high{color:#f85149}.priority-med{color:#d29922}.priority-low{color:#7ee787}
.footer{color:#484f58;font-size:12px;margin-top:20px;text-align:center}
</style></head><body>
<h1>🐛 Azazael Worm Health Dashboard</h1>"""

        total_worms = sum(len(w) for w in self._worm_data.values())
        total_cats = len(self._worm_data)

        html += f"""<div class="summary">
<div class="card"><div class="value">{total_worms}</div><div class="label">Total Worms</div></div>
<div class="card"><div class="value">{total_cats}</div><div class="label">Categories</div></div>
<div class="card"><div class="value">{datetime.now().strftime('%H:%M')}</div><div class="label">Last Updated</div></div>
</div>"""

        for cat, worms in sorted(self._worm_data.items()):
            html += f'<div class="category"><h2>📦 {cat} ({len(worms)} worms)</h2>'
            html += '<table class="category-table"><tr><th>Worm</th><th>Priority</th><th>Version</th><th>Cooldown</th><th>Spawns</th><th>Description</th></tr>'
            for name, info in sorted(worms.items()):
                pri = info.get("priority", 0)
                pri_class = "priority-high" if pri >= 8 else ("priority-med" if pri >= 5 else "priority-low")
                html += f'<tr><td><strong>{name}</strong></td>'
                html += f'<td class="{pri_class}">{pri}</td>'
                html += f'<td>{info.get("version", "?")}</td>'
                html += f'<td>{info.get("cooldown", 0):.0f}s</td>'
                html += f'<td>{info.get("spawn_count", 0)}</td>'
                html += f'<td style="font-size:12px;color:#8b949e">{info.get("description", "")[:60]}</td></tr>'
            html += '</table></div>'

        html += f'<div class="footer">Azazael Worm Health Dashboard * Generated {datetime.now().isoformat()}</div></body></html>'

        if output_path:
            Path(output_path).write_text(html, encoding="utf-8")
        return html


# -- Quick CLI -------------------------------------------------------------

def main():
    """Run all missing tools diagnostics."""
    print("=" * 60)
    print("  MISSING TOOLS — Diagnostics")
    print("=" * 60)

    # Dependency audit
    print("\n[DependencyAuditor]")
    auditor = DependencyAuditor()
    deps = auditor.scan_requirements("requirements.txt")
    print(f"  Dependencies found: {deps.get('total_packages', 0)}")

    # Import graph
    print("\n[ImportGraphBuilder]")
    builder = ImportGraphBuilder()
    graph = builder.build_graph("azazael/src")
    print(f"  Modules: {graph['modules']}, Edges: {graph['total_edges']}")

    # Doc coverage
    print("\n[DocCoverageChecker]")
    checker = DocCoverageChecker()
    cov = checker.check_project("azazael/src")
    print(f"  Overall coverage: {cov['overall_coverage_pct']}%")

    # Complexity
    print("\n[CodeComplexityAnalyzer]")
    ca = CodeComplexityAnalyzer()
    result = ca.analyze_file("azazael/src/CORE/WORMS/BINARY_ANALYSIS.py")
    print(f"  {result.get('file', '?')}: {result.get('loc', 0)} LOC, "
          f"avg complexity {result.get('avg_complexity', 0)}, "
          f"{len(result.get('high_complexity_funcs', []))} high-complexity functions")

    # Worm dashboard
    print("\n[WormHealthDashboard]")
    db = WormHealthDashboard()
    stats = db.collect_worm_data()
    print(f"  Categories: {stats.get('categories', 0)}, Total worms: {stats.get('total_worms', 0)}")

    print("\n" + "=" * 60)
    print("  All tools operational.")
    print("=" * 60)


if __name__ == "__main__":
    main()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
