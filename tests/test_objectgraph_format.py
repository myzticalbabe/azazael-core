"""EP206 ObjectGraph: the typed node/edge layer, and the file format around it.

REACHABILITY CHAIN (why this is not exercise-only)
--------------------------------------------------
`build` -> `MetaCluster`? No. The chain is:

  `WORMS/WORM_SELECTOR.py::WormSelectorWorm.execute` (production: the worm's
      `execute()` entry, invoked by the worm registry / CLUSTERWORMS dispatch)
        -> `LocalMemoryStore.get_knowledge_graph_stats()`   (local_memory.py)
          -> `LocalMemoryStore._object_graph_counts()`      (the typed layer,
                                                             counted here)
  `export_object_graph` / `load_object_graph` are the file-format half, driven
      by the same document builder.

Before this change `get_knowledge_graph_stats` returned only the flat counters
(total/by_agent/by_type/avg_confidence) and `document_graph_stats` -- the method
the technique map names for this row -- existed with `typed_nodes: False` and the
note "typed node/edge layer is a new build (L), not a wiring". That layer is what
this file now builds and asserts.

The document is derived from the rows that EXIST (`knowledge_patterns`), never
from a second store: one `pattern` node per row, one `agent` hub per distinct
`source_agent`, one `pattern_type` hub per distinct type, and `authored_by` /
`of_type` edges. Each edge endpoint must resolve inside the same document, which
`load_object_graph` re-checks on read.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
# Imports stay at MODULE level here: the anti-pattern ratchet counts
# `import_inside_function`, and this file must not raise that count.
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.local_memory import LocalMemoryStore  # noqa: E402 (path set above)


def _store(tmp: str) -> LocalMemoryStore:
    """A store on a temp database with three patterns over two agents."""
    store = LocalMemoryStore(db_path=os.path.join(tmp, "objectgraph.db"))
    store.register_pattern("agent_A", "anomaly", [1.0, 0.0], confidence=0.9)
    store.register_pattern("agent_A", "preference", [0.5, 0.5], confidence=0.7)
    store.register_pattern("agent_B", "anomaly", [0.0, 1.0], confidence=0.8)
    return store


# -- the typed layer ---------------------------------------------------------------

def test_document_has_typed_nodes_and_two_edges_per_pattern():
    with tempfile.TemporaryDirectory() as tmp:
        doc = _store(tmp).object_graph_document()

        assert doc["format"] == LocalMemoryStore.OBJECT_GRAPH_FORMAT
        assert doc["gaps"] == []
        assert doc["truncated"] is False

        kinds = {}
        for node in doc["nodes"]:
            kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
        # 3 pattern rows, 2 distinct agents, 2 distinct types.
        assert kinds == {"pattern": 3, "agent": 2, "pattern_type": 2}, kinds
        assert doc["n_nodes"] == 7
        # authored_by + of_type for each of the 3 rows.
        assert doc["n_edges"] == 6
        assert doc["n_patterns_total"] == 3


def test_every_edge_endpoint_resolves_inside_the_document():
    """A graph with a dangling endpoint is broken, not merely incomplete."""
    with tempfile.TemporaryDirectory() as tmp:
        doc = _store(tmp).object_graph_document()
        known = {n["id"] for n in doc["nodes"]}

        dangling = [e for e in doc["edges"]
                    if e["from"] not in known or e["to"] not in known]
        assert dangling == []
        # Both relations are present, so the edges are not one relation twice.
        assert {e["rel"] for e in doc["edges"]} == {"authored_by", "of_type"}


def test_document_ordering_is_deterministic():
    """Two documents over the same rows differ only in `generated_at`."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        first = store.object_graph_document()
        second = store.object_graph_document()

        assert first["nodes"] == second["nodes"]
        assert first["edges"] == second["edges"]


def test_limit_truncates_and_says_so():
    """A partial graph must not look complete."""
    with tempfile.TemporaryDirectory() as tmp:
        doc = _store(tmp).object_graph_document(limit=2)

        assert doc["truncated"] is True
        assert doc["n_patterns_total"] == 3
        assert sum(1 for n in doc["nodes"] if n["kind"] == "pattern") == 2


# -- the file format ---------------------------------------------------------------

def test_export_then_load_round_trips():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        out = os.path.join(tmp, "graph.json")

        exported = store.export_object_graph(out)
        assert exported["written"] is True, exported
        assert exported["bytes"] > 0
        assert Path(out).exists()

        loaded = store.load_object_graph(out)
        assert loaded["valid"] is True, loaded["gaps"]
        assert loaded["gaps"] == []
        assert loaded["n_nodes"] == exported["n_nodes"]
        assert loaded["n_edges"] == exported["n_edges"]
        assert loaded["format"] == LocalMemoryStore.OBJECT_GRAPH_FORMAT


