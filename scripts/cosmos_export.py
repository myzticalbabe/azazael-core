#!/usr/bin/env python3
"""Convert a graphify graph.json into compact data files for the Cosmos viewer.

Cosmos (@cosmograph/cosmos) is a GPU-accelerated WebGL force-graph engine that
renders 400k+ nodes in real time — the thing vis-network could not do. The
browser cannot `fetch()` from file://, so this script emits script-tag-loadable
JS data files next to a static viewer HTML:

    cosmos/graph.pos.js    -> window.GV_POS  Float32Array [x0,y0,x1,y1,...]
    cosmos/graph.links.js  -> window.GV_LINKS Uint32Array [s0,t0,s1,t1,...]
    cosmos/graph.comm.js   -> window.GV_COMM Uint16Array community id per node
    cosmos/graph.size.js   -> window.GV_SIZE Float32Array per node
    cosmos/graph.labels.js -> window.GV_LABELS [{id,label,file,type,deg}]
    cosmos/graph.tree.js   -> window.GV_TREE {dirs, communities} (mind-map)
    cosmos-viewer.html     -> the viewer itself

Positions are a community-anchored golden-angle spiral computed here so the
graph opens with meaningful structure; Cosmos's GPU simulation then refines it
live (enableSimulation). Usage:

    python cosmos_export.py <graph.json> <out-dir> [--label azazael]
"""
from __future__ import annotations

import base64
import json
import math
import random
import struct
import sys
from pathlib import Path


