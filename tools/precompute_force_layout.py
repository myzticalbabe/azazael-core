#!/usr/bin/env python3
"""precompute_force_layout.py — Pre-compute 3D force layout using Rust acceleration.

Eliminates the 3s force simulation settle time in the browser by computing
node positions in Python (Rust-accelerated) and saving them to JSON.

Usage:
    python precompute_force_layout.py --input graph_3d_data.json --output positions.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from rust_accel_wrapper import RustAccel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="C:/Users/clint/AppData/Local/Temp/opencode/graph_3d_data_small.json")
    parser.add_argument("--output", default="C:/Users/clint/Desktop/YGGDRASIL_RENDERS_NEW/force_positions.json")
    parser.add_argument("--ticks", type=int, default=80)
    args = parser.parse_args()

    data = json.load(open(args.input, encoding="utf-8"))
    nodes = data["nodes"]
    links = data["links"]

    accel = RustAccel()
    if not accel.available:
        print("ERROR: Rust acceleration not available")
        sys.exit(1)

    # Build node index map
    node_ids = [n["id"] for n in nodes]
    id_to_idx = {nid: i for i, nid in enumerate(node_ids)}

    # Initial positions (random, scaled)
    import random
    random.seed(42)
    positions = []
    for n in nodes:
        positions.extend([
            (random.random() - 0.5) * 600,
            (random.random() - 0.5) * 400,
            (random.random() - 0.5) * 600,
        ])

    velocities = [0.0] * (len(nodes) * 3)

    # Build edges as index pairs
    edges = []
    for l in links:
        s = l["source"] if isinstance(l["source"], str) else l["source"]["id"]
        t = l["target"] if isinstance(l["target"], str) else l["target"]["id"]
        if s in id_to_idx and t in id_to_idx:
            edges.append((id_to_idx[s], id_to_idx[t]))

    print(f"Simulating {len(nodes)} nodes, {len(edges)} edges, {args.ticks} ticks...")
    t0 = time.time()
    pos, vel = accel.force_simulate(positions, velocities, edges, 3000.0, 0.008, 0.0002, 0.88, args.ticks)
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.4f}s (Rust-accelerated)")

    # Build output: node_id -> [x, y, z]
    positions_out = {}
    for i, nid in enumerate(node_ids):
        positions_out[nid] = [pos[i*3], pos[i*3+1], pos[i*3+2]]

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(positions_out, f)

    print(f"Saved positions for {len(positions_out)} nodes to {args.output}")


if __name__ == "__main__":
    main()
