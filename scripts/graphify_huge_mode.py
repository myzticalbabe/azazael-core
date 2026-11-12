import math
def _write_huge_html(
    G: nx.Graph,
    communities: dict[int, list[str]],
    output_path: str,
    community_labels: dict[int, str] | None = None,
) -> bool:
    """Render a graph too large for a single inline-script HTML page.

    Browsers time out parsing a single multi-hundred-MB inline <script>, and
    vis.js forceAtlas2Based physics with hundreds of thousands of nodes never
    stabilizes. For huge graphs (azazael: 400k+ nodes) we instead:

      * precompute a deterministic static layout in Python (community-anchored
        spiral), so the page never runs a physics simulation;
      * serialize compactly — string colors, no per-node font/title/community
        name bloat — dropping the payload by ~10x;
      * split the data into sidecar chunk files (<stem>.data.N.js) loaded as
        sequential <script> tags, so no single parse is gigantic;
      * render with physics disabled at the precomputed positions.
    """
    import random
    import re as _re

    node_community = _node_community_map(communities)
    degree = dict(G.degree())
    max_deg = max(degree.values(), default=1) or 1

    # Community-anchored spiral layout: communities ordered by size, placed on
    # a golden-angle spiral; members jittered in a disk around their community
    # center so each cluster stays together without full force layout.
    rng = random.Random(42)
    sized = sorted(communities.items(), key=lambda kv: -len(kv[1]))
    centers: dict[int, tuple[float, float]] = {}
    for i, (cid, members) in enumerate(sized):
        r = 60.0 * math.sqrt(i + 1)
        a = i * 2.399963229728653  # golden angle
        centers[cid] = (r * math.cos(a), r * math.sin(a))

    vis_nodes = []
    for node_id, data in G.nodes(data=True):
        cid = node_community.get(node_id, 0)
        if cid not in centers:
            cid = sized[0][0] if sized else 0
        color = COMMUNITY_COLORS[cid % len(COMMUNITY_COLORS)]
        label = sanitize_label(data.get("label", node_id))
        deg = degree.get(node_id, 1)
        cx, cy = centers.get(cid, (0.0, 0.0))
        r = 8.0 + 30.0 * math.sqrt(len(communities.get(cid, [])))
        a = rng.uniform(0, math.tau)
        rr = r * math.sqrt(rng.random())
        x, y = cx + rr * math.cos(a), cy + rr * math.sin(a)
        vis_nodes.append({
            "id": node_id,
            "label": label,
            "color": color,  # compact: single hex string
            "size": round(4 + 10 * (deg / max_deg), 1),
            "x": round(x, 1),
            "y": round(y, 1),
            "community": cid,
            "source_file": sanitize_label(str(data.get("source_file") or "")),
            "file_type": data.get("file_type", ""),
            "degree": deg,
        })

    vis_edges = []
    for u, v, data in G.edges(data=True):
        relation = data.get("relation", "")
        vis_edges.append({
            "from": data.get("_src", u),
            "to": data.get("_tgt", v),
            "label": relation,
        })

    legend_data = []
    for cid in sorted((community_labels or {}).keys()):
        color = COMMUNITY_COLORS[cid % len(COMMUNITY_COLORS)]
        lbl = _html.escape(sanitize_label((community_labels or {}).get(cid, f"Community {cid}")))
        legend_data.append({"cid": cid, "color": color, "label": lbl,
                            "count": len(communities.get(cid, []))})

    def _js_safe(obj) -> str:
        return json.dumps(obj).replace("</", "<\\/")

    out = Path(output_path)
    stem = out.stem

    def _write_chunks(items, name) -> list[str]:
        """Split a list into ~8MB JSON chunks, each a window.<name>N script."""
        files = []
        per_file = 8_000_000
        cur: list = []
        cur_sz = 0
        idx = 0
        for item in items:
            sz = len(json.dumps(item))
            if cur and cur_sz + sz > per_file:
                var = f"window.{name}{idx} = "
                files.append((var, cur))
                idx += 1
                cur, cur_sz = [], 0
            cur.append(item)
            cur_sz += sz
        if cur:
            files.append((f"window.{name}{idx} = ", cur))
        paths = []
        for j, (var, chunk) in enumerate(files):
            p = out.with_name(f"{stem}.{name.lower()}{j}.js")
            p.write_text(var + _js_safe(chunk) + ";\n", encoding="utf-8")
            paths.append(p.name)
        return paths

    node_files = _write_chunks(vis_nodes, "ND")
    edge_files = _write_chunks(vis_edges, "ED")

    nd_refs = "[].concat(" + ", ".join(f"window.ND{j}" for j in range(len(node_files))) + ")"
    ed_refs = "[].concat(" + ", ".join(f"window.ED{j}" for j in range(len(edge_files))) + ")"
    script_tags = "\n".join(f'<script src="{f}"></script>' for f in node_files + edge_files)

    legend_json = _js_safe(legend_data)
    title = _html.escape(sanitize_label(_html_document_title(output_path)))
    stats = f"{G.number_of_nodes()} nodes &middot; {G.number_of_edges()} edges &middot; {len(communities)} communities"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>graphify - {title}</title>
<script src="https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js"
        integrity="sha384-Ux6phic9PEHJ38YtrijhkzyJ8yQlH8i/+buBR8s3mAZOJrP1gwyvAcIYl3GWtpX1"
        crossorigin="anonymous"></script>
{_html_styles()}
</head>
<body>
<div id="graph"></div>
<div id="sidebar">
  <div id="search-wrap">
    <input id="search" type="text" placeholder="Search nodes..." autocomplete="off">
    <div id="search-results"></div>
  </div>
  <div id="info-panel">
    <h3>Node Info</h3>
    <div id="info-content"><span class="empty">Click a node to inspect it</span></div>
  </div>
  <div id="legend-wrap">
    <h3>Communities</h3>
    <div id="legend-controls">
      <label><input type="checkbox" id="select-all-cb" checked onchange="toggleAllCommunities(!this.checked)">Select All</label>
    </div>
    <div id="legend"></div>
  </div>
  <div id="stats">{stats}</div>
</div>
{script_tags}
{_html_script(nd_refs, ed_refs, legend_json, physics=False, smooth=False, hide_edges_on_zoom=True)}
</body>
</html>"""

    write_text_atomic(output_path, html)
    return True

