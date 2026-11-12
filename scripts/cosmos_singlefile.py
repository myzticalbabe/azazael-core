#!/usr/bin/env python3
"""cosmos_singlefile.py — build a single-file standalone Cosmos viewer.

Reads an existing cosmos/ data dir (as written by cosmos_export.py), then
emits ONE self-contained HTML with everything embedded:

  - positions  quantized Float32 -> uint16 (min/max scaled)      ~2.2MB b64
  - sizes      quantized Float32 -> uint8  (max scaled)          ~0.5MB b64
  - links      uint32 pairs (as-is, base64)                      ~7MB   b64
  - comm       uint16 community ids (as-is)                      ~1.1MB b64
  - labels     gzip(base64) of the full labels JSON              ~10-15MB b64
  - tree       gzip(base64) of the mind-map JSON                 ~1-2MB  b64
  - viewer     esbuild bundle inlined

The labels JSON is the bulk (103MB plain) and is extremely repetitive, so
gzip crushes it; the browser decompresses with DecompressionStream('gzip').
The positions/sizes are already a precomputed static layout, so the page
renders instantly at that layout (physics can stay off).

Usage:
  python cosmos_singlefile.py <cosmos-dir> <out.html> [--bundle viewer.bundle.js]
"""
from __future__ import annotations

import base64
import gzip
import json
import re
import struct
import sys
from pathlib import Path


def _b64_decode(data: str) -> bytes:
    return base64.b64decode(data)


def _extract_atob(text: str) -> bytes:
    """Pull the base64 payload out of `...atob('XXXX')...` JS."""
    m = re.search(r"atob\('([^']+)'\)", text)
    if not m:
        raise ValueError("no atob payload found")
    return _b64_decode(m.group(1))


def _extract_float32(text: str):
    raw = _extract_atob(text)
    return list(struct.unpack(f"<{len(raw)//4}f", raw))


def _extract_uint32(text: str):
    raw = _extract_atob(text)
    return struct.unpack(f"<{len(raw)//4}I", raw)


def _extract_uint16(text: str):
    raw = _extract_atob(text)
    return struct.unpack(f"<{len(raw)//2}H", raw)


def _extract_json_literal(text: str, prefix: str):
    """Extract the JSON literal after `prefix` (e.g. 'window.GV_TREE = ')."""
    start = text.index(prefix) + len(prefix)
    # find the matching closing brace/bracket
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError(f"could not parse JSON after {prefix!r}")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _u16_quant(values, lo: float, hi: float):
    span = hi - lo or 1.0
    return [max(0, min(65535, round((v - lo) / span * 65535))) for v in values]


def _u8_quant(values, hi: float):
    span = hi or 1.0
    return [max(0, min(255, round(v / span * 255))) for v in values]


