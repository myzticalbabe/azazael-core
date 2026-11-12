#!/usr/bin/env python3
"""
bcc_embed_tfidf_diff.py -- Diff chromadb-embedding implied links vs TF-IDF links

For each root-corpus doc: chromadb (all-MiniLM real embeddings) top-3 other-doc
neighbors vs sklearn TF-IDF cosine top-3. Reports pairs found by both, by
embeddings only, and by TF-IDF only — the connections only real embeddings surface.

    venv-313-tools/Scripts/python.exe azazael/scripts/bcc_embed_tfidf_diff.py
"""
import json
import re
from pathlib import Path

HOME = Path.home()
OUT = HOME / "BCC_EMBED_TFIDF_DIFF_evidence.json"
PERSIST = HOME / "chroma_bcc_corpus"


def strip(p: Path) -> str:
    raw = p.read_text(encoding="utf-8", errors="replace")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def main() -> int:
    import chromadb
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    docs = []
    for p in sorted(HOME.glob("*.html")) + sorted(HOME.glob("*.md")):
        if p.name.startswith(("G_ARCHIVE_CATALOG", "BCC_TWO_SPHERE")):
            continue
        t = strip(p)
        if len(t) < 300:
            continue
        docs.append({"name": p.name, "text": t})
    names = [d["name"] for d in docs]

    # TF-IDF top-3 other docs per doc
    vec = TfidfVectorizer(stop_words="english", max_features=5000)
    X = vec.fit_transform([d["text"] for d in docs])
    sim = cosine_similarity(X)
    tfidf = {}
    for i, n in enumerate(names):
        order = sorted(range(len(docs)), key=lambda j: sim[i, j], reverse=True)
        tfidf[n] = [names[j] for j in order[:6] if names[j] != n][:3]

    # chromadb top-3 other docs per doc
    client = chromadb.PersistentClient(path=str(PERSIST))
    col = client.get_collection("bcc_corpus")
    emb = {}
    for d in docs:
        res = col.query(query_texts=[d["text"][:700]], n_results=6)
        seen, hits = set(), []
        for m, dist in zip(res["metadatas"][0], res["distances"][0]):
            f = m["file"]
            if f == d["name"] or f in seen:
                continue
            seen.add(f)
            hits.append(f)
            if len(hits) >= 3:
                break
        emb[d["name"]] = hits

    both, emb_only, tfidf_only = {}, {}, {}
    for n in names:
        e, t = set(emb.get(n, [])), set(tfidf.get(n, []))
        if e & t:
            both[n] = sorted(e & t)
        if e - t:
            emb_only[n] = sorted(e - t)
        if t - e:
            tfidf_only[n] = sorted(t - e)

    n_both = sum(len(v) for v in both.values())
    n_emb = sum(len(v) for v in emb_only.values())
    n_tf = sum(len(v) for v in tfidf_only.values())
    out = {
        "engine": "chromadb all-MiniLM vs sklearn TF-IDF cosine, top-3 other-doc per doc",
        "docs": len(docs),
        "pair_counts": {"both": n_both, "embeddings_only": n_emb, "tfidf_only": n_tf},
        "both": both, "embeddings_only": emb_only, "tfidf_only": tfidf_only,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"Docs: {len(docs)}  Pairs — both: {n_both} | embeddings-only: {n_emb} | tfidf-only: {n_tf}")
    print("\nTop embedding-only connections (new, exact grep + TF-IDF would miss):")
    for n, hits in sorted(emb_only.items(), key=lambda kv: -len(kv[1]))[:10]:
        print(f"  {n} -> {hits}")
    print(f"\nEvidence: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
