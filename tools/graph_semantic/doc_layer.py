"""Doc-file layer: one `document` node per project doc, bucketed and cross-linked.

Mechanical extraction (not semantic): every doc gets an identity, a directory
bucket, and edges from markdown links / backticked path references. The semantic
layer authored by the LLM sits on top and supplies rationale.

Connectivity design — the point is that adding 1,300 docs must NOT add 1,300
islands:
- doc -> bucket concept        (guarantees every doc has >= 1 edge)
- doc -> doc                   (markdown links, when the target resolves)
- doc -> code node             (backticked paths that match a real AST file)
- bucket -> spine node         (attaches each bucket to the semantic layer,
                                which is itself anchored into the code graph)
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCOPE = json.loads(Path("graphify-out/_doc_scope.json").read_text(encoding="utf-8"))
INCLUDED: list[str] = SCOPE["included"]

IDX = json.loads(Path("graphify-out/_ast_index.json").read_text(encoding="utf-8"))
GRAPH = json.loads(Path("graphify-out/graph.json").read_text(encoding="utf-8"))
SEM = json.loads(Path("graphify-out/semantic_fragment.json").read_text(encoding="utf-8"))

# Paths already owned by an LLM-authored document node — skip so we don't double up.
SEM_DOC_PATHS = {n["source_file"] for n in SEM["nodes"] if n.get("file_type") == "document"}

# Code nodes by source_file, for doc -> code bridges.
code_by_sf: dict[str, list[tuple[int, str]]] = {}
deg: dict[str, int] = {}
for e in GRAPH["links"]:
    deg[e.get("source")] = deg.get(e.get("source"), 0) + 1
    deg[e.get("target")] = deg.get(e.get("target"), 0) + 1
for n in GRAPH["nodes"]:
    sf = n.get("source_file")
    if sf and n.get("file_type") == "code":
        code_by_sf.setdefault(sf, []).append((deg.get(n["id"], 0), n["id"]))
for sf in code_by_sf:
    code_by_sf[sf].sort(reverse=True)

ALL_DOCS = set(INCLUDED)


def _raw_stem(path: str) -> str:
    """Spec ID format: full repo-relative path, extension dropped, all segments
    lowercased with non-alphanumerics -> '_'."""
    p = path
    for ext in (".md",):
        if p.lower().endswith(ext):
            p = p[: -len(ext)]
    return re.sub(r"[^a-z0-9]+", "_", p.lower()).strip("_")


# Non-ASCII filenames (e.g. BIBL/\u0391.md, \u0393.md) normalize to the same id, and
# an ID must identify exactly one entity. Disambiguate colliding stems with a short
# digest of the real path rather than a sequence number, so the id stays stable.
_stem_counts: dict[str, int] = {}
for _p in INCLUDED:
    _stem_counts[_raw_stem(_p)] = _stem_counts.get(_raw_stem(_p), 0) + 1


def stem(path: str) -> str:
    s = _raw_stem(path)
    if _stem_counts.get(s, 0) > 1:
        return f"{s}_{hashlib.sha1(path.encode('utf-8')).hexdigest()[:6]}"
    return s


def bucket_of(path: str) -> str:
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    if parts[0] == "skills":
        # family = the token after the leading vendor prefix (azazael-, azazael_pkg-, ...)
        name = parts[1] if len(parts) > 1 else ""
        m = re.match(r"^azazael[-_]([a-z0-9]+)", name)
        fam = m.group(1) if m else (name.split("-")[0] or "misc")
        return f"skills/{fam}"
    if parts[0] == "src":
        return "src/" + (parts[1] if len(parts) > 2 else "(file)")
    return parts[0]


# Generated API docs are titled after the code file they document (e.g.
# docs/core/diagnostics_API.md is titled "diagnostics.py"). graphify dedups by
# label, so such a doc node would be merged into the code node and lost. Keep
# doc labels distinct from code filenames and from each other.
CODE_BASENAMES = {
    Path(n["source_file"]).name.lower()
    for n in GRAPH["nodes"]
    if n.get("source_file") and n.get("file_type") == "code"
}
_used_labels: dict[str, int] = {}


def label_of(path: str) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    title = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            title = s[2:].strip()[:120]
            break
    if not title:
        title = Path(path).stem
    # A title that is literally a code filename would collide with that code node.
    if title.lower() in CODE_BASENAMES:
        title = f"{title} (documentation)"
    key = re.sub(r"[^a-z0-9]+", "", title.lower())
    if _used_labels.get(key):
        title = f"{title} [{Path(path).name}]"
    _used_labels[key] = _used_labels.get(key, 0) + 1
    return title


# Bucket -> spine node, so every bucket attaches to the connected semantic layer.
BUCKET_SPINE: dict[str, str] = {
    "(root)": "system_instructions_rule_0a",
    "docs": "docs_readme_index",
    "reports": "reports_error_lesson_audit",
    "research": "research_subdomains_doc",
    "ADRs": "docs_adr_001_tiered_architecture",
    "algorithms": "terence_doc",
    "packages": "packages_azazael_core_readme",
    "scripts": "graphify_atlas_doc",
    "src/CODEX": "codex_video_generation",
    "src/ORCHESTRATION": "ml_platforms_harness",
    "src/UTILITIES": "terence_doc",
    "src/AOA": "aoa_armorie_of_anubis",
    "src/CORE": "anti_pattern_scanner",
    "src/HYDRA": "hydra_cluster",
    "src/ML": "ml_package_readme",
}
SKILLS_SPINE = "skills_catalog_doc"
SEM_IDS = {n["id"] for n in SEM["nodes"]}

nodes: list[dict] = []
edges: list[dict] = []
seen_buckets: set[str] = set()
edge_keys: set[tuple] = set()


def add_edge(s: str, t: str, rel: str, conf: str, score: float, sf: str) -> None:
    k = (s, t, rel)
    if k in edge_keys or s == t:
        return
    edge_keys.add(k)
    edges.append({"source": s, "target": t, "relation": rel,
                  "confidence": conf, "confidence_score": score,
                  "source_file": sf, "source_location": None, "weight": 1.0})


def bucket_id(b: str) -> str:
    return "dir_" + re.sub(r"[^a-z0-9]+", "_", b.lower()).strip("_")


docs_by_path: dict[str, str] = {}
for path in INCLUDED:
    if path in SEM_DOC_PATHS:
        continue
    nid = stem(path)
    docs_by_path[path] = nid
    b = bucket_of(path)
    bid = bucket_id(b)
    if b not in seen_buckets:
        seen_buckets.add(b)
        nodes.append({"id": bid, "label": f"{b} (directory)", "file_type": "concept",
                      "source_file": None, "source_location": None, "source_url": None,
                      "captured_at": None, "author": None, "contributor": None,
                      "rationale": f"Documentation directory bucket grouping {b}."})
        spine = BUCKET_SPINE.get(b, SKILLS_SPINE if b.startswith("skills/") else None)
        if spine and spine in SEM_IDS:
            add_edge(bid, spine, "references", "INFERRED", 0.85, path)
    nodes.append({"id": nid, "label": label_of(path), "file_type": "document",
                  "source_file": path, "source_location": None, "source_url": None,
                  "captured_at": None, "author": None, "contributor": None})
    add_edge(nid, bid, "references", "EXTRACTED", 1.0, path)

# --- Cross-references: markdown links and backticked paths ---
LINK_RE = re.compile(r"\]\(\s*([^)\s]+?)\s*\)")
CODE_RE = re.compile(r"`([A-Za-z0-9_./\-]+\.(?:py|md|json|js|ts|go|rs|toml|ini|cfg|bat|sh|yaml|yml|sql))`")
linked_docs = 0
linked_code = 0
for path, nid in docs_by_path.items():
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    base = Path(path).parent
    targets = [m.group(1) for m in LINK_RE.finditer(text)]
    targets += [m.group(1) for m in CODE_RE.finditer(text)]
    for raw in targets:
        raw = raw.split("#")[0].strip()
        if not raw or raw.startswith(("http://", "https://", "mailto:")):
            continue
        try:
            resolved = (base / raw).resolve().relative_to(Path(".").resolve()).as_posix()
        except (ValueError, OSError):
            continue
        if resolved in docs_by_path and resolved != path:
            add_edge(nid, docs_by_path[resolved], "references", "EXTRACTED", 1.0, path)
            linked_docs += 1
        elif resolved in code_by_sf:
            for _, cid in code_by_sf[resolved][:2]:
                add_edge(nid, cid, "references", "EXTRACTED", 1.0, path)
                linked_code += 1

print(f"doc nodes: {len(docs_by_path)}  buckets: {len(seen_buckets)}")
print(f"edges: {len(edges)}  (doc->doc {linked_docs}, doc->code {linked_code})")
print(f"skipped (already have an LLM doc node): {len(SEM_DOC_PATHS)}")

frag = {"nodes": nodes, "edges": edges, "hyperedges": [],
        "input_tokens": 0, "output_tokens": 0}
out = Path("graphify-out/doc_layer_fragment.json")
out.write_text(json.dumps(frag, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote", out, f"{out.stat().st_size / 1024:.0f} KB")