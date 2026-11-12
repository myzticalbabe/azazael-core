"""
motif_census_relations.py — Motif census on specific relation subgraphs.

Runs ESU on individual relation types (calls, contains, imports, etc.)
which have richer connectivity than the full graph.
"""

import json
import sys
import time
import random
from pathlib import Path
from collections import defaultdict, Counter, deque
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import networkx as nx
except ImportError:
    nx = None


def load_graph_by_relations(graph_path: Path, relations: List[str] = None) -> nx.DiGraph:
    """Load graphify-out graph, optionally filtering by relation types."""
    data = json.loads(graph_path.read_text(encoding="utf-8", errors="replace"))
    
    G = nx.DiGraph()
    
    nodes = data.get('nodes', [])
    for n in nodes:
        nid = n.get('id') or n.get('label')
        if nid:
            G.add_node(nid, **{k: v for k, v in n.items() if k not in ('id', 'label')})
    
    edges = data.get('edges', data.get('links', []))
    for e in edges:
        s, t = e.get('source'), e.get('target')
        rel = e.get('relation', '')
        if s in G and t in G:
            if relations is None or rel in relations:
                G.add_edge(s, t, relation=rel, **{k: v for k, v in e.items() if k not in ('source', 'target', 'relation')})
    
    return G


def sample_subgraph(G: nx.DiGraph, max_nodes: int = 500) -> nx.DiGraph:
    """Sample largest weakly connected component."""
    if G.number_of_nodes() <= max_nodes:
        return G
    components = list(nx.weakly_connected_components(G))
    largest = max(components, key=len)
    if len(largest) > max_nodes:
        largest = random.sample(list(largest), max_nodes)
    return G.subgraph(largest).copy()


class ScalableMotifEnumerator:
    """ESU with early termination and canonical forms."""
    
    def __init__(self, G: nx.DiGraph, max_k: int = 5, max_motifs: int = 100000):
        self.G = G
        self.max_k = max_k
        self.max_motifs = max_motifs
        self.motif_counts = Counter()
        self.motif_examples = defaultdict(list)
        self.node_list = list(G.nodes())
        self.node_to_idx = {n: i for i, n in enumerate(self.node_list)}
        self.total_enumerated = 0
    
    def enumerate(self) -> Dict[str, Any]:
        if nx is None:
            raise RuntimeError("networkx required")
        
        results = {}
        for k in range(3, self.max_k + 1):
            if self.total_enumerated >= self.max_motifs:
                break
            start = time.time()
            count = self._esu_k(k)
            elapsed = time.time() - start
            results[k] = {"count": count, "time": elapsed}
            print(f"  k={k}: {count} motifs in {elapsed:.1f}s (total: {self.total_enumerated})")
        
        return {
            "by_k": results,
            "total": self.total_enumerated,
            "motif_counts": dict(self.motif_counts),
            "examples": {k: v[:3] for k, v in self.motif_examples.items()}
        }
    
    def _esu_k(self, k: int) -> int:
        count = 0
        
        def extend(subgraph: Set[int], extension: Set[int], depth: int):
            nonlocal count
            if self.total_enumerated >= self.max_motifs:
                return
            if depth == k:
                canonical = self._canonicalize(subgraph)
                self.motif_counts[canonical] += 1
                self.total_enumerated += 1
                if len(self.motif_examples[canonical]) < 3:
                    self.motif_examples[canonical].append(
                        [self.node_list[i] for i in subgraph]
                    )
                count += 1
                return
            
            candidates = set()
            for v in subgraph:
                nbrs = set(self.G.predecessors(self.node_list[v])) | \
                       set(self.G.successors(self.node_list[v]))
                for nbr in nbrs:
                    if nbr in self.node_to_idx:
                        idx = self.node_to_idx[nbr]
                        if idx not in subgraph and idx not in extension:
                            candidates.add(idx)
            
            min_sub = min(subgraph)
            candidates = [c for c in candidates if c > min_sub]
            
            for c in sorted(candidates):
                extend(subgraph | {c}, extension | {c}, depth + 1)
        
        for v in range(len(self.node_list)):
            if self.total_enumerated >= self.max_motifs:
                break
            extend({v}, {v}, 1)
        
        return count
    
    def _canonicalize(self, node_set: Set[int]) -> str:
        subgraph = self.G.subgraph([self.node_list[i] for i in node_set])
        mapping = {old: new for new, old in enumerate(subgraph.nodes())}
        edges = tuple(sorted((mapping[u], mapping[v]) for u, v in subgraph.edges()))
        return f"k{len(node_set)}:{edges}"


def main():
    graph_path = Path(r"C:\Azazael\azazael\graphify-out\graph.json")
    relations_to_test = [
        ["calls"],
        ["contains"],
        ["references"],
        ["method"],
        ["imports", "imports_from"],
        ["rationale_for"],
        ["uses", "indirect_call"],
        ["inherits", "defines", "re_exports"],
    ]
    
    print("Loading full graph...")
    G_full = load_graph_by_relations(graph_path)
    print(f"Full graph: {G_full.number_of_nodes()} nodes, {G_full.number_of_edges()} edges")
    
    all_results = {}
    
    for rel_group in relations_to_test:
        rel_str = "+".join(rel_group)
        print(f"\n{'='*60}")
        print(f"Processing relation group: {rel_str}")
        
        G_rel = load_graph_by_relations(graph_path, relations=rel_group)
        print(f"  Graph: {G_rel.number_of_nodes()} nodes, {G_rel.number_of_edges()} edges")
        
        if G_rel.number_of_edges() < 10:
            print("  Too few edges, skipping")
            continue
        
        # Sample largest component
        subgraph = sample_subgraph(G_rel, max_nodes=300)
        print(f"  Subgraph: {subgraph.number_of_nodes()} nodes, {subgraph.number_of_edges()} edges")
        
        if subgraph.number_of_edges() < 10:
            print("  Subgraph too sparse, skipping")
            continue
        
        # Run ESU
        enumerator = ScalableMotifEnumerator(subgraph, max_k=4, max_motifs=50000)
        result = enumerator.enumerate()
        
        all_results[rel_str] = {
            "relation_group": rel_group,
            "full_nodes": G_rel.number_of_nodes(),
            "full_edges": G_rel.number_of_edges(),
            "subgraph_nodes": subgraph.number_of_nodes(),
            "subgraph_edges": subgraph.number_of_edges(),
            "motifs": result
        }
        
        print(f"  Total motifs: {result['total']}")
        for motif, cnt in sorted(result['motif_counts'].items(), key=lambda x: -x[1])[:5]:
            print(f"    {motif}: {cnt}")
    
    # Save results
    output = Path(r"C:\Azazael\azazael\research\motif_census_by_relation.json")
    output.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\n\nResults saved to {output}")


if __name__ == '__main__':
    main()