#!/usr/bin/env python3
# coding: utf-8
"""Reconcile declared path manifests against the filesystem and the graph.

The problem this answers
-----------------------
`scripts/build_all_concurrent.py` declares 39 build targets, each naming an
`entry` file. Nine of those names did not resolve on disk. Hand-checking
that cost several rounds of grep, and the interesting half of the answer --
*where did the file go?* -- was the part grep could not give.

The graph can: every indexed file is a node, so a file that moved is still
in the graph under its new path, indexed by basename. This tool therefore
reports three outcomes per entry rather than a bare pass/fail:

  ok        the declared path exists
  moved     it does not, but exactly one indexed file has that basename
            (the actionable case: the declaration is stale, the file lives)
  missing   it does not, and either no file shares the basename or several
            do, so the correct fix cannot be inferred from evidence

The "several do" case matters. Reporting one arbitrary match would be a
guess dressed as a finding, and a guess in a build manifest is worse than
no report at all.

Why this is not `test -f`
-------------------------
A test asserting every entry exists tells you nine names are wrong. It does
not tell you which nine moved versus never existed, and it cannot tell you
where they went. Two of the nine had moved; seven have never existed in any
commit. Those need opposite fixes -- a path edit and a deletion -- and
collapsing them into one boolean loses the distinction.
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

#: Declarations of the form NAME = {"entry": "path", ...} or a list of paths.
MANIFEST_SOURCES = ("scripts/build_all_concurrent.py",)

SKIP_DIR_PARTS = {"__pycache__", ".mypy_cache", ".pytest_cache", "graphify-out",
                  ".venv", "venv", "node_modules", ".git", "output", "build"}


def load_graph(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"nodes": data.get("nodes", []), "links": data.get("links", [])}


def graph_index(g: dict[str, Any]) -> tuple[dict[str, set[str]],
                                            dict[str, set[str]]]:
    """(basename -> paths, repo-relative dir path -> basenames) from the graph."""
    by_base: dict[str, set[str]] = defaultdict(set)
    dirs: set[str] = set()
    for n in g["nodes"]:
        sf = (n.get("source_file") or "").replace("\\", "/")
        if not sf or sf in ("?", "unknown"):
            continue
        by_base[Path(sf).name].add(sf)
        parent = str(Path(sf).parent).replace("\\", "/")
        if parent and parent != ".":
            dirs.add(parent)
    return by_base, dirs


def declared_entries(path: Path) -> list[dict[str, Any]]:
    """Every `{"entry": "<path>"}` / `"<path>"` string in a manifest file.

    Parsed rather than grepped: the earlier session found 9 bad paths here
    and the grep version of the same question also matched the docstring
    example paths in the same file, which is how `create_structure` came to
    look exported when the `__all__` was 100 lines up inside a docstring.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    out: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        # `X = {...}` and `X: Dict[str, Dict] = {...}` are both used here, and
        # reading only the bare form found ZERO entries and reported OK --
        # a green result meaning nothing was checked at all.
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if not (isinstance(t, ast.Name) and t.id.isupper()):
                continue
            for sub in ast.walk(node.value):
                if not isinstance(sub, ast.Dict):
                    continue
                for k, v in zip(sub.keys, sub.values):
                    if (isinstance(k, ast.Constant) and k.value == "entry"
                            and isinstance(v, ast.Constant)
                            and isinstance(v.value, str)):
                        out.append({"table": t.id, "entry": v.value,
                                    "line": sub.lineno})
    return out


def classify(entry: str, repo: Path, by_base: dict[str, set[str]],
             dirs: set[str]) -> dict[str, Any]:
    p = repo / entry
    if p.exists():
        return {"entry": entry, "status": "ok", "candidates": []}
    base = Path(entry).name
    hits = sorted(by_base.get(base, ()))
    # Prefer a candidate that still lives under the declared directory: a
    # file that kept its folder and changed name is a different story from
    # one that moved directories, and the folder is the stronger evidence.
    parent = str(Path(entry).parent).replace("\\", "/")
    same_dir = [h for h in hits if str(Path(h).parent).replace("\\", "/") == parent]
    if len(same_dir) == 1:
        return {"entry": entry, "status": "moved",
                "candidates": [same_dir[0]], "why": "same directory, new name"}
    if len(hits) == 1:
        return {"entry": entry, "status": "moved", "candidates": hits,
                "why": "unique basename match in the graph"}
    if len(hits) > 1:
        return {"entry": entry, "status": "ambiguous", "candidates": hits,
                "why": "%d files share this basename" % len(hits)}
    return {"entry": entry, "status": "missing", "candidates": [],
            "why": "no file in the graph has this basename"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reconcile declared manifests against disk and graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", default=str(GRAPH))
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true",
                    help="print only the problems")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    gp = Path(args.graph)
    if not gp.exists():
        print("INCONCLUSIVE: no graph at %s" % gp, file=sys.stderr)
        return 2
    try:
        g = load_graph(gp)
    except Exception as exc:
        print("INCONCLUSIVE: cannot read graph: %s" % exc, file=sys.stderr)
        return 2

    by_base, dirs = graph_index(g)
    if not by_base:
        print("INCONCLUSIVE: graph indexes no files", file=sys.stderr)
        return 2

    found: list[dict[str, Any]] = []
    for rel in MANIFEST_SOURCES:
        src = repo / rel
        if not src.exists():
            print("INCONCLUSIVE: no manifest at %s" % rel, file=sys.stderr)
            return 2
        for d in declared_entries(src):
            rec = classify(d["entry"], repo, by_base, dirs)
            rec["source"] = rel
            rec["table"] = d["table"]
            rec["decl_line"] = d["line"]
            found.append(rec)

    if not found:
        # The single most important guard in this file. "Every entry
        # resolves" is trivially true of an empty set, and reporting OK for
        # an empty set is how the first version of this tool passed while
        # having examined nothing.
        print("INCONCLUSIVE: no manifest entries were extracted from %s"
              % ", ".join(MANIFEST_SOURCES), file=sys.stderr)
        return 2

    bad = [r for r in found if r["status"] != "ok"]
    by_status: dict[str, int] = defaultdict(int)
    for r in found:
        by_status[r["status"]] += 1

    if args.json:
        print(json.dumps({"graph": str(gp), "total": len(found),
                          "counts": dict(by_status), "entries": found},
                         indent=2))
        return 1 if bad else 0

    print("manifest entries: %d | ok %d | moved %d | ambiguous %d | missing %d"
          % (len(found), by_status.get("ok", 0), by_status.get("moved", 0),
             by_status.get("ambiguous", 0), by_status.get("missing", 0)))
    if not args.quiet:
        print("(graph indexed %d distinct filenames)" % len(by_base))

    for status in ("moved", "ambiguous", "missing"):
        rows = [r for r in bad if r["status"] == status]
        if not rows:
            continue
        print("\n%s (%d):" % (status.upper(), len(rows)))
        for r in rows:
            print("   %s:%s" % (r["source"], r["decl_line"]))
            print("      declared: %s" % r["entry"])
            if r["candidates"]:
                for c in r["candidates"][:5]:
                    print("      actual:   %s" % c)
                if len(r["candidates"]) > 5:
                    print("      ... and %d more" % (len(r["candidates"]) - 5))
            else:
                print("      %s" % r["why"])

    if bad:
        print("\nmoved = fix the path. missing = the target does not exist; "
              "delete the entry rather than invent a path.")
        return 1
    print("\nOK: every declared manifest entry resolves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
