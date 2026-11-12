"""God nodes, with the false hubs removed.

`graphify.analyze.god_nodes` excludes file-level hub nodes, concept nodes and
built-in noise labels. It does **not** exclude:

- **document nodes.** A documentation title is not a filename, so the file-node
  check misses it. `CATCHALL_BACKLOG.md` (583 edges — it lists per-line file
  detail) then outranks `DirectMemory` (197) as the #1 "core abstraction".
- **concept nodes we inject** for grouping (skill categories, script suites),
  which have a real `source_file` and so are not flagged as concepts either.
- **test-local helpers.** `f()` in `tests/test_exmachina_operators.py` is the #2
  node (260 edges) purely because bare-name resolution matches `f` across 154
  files. A test helper is not an architectural hub.

This tool applies those three filters and prints the ranking that reflects code
abstractions. It is a measurement, not a rewrite: pass --patch-report to update
the God Nodes section of GRAPH_REPORT.md in place.

Usage:
    python tools/graph_semantic/god_nodes.py
    python tools/graph_semantic/god_nodes.py --top 25 --json
    python tools/graph_semantic/god_nodes.py --patch-report
    python tools/graph_semantic/god_nodes.py --include-tests
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

# The dependencies (networkx, graphify) must come from an interpreter that HAS
# them. Production is the 3.15t venv; the 3.14 install is a retired-era last
# resort and is only used when the production venv lacks the pair. Inserting a
# 3.14 site-packages into a 3.15 process would hand it cp314 binaries, so the
# directories are checked before either is added.
_PRODUCTION_SP = Path("C:/Azazael/.venvs/py315t/Lib/site-packages")
_LEGACY_SP = Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python314" / "Lib" / "site-packages"
for _sp in (_PRODUCTION_SP, _LEGACY_SP):
    if (_sp / "networkx").is_dir() and (_sp / "graphify").is_dir():
        sys.path.insert(0, str(_sp))
        break

import networkx as nx  # noqa: E402  (after the sys.path bootstrap above)
from graphify.analyze import _is_concept_node, _is_file_node, _is_json_key_node  # noqa: E402

GRAPH = Path("graphify-out/graph.json")
REPORT = Path("graphify-out/GRAPH_REPORT.md")

# Names that are structurally meaningless as abstractions: stdlib module names the
# AST pass already half-filters, plus 1-2 character function names.
_STDLIB = {
    "sys", "os", "re", "json", "io", "abc", "typing", "pathlib", "argparse",
    "sqlite3", "logging", "threading", "subprocess", "collections", "dataclasses",
    "datetime", "time", "math", "random", "hashlib", "shutil", "tempfile", "uuid",
    "functools", "itertools", "contextlib", "asyncio", "concurrent", "unittest",
}


def compute(graph_path: Path, top: int, include_tests: bool) -> list[dict]:
    data = json.loads(graph_path.read_text(encoding="utf-8"))
    G = nx.Graph()
    for n in data["nodes"]:
        attrs = dict(n)
        G.add_node(n["id"], **attrs)
    for e in data.get("links", data.get("edges", [])):
        s, t = e.get("source"), e.get("target")
        if s is not None and t is not None and s in G and t in G:
            G.add_edge(s, t)

    degree = dict(G.degree())
    ranked = sorted(degree.items(), key=lambda kv: kv[1], reverse=True)
    out: list[dict] = []
    excluded: collections.Counter = collections.Counter()
    for nid, deg in ranked:
        attrs = G.nodes[nid]
        ftype = (attrs.get("file_type") or "").lower()
        label = str(attrs.get("label") or "")
        sf = str(attrs.get("source_file") or "")

        if ftype in {"document", "concept", "rationale"} and sf.endswith(".md"):
            excluded["document_or_injected_concept"] += 1
            continue
        if ftype in {"document", "concept"}:
            excluded["document_or_concept"] += 1
            continue
        if _is_file_node(G, nid) or _is_concept_node(G, nid) or _is_json_key_node(G, nid):
            excluded["graphify_file_concept_json"] += 1
            continue
        if label.lower() in _STDLIB:
            excluded["stdlib"] += 1
            continue
        if len(label.rstrip("()")) <= 2:
            excluded["single_char_name"] += 1
            continue
        if not include_tests and ("/tests/" in sf or "/test/" in sf or Path(sf).name.startswith("test_")):
            excluded["test_local"] += 1
            continue
        out.append({"id": nid, "label": label, "degree": deg,
                    "file_type": ftype, "source_file": sf})
        if len(out) >= top:
            break
    print(f"excluded: {dict(excluded)}", file=sys.stderr)
    return out


def patch_report(ranking: list[dict]) -> None:
    if not REPORT.exists():
        print("no GRAPH_REPORT.md to patch", file=sys.stderr)
        return
    text = REPORT.read_text(encoding="utf-8")
    start = text.find("## God Nodes")
    if start < 0:
        print("no God Nodes section found", file=sys.stderr)
        return
    end = text.find("\n## ", start + 1)
    end = len(text) if end < 0 else end
    lines = [
        "## God Nodes (most connected - your core abstractions)",
        "",
        "_Ranked by `tools/graph_semantic/god_nodes.py`, which additionally excludes document",
        "and injected-concept hubs, stdlib names, and test-local helpers. The raw",
        "graphify ranking is dominated by documentation reference hubs._",
        "",
    ]
    for i, n in enumerate(ranking, 1):
        lines.append(f"{i}. `{n['label']}` - {n['degree']} edges")
    lines.append("")
    REPORT.write_text(text[:start] + "\n".join(lines) + text[end:], encoding="utf-8")
    print(f"patched God Nodes section ({len(ranking)} entries)", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--graph", default=str(GRAPH))
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--patch-report", action="store_true")
    ap.add_argument("--include-tests", action="store_true")
    args = ap.parse_args()

    ranking = compute(Path(args.graph), args.top, args.include_tests)
    if args.json:
        print(json.dumps(ranking, indent=1))
    else:
        for i, n in enumerate(ranking, 1):
            print(f"{i:2d}. {n['degree']:4d}  {n['label']}  [{n['file_type']}] {n['source_file']}")
    if args.patch_report:
        patch_report(ranking)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
