#!/usr/bin/env python3
# coding: utf-8
"""Make the audit scope boundary visible instead of implicit.

The problem
-----------
`mesh_function_preflight` declares its scope as 61 named module stems
(`SCOPE_GROUPS`) and enforces, via `tests/test_mesh_wire_or_cut.py`, that
*every unreferenced function within that scope is declared wired, retired or
pending*. That test passes, and it is right to.

But nothing states the boundary. The artifact says `declared: 171` and reads
as though the mesh is fully triaged, while 584 of the 645 module stems under
`src/` are outside the declared scope by design. A reader has no way to tell
"nothing left to do" from "never looked here" — and on 2026-09-29 that
ambiguity was resolved wrongly, in both directions: once by reading 109
pending as the whole tree, and once by reading `src/CORE/` as the boundary
when the real scope is 61 stems.

So this tool does not widen the scope. Widening is a judgement about what
counts as the architecture, and it belongs to whoever owns that judgement.
What it does is measure the boundary and fail when it moves silently.

Two failure modes it prevents
-----------------------------
1. **A scope that shrinks without anyone noticing.** If a stem is dropped
   from `SCOPE_GROUPS`, findings inside it silently stop being reported and
   the wire-or-cut test stays green — a green test over a smaller question.
2. **A scope that drifts from reality.** A stem listed but absent from disk,
   or a directory of `src/` with no stem covering it at all, is unexamined
   code wearing a passing test.

The report is a census, not a verdict. "613 stems unexamined" is a fact about
coverage. Whether that is acceptable is not this tool's call.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "tools" / "mesh_function_preflight.py"
SRC = ROOT / "src"

SKIP_PARTS = {"__pycache__", ".mypy_cache", ".pytest_cache", "graphify-out",
              ".git", "node_modules", "output", "build"}


def declared_stems(path: Path = PREFLIGHT) -> dict[str, list[str]]:
    """The scope the preflight actually enforces, read from its AST.

    Read from the AST rather than imported: the preflight imports the whole
    worm registry at module scope, and a tool whose job is to measure the
    boundary should not have side effects just to read it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    consts: dict[str, list[str]] = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, (ast.List, ast.Tuple)):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    try:
                        consts[t.id] = [e.value for e in n.value.elts
                                       if isinstance(e, ast.Constant)]
                    except AttributeError:
                        pass
    out: dict[str, list[str]] = {}
    for n in tree.body:
        if not (isinstance(n, ast.AnnAssign)
                and getattr(n.target, "id", "") == "SCOPE_GROUPS"):
            continue
        for tup in n.value.elts:
            if not isinstance(tup, ast.Tuple) or len(tup.elts) != 2:
                continue
            label = (tup.elts[0].value if isinstance(tup.elts[0], ast.Constant)
                     else "?")
            lst = tup.elts[1]
            names = (consts.get(lst.id, []) if isinstance(lst, ast.Name)
                     else [e.value for e in getattr(lst, "elts", [])
                           if isinstance(e, ast.Constant)])
            out[label] = names
    return out


def on_disk(src: Path) -> set[str]:
    out: set[str] = set()
    for p in src.rglob("*.py"):
        if SKIP_PARTS & set(p.parts):
            continue
        out.add(p.stem)
    return out


def analyse(preflight: Path = PREFLIGHT, src: Path = SRC) -> dict[str, Any]:
    """Bound parameters, not globals.

    Reading the module-level SRC made this untestable: a caller that sets
    `scope_coverage.SRC` sees no effect, and the tool silently reports the
    real repo's numbers while appearing to analyse the fixture.
    """
    groups = declared_stems(preflight)
    declared = {s for names in groups.values() for s in names}
    disk = on_disk(src)
    present = declared & disk
    absent = sorted(declared - disk)
    uncovered = sorted(disk - declared)
    return {
        "groups": {k: len(v) for k, v in groups.items()},
        "declared_total": len(declared),
        "present_on_disk": len(present),
        "declared_but_absent": absent,
        "modules_on_disk": len(disk),
        "unexamined": len(uncovered),
        "coverage_pct": round(100.0 * len(present) / len(disk), 1) if disk else 0.0,
        "unexamined_sample": uncovered[:40],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report the preflight's declared scope vs what is on disk.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--preflight", default=str(PREFLIGHT))
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--min-coverage", type=float, default=0.0,
                    help="fail if coverage drops below this percentage")
    args = ap.parse_args(argv)

    pf = Path(args.preflight)
    if not pf.exists():
        print("INCONCLUSIVE: no preflight at %s" % pf, file=sys.stderr)
        return 2
    rep = analyse(pf, Path(args.src))
    if rep["modules_on_disk"] == 0:
        print("INCONCLUSIVE: no modules found under src/", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(rep, indent=2))
        return 1 if rep["declared_but_absent"] else 0

    print("preflight scope: %d declared stems across %d groups"
          % (rep["declared_total"], len(rep["groups"])))
    for g, n in sorted(rep["groups"].items()):
        print("   %-26s %d" % (g, n))
    print("\nmodules on disk under src/: %d" % rep["modules_on_disk"])
    print("in declared scope:          %d  (%.1f%%)"
          % (rep["present_on_disk"], rep["coverage_pct"]))
    print("NOT in declared scope:      %d" % rep["unexamined"])
    if rep["unexamined_sample"]:
        print("   e.g. %s" % ", ".join(rep["unexamined_sample"][:12]))

    rc = 0
    if rep["declared_but_absent"]:
        # The dangerous direction: a stem that exists in SCOPE_GROUPS but
        # matches no file means findings in it can never be reported, and the
        # wire-or-cut test stays green over a silently smaller question.
        print("\nDECLARED BUT ABSENT (%d) -- scope entries that match no file:"
              % len(rep["declared_but_absent"]))
        for s in rep["declared_but_absent"]:
            print("   %s" % s)
        rc = 1
    if args.min_coverage and rep["coverage_pct"] < args.min_coverage:
        print("\nFAIL: coverage %.1f%% is below the --min-coverage floor of %.1f%%"
              % (rep["coverage_pct"], args.min_coverage))
        return 1

    if not rc:
        print("\nOK: every declared scope entry matches a real module.")
        print("Note: this says the declared scope is intact, NOT that the tree")
        print("is triaged. %d modules are outside it by design."
              % rep["unexamined"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
