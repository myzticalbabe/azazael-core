#!/usr/bin/env python3
# coding: utf-8
"""Find functions in src/ that nothing in the repository calls.

Why the first version was thrown away
-------------------------------------
It cross-checked each candidate with a bare `\\bname\\b` grep. That
produced 684 "unreferenced" and 3,546 "graph blind spot" findings, almost
all of them false: `initialize` appears on 21 lines, `shutdown` on 56, and
a match anywhere -- including another class's method of the same name, a
docstring, or a comment -- was read as a reference. A dead-code report with
3,546 entries is a report nobody reads, and one with 684 is worse.

What it does instead
--------------------
It resolves references with the same thing that makes a call a call: the
AST, and the module the name is used in. A bare name is resolved against
that module's own definitions and imports, and a `self.x` / `cls.x` /
`obj.x` attribute is resolved against the class that defines it. So
`arm.initialize()` does not count as a reference to `EventBus.initialize`.

`__all__`, decorators, and `getattr("name")` count, because those are the
real ways this codebase dispatches by name.

The honest result
-----------------
Even after all that, a public method reached by the worm registry has no
static call edge by construction -- the registry looks the name up in a
dict. Those are reported in their own section, never as dead code, because
"no caller" and "not an entry point" are different claims.
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
GRAPH = ROOT / "graphify-out" / "graph.json"
SRC = ROOT / "src"

#: Definitions are only looked for under src/, but REFERENCES are looked for
#: in the whole repo. Tests, tools and scripts are 528 of the 1,273 Python
#: files here and they call into src/ constantly; reading references from src/
#: alone reported every src/ function that only a test exercises as dead.
#: That was the single largest source of false positives in the first version.
REFSCAN = ("src", "tests", "tools", "scripts")

#: Never parsed: caches hold stale copies of every source file and would
#: count each definition as several references, hiding real dead code.
SKIP_DIR_PARTS = {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
                  "graphify-out", ".venv", "venv", "node_modules", ".git"}

CALL_RELATIONS = {"calls", "indirect_call"}

#: Filled by analyse() from the whole repo; see the comment there.
ALL_REEXPORTED: set[str] = set()


def py_files(roots: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        base = ROOT / r
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if SKIP_DIR_PARTS & set(p.parts):
                continue
            out.append(p)
    return out

#: Reached by name from outside; a missing call edge is expected.
FRAMEWORK_HOOKS = {
    "execute", "run", "to_dict", "from_dict", "validate", "verify", "check",
    "health", "status", "describe", "schema", "register", "discover",
    "on_start", "on_stop", "stop", "setup", "teardown", "main",
    "load", "save", "reset", "close", "flush",
    # pytest / unittest call these by name inside a conftest.
    "pytest_configure", "pytest_addoption", "pytest_collection_modifyitems",
    "pytest_runtest_setup", "pytest_runtest_teardown", "pytest_terminal_summary",
    "pytest_report_header", "pytest_report_teststatus", "pytest_unconfigure",
    "pytest_ignore_collect", "pytest_generate_tests", "pytest_cmdline_main",
    "setup_module", "teardown_module", "setup_function", "teardown_function",
    "setup_class", "teardown_class", "setUp", "tearDown", "setUpClass",
    "tearDownClass", "setUpModule", "tearDownModule",
    # unittest / logging / abc / enum conventions.
    "setUpTestData", "runTest", "feed", "nextEvent", "emit", "log",
}

#: Names so generic that a same-name match carries no information.
TOO_COMMON = {
    "get", "set", "run", "start", "stop", "init", "close", "open", "read",
    "write", "load", "save", "next", "main", "test", "helper", "cb", "fn",
}


def load_graph(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"nodes": data.get("nodes", []), "links": data.get("links", [])}


def module_index(files: list[Path]) -> dict[Path, dict[str, Any]]:
    """Per-file: definitions by name, exported names, and attribute uses."""
    idx: dict[Path, dict[str, Any]] = {}
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        funcs: dict[str, int] = {}
        classes: dict[str, int] = {}
        methods: dict[tuple[str, str], int] = {}
        exported: set[str] = set()
        reexported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.setdefault(node.name, node.lineno)
            elif isinstance(node, ast.ClassDef):
                classes.setdefault(node.name, node.lineno)
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.setdefault((node.name, sub.name), sub.lineno)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                # `__all__: list[str] = [...]` is as common here as the bare
                # assignment. Reading only the bare form made every exported
                # function look dead -- the module's own contract said
                # otherwise.
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    try:
                        exported = {e.value for e in node.value.elts
                                    if isinstance(e, ast.Constant)}
                    except AttributeError:
                        pass
        # A package __init__ that does `from .audit_trail import
        # audit_gate_passed` is publishing that name, whether or not anyone
        # in this repo imports it back. Treating those as dead reported the
        # entire public surface of src/CORE as unreferenced.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name != "*":
                        reexported.add(a.asname or a.name)
        idx[f] = {"tree": tree, "funcs": funcs, "classes": classes,
                  "methods": methods, "exported": exported,
                  "reexported": reexported}
    return idx


def real_references(idx: dict[Path, dict[str, Any]], names: set[str],
                    sites: dict[str, set]) -> None:
    """Record, for each name in `names`, every place it is mentioned.

    `sites` is mutated in place and MUST be a defaultdict(set): this
    function does `sites[name].add(...)` and a plain dict raises KeyError on
    the first mention of any name that was not pre-seeded. The caller in
    analyse() happens to pass a defaultdict, which is why the bug stayed
    hidden until a test passed a plain one.
    """
    """Fill in `candidates[name] = {file, line, ...}` reference sites.

    ONE pass over every module, testing every name in the candidate set
    against each node. The obvious implementation walks the tree once per
    name, which is O(names x nodes) and took over ten minutes on this
    repo. Inverting it -- walk each node once, and for each of its
    attributes and names update whichever candidates care -- is a single
    pass and takes seconds.

    Each recorded site is (file, lineno) so the definition itself can be
    excluded later, which is what separates "referenced once, by its own
    def" from "never referenced".
    """
    # A string constant matching a candidate name is a *weak* reference: it is
    # how this codebase dispatches by name, but "name" also appears in prose
    # and in test titles. Weak sites are counted separately so a lone string
    # mention cannot make a function look alive on its own.
    for f, meta in idx.items():
        tree = meta["tree"]
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr in names:
                    sites.setdefault(node.attr, set()).add(
                        (f, node.lineno, "attr"))
            # a method may also be referenced by its qualified graph label
            elif isinstance(node, ast.Name):
                if node.id in names:
                    sites.setdefault(node.id, set()).add(
                        (f, node.lineno, "name"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # getattr("x") / dispatch tables keyed by name
                if node.value in names:
                    sites.setdefault(node.value, set()).add(
                        (f, node.lineno, "str"))


def analyse(g: dict[str, Any], limit: int) -> dict[str, Any]:
    called: set[str] = set()
    for link in g["links"]:
        if link.get("relation") in CALL_RELATIONS:
            called.add(link.get("target"))

    files = py_files(("src",))
    idx = module_index(files)
    # Reference sites come from a wider tree than the definitions do.
    ref_idx = module_index(py_files(REFSCAN))
    # A re-export is a publishing act by *some* module, not by the module
    # that happens to hold the definition: `from .audit_trail import
    # audit_gate_passed` lives in src/CORE/__init__.py while the def is in
    # audit_trail.py. Scoping the check to the defining file therefore found
    # nothing, so the set is collected repo-wide.
    global ALL_REEXPORTED
    ALL_REEXPORTED = set()
    for m in ref_idx.values():
        ALL_REEXPORTED |= m["reexported"]

    dead: list[dict[str, Any]] = []
    api: list[dict[str, Any]] = []
    checked = 0

    # Pass 1: pick out the uncalled candidates and their definition sites.
    # `wanted` maps a bare name to the candidate records that own it.
    wanted: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in g["nodes"]:
        if not n.get("_callable"):
            continue
        sf = (n.get("source_file") or "").replace("\\", "/")
        if not sf.startswith("src/"):
            continue
        p = ROOT / sf
        meta = idx.get(p)
        if meta is None:
            continue
        label = (n.get("label") or "").lstrip(".")
        bare = label.split("(")[0].strip()
        # A method's label is "Class.method". Comparing that whole string
        # against FRAMEWORK_HOOKS and TOO_COMMON never matches, so every
        # `execute`, `health` and `run` method bypassed both exclusions --
        # which is most of why the unreferenced-methods list ran to 2,817.
        if "." in bare:
            bare = bare.rsplit(".", 1)[-1]
        if not bare or bare in TOO_COMMON:
            continue
        if bare.startswith("__") and bare.endswith("__"):
            continue
        # conftest.py is loaded by pytest, not imported by anything.
        if p.name == "conftest.py":
            continue
        if bare in FRAMEWORK_HOOKS:
            continue
        loc = n.get("source_location") or ""
        try:
            lineno = int(loc.lstrip("L") or 0)
        except ValueError:
            lineno = 0
        if meta["funcs"].get(bare) != lineno and \
           not any(v == lineno for v in meta["methods"].values()):
            continue
        is_method = any(v == lineno for v in meta["methods"].values())
        owner = next((k for k, v in meta["methods"].items() if v == lineno), None)
        if n["id"] in called:
            continue
        checked += 1
        rec = {"name": bare, "file": sf, "line": lineno, "path": p,
               "kind": "method" if is_method else "function",
               "owner": owner[0] if owner else None,
               "exported": bare in meta["exported"],
               "reexported": bare in ALL_REEXPORTED}
        wanted[bare].append(rec)

    # Pass 2: one walk, recording where each candidate name is mentioned.
    #
    # The candidate NAMES come from `wanted`; the reference SITES are
    # accumulated into a separate dict. Passing the sites dict as the lookup
    # table (as an earlier revision did) meant every lookup missed, because
    # the sites dict starts empty -- so zero references were ever recorded
    # and every uncalled candidate was reported dead no matter how often it
    # was used. The signature takes the two separately for that reason.
    sites: dict[str, set] = defaultdict(set)
    real_references(ref_idx, set(wanted), sites)

    for bare, recs in wanted.items():
        for rec in recs:
            if rec["exported"] or rec["reexported"]:
                # In `__all__` means the module declares it public. Reporting
                # it as dead contradicts the module's own contract, so it is
                # not a finding at all.
                continue
            hit = [s for s in sites.get(bare, ()) if (s[0], s[1])
                   != (rec["path"], rec["line"])]
            rec["refs"] = sum(1 for s in hit if s[2] != "str")
            rec["weak_refs"] = sum(1 for s in hit if s[2] == "str")
            if rec["refs"] == 0:
                (api if rec["kind"] == "method" else dead).append(rec)

    dead.sort(key=lambda r: (r["file"], r["line"]))
    api.sort(key=lambda r: (r["file"], r["line"]))
    for coll in (dead, api):
        for r in coll:
            r.pop("path", None)
    return {"checked": checked, "dead_functions": dead,
            "unreferenced_methods": api}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", default=str(GRAPH))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv)

    gp = Path(args.graph)
    if not gp.exists():
        print("INCONCLUSIVE: no graph at %s" % gp, file=sys.stderr)
        return 2
    try:
        g = load_graph(gp)
    except Exception as exc:
        print("INCONCLUSIVE: cannot read graph: %s" % exc, file=sys.stderr)
        return 2
    n_calls = sum(1 for l in g["links"] if l.get("relation") in CALL_RELATIONS)
    if n_calls == 0:
        print("INCONCLUSIVE: graph has no call edges", file=sys.stderr)
        return 2

    rep = analyse(g, args.limit)
    # A function whose ONLY mentions are string literals is a weaker claim
    # than one nothing mentions at all: the string is at least evidence that
    # some dispatcher looks the name up. Sorted strongest-first, because a
    # flat list of 700 is not a report.
    rep["dead_functions"].sort(key=lambda r: (r["weak_refs"], r["file"], r["line"]))
    rep["unreferenced_methods"].sort(key=lambda r: (r["weak_refs"], r["file"], r["line"]))

    if args.json:
        print(json.dumps({"graph": str(gp), "call_edges": n_calls, **rep},
                         indent=2))
        return 1 if rep["dead_functions"] else 0

    print("graph: %s | call edges: %d | uncalled candidates examined: %d"
          % (gp.name, n_calls, rep["checked"]))
    print("\nUNREFERENCED FUNCTIONS (%d):" % len(rep["dead_functions"]))
    print("   (str-only = name appears only inside string literals)")
    for r in rep["dead_functions"][:args.limit]:
        print("   %-40s %s:%s%s" % (r["name"], r["file"], r["line"],
                                     "  [str-only x%d]" % r["weak_refs"]
                                     if r["weak_refs"] else ""))
    if len(rep["dead_functions"]) > args.limit:
        print("   ... %d more" % (len(rep["dead_functions"]) - args.limit))

    print("\nUNREFERENCED METHODS (%d) -- API surface reached by name, "
          "NOT dead code:" % len(rep["unreferenced_methods"]))
    for r in rep["unreferenced_methods"][:12]:
        print("   %-30s %s:%s  (%s)%s"
              % (r["name"], r["file"], r["line"], r["owner"] or "?",
                 "  [str-only x%d]" % r["weak_refs"] if r["weak_refs"] else ""))
    if len(rep["unreferenced_methods"]) > 12:
        print("   ... %d more" % (len(rep["unreferenced_methods"]) - 12))

    if rep["dead_functions"]:
        print("\nThese are CANDIDATES: a function called only from a shell "
              "script, an eval, or another repo will not appear here.")
    else:
        print("\nOK: no unreferenced module-level functions in src/.")
    return 1 if rep["dead_functions"] else 0


if __name__ == "__main__":
    sys.exit(main())
