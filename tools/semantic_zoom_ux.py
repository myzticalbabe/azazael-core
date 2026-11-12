#!/usr/bin/env python3
# coding: utf-8
"""
semantic_zoom_ux.py — Semantic Zoom UX Design for Yggdrasil Forest (K5)

Implements distance-based LOD (Level of Detail) rendering with progressive
disclosure based on Shneiderman's Visual Information-Seeking Mantra:
"Overview first, zoom and filter, then details-on-demand"

And C4 Model (Context → Container → Component → Code) for code navigation.

Cognitive Load Theory principles:
- Minimize extraneous load at each zoom level
- Progressive disclosure prevents overwhelm
- Consistent mental model across zoom levels

Zoom Levels (L1-L4):
    L1: Context      — System/Repo level: tree names, sizes, connections, hubs
    L2: Container    — Subsystem/Domain: module groups, import boundaries
    L3: Component    — Module/File: classes, functions, internal edges
    L4: Code         — Symbol level: source, AST, CFG, signatures

Each level has distinct visual encoding, interaction model, and data density.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum

# ---------------------------------------------------------------------------
# Zoom Level Definitions
# ---------------------------------------------------------------------------

class ZoomLevel(Enum):
    L1_CONTEXT = 1      # System/Repo overview
    L2_CONTAINER = 2    # Subsystem/Domain
    L3_COMPONENT = 3    # Module/File
    L4_CODE = 4         # Symbol level


@dataclass
class ZoomLevelConfig:
    """Configuration for a zoom level."""
    level: ZoomLevel
    name: str
    description: str
    
    # Camera distance range (arbitrary units)
    distance_min: float
    distance_max: float
    
    # What to show at this level
    show_nodes: List[str]           # node types: "tree", "module", "class", "function"
    show_edges: List[str]           # edge types: "contains", "imports", "calls", "inherits"
    
    # Visual encoding
    node_size_base: float
    node_size_attribute: Optional[str]  # "lines", "imports", "complexity"
    edge_width_base: float
    edge_opacity: float
    
    # Labels
    show_labels: bool
    label_attribute: str            # "name", "lines", "complexity"
    label_max_length: int
    
    # Interaction
    click_action: str               # "zoom_in", "select", "show_source"
    hover_detail: str               # "summary", "signature", "docstring"
    
    # Performance
    max_nodes: int
    max_edges: int
    lod_strategy: str               # "frustum_culling", "clustering", "sampling"
    
    # Cognitive load limits
    max_cognitive_items: int = 7    # Miller's law


# Default zoom level configurations (C4 model aligned)
DEFAULT_ZOOM_CONFIGS = {
    ZoomLevel.L1_CONTEXT: ZoomLevelConfig(
        level=ZoomLevel.L1_CONTEXT,
        name="Context",
        description="System/Repo overview — tree names, sizes, connections, hubs",
        distance_min=1000.0,
        distance_max=float('inf'),
        show_nodes=["tree"],
        show_edges=["contains"],
        node_size_base=50.0,
        node_size_attribute="lines",  # total lines in tree
        edge_width_base=3.0,
        edge_opacity=0.6,
        show_labels=True,
        label_attribute="name",
        label_max_length=20,
        click_action="zoom_in",
        hover_detail="summary",
        max_nodes=50,
        max_edges=200,
        lod_strategy="frustum_culling",
    ),
    ZoomLevel.L2_CONTAINER: ZoomLevelConfig(
        level=ZoomLevel.L2_CONTAINER,
        name="Container",
        description="Subsystem/Domain — module groups, import boundaries",
        distance_min=200.0,
        distance_max=1000.0,
        show_nodes=["tree", "module"],
        show_edges=["contains", "imports"],
        node_size_base=30.0,
        node_size_attribute="imports",  # cross-module imports
        edge_width_base=2.0,
        edge_opacity=0.5,
        show_labels=True,
        label_attribute="name",
        label_max_length=30,
        click_action="zoom_in",
        hover_detail="summary",
        max_nodes=200,
        max_edges=1000,
        lod_strategy="clustering",
    ),
    ZoomLevel.L3_COMPONENT: ZoomLevelConfig(
        level=ZoomLevel.L3_COMPONENT,
        name="Component",
        description="Module/File — classes, functions, internal edges",
        distance_min=50.0,
        distance_max=200.0,
        show_nodes=["module", "class", "function"],
        show_edges=["contains", "imports", "calls", "inherits"],
        node_size_base=20.0,
        node_size_attribute="complexity",
        edge_width_base=1.5,
        edge_opacity=0.4,
        show_labels=True,
        label_attribute="name",
        label_max_length=40,
        click_action="show_source",
        hover_detail="signature",
        max_nodes=1000,
        max_edges=5000,
        lod_strategy="sampling",
    ),
    ZoomLevel.L4_CODE: ZoomLevelConfig(
        level=ZoomLevel.L4_CODE,
        name="Code",
        description="Symbol level — source, AST, CFG, signatures",
        distance_min=0.0,
        distance_max=50.0,
        show_nodes=["class", "function", "variable"],
        show_edges=["calls", "contains"],
        node_size_base=15.0,
        node_size_attribute="lines",
        edge_width_base=1.0,
        edge_opacity=0.3,
        show_labels=True,
        label_attribute="signature",
        label_max_length=60,
        click_action="show_source",
        hover_detail="docstring",
        max_nodes=5000,
        max_edges=20000,
        lod_strategy="sampling",
    ),
}


# ---------------------------------------------------------------------------
# Semantic Zoom Engine
# ---------------------------------------------------------------------------

@dataclass
class CameraState:
    """Camera/viewport state for zoom calculation."""
    x: float = 0.0
    y: float = 0.0
    z: float = 1000.0  # distance from focal point
    target_x: float = 0.0
    target_y: float = 0.0
    target_z: float = 0.0
    
    @property
    def distance(self) -> float:
        """Distance from camera to focal point."""
        return abs(self.z - self.target_z)
    
    def get_zoom_level(self, configs: Dict[ZoomLevel, ZoomLevelConfig]) -> ZoomLevel:
        """Determine current zoom level from camera distance."""
        dist = self.distance
        for level in [ZoomLevel.L4_CODE, ZoomLevel.L3_COMPONENT, 
                      ZoomLevel.L2_CONTAINER, ZoomLevel.L1_CONTEXT]:
            config = configs[level]
            if config.distance_min <= dist <= config.distance_max:
                return level
        return ZoomLevel.L1_CONTEXT


@dataclass
class ViewportConfig:
    """Viewport rendering configuration for current zoom."""
    zoom_level: ZoomLevel
    config: ZoomLevelConfig
    visible_nodes: List[str]
    visible_edges: List[Tuple[str, str, str]]  # (src, type, dst)
    frustum_bounds: Tuple[float, float, float, float]  # min_x, max_x, min_y, max_y
    node_positions: Dict[str, Tuple[float, float]]
    edge_bundles: List[Dict[str, Any]]


class SemanticZoomEngine:
    """Core engine for semantic zoom level management and LOD."""
    
    def __init__(self, configs: Optional[Dict[ZoomLevel, ZoomLevelConfig]] = None):
        self.configs = configs or DEFAULT_ZOOM_CONFIGS
        self.camera = CameraState()
        self._node_data: Dict[str, Any] = {}
        self._edge_data: List[Tuple[str, str, str]] = []
        self._tree_hierarchy: Dict[str, List[str]] = defaultdict(list)  # parent -> children
    
    def load_graph_data(self, nodes: Dict[str, Any], edges: List[Tuple[str, str, str]]):
        """Load graph data and build hierarchy."""
        self._node_data = nodes
        self._edge_data = edges
        
        # Build tree hierarchy
        self._tree_hierarchy.clear()
        for src, edge_type, dst in edges:
            if edge_type == "contains":
                self._tree_hierarchy[src].append(dst)
    
    def update_camera(self, x: float, y: float, z: float):
        """Update camera position."""
        self.camera.x = x
        self.camera.y = y
        self.camera.z = z
    
    def set_target(self, x: float, y: float, z: float):
        """Set camera focal point."""
        self.camera.target_x = x
        self.camera.target_y = y
        self.camera.target_z = z
    
    def get_viewport_config(self) -> ViewportConfig:
        """Compute viewport configuration for current camera state."""
        zoom_level = self.camera.get_zoom_level(self.configs)
        config = self.configs[zoom_level]
        
        # Filter nodes by type and distance
        visible_nodes = self._filter_nodes(config)
        visible_edges = self._filter_edges(config, visible_nodes)
        
        # Apply frustum culling
        visible_nodes = self._frustum_cull(visible_nodes, config)
        
        # Limit to max items (cognitive load)
        if len(visible_nodes) > config.max_nodes:
            visible_nodes = self._prioritize_nodes(visible_nodes, config)[:config.max_nodes]
        
        # Build edge bundles for L2+
        edge_bundles = []
        if zoom_level in (ZoomLevel.L2_CONTAINER, ZoomLevel.L1_CONTEXT):
            edge_bundles = self._build_edge_bundles(visible_edges, config)
        
        return ViewportConfig(
            zoom_level=zoom_level,
            config=config,
            visible_nodes=visible_nodes,
            visible_edges=visible_edges,
            frustum_bounds=self._get_frustum_bounds(),
            node_positions=self._compute_positions(visible_nodes),
            edge_bundles=edge_bundles,
        )
    
    def _filter_nodes(self, config: ZoomLevelConfig) -> List[str]:
        """Filter nodes by allowed types."""
        allowed_types = set(config.show_nodes)
        return [
            node_id for node_id, node_data in self._node_data.items()
            if node_data.get("type", "").lower() in allowed_types
        ]
    
    def _filter_edges(self, config: ZoomLevelConfig, 
                      visible_nodes: List[str]) -> List[Tuple[str, str, str]]:
        """Filter edges by type and node visibility."""
        visible_set = set(visible_nodes)
        allowed_types = set(config.show_edges)
        
        return [
            (src, etype, dst) for src, etype, dst in self._edge_data
            if etype in allowed_types and src in visible_set and dst in visible_set
        ]
    
    def _frustum_cull(self, nodes: List[str], config: ZoomLevelConfig) -> List[str]:
        """Frustum culling based on camera view."""
        # Simplified: return all nodes for now
        # Real implementation would check node positions against camera frustum
        return nodes
    
    def _prioritize_nodes(self, nodes: List[str], config: ZoomLevelConfig) -> List[str]:
        """Prioritize nodes by importance attribute."""
        attr = config.node_size_attribute or "lines"
        
        def importance(node_id: str) -> float:
            node = self._node_data.get(node_id, {})
            return node.get(attr, node.get("lines", node.get("importance", 1.0)))
        
        return sorted(nodes, key=importance, reverse=True)
    
    def _build_edge_bundles(self, edges: List[Tuple[str, str, str]], 
                            config: ZoomLevelConfig) -> List[Dict[str, Any]]:
        """Bundle edges by type for reduced visual clutter."""
        bundles = defaultdict(lambda: {"count": 0, "type": "", "source_cluster": "", "target_cluster": ""})
        
        for src, etype, dst in edges:
            # Cluster by parent tree
            src_tree = self._get_tree_root(src)
            dst_tree = self._get_tree_root(dst)
            
            key = f"{src_tree}->{dst_tree}:{etype}"
            bundles[key]["count"] += 1
            bundles[key]["type"] = etype
            bundles[key]["source_cluster"] = src_tree
            bundles[key]["target_cluster"] = dst_tree
        
        return [
            {"key": k, **v} for k, v in bundles.items() if v["count"] > 1
        ]
    
    def _get_tree_root(self, node_id: str) -> str:
        """Get root tree for a node."""
        node = self._node_data.get(node_id, {})
        module = node.get("module", "")
        if module:
            return module.split(".")[0]
        # Try to find parent tree
        for parent, children in self._tree_hierarchy.items():
            if node_id in children:
                return self._get_tree_root(parent)
        return "unknown"
    
    def _get_frustum_bounds(self) -> Tuple[float, float, float, float]:
        """Get camera frustum bounds in world coordinates."""
        # Simplified: return large bounds
        return (-10000, 10000, -10000, 10000)
    
    def _compute_positions(self, nodes: List[str]) -> Dict[str, Tuple[float, float]]:
        """Compute 2D positions for nodes (force-directed layout placeholder)."""
        positions = {}
        # Use tree hierarchy for initial layout
        for i, node_id in enumerate(nodes):
            angle = 2 * math.pi * i / max(len(nodes), 1)
            radius = 100 + hash(node_id) % 500
            positions[node_id] = (radius * math.cos(angle), radius * math.sin(angle))
        return positions


# ---------------------------------------------------------------------------
# Export Configuration for Renderer
# ---------------------------------------------------------------------------

def export_zoom_config(configs: Dict[ZoomLevel, ZoomLevelConfig], 
                       output_path: Path):
    """Export zoom level configurations for renderer (JS/TypeScript)."""
    export_data = {
        "version": "1.0",
        "levels": {},
    }
    
    for level, config in configs.items():
        export_data["levels"][level.name] = {
            "level": level.value,
            "name": config.name,
            "description": config.description,
            "distance_min": config.distance_min,
            "distance_max": config.distance_max,
            "show_nodes": config.show_nodes,
            "show_edges": config.show_edges,
            "visual": {
                "node_size_base": config.node_size_base,
                "node_size_attribute": config.node_size_attribute,
                "edge_width_base": config.edge_width_base,
                "edge_opacity": config.edge_opacity,
            },
            "labels": {
                "show": config.show_labels,
                "attribute": config.label_attribute,
                "max_length": config.label_max_length,
            },
            "interaction": {
                "click_action": config.click_action,
                "hover_detail": config.hover_detail,
            },
            "performance": {
                "max_nodes": config.max_nodes,
                "max_edges": config.max_edges,
                "lod_strategy": config.lod_strategy,
            },
            "cognitive": {
                "max_items": config.max_cognitive_items,
            },
        }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(export_data, f, indent=2)
    
    print(f"Zoom config exported to {output_path}")


# ---------------------------------------------------------------------------
# Renderer Integration (HTML/JS Template)
# ---------------------------------------------------------------------------

RENDERER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Yggdrasil Semantic Zoom</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        body { margin: 0; overflow: hidden; background: #0b0609; }
        #canvas { width: 100vw; height: 100vh; }
        .zoom-indicator {
            position: fixed; top: 20px; right: 20px;
            background: rgba(11,6,9,0.9); color: #fdeee8;
            padding: 12px 16px; border-radius: 8px;
            font-family: monospace; font-size: 14px;
            border: 1px solid #e05a3c;
            z-index: 1000;
        }
        .node { cursor: pointer; transition: opacity 0.2s; }
        .node:hover { opacity: 1; stroke: #ffd166; stroke-width: 2; }
        .edge { stroke-opacity: 0.4; pointer-events: none; }
        .label { font-family: monospace; font-size: 11px; pointer-events: none; }
        .bundle { stroke: #a08579; stroke-width: 2; stroke-dasharray: 5,5; }
    </style>
</head>
<body>
    <svg id="canvas"></svg>
    <div class="zoom-indicator" id="zoom-info">Loading...</div>
    <script>
        // Zoom level config (injected from Python)
        const ZOOM_CONFIG = {{ZOOM_CONFIG_JSON}};
        
        let camera = { x: 0, y: 0, z: 1000, targetX: 0, targetY: 0, targetZ: 0 };
        let currentLevel = 'L1_CONTEXT';
        let nodes = [];
        let edges = [];
        let simulation = null;
        
        const svg = d3.select("#canvas");
        const g = svg.append("g");
        
        // Initialize
        function init() {
            const width = window.innerWidth;
            const height = window.innerHeight;
            svg.attr("width", width).attr("height", height);
            
            // Load graph data (from Python)
            fetch('/api/graph')
                .then(r => r.json())
                .then(data => {
                    nodes = data.nodes;
                    edges = data.edges;
                    startSimulation();
                });
            
            // Zoom handling
            svg.call(d3.zoom()
                .scaleExtent([0.1, 10])
                .on("zoom", (event) => {
                    g.attr("transform", event.transform);
                    updateZoomLevel(event.transform.k);
                }));
        }
        
        function updateZoomLevel(scale) {
            // Map scale to zoom level
            const levels = ['L4_CODE', 'L3_COMPONENT', 'L2_CONTAINER', 'L1_CONTEXT'];
            let level = levels[Math.floor(Math.log2(scale) * 2 + 2)];
            level = level || 'L1_CONTEXT';
            
            if (level !== currentLevel) {
                currentLevel = level;
                const config = ZOOM_CONFIG[level];
                applyLevelConfig(config);
                updateZoomIndicator();
            }
        }
        
        function applyLevelConfig(config) {
            // Update visual encoding based on config
            // Node size
            g.selectAll(".node")
                .attr("r", d => config.visual.node_size_base * Math.sqrt(d[config.visual.node_size_attribute] || 1));
            
            // Edge width/opacity
            g.selectAll(".edge")
                .attr("stroke-width", config.visual.edge_width_base)
                .attr("stroke-opacity", config.visual.edge_opacity);
            
            // Labels
            g.selectAll(".label")
                .style("display", config.labels.show ? "block" : "none")
                .text(d => d[config.labels.attribute].substring(0, config.labels.max_length));
        }
        
        function updateZoomIndicator() {
            const config = ZOOM_CONFIG[currentLevel];
            document.getElementById('zoom-info').innerHTML = 
                `Level: ${config.name}<br>Distance: ${camera.z.toFixed(0)}<br>Nodes: ${nodes.length}`;
        }
        
        // Camera controls
        let camera = { x: 0, y: 0, z: 1000 };
        
        window.addEventListener('wheel', (e) => {
            e.preventDefault();
            camera.z *= e.deltaY > 0 ? 1.1 : 0.9;
            camera.z = Math.max(10, Math.min(5000, camera.z));
            updateZoomLevel(1000 / camera.z);
        }, { passive: false });
        
        init();
    </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Semantic Zoom UX Engine for Yggdrasil")
    parser.add_argument("--export-config", type=Path, default=Path("tools/semantic_zoom_config.json"),
                        help="Export zoom config JSON for renderer")
    parser.add_argument("--export-renderer", type=Path, default=Path("tools/semantic_zoom_renderer.html"),
                        help="Export HTML renderer template")
    parser.add_argument("--test", action="store_true", help="Run self-test")
    parser.add_argument("--graph", type=Path, help="Load graph data from JSON")
    
    args = parser.parse_args()
    
    if args.export_config:
        export_zoom_config(DEFAULT_ZOOM_CONFIGS, args.export_config)
        return 0
    
    if args.export_renderer:
        # Inject config into template
        config_json = json.dumps({
            level.name: {
                "level": config.level.value,
                "name": config.name,
                "description": config.description,
                "distance_min": config.distance_min,
                "distance_max": config.distance_max,
                "show_nodes": config.show_nodes,
                "show_edges": config.show_edges,
                "visual": {
                    "node_size_base": config.node_size_base,
                    "node_size_attribute": config.node_size_attribute,
                    "edge_width_base": config.edge_width_base,
                    "edge_opacity": config.edge_opacity,
                },
                "labels": {
                    "show": config.show_labels,
                    "attribute": config.label_attribute,
                    "max_length": config.label_max_length,
                },
                "interaction": {
                    "click_action": config.click_action,
                    "hover_detail": config.hover_detail,
                },
                "performance": {
                    "max_nodes": config.max_nodes,
                    "max_edges": config.max_edges,
                    "lod_strategy": config.lod_strategy,
                },
                "cognitive": {
                    "max_items": config.max_cognitive_items,
                },
            }
            for level, config in DEFAULT_ZOOM_CONFIGS.items()
        }, indent=2)
        
        template = RENDERER_TEMPLATE.replace("{{ZOOM_CONFIG_JSON}}", config_json)
        args.export_renderer.parent.mkdir(parents=True, exist_ok=True)
        with args.export_renderer.open("w") as f:
            f.write(template)
        print(f"Renderer template exported to {args.export_renderer}")
        return 0
    
    if args.test:
        print("Running semantic zoom self-test...")
        
        # Test zoom level detection
        engine = SemanticZoomEngine()
        
        # Test L1
        engine.camera.z = 2000
        assert engine.camera.get_zoom_level(engine.configs) == ZoomLevel.L1_CONTEXT
        print("✓ L1_CONTEXT at distance 2000")
        
        # Test L2
        engine.camera.z = 500
        assert engine.camera.get_zoom_level(engine.configs) == ZoomLevel.L2_CONTAINER
        print("✓ L2_CONTAINER at distance 500")
        
        # Test L3
        engine.camera.z = 100
        assert engine.camera.get_zoom_level(engine.configs) == ZoomLevel.L3_COMPONENT
        print("✓ L3_COMPONENT at distance 100")
        
        # Test L4
        engine.camera.z = 25
        assert engine.camera.get_zoom_level(engine.configs) == ZoomLevel.L4_CODE
        print("✓ L4_CODE at distance 25")
        
        # Test viewport config
        engine.load_graph_data(
            {"tree:CORE": {"type": "tree", "name": "CORE", "lines": 50000}},
            [("tree:CORE", "contains", "module:CORE.worm_registry")]
        )
        viewport = engine.get_viewport_config()
        assert viewport.zoom_level == ZoomLevel.L1_CONTEXT
        print("✓ Viewport config generation")
        
        print("\nAll self-tests passed!")
        return 0
    
    if args.graph:
        # Load graph and test viewport
        with open(args.graph, encoding="utf-8") as f:
            data = json.load(f)
        
        engine = SemanticZoomEngine()
        engine.load_graph_data(data.get("nodes", {}), data.get("edges", []))
        
        # Test at different zoom levels
        for dist, expected in [(2000, ZoomLevel.L1_CONTEXT), (500, ZoomLevel.L2_CONTAINER),
                                (100, ZoomLevel.L3_COMPONENT), (25, ZoomLevel.L4_CODE)]:
            engine.camera.z = dist
            viewport = engine.get_viewport_config()
            assert viewport.zoom_level == expected, f"Expected {expected}, got {viewport.zoom_level}"
            print(f"✓ Distance {dist} -> {viewport.zoom_level.name}")
        
        print("\nGraph viewport tests passed!")
        return 0
    
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())