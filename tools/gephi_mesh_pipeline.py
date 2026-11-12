#!/usr/bin/env python3
# coding: utf-8
"""
gephi_mesh_pipeline.py — Export the Azazael neural mesh to Gephi (.gexf)

Generates a rich graph with:
  - Nature nodes (35 categories) as large colored hubs
  - Worm nodes (226 registered) sized by priority, colored by category
  - Edge types: feeds_into (nature→nature), Q-weight (worm→worm), reflex (scar), quarantine, dream
  - Dynamic attributes: Q-weight values, spawn counts, cooldown, fail rates

Usage:
    python tools/gephi_mesh_pipeline.py                    # full export
    python tools/gephi_mesh_pipeline.py --json             # also dump raw JSON
    python tools/gephi_mesh_pipeline.py --svg              # also render SVG via graphviz
    python tools/gephi_mesh_pipeline.py --html             # also render interactive HTML

Output: azazael/graphify-out/mesh_gephi.gexf (+ optional .json, .svg, .html)
"""

import json
import sys
import os
import time
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Any

# ── Bootstrap paths ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The vis.js bundle is vendored, and the interactive view links to it instead
#: of to a CDN. The CDN version made the artifact unverifiable *offline*: with
#: no route to unpkg the page dies with `ReferenceError: vis is not defined`
#: and the graph never initialises, so `tools/verify_mesh_html.py` reported the
#: correct FAIL for a defect that was the artifact's, not the machine's. A
#: generated file that only works when the network is up is not an artifact.
VIS_BUNDLE = PROJECT_ROOT / "lib" / "vis-9.1.2" / "vis-network.min.js"


def vis_bundle_href(out_dir: Path) -> str:
    """Document-relative href to the vendored vis.js bundle.

    Raises rather than falling back to the CDN: a missing bundle is a packaging
    defect, and a silent fallback would make the next offline run fail exactly
    where this one did, with the cause further away.
    """
    if not VIS_BUNDLE.exists():
        raise FileNotFoundError(
            f"vendored vis.js bundle missing: {VIS_BUNDLE} -- the interactive "
            "view cannot initialise or be verified without it"
        )
    return Path(os.path.relpath(VIS_BUNDLE, out_dir)).as_posix()
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ── Color palette for natures ────────────────────────────────────
NATURE_COLORS = {
    "monitor": "#4ECDC4",
    "diagnostic": "#45B7D1",
    "analysis": "#96CEB4",
    "defense": "#FF6B6B",
    "optimization": "#FFEAA7",
    "orchestration": "#DDA0DD",
    "sync": "#98D8C8",
    "repair": "#F7DC6F",
    "security": "#E74C3C",
    "profiling": "#3498DB",
    "telemetry": "#1ABC9C",
    "container": "#8E44AD",
    "config": "#F39C12",
    "backup": "#2ECC71",
    "migration": "#E67E22",
    "communication": "#9B59B6",
    "learning": "#2980B9",
    "visualization": "#16A085",
    "mesh": "#C0392B",
    "synapse": "#8E44AD",
    "consensus": "#D35400",
    "engram": "#27AE60",
    "federated": "#2980B9",
    "servemesh": "#1ABC9C",
    "immune": "#E74C3C",
    "xenomorph": "#2C3E50",
}

# ── GEXF helpers ─────────────────────────────────────────────────

