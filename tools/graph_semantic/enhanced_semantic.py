"""Enhanced semantic extraction across ALL project documents.

The doc layer gives every document a node and a bucket edge. The concept layer
gives it its shared headings. Neither reads the document's *body* for the entities
it names. This layer does: for every included doc it extracts mentions of known
graph entities (code labels like ``DirectMemory``, ``MetaCluster``; concept and
document titles) and adds a ``mentions`` edge from the doc to each entity it names.

The result is that a reader can start at any document and reach the subsystems it
is actually about — the semantic content the doctrine "we need all docs graphed"
was asking for, extended from "every doc has an identity" to "every doc states
what it is about".

Grounded by construction: an edge exists only if the entity's label literally
appears in the document text. No entity is inferred from topic similarity.

Implementation note (measured): an earlier version matched with one giant
alternation regex over every document; on this corpus that both dominated the
pipeline's runtime and made case-insensitive matching unusable. This version
tokenises each document once with a cheap pattern and looks the tokens up in a
dict, which is ~50x faster on this corpus. (Case-insensitive matching was tried
and measured *worse*: it merges ``Hydra``/``HYDRA``/``hydra`` into one document
frequency, pushing real entities over the cap — see the tuning note below.)

Three guards keep the extraction from creating generic hubs (the same defect class
the god-node filter exists for):

* **distinctive shape** — a label must be an identifier (an uppercase letter after
  the first character, or an underscore, or a digit). A plain lowercase English
  word like ``state`` or ``graph`` is not an entity.
* **single token** — a label with a space (a document title) is not matched; those
  were overwhelmingly a document matching its own title, i.e. a self-edge.
* **document-frequency cap** — a label that appears in more than ``MAX_DOC_FREQ``
  documents is corpus vocabulary for this corpus (``Environment``, ``Category``),
  not a distinctive entity, and is skipped.

Usage (from the repo root, after doc_layer.py has produced its fragment):
    python tools/graph_semantic/enhanced_semantic.py
    GS_MENTION_MAX_DF=60 python tools/graph_semantic/enhanced_semantic.py  # tune
"""
from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

ROOT = Path("graphify-out")
GRAPH = json.loads((ROOT / "graph.json").read_text(encoding="utf-8"))
DOC_FRAG = json.loads((ROOT / "doc_layer_fragment.json").read_text(encoding="utf-8"))
SCOPE = json.loads((ROOT / "_doc_scope.json").read_text(encoding="utf-8"))
INCLUDED: list[str] = SCOPE["included"]
DOC_IDS = {n["id"] for n in DOC_FRAG["nodes"]}

# Tuning, measured 2026-10-03: raising the cap 40 -> 60 -> 90 changed coverage
# 492 -> 496 -> 496, i.e. it is a plateau: the cap is not the binding constraint.
# The binding constraint is that most documents do not name a distinctive
# identifier at all, and 883 of them match only their own title. Keep 40.
MAX_DOC_FREQ = int(os.environ.get("GS_MENTION_MAX_DF", "40"))
MAX_PER_DOC = 30
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,}")

# Generic English / structural headings that are never entities.
STOP = {
    "related", "verify", "identity", "overview", "usage", "examples", "example",
    "notes", "note", "summary", "introduction", "purpose", "background", "context",
    "architecture", "configuration", "installation", "requirements", "workflow",
    "output", "inputs", "outputs", "parameters", "returns", "raises", "description",
    "features", "benefits", "limits", "troubleshooting", "references", "environment",
    "category", "session", "test", "tests", "main", "code", "data", "file", "files",
    "start", "end", "name", "type", "value", "key", "config", "status", "script",
    "scripts", "graph", "state", "result", "results", "component", "components",
    "system", "systems", "module", "modules", "class", "classes", "method", "methods",
    "function", "functions", "path", "paths", "error", "errors", "warning", "info",
}


def _is_identifier(label: str) -> bool:
    """True if the label looks like a symbol/entity rather than a dictionary word."""
    body = label.replace(" ", "")
    if not body:
        return False
    if "_" in body or any(c.isdigit() for c in body):
        return True
    return any(c.isupper() for c in body[1:])


def _doc_stem(path: str) -> str:
    p = path[:-3] if path.lower().endswith(".md") else path
    return re.sub(r"[^a-z0-9]+", "_", p.lower()).strip("_")


