#!/usr/bin/env python3
"""Real import graph for the repo (2026-09-12).

Replaces the audit's name-substring coverage check. The old testmap asked
"does the string `providers` appear anywhere in a test file?" — which is true
of a comment, a docstring, a variable, or an unrelated word, and false for a
module that is imported under an alias or exercised through a subprocess
(`tests/test_scanner.py` runs `src/CORE/compliance/scanner.py` as a child
process, so it names the file but never imports the module).

That is how `src/CORE/inference/providers.py` looked covered while its
mutation score was 0.1 and `src/CORE/compliance/scanner.py` looked covered at
0.067: the audit paired each module with a suite that never touches it.

This module answers the question properly, by parsing:

  direct[relpath]    the project modules a file actually imports (AST)
  path_refs[relpath] project files named by a `.py` string literal (catches
                     subprocess-style tests and loader scripts)
  importers(relpath) who imports this module
  test_closure()     every module reachable from a test module, transitively
  coverage()         src modules reached by tests vs not

Module naming: files under `src/` are importable as their path from `src`
(`src/CORE/WORMS/expansion/evolution.py` -> `CORE.WORMS.expansion.evolution`),
because `src` is on sys.path; `scripts/` and `tests/` files are importable by
basename because the suites put those directories on sys.path. A file can
therefore be reached by more than one name and all aliases are kept.

stdlib only, cached per process (a full parse of ~840 files is ~1s).
"""
from __future__ import annotations

import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_ROOTS = ("src", "scripts", "tests")
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             "node_modules", ".git", "archive", "archived"}

_PATH_LITERAL = re.compile(r"[\w./\\-]+\.py$")

# A path is only a REFERENCE when something actually loads or runs it. A bare
# string is a mention: `assertEqual(mod.is_live("src/tmp_verify.py"), ...)`
# in a test names that file without touching it, and counting mentions was the
# same false-positive class as the substring testmap this module replaces
# (2026-09-12: a test file's own string literal made a dead module look live).
_RUN_FUNCS = {"run", "Popen", "call", "check_call", "check_output", "system",
              "popen", "execv", "execve", "execl", "spawnv", "spawnl",
              "run_path", "run_module", "import_module", "__import__",
              "exec", "eval", "load_module", "spec_from_file_location"}

_CACHE: dict[str, "ImportMap"] = {}


def _func_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


class _Collector(ast.NodeVisitor):
    """Collect imports, run/exec path literals, and runnability."""

    def __init__(self) -> None:
        self.imports: list[str] = []
        self.path_strings: list[str] = []
        self.has_main = False

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.imports.append(a.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and not node.level:
            self.imports.append(node.module)
            for a in node.names:
                if a.name != "*":
                    self.imports.append(node.module + "." + a.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _func_name(node.func) in _RUN_FUNCS:
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Constant)
                        and isinstance(sub.value, str)):
                    self.path_strings.append(sub.value)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        t = node.test
        if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                and t.left.id == "__name__"):
            self.has_main = True
        self.generic_visit(node)


