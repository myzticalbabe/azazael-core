"""Build a label -> node-id index of the AST code nodes so semantic edges can be
anchored to REAL code symbols instead of creating orphan ghosts."""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

g = json.loads(Path("graphify-out/graph.json").read_text(encoding="utf-8"))
nodes = g["nodes"]

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

index: dict[str, list[str]] = collections.defaultdict(list)
code_nodes = [n for n in nodes if n.get("_origin") == "ast" and n.get("file_type") == "code"]
for n in code_nodes:
    index[norm(n.get("label"))].append(n["id"])

# Also index by the trailing symbol name of the id (last underscore segment group)
by_tail: dict[str, list[str]] = collections.defaultdict(list)
for n in code_nodes:
    nid = n["id"]
    tail = nid.split("_")[-1]
    by_tail[tail].append(nid)

Path("graphify-out/_ast_index.json").write_text(json.dumps({
    "by_label": {k: v for k, v in index.items() if k},
    "by_tail": {k: v for k, v in by_tail.items() if k},
}, indent=0), encoding="utf-8")

print("code nodes:", len(code_nodes))
print("distinct labels:", len(index), "distinct tails:", len(by_tail))

# What do we have for the key architectural symbols?
probe = [
    "Worm Registry", "worm_registry", "@register_worm", "register_worm",
    "ClusterWORMZ", "MetaCluster", "NatureCluster", "EvolutionEngine",
    "SpineCycleWorm", "DirectMemory", "LocalMemoryStore", "MemoryBus",
    "TERENCE", "TERENCE", "KLINK", "HYDRA", "HORUS", "PAMS",
    "CurativePool", "TensorGene", "ComplianceOrchestrator", "DualLedger",
    "GeneticOptimizer", "MeshRouterWorm", "EX MACHINA", "AOA",
    "get_status", "discover_worms", "load_spine_state", "save_spine_state",
    "validate_registry", "orchestrate", "evolve", "fix_all",
]
for p in probe:
    k = norm(p)
    hits = index.get(k, [])
    tail = by_tail.get(p.lower(), [])
    print(f"{p:28s} label->{len(hits):3d}  tail->{len(tail):3d}  {hits[:2] or tail[:2]}")