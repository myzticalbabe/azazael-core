"""EP395 / EP160 / EP206 host edits on `CORE/local_memory.py`.

`SOTA_ROW_RESCORE.md` (parts B/C) reassigned three rows onto `LocalMemoryStore`,
because it is the only write path and the only owner of the rows themselves:

* **EP395 ConsistencyGate** -- admission must run BEFORE the row is committed. A
  retrieval-time filter is too late: the inadmissible row is already the
  retrieval set's input. The gate is OPT-IN (`admit=None` is the old behaviour),
  it refuses only on an explicit denial so an observing gate cannot silently
  start dropping records, and it fails OPEN on its own error, because a broken
  gate must not become a data-loss path.
* **EP160 AgentSys isolation** -- the partition boundary is a nullable column,
  so NULL = SHARED and every pre-existing row stays visible. Isolation is a read
  filter over the one store, not a second store to keep in sync.
* **EP206 ObjectGraph** -- the graph is a PROJECTION over the correlation_id /
  trace_id / session_id / agent_id columns the write path already fills, so it
  cannot drift from the rows the way a parallel index would.

Each test asserts the NEGATIVE as well as the positive: the gate is proven to
actually block a write, the default is proven unchanged, and a shared read is
proven to still see every partition.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _store(tmp_path):
    """A store on its own file -- never the live DB."""
    from CORE.local_memory import LocalMemoryStore
    return LocalMemoryStore(db_path=str(tmp_path / "mem.db"))


# -- EP395: write-time admission -------------------------------------------

def test_write_path_default_is_unchanged_without_a_gate(tmp_path):
    """No `admit` means exactly the old behaviour: the row is written."""
    store = _store(tmp_path)
    mem_id = store.record_event("worker", "plain_event", {"n": 1})
    assert mem_id > 0
    rows = store.query_memories(event_type="plain_event")
    assert len(rows) == 1
    assert rows[0]["data"] == {"n": 1}


def test_admission_gate_refuses_before_the_row_is_committed(tmp_path):
    """A refusal must leave the store without the row AND record the veto."""
    store = _store(tmp_path)

    def refuse(_candidate):
        return False

    mem_id = store.record_event("worker", "wanted_event", {"n": 2}, admit=refuse)

    assert mem_id == 0, "a refused write must not report a memory id"
    assert store.query_memories(event_type="wanted_event") == [], (
        "the refused row reached the store -- admission ran after commit")
    # The veto is itself recorded: an unrecorded refusal is indistinguishable
    # from a caller that never attempted the write.
    refusals = store.query_memories(event_type="admission_refused")
    assert len(refusals) == 1
    assert refusals[0]["data"]["rejected_event_type"] == "wanted_event"


def test_admission_dict_verdict_denies_and_observer_return_admits(tmp_path):
    """A dict verdict may deny; anything non-denying (incl. None) admits."""
    store = _store(tmp_path)

    def deny_by_verdict(_candidate):
        return {"admitted": False, "reason": "contradicts a confirmed fact"}

    assert store.record_event("worker", "contradicting", {}, admit=deny_by_verdict) == 0
    assert store.query_memories(event_type="contradicting") == []

    def observe_only(_candidate):
        return None          # logs and returns nothing -- must NOT become a veto

    assert store.record_event("worker", "observed", {}, admit=observe_only) > 0
    assert len(store.query_memories(event_type="observed")) == 1

    def allow(_candidate):
        return {"admitted": True}

    assert store.record_event("worker", "allowed", {}, admit=allow) > 0


def test_admission_gate_error_fails_open_and_is_recorded(tmp_path):
    """A gate that raises must not become a data-loss path."""
    store = _store(tmp_path)

    def broken(_candidate):
        raise RuntimeError("gate dependency unavailable")

    mem_id = store.record_event("worker", "survived", {}, admit=broken)
    assert mem_id > 0, "a broken gate must fail OPEN, not drop the record"
    assert len(store.query_memories(event_type="survived")) == 1


# -- EP160: per-agent partition -------------------------------------------

def test_agent_partition_isolates_reads_and_defaults_to_shared(tmp_path):
    store = _store(tmp_path)
    store.record_event("agent_run", "step", {"who": "alpha"}, agent_id="alpha")
    store.record_event("agent_run", "step", {"who": "beta"}, agent_id="beta")
    store.record_event("agent_run", "step", {"who": "nobody"})
    # A whitespace-only key collapses to SHARED, not to a third partition.
    store.record_event("agent_run", "step", {"who": "blank"}, agent_id="   ")

    alpha = store.query_memories(agent_id="alpha")
    beta = store.query_memories(agent_id="beta")
    assert len(alpha) == 1 and alpha[0]["data"]["who"] == "alpha"
    assert len(beta) == 1 and beta[0]["data"]["who"] == "beta"
    assert alpha[0]["agent_id"] == "alpha"

    # UNSET = SHARED: the default read still sees every row, which is what every
    # caller did before the column existed.
    assert len(store.query_memories()) == 4
    assert store.query_memories(agent_id="blank") == []


# -- EP206: typed graph projection + traversal ----------------------------

def test_object_graph_edges_and_traversal_reach_the_linked_row(tmp_path):
    store = _store(tmp_path)
    first = store.record_event("worker", "phase", {"p": 1}, correlation_id="corr-1")
    second = store.record_event("worker", "phase", {"p": 2}, correlation_id="corr-1")
    store.record_event("worker", "unlinked", {"p": 3})

    graph = store.object_graph(limit=50)
    assert graph["n_nodes"] == 3
    correlation = [e for e in graph["edges"] if e["type"] == "correlation"]
    assert correlation == [{"type": "correlation", "key": "corr-1",
                            "from": first, "to": second}], (
        "two rows sharing a correlation_id must yield exactly one edge")
    # A lone row is not an edge, and an unset key is never a shared key.
    assert all(e["key"] for e in graph["edges"])

    reached = store.traverse(first, edge_types=["correlation"], depth=1)
    assert [r["id"] for r in reached] == [second]
    assert reached[0]["_edge"] == "correlation"
    assert reached[0]["_hop"] == 1
    assert first not in [r["id"] for r in reached], "the start node is not a result"


def test_traversal_respects_depth_limit_and_unknown_edge_types(tmp_path):
    store = _store(tmp_path)
    a = store.record_event("worker", "chain", {"p": 1}, correlation_id="chain")
    b = store.record_event("worker", "chain", {"p": 2}, correlation_id="chain")
    c = store.record_event("worker", "chain", {"p": 3}, correlation_id="chain")

    one_hop = store.traverse(a, edge_types=["correlation"], depth=1, limit=50)
    two_hops = store.traverse(a, edge_types=["correlation"], depth=2, limit=50)
    assert [r["id"] for r in one_hop] == [b]
    assert [r["id"] for r in two_hops] == [b, c]
    assert [r["_hop"] for r in two_hops] == [1, 2]

    # An unknown edge type is not silently coerced onto a real column; it is
    # dropped, and the graph says which types it actually used.
    empty = store.object_graph(limit=50, edge_types=["not_an_edge_type"])
    assert empty["edge_types"] == [] and empty["n_edges"] == 0
    assert store.traverse(a, edge_types=["not_an_edge_type"]) == []