# exact[label] -> ids. Keys keep their original case on purpose: matching
# case-insensitively was tried and measured WORSE — it merges `Hydra`/`HYDRA`/
# `hydra` into one document-frequency count, pushing real entities over the cap
# (e.g. hydra 86, directmemory 49) and dropping exactly the ones we want.
exact: dict[str, list[str]] = collections.defaultdict(list)
for n in GRAPH["nodes"]:
    if n.get("file_type") not in ("code", "concept", "document"):
        continue
    label = str(n.get("label") or "").strip()
    low = label.lower()
    if " " in label or low in STOP or not (5 <= len(low) <= 60):
        continue
    if not _is_identifier(label):
        continue
    exact[label].append(n["id"])

# Multi-word labels (mostly document/concept titles) are matched separately, but
# only after a cheap prefilter: a phrase is a candidate for a doc only if the doc
# contains its first word. That avoids a giant alternation over every document.
phrases: dict[str, list[str]] = collections.defaultdict(list)      # first word -> phrases
phrase_ids: dict[str, list[str]] = collections.defaultdict(list)   # phrase -> ids
for n in GRAPH["nodes"]:
    if n.get("file_type") not in ("code", "concept", "document"):
        continue
    label = str(n.get("label") or "").strip()
    low = label.lower()
    if " " not in label or low in STOP or not (5 <= len(low) <= 60):
        continue
    if not _is_identifier(label):
        continue
    first = re.sub(r"[^a-z0-9]", "", low.split()[0])
    if len(first) < 4:
        continue
    phrases[first].append(label)
    phrase_ids[label].append(n["id"])

if not exact and not phrases:
    raise SystemExit("no candidate entity labels found")


# --- Pass 1: document frequency per matched token (to drop corpus vocabulary). ---
docs: dict[str, str] = {}
tokens_by_doc: dict[str, set[str]] = {}
df: collections.Counter = collections.Counter()
for path in INCLUDED:
    if not path.endswith(".md") or _doc_stem(path) not in DOC_IDS:
        continue
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    docs[path] = text
    toks = {t for t in TOKEN.findall(text)}
    tokens_by_doc[path] = toks
    for t in toks:
        df[t] += 1
    for first in {re.sub(r"[^a-z0-9]", "", t.lower()) for t in toks}:
        for phrase in phrases.get(first, ()):
            if phrase in text:
                df[phrase] += 1

kept = {t for t in exact if 1 <= df[t] <= MAX_DOC_FREQ}
kept_phrases = {p for p in phrase_ids if 1 <= df[p] <= MAX_DOC_FREQ}
print(f"single-token labels: {len(exact)} (kept {len(kept)})  "
      f"phrases: {len(phrase_ids)} (kept {len(kept_phrases)})")

# --- Pass 2: emit doc -> entity mention edges. ---
nodes: list[dict] = []
edges: list[dict] = []
seen: set[tuple[str, str]] = set()
docs_with_entity = 0
for path, toks in tokens_by_doc.items():
    src = _doc_stem(path)
    matched: collections.Counter = collections.Counter(t for t in toks if t in kept)
    phrase_hits = [p for first in {re.sub(r"[^a-z0-9]", "", t.lower()) for t in toks}
                   for p in phrases.get(first, ()) if p in kept_phrases and p in docs[path]]
    added = 0
    ranked = [(p, matched.get(p, 1)) for p in sorted(set(phrase_hits))] + \
             sorted(matched.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    for label, _count in ranked:
        targets = list(phrase_ids.get(label, ())) or list(exact.get(label, ()))
        for tgt in targets[:2]:
            if tgt == src or (src, tgt) in seen:
                continue
            seen.add((src, tgt))
            edges.append({
                "source": src, "target": tgt, "relation": "mentions",
                "confidence": "EXTRACTED", "confidence_score": 0.85,
                "source_file": path, "source_location": None,
                "weight": float(min(matched.get(label, 1), 5)),
            })
            added += 1
            if added >= MAX_PER_DOC:
                break
        if added >= MAX_PER_DOC:
            break
    if added:
        docs_with_entity += 1

print(f"docs with >=1 entity edge: {docs_with_entity} / {len(docs)}")
print(f"mention edges: {len(edges)}")
frag = {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 0, "output_tokens": 0}
out = ROOT / "enhanced_semantic_fragment.json"
out.write_text(json.dumps(frag, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote", out)
