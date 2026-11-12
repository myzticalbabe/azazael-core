#!/usr/bin/env python3
"""
Auto Documentation Generator

Generates API documentation from docstrings.
"""

import ast
import json
from pathlib import Path
from datetime import datetime
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class DocumentationGenerator:
    """Auto-generates documentation from source code."""

    def __init__(self, source_dir: str, output_dir: str):
        self.source_dir = Path(source_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract_docstrings(self, filepath: Path) -> dict[str, Any]:
        """Extract module, class, and function docstrings."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            tree = ast.parse(content)
        except (SyntaxError, FileNotFoundError) as exc:
            note_failure("AUTO_DOC_GENERATOR:L29", exc)
            return {"error": "Could not parse file"}

        docstrings = {
            "file": str(filepath),
            "module": ast.get_docstring(tree),
            "classes": [],
            "functions": [],
            "imports": self._extract_imports(content),
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_doc = {
                    "name": node.name,
                    "docstring": ast.get_docstring(node),
                    "methods": [],
                    "line_number": node.lineno,
                }
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        method_doc = {
                            "name": item.name,
                            "docstring": ast.get_docstring(item),
                            "line_number": item.lineno,
                            "args": self._get_args(item),
                        }
                        class_doc["methods"].append(method_doc)
                docstrings["classes"].append(class_doc)

            elif isinstance(node, ast.FunctionDef):
                func_doc = {
                    "name": node.name,
                    "docstring": ast.get_docstring(node),
                    "line_number": node.lineno,
                    "args": self._get_args(node),
                }
                docstrings["functions"].append(func_doc)

        return docstrings

    def _extract_imports(self, content: str) -> list[str]:
        """Extract import statements."""
        imports = []
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("import ") or line.startswith("from "):
                imports.append(line)
        return imports[:10]  # Limit to first 10 imports

    def _get_args(self, node: ast.FunctionDef) -> list[dict[str, str]]:
        """Get function arguments."""
        args = []
        for arg in node.args.args:
            args.append(
                {
                    "name": arg.arg,
                    "type": "unknown",  # Would need type annotations
                }
            )
        return args

    def generate_markdown(self, docstrings: dict[str, Any]) -> str:
        """Generate Markdown documentation."""
        lines = []
        lines.append("# Module Documentation")
        lines.append(f"\n**File:** `{docstrings['file']}`")
        lines.append("\n## Module Docstring\n")

        if docstrings.get("module"):
            lines.append(docstrings["module"])
        else:
            lines.append("*No module docstring found.*")

        if docstrings.get("classes"):
            lines.append("\n## Classes\n")
            for cls in docstrings["classes"]:
                lines.append(f"### {cls['name']}")
                lines.append(f"\n*Line:* {cls['line_number']}\n")
                if cls["docstring"]:
                    lines.append(cls["docstring"])
                else:
                    lines.append("*No class docstring found.*")

                if cls["methods"]:
                    lines.append("\n#### Methods\n")
                    for method in cls["methods"]:
                        lines.append(
                            f"- `{method['name']}()` - Line {method['line_number']}"
                        )

        if docstrings.get("functions"):
            lines.append("\n## Functions\n")
            for func in docstrings["functions"]:
                lines.append(f"### {func['name']}")
                lines.append(f"\n*Line:* {func['line_number']}\n")
                if func["docstring"]:
                    lines.append(func["docstring"])
                else:
                    lines.append("*No function docstring found.*")

        return "\n".join(lines)

    def generate_json(self, docstrings: dict[str, Any]) -> str:
        """Generate JSON documentation."""
        return json.dumps(docstrings, indent=2)

    def run(self) -> dict[str, Any]:
        """Execute documentation generation."""
        results = {
            "generated": datetime.now().isoformat(),
            "source_dir": str(self.source_dir),
            "files": [],
            "skipped": [],
        }

        for pyfile in self.source_dir.rglob("*.py"):
            if "__pycache__" in str(pyfile) or ".venv" in str(pyfile):
                continue

            docstrings = self.extract_docstrings(pyfile)

            # Skip files that couldn't be parsed
            if "error" in docstrings:
                results["skipped"].append({
                    "source": str(pyfile),
                    "reason": docstrings["error"],
                })
                continue

            output_file = self.output_dir / (pyfile.stem + ".md")

            markdown = self.generate_markdown(docstrings)
            output_file.write_text(markdown, encoding="utf-8")

            results["files"].append({"source": str(pyfile), "doc": str(output_file)})

        return results


if __name__ == "__main__":
    import sys
    try:
        from CORE.paths import get_desktop_dir
    except ImportError as exc:
        note_failure("AUTO_DOC_GENERATOR:L173", exc)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from CORE.paths import get_desktop_dir

    desktop = get_desktop_dir()
    generator = DocumentationGenerator(
        str(desktop),
        str(desktop / "CODEX" / "DOCUMENTATION" / "API")
    )
    results = generator.run()
    print(f"Generated documentation for {len(results['files'])} files")
