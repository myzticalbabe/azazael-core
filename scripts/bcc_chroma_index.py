#!/usr/bin/env python3
"""
bcc_chroma_index.py -- Real-embedding semantic index over the investigation corpus

Builds a chromadb (persistent) collection with the default all-MiniLM-L6-v2 ONNX
embedder over the root corpus docs, then surfaces the top implied cross-document
connections per doc (cosine on real sentence embeddings, not TF-IDF).

Requires the 3.13 tooling venv (chromadb segfaults on cp314t):
    venv-313-tools/Scripts/python.exe azazael/scripts/bcc_chroma_index.py
"""
import json
import os
import re
import time
from pathlib import Path

try:
    import chromadb
except ImportError as _dep_exc:
    raise SystemExit("bcc_chroma_index.py needs chromadb (3.13 tooling venv per docstring): " + str(_dep_exc)) from _dep_exc

HOME = Path.home()
CORPUS_DIR = HOME
OUT = HOME / "BCC_CHROMA_INDEX_evidence.json"
PERSIST = HOME / "chroma_bcc_corpus"

SKIP_PREFIX = ("G_ARCHIVE_CATALOG", "BCC_TWO_SPHERE_NETWORK")


def gather_docs() -> list:
    docs = []
    for p in sorted(CORPUS_DIR.glob("*.html")) + sorted(CORPUS_DIR.glob("*.md")):
        if any(p.name.startswith(s) for s in SKIP_PREFIX):
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        text = re.sub(r"<[^>]+>", " ", raw)  # strip HTML tags
        text = re.sub(r"\s+", " ", text)
        if len(text.strip()) < 80:
            continue
        docs.append({"name": p.name, "text": text.strip()})
    return docs


def chunk(text: str, size: int = 900) -> list:
    return [text[i : i + size] for i in range(0, len(text), size)][:6]


def main() -> int:
    t0 = time.time()
    docs = gather_docs()
    print(f"Docs: {len(docs)}")

    client = chromadb.PersistentClient(path=str(PERSIST))
    try:
        client.delete_collection("bcc_corpus")
    except Exception:  # noqa: BLE001
        pass
    col = client.get_or_create_collection(
        "bcc_corpus", metadata={"hnsw:space": "cosine"}
    )
    n = 0
    for d in docs:
        for i, c in enumerate(chunk(d["text"])):
            col.add(
                ids=[f"{d['name']}#{i}"],
                documents=[c],
                metadatas=[{"file": d["name"], "chunk": i}],
            )
            n += 1
    print(f"Chunks indexed: {n} ({time.time() - t0:.1f}s)")

    # Per-doc implied connections: mean of chunk vectors is unavailable via API;
    # instead probe with each doc's opening passage and keep best other-doc hits.
    implied = {}
    probe_docs = docs[:40]
    for d in probe_docs:
        q = d["text"][:700]
        try:
            res = col.query(query_texts=[q], n_results=6)
        except Exception as e:  # noqa: BLE001
            print(f"query fail {d['name']}: {e}")
            continue
        metas = res["metadatas"][0]
        dists = res["distances"][0]
        seen = set()
        for m, dist in zip(metas, dists):
            f = m["file"]
            if f == d["name"] or f in seen:
                continue
            seen.add(f)
            sim = 1.0 - float(dist)
            implied.setdefault(d["name"], []).append(
                {"other": f, "similarity": round(sim, 3)}
            )
            if len(seen) >= 3:
                break

    out = {
        "engine": "chromadb 1.5.9 persistent + default all-MiniLM-L6-v2 ONNX embedder",
        "db_path": str(PERSIST),
        "docs_indexed": len(docs),
        "chunks": n,
        "elapsed_s": round(time.time() - t0, 1),
        "implied_connections_sample": implied,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(f"=== Top implied connections (real embeddings) ===")
    for src, hits in list(implied.items())[:12]:
        top = ", ".join(f"{h['other']} ({h['similarity']})" for h in hits[:2])
        print(f"  {src} -> {top}")
    print(f"Evidence: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