def build(cosmos_dir: Path, out_path: Path, bundle: Path, label: str) -> None:
    print(f"[singlefile] reading {cosmos_dir} ...")
    pos_js = (cosmos_dir / "graph.pos.js").read_text(encoding="utf-8")
    links_js = (cosmos_dir / "graph.links.js").read_text(encoding="utf-8")
    comm_js = (cosmos_dir / "graph.comm.js").read_text(encoding="utf-8")
    size_js = (cosmos_dir / "graph.size.js").read_text(encoding="utf-8")

    positions = _extract_float32(pos_js)
    links = _extract_uint32(links_js)
    comm = _extract_uint16(comm_js)
    sizes = _extract_float32(size_js)
    n = len(comm)
    print(f"[singlefile] {n} nodes, {len(links)//2} links, {len(positions)//2} coord pairs")

    # labels: reassemble from chunk files (they hold JSON arrays, not base64)
    labels: list = []
    for chunk_file in sorted(cosmos_dir.glob("graph.labels.[0-9]*.js")):
        text = chunk_file.read_text(encoding="utf-8")
        m = re.search(r"GV_LABELS_CHUNKS\.push\((.*)\);", text, re.S)
        if m:
            labels.extend(json.loads(m.group(1)))
    print(f"[singlefile] {len(labels)} labels collected")

    tree_js = (cosmos_dir / "graph.tree.js").read_text(encoding="utf-8")
    tree = _extract_json_literal(tree_js, "window.GV_TREE = ")
    tree["label"] = label

    # quantize
    plo, phi = min(positions), max(positions)
    pos_q = _u16_quant(positions, plo, phi)
    smax = max(sizes) if sizes else 1.0
    size_q = _u8_quant(sizes, smax)

    labels_gz = gzip.compress(json.dumps(labels).encode("utf-8"), compresslevel=9)
    tree_gz = gzip.compress(json.dumps(tree).encode("utf-8"), compresslevel=9)

    pos_q_b64 = _b64(struct.pack(f"<{len(pos_q)}H", *pos_q))
    size_q_b64 = _b64(struct.pack(f"<{len(size_q)}B", *size_q))
    links_b64 = _b64(struct.pack(f"<{len(links)}I", *links))
    comm_b64 = _b64(struct.pack(f"<{len(comm)}H", *comm))
    labels_b64 = _b64(labels_gz)
    tree_b64 = _b64(tree_gz)

    bundle_src = bundle.read_text(encoding="utf-8")

    payload = {
        "label": label,
        "n": n,
        "links": len(links) // 2,
        "communities": len(tree.get("communities", [])),
    }
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{label} — Cosmos (single-file)</title>
<style>
  html, body {{ margin:0; height:100%; background:#0f0f1a; color:#ddd; font-family:'Segoe UI',system-ui,sans-serif; overflow:hidden; }}
  #graph {{ position:fixed; inset:0; display:none; }}
  #loading {{ position:fixed; inset:0; display:flex; align-items:center; justify-content:center; font-size:15px; color:#9aa; z-index:5; background:#0f0f1a; }}
  #loading .spin {{ width:34px; height:34px; border:3px solid #334; border-top-color:#4E79A7; border-radius:50%; margin-right:14px; animation:sp 1s linear infinite; }}
  @keyframes sp {{ to {{ transform:rotate(360deg); }} }}
  #sidebar {{ position:fixed; left:0; top:0; bottom:0; width:290px; background:#14142a; border-right:1px solid #26263f; z-index:4; display:flex; flex-direction:column; }}
  #topbar {{ position:fixed; left:290px; right:0; top:0; height:44px; background:#14142a; border-bottom:1px solid #26263f; z-index:4; display:flex; align-items:center; gap:10px; padding:0 12px; }}
  #search {{ width:320px; background:#1c1c38; color:#eee; border:1px solid #33336a; border-radius:6px; padding:7px 10px; font-size:13px; outline:none; }}
  #total-label {{ color:#889; font-size:12px; }}
  #tabs {{ display:flex; gap:2px; padding:8px 10px 0; }}
  .tab {{ flex:1; text-align:center; padding:7px 0; font-size:12px; color:#889; cursor:pointer; border-bottom:2px solid transparent; user-select:none; }}
  .tab.active {{ color:#eee; border-bottom-color:#4E79A7; }}
  .pane {{ flex:1; overflow-y:auto; padding:4px 10px 20px; }}
  .tree-item, .comm-item {{ display:flex; align-items:center; gap:6px; padding:3px 6px; border-radius:5px; cursor:pointer; font-size:12px; color:#ccd; white-space:nowrap; }}
  .tree-item:hover, .comm-item:hover {{ background:#20203e; }}
  .tw {{ color:#4E79A7; width:10px; flex:none; }}
  .tl {{ overflow:hidden; text-overflow:ellipsis; }}
  .tc, .comm-count {{ margin-left:auto; color:#667; font-size:11px; }}
  .comm-dot {{ width:9px; height:9px; border-radius:50%; flex:none; }}
  #info {{ position:fixed; right:12px; top:52px; width:300px; max-height:60vh; overflow-y:auto; background:#14142a; border:1px solid #26263f; border-radius:8px; padding:12px; font-size:12px; z-index:4; }}
  .field {{ margin-bottom:6px; word-break:break-all; }}
  .neighbor-link {{ display:block; padding:2px 0 2px 8px; border-left:3px solid #4E79A7; color:#aab; cursor:pointer; }}
  .neighbor-link:hover {{ color:#fff; }}
  #search-results {{ position:fixed; left:302px; top:50px; width:340px; max-height:60vh; overflow-y:auto; background:#14142a; border:1px solid #26263f; border-radius:8px; z-index:6; display:none; }}
  .search-item {{ padding:6px 10px; font-size:12px; color:#ccd; cursor:pointer; border-bottom:1px solid #1c1c38; }}
  .search-item:hover {{ background:#20203e; color:#fff; }}
</style>
</head>
<body>
<div id="loading"><div class="spin"></div><span>loading {label} — {payload['n']:,} nodes, {payload['links']:,} links…</span></div>
<div id="graph"></div>
<div id="sidebar">
  <div id="tabs"><div class="tab active" id="tab-tree">Mind-map</div><div class="tab" id="tab-comm">Communities</div></div>
  <div class="pane" id="tree-pane"></div>
  <div class="pane" id="comm-pane" style="display:none"></div>
</div>
<div id="topbar"><input id="search" placeholder="search {label} (404k nodes)…"><span id="total-label"></span></div>
<div id="search-results"></div>
<div id="info"><span class="empty">Click a node to inspect it</span></div>
<script>
window.GV_LABELS_GZ = "{labels_b64}";
window.GV_TREE_GZ = "{tree_b64}";
window.GV_POS_Q = {{ data: new Uint16Array(Uint8Array.from(atob("{pos_q_b64}"), c=>c.charCodeAt(0)).buffer), min: {plo:.4f}, max: {phi:.4f} }};
window.GV_SIZE_Q = {{ data: new Uint8Array(Uint8Array.from(atob("{size_q_b64}"), c=>c.charCodeAt(0)).buffer), max: {smax:.4f} }};
window.GV_LINKS = new Uint32Array(Uint8Array.from(atob("{links_b64}"), c=>c.charCodeAt(0)).buffer);
window.GV_COMM = new Uint16Array(Uint8Array.from(atob("{comm_b64}"), c=>c.charCodeAt(0)).buffer);
</script>
<script>
{bundle_src}
</script>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")
    mb = out_path.stat().st_size / (1024 * 1024)
    print(f"[singlefile] wrote {out_path} ({mb:.1f} MB)")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: cosmos_singlefile.py <cosmos-dir> <out.html> [--bundle FILE] [--label NAME]")
        return 2
    cosmos_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    bundle = Path(sys.argv[sys.argv.index("--bundle") + 1]) if "--bundle" in sys.argv else None
    label = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else "azazael"
    if bundle is None:
        # default: next to the script in cosmos-viewer/dist/
        bundle = Path(__file__).parent / "cosmos-viewer" / "dist" / "viewer.bundle.js"
    if not bundle.exists():
        print(f"error: bundle not found: {bundle}", file=sys.stderr)
        return 1
    build(cosmos_dir, out_path, bundle, label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
