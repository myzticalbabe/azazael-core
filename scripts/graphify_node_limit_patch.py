#!/usr/bin/env python3
"""Re-apply the graphify visualization fixes after reinstall.

`graphify` (pip package `graphifyy`) ships with a hardcoded 5000-node cap on
HTML visualization. For a corpus as large as azazael (400k+ nodes) this broke
`graph.html` generation in two ways:

  BUG 1 — recursion re-applies the cap:
      exporters.html.to_html() builds an aggregated community meta-graph and
      recurses WITHOUT passing the limit, so the default 5000 cap is re-applied
      to the community graph (azazael has 19k+ communities) and it raises
      ValueError instead of rendering.

  BUG 2 — raised limits produce an unrenderable page:
      Bumping GRAPHIFY_VIZ_NODE_LIMIT to embed all 404k nodes inlines a
      ~440MB single <script> (browsers time out parsing it) and still runs
      forceAtlas2Based physics with 200 stabilization iterations on 404k nodes
      (never stabilizes; tab freezes). Fixed with a "huge mode" renderer:
      static Python-computed community-spiral layout, physics off, compact
      serialization, and chunked sidecar data files (~8MB each).

This script re-applies the patches idempotently. Run it after any
`uv tool upgrade graphifyy` (or pip reinstall) that wipes site-packages:

    python scripts/graphify_node_limit_patch.py          # auto-detect installs
    python scripts/graphify_node_limit_patch.py --path <site-packages/graphify>

Patches:
  1. exporters/html.py — recursion node_limit passthrough (BUG 1).
  2. cli.py — export html default + over-cap fallback honor GRAPHIFY_VIZ_NODE_LIMIT.
  3. exporters/html.py — huge-mode renderer (BUG 2): HUGE_MODE_NODES constant,
     _write_huge_html() (read from scripts/graphify_huge_mode.py), script
     template physics/smooth/colorBg changes, and the to_html() branch.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# --- patch definitions --------------------------------------------------------
# Each html patch is (marker, old, new). marker proves the patch is applied.

HTML_RECURSION_PATCH = (
    "node_limit=meta.number_of_nodes()",
    "            written = to_html(meta, meta_communities, output_path,\n"
    "                              community_labels=community_labels, member_counts=mc)\n"
    "            if not written:\n"
    "                return False",
    "            # Pass the effective limit through the recursion so the aggregated\n"
    "            # community meta-graph renders even when the number of communities\n"
    "            # itself exceeds MAX_NODES_FOR_VIZ (e.g. azazael: 13k communities).\n"
    "            # meta has exactly len(communities) nodes, so node_limit=\n"
    "            # meta.number_of_nodes() guarantees the second call renders directly\n"
    "            # instead of re-aggregating (which would otherwise raise ValueError\n"
    "            # and drop graph.html entirely).\n"
    "            written = to_html(meta, meta_communities, output_path,\n"
    "                              community_labels=community_labels, member_counts=mc,\n"
    "                              node_limit=meta.number_of_nodes())\n"
    "            if not written:\n"
    "                return False",
)

HTML_HUGE_CONST = (
    "HUGE_MODE_NODES = 50_000",
    "MAX_NODES_FOR_VIZ = 5_000\n"
    "_HTML_STALE_MARKER = \".graph.html.stale\"",
    "MAX_NODES_FOR_VIZ = 5_000\n"
    "HUGE_MODE_NODES = 50_000  # above this: compact/chunked/static-layout HTML\n"
    "_HTML_STALE_MARKER = \".graph.html.stale\"",
)

HTML_SCRIPT_SIG = (
    "physics_enabled_js = \"true\" if physics else \"false\"",
    "def _html_script(nodes_json: str, edges_json: str, legend_json: str) -> str:\n"
    "    return f\"\"\"<script>\n"
    "const RAW_NODES = {nodes_json};",
    "def _html_script(nodes_json: str, edges_json: str, legend_json: str,\n"
    "                 physics: bool = True, smooth: bool = True,\n"
    "                 hide_edges_on_zoom: bool = False) -> str:\n"
    "    physics_enabled_js = \"true\" if physics else \"false\"\n"
    "    smooth_opts = \"{ type: 'continuous', roundness: 0.2 }\" if smooth else \"false\"\n"
    "    hide_edges_on_zoom_js = \"true\" if hide_edges_on_zoom else \"false\"\n"
    "    return f\"\"\"<script>\n"
    "const RAW_NODES = {nodes_json};",
)

HTML_COLORBG = (
    "function colorBg(n) {{",
    "// HTML-escape helper — prevents XSS when injecting graph data into innerHTML\n"
    "function esc(s) {{\n"
    "  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&#39;');\n"
    "}}",
    "// HTML-escape helper — prevents XSS when injecting graph data into innerHTML\n"
    "function esc(s) {{\n"
    "  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&#39;');\n"
    "}}\n"
    "\n"
    "// Node color — huge-mode nodes carry a single hex string; normal-mode nodes\n"
    "// carry the full vis color object. Normalize both to a background hex.\n"
    "function colorBg(n) {{\n"
    "  return n ? (typeof n.color === 'string' ? n.color : (n.color && n.color.background) || '#555') : '#555';\n"
    "}}",
)

HTML_NODESDS = (
    "font: n.font || {{ size: 10, color: '#ffffff' }},",
    "const nodesDS = new vis.DataSet(RAW_NODES.map(n => ({{\n"
    "  id: n.id, label: n.label, color: n.color, size: n.size,\n"
    "  font: n.font, title: n.title,\n"
    "  _community: n.community, _community_name: n.community_name,\n"
    "  _source_file: n.source_file, _file_type: n.file_type, _degree: n.degree,\n"
    "}})));",
    "const nodesDS = new vis.DataSet(RAW_NODES.map(n => ({{\n"
    "  id: n.id, label: n.label, color: n.color, size: n.size,\n"
    "  font: n.font || {{ size: 10, color: '#ffffff' }},\n"
    "  title: n.title || n.label,\n"
    "  x: n.x, y: n.y,\n"
    "  _community: n.community, _community_name: n.community_name || ('Community ' + n.community),\n"
    "  _source_file: n.source_file, _file_type: n.file_type, _degree: n.degree,\n"
    "}})));",
)

HTML_EDGESDS = (
    "dashes: e.dashes || false,",
    "const edgesDS = new vis.DataSet(RAW_EDGES.map((e, i) => ({{\n"
    "  id: i, from: e.from, to: e.to,\n"
    "  label: '',\n"
    "  title: e.title,\n"
    "  dashes: e.dashes,\n"
    "  width: e.width,\n"
    "  color: e.color,\n"
    "  arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},\n"
    "}})));",
    "const edgesDS = new vis.DataSet(RAW_EDGES.map((e, i) => ({{\n"
    "  id: i, from: e.from, to: e.to,\n"
    "  label: '',\n"
    "  title: e.title || e.label || '',\n"
    "  dashes: e.dashes || false,\n"
    "  width: e.width || 1,\n"
    "  color: e.color || {{ opacity: 0.7 }},\n"
    "  arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},\n"
    "}})));",
)

HTML_PHYSICS = (
    "enabled: {physics_enabled_js},",
    "  physics: {{\n"
    "    enabled: true,\n"
    "    solver: 'forceAtlas2Based',",
    "  physics: {{\n"
    "    enabled: {physics_enabled_js},\n"
    "    solver: 'forceAtlas2Based',",
)

HTML_SMOOTH = (
    "smooth: {smooth_opts},",
    "  edges: {{ smooth: {{ type: 'continuous', roundness: 0.2 }}, selectionWidth: 3 }},",
    "  edges: {{ smooth: {smooth_opts}, selectionWidth: 3 }},",
)

HTML_HIDEZOOM = (
    "hideEdgesOnZoom: {hide_edges_on_zoom_js},",
    "  interaction: {{\n"
    "    hover: true,\n"
    "    tooltipDelay: 100,\n"
    "    hideEdgesOnDrag: true,",
    "  interaction: {{\n"
    "    hover: true,\n"
    "    tooltipDelay: 100,\n"
    "    hideEdgesOnDrag: true,\n"
    "    hideEdgesOnZoom: {hide_edges_on_zoom_js},",
)

HTML_COLOR_NB = (
    "const color = nb ? colorBg(nb) : '#555';",
    "    const color = nb ? nb.color.background : '#555';",
    "    const color = nb ? colorBg(nb) : '#555';",
)

HTML_COLOR_N = (
    "el.style.borderLeft = `3px solid ${{colorBg(n)}}`;",
    "    el.style.borderLeft = `3px solid ${{n.color.background}}`;",
    "    el.style.borderLeft = `3px solid ${{colorBg(n)}}`;",
)

# to_html(): restrict the aggregation path to <= HUGE_MODE_NODES so oversized
# graphs skip straight to the huge-mode renderer, and add the branch itself.
HTML_LIMIT_GUARD = (
    "if G.number_of_nodes() > limit and G.number_of_nodes() <= HUGE_MODE_NODES:",
    "    if G.number_of_nodes() > limit:\n"
    "        if node_limit is not None:",
    "    if G.number_of_nodes() > limit and G.number_of_nodes() <= HUGE_MODE_NODES:\n"
    "        if node_limit is not None:",
)

HTML_HUGE_BRANCH = (
    "using huge-mode HTML",
    "    node_community = _node_community_map(communities)\n"
    "    degree = dict(G.degree())\n"
    "    max_deg = max(degree.values(), default=1) or 1\n"
    "    max_mc = (max(member_counts.values(), default=1) or 1) if member_counts else 1",
    "    # Huge graphs (azazael: 400k+ nodes) can't be inlined as one script or run\n"
    "    # force-layout physics in the browser — route them to the chunked, static-\n"
    "    # layout renderer instead of producing an unusable multi-hundred-MB page.\n"
    "    if G.number_of_nodes() > HUGE_MODE_NODES:\n"
    "        print(f\"Graph has {G.number_of_nodes()} nodes - using huge-mode HTML \"\n"
    "              f\"(static layout, chunked data, physics off).\")\n"
    "        return _write_huge_html(G, communities, output_path,\n"
    "                                community_labels=community_labels)\n"
    "\n"
    "    node_community = _node_community_map(communities)\n"
    "    degree = dict(G.degree())\n"
    "    max_deg = max(degree.values(), default=1) or 1\n"
    "    max_mc = (max(member_counts.values(), default=1) or 1) if member_counts else 1",
)

# _write_huge_html() body lives in the companion file (keeps this script small).
_HUGE_FN_SOURCE = (Path(__file__).resolve().parent / "graphify_huge_mode.py").read_text(
    encoding="utf-8"
).rstrip() + "\n"

HTML_HUGE_FUNC = (
    "def _write_huge_html(",
    "def to_html(\n"
    "    G: nx.Graph,",
    _HUGE_FN_SOURCE + "\n"
    "\n"
    "def to_html(\n"
    "    G: nx.Graph,",
)

CLI_PATCH_1 = (
    "_viz_limit_fn",
    "        analysis_path = Path(_GRAPHIFY_OUT) / \".graphify_analysis.json\"\n"
    "        node_limit = 5000\n"
    "        no_viz = False",
    "        analysis_path = Path(_GRAPHIFY_OUT) / \".graphify_analysis.json\"\n"
    "        from graphify.exporters.html import _viz_node_limit as _viz_limit_fn\n"
    "        node_limit = _viz_limit_fn()  # honors GRAPHIFY_VIZ_NODE_LIMIT (default 5000)\n"
    "        no_viz = False",
)

CLI_PATCH_2 = (
    "_effective_node_limit = _viz_limit_fn() if _over_cap else node_limit",
    "                _effective_node_limit = 5000 if _over_cap else node_limit",
    "                # Over-cap fallback (#1019): force the community-aggregation\n"
    "                # path so the oversized graph still renders a usable artifact.\n"
    "                # Honor GRAPHIFY_VIZ_NODE_LIMIT instead of hardcoding 5000 so a\n"
    "                # raised limit applies on this path too.\n"
    "                _effective_node_limit = _viz_limit_fn() if _over_cap else node_limit",
)

HTML_PATCHES = [
    HTML_RECURSION_PATCH,
    HTML_HUGE_CONST,
    HTML_SCRIPT_SIG,
    HTML_COLORBG,
    HTML_NODESDS,
    HTML_EDGESDS,
    HTML_PHYSICS,
    HTML_SMOOTH,
    HTML_HIDEZOOM,
    HTML_COLOR_NB,
    HTML_COLOR_N,
    HTML_LIMIT_GUARD,
    HTML_HUGE_BRANCH,
    HTML_HUGE_FUNC,
]

# --- discovery ----------------------------------------------------------------

def candidate_roots() -> list[Path]:
    roots: list[Path] = []
    # uv tool installs (graphifyy 0.9.x — the version with exporters/html.py)
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "uv" / "tools" / "graphifyy" / "Lib" / "site-packages")
    # generic site-packages of common pythons
    for base in (Path.home() / "AppData" / "Local" / "Programs",):
        if base.exists():
            for py in sorted(base.glob("Python*/Lib/site-packages")):
                roots.append(py)
    return roots


def find_graphify_dirs() -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots():
        g = root / "graphify"
        if g.is_dir() and g not in seen:
            seen.add(g)
            found.append(g)
    return found


def patch_file(path: Path, marker: str, old: str, new: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"  ERROR reading {path}: {exc}"
    if marker in text:
        return f"  already applied ({path.name})"
    if old not in text:
        return f"  ERROR: anchor not found in {path.name} — version drift? (marker={marker!r})"
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    return f"  patched {path.name}"


def patch_one(graphify_dir: Path) -> list[str]:
    out: list[str] = []
    html = graphify_dir / "exporters" / "html.py"
    cli = graphify_dir / "cli.py"
    out.append(f"graphify dir: {graphify_dir}")
    if html.is_file():
        for patch in HTML_PATCHES:
            out.append(patch_file(html, *patch))
    else:
        out.append("  SKIP exporters/html.py (not present — old version without exporters/)")
    if cli.is_file():
        out.append(patch_file(cli, *CLI_PATCH_1))
        out.append(patch_file(cli, *CLI_PATCH_2))
    else:
        out.append("  SKIP cli.py (not present)")
    return out


def main() -> int:
    args = sys.argv[1:]
    explicit = None
    if args and args[0] == "--path":
        if len(args) < 2:
            print("usage: graphify_node_limit_patch.py [--path <graphify-dir>]", file=sys.stderr)
            return 2
        explicit = Path(args[1])

    if explicit is not None:
        dirs = [explicit]
    else:
        dirs = find_graphify_dirs()
        if not dirs:
            print("error: no graphify install found. Pass --path <site-packages/graphify>", file=sys.stderr)
            return 1

    for d in dirs:
        for line in patch_one(d):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
