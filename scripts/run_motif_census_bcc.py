import json
import networkx as nx
import sys
sys.path.insert(0, r'C:\Azazael\azazael\scripts')
from motif_census import MotifCensus

d = json.load(open(r'C:\Users\clint\BCC_COSMOS\bcc_network_sna_true.json'))
edges = d['person_edges_graph']
G = nx.Graph()
for e in edges:
    G.add_edge(e['source'], e['target'], types=e['types'], detail=e['detail'])

print(f"Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")

census = MotifCensus(G, max_k=5)
counts = census.enumerate_all()
summary = census.get_summary()

print(f"\nTotal motifs: {sum(counts.values())}")
for motif, info in summary.items():
    print(f"  {motif}: {info['count']} ({info['nodes']} nodes, {info['edges']} edges)")
    for ex in info['examples'][:2]:
        print(f"    ex: {ex}")