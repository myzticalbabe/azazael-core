#!/usr/bin/env python3
# coding: utf-8
"""Check dict-key contracts: keys read but never written, and keys written
but never read.

Why this tool exists at all
--------------------------
The graph indexes functions, classes and files. It indexes *zero* string
literals -- verified: `model_values`, `violations`, `total_checks` and
`summary` are all absent from `graphify-out/graph.json`'s node labels, and
the graph has no string-literal node kind at all.

That is the right scope for a call graph, and it means an entire defect
class is outside its reach. `cd2_verifier._z3_verify` built a model and
threw it away because it called `model.eval(d)` on a `FuncDeclRef` instead
of `model.eval(d())`; the `except` turned the resulting exception into `{}`.
`model_values` was an empty dict in production for months. The function was
in the graph, called, and healthy-looking throughout: the graph could see
that it ran and could not see that it returned nothing.

Three defects of that shape were live in one file at once --
  * a key read but written only on an unreachable path
  * a key the docstring promised that no code path ever set
  * a summary counter that was structurally always 0

The check
---------
For each module, collect the dict keys it writes (subscript assignment /
update, `setdefault`, `.update`, and a returned dict literal) and the keys
it reads (subscript load, `.get`, `in`, and `keys()`). A key read but
never written *in the same module* is a candidate, and a key written but
never read is a candidate for the opposite reason.

The candidate set is deliberately wide. Deciding that a particular read is
supposed to come from a caller rather than from this module is a judgement
about intent, and this tool does not have that judgement. What it does have
is the ability to say "a human should look at this" -- which is strictly
more than the call graph could offer, because the call graph offered
nothing. Every finding is a question, not a verdict, and the report says so.

Where it will not help
----------------------
Keys that arrive from outside the module -- function parameters, config
files, JSON on disk, values returned by callees -- are invisible to a
within-module read/write census, and this repo has a great many of those.
The default therefore restricts the report to files where the *density* of
internal key use is high enough that an anomaly is meaningful, and it always
prints the counts it used so the threshold is inspectable rather than
magic.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIR_PARTS = {"__pycache__", ".mypy_cache", ".pytest_cache", "graphify-out",
                  ".venv", "venv", "node_modules", ".git", "output", "build",
                  "archive", "snapshots"}

#: `self.x` / bare `x` are attributes and locals, not dict keys. Only
#: subscripts on a non-Name value, or a Name known to be a dict, can be.
#: Being wrong here is the difference between a 12-line report and a
#: 4,000-line one, so the tool reports its own coverage next to its
#: findings and refuses to claim "OK" when coverage is too low to mean it.
DEFAULT_MIN_SITES = 6

#: A dict literal in a `return` is read by CALLERS, by definition, so
#: "written but never read here" is a statement about the whole program,
#: not about this module. Keeping it produced 7,119 of 13,634 findings --
#: over half the report, all of it structurally meaningless.
EXCLUDED_OWNERS = {"<returned>", "kwargs", "ort_inputs"}


def _dotted(node: ast.AST) -> str:
    """`a.b.c` -> 'a.b.c'; anything else -> ''. Subscript-aware."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return (base + "." + node.attr) if base else ""
    if isinstance(node, ast.Subscript):
        # `d["k"] = ...` names the dict, not the key; the key is the slice.
        return _dotted(node.value)
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _key_of(node: ast.Subscript) -> str | None:
    sl = node.slice
    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
        return sl.value
    return None


class ModuleKeys:
    def __init__(self) -> None:
        # keyed by the DICT's dotted name, not by the key alone
        self.written: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(list))
        self.read: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(list))
        self.dict_names: set[str] = set()
        self.sites = 0

    def total(self) -> int:
        return sum(len(v) for m in self.written.values() for v in m.values()) \
            + sum(len(v) for m in self.read.values() for v in m.values())


def _param_dict_names(fn: ast.AST) -> set[str]:
    """Dotted names that are dict PARAMETERS of this function.

    A parameter's keys are set by the caller. Counting them made the report
    mostly a list of method signatures -- `worm_result` is a parameter in
    four methods of NATURE_CLUSTERS.py and none of them write it.
    """
    out: set[str] = set()
    a = getattr(fn, "args", None)
    if a is None:
        return out
    for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
        out.add(arg.arg)
    return out


