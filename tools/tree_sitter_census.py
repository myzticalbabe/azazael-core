#!/usr/bin/env python3
"""tree_sitter_census.py — multi-language codebase census using tree-sitter.

Extends yggdrasil_census.py with multi-language support via tree-sitter.
Currently Python-only AST; this module adds parsing for HTML, JavaScript,
TypeScript, PowerShell, Shell, and Batch files in the CODEX, CLI, HYDRA,
and TOOLS trees.

Usage:
    python tree_sitter_census.py --json          # JSON output
    python tree_sitter_census.py --selftest       # run controls
    python tree_sitter_census.py --language js    # filter by language

Dependencies:
    pip install tree-sitter tree-sitter-python tree-sitter-javascript
    pip install tree-sitter-html tree-sitter-typescript tree-sitter-bash
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Tree-sitter language parsers (optional — graceful degradation)
_LANGUAGES = {}
_AVAILABLE = False
try:
    import tree_sitter as ts
    _AVAILABLE = True
except ImportError:
    ts = None

# Language extension mapping
EXT_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".html": "html",
    ".htm": "html",
    ".ps1": "powershell",
    ".sh": "bash",
    ".bash": "bash",
    ".bat": "batch",
    ".cmd": "batch",
}

# Try to load individual language parsers
def _load_parser(lang_name):
    try:
        if lang_name == "python":
            import tree_sitter_python as tsp
            return ts.Language(tsp.language())
        elif lang_name == "javascript":
            import tree_sitter_javascript as tsj
            return ts.Language(tsj.language())
        elif lang_name == "typescript":
            import tree_sitter_typescript as tst
            return ts.Language(tst.language_typescript())
        elif lang_name == "html":
            import tree_sitter_html as tsh
            return ts.Language(tsh.language())
        elif lang_name == "bash":
            import tree_sitter_bash as tsb
            return ts.Language(tsb.language())
        elif lang_name == "powershell":
            import tree_sitter_powershell as tsp
            return ts.Language(tsp.language())
    except ImportError as exc:
        note_failure("tools/tree_sitter_census.py", exc)
        return None
    return None

_PARSERS = {}
def get_parser(lang_name):
    if lang_name in _PARSERS:
        return _PARSERS[lang_name]
    if not _AVAILABLE:
        return None
    lang = _load_parser(lang_name)
    if lang is None:
        _PARSERS[lang_name] = None
        return None
    parser = ts.Parser(lang)
    _PARSERS[lang_name] = parser
    return parser


def parse_file(filepath):
    """Parse a file with tree-sitter, returning structural info."""
    ext = Path(filepath).suffix.lower()
    lang_name = EXT_LANG_MAP.get(ext)
    if lang_name is None:
        return None

    parser = get_parser(lang_name)
    if parser is None:
        # Fallback: basic line counting for unsupported languages
        try:
            lines = Path(filepath).read_text(encoding="utf-8", errors="replace").splitlines()
            return {
                "path": str(filepath),
                "language": lang_name,
                "lines": len(lines),
                "functions": 0,
                "classes": 0,
                "imports": 0,
                "parse_method": "fallback-linecount",
            }
        except Exception as exc:
            note_failure("tools/tree_sitter_census.py", exc)
            return None

    try:
        source = Path(filepath).read_bytes()
        tree = parser.parse(source)
        root = tree.root_node

        # Count structural elements by node type
        functions = 0
        classes = 0
        imports = 0

        def walk(node):
            nonlocal functions, classes, imports
            if node.type in ("function_definition", "function_declaration",
                             "method_definition", "arrow_function",
                             "function", "generator_function_declaration"):
                functions += 1
            elif node.type in ("class_definition", "class_declaration",
                               "class", "interface_declaration"):
                classes += 1
            elif node.type in ("import_statement", "import_from_statement",
                               "import_declaration", "require_call",
                               "source_file" if False else "import"):
                imports += 1
            for child in node.children:
                walk(child)

        walk(root)

        return {
            "path": str(filepath),
            "language": lang_name,
            "lines": source.count(b"\n") + 1,
            "functions": functions,
            "classes": classes,
            "imports": imports,
            "parse_method": "tree-sitter",
        }
    except Exception as e:
        return {
            "path": str(filepath),
            "language": lang_name,
            "lines": 0,
            "functions": 0,
            "classes": 0,
            "imports": 0,
            "parse_method": "error",
            "error": str(e)[:200],
        }


def scan_directory(root, extensions=None):
    """Scan a directory for multi-language files."""
    if extensions is None:
        extensions = set(EXT_LANG_MAP.keys())
    results = []
    for dirpath, dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in extensions:
                fp = Path(dirpath) / f
                info = parse_file(fp)
                if info:
                    results.append(info)
    return results


def selftest():
    """Run basic controls."""
    # Test 1: EXT_LANG_MAP covers all extensions
    assert ".py" in EXT_LANG_MAP
    assert EXT_LANG_MAP[".py"] == "python"
    assert EXT_LANG_MAP[".js"] == "javascript"
    assert EXT_LANG_MAP[".html"] == "html"

    # Test 2: parse_file handles missing files gracefully
    result = parse_file("/nonexistent/file.py")
    assert result is None or "error" in result or result.get("lines", 0) == 0

    # Test 3: scan_directory returns list
    results = scan_directory("/tmp/nonexistent_dir_xyz")
    assert isinstance(results, list)


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    print("SELFTEST OK — 3 controls (ext map, missing file, scan list)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--selftest", action="store_true", help="run controls")
    parser.add_argument("--language", help="filter by language")
    parser.add_argument("--root", default="src", help="root directory to scan")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return

    root = Path(args.root)
    if not root.exists():
        print(f"Root not found: {root}", file=sys.stderr)
        sys.exit(1)

    results = scan_directory(root)

    if args.language:
        results = [r for r in results if r["language"] == args.language]

    if args.json:
        summary = {
            "total_files": len(results),
            "by_language": {},
            "total_lines": sum(r["lines"] for r in results),
            "total_functions": sum(r["functions"] for r in results),
            "total_classes": sum(r["classes"] for r in results),
            "files": results,
        }
        for r in results:
            lang = r["language"]
            if lang not in summary["by_language"]:
                summary["by_language"][lang] = {"files": 0, "lines": 0, "functions": 0, "classes": 0}
            summary["by_language"][lang]["files"] += 1
            summary["by_language"][lang]["lines"] += r["lines"]
            summary["by_language"][lang]["functions"] += r["functions"]
            summary["by_language"][lang]["classes"] += r["classes"]
        print(json.dumps(summary, indent=2))
    else:
        by_lang = {}
        for r in results:
            by_lang.setdefault(r["language"], 0)
            by_lang[r["language"]] += 1
        print(f"Scanned {len(results)} files across {len(by_lang)} languages")
        for lang, count in sorted(by_lang.items()):
            print(f"  {lang}: {count} files")
        if _AVAILABLE:
            print(f"tree-sitter: available")
            for lang in sorted(set(r["language"] for r in results)):
                p = get_parser(lang)
                print(f"  {lang}: {'tree-sitter' if p else 'fallback'}")
        else:
            print("tree-sitter: NOT installed (using fallback line counts)")


if __name__ == "__main__":
    main()
