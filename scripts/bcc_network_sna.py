#!/usr/bin/env python3
"""bcc_network_sna.py — Social network analysis of the BCC people-entity network.

Extracts the BCC cosmos graph (nodes + edges) from the graphify/cosmos output,
optionally merges a person→person intimacy layer (same-board / co-employment /
same-committee / succession edges, all corpus-documented), and computes:
  * degree / betweenness / eigenvector / closeness centrality
  * Louvain community detection (networkx built-in, seedable)
  * network-level stats (avg degree, density, components, diameter, 6-degrees check)
  * Gephi-ready CSV exports (nodes.csv + edges.csv with attributes)

Usage:
  python bcc_network_sna.py                                     # org-attendance only
  python bcc_network_sna.py --person-edges bcc_person_edges.json  # merged layer
  python bcc_network_sna.py --out results.json                  # dump machine-readable JSON
  python bcc_network_sna.py --export-csv OUTDIR                 # write Gephi CSVs
  python bcc_network_sna.py --cosmos-dir <dir>                  # override cosmos graph dir

Artifacts consumed:
  <cosmos-dir>/graph.labels.0.js   (window.GV_LABELS_CHUNKS.push([{id,label,file,type,deg},...]))
  <cosmos-dir>/graph.links.js      (window.GV_LINKS Uint32Array of source,target index pairs)
  --person-edges JSON              ({same_board_clique:[ids], edges:[{source,target,type,detail,evidence}]})

CSV export (Gephi 0.11.x import: File > Import Spreadsheet; nodes = nodes.csv,
edges = edges.csv with Source/Target as node Ids, Type column "Undirected"):
  <outdir>/nodes.csv   Id,Label,Type,File,Degree,Betweenness,Eigenvector,Closeness,Community
  <outdir>/edges.csv   Source,Target,Type,EdgeType

Companion artifacts: bcc_network_sna_results.json (dumped with --out),
bcc_person_edges.json (the intimacy layer).
Corpus home: BCC_FINANCIAL_ANOMALY.html / PEOPLE_DOSSIER.html / HIDDEN_NETWORK.html.
"""
import argparse
import base64
import csv
import json
import re
import struct
import sys

import networkx as nx
from networkx.algorithms.community import louvain_communities
import os