def test_load_rejects_a_foreign_format_tag():
    """The version tag is the whole point of a format: it must be checked."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        out = os.path.join(tmp, "foreign.json")
        Path(out).write_text(json.dumps({
            "format": "objectgraph/99", "nodes": [], "edges": [],
        }), encoding="utf-8")

        loaded = store.load_object_graph(out)

        assert loaded["valid"] is False
        assert any(g.startswith("unknown-format:") for g in loaded["gaps"])


def test_load_reports_a_dangling_edge_and_malformed_collections():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        out = os.path.join(tmp, "broken.json")
        Path(out).write_text(json.dumps({
            "format": LocalMemoryStore.OBJECT_GRAPH_FORMAT,
            "nodes": [{"id": "pattern:1", "kind": "pattern"}],
            "edges": [{"from": "pattern:1", "to": "agent:missing",
                       "rel": "authored_by"}],
        }), encoding="utf-8")

        loaded = store.load_object_graph(out)

        assert loaded["valid"] is False
        assert any(g.startswith("dangling-edge:") for g in loaded["gaps"])

    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        out = os.path.join(tmp, "notlists.json")
        Path(out).write_text(json.dumps({
            "format": LocalMemoryStore.OBJECT_GRAPH_FORMAT,
            "nodes": "nope", "edges": 7,
        }), encoding="utf-8")

        loaded = store.load_object_graph(out)

        assert loaded["valid"] is False
        assert "nodes-not-a-list" in loaded["gaps"]
        assert "edges-not-a-list" in loaded["gaps"]


def test_unreadable_input_is_reported_not_raised():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        loaded = store.load_object_graph(os.path.join(tmp, "does_not_exist.json"))

        assert loaded["valid"] is False
        assert any(g.startswith("unreadable:") for g in loaded["gaps"])


def test_export_to_an_unwritable_path_fails_open():
    """A snapshot must never be able to break the session producing it."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        unwritable = os.path.join(tmp, "no_such_dir", "graph.json")

        exported = store.export_object_graph(unwritable)

        assert exported["written"] is False
        assert any(g.startswith("export-failed:") for g in exported["gaps"])
        assert not Path(unwritable).exists()


# -- the production anchor ---------------------------------------------------------

def test_production_anchor_exposes_the_typed_layer():
    """`get_knowledge_graph_stats` is what production actually calls.

    So the typed layer is only real if it is visible there -- and the flat keys
    the existing callers read must be unchanged by its arrival.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)

        stats = store.get_knowledge_graph_stats()

        # The pre-existing contract, unchanged.
        assert stats["total_patterns"] == 3
        assert stats["by_agent"] == {"agent_A": 2, "agent_B": 1}
        assert stats["by_type"] == {"anomaly": 2, "preference": 1}
        assert stats["avg_confidence"] > 0.0

        # The typed layer, counted from the same table.
        og = stats["object_graph"]
        assert og["typed"] is True
        assert og["format"] == LocalMemoryStore.OBJECT_GRAPH_FORMAT
        assert og["n_pattern_nodes"] == 3
        assert og["n_agent_nodes"] == 2
        assert og["n_type_nodes"] == 2
        assert og["n_nodes"] == 7
        assert og["n_edges"] == 6

        # The counts agree with what the full document actually contains, so the
        # cheap aggregate path cannot drift from the materialised one.
        doc = store.object_graph_document()
        assert og["n_nodes"] == doc["n_nodes"]
        assert og["n_edges"] == doc["n_edges"]


def test_document_graph_stats_reports_the_built_layer():
    """The map's named method for this row must not still call the layer absent.

    `document_graph_stats` reported `typed_nodes: False` and
    "typed node/edge layer is a new build (L), not a wiring" while the layer
    exists in this same module. A flag that says MISSING next to a built
    implementation is worse than no flag: it is read as a measurement.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        report = store.document_graph_stats()

        assert report["verified"] is True
        assert report["typed_nodes"] is True, report
        assert report["typed_edges"] is True, report
        assert report["gap"] == "", report
        # The report is the anchor's own numbers, not a second computation.
        anchor = store.get_knowledge_graph_stats()["object_graph"]
        assert report["n_nodes"] == anchor["n_nodes"]
        assert report["n_edges"] == anchor["n_edges"]
        # ...and they match the materialised document too.
        doc = store.object_graph_document()
        assert report["n_nodes"] == doc["n_nodes"]
        assert report["n_edges"] == doc["n_edges"]


# -- the two graph properties the traversal tests above do not reach -----------------

#: MEASURED 2026-10-11 (worker 5, EP206). The traversal tests in
#: `tests/test_local_memory_sota_rows.py` all exercise a PATH -- `a-b-c` over one
#: shared `correlation_id`. The published edge set is still `zip(ordered,
#: ordered[1:])` per key, so a path never closes. Nothing pinned what happens when
#: the edges DO close, and `traverse`'s docstring claims both bounds are enforced
#: without a test for either. These two tests close that gap. No change was needed
#: in `CORE/local_memory.py`: the row's implementation already exists (see the
#: stale-row note in the module docstring above) and the properties below hold.


