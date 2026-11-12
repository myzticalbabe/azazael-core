"""Tests for static codebase graph extraction."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.codebase_graph import build_graph
from CORE.graph_artifacts import determine_gaps, runtime_overlay


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "sample"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("from .a import value\n", encoding="utf-8")
    (root / "pkg" / "a.py").write_text("from pkg.b import value\nvalue = 1\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text("from pkg.a import value\nvalue = 2\n", encoding="utf-8")
    return root


def test_extracts_modules_edges_and_cycles(tmp_path: Path):
    graph = build_graph(_fixture(tmp_path))
    assert "pkg.a" in graph.nodes
    assert "pkg.b" in graph.nodes
    assert any(edge.source == "pkg.a" and edge.target == "pkg.b" for edge in graph.edges)
    assert any(set(cycle) == {"pkg.a", "pkg.b"} for cycle in graph.cycles())


def test_exports_are_structured_and_escaped(tmp_path: Path):
    graph = build_graph(_fixture(tmp_path))
    payload = json.loads(graph.to_json())
    assert payload["schema_version"] == 1
    assert "flowchart LR" in graph.to_mermaid()
    assert "digraph Codebase" in graph.to_dot()
    assert "mindmap" in graph.to_mermaid_mindmap()
    assert "graphml" in graph.to_graphml()
    assert "gexf" in graph.to_gexf()
    assert '"elements"' in graph.to_cytoscape_json()
    assert graph.to_csv().splitlines()[0] == "source,target,kind"
    assert "<svg" in graph.to_svg()
    assert "Canonical graph JSON" in graph.to_html()


def test_snapshot_diff_reports_changes(tmp_path: Path):
    root = _fixture(tmp_path)
    previous = build_graph(root).to_dict()
    (root / "pkg" / "c.py").write_text("value = 3\n", encoding="utf-8")
    current = build_graph(root)
    diff = current.snapshot_diff(previous)
    assert any(item["node_id"] == "pkg.c" for item in diff["added_nodes"])
    assert diff["removed_nodes"] == []


def test_architecture_edges_and_gap_report(tmp_path: Path):
    graph = build_graph(_fixture(tmp_path))
    assert any(edge.kind == "contains" for edge in graph.edges)
    report = determine_gaps(graph)
    assert report["summary"]["cycles"] == 1


def test_runtime_overlay_is_safe():
    nodes, edges, warnings = runtime_overlay()
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    assert isinstance(warnings, list)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory))
        graph = build_graph(fixture)
        assert graph.cycles()
        print("codebase graph tests passed")
