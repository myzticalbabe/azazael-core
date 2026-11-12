# coding: utf-8
"""Tests for tools/graph_semantic/graphify_patch.py.

Locks the corrected ranking contract: a document/injected-concept node must not
appear in the god-node ranking even when it has the highest degree, while a real
code node still does. Also checks that the upstream source patch is well-formed
and idempotent.
"""
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH_MOD = REPO_ROOT / "tools" / "graph_semantic" / "graphify_patch.py"


def _load():
    spec = importlib.util.spec_from_file_location("gs_graphify_patch", PATCH_MOD)
    if spec is None or spec.loader is None:  # pragma: no cover
        pytest.skip("cannot load graphify_patch")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:  # networkx/graphify absent
        pytest.skip(f"dependency missing: {exc}")
    return mod


def _graph():
    import networkx as nx
    G = nx.Graph()
    # A backlog document with many edges — the false hub.
    G.add_node("catchall", label="Catch-All Refactoring Backlog", file_type="document",
               source_file="CATCHALL_BACKLOG.md")
    # A real code abstraction.
    G.add_node("dm", label="DirectMemory", file_type="code",
               source_file="src/CORE/direct_memory.py")
    # A short-name test helper.
    G.add_node("f", label="f()", file_type="code", source_file="tests/test_x.py")
    for i in range(50):
        G.add_node(f"n{i}", label=f"node{i}", file_type="code", source_file="m.py")
        G.add_edge("catchall", f"n{i}")
    for i in range(20):
        G.add_edge("dm", f"n{i}")
    return G


def test_document_hub_is_excluded_from_ranking():
    mod = _load()
    if mod._analyze is None:
        pytest.skip("graphify not importable")
    ranking = mod.god_nodes_fixed(_graph(), top_n=5)
    labels = [n["label"] for n in ranking]
    assert "Catch-All Refactoring Backlog" not in labels
    assert "DirectMemory" in labels


def test_predicate_flags_docs_stdlib_and_short_names():
    mod = _load()
    g = _graph()
    assert mod.is_doc_or_injected_hub(g, "catchall") is True
    assert mod.is_doc_or_injected_hub(g, "f") is True
    assert mod.is_doc_or_injected_hub(g, "dm") is False


def test_install_replaces_graphify_god_nodes():
    mod = _load()
    if mod._analyze is None:
        pytest.skip("graphify not importable")
    original = mod._analyze.god_nodes
    try:
        assert mod.install() is True
        assert mod._analyze.god_nodes is mod.god_nodes_fixed
    finally:
        mod._analyze.god_nodes = original


def test_patched_source_inserts_helper_and_is_idempotent():
    mod = _load()
    src = (
        "def god_nodes(G: nx.Graph, top_n: int = 10):\n"
        "    for node_id in G:\n"
        "        if _is_file_node(G, node_id) or _is_concept_node(G, node_id) "
        "or _is_json_key_node(G, node_id):\n"
        "            continue\n"
        "        yield node_id\n"
    )
    patched = mod._patched_source(src)
    assert "_is_doc_or_injected_hub" in patched
    assert "if _is_doc_or_injected_hub(G, node_id):" in patched
    # Idempotent: patching an already-patched source is a no-op.
    assert mod._patched_source(patched) == patched
    # Still valid Python.
    compile(patched, "analyze.py", "exec")
