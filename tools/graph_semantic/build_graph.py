"""Merge the semantic fragment into graph.json, re-cluster, label, and export.

Mirrors what `graphify cluster-only` / the skill merge path does, but with the
semantic layer supplied by the LLM (the agent) and the community labels authored
by the agent for the largest communities.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import networkx as nx

from graphify.build import build_merge
from graphify.cluster import cluster, label_communities_by_hub
from graphify.export import attach_hyperedges, to_json

GRAPH = Path("graphify-out/graph.json")
FRAGS = [
    Path("graphify-out/semantic_fragment.json"),
    Path("graphify-out/doc_layer_fragment.json"),
    Path("graphify-out/concept_layer_fragment.json"),
    Path("graphify-out/coverage_layer_fragment.json"),
    Path("graphify-out/rationale_tranche2_fragment.json"),
    Path("graphify-out/rationale_tranche3_fragment.json"),
    Path("graphify-out/enhanced_semantic_fragment.json"),
    Path("graphify-out/bridge_layer_fragment.json"),
]

chunks = [json.loads(p.read_text(encoding="utf-8")) for p in FRAGS]
frag = chunks[0]
for p, c in zip(FRAGS, chunks):
    print(f"  chunk {p.name}: {len(c['nodes'])} nodes {len(c['edges'])} edges")
hyperedges = [h for c in chunks for h in c.get("hyperedges", [])]

# --- Fair before/after: same function, no new chunks. ---
G_before = build_merge([], str(GRAPH), root=".")
comp_before = nx.number_connected_components(G_before)
n_before, e_before = G_before.number_of_nodes(), G_before.number_of_edges()
print(f"BEFORE  nodes={n_before} edges={e_before} components={comp_before}")

G = build_merge(chunks, str(GRAPH), root=".")

# Self-loops carry no information in an undirected graph (source == target) and
# graphify's diagnose reports them; drop them so the exported graph is loop-free.
loops = list(nx.selfloop_edges(G))
if loops:
    G.remove_edges_from(loops)
    print(f"removed {len(loops)} self-loop edge(s)")

comp_after = nx.number_connected_components(G)
print(f"AFTER   nodes={G.number_of_nodes()} edges={G.number_of_edges()} components={comp_after}")

sizes = sorted((len(c) for c in nx.connected_components(G)), reverse=True)
print("largest components:", sizes[:12])
print("isolated nodes:", sum(1 for s in sizes if s == 1))

# --- Cluster ---
communities = cluster(G)
print(f"communities={len(communities)}")

# --- Labels: hub-derived base, then agent labels for the big ones ---
labels = label_communities_by_hub(G, communities)
AGENT_LABELS = {
    "src/CORE/worm_registry.py": "Worm Registry & Dynamic Resolution",
    "src/CORE/direct_memory.py": "DirectMemory Session Recording",
    "src/CORE/local_memory.py": "LocalMemoryStore / PAMS",
    "src/CORE/memorybus.py": "MemoryBus Pub/Sub",
    "src/CORE/compliance/scanner.py": "Anti-Pattern Scanner & Gates",
}
for cid, members in communities.items():
    present = [m for m in members if m in G]
    if not present:
        continue
    hub = min(present, key=lambda n: (-G.degree(n), str(n)))
    sf = str(G.nodes[hub].get("source_file") or "")
    if sf in AGENT_LABELS:
        labels[cid] = AGENT_LABELS[sf]

attach_hyperedges(G, hyperedges)
ok = to_json(G, communities, str(GRAPH), force=True, community_labels=labels)
print("exported:", ok)

# --- Verify what landed on disk ---
g = json.loads(GRAPH.read_text(encoding="utf-8"))
sem = [n for n in g["nodes"] if n.get("file_type") in ("document", "rationale", "concept")]
print(f"on-disk nodes={len(g['nodes'])} links={len(g.get('links', g.get('edges', [])))}")
print(f"on-disk document/rationale/concept nodes={len(sem)}")
print(f"on-disk hyperedges={len(g.get('hyperedges', []))}")
print(f"on-disk communities={len(g.get('communities', {}))}")
rationales = [n for n in sem if n.get("rationale")]
print(f"nodes carrying a rationale attribute={len(rationales)}")
print("BEFORE/AFTER components:", comp_before, "->", comp_after)