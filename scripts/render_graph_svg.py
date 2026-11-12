#!/usr/bin/env python3
"""render_graph_svg.py — static, dependency-free SVG render of any pipeline graph.

Reads a graph.json produced by learning_loop_graph.py / ecosystem_graph.py and
writes a color-coded SVG: node color by area (ecosystem) or node type
(learning-loop), node size by degree, glowing border for live-active nodes
(event count > 0), edges as translucent lines, plus an in-SVG legend.

matplotlib/PIL are broken on this 3.14t stack (invalid ELF header wheels), so
this uses only networkx + stdlib to keep the pipeline dependency-free.

Usage:
  python3 scripts/render_graph_svg.py graphs/ecosystem/graph.json --mode area
  python3 scripts/render_graph_svg.py graphs/learning_loop/graph.json --mode type
"""
import argparse
import json
import math
import os
import sys

try:
    import networkx as nx
except ImportError:
    sys.exit("networkx required")

AREA_COLORS = {
    "CORE": "#3fb950", "scripts": "#58a6ff", "SCRIPTS": "#58a6ff",
    "ORCHESTRATION": "#d29922", "HYDRA": "#d29922", "CODEX": "#a371f7",
    "UTILITIES": "#f778ba", "TOOLS": "#f778ba", "AZAZAEL_TOOLKIT": "#f778ba",
    "META": "#e3b341", "eve_unleashed": "#f0883e", "tests": "#8b949e",
    "INTERPRETERS": "#39c5cf", "DAEMONS": "#39c5cf", "CLI": "#39c5cf",
}
TYPE_COLORS = {"module": "#3fb950", "external": "#ffd700", "event": "#58a6ff",
               "strategy": "#ff8844", "action": "#aa66ff"}
FALLBACK = ["#f47067", "#7ee787", "#79c0ff", "#ffa657", "#d2a8ff", "#ff7b72",
            "#a5d6ff", "#ffc680", "#c69026", "#ffa198", "#56d4dd", "#e3b341",
            "#bc8cff", "#76e3ea", "#f778ba"]
# spaced hues for Louvain communities (distinguishable at 300+ communities)
COMMUNITY_PALETTE = ["#f47067", "#7ee787", "#79c0ff", "#ffa657", "#d2a8ff",
                     "#ff7b72", "#a5d6ff", "#ffc680", "#c69026", "#ffa198",
                     "#56d4dd", "#e3b341", "#bc8cff", "#76e3ea", "#f778ba",
                     "#ff9bce", "#7ce38f", "#8bd5ca", "#e6edf3", "#f5a623"]


def community_color(ci: int) -> str:
    if ci < len(COMMUNITY_PALETTE):
        return COMMUNITY_PALETTE[ci]
    # deterministic golden-angle hue spacing for the long tail
    import colorsys
    h = (ci * 0.61803398875) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.75, 0.95)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def load_communities(sna_path):
    """label -> community index from sna_results.json (members are labels)."""
    with open(sna_path, encoding="utf-8") as f:
        sna = json.load(f)
    lab_to_comm = {}
    for c in sna.get("communities", []):
        for m in c.get("members", []):
            lab_to_comm[m] = c["index"]
    return lab_to_comm


def load_betweenness(sna_path):
    """label -> betweenness from sna_results.json (keyed by label)."""
    with open(sna_path, encoding="utf-8") as f:
        sna = json.load(f)
    return dict(sna.get("centrality", {}).get("betweenness", {}))


def area_color(area):
    if area in AREA_COLORS:
        return AREA_COLORS[area]
    return FALLBACK[sum(ord(c) for c in area) % len(FALLBACK)]


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(r, g, b):
    return "#%02x%02x%02x" % (int(r), int(g), int(b))


def lighten(h, amt=0.35):
    r, g, b = hex_to_rgb(h)
    return rgb_to_hex(r + (255 - r) * amt, g + (255 - g) * amt, b + (255 - b) * amt)


def build(G, data, mode, lab_to_comm=None, size_mode="degree", lab_to_bet=None):
    """Set node colors/sizes on G from graph.json node records."""
    deg = dict(G.degree())
    max_deg = max(deg.values()) or 1
    bet = lab_to_bet or {}
    max_bet = max(bet.values(), default=0.0) or 1e-9
    by_id = {n["id"]: n for n in data["nodes"]}
    for i in G.nodes():
        rec = by_id.get(G.nodes[i].get("id"), {})
        if mode == "area":
            col = area_color(rec.get("type", "?"))
        elif mode == "community":
            ci = (lab_to_comm or {}).get(rec.get("label", ""), 0)
            col = community_color(ci)
        else:
            col = TYPE_COLORS.get(rec.get("type", "module"), "#888")
        ev = rec.get("events", 0) or rec.get("count", 0)
        if size_mode == "betweenness":
            b = bet.get(rec.get("label", ""), 0.0)
            size = 3.5 + 26 * (b / max_bet)
        else:
            size = 3.5 + 10 * (deg.get(i, 1) / max_deg)
        G.nodes[i]["color"] = col
        G.nodes[i]["ev"] = ev
        G.nodes[i]["size"] = size * (1.3 if ev else 1.0)
        G.nodes[i]["label"] = rec.get("label", str(i))