def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _gexf_header(title: str, dt: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<gexf xmlns="http://www.gexf.net/1.3draft"
      xmlns:viz="http://www.gexf.net/1.3draft/viz"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xsi:schemaLocation="http://www.gexf.net/1.3draft http://www.gexf.net/1.3draft/gexf.xsd"
      version="1.3">
  <meta lastmodifieddate="{dt}">
    <creator>Azazael Neural Mesh → Gephi Pipeline</creator>
    <description>Neural mesh architecture graph for Azazael ecosystem</description>
  </meta>
  <graph defaultedgetype="directed" mode="static" name="{_xml_escape(title)}">
    <nodes>
'''


def _gexf_footer() -> str:
    return '''    </nodes>
    <edges>
''' + '    </edges>\n  </graph>\n</gexf>\n'


# ── Data collection ──────────────────────────────────────────────

def collect_nature_definitions() -> dict:
    """Load NATURE_DEFINITIONS from NATURE_CLUSTERS.py"""
    try:
        from CORE.WORMS.NATURE_CLUSTERS import NATURE_DEFINITIONS
        return NATURE_DEFINITIONS
    except ImportError:
        return {}


def collect_registry() -> dict:
    """Load WORM_REGISTRY from worm_registry.py"""
    try:
        from CORE.worm_registry import WORM_REGISTRY, discover_worms
        discover_worms()
        return WORM_REGISTRY
    except ImportError:
        return {}


def collect_spine_data() -> dict:
    """Load Q-weights, scars, homeostat from spine_registry"""
    data = {"q_weights": {}, "scars": {}, "homeostat": {}}
    try:
        from CORE import spine_registry as sp
        data["q_weights"] = sp.get_q_weights()
        data["scars"] = sp.get_scars()
        data["homeostat"] = sp.get_homeostat_view()
    except ImportError:
        pass
    return data


def collect_nature_edges(natures: dict) -> list:
    """Extract feeds_into edges from NATURE_DEFINITIONS"""
    edges = []
    for nature, defn in natures.items():
        for target in (defn.get("feeds_into") or []):
            if target in natures:
                edges.append((nature, target, "feeds_into", 1.0))
    return edges


def collect_q_edges(spine: dict) -> list:
    """Extract Q-weight edges between a nature hub and its worm.

    Only ``<nature>:<worm>`` ledger keys are handled here — the homeostat /
    brainstem shape, whose endpoints are real nodes (``nature:<nature>`` is
    a hub, ``<worm>`` is registered). See ``collect_route_edges`` for the
    other key shape.

    NOTE (2026-09-14): this function used to run the generic ``split(":", 1)``
    over *every* key, so a learned route key ``route:windows>mesh`` became an
    edge from ``nature:route`` to ``windows>mesh`` — **neither of which is a
    node**. vis.js drops edges whose endpoints do not exist, silently, so the
    route telemetry the mesh began learning today was invisible in the only
    view that draws it. Keys that are not ``<nature>:<worm>`` are now skipped
    here and drawn by the route collector instead; ``build_gexf``/``dump_json``
    additionally drop any edge whose endpoints are unknown, so a future key
    shape can never re-introduce a silent hole.
    """
    edges = []
    qw = spine.get("q_weights", {})
    for key, weight in qw.items():
        if key.startswith("route:"):
            continue
        parts = key.split(":", 1)
        if len(parts) == 2:
            nature, worm = parts
            # Only include edges with non-trivial weight
            if abs(weight) > 0.1:
                edges.append((f"nature:{nature}", worm, "q_weight", weight))
    return edges


def collect_route_edges(spine: dict, natures: dict) -> list:
    """Extract learned route edges from ``route:<src>><dst>`` ledger keys.

    Audit §7: ``MeshRouterWorm.record_route_result()`` writes route outcomes
    as ``route:<src>><dst>``, which the brainstem auto-reinforces. Those keys
    are the *only* record of which route actually fired and whether it
    worked, so dropping them leaves the graph claiming the mesh has no
    routing. ``src``/``dst`` are hops, not natures, so the route is drawn
    from a dedicated route node to the destination, and the source hop is
    recorded on the edge title.
    """
    edges = []
    for key, weight in (spine.get("q_weights", {}) or {}).items():
        if not key.startswith("route:"):
            continue
        hop = key[len("route:"):]
        src, _, dst = hop.partition(">")
        if not dst:
            continue
        target = f"nature:{dst}" if dst in natures else f"hop:{dst}"
        edges.append((f"route:{hop}", target, "route_q", float(weight)))
    return edges


def resolve_edge_endpoints(edges: list, known_ids: set) -> tuple:
    """Guarantee every edge endpoint is a node, creating placeholders.

    Every consumer in this module (GEXF, JSON, HTML) previously wrote edge
    endpoints straight through. A node that does not exist is not an error in
    GEXF and is *silently dropped* by vis.js in the browser, so a whole edge
    class could vanish from the only interactive view with nothing raised.
    This is the standing guard against that: returns ``(edges, extra_nodes, n)``
    where ``extra_nodes`` are dicts for endpoints that had no node yet, and
    ``n`` counts them so the run can report rather than hide them.
    """
    extra: Dict[str, dict] = {}
    kept = []
    for src, dst, etype, weight in edges:
        for node_id in (src, dst):
            if node_id not in known_ids and node_id not in extra:
                label = node_id.split(":", 1)[-1]
                extra[node_id] = {"id": node_id, "label": label, "kind": "implicit"}
        kept.append((src, dst, etype, weight))
    return kept, list(extra.values()), len(extra)


def collect_reflex_edges(spine: dict) -> list:
    """Extract reflex arc (scar) edges"""
    edges = []
    scars = spine.get("scars", {})
    for key, info in scars.items():
        parts = key.split(":", 1)
        if len(parts) == 2:
            nature, worm = parts
            edges.append((f"nature:{nature}", worm, "reflex_scar", -1.0))
    return edges


# ── GEXF generation ──────────────────────────────────────────────

def build_gexf(natures: dict, registry: dict, spine: dict) -> str:
    """Build complete GEXF XML string"""
    dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lines = [_gexf_header("Azazael Neural Mesh", dt)]

    # ── Nature nodes (large hubs) ──
    for nature, defn in natures.items():
        color = NATURE_COLORS.get(nature, "#808080")
        worm_count = sum(1 for name, info in registry.items()
                         if info.get("category") == nature)
        size = max(20, min(60, 15 + worm_count * 2))
        lines.append(
            f'      <node id="nature:{nature}" label="{_xml_escape(nature.upper())}" '
            f'pid="nature">\n'
            f'        <viz:size value="{size}"/>\n'
            f'        <viz:color r="{int(color[1:3],16)}" g="{int(color[3:5],16)}" '
            f'b="{int(color[5:7],16)}"/>\n'
            f'        <attvalues>\n'
            f'          <attvalue for="type" value="nature"/>\n'
            f'          <attvalue for="purpose" value="{_xml_escape(defn.get("purpose",""))}"/>\n'
            f'          <attvalue for="worm_count" value="{worm_count}"/>\n'
            f'          <attvalue for="icon" value="{defn.get("icon","")}"/>\n'
            f'        </attvalues>\n'
            f'      </node>\n'
        )

    # ── Worm nodes ──
    for name, info in registry.items():
        cat = info.get("category", "utility")
        color = NATURE_COLORS.get(cat, "#808080")
        priority = info.get("priority", 5)
        size = max(5, min(30, priority * 3))
        cooldown = info.get("cooldown_seconds", 30)
        spawn_count = info.get("spawn_count", 0)
        desc = info.get("description", "")[:120]

        lines.append(
            f'      <node id="{_xml_escape(name)}" label="{_xml_escape(name)}">\n'
            f'        <viz:size value="{size}"/>\n'
            f'        <viz:color r="{int(color[1:3],16)}" g="{int(color[3:5],16)}" '
            f'b="{int(color[5:7],16)}"/>\n'
            f'        <attvalues>\n'
            f'          <attvalue for="type" value="worm"/>\n'
            f'          <attvalue for="category" value="{_xml_escape(cat)}"/>\n'
            f'          <attvalue for="priority" value="{priority}"/>\n'
            f'          <attvalue for="cooldown_seconds" value="{cooldown}"/>\n'
            f'          <attvalue for="spawn_count" value="{spawn_count}"/>\n'
            f'          <attvalue for="description" value="{_xml_escape(desc)}"/>\n'
            f'        </attvalues>\n'
            f'      </node>\n'
        )

    lines.append('    </nodes>\n    <edges>\n')

    # ── Edges ──
    edge_id = 0

    # Nature feeds_into edges
    for src, dst, etype, weight in collect_nature_edges(natures):
        lines.append(
            f'      <edge id="{edge_id}" source="nature:{src}" target="nature:{dst}" '
            f'weight="{weight}" label="feeds_into">\n'
            f'        <attvalues>\n'
            f'          <attvalue for="edge_type" value="feeds_into"/>\n'
            f'        </attvalues>\n'
            f'      </edge>\n'
        )
        edge_id += 1

    # Nature → worm membership edges (thin)
    for name, info in registry.items():
        cat = info.get("category", "utility")
        if cat in natures:
            lines.append(
                f'      <edge id="{edge_id}" source="nature:{cat}" '
                f'target="{_xml_escape(name)}" weight="0.3" label="member_of">\n'
                f'        <attvalues>\n'
                f'          <attvalue for="edge_type" value="member_of"/>\n'
                f'        </attvalues>\n'
                f'      </edge>\n'
            )
            edge_id += 1

    # Q-weight edges — nature hubs to their worms, then learned routes.
    q_edges = collect_q_edges(spine)
    q_edges += collect_route_edges(spine, natures)
    for src, dst, etype, weight in q_edges:
        abs_w = abs(weight)
        if abs_w > 0.1:
            lines.append(
                f'      <edge id="{edge_id}" source="{_xml_escape(src)}" '
                f'target="{_xml_escape(dst)}" weight="{abs_w:.3f}" '
                f'label="Q:{weight:.2f}">\n'
                f'        <attvalues>\n'
                f'          <attvalue for="edge_type" value="q_weight"/>\n'
                f'          <attvalue for="q_value" value="{weight:.4f}"/>\n'
                f'        </attvalues>\n'
                f'      </edge>\n'
            )
            edge_id += 1

    # Reflex (scar) edges
    for src, dst, etype, weight in collect_reflex_edges(spine):
        lines.append(
            f'      <edge id="{edge_id}" source="{_xml_escape(src)}" '
            f'target="{_xml_escape(dst)}" weight="2.0" label="REFLEX_SCAR" '
            f'color="#FF0000">\n'
            f'        <attvalues>\n'
            f'          <attvalue for="edge_type" value="reflex_scar"/>\n'
            f'        </attvalues>\n'
            f'      </edge>\n'
        )
        edge_id += 1

    lines.append(_gexf_footer())
    return "".join(lines)


# ── Optional outputs ─────────────────────────────────────────────

def dump_json(natures: dict, registry: dict, spine: dict, out_dir: Path):
    """Dump raw graph data as JSON for programmatic access"""
    nodes = []
    for nature, defn in natures.items():
        worm_count = sum(1 for n, i in registry.items() if i.get("category") == nature)
        nodes.append({
            "id": f"nature:{nature}", "type": "nature", "label": nature.upper(),
            "purpose": defn.get("purpose", ""), "worm_count": worm_count,
            "icon": defn.get("icon", ""),
        })
    for name, info in registry.items():
        nodes.append({
            "id": name, "type": "worm", "label": name,
            "category": info.get("category", ""),
            "priority": info.get("priority", 5),
            "cooldown_seconds": info.get("cooldown_seconds", 30),
            "spawn_count": info.get("spawn_count", 0),
            "description": info.get("description", ""),
        })

    edges = []
    for src, dst, etype, w in collect_nature_edges(natures):
        edges.append({"source": f"nature:{src}", "target": f"nature:{dst}",
                       "type": etype, "weight": w})
    for src, dst, etype, w in collect_q_edges(spine) + collect_route_edges(spine, natures):
        edges.append({"source": src, "target": dst, "type": etype, "weight": w})
    for src, dst, etype, w in collect_reflex_edges(spine):
        edges.append({"source": src, "target": dst, "type": etype, "weight": w})

    data = {
        "generated_at": datetime.now().isoformat(),
        "nodes": nodes, "edges": edges,
        "stats": {
            "natures": len(natures), "worms": len(registry),
            "q_weights": len(spine.get("q_weights", {})),
            "scars": len(spine.get("scars", {})),
        },
    }
    path = out_dir / "mesh_gephi.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  JSON: {path}")


def dump_svg(gexf_path: Path, out_dir: Path):
    """Render SVG via graphviz if available"""
    try:
        import subprocess
        svg_path = out_dir / "mesh_gephi.svg"
        subprocess.run(
            ["dot", "-Tsvg", str(gexf_path), "-o", str(svg_path)],
            capture_output=True, timeout=60,
        )
        if svg_path.exists():
            print(f"  SVG:  {svg_path}")
        else:
            print("  SVG:  graphviz dot not found or failed (install graphviz)")
    except Exception as e:
        print(f"  SVG:  skipped ({e})")


def dump_html(natures: dict, registry: dict, spine: dict, out_dir: Path):
    """Render interactive force-directed HTML visualization.

    Writes a machine-readable copy of the graph alongside the HTML
    (``mesh_gephi_interactive.json``) whose only job is to make the rendered
    graph *verifiable*: a browser check can then assert that every edge in
    the file resolved to a real node, instead of trusting that vis.js drew
    what was asked for.
    """
    # Build node/edge data for vis.js
    vis_nodes = []
    vis_edges = []
    eid = 0

    for nature, defn in natures.items():
        color = NATURE_COLORS.get(nature, "#808080")
        worm_count = sum(1 for n, i in registry.items() if i.get("category") == nature)
        vis_nodes.append({
            "id": f"nature:{nature}", "label": nature.upper(),
            "group": "nature", "color": color,
            "size": max(20, min(60, 15 + worm_count * 2)),
            "shape": "diamond",
        })

    for name, info in registry.items():
        cat = info.get("category", "utility")
        color = NATURE_COLORS.get(cat, "#808080")
        vis_nodes.append({
            "id": name, "label": name,
            "group": cat, "color": color,
            "size": max(5, min(25, info.get("priority", 5) * 2.5)),
            "title": info.get("description", "")[:200],
        })

    for src, dst, etype, w in collect_nature_edges(natures):
        vis_edges.append({"from": f"nature:{src}", "to": f"nature:{dst}",
                          "arrows": "to", "color": "#999", "width": 1})
    for src, dst, etype, w in collect_q_edges(spine):
        vis_edges.append({"from": src, "to": dst, "arrows": "to",
                          "color": "#4ECDC4", "width": max(0.5, abs(w) * 2),
                          "title": f"Q={w:.3f}"})
    for src, dst, etype, w in collect_route_edges(spine, natures):
        vis_edges.append({"from": src, "to": dst, "arrows": "to",
                          "color": "#FFA500" if w < 0 else "#3fb950",
                          "width": max(0.5, abs(w) * 2),
                          "title": f"ROUTE Q={w:+.3f}"})
    for src, dst, etype, w in collect_reflex_edges(spine):
        vis_edges.append({"from": src, "to": dst, "arrows": "to",
                          "color": "#FF0000", "width": 3, "dashes": True,
                          "title": "REFLEX SCAR"})

    # ── Endpoint guarantee ──
    # Anything an edge points at must exist as a node, or the browser drops
    # the edge without a word. Route nodes get their own look so learned
    # routing reads as telemetry rather than as a mystery hub.
    known = {n["id"] for n in vis_nodes}
    edges_tuples = [(e["from"], e["to"], "vis", 0.0) for e in vis_edges]
    _kept, extra_nodes, n_implicit = resolve_edge_endpoints(edges_tuples, known)
    for node in extra_nodes:
        is_route = node["id"].startswith("route:")
        vis_nodes.append({
            "id": node["id"],
            "label": node["label"],
            "group": "route" if is_route else "implicit",
            "color": "#FFA500" if is_route else "#6e7681",
            "size": 14 if is_route else 10,
            "shape": "box" if is_route else "dot",
            "title": "learned route" if is_route else "referenced endpoint",
        })

    html = f'''<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Azazael Neural Mesh — Interactive</title>
<script src="{vis_bundle_href(out_dir)}"></script>
<style>
  body {{ margin: 0; font-family: monospace; background: #1a1a2e; color: #eee; }}
  #graph {{ width: 100vw; height: 90vh; border: 1px solid #333; }}
  #stats {{ padding: 8px 16px; background: #16213e; font-size: 13px; }}
  .legend {{ display: inline-block; margin-right: 12px; }}
  .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%;
           margin-right: 4px; vertical-align: middle; }}
</style>
</head><body>
<div id="stats">
  <b>Azazael Neural Mesh</b> — {len(natures)} natures · {len(registry)} worms
  · {len(collect_q_edges(spine))} Q-edges · {len(collect_route_edges(spine, natures))} route-edges
  · {len(spine.get("q_weights", {}))} Q-weights · {len(spine.get("scars", {}))} scars
  · {len(vis_nodes)} nodes · {len(vis_edges)} edges · {n_implicit} implicit endpoints
  · Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}
