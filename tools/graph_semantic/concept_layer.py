"""Semantic concept layer: deduped concept nodes extracted from every doc's headings.

A heading is a document's own declaration of the concept it is about. Extracting
them across the whole corpus and deduping by normalized label turns recurring
concepts ("Python 3.14t free-threading", "sync direction") into real cross-document
hubs instead of 1,500 local headings.

IDs are `concept_<slug>` — deterministic from the label alone, so the same concept
always resolves to the same node whichever doc is processed first (the spec's
determinism requirement).
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
from pathlib import Path

SCOPE = json.loads(Path("graphify-out/_doc_scope.json").read_text(encoding="utf-8"))
INCLUDED: list[str] = SCOPE["included"]
SEM = json.loads(Path("graphify-out/semantic_fragment.json").read_text(encoding="utf-8"))
IDX = json.loads(Path("graphify-out/_ast_index.json").read_text(encoding="utf-8"))
GRAPH = json.loads(Path("graphify-out/graph.json").read_text(encoding="utf-8"))

SEM_DOC_PATHS = {n["source_file"] for n in SEM["nodes"] if n.get("file_type") == "document"}
SEM_IDS = {n["id"] for n in SEM["nodes"]}

# Boilerplate headings carry no project meaning.
STOP = {
    "overview", "usage", "examples", "example", "quick start", "quickstart",
    "table of contents", "contents", "notes", "note", "summary", "references",
    "installation", "install", "configuration", "config", "api", "api reference",
    "introduction", "background", "purpose", "scope", "conclusion", "appendix",
    "related documentation", "related docs", "see also", "commands", "command line",
    "key features", "features", "requirements", "prerequisites", "getting started",
    "troubleshooting", "faq", "changelog", "license", "contributing", "next steps",
    "file structure", "directory structure", "structure", "step 1", "step 2",
    "output", "inputs", "options", "flags", "parameters", "returns", "design",
    "implementation", "implementation details", "technical details", "how it works",
    "what this file does", "key functions", "key classes", "yaml frontmatter",
    "version", "authors", "author", "tags", "metrics", "results", "test results",
    "progress", "status", "roadmap", "milestones", "budget", "risks",
    # Template headings emitted by the generated skill docs — they appear in ~1,090
    # of 1,425 docs, so they are boilerplate, not shared meaning.
    "related", "related skills", "verify", "identity", "verification",
    "quick reference", "relationship to other files", "key files", "intent",
    "hard rules", "execute — python api", "execute — cli entry points",
    "execute — canonical invocations", "output contract", "see also",
}
# A heading appearing in this many docs is a template artefact, not a concept.
MAX_DOC_FREQ = 60
HEAD_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$", re.M)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:80]


def norm(text: str) -> str:
    t = re.sub(r"[`*_\[\]()#:.,;!?\"'|>]", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


# --- Collect heading -> docs ---
heading_docs: dict[str, set[str]] = collections.defaultdict(set)
heading_display: dict[str, str] = {}
heading_file: dict[str, str] = {}
for path in INCLUDED:
    if path in SEM_DOC_PATHS:
        continue
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for m in HEAD_RE.finditer(text):
        raw = m.group(2).strip()
        raw = re.sub(r"\s*\{#[\w\-]+\}\s*$", "", raw)          # anchor ids
        raw = re.sub(r"`", "", raw)
        n = norm(raw)
        if not n or n in STOP:
            continue
        if len(n) < 4 or len(n) > 70:
            continue
        if re.fullmatch(r"[\d\s.\-]+", n):                      # pure numbers
            continue
        if n.split()[0] in {"step", "phase", "pass", "part"} and len(n.split()) <= 2:
            continue
        heading_docs[n].add(path)
        heading_display.setdefault(n, raw[:120])
        heading_file.setdefault(n, path)

# --- Only shared concepts become nodes (>= 2 docs). Single-doc headings are local. ---
shared = {n: ds for n, ds in heading_docs.items() if 2 <= len(ds) <= MAX_DOC_FREQ}
print(f"distinct headings: {len(heading_docs)}  shared (2..{MAX_DOC_FREQ} docs): {len(shared)}")

# Doc node id must match the doc layer's stem(), including its collision
# disambiguation, or these edges land on the wrong node / dangle.
_doc_counts: dict[str, int] = {}
for _p in INCLUDED:
    _s = re.sub(r"[^a-z0-9]+", "_", _p[:-3].lower()).strip("_")
    _doc_counts[_s] = _doc_counts.get(_s, 0) + 1


def stem(path: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (path[:-3] if path.lower().endswith(".md") else path).lower()).strip("_")
    if _doc_counts.get(s, 0) > 1:
        return f"{s}_{hashlib.sha1(path.encode('utf-8')).hexdigest()[:6]}"
    return s


nodes: list[dict] = []
edges: list[dict] = []
for n, docs in sorted(shared.items(), key=lambda kv: -len(kv[1])):
    cid = "concept_" + slug(n)
    nodes.append({
        "id": cid,
        "label": heading_display[n],
        "file_type": "concept",
        "source_file": heading_file[n],
        "source_location": None, "source_url": None, "captured_at": None,
        "author": None, "contributor": None,
        "rationale": f"Shared concept declared as a heading in {len(docs)} documents.",
    })
    for p in sorted(docs):
        edges.append({
            "source": stem(p), "target": cid, "relation": "references",
            "confidence": "EXTRACTED", "confidence_score": 1.0,
            "source_file": p, "source_location": None, "weight": 1.0,
        })

# --- Anchor concepts to code where the concept name matches a real AST label ---
BY_LABEL = IDX["by_label"]
anchored = 0
for node in nodes:
    key = re.sub(r"[^a-z0-9]+", "", node["label"].lower())
    hits = BY_LABEL.get(key)
    if hits:
        edges.append({
            "source": node["id"], "target": hits[0], "relation": "references",
            "confidence": "INFERRED", "confidence_score": 0.95,
            "source_file": node["source_file"], "source_location": None, "weight": 1.0,
        })
        anchored += 1

hub_sizes = collections.Counter()
for e in edges:
    hub_sizes[e["target"]] += 1
print(f"concept nodes: {len(nodes)}  edges: {len(edges)}  code-anchored: {anchored}")
print("top concept hubs:")
for cid, c in hub_sizes.most_common(12):
    lab = next((x["label"] for x in nodes if x["id"] == cid), cid)
    print(f"  {c:4d}  {lab[:70]}")

frag = {"nodes": nodes, "edges": edges, "hyperedges": [],
        "input_tokens": 0, "output_tokens": 0}
out = Path("graphify-out/concept_layer_fragment.json")
out.write_text(json.dumps(frag, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote", out, f"{out.stat().st_size / 1024:.0f} KB")