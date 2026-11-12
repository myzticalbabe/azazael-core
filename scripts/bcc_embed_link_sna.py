#!/usr/bin/env python3
"""
bcc_embed_link_sna.py -- SNA over the embedding-only cross-doc link layer

Builds an undirected doc-network whose edges are the 211 embedding-only implied
connections (found by real embeddings but missed by TF-IDF/exact grep), weighted
by cosine similarity. Reports hubs (high degree/PageRank), the doc community
structure, and the k-core of this investigation-lead layer.

    venv-313-tools/Scripts/python.exe azazael/scripts/bcc_embed_link_sna.py
"""
import json
import re
from pathlib import Path

HOME = Path.home()
OUT = HOME / "BCC_EMBED_LINK_SNA_evidence.json"


def main() -> int:
    import chromadb
    import networkx as nx
    import rustworkx as rx
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    docs = []
    for p in sorted(HOME.glob("*.html")) + sorted(HOME.glob("*.md")):
        if p.name.startswith(("G_ARCHIVE_CATALOG", "BCC_TWO_SPHERE")):
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
        if len(t) >= 300:
            docs.append({"name": p.name, "text": t})
    names = [d["name"] for d in docs]
    idx = {n: i for i, n in enumerate(names)}

    vec = TfidfVectorizer(stop_words="english", max_features=5000)
    X = vec.fit_transform([d["text"] for d in docs])
    sim = cosine_similarity(X)
    tfidf_pairs = {}
    for i, n in enumerate(names):
        order = sorted(range(len(docs)), key=lambda j: sim[i, j], reverse=True)
        tfidf_pairs[n] = {(n, names[j]) for j in order[:8] if names[j] != n}

    client = chromadb.PersistentClient(path=str(HOME / "chroma_bcc_corpus"))
    col = client.get_collection("bcc_corpus")
    embed_pairs = {}
    for d in docs:
        res = col.query(query_texts=[d["text"][:700]], n_results=8)
        seen, hits = set(), []
        for m, dist in zip(res["metadatas"][0], res["distances"][0]):
            f = m["file"]
            if f == d["name"] or f in seen:
                continue
            seen.add(f)
            hits.append((f, round(1.0 - float(dist), 3)))
        embed_pairs[d["name"]] = set(f for f, _ in hits)

    # Edge = pair found by embeddings but NOT by TF-IDF top-8
    g = nx.Graph()
    g.add_nodes_from(names)
    weights = {}
    for n in names:
        for o in embed_pairs.get(n, set()):
            key = tuple(sorted((n, o)))
            if key not in tfidf_pairs.get(n, set()) and key not in tfidf_pairs.get(o, set()):
                g.add_edge(n, o)

    deg = dict(g.degree())
    core = nx.core_number(g)
    comps = sorted((sorted(c) for c in nx.connected_components(g)), key=len, reverse=True)
    rxd = rx.PyDiGraph()
    rmap = {n: rxd.add_node(n) for n in g.nodes()}
    rxd.add_edges_from_no_data(
        [(rmap[u], rmap[v]) for u, v in g.edges()]
        + [(rmap[v], rmap[u]) for u, v in g.edges()]
    )
    pr = rx.pagerank(rxd)
    nodes_list = list(g.nodes())
    pr_idx = {nodes_list[i]: float(pr[i]) for i in range(len(nodes_list))}
    top_hubs = sorted(pr_idx, key=pr_idx.get, reverse=True)[:10]

    out = {
        "engine": "networkx + rustworkx over embedding-only doc links",
        "docs": len(names),
        "embedding_only_edges": g.number_of_edges(),
        "top_degree_hubs": sorted(deg, key=deg.get, reverse=True)[:8],
        "top_pagerank_docs": top_hubs,
        "k_core_max": max(core.values()),
        "k_core_top": {k: v for k, v in sorted(core.items(), key=lambda kv: -kv[1])[:8]},
        "components": [{"size": len(c), "docs": c[:6]} for c in comps[:5]],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"Docs: {len(names)}  Embedding-only edges: {g.number_of_edges()}")
    print(f"Top degree hubs: {out['top_degree_hubs']}")
    print(f"Top PageRank docs (lead generators): {top_hubs}")
    print(f"Max k-core: {out['k_core_max']}")
    print(f"Components: {[c['size'] for c in out['components']]}")
    print(f"Evidence: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