def _f32_bytes(values: list[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode()


def _u32_bytes(values: list[int]) -> str:
    return base64.b64encode(struct.pack(f"<{len(values)}I", *values)).decode()


def _u16_bytes(values: list[int]) -> str:
    return base64.b64encode(struct.pack(f"<{len(values)}H", *values)).decode()


def build(graph_path: Path, out_dir: Path, label: str) -> None:
    print(f"[cosmos] loading {graph_path} ...")
    with open(graph_path, encoding="utf-8") as f:
        g = json.load(f)
    raw_nodes = g.get("nodes", [])
    raw_edges = g.get("links", g.get("edges", []))
    print(f"[cosmos] {len(raw_nodes)} nodes, {len(raw_edges)} edges")

    # node id -> index
    index_of = {n["id"]: i for i, n in enumerate(raw_nodes)}
    n = len(raw_nodes)

    # community colors (mirror graphify COMMUNITY_COLORS palette)
    PALETTE = [
        (0x4E, 0x79, 0xA7), (0xF2, 0x8E, 0x2B), (0xE1, 0x57, 0x59),
        (0x76, 0xB7, 0xB2), (0x59, 0xA1, 0x4F), (0xED, 0xC9, 0x48),
        (0xB0, 0x7A, 0xA1), (0xFF, 0x9D, 0xA7), (0x9C, 0x75, 0x5F),
        (0xBA, 0xB0, 0xAC), (0x86, 0xBC, 0xB6), (0xBE, 0xE1, 0x9E),
    ]

    # collect per-node fields
    comm_of: list[int] = []
    labels: list[dict] = []
    degrees = [0] * n
    for i, nd in enumerate(raw_nodes):
        cid = int(nd.get("community") or 0)
        comm_of.append(cid)
        labels.append({
            "id": nd.get("id", ""),
            "label": nd.get("label", ""),
            "file": nd.get("source_file", ""),
            "type": nd.get("file_type", ""),
        })
    for e in raw_edges:
        s = e.get("source") or e.get("from")
        t = e.get("target") or e.get("to")
        if s in index_of and t in index_of:
            degrees[index_of[s]] += 1
            degrees[index_of[t]] += 1
    for i, d in enumerate(degrees):
        labels[i]["deg"] = d

    # --- community-anchored spiral layout -------------------------------
    # communities ordered by size, placed on a golden-angle spiral; members
    # jittered in a disk around their community center (deterministic).
    comm_members: dict[int, list[int]] = {}
    for i, cid in enumerate(comm_of):
        comm_members.setdefault(cid, []).append(i)
    rng = random.Random(42)
    sized = sorted(comm_members.items(), key=lambda kv: -len(kv[1]))
    centers: dict[int, tuple[float, float]] = {}
    for i, (cid, members) in enumerate(sized):
        r = 60.0 * math.sqrt(i + 1)
        a = i * 2.399963229728653  # golden angle
        centers[cid] = (r * math.cos(a), r * math.sin(a))

    positions: list[float] = []
    for i in range(n):
        cid = comm_of[i] if comm_of[i] in centers else (sized[0][0] if sized else 0)
        cx, cy = centers.get(cid, (0.0, 0.0))
        r = 8.0 + 30.0 * math.sqrt(len(comm_members.get(cid, [])))
        a = rng.uniform(0, math.tau)
        rr = r * math.sqrt(rng.random())
        positions.append(cx + rr * math.cos(a))
        positions.append(cy + rr * math.sin(a))

    # --- links as index pairs --------------------------------------------
    links: list[int] = []
    for e in raw_edges:
        s = e.get("source") or e.get("from")
        t = e.get("target") or e.get("to")
        if s in index_of and t in index_of and index_of[s] != index_of[t]:
            links.append(index_of[s])
            links.append(index_of[t])

    # --- sizes: degree-scaled, log-normalized ----------------------------
    max_deg = max(degrees) if degrees else 1
    sizes = [1.0 + 4.0 * (math.log1p(d) / math.log1p(max_deg)) for d in degrees]

    # --- mind-map hierarchy (directories + communities) ------------------
    dirs: dict[str, dict] = {}
    for i, nd in enumerate(labels):
        path = nd["file"]
        if not path:
            # Nodes with no file -- concept / community hubs the extractor
            # emits without a "file" key value -- have no directory to belong
            # to. Without this guard Path(None) raised TypeError and aborted
            # the whole cosmos rebuild at the first such node (2026-10-09);
            # the community-histogram loop below already guards the same
            # field with `if f:`, so this is the missing half of that guard.
            continue
        parts = [p for p in Path(path).parts if p not in ("", ".", "\\", "/")]
        for k in range(1, len(parts) + 1):
            key = "/".join(parts[:k])
            d = dirs.setdefault(key, {"count": 0, "files": set(), "communities": {}})
            d["count"] += 1
            d["files"].add(path)
            d["communities"][comm_of[i]] = d["communities"].get(comm_of[i], 0) + 1
    tree_dirs = []
    for key in sorted(dirs):
        d = dirs[key]
        tree_dirs.append({
            "path": key,
            "count": d["count"],
            "files": len(d["files"]),
            "top_comm": sorted(d["communities"], key=d["communities"].get, reverse=True)[:5],
        })
    tree_comms = []
    for cid, members in sorted(comm_members.items()):
        top_files: dict[str, int] = {}
        for i in members:
            f = labels[i]["file"]
            if f:
                top_files[f] = top_files.get(f, 0) + 1
        tree_comms.append({
            "cid": cid,
            "count": len(members),
            "label": f"Community {cid}",
            "top_files": sorted(top_files, key=top_files.get, reverse=True)[:8],
        })

    # --- write files -------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    pos_b64 = _f32_bytes(positions)
    (out_dir / "graph.pos.js").write_text(
        f"window.GV_POS = new Float32Array(Uint8Array.from(atob('{pos_b64}'), c=>c.charCodeAt(0)).buffer);\n",
        encoding="utf-8",
    )
    links_b64 = _u32_bytes(links)
    (out_dir / "graph.links.js").write_text(
        f"window.GV_LINKS = new Uint32Array(Uint8Array.from(atob('{links_b64}'), c=>c.charCodeAt(0)).buffer);\n",
        encoding="utf-8",
    )
    comm_b64 = _u16_bytes(comm_of)
    (out_dir / "graph.comm.js").write_text(
        f"window.GV_COMM = new Uint16Array(Uint8Array.from(atob('{comm_b64}'), c=>c.charCodeAt(0)).buffer);\n",
        encoding="utf-8",
    )
    size_b64 = _f32_bytes(sizes)
    (out_dir / "graph.size.js").write_text(
        f"window.GV_SIZE = new Float32Array(Uint8Array.from(atob('{size_b64}'), c=>c.charCodeAt(0)).buffer);\n",
        encoding="utf-8",
    )
    # Chunk the labels into ~10MB files so no single <script> parse is huge
    # (the 100MB+ single-file case is exactly what made graph-full.html fail).
    labels_b64_all: list[str] = []
    chunk: list[dict] = []
    chunk_sz = 0
    for nd in labels:
        s = len(json.dumps(nd))
        if chunk and chunk_sz + s > 10_000_000:
            labels_b64_all.append(json.dumps(chunk))
            chunk, chunk_sz = [], 0
        chunk.append(nd)
        chunk_sz += s
    if chunk:
        labels_b64_all.append(json.dumps(chunk))
    for i, chunk_json in enumerate(labels_b64_all):
        (out_dir / f"graph.labels.{i}.js").write_text(
            f"window.GV_LABELS_CHUNKS = window.GV_LABELS_CHUNKS || [];\n"
            f"window.GV_LABELS_CHUNKS.push({chunk_json});\n",
            encoding="utf-8",
        )
    (out_dir / "graph.labels.js").write_text(
        "window.GV_LABELS = [].concat(...window.GV_LABELS_CHUNKS);\n", encoding="utf-8"
    )
    (out_dir / "graph.tree.js").write_text(
        f"window.GV_TREE = {json.dumps({'dirs': tree_dirs, 'communities': tree_comms, 'label': label})};\n",
        encoding="utf-8",
    )
    print(f"[cosmos] wrote {len(labels)} labels, {len(links)//2} links, "
          f"{len(comm_members)} communities -> {out_dir}")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: cosmos_export.py <graph.json> <out-dir> [--label NAME]")
        return 2
    graph_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    label = "graph"
    if "--label" in sys.argv:
        label = sys.argv[sys.argv.index("--label") + 1]
    if not graph_path.exists():
        print(f"error: {graph_path} not found", file=sys.stderr)
        return 1
    build(graph_path, out_dir, label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