def render_svg(G, data, mode, title, out):
    W, H, PAD = 1600, 1100, 80
    pos = nx.spring_layout(G, seed=7, k=1.6 / math.sqrt(max(G.number_of_nodes(), 1)))
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0, 1e-6)

    def px(p):
        return (PAD + (p[0] - x0) / span * (W - 2 * PAD),
                PAD + (p[1] - y0) / span * (H - 2 * PAD))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">']
    parts.append(f'<rect width="{W}" height="{H}" fill="#0d1117"/>')
    parts.append(f'<text x="{PAD}" y="42" fill="#c9d1d9" font-family="Tahoma,Arial" '
                 f'font-size="20" font-weight="bold">{title}</text>')
    parts.append(f'<text x="{PAD}" y="66" fill="#8b949e" font-family="Tahoma,Arial" '
                 f'font-size="13">{G.number_of_nodes()} nodes · {G.number_of_edges()} edges'
                 f' · {mode} coloring</text>')

    # edges (drawn first)
    for a, b in G.edges():
        ax, ay = px(pos[a])
        bx, by = px(pos[b])
        parts.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
                     f'stroke="#30363d" stroke-width="1" opacity="0.55"/>')

    # nodes
    for i in G.nodes():
        x, y = px(pos[i])
        d = G.nodes[i]
        r = d["size"]
        fill = d["color"]
        glow = d["ev"] > 0
        stroke = lighten(fill) if glow else "#1b1f24"
        sw = 3 if glow else 1
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{fill}" '
                     f'stroke="{stroke}" stroke-width="{sw}"/>')
        lab = d["label"].split("/")[-1]
        parts.append(f'<text x="{x:.1f}" y="{y + r + 11:.1f}" fill="#c9d1d9" '
                     f'font-family="Tahoma,Arial" font-size="10" text-anchor="middle" '
                     f'opacity="0.9">{lab}</text>')

    # legend
    colors = sorted(set(G.nodes[i]["color"] for i in G.nodes()))
    ly = H - 60
    parts.append(f'<text x="{PAD}" y="{ly - 16}" fill="#e6edf3" font-family="Tahoma,Arial" '
                 f'font-size="13" font-weight="bold">Legend — {mode}:</text>')
    x = PAD
    used = {}
    for i in G.nodes():
        used.setdefault(G.nodes[i]["color"], []).append(G.nodes[i]["label"])
    for c in colors:
        labels = used[c]
        sample = labels[0].split("/")[-1] if len(labels) == 1 else f"{len(labels)} nodes"
        parts.append(f'<circle cx="{x + 5}" cy="{ly - 4}" r="6" fill="{c}"/>')
        parts.append(f'<text x="{x + 16}" y="{ly}" fill="#c9d1d9" font-family="Tahoma,Arial" '
                     f'font-size="11">{sample}</text>')
        x += 16 + 8 + len(sample) * 6.2 + 14
    parts.append(f'<text x="{PAD}" y="{H - 24}" fill="#8b949e" font-family="Tahoma,Arial" '
                 f'font-size="11">glowing border = live-active (events in 24h) · '
                 f'size = degree</text>')
    parts.append("</svg>")

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out


def main():
    ap = argparse.ArgumentParser(description="Static SVG render of a pipeline graph.json")
    ap.add_argument("graph_json")
    ap.add_argument("--mode", choices=["area", "type", "community"], default="area",
                    help="node color scheme: area (source dir), type (module/event/...), "
                         "community (Louvain from sna_results.json)")
    ap.add_argument("--size", choices=["degree", "betweenness"], default="degree",
                    help="node size metric (betweenness reads sna_results.json)")
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true",
                    help="write community membership JSON next to the SVG")
    args = ap.parse_args()

    with open(args.graph_json, encoding="utf-8") as f:
        data = json.load(f)

    lab_to_comm = None
    lab_to_bet = None
    sna_path = os.path.join(os.path.dirname(os.path.abspath(args.graph_json)),
                            "sna_results.json")
    if args.mode == "community" or args.size == "betweenness" or args.json:
        if os.path.exists(sna_path):
            lab_to_comm = load_communities(sna_path)
            lab_to_bet = load_betweenness(sna_path)
            if args.mode == "community":
                print(f"community map: {len(lab_to_comm)} nodes assigned")
        else:
            print(f"[render_graph_svg] WARN: {sna_path} missing - falling back to area")
            if args.mode == "community":
                args.mode = "area"

    if args.json:
        _write_communities_json(args.graph_json, lab_to_comm or {})

    G = nx.Graph()
    for n in data["nodes"]:
        G.add_node(n["id"], id=n["id"])
    for e in data["links"]:
        G.add_edge(e["source"], e["target"])

    build(G, data, args.mode, lab_to_comm, args.size, lab_to_bet)
    if not args.out:
        args.out = os.path.splitext(args.graph_json)[0] + ".svg"
    title = os.path.basename(os.path.dirname(os.path.abspath(args.graph_json)))
    print("wrote", render_svg(G, data, args.mode, title, args.out))
    return 0


def _write_communities_json(graph_json, lab_to_comm):
    """Write per-community member lists next to the source graph.json."""
    out = os.path.join(os.path.dirname(os.path.abspath(graph_json)), "communities.json")
    by_comm = {}
    for label, ci in lab_to_comm.items():
        by_comm.setdefault(ci, []).append(label)
    payload = {"n_communities": len(by_comm),
               "communities": [{"index": ci, "n": len(members),
                                "members": sorted(members)}
                               for ci, members in sorted(by_comm.items())]}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"community memberships -> {out} ({len(by_comm)} communities)")


if __name__ == "__main__":
    sys.exit(main())