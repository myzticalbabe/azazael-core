"""HYDRA Node Dependency Graph — Generates Graphviz DOT output.

Usage:  python3 tools/hydra_graph.py | dot -Tpng -o hydra_graph.png
        (run from the azazael/ repo root)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ORCHESTRATION.HYDRA import HYDRACluster

cluster = HYDRACluster()
cluster.discover_nodes()

if not cluster.nodes:
    print("// No nodes discovered.", file=sys.stderr)
    sys.exit(1)

# Color mapping by group
GROUP_COLORS = {
    "core": "#4CAF50",
    "analysis": "#2196F3",
    "qlearning": "#FF9800",
    "integration": "#9C27B0",
    "monitoring": "#00BCD4",
    "lm_studio": "#E91E63",
    "installation": "#795548",
    "conversion": "#607D8B",
    "autoload": "#3F51B5",
    "improvements": "#009688",
}

print("digraph HYDRA {")
print("  rankdir=LR;")
print("  splines=ortho;")
print('  node [style=filled, shape=box, fontname="monospace"];')
print("  ranksep=1.5;")
print()

# Create subgraphs by group
groups = {}
for name, node in cluster.nodes.items():
    groups.setdefault(node.group, []).append(name)

for group, members in groups.items():
    color = GROUP_COLORS.get(group, "#cccccc")
    print(f"  subgraph cluster_{group.lower().replace('-', '_')} {{")
    print(f'    label="{group} ({len(members)} nodes)";')
    print(f'    style=filled;')
    print(f'    color="{color}22";')
    print(f'    fontname="monospace";')
    for m in members:
        status_color = "palegreen" if cluster.nodes[m].enabled else "lightyellow"
        print(f'    "{m}" [fillcolor="{status_color}"];')
    print("  }")
    print()

# Draw dependency edges
for name, node in cluster.nodes.items():
    for dep in node.dependencies:
        print(f'  "{dep}" -> "{name}";')

print("}")
