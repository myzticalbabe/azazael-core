"""Graph artifacts, runtime overlays, and gap analysis."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, List

from CORE.codebase_graph import CodebaseGraph, GraphEdge, GraphNode, MapConsultation, build_graph

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def runtime_overlay() -> tuple[list[GraphNode], list[GraphEdge], list[str]]:
    """Best-effort overlay of registered worms and Nature routes."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    warnings: list[str] = []
    try:
        from CORE.worm_registry import WORM_REGISTRY, discover_worms

        discover_worms()
        for name, info in WORM_REGISTRY.items():
            category = str(info.get("category", "unknown"))
            category_id = f"runtime:nature:{category}"
            nodes.append(GraphNode(category_id, category, "runtime_nature", metadata={"category": category}))
            node_id = f"runtime:worm:{name}"
            nodes.append(GraphNode(node_id, name, "runtime_worm", metadata={"category": category, "priority": info.get("priority")}))
            edges.append(GraphEdge(category_id, node_id, "registry"))
    except Exception as exc:
        warnings.append(f"runtime registry unavailable: {exc}")

    # U3 surgery: the nature-routing overlay is deleted, not ported.
    # feeds_into chains described taxonomy routing (NATURE_DEFINITIONS);
    # D1 removes the taxonomy, so there is nothing to route between and no
    # edges to draw. The registry block above already carries the flat
    # category -> worm membership. If routing edges return one day they must
    # come from a live dispatch table, never a static taxonomy map.
    return _unique_nodes(nodes), _unique_edges(edges), warnings


def _unique_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    return list({node.node_id: node for node in nodes}.values())


def _unique_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    return list(set(edges))


def generate_artifacts(root: Path | str, output_dir: Path | str, include_runtime: bool = True, include_tests: bool = False) -> dict[str, Any]:
    """Generate JSON, Mermaid, DOT, HTML, XML, SVG, CSV, and gap artifacts."""
    graph = build_graph(root, include_tests=include_tests)
    runtime_warnings: list[str] = []
    if include_runtime:
        nodes, edges, runtime_warnings = runtime_overlay()
        graph.add_overlay(nodes, edges)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    files = {
        "json": output / "codebase_graph.json",
        "mermaid": output / "codebase_graph.mmd",
        "dot": output / "codebase_graph.dot",
        "html": output / "codebase_graph.html",
        "mindmap": output / "codebase_mindmap.mmd",
        "graphml": output / "codebase_graph.graphml",
        "gexf": output / "codebase_graph.gexf",
        "cytoscape": output / "codebase_graph_cytoscape.json",
        "csv": output / "codebase_graph_edges.csv",
        "svg": output / "codebase_graph.svg",
        "gaps": output / "codebase_graph_gaps.json",
    }
    files["json"].write_text(graph.to_json(), encoding="utf-8")
    files["mermaid"].write_text(graph.to_mermaid(), encoding="utf-8")
    files["dot"].write_text(graph.to_dot(), encoding="utf-8")
    files["html"].write_text(graph.to_html(), encoding="utf-8")
    files["mindmap"].write_text(graph.to_mermaid_mindmap(), encoding="utf-8")
    files["graphml"].write_text(graph.to_graphml(), encoding="utf-8")
    files["gexf"].write_text(graph.to_gexf(), encoding="utf-8")
    files["cytoscape"].write_text(graph.to_cytoscape_json(), encoding="utf-8")
    files["csv"].write_text(graph.to_csv(), encoding="utf-8")
    files["svg"].write_text(graph.to_svg(), encoding="utf-8")
    gaps = determine_gaps(graph, runtime_warnings)
    files["gaps"].write_text(json.dumps(gaps, indent=2, sort_keys=True), encoding="utf-8")
    return {"generated_at": datetime.now().isoformat(), "files": {key: str(value) for key, value in files.items()}, "gaps": gaps}


def _atlas_file_for(atlas: Any, consultation: MapConsultation) -> Any | None:
    """The atlas record for a consulted node's path, or None."""
    if not consultation.path:
        return None
    suffix = "/" + consultation.path
    for record in getattr(atlas, "files", ()) or ():
        candidate = getattr(record, "path", "")
        if candidate == consultation.path or candidate.endswith(suffix):
            return record
    return None


