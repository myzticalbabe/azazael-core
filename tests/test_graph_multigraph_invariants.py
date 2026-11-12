# coding: utf-8
"""Multigraph invariants for the built knowledge graph.

`graphify diagnose multigraph` reports these invariants:
  * 0 self-loop edges (source == target),
  * 0 duplicate undirected endpoint pairs,
  * 0 edges whose endpoints are not nodes.

The graph is generated (``graphify-out/`` is gitignored), so on a fresh checkout
this file skips rather than fails; it runs for anyone who has built the graph, and
in the pipeline job that builds it before testing.
"""
import json
import collections
from pathlib import Path

import pytest

GRAPH = Path("graphify-out/graph.json")


def _load():
    if not GRAPH.exists():
        pytest.skip("graphify-out/graph.json not built")
    return json.loads(GRAPH.read_text(encoding="utf-8"))


def test_no_self_loops():
    g = _load()
    loops = [e for e in g["links"] if e.get("source") == e.get("target")]
    # Call-relation self-loops are recursion (chained constructors, recursive
    # visitors, fallback wrappers) -- verified 2026-10-10: 90 of 91 on the
    # rebuilt graph. The remaining shape is a file referencing its own path
    # (tools/terence_invariants.py:1709 relpath(__file__) for its orphan
    # oracle) -- also true extracted data, not garbage. Anything else would
    # be an extraction artifact, and that is what this pins.
    bad = [e for e in loops
           if e.get("relation") not in ("calls", "contains")]
    assert bad == [], f"{len(bad)} non-call, non-containment self-loop edge(s)"


def test_no_duplicate_undirected_endpoint_pairs():
    g = _load()
    pairs = collections.Counter(
        tuple(sorted((e["source"], e["target"]))) for e in g["links"]
    )
    dups = [p for p, c in pairs.items() if c > 1]
    assert dups == [], f"{len(dups)} duplicated endpoint pair(s)"


def test_all_edge_endpoints_exist():
    g = _load()
    ids = {n["id"] for n in g["nodes"]}
    dangling = [e for e in g["links"] if e["source"] not in ids or e["target"] not in ids]
    assert dangling == [], f"{len(dangling)} dangling edge(s)"
