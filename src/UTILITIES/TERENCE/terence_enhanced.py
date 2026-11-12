# coding: utf-8
"""
TERENCE Enhanced - Conflict Incompatability Resolution Protocol
Reverse-engineers conflicting incompatabilities and generates resolution patterns.

Usage:
    >>> from terence_enhanced import TerenceEnhanced
    >>> terence = TerenceEnhanced()
    >>> terence.analyze_conflicts()
    >>> terence.resolve_incompatabilities()
"""

import re
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Callable, List
from dataclasses import dataclass
from collections import defaultdict, deque
from enum import Enum
import logging
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ConflictSeverity(Enum):
    """Severity levels for conflicts."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ConflictCategory(Enum):
    """Categories of conflicts."""

    PATH_REFERENCE = "path_reference"
    PYTHON_VERSION = "python_version"
    DEPENDENCY = "dependency"
    IMPORT = "import"
    SYNTAX = "syntax"
    NAMING = "naming"
    ENCODING = "encoding"
    ENVIRONMENT = "environment"


@dataclass
class Incompatability:
    """A detected incompatability between patterns."""

    category: ConflictCategory
    source_pattern: str
    conflicting_pattern: str
    conflict_type: str
    files_affected: list[str]
    lines_affected: list[int]
    severity: ConflictSeverity
    root_cause: str
    resolution_strategy: str


@dataclass
class ResolutionPattern:
    """A pattern for resolving conflicts."""

    id: str
    category: ConflictCategory
    pattern_signature: str
    conflict_signatures: list[str]
    resolution_code: str
    success_count: int = 0
    failure_count: int = 0
    last_used: str = ""
    confidence: float = 0.0


@dataclass
class ConflictReport:
    """Report of conflict analysis."""

    timestamp: str
    total_conflicts: int
    conflicts_by_category: dict[str, int]
    conflicts_by_severity: dict[str, int]
    incompatabilities: list[Incompatability]
    suggested_resolutions: list[str]
    files_scanned: int
    duration_ms: float


class ConflictGraph:
    """Graph-based conflict analysis."""

    def __init__(self):
        self.nodes: set[str] = set()
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.node_types: dict[str, str] = {}
        self.edge_types: dict[tuple[str, str], str] = {}

    def add_node(self, node_id: str, node_type: str):
        self.nodes.add(node_id)
        self.node_types[node_id] = node_type

    def add_edge(self, from_node: str, to_node: str, edge_type: str = "related"):
        self.edges[from_node].add(to_node)
        self.edge_types[(from_node, to_node)] = edge_type

    def find_conflict_chain(self, start_node: str) -> list[str]:
        visited = set()
        queue = deque([start_node])
        chain = []
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            chain.append(node)
            for neighbor in self.edges[node]:
                if neighbor not in visited:
                    queue.append(neighbor)
        return chain


class TerenceEnhanced:
    """TERENCE Enhanced - Reverse-Engineered Conflict Resolution."""

    VERSION = "2.0.0"

    def __init__(self, root: (str | None) = None):
        self.root = Path(root) if root else Path(__file__).parent.parent
        self.conflict_graph = ConflictGraph()
        self.incompatabilities: list[Incompatability] = []
        self.resolution_patterns: dict[str, ResolutionPattern] = {}
        self.conflict_history: list[dict] = []

        self.known_conflicts = self._load_known_conflicts()
        self.resolution_strategies = self._init_resolution_strategies()

    def _load_known_conflicts(self) -> dict[str, dict]:
        return {
            "Azazael_spelling": {
                "patterns": [r"Azazael", r"Azazael", r"Azazael"],
                "correct": "Azazael",
                "category": ConflictCategory.NAMING,
                "severity": ConflictSeverity.HIGH,
            },
            "old_path_structure": {
                "patterns": [r"ORCHESTRATION"],
                "correct": "ORCHESTRATION",
                "category": ConflictCategory.PATH_REFERENCE,
                "severity": ConflictSeverity.CRITICAL,
            },
            "python_path_conflict": {
                "patterns": [r"python\.exe"],
                "correct": ".venv/Scripts/python.exe",
                "category": ConflictCategory.PYTHON_VERSION,
                "severity": ConflictSeverity.CRITICAL,
            },
            "bare_except": {
                "patterns": [r"except\s*:"],
                "correct": "except Exception as e:",
                "category": ConflictCategory.SYNTAX,
                "severity": ConflictSeverity.MEDIUM,
            },
        }

    def _init_resolution_strategies(self) -> dict[str, Callable]:
        return {
            "replace_text": self._resolve_replace_text,
            "regex_replace": self._resolve_regex_replace,
            "append_context": self._resolve_append_context,
            "prepend_context": self._resolve_prepend_context,
            "wrap_block": self._resolve_wrap_block,
            "remove_pattern": self._resolve_remove_pattern,
        }

    def analyze_conflicts(self) -> ConflictReport:
        """Analyze codebase for all conflicts."""
        start_time = time.time()

        print("=" * 70)
        print("  TERENCE Enhanced - Conflict Analysis")
        print("=" * 70)
        print(f"Root: {self.root}")
        print()

        self.incompatabilities = []
        self.conflict_graph = ConflictGraph()

        files_scanned = 0
        conflicts_found = defaultdict(int)
        severity_counts = defaultdict(int)

        for py_file in self.root.rglob("*.py"):
            if "__pycache__" in str(py_file) or ".venv" in str(py_file):
                continue

            files_scanned += 1
            file_conflicts = self._scan_file_conflicts(py_file)

            if file_conflicts:
                self.conflict_graph.add_node(str(py_file), "file")

                for conflict in file_conflicts:
                    self.incompatabilities.append(conflict)
                    conflicts_found[conflict.category.value] += 1
                    severity_counts[conflict.severity.value] += 1

                    self.conflict_graph.add_node(
                        f"{conflict.category.value}:{conflict.conflict_type}",
                        "conflict_type",
                    )
                    self.conflict_graph.add_edge(
                        str(py_file),
                        f"{conflict.category.value}:{conflict.conflict_type}",
                        "has_conflict",
                    )

        report = ConflictReport(
            timestamp=datetime.now().isoformat(),
            total_conflicts=len(self.incompatabilities),
            conflicts_by_category=dict(conflicts_found),
            conflicts_by_severity=dict(severity_counts),
            incompatabilities=self.incompatabilities,
            suggested_resolutions=list(self.resolution_strategies.keys()),
            files_scanned=files_scanned,
            duration_ms=(time.time() - start_time) * 1000,
        )

        self._print_conflict_report(report)
        return report

    def _scan_file_conflicts(self, file_path: Path) -> list[Incompatability]:
        conflicts = []

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")

            for line_num, line in enumerate(lines, 1):
                for conflict_name, conflict_data in self.known_conflicts.items():
                    for pattern in conflict_data.get("patterns", []):
                        if re.search(pattern, line):
                            incompatability = Incompatability(
                                category=conflict_data["category"],
                                source_pattern=pattern,
                                conflicting_pattern=line.strip()[:800],
                                conflict_type=conflict_name,
                                files_affected=[str(file_path)],
                                lines_affected=[line_num],
                                severity=conflict_data["severity"],
                                root_cause=f"Pattern '{pattern}' found in {file_path.name}:{line_num}",
                                resolution_strategy="replace_text",
                            )
                            conflicts.append(incompatability)
                            break

        except Exception as e:
            logger.error(f"Error scanning {file_path}: {e}")

        return conflicts

    def _print_conflict_report(self, report: ConflictReport):
        print(f"Files Scanned: {report.files_scanned}")
        print(f"Total Conflicts: {report.total_conflicts}")
        print()

        print("By Category:")
        for cat, count in sorted(report.conflicts_by_category.items()):
            print(f"  {cat}: {count}")

        print()
        print("By Severity:")
        for sev, count in sorted(
            report.conflicts_by_severity.items(),
            key=lambda x: ["critical", "high", "medium", "low"].index(x[0]),
        ):
            print(f"  {sev}: {count}")

        print()
        print("=" * 70)

    def resolve_incompatabilities(self, apply_fixes: bool = False) -> dict:
        """Resolve all detected incompatabilities."""
        print("=" * 70)
        print("  TERENCE Enhanced - Resolution Engine")
        print("=" * 70)
        print(f"Mode: {'APPLY' if apply_fixes else 'PREVIEW'}")
        print()

        if not self.incompatabilities:
            print("[INFO] Auto-analyzing conflicts first...")
            self.analyze_conflicts()

        resolutions_applied = 0
        resolutions_previewed = 0
        files_modified = set()
        errors = []

        for incompatability in self.incompatabilities:
            resolution = self.reverse_engineer_resolution(incompatability)

            for file_path in incompatability.files_affected:
                for line_num in incompatability.lines_affected:
                    if apply_fixes:
                        try:
                            lines = Path(file_path).read_text(
                                encoding="utf-8"
                            ).split("\n")
                            if line_num <= len(lines):
                                lines[line_num - 1] = resolution
                                Path(file_path).write_text("\n".join(lines), encoding="utf-8")
                                files_modified.add(file_path)
                                resolutions_applied += 1
                        except Exception as e:
                            note_failure("terence_enhanced:L317", e)
                            errors.append(f"{file_path}:{line_num} - {str(e)}")
                    else:
                        resolutions_previewed += 1

        result = {
            "applied": resolutions_applied,
            "previewed": resolutions_previewed,
            "files_modified": list(files_modified),
            "errors": errors,
            "patterns_learned": len(self.resolution_patterns),
        }

        print("=" * 70)
        print("RESOLUTION SUMMARY")
        print("=" * 70)
        print(f"Resolved: {resolutions_applied}")
        print(f"Previewed: {resolutions_previewed}")
        print(f"Files modified: {len(files_modified)}")
        print("=" * 70)

        return result

    def reverse_engineer_resolution(self, incompatability: Incompatability) -> str:
        """Reverse-engineer a resolution for a given incompatability."""
        context = self._extract_context(incompatability)
        pattern_signature = self._create_pattern_signature(incompatability)

        existing_pattern = self._find_matching_resolution(pattern_signature)

        if existing_pattern:
            existing_pattern.success_count += 1
            existing_pattern.last_used = datetime.now().isoformat()
            return existing_pattern.resolution_code

        generated_resolution = self._generate_resolution(incompatability, context)

        new_pattern = ResolutionPattern(
            id=f"pattern_{len(self.resolution_patterns) + 1}",
            category=incompatability.category,
            pattern_signature=pattern_signature,
            conflict_signatures=[incompatability.conflict_type],
            resolution_code=generated_resolution,
            confidence=0.5,
        )

        self.resolution_patterns[pattern_signature] = new_pattern

        return generated_resolution

    def _extract_context(self, incompatability: Incompatability) -> dict:
        return {
            "file": incompatability.files_affected[0]
            if incompatability.files_affected
            else "",
            "line": incompatability.lines_affected[0]
            if incompatability.lines_affected
            else 0,
            "category": incompatability.category.value,
            "severity": incompatability.severity.value,
            "conflict_type": incompatability.conflict_type,
        }

    def _create_pattern_signature(self, incompatability: Incompatability) -> str:
        sig_parts = [
            incompatability.category.value,
            incompatability.conflict_type,
            incompatability.source_pattern[:20]
            if incompatability.source_pattern
            else "",
        ]
        return "|".join(sig_parts)

    def _find_matching_resolution(
        self, pattern_signature: str
    ) -> (ResolutionPattern | None):
        for sig, pattern in self.resolution_patterns.items():
            if (
                pattern.category.value in pattern_signature
                and sig.split("|")[1] in pattern_signature
            ):
                return pattern
        return None

    def _generate_resolution(
        self, incompatability: Incompatability, context: dict
    ) -> str:
        strategy = incompatability.resolution_strategy

        if strategy in self.resolution_strategies:
            return self.resolution_strategies[strategy](incompatability, context)

        return self._resolve_replace_text(incompatability, context)

    def _resolve_replace_text(
        self, incompatability: Incompatability, context: dict
    ) -> str:
        if incompatability.conflict_type == "Azazael_spelling":
            correct = self.known_conflicts["Azazael_spelling"]["correct"]
            return incompatability.conflicting_pattern.replace(
                incompatability.source_pattern, correct
            )
        return incompatability.conflicting_pattern

    def _resolve_regex_replace(
        self, incompatability: Incompatability, context: dict
    ) -> str:
        correct = self.known_conflicts.get(incompatability.conflict_type, {}).get(
            "correct", ""
        )
        return re.sub(
            incompatability.source_pattern, correct, incompatability.conflicting_pattern
        )

    def _resolve_append_context(
        self, incompatability: Incompatability, context: dict
    ) -> str:
        return f"{incompatability.conflicting_pattern}\n# Context: {context}"

    def _resolve_prepend_context(
        self, incompatability: Incompatability, context: dict
    ) -> str:
        # (2026-09-10: was a bare TODO emitter — debt generator. Name the
        # conflict and the needed decision instead of an open TODO.)
        return (f"# UNRESOLVED {incompatability.conflict_type}: "
                f"review {incompatability.conflicting_pattern}")

    def _resolve_wrap_block(
        self, incompatability: Incompatability, context: dict
    ) -> str:
        # (2026-09-10: was try/except-pass — the silencing anti-pattern.)
        return (f"try:\n    {incompatability.conflicting_pattern}\n"
                f"except Exception as e:\n    print(f\"terence-guard: {{e}}\")")

    def _resolve_remove_pattern(
        self, incompatability: Incompatability, context: dict
    ) -> str:
        return f"# REMOVED: {incompatability.conflicting_pattern}"

    def get_status(self) -> dict:
        """Get status."""
        return {
            "version": self.VERSION,
            "root": str(self.root),
            "conflicts_detected": len(self.incompatabilities),
            "resolution_patterns": len(self.resolution_patterns),
            "known_conflict_types": len(self.known_conflicts),
            "resolution_strategies": list(self.resolution_strategies.keys()),
        }


def main():
    """CLI interface."""
    parser = argparse.ArgumentParser(
    description="TERENCE Enhanced - Conflict Resolution"
    )
    parser.add_argument(
    "--version", action="version", version=f"TERENCE v{TerenceEnhanced.VERSION}"
    )

    subparsers = parser.add_subparsers(dest="command")

    p_analyze = subparsers.add_parser("analyze", help="Analyze conflicts")
    p_resolve = subparsers.add_parser("resolve", help="Resolve conflicts")
    p_resolve.add_argument("--apply", action="store_true", help="Apply fixes")
    p_status = subparsers.add_parser("status", help="Show status")

    args = parser.parse_args()

    terence = TerenceEnhanced()

    if args.command == "analyze":
        terence.analyze_conflicts()
    elif args.command == "resolve":
        terence.resolve_incompatabilities(apply_fixes=args.apply)
    elif args.command == "status":
        print(json.dumps(terence.get_status(), indent=2))
    else:
        print("TERENCE Enhanced")
        print()
        print("Usage:")
        print("  python terence_enhanced.py analyze")
        print("  python terence_enhanced.py resolve")
        print("  python terence_enhanced.py resolve --apply")
        print("  python terence_enhanced.py status")


if __name__ == "__main__":
    main()