class ImportMap:
    """The parsed import graph for one checkout."""

    def __init__(self, root: str = ROOT) -> None:
        self.root = root
        self.files: set[str] = set()
        self.parse_errors: dict[str, str] = {}
        self.has_main: set[str] = set()
        self.by_full: dict[str, str] = {}
        self.by_base: dict[str, set[str]] = {}
        self.direct: dict[str, set[str]] = {}
        self.path_refs: dict[str, set[str]] = {}
        self._build()

    # -- construction -----------------------------------------------------
    def _rel(self, path: str) -> str:
        return os.path.relpath(path, self.root).replace("\\", "/")

    def _walk(self) -> None:
        for top in SCAN_ROOTS:
            base = os.path.join(self.root, top)
            if not os.path.isdir(base):
                continue
            for dp, dn, fn in os.walk(base):
                dn[:] = [d for d in dn if d not in SKIP_DIRS
                         and not d.startswith(".")]
                for f in fn:
                    if f.endswith(".py"):
                        self.files.add(self._rel(os.path.join(dp, f)))

    def _names(self, rel: str) -> set[str]:
        parts = rel.split("/")
        base = parts[-1][:-3]
        names = {base}
        if parts[0] == "src":
            names.add(".".join(parts[1:-1] + [base]))
        return {n for n in names if n}

    def _build(self) -> None:
        self._walk()
        for rel in sorted(self.files):
            base = os.path.basename(rel)[:-3]
            for name in self._names(rel):
                if "." in name:
                    self.by_full.setdefault(name, rel)
                self.by_base.setdefault(name, set()).add(rel)

        for rel in sorted(self.files):
            path = os.path.join(self.root, rel)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    tree = ast.parse(f.read())
            except SyntaxError as e:
                self.parse_errors[rel] = "line %s: %s" % (e.lineno, e.msg)
                self.direct.setdefault(rel, set())
                continue
            except Exception as e:  # noqa: BLE001
                self.parse_errors[rel] = str(e)[:120]
                self.direct.setdefault(rel, set())
                continue
            col = _Collector()
            col.visit(tree)
            if col.has_main:
                self.has_main.add(rel)
            targets: set[str] = set()
            for dotted in col.imports:
                targets |= self.resolve(dotted)
            self.direct[rel] = targets - {rel}
            for s in col.path_strings:
                ref = self.resolve_path_literal(s, rel)
                if ref:
                    self.path_refs.setdefault(ref, set()).add(rel)

    # -- resolution -------------------------------------------------------
    def resolve(self, dotted: str) -> set[str]:
        """Map an import string to project file(s). Unknown names -> empty."""
        out: set[str] = set()
        if dotted in self.by_full:
            out.add(self.by_full[dotted])
        parts = dotted.split(".")
        # from a.b import c may also be the submodule a.b.c
        for i in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in self.by_full:
                out.add(self.by_full[candidate])
                break
        out |= self.by_base.get(parts[-1], set())
        return out

    def resolve_path_literal(self, literal: str, referrer: str) -> str | None:
        """`.../scanner.py` in a string -> the project file it names."""
        if len(literal) > 400 or not _PATH_LITERAL.search(literal):
            return None
        cand = literal.replace("\\", "/").lstrip("./")
        if cand in self.files:
            return cand
        # tolerate bare paths relative to a scan root, e.g. "CORE/x.py"
        for top in SCAN_ROOTS:
            joined = "%s/%s" % (top, cand)
            if joined in self.files:
                return joined
        base = os.path.basename(cand)
        same_dir = [f for f in self.files
                    if os.path.basename(f) == base
                    and f.rsplit("/", 1)[0] == referrer.rsplit("/", 1)[0]]
        if len(same_dir) == 1:
            return same_dir[0]
        return None

    # -- queries ----------------------------------------------------------
    def importers(self, rel: str) -> set[str]:
        """Files that import `rel` directly (excluding itself)."""
        return {f for f, tgts in self.direct.items() if rel in tgts and f != rel}

    def referenced_by(self, rel: str) -> set[str]:
        """Files that name `rel` by path literal (subprocess / loader use)."""
        return set(self.path_refs.get(rel, set()))

    def is_test(self, rel: str) -> bool:
        return rel.startswith("tests/") or os.path.basename(rel).startswith("test_")

    def is_src(self, rel: str) -> bool:
        base = os.path.basename(rel)[:-3]
        return (rel.startswith("src/") and not base.startswith(("test_", "_"))
                and "/tests/" not in rel)

    def src_modules(self) -> list[str]:
        return sorted(f for f in self.files if self.is_src(f))

    def _closure(self, seeds) -> set[str]:
        seen: set[str] = set()
        stack = [s for s in seeds if s in self.direct]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in self.direct.get(cur, ()):  # type: ignore[arg-type]
                if nxt not in seen:
                    stack.append(nxt)
        return seen

    def test_closure(self) -> set[str]:
        """Every project module reachable from a test module, transitively."""
        tests = [f for f in self.files if self.is_test(f)]
        return self._closure(tests)

    def coverage(self) -> dict:
        """Which src modules the tests actually reach, and by what evidence."""
        closure = self.test_closure()
        # A test that shells out to a file names it but never imports it.
        direct_refs: dict[str, set[str]] = {}
        for ref, referrers in self.path_refs.items():
            if any(self.is_test(r) for r in referrers):
                direct_refs.setdefault(ref, set()).update(referrers)
        covered = {f for f in self.src_modules()
                   if f in closure or f in direct_refs}
        uncovered = [f for f in self.src_modules() if f not in covered]
        return {
            "src_modules": len(self.src_modules()),
            "covered": len(covered),
            "uncovered": uncovered,
            "via_import": sorted(f for f in covered if f in closure),
            "via_path_literal": sorted(f for f in covered
                                       if f not in closure and f in direct_refs),
            # every file named by a run/exec call, whether or not a test also
            # imports it - the raw evidence behind the path channel
            "path_referenced": sorted(direct_refs),
            "parse_errors": dict(self.parse_errors),
        }

    def is_live(self, rel: str) -> tuple[bool, str]:
        """Is a module reachable from anything at all?

        Returns (live, why). An orphan has no importer, no path-literal
        reference and is not a runnable module - that is the only category
        that is safe to consider deleting.
        """
        n = len(self.importers(rel))
        if n:
            return True, "imported by %d" % n
        if self.referenced_by(rel):
            return True, "referenced by path"
        if rel.startswith("scripts/") and rel in self.has_main:
            return True, "cli entry point"
        return False, "no importer, no reference, not runnable"


def get_map(root: str = ROOT) -> ImportMap:
    """Process-cached ImportMap (a full parse is ~1s)."""
    key = os.path.abspath(root)
    if key not in _CACHE:
        _CACHE[key] = ImportMap(key)
    return _CACHE[key]


if __name__ == "__main__":  # pragma: no cover - manual inspection
    import json as _json
    m = get_map()
    cov = m.coverage()
    print("files parsed:      %d" % len(m.files))
    print("parse errors:      %d" % len(cov["parse_errors"]))
    print("src modules:       %d" % cov["src_modules"])
    print("covered by import: %d" % len(cov["via_import"]))
    print("covered by path:   %d" % len(cov["via_path_literal"]))
    print("uncovered:         %d" % len(cov["uncovered"]))
    if "--json" in os.sys.argv:
        print(_json.dumps(cov, indent=2)[:4000])