def consult_map(target: str, graph: CodebaseGraph | None = None, root: Path | str | None = None,
                include_tests: bool = False, atlas: Any = None) -> MapConsultation:
    """Query the map for one target ahead of acting on it (EP237, act-time stage).

    ``graph`` is preferred: a caller running many actions builds it once. Passing
    only ``root`` builds a fresh graph for this single consultation. ``atlas`` is
    optional and only annotates the result, so asking a consultation to include
    atlas provenance never triggers a second project scan.
    """
    if graph is None:
        graph = build_graph(root if root is not None else Path.cwd(), include_tests=include_tests)
    consultation = graph.consult(target)
    if atlas is not None:
        record = _atlas_file_for(atlas, consultation)
        if record is None:
            consultation.notes.append("atlas carries no record of this path")
        else:
            consultation.notes.append("atlas: %s (%s, %d bytes)" % (
                getattr(record, "classification", "unknown"),
                "readable" if getattr(record, "readable", False) else "unreadable",
                getattr(record, "size", 0),
            ))
    return consultation


class MapBeforeAct:
    """L4 act-time stage: consult the cognitive map before acting on a target.

    Additive and opt-in. Constructing the stage builds nothing until the first
    ``consult``, and a caller that never constructs one sees the substrate's
    behaviour unchanged -- the graph, the atlas and the drift check are untouched.
    The graph is built once and reused, so the map cost falls at the start of a
    run instead of per action, and every consultation can be recorded where the
    run's evidence lives.
    """

    def __init__(self, root: Path | str, include_tests: bool = False, atlas: Any = None,
                 record_to: Path | str | Any | None = None) -> None:
        self.root = Path(root)
        self.include_tests = include_tests
        self.atlas = atlas
        self.record_to = record_to
        self._graph: CodebaseGraph | None = None
        self.consultations: list[MapConsultation] = []

    @property
    def graph(self) -> CodebaseGraph:
        """The map, built on first use and reused for every later consultation."""
        if self._graph is None:
            self._graph = build_graph(self.root, include_tests=self.include_tests)
        return self._graph

    def consult(self, target: str) -> MapConsultation:
        """Consult the map for ``target``, record it, and return the answer."""
        consultation = consult_map(target, graph=self.graph, atlas=self.atlas)
        self.consultations.append(consultation)
        self.record(consultation)
        return consultation

    def record(self, consultation: MapConsultation) -> None:
        """Record a consultation: to a callable, to a JSONL path, or nowhere.

        ``record_to=None`` writes nothing -- the default. A callable receives the
        dict; anything else is treated as a path and appended as one JSONL line,
        so a run's consultations outlive the process without a new global store.
        """
        destination = self.record_to
        if destination is None:
            return
        payload = consultation.to_dict()
        if callable(destination):
            destination(payload)
            return
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def determine_gaps(graph: CodebaseGraph, runtime_warnings: list[str] | None = None) -> dict[str, Any]:
    """Identify graph-specific development gaps from observed graph data."""
    runtime_warnings = runtime_warnings or []
    node_ids = set(graph.nodes)
    import_edges = [edge for edge in graph.edges if edge.kind == "imports"]
    directory_edges = [edge for edge in graph.edges if edge.kind == "contains"]
    runtime_nodes = [node for node in graph.nodes.values() if node.kind.startswith("runtime")]
    gaps: list[dict[str, Any]] = []
    if graph.parse_errors:
        gaps.append({"id": "parse-errors", "severity": "high", "count": len(graph.parse_errors), "action": "Repair or explicitly exclude files that cannot be parsed."})
    if graph.cycles():
        gaps.append({"id": "import-cycles", "severity": "high", "count": len(graph.cycles()), "action": "Review strongly connected components and define dependency direction."})
    if not directory_edges:
        gaps.append({"id": "architecture-containment", "severity": "medium", "action": "Add package/directory containment edges."})
    if not runtime_nodes:
        gaps.append({"id": "runtime-overlay", "severity": "medium", "action": "Make runtime registry imports available for overlay generation."})
    if not import_edges:
        gaps.append({"id": "import-edges", "severity": "high", "action": "Check source root and module resolution configuration."})
    if runtime_warnings:
        gaps.append({"id": "runtime-warnings", "severity": "low", "count": len(runtime_warnings), "action": "Review runtime overlay warnings.", "details": runtime_warnings})
    return {
        "schema_version": 1,
        "summary": {"nodes": len(node_ids), "edges": len(graph.edges), "cycles": len(graph.cycles()), "parse_errors": len(graph.parse_errors), "gap_count": len(gaps)},
        "gaps": gaps,
    }