def load_graph(cosmos_dir: str) -> "tuple[nx.Graph, list[dict]]":
    """Parse cosmos label + link JS files into a networkx Graph.

    Returns (G, nodes) where G node ids are the label-list indices and nodes is
    the original label dict list (id/label/file/type/deg).
    """
    label_src = open(f"{cosmos_dir}/graph.labels.0.js", encoding="utf-8").read()
    nodes = json.loads(re.search(r"push\((\[.*\])\);", label_src, re.S).group(1))

    link_src = open(f"{cosmos_dir}/graph.links.js", encoding="utf-8").read()
    b = base64.b64decode(re.search(r"from\(atob\('([^']+)'", link_src).group(1))
    pairs = struct.unpack("<%dI" % (len(b) // 4), b)

    G = nx.Graph()
    for i, n in enumerate(nodes):
        G.add_node(i, id=n["id"], label=n["label"], type=n.get("type", "?"), file=n.get("file", ""))
    for s, t in zip(pairs[0::2], pairs[1::2]):
        if s < len(nodes) and t < len(nodes) and s != t:
            G.add_edge(s, t)
    return G, nodes


def merge_person_edges(G: nx.Graph, nodes: list, person_edges_path: str) -> list:
    """Merge the corpus-documented person→person layer into G.

    Returns the list of added edges as {source,target,type,detail,evidence} dicts.
    """
    idx = {n["id"]: i for i, n in enumerate(nodes)}
    with open(person_edges_path, encoding="utf-8") as f:
        layer = json.load(f)

    added = []
    pairs = set()

    # 1) same-board clique (all documented current board members co-serve)
    clique = layer.get("same_board_clique", [])
    for a in range(len(clique)):
        for b in range(a + 1, len(clique)):
            s, t = idx.get(clique[a]), idx.get(clique[b])
            if s is None or t is None or s == t:
                continue
            key = tuple(sorted((s, t)))
            if key in pairs:
                continue
            pairs.add(key)
            G.add_edge(s, t, etype=["same_board"],
                       detail=["co-serve on BCC board (FY2025 990 roster / live site Sep 2026)"],
                       evidence=[["PEOPLE_DOSSIER.html"]])
            added.append({"source": clique[a], "target": clique[b],
                          "type": ["same_board"],
                          "detail": G.edges[s, t]["detail"],
                          "evidence": G.edges[s, t]["evidence"]})

    # 2) typed intimacy edges (accumulate onto existing clique edges, never drop a type)
    for e in layer.get("edges", []):
        s, t = idx.get(e["source"]), idx.get(e["target"])
        if s is None or t is None or s == t:
            continue
        key = tuple(sorted((s, t)))
        if key in pairs:
            # already added (e.g. same-board clique) — append the extra type/evidence
            G.edges[s, t]["etype"].append(e["type"])
            G.edges[s, t]["detail"].append(e.get("detail", ""))
            G.edges[s, t]["evidence"].append(e.get("evidence", []))
            continue
        pairs.add(key)
        G.add_edge(s, t, etype=[e["type"]], detail=[e.get("detail", "")],
                   evidence=[e.get("evidence", [])])
        added.append({"source": e["source"], "target": e["target"],
                      "type": [e["type"]], "detail": e.get("detail", ""),
                      "evidence": [e.get("evidence", [])]})
    return added


def analyze(G: nx.Graph, seed: int = 42) -> dict:
    deg = nx.degree_centrality(G)
    bet = nx.betweenness_centrality(G)
    try:
        eig = nx.eigenvector_centrality(G, max_iter=1000)
    except (nx.PowerIterationFailedConvergence, ZeroDivisionError):
        # large / disconnected graphs can fail to converge; fall back to
        # per-component eigenvector centrality (zero where undefined)
        eig = {}
        for comp in nx.connected_components(G):
            sub = G.subgraph(comp)
            try:
                eig.update(nx.eigenvector_centrality(sub, max_iter=1000))
            except (nx.PowerIterationFailedConvergence, ZeroDivisionError):
                for n in comp:
                    eig[n] = 0.0
    close = nx.closeness_centrality(G)
    comms = sorted(louvain_communities(G, seed=seed), key=len, reverse=True)

    communities = []
    comm_of = {}
    for ci, c in enumerate(comms):
        members = sorted(G.nodes[j]["label"] for j in c)
        types = {}
        for j in c:
            t = G.nodes[j]["type"]
            types[t] = types.get(t, 0) + 1
            comm_of[j] = ci
        communities.append({"index": ci, "n": len(c), "types": types, "members": members})

    # disconnected-safe diameter / path stats (largest component only)
    components = sorted(nx.connected_components(G), key=len, reverse=True)
    giant = G.subgraph(components[0]) if components else G
    diam = nx.diameter(giant) if giant.number_of_nodes() > 1 else 0
    avg_path = round(nx.average_shortest_path_length(giant), 3) if giant.number_of_nodes() > 1 else 0.0
    return {
        "network": {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "components": nx.number_connected_components(G),
            "density": round(nx.density(G), 4),
            "avg_degree": round(sum(dict(G.degree()).values()) / G.number_of_nodes(), 3),
            "diameter": diam,
            "avg_shortest_path": avg_path,
        },
        "persons": person_separation(G),
        "centrality": {
            "degree": {G.nodes[i]["label"]: round(v, 4) for i, v in deg.items()},
            "betweenness": {G.nodes[i]["label"]: round(v, 4) for i, v in bet.items()},
            "eigenvector": {G.nodes[i]["label"]: round(v, 4) for i, v in eig.items()},
            "closeness": {G.nodes[i]["label"]: round(v, 4) for i, v in close.items()},
        },
        "rankings": {
            "top_degree": [G.nodes[i]["label"] for i, _ in sorted(deg.items(), key=lambda x: -x[1])[:10]],
            "top_betweenness": [G.nodes[i]["label"] for i, _ in sorted(bet.items(), key=lambda x: -x[1])[:8]],
            "top_eigenvector": [G.nodes[i]["label"] for i, _ in sorted(eig.items(), key=lambda x: -x[1])[:8]],
        },
        "communities": communities,
        "core_clique": core_and_clique(G),
        "_internal": {"deg": deg, "bet": bet, "eig": eig, "close": close, "comm_of": comm_of},
    }


def person_separation(G: nx.Graph) -> dict:
    """All-pairs shortest-path separation among person nodes (the 6-degrees check).

    Returns stats over the *connected person subgraph* plus the list of person
    nodes with no person-to-person path (disconnected / separation undefined).
    """
    persons = [i for i in G.nodes() if G.nodes[i]["type"] == "person"]
    if len(persons) < 2:
        return {"n_persons": len(persons), "connected": 0, "note": "<2 persons"}

    pairs = {}
    unreachable = {}
    for s in persons:
        lens = nx.single_source_shortest_path_length(G, s)
        for t in persons:
            if s >= t:
                continue
            d = lens.get(t)
            if d is None:
                unreachable[frozenset((s, t))] = None
            else:
                pairs[frozenset((s, t))] = d

    dists = sorted(pairs.values())
    total_pairs = len(persons) * (len(persons) - 1) // 2
    connected = len(dists)
    unreach_pairs = len(unreachable)

    def med(xs):
        n = len(xs)
        if n == 0:
            return None
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    max_pair = None
    if dists:
        mx = dists[-1]
        for k, v in pairs.items():
            if v == mx:
                s, t = tuple(k)
                max_pair = {"a": G.nodes[s]["label"], "b": G.nodes[t]["label"], "dist": mx}
                break

    return {
        "n_persons": len(persons),
        "total_pairs": total_pairs,
        "connected_pairs": connected,
        "unreachable_pairs": unreach_pairs,
        "max_separation": dists[-1] if dists else None,
        "median_separation": med(dists),
        "mean_separation": round(sum(dists) / len(dists), 3) if dists else None,
        "dist_histogram": {d: dists.count(d) for d in sorted(set(dists))},
        "max_pair": max_pair,
        "persons_with_no_path_to_any_other_person": [
            G.nodes[i]["label"] for i in persons
            if sum(1 for t in persons if t != i and t in nx.single_source_shortest_path_length(G, i)) == 0
        ],
    }


def core_and_clique(G: nx.Graph) -> dict:
    """k-core decomposition + maximal-clique analysis: the tightest-knit subgroups.

    Innermost k-core = the irreducible inner circle (every member has >= k neighbors
    inside the core). Largest maximal cliques = fully-connected subgraphs.
    """
    def label_set(idxs):
        return sorted(G.nodes[j]["label"] for j in idxs)

    def types_of(idxs):
        t = {}
        for j in idxs:
            ty = G.nodes[j]["type"]
            t[ty] = t.get(ty, 0) + 1
        return t

    core = nx.core_number(G)
    max_k = max(core.values()) if core else 0
    shells = {}
    for i, k in core.items():
        shells.setdefault(k, []).append(i)

    cliques = list(nx.find_cliques(G))
    clique_number = max((len(c) for c in cliques), default=0)
    largest = sorted((c for c in cliques if len(c) == clique_number),
                     key=lambda c: [G.nodes[j]["label"] for j in c])

    persons = [i for i in G.nodes() if G.nodes[i]["type"] == "person"]
    person_res = {}
    if len(persons) >= 3:
        pG = G.subgraph(persons)
        pcore = nx.core_number(pG)
        pmax = max(pcore.values()) if pcore else 0
        pshells = {}
        for i, k in pcore.items():
            pshells.setdefault(k, []).append(i)
        pcliques = list(nx.find_cliques(pG))
        pnum = max((len(c) for c in pcliques), default=0)
        plargest = sorted((c for c in pcliques if len(c) == pnum),
                          key=lambda c: [G.nodes[j]["label"] for j in c])
        person_res = {
            "person_core_max_k": pmax,
            "person_innermost_core": {"k": pmax, "n": len(pshells[pmax]),
                                      "types": types_of(pshells[pmax]),
                                      "members": label_set(pshells[pmax])},
            "person_clique_number": pnum,
            "person_largest_cliques": [{"n": len(c), "types": types_of(c),
                                        "members": label_set(c)} for c in plargest[:3]],
        }

    return {
        "core_max_k": max_k,
        "core_shells": {str(k): {"n": len(v), "types": types_of(v), "members": label_set(v)}
                         for k, v in sorted(shells.items())},
        "innermost_core": {"k": max_k, "n": len(shells[max_k]), "types": types_of(shells[max_k]),
                           "members": label_set(shells[max_k])},
        "clique_number": clique_number,
        "largest_cliques": [{"n": len(c), "types": types_of(c), "members": label_set(c)}
                            for c in largest[:5]],
        "maximal_cliques_count": len(cliques),
        "person": person_res,
    }


def export_csv(G: nx.Graph, nodes: list, outdir: str, results: dict) -> tuple:
    """Write Gephi-ready nodes.csv + edges.csv. Returns (nodes_path, edges_path)."""
    os.makedirs(outdir, exist_ok=True)
    node_path = os.path.join(outdir, "nodes.csv")
    edge_path = os.path.join(outdir, "edges.csv")

    int_ = results["_internal"]
    deg, bet, eig, close, comm_of = int_["deg"], int_["bet"], int_["eig"], int_["close"], int_["comm_of"]

    with open(node_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Id", "Label", "Type", "File", "Degree", "Betweenness",
                    "Eigenvector", "Closeness", "Community"])
        for i, n in enumerate(nodes):
            w.writerow([n["id"], n["label"], n["type"], n.get("file", ""),
                        G.degree(i), round(bet.get(i, 0), 4), round(eig.get(i, 0), 4),
                        round(close.get(i, 0), 4), comm_of.get(i, "")])

    with open(edge_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Source", "Target", "Type", "EdgeType"])
        for s, t in G.edges():
            e = G.edges[s, t]
            etype = "+".join(e["etype"]) if "etype" in e else "org_attendance"
            w.writerow([G.nodes[s]["id"], G.nodes[t]["id"], "Undirected", etype])

    return node_path, edge_path


def main() -> None:
    ap = argparse.ArgumentParser(description="SNA on the BCC cosmos people-entity network")
    ap.add_argument("--out", help="path to write JSON results dump")
    ap.add_argument("--cosmos-dir", default="Desktop/AZAZAEL_COSMOS_VIEWER_bcc/cosmos",
                    help="dir holding graph.labels.0.js + graph.links.js")
    ap.add_argument("--person-edges", help="JSON file with the person→person intimacy layer")
    ap.add_argument("--export-csv", metavar="OUTDIR",
                    help="write Gephi-ready nodes.csv + edges.csv into OUTDIR")
    ap.add_argument("--seed", type=int, default=42, help="Louvain seed (default 42)")
    args = ap.parse_args()

    G, nodes = load_graph(args.cosmos_dir)

    added = []
    if args.person_edges:
        added = merge_person_edges(G, nodes, args.person_edges)

    results = analyze(G, seed=args.seed)
    if added:
        results["person_edges_added"] = added
        results["person_edges_count"] = len(added)
        pe = []
        for s, t in G.edges():
            e = G.edges[s, t]
            if "etype" in e:
                pe.append({"source": G.nodes[s]["id"], "target": G.nodes[t]["id"],
                           "types": e["etype"], "detail": e["detail"], "evidence": e["evidence"]})
        results["person_edges_graph"] = pe

    print(f"Network: {results['network']}")
    if added:
        from collections import Counter
        by_type = Counter(t for e in added for t in e["type"])
        print(f"\nMerged person->person edges: {len(added)} unique pairs, {dict(by_type)} type-instances")
        added_by_pair = {(e["source"], e["target"]): e for e in added}
        for e in added:
            ev = ", ".join(sorted({x for lst in e["evidence"] for x in lst}))
            print(f"  {e['source']} --{'+'.join(e['type'])}--> {e['target']}  [{ev}]")
        multi = [(s, t) for s, t in G.edges()
                 if isinstance(G.edges[s, t].get("etype"), list) and len(G.edges[s, t]["etype"]) > 1]
        for s, t in multi:
            lab_s, lab_t = G.nodes[s]["id"], G.nodes[t]["id"]
            entry = added_by_pair.get((lab_s, lab_t)) or added_by_pair.get((lab_t, lab_s))
            if entry and set(entry["type"]) == set(G.edges[s, t]["etype"]):
                continue
            ev = ", ".join(sorted({x for lst in G.edges[s, t]["evidence"] for x in lst}))
            print(f"  {lab_s} --{'+'.join(G.edges[s, t]['etype'])}--> {lab_t}  [{ev}]")
    print("\n=== TOP DEGREE ===")
    for label in results["rankings"]["top_degree"]:
        print(f"  {label}")
    print("\n=== TOP BETWEENNESS (bridges) ===")
    for label in results["rankings"]["top_betweenness"]:
        print(f"  {label}")
    print("\n=== TOP EIGENVECTOR (influence) ===")
    for label in results["rankings"]["top_eigenvector"]:
        print(f"  {label}")
    p = results.get("persons")
    if p and p.get("max_separation") is not None:
        print("\n=== 6-DEGREES (person-to-person all-pairs) ===")
        print(f"  persons: {p['n_persons']} | pairs: {p['connected_pairs']}/{p['total_pairs']} connected | "
              f"{p['unreachable_pairs']} unreachable")
        print(f"  MAX separation: {p['max_separation']} hops ({p['max_pair']['a']} <-> {p['max_pair']['b']})")
        print(f"  MEDIAN separation: {p['median_separation']} | MEAN: {p['mean_separation']}")
        print(f"  histogram: {p['dist_histogram']}")
        if p["persons_with_no_path_to_any_other_person"]:
            print("  persons with NO path to any other person:")
            for lbl in p["persons_with_no_path_to_any_other_person"]:
                print(f"      - {lbl}")
    print(f"\n=== LOUVAIN: {len(results['communities'])} communities ===")
    for c in results["communities"]:
        print(f"  C{c['index']} (n={c['n']}, {c['types']})")
        for m in c["members"]:
            print(f"      - {m}")

    cc = results.get("core_clique")
    if cc:
        print("\n=== K-CORE SHELLS (tightest-knit subgroups) ===")
        for k in sorted((int(x) for x in cc["core_shells"]), reverse=True):
            s = cc["core_shells"][str(k)]
            print(f"  {k}-core (n={s['n']}, {s['types']})")
            for m in s["members"]:
                print(f"      - {m}")
        print("\n=== MAXIMAL CLIQUES ===")
        print(f"  clique number: {cc['clique_number']} | maximal cliques: {cc['maximal_cliques_count']}")
        for c in cc["largest_cliques"]:
            print(f"  n={c['n']} {c['types']}: {', '.join(c['members'])}")
        per = cc.get("person") or {}
        if per:
            print("\n=== PERSON-ONLY INNER CIRCLE ===")
            pc = per["person_innermost_core"]
            print(f"  innermost person core: {pc['k']}-core (n={pc['n']}, {pc['types']})")
            for m in pc["members"]:
                print(f"      - {m}")
            print(f"  person clique number: {per['person_clique_number']}")
            for c in per["person_largest_cliques"]:
                print(f"    n={c['n']}: {', '.join(c['members'])}")

    if args.export_csv:
        node_path, edge_path = export_csv(G, nodes, args.export_csv, results)
        print(f"\nWrote {node_path}")
        print(f"Wrote {edge_path}")

    if args.out:
        # drop the internal helper keys before JSON dump
        dump = {k: v for k, v in results.items() if k != "_internal"}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2)
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())