def _is_dict_name(tree: ast.AST) -> set[str]:
    """Dotted names this module constructs as dicts.

    A subscript on a name that is not here is a dict from somewhere else --
    a parameter, a return value, `self.state` set in another method. Its
    keys cannot be settled by looking at this module, so counting them is
    how the first version produced 23,027 candidates, none of them
    actionable. The boundary is the point of the tool: it only claims to
    know about dicts whose construction it can see.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        val: ast.AST | None = None
        tgts: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            tgts = list(node.targets)
            val = node.value
        elif isinstance(node, ast.AnnAssign):
            tgts = [node.target]
            val = node.value
        if val is None:
            continue
        if not isinstance(val, (ast.Dict, ast.DictComp)):
            continue
        for t in tgts:
            d = _dotted(t)
            if d:
                names.add(d)
    return names


def scan(tree: ast.AST) -> ModuleKeys:
    """Census of dict keys, per (function, dict) pair.

    Each function body is its own scope. A dict built in one method and read
    in another is the normal shape of this codebase -- `worm_result` alone
    crosses four methods in NATURE_CLUSTERS.py -- so pooling every method
    into one census both hid real asymmetries and flagged every legitimate
    boundary crossing.
    """
    mk = ModuleKeys()
    mk.dict_names = _is_dict_name(tree)

    def owned(dotted: str, params: frozenset) -> bool:
        if not dotted:
            return False
        if dotted in params:
            return False
        if dotted in mk.dict_names:
            return True
        # `self.stats["x"]` where `stats` is built as a dict somewhere.
        return dotted.split(".")[-1] in mk.dict_names

    def scan_scope(body: list[ast.AST], scope: str, params: frozenset) -> None:
        for node in body:
            for sub in ast.walk(node):
                # --- writes ---
                if isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    if isinstance(sub, ast.Assign):
                        tgts = list(sub.targets)
                    else:
                        tgts = [sub.target]
                    for t in tgts:
                        if isinstance(t, ast.Subscript):
                            d = _dotted(t.value)
                            k = _key_of(t)
                            if k and owned(d, params):
                                mk.written["%s|%s" % (scope, d)][k].append(
                                    sub.lineno)
                if isinstance(sub, ast.Call):
                    fn = _dotted(sub.func)
                    base = fn.rsplit(".", 1)[-1] if fn else ""
                    owner = fn.rsplit(".", 1)[0] if "." in fn else ""
                    if base == "setdefault" and len(sub.args) >= 2:
                        a0 = sub.args[0]
                        if (isinstance(a0, ast.Constant)
                                and isinstance(a0.value, str)
                                and owned(owner, params)):
                            mk.written["%s|%s" % (scope, owner)][a0.value].append(
                                sub.lineno)
                    if base == "update" and sub.args:
                        a0 = sub.args[0]
                        if isinstance(a0, ast.Dict) and owned(owner, params):
                            for k in a0.keys:
                                if (isinstance(k, ast.Constant)
                                        and isinstance(k.value, str)):
                                    mk.written["%s|%s" % (scope, owner)][
                                        k.value].append(sub.lineno)
                    if base == "get" and sub.args:
                        a0 = sub.args[0]
                        if (isinstance(a0, ast.Constant)
                                and isinstance(a0.value, str)
                                and owned(owner, params)):
                            mk.read["%s|%s" % (scope, owner)][a0.value].append(
                                sub.lineno)
                if (isinstance(sub, ast.Return)
                        and isinstance(sub.value, ast.Dict)):
                    for k in sub.value.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            mk.written["<returned>"].setdefault(
                                k.value, []).append(sub.lineno)
                # --- reads ---
                if (isinstance(sub, ast.Subscript)
                        and isinstance(sub.ctx, ast.Load)):
                    d = _dotted(sub.value)
                    k = _key_of(sub)
                    if k and owned(d, params):
                        mk.read["%s|%s" % (scope, d)][k].append(sub.lineno)

    mod_body: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan_scope(node.body, node.name, frozenset(_param_dict_names(node)))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scan_scope(sub.body, "%s.%s" % (node.name, sub.name),
                               frozenset(_param_dict_names(sub)))
        else:
            mod_body.append(node)
    scan_scope(mod_body, "<module>", frozenset())
    mk.sites = mk.total()
    return mk


def analyse(files: list[Path], min_sites: int) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    examined = 0
    skipped_low_coverage = 0
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        mk = scan(tree)
        if mk.sites < min_sites:
            skipped_low_coverage += 1
            continue
        examined += 1
        rel = f.relative_to(ROOT).as_posix() if f.is_relative_to(ROOT) \
            else f.as_posix()
        for d, keys in sorted(mk.read.items()):
            if d.split("|", 1)[-1] in EXCLUDED_OWNERS or \
                    d.split("|", 1)[0] in EXCLUDED_OWNERS:
                continue
            for key, lines in sorted(keys.items()):
                if key not in mk.written.get(d, {}):
                    findings.append({"kind": "read_never_written",
                                     "file": rel, "dict": d, "key": key,
                                     "line": lines[0], "read_at": lines[:6]})
        for d, keys in sorted(mk.written.items()):
            if d.split("|", 1)[-1] in EXCLUDED_OWNERS or \
                    d.split("|", 1)[0] in EXCLUDED_OWNERS:
                continue
            for key, lines in sorted(keys.items()):
                if key not in mk.read.get(d, {}):
                    findings.append({"kind": "written_never_read",
                                     "file": rel, "dict": d, "key": key,
                                     "line": lines[0],
                                     "written_at": lines[:6]})
    return {"examined": examined, "skipped_low_coverage": skipped_low_coverage,
            "findings": findings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report dict keys read but never written, and vice versa.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--scope", nargs="*", default=["src", "tests", "tools", "scripts"],
                    help="top-level trees to scan")
    ap.add_argument("--min-sites", type=int, default=DEFAULT_MIN_SITES)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv)

    root = Path(args.root)
    # Scanning the whole repo double-counts: this tree carries tracked,
    # unlinked duplicates of its own modules at the repo root
    # (NATURE_CLUSTERS.py, health_server.py -- both from 2026-07-27, both
    # with content that differs from the src/ copies) plus a whole
    # packages/ tree. A report that names "NATURE_CLUSTERS.py:706" when the
    # reader has src/CORE/WORMS/NATURE_CLUSTERS.py open is worse than no
    # report, because the line numbers point at the wrong file.
    files: list[Path] = []
    for sub in args.scope:
        base = root / sub
        if base.is_dir():
            files += [p for p in base.rglob("*.py")
                      if not (SKIP_DIR_PARTS & set(p.parts))]
    if not files:
        print("INCONCLUSIVE: no Python files under %s" % ", ".join(args.scope),
              file=sys.stderr)
        return 2
    rep = analyse(files, args.min_sites)

    if rep["examined"] == 0:
        print("INCONCLUSIVE: no module had enough dict-key sites to examine; "
              "a clean result here would mean nothing was read.",
              file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(rep, indent=2))
        return 1 if rep["findings"] else 0

    print("modules examined: %d (skipped %d below --min-sites=%d) | "
          "candidates: %d | scope: %s"
          % (rep["examined"], rep["skipped_low_coverage"],
             args.min_sites, len(rep["findings"]), " ".join(args.scope)))
    print("These are QUESTIONS, not defects: a key can legitimately be set by "
          "a caller,\na config file, or a callee. Only dicts this module "
          "itself builds are counted, and\nonly within a single function, so "
          "keys crossing either boundary are invisible.\n")
    for kind in ("read_never_written", "written_never_read"):
        rows = [f for f in rep["findings"] if f["kind"] == kind]
        if not rows:
            continue
        print("%s (%d):" % (kind.upper(), len(rows)))
        for r in rows[:args.limit]:
            scope, _, owner = r["dict"].partition("|")
            print("   %-22s %-26s %s:%s" % (r["key"], "%s.%s" % (scope, owner),
                                            r["file"], r["line"]))
        if len(rows) > args.limit:
            print("   ... %d more" % (len(rows) - args.limit))
        print()
    if rep["findings"]:
        return 1
    print("OK: no intra-module dict-key asymmetry in the examined files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
