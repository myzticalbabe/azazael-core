# coding: utf-8
"""
Validate Docs

Module for scripts functionality.

Functions:
    - main()
    - validate_file()
    - validate_all()
    - auto_fix_header()
    - generate_report()
    - generate_from_module()
    - generate()
    - add_breadcrumb()
    - generate_for_index()
    - extract_version_requirements()

Classes:
    - DocumentationValidator
    - APIDocGenerator
    - DocumentationSearchIndex
    - BreadcrumbGenerator
    - VersionTracker

Usage:
    >>> import validate_docs
    >>> validate_docs.main()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
Documentation validation with Q-Learning feedback.
Validates documentation against the consolidated DOCUMENTATION_PROTOCOL.md.

Features:
- YAML header validation
- Required section checks
- Placeholder link detection
- Naming convention enforcement
- External link checking
- Code example validation
- Auto-generate API docs from docstrings
- Search index generation
"""

import re
import subprocess
import sys
import json
from pathlib import Path
# from typing import Dict, List, Optional  # removed — all names have native equivalents
from datetime import datetime, timedelta
import tempfile
import os
from typing import List
import argparse
import inspect

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class DocumentationValidator:
    """Validates documentation against protocol."""

    REQUIRED_HEADERS = ["title", "version", "last_updated", "status"]
    REQUIRED_SECTIONS = ["Overview", "Usage"]
    VALID_STATUSES = ["DRAFT", "REVIEW", "APPROVED", "DEPRECATED"]
    EXTERNAL_TIMEOUT = 10

    def __init__(self, check_external: bool = True, check_code: bool = False):
        self.issues = []
        self.check_external = check_external
        self.check_code = check_code
        self.external_link_results = {}

    def validate_file(self, path: Path) -> dict:
        """Validate a single documentation file."""
        content = path.read_text(encoding="utf-8")

        issues = {
            "header_issues": self._check_header(content),
            "section_issues": self._check_sections(content),
            "link_issues": self._check_links(content),
            "naming_issues": self._check_naming(path),
            "external_link_issues": self._check_external_links(content)
            if self.check_external
            else [],
            "code_issues": self._validate_code_examples(content)
            if self.check_code
            else [],
        }

        total_score = self._calculate_score(issues)

        return {
            "file": str(path),
            "score": total_score,
            "issues": issues,
            "recommended_action": self._get_action(issues),
        }

    def validate_all(self, docs_dir: Path) -> list[dict]:
        """Validate all documentation files."""
        results = []
        for md_file in docs_dir.rglob("*.md"):
            if "node_modules" not in str(md_file):
                result = self.validate_file(md_file)
                results.append(result)
        return results

    def _check_header(self, content: str) -> list[str]:
        """Check YAML header is present and complete."""
        issues = []

        if not content.startswith("---"):
            issues.append("Missing YAML header")
            return issues

        header_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not header_match:
            issues.append("Cannot parse YAML header")
            return issues

        header_text = header_match.group(1)
        for field in self.REQUIRED_HEADERS:
            if f"{field}:" not in header_text:
                issues.append(f"Missing field: {field}")

        status_match = re.search(r"status:\s*(\w+)", header_text)
        if status_match:
            if status_match.group(1) not in self.VALID_STATUSES:
                issues.append(f"Invalid status: {status_match.group(1)}")

        version_match = re.search(r"version:\s*([\d.]+)", header_text)
        if version_match:
            version = version_match.group(1)
            if not re.match(r"^\d+\.\d+\.\d+$", version):
                issues.append(f"Invalid version format: {version}")

        last_updated_match = re.search(
            r"last_updated:\s*(\d{4}-\d{2}-\d{2})", header_text
        )
        if last_updated_match:
            date_str = last_updated_match.group(1)
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
                if date < datetime.now() - timedelta(days=365):
                    issues.append(f"Document is over 1 year old ({date_str})")
            except ValueError as exc:
                note_failure("validate_docs:L148", exc)

        return issues

    def _check_sections(self, content: str) -> list[str]:
        """Check required sections exist."""
        issues = []
        for section in self.REQUIRED_SECTIONS:
            if f"## {section}" not in content and f"# {section}" not in content:
                issues.append(f"Missing section: {section}")
        return issues

    def _check_links(self, content: str) -> list[str]:
        """Check for broken or placeholder links."""
        issues = []

        placeholder_pattern = r"\[.*?\]\(\s*#\s*\)"
        matches = re.findall(placeholder_pattern, content)
        if matches:
            issues.append(f"Found {len(matches)} placeholder link(s): {matches}")

        empty_link_pattern = r"\[.*?\]\(\s*\)"
        empty_matches = re.findall(empty_link_pattern, content)
        if empty_matches:
            issues.append(f"Found {len(empty_matches)} empty link(s)")

        dead_end_pattern = r"\[([^\]]+)\]\(([^)]+)\)"

        for match in re.finditer(dead_end_pattern, content):
            match.group(1)
            link_url = match.group(2)

            if link_url.startswith("#"):
                anchor = link_url[1:]
                if anchor and anchor not in content.lower():
                    issues.append(f"Dangling internal anchor: #{anchor}")

        return issues

    def _check_external_links(self, content: str) -> list[str]:
        """Check external URLs are valid."""
        issues = []
        url_pattern = r"\[([^\]]+)\]\((https?://[^\s\)]+)\)"

        for match in re.finditer(url_pattern, content):
            match.group(1)
            url = match.group(2)

            if url in self.external_link_results:
                if not self.external_link_results[url]:
                    issues.append(f"Broken external link: {url}")
                continue

            try:
                result = subprocess.run(
                    [
                        "curl",
                        "-s",
                        "-o",
                        "/dev/null",
                        "-w",
                        "%{http_code}",
                        "--max-time",
                        str(self.EXTERNAL_TIMEOUT),
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self.EXTERNAL_TIMEOUT + 2, encoding="utf-8", errors="replace"
                )
                http_code = result.stdout.strip()
                if http_code not in ["200", "301", "302"]:
                    issues.append(f"External link returns HTTP {http_code}: {url}")
                    self.external_link_results[url] = False
                else:
                    self.external_link_results[url] = True
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as exc:
                note_failure("validate_docs:L225", exc)
                issues.append(f"Could not verify external link: {url}")
                self.external_link_results[url] = None

        return issues

    def _validate_code_examples(self, content: str) -> list[str]:
        """Validate code examples by executing them."""
        issues = []
        code_blocks = re.findall(r"```python\n(.*?)```", content, re.DOTALL)

        for i, code in enumerate(code_blocks):
            if "import" in code and "sys.path" not in code:
                continue

            temp_file = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", delete=False
                ) as f:
                    f.write(code)
                    temp_file = f.name

                result = subprocess.run(
                    [sys.executable, temp_file],
                    capture_output=True,
                    text=True,
                    timeout=30, encoding="utf-8", errors="replace"
                )

                if result.returncode != 0:
                    error_lines = result.stderr.strip().split("\n")[:3]
                    issues.append(f"Code block {i + 1} error: {'; '.join(error_lines)}")

            except subprocess.TimeoutExpired as exc:
                note_failure("validate_docs:L259", exc)
                issues.append(f"Code block {i + 1} timed out (>30s)")
            except Exception as e:
                note_failure("validate_docs:L261", e)
                issues.append(f"Code block {i + 1} validation error: {str(e)}")
            finally:
                if temp_file and os.path.exists(temp_file):
                    os.unlink(temp_file)

        return issues

    def _check_naming(self, path: Path) -> list[str]:
        """Check file naming conventions."""
        issues = []
        name = path.name

        if name.endswith(".md"):
            if "_API.md" in name or "_QUICKREF.md" in name or "_TUTORIAL.md" in name:
                return issues

            if name in [
                "README.md",
                "INDEX.md",
                "DOCUMENTATION_PROTOCOL.md",
                "CHANGELOG.md",
            ]:
                return issues

            if name.startswith("_"):
                return issues

            issues.append(f"Non-standard naming: {name} (consider *_API.md)")

        return issues

    def _calculate_score(self, issues: dict) -> float:
        """Calculate documentation quality score (0-1)."""
        penalty = 0.0

        penalty += len(issues.get("header_issues", [])) * 0.15
        penalty += len(issues.get("section_issues", [])) * 0.10
        penalty += len(issues.get("link_issues", [])) * 0.20
        penalty += len(issues.get("naming_issues", [])) * 0.10
        penalty += len(issues.get("external_link_issues", [])) * 0.10
        penalty += len(issues.get("code_issues", [])) * 0.15

        return max(0.0, 1.0 - penalty)

    def _get_action(self, issues: dict) -> str:
        """Get recommended fix action."""
        if issues.get("code_issues"):
            return "fix_code"
        if issues.get("external_link_issues"):
            return "fix_external_links"
        if issues.get("link_issues"):
            return "fix_links"
        if issues.get("header_issues"):
            return "add_fields"
        if issues.get("section_issues"):
            return "add_sections"
        if issues.get("naming_issues"):
            return "rename"
        return "maintain"

    def auto_fix_header(
        self, content: str, title: str, authors: str = "Azazael System"
    ) -> str:
        """Auto-generate missing YAML header fields."""
        header = f"""---
title: {title}
version: 1.0.0
last_updated: {datetime.now().strftime("%Y-%m-%d")}
status: DRAFT
authors: {authors}
---

"""

        if content.startswith("---"):
            existing = content[: content.find("---", 4) + 3]
            return content.replace(existing, header.rstrip() + "\n\n", 1)

        return header + content

    def generate_report(
        self, results: list[dict], output_path: (Path | None) = None
    ) -> str:
        """Generate validation report."""
        report_lines = [
            "Documentation Validation Report",
            "=" * 50,
            "",
        ]

        passed = [r for r in results if r["score"] >= 0.8]
        failed = [r for r in results if r["score"] < 0.8]

        report_lines.append(f"Total Files: {len(results)}")
        report_lines.append(f"PASS: {len(passed)}")
        report_lines.append(f"FAIL: {len(failed)}")
        report_lines.append("")

        if failed:
            report_lines.append("Failed Files:")
            report_lines.append("-" * 30)
            for result in sorted(failed, key=lambda x: x["score"]):
                report_lines.append(f"\n[{result['score']:.2f}] {result['file']}")
                report_lines.append(f"  Action: {result['recommended_action']}")

                for category, issues in result["issues"].items():
                    if issues:
                        report_lines.append(f"  {category}: {issues}")

        report_lines.append("")
        report_lines.append("=" * 50)
        avg_score = sum(r["score"] for r in results) / len(results) if results else 0
        report_lines.append(f"Average Score: {avg_score:.2f}")

        report = "\n".join(report_lines)

        if output_path:
            output_path.write_text(report, encoding="utf-8")

        return report


