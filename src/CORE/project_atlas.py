"""Read-only cross-project provenance and architecture atlas."""

from __future__ import annotations

import ast
import csv
import hashlib
import html
import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


DEFAULT_EXCLUDED_PARTS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".hypothesis", "node_modules", "site-packages", ".venv",
    "venv", "venv-314t", "build", "dist", "target", "bin", "obj",
}
SOURCE_SUFFIXES = {".py", ".rs", ".go", ".js", ".jsx", ".ts", ".tsx", ".c", ".cc", ".cpp", ".h", ".hpp", ".java", ".cs", ".rb", ".php", ".sh", ".bash", ".ps1", ".bat", ".cmd"}
MANIFEST_NAMES = {"pyproject.toml", "setup.py", "setup.cfg", "Cargo.toml", "go.mod", "package.json", "requirements.txt", "pom.xml", "build.gradle", "Dockerfile", "CMakeLists.txt"}
CLI_NAMES = {"cli", "command", "commands", "launcher", "launchers", "menu", "menus", "entrypoint", "entrypoints"}


@dataclass
class AtlasFile:
    """One readable or unreadable file record."""

    project: str
    root: str
    path: str
    suffix: str
    size: int = 0
    sha256: str | None = None
    classification: str = "other"
    readable: bool = True
    error: str | None = None
    indicators: list[str] = field(default_factory=list)


@dataclass
class AtlasProject:
    """A scanned project root and its aggregate facts."""

    project: str
    root: str
    exists: bool
    files: int = 0
    readable_files: int = 0
    source_files: int = 0
    manifests: int = 0
    tests: int = 0
    cli_candidates: int = 0
    documentation: int = 0
    logs: int = 0
    excluded_paths: int = 0
    parse_errors: int = 0
    notes: list[str] = field(default_factory=list)


