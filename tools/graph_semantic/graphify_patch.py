"""Correct graphify's own god-node ranking, rather than post-processing the report.

`tools/graph_semantic/god_nodes.py` measures the corrected ranking and patches the
generated `GRAPH_REPORT.md`. That works, but it is a *post*-process: every
`graphify cluster-only` regenerates the wrong ranking and it has to be re-applied.
This module fixes the ranking at the source instead.

The bug in `graphify.analyze.god_nodes`: it excludes file-level hub nodes, concept
nodes (those with no file extension) and JSON keys, but **not** `document` nodes.
On a graph where every doc has a node, a backlog document that lists per-line file
detail (`CATCHALL_BACKLOG.md`, 583 edges) then outranks `DirectMemory` (197) as the
"#1 core abstraction".

Two ways to use this:

* In-process — ``import graphify_patch; graphify_patch.install()`` replaces
  ``graphify.analyze.god_nodes`` with the corrected version for the rest of the
  process. Any report generated in that process (e.g. via ``graphify.export``) uses
  the corrected ranking.
* Upstream — ``python tools/graph_semantic/graphify_patch.py --emit-patch`` writes a
  unified diff against the installed ``graphify/analyze.py``
  (``tools/graph_semantic/patches/graphify_analyze_god_nodes.patch``) so the fix can
  be submitted to graphify itself.

Usage:
    python tools/graph_semantic/graphify_patch.py                 # show whether applied
    python tools/graph_semantic/graphify_patch.py --emit-patch    # write the diff
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import networkx as nx

try:  # graphify is imported lazily so --emit-patch works without it on the path
    import graphify.analyze as _analyze
except Exception:  # pragma: no cover - only when graphify is absent
    _analyze = None  # type: ignore[assignment]

PATCH_DIR = Path("tools/graph_semantic/patches")
PATCH_FILE = PATCH_DIR / "graphify_analyze_god_nodes.patch"

# Structurally meaningless as abstractions, in addition to graphify's own
# _BUILTIN_NOISE_LABELS: stdlib module names that slip through as bare symbols,
# and 1-2 character names (a bare `f` resolved across 154 files is a test helper,
# not an architectural hub).
_STDLIB = {
    "sys", "os", "re", "json", "io", "abc", "typing", "pathlib", "argparse",
    "sqlite3", "logging", "threading", "subprocess", "collections", "dataclasses",
    "datetime", "time", "math", "random", "hashlib", "shutil", "tempfile", "uuid",
    "functools", "itertools", "contextlib", "asyncio", "concurrent", "unittest",
}


def is_doc_or_injected_hub(G: nx.Graph, node_id: str) -> bool:
    """True for document / injected-concept / rationale nodes and test helpers.

    These are exactly the node classes graphify's ranking does not exclude but
    which encode "this file mentions many things" rather than "this is a core
    abstraction".
    """
    attrs = G.nodes[node_id]
    ftype = str(attrs.get("file_type") or "").lower()
    if ftype in {"document", "concept", "rationale"}:
        return True
    label = str(attrs.get("label") or "")
    if label.lower() in _STDLIB:
        return True
    if len(label.rstrip("()")) <= 2:
        return True
    sf = str(attrs.get("source_file") or "")
    if "/tests/" in sf or "/test/" in sf or Path(sf).name.startswith("test_"):
        return True
    return False


def god_nodes_fixed(
    G: nx.Graph,
    top_n: int = 10,
    exclude_hubs_percentile: float | None = None,
) -> list[dict]:
    """Drop-in replacement for ``graphify.analyze.god_nodes`` with doc hubs excluded."""
    if _analyze is None:
        raise RuntimeError("graphify is not importable")
    degree = dict(G.degree())
    hub_threshold: float | None = None
    if exclude_hubs_percentile is not None:
        degrees = sorted(degree.values())
        if degrees:
            idx = max(0, int(len(degrees) * exclude_hubs_percentile / 100) - 1)
            hub_threshold = degrees[idx]
    sorted_nodes = sorted(degree.items(), key=lambda x: x[1], reverse=True)
    result: list[dict] = []
    for node_id, deg in sorted_nodes:
        if hub_threshold is not None and deg > hub_threshold:
            continue
        if (
            _analyze._is_file_node(G, node_id)
            or _analyze._is_concept_node(G, node_id)
            or _analyze._is_json_key_node(G, node_id)
        ):
            continue
        if G.nodes[node_id].get("label", "") in _analyze._BUILTIN_NOISE_LABELS:
            continue
        if is_doc_or_injected_hub(G, node_id):
            continue
        result.append({
            "id": node_id,
            "label": G.nodes[node_id].get("label", node_id),
            "degree": deg,
        })
        if len(result) >= top_n:
            break
    return result


def install() -> bool:
    """Monkeypatch ``graphify.analyze.god_nodes`` in this process. Returns True on success."""
    if _analyze is None:
        return False
    _analyze.god_nodes = god_nodes_fixed
    return True


# ---------------------------------------------------------------------------
# Upstream patch generation against the installed analyze.py
# ---------------------------------------------------------------------------
_HELPER = '''def _is_doc_or_injected_hub(G, node_id: str) -> bool:
    """Document / injected-concept nodes that outrank real code when ranked by degree."""
    attrs = G.nodes[node_id]
    if str(attrs.get("file_type") or "").lower() in {"document", "concept", "rationale"}:
        return True
    label = str(attrs.get("label") or "")
    if label.lower() in _BUILTIN_NOISE_LABELS:
        return True
    return len(label.rstrip("()")) <= 2


'''


def _patched_source(original: str) -> str:
    if "_is_doc_or_injected_hub" in original:
        return original  # already patched
    sentinel = "def god_nodes(G: nx.Graph, top_n: int = 10"
    idx = original.index(sentinel)
    text = original[:idx] + _HELPER + original[idx:]
    clause = (
        "        if _is_file_node(G, node_id) or _is_concept_node(G, node_id) "
        "or _is_json_key_node(G, node_id):\n            continue\n"
    )
    addition = (
        clause
        + "        if _is_doc_or_injected_hub(G, node_id):\n            continue\n"
    )
    if clause not in text:
        raise SystemExit("could not locate the god_nodes exclusion clause in analyze.py")
    return text.replace(clause, addition, 1)


def emit_patch() -> int:
    if _analyze is None:
        print("graphify is not importable", file=sys.stderr)
        return 2
    source_path = Path(_analyze.__file__)
    original = source_path.read_text(encoding="utf-8")
    if "_is_doc_or_injected_hub" in original:
        print("installed graphify already has the fix; nothing to emit")
        return 0
    patched = _patched_source(original)
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"a/{source_path.name}",
        tofile=f"b/{source_path.name}",
    )
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    header = (
        "# Correct graphify.analyze.god_nodes: exclude document / injected-concept hubs.\n"
        "# Without this, a backlog document with per-line file detail outranks every\n"
        "# real code abstraction in the ranking.\n"
        "#\n"
        f"# Target: {source_path}\n"
        "# Apply with: patch -p1 < this-file   (from the graphify package root)\n"
        "#\n"
    )
    PATCH_FILE.write_text(header + "".join(diff), encoding="utf-8")
    print(f"wrote {PATCH_FILE}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emit-patch", action="store_true", help="write the upstream unified diff")
    args = ap.parse_args()
    if args.emit_patch:
        return emit_patch()
    if _analyze is None:
        print("graphify is not importable", file=sys.stderr)
        return 2
    src = Path(_analyze.__file__).read_text(encoding="utf-8")
    applied = "_is_doc_or_injected_hub" in src
    print(f"graphify.analyze at {_analyze.__file__}")
    print(f"doc-hub fix present in installed source: {applied}")
    print("call install() to apply it in-process, or --emit-patch to prepare it upstream")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