class APIDocGenerator:
    """Auto-generate API documentation from Python source files."""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.docs_path = base_path / "docs"

    def generate_from_module(
        self, module_path: Path, output_name: (str | None) = None
    ) -> (Path | None):
        """Generate API docs from a Python module."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
        if spec is None:
            print(f"Warning: Could not load spec for {module_path}")
            return None
        module = importlib.util.module_from_spec(spec)

        try:
            if spec.loader is not None:
                spec.loader.exec_module(module)
        except Exception as e:
            print(f"Warning: Could not import {module_path}: {e}")
            return None

        doc_content = self._generate_doc_content(module, module_path)
        output_path = self.docs_path / (output_name or f"{module_path.stem}_API.md")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(doc_content, encoding="utf-8")

        return output_path

    def _generate_doc_content(self, module, module_path: Path) -> str:
        """Generate markdown documentation from module."""

        lines = [
            "---",
            f"title: {module_path.stem.replace('_', ' ').title()} API",
            "version: 1.0.0",
            f"last_updated: {datetime.now().strftime('%Y-%m-%d')}",
            "status: AUTO-GENERATED",
            "authors: APIDocGenerator",
            "---",
            "",
            f"# {module_path.stem}.py",
            "",
            f"**Location:** `{module_path}`",
            "",
            "## Overview",
            "",
            module.__doc__.strip()
            if module.__doc__
            else "*No module description available.*",
            "",
        ]

        if hasattr(module, "__all__"):
            items = module.__all__
        else:
            items = [name for name in dir(module) if not name.startswith("_")]

        classes = [
            name for name in items if isinstance(getattr(module, name, None), type)
        ]
        functions = [
            name
            for name in items
            if callable(getattr(module, name, None)) and name not in classes
        ]

        if classes:
            lines.append("## Classes\n")
            for class_name in classes:
                cls = getattr(module, class_name)
                lines.append(f"### {class_name}\n")
                if cls.__doc__:
                    lines.append(cls.__doc__.strip())
                    lines.append("")
                lines.append(f"**Location:** `{module_path}`\n")

        if functions:
            lines.append("## Functions\n")
            for func_name in functions:
                func = getattr(module, func_name)
                if callable(func) and not isinstance(func, type):
                    lines.append(f"### {func_name}()\n")
                    if func.__doc__:
                        lines.append("```python")
                        sig_match = re.search(
                            rf"def {func_name}\([^)]*\)", str(inspect.getsource(func))
                        )
                        if sig_match:
                            lines.append(sig_match.group(0) + ":")
                        lines.append("```")
                        lines.append(func.__doc__.strip())
                        lines.append("")
                    else:
                        lines.append("*No function description available.*\n")

        return "\n".join(lines)


class DocumentationSearchIndex:
    """Generate search index for documentation."""

    def __init__(self, docs_dir: Path):
        self.docs_dir = docs_dir
        self.index_path = docs_dir / "search.json"

    def generate(self) -> dict:
        """Generate search index from all markdown files."""
        index = {
            "version": "1.0",
            "generated": datetime.now().isoformat(),
            "documents": [],
        }

        for md_file in self.docs_dir.rglob("*.md"):
            if "node_modules" in str(md_file):
                continue

            content = md_file.read_text(encoding="utf-8")
            doc = {
                "path": str(md_file.relative_to(self.docs_dir.parent)),
                "title": self._extract_title(content),
                "headings": self._extract_headings(content),
                "code_snippets": self._extract_code_snippets(content),
                "links": self._extract_links(content),
            }
            index["documents"].append(doc)

        if self.index_path:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            self.index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

        return index

    def _extract_title(self, content: str) -> str:
        """Extract document title."""
        title_match = re.search(r"title:\s*(.+)", content)
        if title_match:
            return title_match.group(1).strip()

        h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if h1_match:
            return h1_match.group(1).strip()

        return "Untitled"

    def _extract_headings(self, content: str) -> list[dict]:
        """Extract all headings with levels."""
        headings = []
        for match in re.finditer(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE):
            headings.append(
                {
                    "level": len(match.group(1)),
                    "text": match.group(2).strip(),
                    "anchor": self._make_anchor(match.group(2)),
                }
            )
        return headings

    def _extract_code_snippets(self, content: str) -> list[str]:
        """Extract code snippets for indexing."""
        snippets = []
        for match in re.findall(r"```python\n(.*?)```", content, re.DOTALL):
            snippets.append(match.strip()[:500])
        return snippets

    def _extract_links(self, content: str) -> list[str]:
        """Extract all links."""
        links = []
        for match in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content):
            links.append({"text": match[0], "url": match[1]})
        return links

    def _make_anchor(self, text: str) -> str:
        """Create markdown anchor from text."""
        return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")


class BreadcrumbGenerator:
    """Add breadcrumb navigation to INDEX files."""

    @staticmethod
    def add_breadcrumb(
        content: str, doc_path: Path, base_readme: str = "../../README.md"
    ) -> str:
        """Add breadcrumb to document."""
        path_parts = doc_path.parts
        breadcrumbs = []

        for i, part in enumerate(path_parts):
            if part.endswith(".md"):
                continue
            if part in ["docs", "ml_platforms"]:
                breadcrumbs.append(
                    f"[{part.title()}]({'../' * (len(path_parts) - i - 2) or ''})"
                )
            elif i > 0 and path_parts[i - 1] == "docs":
                breadcrumbs.append(
                    f"[{part.title()}]({'../' * (len(path_parts) - i - 2) or ''}{part}/INDEX.md)"
                )

        breadcrumb_line = "> " + " > ".join(breadcrumbs) + "\n\n"

        if content.startswith("---"):
            header_end = content.find("---", 4) + 3
            return content[:header_end] + "\n" + breadcrumb_line + content[header_end:]

        return breadcrumb_line + content

    @staticmethod
    def generate_for_index(index_path: Path) -> str:
        """Generate breadcrumb for an INDEX.md file."""
        parts = index_path.parts
        breadcrumbs = ["[Azazael](../../README.md)"]

        for i, part in enumerate(parts):
            if part in ["docs", "ml_platforms"]:
                continue
            if part.endswith(".md"):
                title = (
                    part.replace("_", " ").replace("-", " ").replace(".md", "").title()
                )
                breadcrumbs.append(f"[{title}]")
            elif i > 0:
                prev = parts[i - 1]
                if prev in ["docs", "ml_platforms"]:
                    breadcrumbs.append(f"[{part.title()}](INDEX.md)")

        return "> " + " > ".join(breadcrumbs) + "\n"


class VersionTracker:
    """Track version requirements and breaking changes."""

    @staticmethod
    def extract_version_requirements(content: str) -> list[dict]:
        """Extract version requirements from content."""
        requirements = []

        pattern = r"<!--\s*Requires Azazael v?([\d.]+)\+?\s*-->"
        for match in re.finditer(pattern, content):
            requirements.append(
                {
                    "type": "version_requirement",
                    "version": match.group(1),
                    "raw": match.group(0),
                }
            )

        breaking_pattern = r"<!--\s*BREAKING:\s*(.+?)\s*-->"
        for match in re.finditer(breaking_pattern, content, re.IGNORECASE):
            requirements.append(
                {
                    "type": "breaking_change",
                    "description": match.group(1),
                    "raw": match.group(0),
                }
            )

        return requirements

    @staticmethod
    def check_version_compatibility(
        doc_version: str, current_version: str = "2.0.0"
    ) -> dict:
        """Check if document version is compatible with current."""
        doc_parts = [int(x) for x in doc_version.split(".")]
        curr_parts = [int(x) for x in current_version.split(".")]

        if doc_parts[0] < curr_parts[0]:
            return {"compatible": False, "reason": "Major version mismatch"}
        if doc_parts[1] < curr_parts[1]:
            return {
                "compatible": False,
                "reason": "Minor version mismatch - may have breaking changes",
            }
        return {"compatible": True, "reason": "Up to date"}


class ContributorsSection:
    """Add and manage contributors section."""

    @staticmethod
    def add_contributors(
        content: str, contributors: (list[dict] | None) = None
    ) -> str:
        """Add contributors section to document."""
        if contributors is None:
            contributors = []

        section = """
