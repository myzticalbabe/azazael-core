"""motif_census.py — Exhaustive graph motif enumeration for BCC network.

ESU (Exact Subgraph Enumeration) algorithm for all k-node induced subgraphs.
Complements triangle census (clustering coefficient) with full structural signature.

Usage:
    python motif_census.py --graph <graph.json> --k 5 --output <motif_counts.json>
    python motif_census.py --networkx --nodes <nodes.json> --edges <edges.json> --k 4
"""

import json
import sys
import time
import argparse
from pathlib import Path
from collections import defaultdict, Counter
from itertools import combinations

try:
    import networkx as nx
except ImportError:
    nx = None


class MotifCensus:
    """ESU algorithm for exhaustive k-node motif enumeration."""

    def __init__(self, graph, max_k=5):
        self.G = graph
        self.max_k = max_k
        self.motif_counts = Counter()
        self.motif_examples = defaultdict(list)

    def enumerate_all(self):
        """Run ESU for all k from 3 to max_k."""
        if nx is None:
            raise RuntimeError("networkx required for motif census")

        nodes = list(self.G.nodes())
        n = len(nodes)
        node_to_idx = {node: i for i, node in enumerate(nodes)}

        for k in range(3, self.max_k + 1):
            print(f"  Enumerating {k}-node motifs...")
            start = time.time()
            self._esu_k(k, nodes, node_to_idx)
            elapsed = time.time() - start
            print(f"    {sum(self.motif_counts.values())} total motifs found in {elapsed:.1f}s")

        return dict(self.motif_counts)

    def _esu_k(self, k, nodes, node_to_idx):
        """ESU for fixed k."""
        n = len(nodes)

        # ESU uses recursive extension with node ordering
        def extend(subgraph, extension, depth):
            if depth == k:
                # Found a k-node subgraph - canonicalize and count
                canonical = self._canonicalize(subgraph)
                self.motif_counts[canonical] += 1
                if len(self.motif_examples[canonical]) < 3:
                    self.motif_examples[canonical].append([nodes[i] for i in subgraph])
                return

            # Extension candidates: neighbors of subgraph with higher index
            candidates = set()
            for v in subgraph:
                for nbr in self.G.neighbors(nodes[v]):
                    if nbr in node_to_idx:
                        idx = node_to_idx[nbr]
                        if idx not in subgraph and idx not in extension:
                            candidates.add(idx)

            # Filter by ordering to avoid duplicates
            candidates = [c for c in candidates if c > min(subgraph)]

            for c in sorted(candidates):
                extend(subgraph | {c}, extension | {c}, depth + 1)

        # Start from each node
        for v in range(n):
            extend({v}, {v}, 1)

    def _canonicalize(self, node_set):
        """Canonical form: sorted edge list with relabeled nodes 0..k-1."""
        subgraph = self.G.subgraph([list(self.G.nodes())[i] for i in node_set])
        # Relabel to 0..k-1
        mapping = {old: new for new, old in enumerate(subgraph.nodes())}
        edges = tuple(sorted((mapping[u], mapping[v]) for u, v in subgraph.edges()))
        return f"k{len(node_set)}:{edges}"

    def get_summary(self):
        """Human-readable summary."""
        summary = {}
        for motif, count in self.motif_counts.most_common():
            k = int(motif.split(':')[0][1:])
            edges = eval(motif.split(':')[1])
            summary[motif] = {
                'count': count,
                'nodes': k,
                'edges': len(edges),
                'examples': self.motif_examples[motif][:3]
            }
        return summary


def load_graph(graph_path):
    """Load graph from JSON (nodes/edges or graph.json format)."""
    data = json.loads(Path(graph_path).read_text())

    if isinstance(data, list):
        # Assume edges list: [{"source": ..., "target": ...}, ...]
        G = nx.DiGraph()
        for e in data:
            G.add_edge(e['source'], e['target'])
    elif 'nodes' in data and 'edges' in data:
        G = nx.DiGraph()
        for n in data['nodes']:
            G.add_node(n['id'], **{k: v for k, v in n.items() if k != 'id'})
        for e in data['edges']:
            G.add_edge(e['source'], e['target'], relation=e.get('relation', ''))
    elif 'nodes' in data and 'links' in data:
        G = nx.DiGraph()
        for n in data['nodes']:
            G.add_node(n['id'], **{k: v for k, v in n.items() if k != 'id'})
        for e in data['links']:
            G.add_edge(e['source'], e['target'], relation=e.get('relation', ''))
    else:
        raise ValueError("Unrecognized graph format")

    return G


def main():
    parser = argparse.ArgumentParser(description='Exhaustive motif census')
    parser.add_argument('--graph', help='Path to graph JSON')
    parser.add_argument('--networkx', action='store_true', help='Load from separate nodes/edges JSON')
    parser.add_argument('--nodes', help='Nodes JSON (with --networkx)')
    parser.add_argument('--edges', help='Edges JSON (with --networkx)')
    parser.add_argument('--k', type=int, default=5, help='Maximum k for k-node motifs')
    parser.add_argument('--output', help='Output JSON path')
    parser.add_argument('--triangles-only', action='store_true', help='Only count triangles')
    args = parser.parse_args()

    if args.triangles_only:
        # Fast triangle counting only
        if args.graph:
            G = load_graph(args.graph)
        else:
            G = nx.DiGraph()
            nodes = json.loads(Path(args.nodes).read_text())
            edges = json.loads(Path(args.edges).read_text())
            for n in nodes:
                G.add_node(n['id'], **{k: v for k, v in n.items() if k != 'id'})
            for e in edges:
                G.add_edge(e['source'], e['target'])

        # Convert to undirected for triangles
        G_undir = G.to_undirected()
        triangles = sum(nx.triangles(G_undir).values()) // 3
        print(f"Triangles: {triangles}")
        if args.output:
            Path(args.output).write_text(json.dumps({'triangles': triangles}))
        return

    # Full motif census
    if args.graph:
        G = load_graph(args.graph)
    else:
        G = nx.DiGraph()
        nodes = json.loads(Path(args.nodes).read_text())
        edges = json.loads(Path(args.edges).read_text())
        for n in nodes:
            G.add_node(n['id'], **{k: v for k, v in n.items() if k != 'id'})
        for e in edges:
            G.add_edge(e['source'], e['target'])

    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    print(f"Running motif census up to k={args.k}...")

    census = MotifCensus(G, max_k=args.k)
    counts = census.enumerate_all()

    summary = census.get_summary()
    result = {
        'graph_stats': {
            'nodes': G.number_of_nodes(),
            'edges': G.number_of_edges(),
            'density': nx.density(G.to_undirected()),
        },
        'max_k': args.k,
        'total_motifs': sum(counts.values()),
        'motifs': summary
    }

    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2, default=str))
        print(f"Results written to {args.output}")

    print(f"\nTotal motifs: {result['total_motifs']}")
    for motif, info in summary.items():
        print(f"  {motif}: {info['count']} ({info['nodes']} nodes, {info['edges']} edges)")

    return result


if __name__ == '__main__':
    main()