class ProjectAtlas:
    """Scan explicitly supplied roots without importing or executing code."""

    def __init__(self, roots: dict[str, Path | str], excluded_parts: Iterable[str] | None = None, max_files_per_project: int = 100000) -> None:
        self.roots = {name: Path(root).expanduser() for name, root in roots.items()}
        self.excluded_parts = set(excluded_parts or DEFAULT_EXCLUDED_PARTS)
        self.max_files_per_project = max_files_per_project
        self.projects: list[AtlasProject] = []
        self.files: list[AtlasFile] = []
        self.edges: list[dict[str, str]] = []
        self.unavailable: list[dict[str, str]] = []

    def scan(self) -> "ProjectAtlas":
        for name, root in self.roots.items():
            project = AtlasProject(name, str(root), root.exists())
            self.projects.append(project)
            if not root.exists():
                self.unavailable.append({"project": name, "root": str(root), "reason": "root does not exist"})
                continue
            try:
                paths = root.rglob("*")
            except OSError as exc:
                note_failure("project_atlas:L85", exc)
                self.unavailable.append({"project": name, "root": str(root), "reason": str(exc)})
                continue
            seen = 0
            for path in paths:
                if seen >= self.max_files_per_project:
                    project.notes.append(f"scan limit reached: {self.max_files_per_project} paths")
                    break
                if not path.is_file():
                    continue
                seen += 1
                try:
                    relative = path.relative_to(root)
                except ValueError as exc:
                    note_failure("project_atlas:L98", exc)
                    continue
                if any(part in self.excluded_parts for part in relative.parts):
                    project.excluded_paths += 1
                    continue
                record = self._record_file(name, root, path, relative)
                self.files.append(record)
                self._aggregate(project, record)
                self._relationship_edges(name, root, path, relative, record)
        return self

    def _record_file(self, project: str, root: Path, path: Path, relative: Path) -> AtlasFile:
        suffix = path.suffix.lower()
        classification = self._classification(path, relative)
        indicators: list[str] = []
        if path.name in MANIFEST_NAMES:
            indicators.append("manifest")
        if any(part.lower() in CLI_NAMES for part in relative.parts) or "cli" in path.stem.lower() or "launcher" in path.stem.lower():
            indicators.append("cli_candidate")
        if "test" in path.stem.lower() or any(part.lower() in {"test", "tests", "testing", "benchmarks"} for part in relative.parts):
            indicators.append("test_candidate")
        try:
            data = path.read_bytes()
            return AtlasFile(project, str(root), relative.as_posix(), suffix, len(data), hashlib.sha256(data).hexdigest(), classification, True, None, indicators)
        except (OSError, UnicodeError) as exc:
            note_failure("project_atlas:L122", exc)
            return AtlasFile(project, str(root), relative.as_posix(), suffix, 0, None, classification, False, str(exc), indicators)

    def _classification(self, path: Path, relative: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in SOURCE_SUFFIXES:
            return "source"
        if suffix in {".md", ".rst", ".txt", ".html", ".adoc"}:
            return "documentation"
        if suffix in {".log", ".out", ".err", ".dmp"}:
            return "log"
        if suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".xml"} or path.name in MANIFEST_NAMES:
            return "configuration"
        if suffix in {".exe", ".dll", ".so", ".dylib", ".mp4", ".zip", ".whl", ".pyc"}:
            return "artifact"
        return "other"

    def _aggregate(self, project: AtlasProject, record: AtlasFile) -> None:
        project.files += 1
        project.readable_files += int(record.readable)
        project.source_files += int(record.classification == "source")
        project.manifests += int("manifest" in record.indicators)
        project.tests += int("test_candidate" in record.indicators)
        project.cli_candidates += int("cli_candidate" in record.indicators)
        project.documentation += int(record.classification == "documentation")
        project.logs += int(record.classification == "log")
        if not record.readable:
            project.parse_errors += 1

    def _relationship_edges(self, project: str, root: Path, path: Path, relative: Path, record: AtlasFile) -> None:
        source_id = f"{project}:{relative.as_posix()}"
        if "manifest" in record.indicators:
            self.edges.append({"source": f"project:{project}", "target": source_id, "kind": "manifest"})
        for indicator in record.indicators:
            if indicator != "manifest":
                self.edges.append({"source": source_id, "target": f"index:{indicator}", "kind": "classified_as"})
        if path.suffix.lower() == ".py" and record.readable:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                note_failure("project_atlas:L161", exc)
                return
            for node in ast.walk(tree):
                imported = node.names[0].name if isinstance(node, ast.Import) and node.names else node.module if isinstance(node, ast.ImportFrom) else None
                if imported:
                    self.edges.append({"source": source_id, "target": f"import:{imported}", "kind": "imports"})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "excluded_parts": sorted(self.excluded_parts),
            "projects": [asdict(project) for project in self.projects],
            "files": [asdict(record) for record in self.files],
            "edges": self.edges,
            "unavailable": self.unavailable,
            "coverage": {
                "requested_projects": len(self.roots),
                "existing_projects": sum(project.exists for project in self.projects),
                "files": len(self.files),
                "readable_files": sum(record.readable for record in self.files),
                "unavailable_projects": len(self.unavailable),
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_csv(self) -> str:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("project", "path", "suffix", "classification", "size", "sha256", "readable", "indicators"))
        for record in self.files:
            writer.writerow((record.project, record.path, record.suffix, record.classification, record.size, record.sha256 or "", record.readable, ";".join(record.indicators)))
        return stream.getvalue()

    def to_html(self, title: str = "Project Provenance Atlas") -> str:
        rows = "".join(f"<tr><td>{html.escape(p.project)}</td><td>{html.escape(p.root)}</td><td>{p.files}</td><td>{p.source_files}</td><td>{p.tests}</td><td>{p.cli_candidates}</td><td>{'yes' if p.exists else 'no'}</td></tr>" for p in self.projects)
        unavailable = "<br>".join(f"{html.escape(item['project'])}: {html.escape(item['reason'])}" for item in self.unavailable) or "None"
        payload = html.escape(self.to_json())
        return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>{html.escape(title)}</title><style>body{{font:14px system-ui;background:#0f172a;color:#e2e8f0;margin:2rem}}h1,h2{{color:#67e8f9}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #334155;padding:.5rem;text-align:left}}th{{color:#86efac}}pre{{white-space:pre-wrap;background:#111827;padding:1rem;max-height:30rem;overflow:auto}}</style></head><body><h1>{html.escape(title)}</h1><h2>Projects</h2><table><tr><th>Project</th><th>Root</th><th>Files</th><th>Source</th><th>Tests</th><th>CLI candidates</th><th>Exists</th></tr>{rows}</table><h2>Unavailable or unresolved</h2><p>{unavailable}</p><h2>Raw atlas</h2><details><summary>Show JSON</summary><pre>{payload}</pre></details></body></html>"


def scan_projects(roots: dict[str, Path | str], max_files_per_project: int = 100000) -> ProjectAtlas:
    """Scan supplied project roots with a deterministic per-root file limit."""
    return ProjectAtlas(roots, max_files_per_project=max_files_per_project).scan()
