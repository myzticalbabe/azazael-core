"""dep_gap_315t.py — what does 3.15t NOT have that the tree actually imports?

Answers "install anything needed for all of our extracted techniques" with a
measurement instead of a guess: parse every top-level import in the tree, drop
the standard library, and ask the running interpreter whether it can find the
rest. Run it WITH the 3.15t interpreter so `find_spec` is the 3.15t answer.

    C:/Azazael/.venvs/py315t/Scripts/python.exe azazael/tools/dep_gap_315t.py

Output: three sets, because they are three different problems.
  MISSING   — imported at module level, absent here. The tree cannot run that
              module on this interpreter.
  OPTIONAL  — imported inside a try/except ImportError or a function body.
              Absent is survivable, but a guarded import that always fails is a
              capability silently switched off; each is listed with site:line.
  PRESENT   — resolvable here.

--json for the machine-readable form. Nothing is installed; this is read-only.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Only directories this project OWNS. `archived/` and `nuitka_build_backup/`
# contain vendored third-party trees (onnxruntime, jinja2, clcache) whose imports
# are not Azazael's dependencies; counting them buried the real gaps under ~50
# vendored names on the first run.
SCAN_DIRS = ("src", "tools", "scripts", "tests", "packages", "config")
SKIP_PARTS = {"archive", "archived", "archived_unified", "nuitka_build_backup",
              "__pycache__", ".venvs", ".build", "node_modules", "graphify-out",
              ".git", "logs", "state", "skills", "dashboard"}


def _own_module_names() -> set[str]:
    """Names that resolve to THIS repo's own packages, not to PyPI.

    `scripts`, `src`, `version`, `terence` all appear as bare imports inside the
    tree and all resolve to a local file or package -- reporting them as missing
    third-party dependencies is a false positive of the scanner, not a finding.
    """
    names = {"CORE", "azazael", "azazael_core", "ORCHESTRATION", "HYDRA", "AOA",
             "META", "UTILITIES", "CODEX", "INTERPRETERS", "packages"}
    for entry in ROOT.iterdir():
        if entry.is_dir():
            names.add(entry.name)
        elif entry.suffix == ".py":
            names.add(entry.stem)
    for rel in SCAN_DIRS:
        base = ROOT / rel
        if base.is_dir():
            for entry in base.iterdir():
                if entry.is_dir():
                    names.add(entry.name)
                elif entry.suffix == ".py":
                    names.add(entry.stem)
    return names


def _stdlib() -> set[str]:
    names = set(getattr(sys, "stdlib_module_names", ()))
    names |= {"__future__", "typing_extensions"}
    return names


def _iter_files() -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for rel in SCAN_DIRS:
        base = ROOT / rel
        if rel == "config" or not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            if path in seen:
                continue
            seen.add(path)
            out.append(path)
    return out


def _top_module(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        first = node.names[0].name if node.names else ""
        return first.split(".")[0] or None
    if isinstance(node, ast.ImportFrom):
        if node.level:          # relative import — this package's own module
            return None
        return (node.module or "").split(".")[0] or None
    return None


def _guarded(tree: ast.AST) -> set[int]:
    """Line numbers of imports that sit in a try/except ImportError or a def."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        body: list[ast.stmt] = []
        if isinstance(node, ast.Try) and any(
                isinstance(h.type, ast.Name) and h.type.id == "ImportError"
                or isinstance(h.type, ast.Tuple) for h in node.handlers):
            body = node.body
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
        for child in body:
            for inner in ast.walk(child):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    guarded.add(inner.lineno)
    return guarded


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    stdlib = _stdlib()
    ours = _own_module_names()
    hard: dict[str, list[str]] = {}
    soft: dict[str, list[str]] = {}
    files = _iter_files()

    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        guarded = _guarded(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            name = _top_module(node)
            if not name or name in stdlib or name in ours or name.startswith("_"):
                continue
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            bucket = soft if node.lineno in guarded else hard
            bucket.setdefault(name, [])
            if len(bucket[name]) < 6:
                bucket[name].append(where)

    def resolvable(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError) as e:
            print("dep_gap_315t:resolvable failed: %s" % type(e).__name__, file=sys.stderr)
            return False

    present = {n: v for n, v in hard.items() if resolvable(n)}
    missing = {n: v for n, v in hard.items() if not resolvable(n)}
    optional = {n: v for n, v in soft.items() if not resolvable(n)}

    report = {
        "interpreter": sys.executable,
        "version": sys.version.split()[0],
        "files_scanned": len(files),
        "distinct_third_party": len(set(hard) | set(soft)),
        "present": sorted(present),
        "MISSING": {n: v for n, v in sorted(missing.items())},
        "OPTIONAL_absent": {n: v for n, v in sorted(optional.items())},
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"interpreter          : {report['interpreter']}")
        print(f"python               : {report['version']}")
        print(f"files scanned        : {report['files_scanned']}")
        print(f"distinct third-party : {report['distinct_third_party']}")
        print(f"\nMISSING (module-level import, not importable here) — {len(missing)}")
        for name, sites in sorted(missing.items()):
            print(f"  {name:28s} {sites[0]}" + (f"  (+{len(sites) - 1} more)" if len(sites) > 1 else ""))
        print(f"\nOPTIONAL absent (guarded/function-level, capability switched off) — {len(optional)}")
        for name, sites in sorted(optional.items()):
            print(f"  {name:28s} {sites[0]}" + (f"  (+{len(sites) - 1} more)" if len(sites) > 1 else ""))
        print(f"\nPRESENT — {len(present)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