def test_traversal_terminates_on_a_cycle_and_visits_each_node_once():
    """A closed edge set must terminate, and no node may be visited twice.

    A cycle needs TWO keys to close. The store's default `session_id` is shared by
    every row written to one store, so the session chain supplies `1-2` and `2-3`;
    one shared `correlation_id` on the first and last row supplies `3-1`. A
    traversal without a `seen` set seeded with `start_id` re-walks that ring until
    `depth` runs out, so the assertion is not merely "it returns" -- it returns
    each reachable node EXACTLY once and excludes the start node.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalMemoryStore(db_path=os.path.join(tmp, "cycle.db"))
        # Only the first and last row share a correlation_id; all three share the
        # store's session, which is what the session edge chain is derived from.
        first = store.record_event("worker", "cycle", {"p": 1},
                                  correlation_id="ring")
        second = store.record_event("worker", "cycle", {"p": 2})
        third = store.record_event("worker", "cycle", {"p": 3},
                                   correlation_id="ring")

        graph = store.object_graph(limit=50)
        published = {(e["type"], e["from"], e["to"]) for e in graph["edges"]}
        # The triangle is a precondition, not an assumption: if the session column
        # were not projected the failure prints the edges that WERE published.
        assert ("correlation", first, third) in published, published
        assert ("session", first, second) in published, published
        assert ("session", second, third) in published, published

        # `depth=6` is far past the cycle length (3), so a `seen` set that omitted
        # the start node would revisit the ring and blow past these two rows.
        reached = store.traverse(first, depth=6, limit=50)

        ids = [r["id"] for r in reached]
        assert sorted(ids) == sorted([second, third]), (
            "a cycle must terminate after reaching each node once: %r" % (ids,))
        assert len(ids) == len(set(ids)), "a cycle revisited a node: %r" % (ids,)
        assert first not in ids, "the start node must not be its own result"
        # Both arriving edge kinds are reported, not just the first one found.
        assert {r["_edge"] for r in reached} == {"session", "correlation"}


def test_traversal_limit_is_a_cap_and_can_return_fewer_than_it():
    """`limit` is enforced, and it also CLIPS the group page it reads adjacency from.

    Two behaviours, both measured here rather than assumed:

    1. `limit` bounds the result -- a traversal never returns more than it.
    2. A small `limit` returns FEWER rows than the cap, because `_graph_peers`
       fetches the adjacency page with `LIMIT limit` as well: the page is clipped
       to the cap, so a node's successor can fall outside it and the neighbourhood
       reads empty. `_graph_peers`' docstring states this truncation deliberately
       ("a node absent from the fetched group ... yields [] ... recorded rather
       than returned as a confident empty neighbourhood"), so it is pinned as
       observed behaviour, NOT corrected here.

    Consequence worth knowing before changing it: `limit=1` returns nothing at all
    even when neighbours exist, because the page holds only the node itself and has
    neither a predecessor nor a successor in view.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalMemoryStore(db_path=os.path.join(tmp, "bound.db"))
        root = store.record_event("worker", "bound", {"p": 0}, correlation_id="ring")
        for i in range(1, 5):
            store.record_event("worker", "bound", {"p": i}, correlation_id="ring")

        # The chain is 1-2-3-4-5, so four rows are reachable from the root.
        unbounded = store.traverse(root, edge_types=["correlation"], depth=5, limit=50)
        assert len(unbounded) == 4, [r["id"] for r in unbounded]

        bounded = store.traverse(root, edge_types=["correlation"], depth=5, limit=2)
        assert len(bounded) <= 2, [r["id"] for r in bounded]
        assert len(bounded) < len(unbounded), (len(bounded), len(unbounded))

        # The documented consequence of (2), pinned so it cannot become silent.
        assert store.traverse(root, edge_types=["correlation"], depth=5,
                              limit=1) == []


def test_a_valid_empty_graph_is_distinguishable_from_a_failed_read():
    """An empty graph and an unreadable one must not answer the same.

    Both report `n_nodes == 0`, so the distinction has to be carried by
    `valid`/`gaps` -- otherwise a caller cannot tell "no rows yet" from "the file
    is broken", and a snapshot that failed to write looks like a store that is
    genuinely empty.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalMemoryStore(db_path=os.path.join(tmp, "empty.db"))
        out = os.path.join(tmp, "empty_graph.json")

        # A store with no patterns exports a well-formed document with no nodes.
        exported = store.export_object_graph(out)
        assert exported["written"] is True, exported
        assert exported["n_nodes"] == 0 and exported["n_edges"] == 0

        empty = store.load_object_graph(out)
        assert empty["valid"] is True and empty["gaps"] == [], empty["gaps"]
        assert empty["n_nodes"] == 0 and empty["nodes"] == []

        failed = store.load_object_graph(os.path.join(tmp, "absent.json"))
        assert failed["valid"] is False and failed["gaps"], failed
        # Same count, opposite verdict: the distinction is `valid`, not a number.
        assert failed["n_nodes"] == empty["n_nodes"]
        assert failed["valid"] != empty["valid"]
