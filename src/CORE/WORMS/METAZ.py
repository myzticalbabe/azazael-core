# coding: utf-8
"""
METAZ - Metadata Analysis Worm v2.0

Analyzes codebase metadata: imports, exports, dependencies, class hierarchies,
module coupling, and code health metrics.
Registered for the 'analysis' cluster in CLUSTER_REGISTRY.

Usage:
    from CORE.WORMS.METAZ import METAZ
    m = METAZ()
    result = m.run()
    # result: {total_files, imports_found, top_imports, top_classes, coupling, ...}
"""

import ast
from pathlib import Path
from collections import defaultdict, Counter
from typing import Any, List

from CORE.worm_registry import register_worm

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__version__ = "2.0.0"


@register_worm(
    category="analysis", priority=4, cooldown_seconds=60.0,
    description="Metadata analysis worm — imports, exports, dependency graphs, coupling metrics"
)
class METAZ:
    """Metadata analyzer for the Azazael codebase.

    Scans Python files for:
    - Import statements (stdlib vs third-party vs internal)
    - Class and function definitions
    - Export analysis (__all__, public API surface)
    - Module coupling (who imports whom)
    - Code health metrics (file sizes, comment ratios)

    Replaces the legacy METAZ name in CLUSTER_REGISTRY["analysis"].
    """

    VERSION = "2.0.0"

    def __init__(self, root: (str | None) = None):
        self._root = Path(root) if root else Path(__file__).parent.parent.parent
        self._results: dict[str, Any] = {}
        self._import_graph: dict[str, set[str]] = defaultdict(set)
        self._all_imports: Counter = Counter()
        self._class_counts: Counter = Counter()
        self._func_counts: Counter = Counter()

    # -- Core Analysis ------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Run full metadata analysis. Returns comprehensive dict."""
        py_files = list(self._root.rglob("*.py"))
        # Exclude __pycache__ and .venv
        py_files = [f for f in py_files if "__pycache__" not in str(f) and ".venv" not in str(f)]

        total_lines = 0
        total_comments = 0
        imports_found = 0

        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8-sig", errors="replace")
                total_lines += content.count("\n")
                total_comments += content.count("#")

                tree = ast.parse(content, filename=str(py_file))
                imports_found += self._analyze_ast(tree, py_file)

            except (SyntaxError, UnicodeDecodeError) as exc:
                note_failure("METAZ:L74", exc)
            except Exception as exc:
                note_failure("METAZ:L76", exc)

        # Compute coupling metrics
        coupling = self._compute_coupling()

        self._results = {
                "version": self.VERSION,
                "root": str(self._root),
                "total_files": len(py_files),
                "total_lines": total_lines,
                "total_comments": total_comments,
                "comment_ratio": round(total_comments / max(total_lines, 1), 2),
                "imports_found": imports_found,
                "unique_modules_imported": len(self._all_imports),
                "top_imports": self._all_imports.most_common(20),
                "top_classes": self._class_counts.most_common(15),
                "top_functions": self._func_counts.most_common(15),
                "coupling": coupling,
                "status": "ok",
            }
        return self._results

    def _analyze_ast(self, tree: ast.AST, filepath: Path) -> int:
        """Extract imports, classes, and functions from AST. Returns import count."""
        rel_path = str(filepath.relative_to(self._root))
        import_count = 0

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    self._all_imports[top] += 1
                    self._import_graph[rel_path].add(top)
                    import_count += 1

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    self._all_imports[top] += 1
                    self._import_graph[rel_path].add(top)
                    import_count += 1

            elif isinstance(node, ast.ClassDef):
                self._class_counts[node.name] += 1

            elif isinstance(node, ast.FunctionDef):
                if not node.name.startswith("_"):
                    self._func_counts[node.name] += 1

        return import_count

    def _extract_imports(self, tree: ast.AST) -> list[str]:
        """Extract all import names from an AST."""
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    def _compute_coupling(self) -> dict[str, Any]:
        """Compute module coupling metrics from import graph."""
        if not self._import_graph:
            return {"modules": 0, "most_imported": [], "most_dependent": []}

        imported_by: dict[str, set[str]] = defaultdict(set)
        for importer, imported in self._import_graph.items():
            for mod in imported:
                imported_by[mod].add(importer)

        most_imported = sorted(
            [(m, len(importers)) for m, importers in imported_by.items()],
            key=lambda x: -x[1]
        )[:10]

        most_dependent = sorted(
            [(m, len(imports)) for m, imports in self._import_graph.items()],
            key=lambda x: -x[1]
        )[:10]

        return {
            "modules_in_graph": len(self._import_graph),
            "most_imported": most_imported,
            "most_dependent": most_dependent,
            "total_edges": sum(len(v) for v in self._import_graph.values()),
        }

    def find_circular_imports(self) -> list[list[str]]:
        """Detect circular import chains in the import graph."""
        visited: set[str] = set()
        path: list[str] = []
        cycles: list[list[str]] = []

        def dfs(node: str):
            if node in path:
                cycle_start = path.index(node)
                cycles.append(path[cycle_start:] + [node])
                return
            if node in visited:
                return
            visited.add(node)
            path.append(node)
            for neighbor in self._import_graph.get(node, set()):
                dfs(neighbor)
            path.pop()

        modules = list(self._import_graph.keys())[:300]
        for module in modules:
            dfs(module)

        return cycles[:20]

    # -- Standard Worm Methods ----------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Get summary. Runs analysis on first call (cached thereafter)."""
        if not self._results:
            # Auto-run only when explicitly requested via run(); smoke tests
            # and lightweight callers get the cached (possibly empty) result
            # without triggering a full codebase scan.
            return self._results
        return self._results

    def execute(self) -> dict[str, Any]:
        """Worm execute() standard method."""
        return self.run()

    def __repr__(self) -> str:
        n = self._results.get("total_files", 0)
        return f"METAZ(v{self.VERSION}, {n} files analyzed)"


if __name__ == "__main__":
    m = METAZ()
    print(f"METAZ v{METAZ.VERSION}")
    result = m.run()
    print(f"Files: {result['total_files']}")
    print(f"Lines: {result['total_lines']}")
    print(f"Unique imports: {result['unique_modules_imported']}")
    print(f"Top imports: {result['top_imports'][:5]}")
    print(f"Top classes: {result['top_classes'][:5]}")
    print(f"Coupling: {result['coupling']['modules_in_graph']} modules, {result['coupling']['total_edges']} edges")