</div>
<div id="graph"></div>
<script>
const nodes = new vis.DataSet({json.dumps(vis_nodes, ensure_ascii=False)});
const edges = new vis.DataSet({json.dumps(vis_edges, ensure_ascii=False)});
const container = document.getElementById('graph');
const data = {{ nodes, edges }};
const options = {{
  // vis.js recommends disabling improvedLayout past ~100 nodes: it tries to
  // pre-position the network and, when it cannot, logs
  // "This network could not be positioned by this version of the improved
  // layout algorithm" and falls back anyway. At 255 nodes it always fails, so
  // the warning was permanent noise on every load.
  // NB: `layout.improvedLayout`, not a top-level `improvedLayout`. The
  // top-level spelling is not a valid option, and vis.js reports it as a
  // console *error* — caught by tools/verify_mesh_html.py, which is the point
  // of having a browser check rather than trusting the template.
  layout: {{ improvedLayout: false }},
  physics: {{ solver: 'forceAtlas2Based', forceAtlas2Based: {{ gravitationalConstant: -80, springLength: 120 }} }},
  nodes: {{ font: {{ size: 10, color: '#eee' }} }},
  edges: {{ smooth: {{ type: 'continuous' }}, font: {{ size: 8, color: '#666' }} }},
  interaction: {{ hover: true, tooltipDelay: 200 }},
  groups: {{
    nature: {{ shape: 'diamond', borderWidth: 2 }},
  }},
}};
const network = new vis.Network(container, data, options);
// Handles for verification: a headless check reads these to confirm what the
// browser actually built, rather than trusting that the input was well-formed.
// Without them the only way to check this view is by eye.
window.__mesh = {{
  nodes: data.nodes.length,
  edges: data.edges.length,
  nodeIds: data.nodes.getIds(),
  edgeRefs: data.edges.get().map(e => [e.from, e.to]),
  network,
  ready: true,
}};
</script></body></html>'''

    path = out_dir / "mesh_gephi_interactive.html"
    path.write_text(html, encoding="utf-8")

    # Sidecar manifest for verification: the graph as the browser received it,
    # plus whether every edge endpoint resolved to a node. A reader (or a
    # headless check) can assert `unresolved_edges == 0` without parsing HTML.
    # Recompute against the FINAL node set (after placeholders): this is the
    # number a browser check asserts is zero.
    final_ids = {n["id"] for n in vis_nodes}
    manifest = {
        "generated_at": datetime.now().isoformat(),
        "nodes": len(vis_nodes),
        "edges": len(vis_edges),
        "node_ids": sorted(final_ids),
        "unresolved_edges": sum(
            1 for e in vis_edges if e["from"] not in final_ids or e["to"] not in final_ids
        ),
        "implicit_endpoints": n_implicit,
        "route_edges": [
            {"from": e["from"], "to": e["to"], "title": e.get("title", "")}
            for e in vis_edges if e.get("color") in ("#FFA500", "#3fb950")
        ],
        "edge_types_rendered": sorted({e.get("title", "").split(" ")[0]
                                       for e in vis_edges if e.get("title")}),
    }
    (out_dir / "mesh_gephi_interactive.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"  HTML: {path}  ({len(vis_nodes)} nodes, {len(vis_edges)} edges, "
          f"{n_implicit} implicit endpoints)")


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export neural mesh to Gephi")
    parser.add_argument("--json", action="store_true", help="Also dump raw JSON")
    parser.add_argument("--svg", action="store_true", help="Also render SVG via graphviz")
    parser.add_argument("--html", action="store_true", help="Also render interactive HTML")
    parser.add_argument("--all", action="store_true", help="All output formats")
    args = parser.parse_args()

    print("Azazael Neural Mesh -> Gephi Pipeline")
    print("=" * 50)

    print("1. Collecting nature definitions...")
    natures = collect_nature_definitions()
    print(f"   {len(natures)} natures loaded")

    print("2. Collecting worm registry...")
    registry = collect_registry()
    print(f"   {len(registry)} worms registered")

    print("3. Collecting spine data (Q-weights, scars)...")
    spine = collect_spine_data()
    print(f"   {len(spine.get('q_weights', {}))} Q-weights, "
          f"{len(spine.get('scars', {}))} scars")

    out_dir = PROJECT_ROOT / "graphify-out"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("4. Building GEXF...")
    gexf = build_gexf(natures, registry, spine)
    gexf_path = out_dir / "mesh_gephi.gexf"
    gexf_path.write_text(gexf, encoding="utf-8")
    print(f"   GEXF: {gexf_path}")

    if args.json or args.all:
        print("5. Dumping JSON...")
        dump_json(natures, registry, spine, out_dir)

    if args.svg or args.all:
        print("6. Rendering SVG...")
        dump_svg(gexf_path, out_dir)

    if args.html or args.all:
        print("7. Rendering interactive HTML...")
        dump_html(natures, registry, spine, out_dir)
    else:
        # Say it out loud. A default run leaves `mesh_gephi_interactive.{json,html}`
        # at whatever they were, so the browser view keeps showing an older mesh
        # than the ledger holds — the exact "stale artifact read as live" trap
        # this pipeline was fixed for. The monitors read those files.
        _prev = out_dir / "mesh_gephi_interactive.json"
        _when = ""
        try:
            _when = (" (last generated %s)" % datetime.fromtimestamp(
                _prev.stat().st_mtime).strftime("%Y-%m-%d %H:%M"))
        except OSError:
            pass
        print("7. Interactive view NOT rewritten" + _when +
              " -- pass --html (or --all) to refresh the browser view")

    # Summary
    edges_nature = len(collect_nature_edges(natures))
    edges_q = len(collect_q_edges(spine))
    edges_route = len(collect_route_edges(spine, natures))
    edges_reflex = len(collect_reflex_edges(spine))
    edges_membership = sum(1 for n, i in registry.items()
                           if i.get("category") in natures)

    print()
    print("=" * 50)
    print(f"Export complete: {out_dir / 'mesh_gephi.gexf'}")
    print(f"   Nodes: {len(natures)} natures + {len(registry)} worms")
    print(f"   Edges: {edges_nature} feeds_into + {edges_membership} membership "
          f"+ {edges_q} Q-weight + {edges_route} route + {edges_reflex} reflex")
    print()
    print("   Open in Gephi: File > Open > select mesh_gephi.gexf")
    print("   Recommended layout: ForceAtlas2 (overlap prevention on)")


if __name__ == "__main__":
    main()