---

## Contributors

| Name | Contributions | Date |
|------|---------------|------|
"""
        for c in contributors:
            section += f"| {c.get('name', 'Anonymous')} | {c.get('contributions', 'Documentation')} | {c.get('date', datetime.now().strftime('%Y-%m-%d'))} |\n"

        return content + section

    @staticmethod
    def extract_contributors(content: str) -> list[dict]:
        """Extract contributors from document."""
        contributors = []

        pattern = r"\|\s*([^\|]+)\s*\|\s*([^\|]+)\s*\|\s*([^\|]+)\s*\|"
        in_contributors = False

        for line in content.split("\n"):
            if "## Contributors" in line:
                in_contributors = True
                continue
            if in_contributors and line.startswith("---"):
                break
            if in_contributors and "|" in line:
                match = re.match(pattern, line)
                if match:
                    contributors.append(
                        {
                            "name": match.group(1).strip(),
                            "contributions": match.group(2).strip(),
                            "date": match.group(3).strip(),
                        }
                    )

        return contributors


def main():
    """Main entry point with all validation options."""

    parser = argparse.ArgumentParser(
        description="Documentation validation and management"
    )
    parser.add_argument("path", nargs="?", default=None, help="Path to validate")
    parser.add_argument(
        "--check-external", action="store_true", help="Check external links"
    )
    parser.add_argument(
        "--check-code", action="store_true", help="Validate code examples"
    )
    parser.add_argument(
        "--generate-api", metavar="MODULE", help="Generate API docs from module"
    )
    parser.add_argument(
        "--generate-search", action="store_true", help="Generate search index"
    )
    parser.add_argument(
        "--add-breadcrumbs", action="store_true", help="Add breadcrumbs to INDEX files"
    )
    parser.add_argument("--report", metavar="FILE", help="Output report to file")

    args = parser.parse_args()

    if args.generate_api:
        generator = APIDocGenerator(Path(__file__).parent.parent)
        output = generator.generate_from_module(Path(args.generate_api))
        if output:
            print(f"Generated: {output}")
        return

    if args.generate_search:
        search = DocumentationSearchIndex(Path(__file__).parent.parent)
        index = search.generate()
        print(f"Generated search index with {len(index['documents'])} documents")
        return

    validator = DocumentationValidator(
        check_external=args.check_external,
        check_code=args.check_code,
    )

    if args.path:
        docs_path = Path(args.path)
    else:
        docs_path = Path(__file__).parent.parent

    if not docs_path.exists():
        print(f"Error: Path {docs_path} does not exist")
        sys.exit(1)

    results = validator.validate_all(docs_path)

    if args.report:
        output_path = Path(args.report)
        report = validator.generate_report(results, output_path)
        print(f"Report saved to: {output_path}")
    else:
        report = validator.generate_report(results)
        print(report)

    avg_score = sum(r["score"] for r in results) / len(results) if results else 0
    sys.exit(0 if avg_score >= 0.8 else 1)


if __name__ == "__main__":
    main()
