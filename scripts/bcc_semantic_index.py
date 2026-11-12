"""
bcc_semantic_index.py — TF-IDF semantic cross-connection index over the BCC corpus.

chromadb/sentence-transformers segfault on the free-threaded stack, so this uses sklearn
TF-IDF + cosine similarity (works, zero native deps). TF-IDF is well-suited to this
corpus because the "implied connections" are proper-noun/topic co-occurrences that
exact grep misses when the surrounding words differ.

Outputs:
  BCC_SEMANTIC_INDEX.html        — interactive report of implied cross-connections
  bcc_semantic_index.json        — machine-readable pairs

Run:  C:/Azazael/.venvs/py315t/Scripts/python.exe azazael/scripts/bcc_semantic_index.py [corpus-dir]
      (deps belong in py315t; .venvs/py314m is the sanctioned 3.14 fallback)
"""
import argparse
import glob
import html
import json
import os
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def load_docs(corpus_dir: str):
    docs = []
    for path in sorted(glob.glob(os.path.join(corpus_dir, "*.html")) +
                       glob.glob(os.path.join(corpus_dir, "*.md"))):
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError:
            continue
        text = re.sub(r"<[^>]+>", " ", raw)          # strip tags
        text = re.sub(r"<script.*?</script>", " ", raw, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"\s+", " ", text)
        docs.append({"name": name, "text": text, "path": path})
    return docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus_dir", nargs="?", default=".")
    ap.add_argument("--top", type=int, default=25,
                    help="top-N implied pairs to surface per doc (default 25)")
    args = ap.parse_args()

    docs = load_docs(args.corpus_dir)
    print(f"indexed {len(docs)} docs")

    names = [d["name"] for d in docs]
    vec = TfidfVectorizer(strip_accents="unicode", lowercase=True,
                          stop_words="english", max_features=20000,
                          sublinear_tf=True, ngram_range=(1, 2))
    X = vec.fit_transform([d["text"] for d in docs])
    sim = cosine_similarity(X)
    n = len(docs)

    # Exclude self and near-duplicate basenames (same doc in different case forms).
    def same_doc(i, j):
        a, b = names[i], names[j]
        if a == b:
            return True
        base = lambda s: re.sub(r"_(v\d+)?\.(html|md)$", "", s.lower())
        return base(a) == base(b)

    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if same_doc(i, j):
                continue
            pairs.append((sim[i, j], names[i], names[j]))
    pairs.sort(reverse=True)
    pairs = [p for p in pairs if p[0] > 0.05]

    print(f"{len(pairs)} cross-doc pairs above 0.05 cosine")

    # Top terms per doc for the report
    feature_names = vec.get_feature_names_out()
    top_terms = {}
    for i, d in enumerate(docs):
        row = X[i].tocoo()
        scored = sorted(zip(row.col, row.data), key=lambda t: -t[1])[:10]
        top_terms[d["name"]] = [feature_names[c] for c, _ in scored]

    out = {
        "generated": "2026-09-05",
        "corpus_dir": args.corpus_dir,
        "n_docs": n,
        "top_pairs": [{"score": round(s, 4), "a": a, "b": b}
                      for s, a, b in pairs[: args.top * 3]],
    }
    with open("bcc_semantic_index.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    rows = []
    for s, a, b in pairs[: args.top]:
        ta = ", ".join(top_terms.get(a, [])[:6])
        tb = ", ".join(top_terms.get(b, [])[:6])
        rows.append(
            f'<tr><td>{s:.3f}</td><td><b>{html.escape(a)}</b><br>'
            f'<span class="terms">{html.escape(ta)}</span></td>'
            f'<td><b>{html.escape(b)}</b><br>'
            f'<span class="terms">{html.escape(tb)}</span></td></tr>'
        )

    report = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BCC Semantic Cross-Connection Index — September 5, 2026</title>
<style>
  :root {{ --bg:#0d1117; --card:#161b22; --border:#30363d; --text:#c9d1d9;
          --h:#f0f6fc; --a:#58a6ff; --g:#3fb950; --y:#ffa657; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--text); line-height:1.55; }}
  header {{ padding:28px 32px; border-bottom:1px solid var(--border);
           background:linear-gradient(180deg,#10151d,#0d1117); }}
  h1 {{ color:var(--h); font-size:1.35rem; }}
  .sub {{ color:#8b949e; font-size:.9rem; margin-top:6px; }}
  main {{ max-width:1100px; margin:0 auto; padding:24px 32px; }}
  .stat {{ display:inline-block; background:var(--card); border:1px solid var(--border);
          border-radius:10px; padding:14px 20px; margin:0 10px 18px 0; }}
  .stat b {{ color:var(--h); font-size:1.5rem; display:block; }}
  .stat span {{ color:#8b949e; font-size:.78rem; }}
  table {{ width:100%; border-collapse:collapse; }}
  th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--border);
          vertical-align:top; }}
  th {{ color:var(--a); font-size:.8rem; text-transform:uppercase;
       letter-spacing:.06em; }}
  .terms {{ color:#8b949e; font-size:.78rem; }}
  .note {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
          padding:14px 18px; margin:18px 0; color:#8b949e; font-size:.88rem; }}
</style></head><body>
<header><h1>BCC Semantic Cross-Connection Index</h1>
<div class="sub">TF-IDF cosine similarity over the full corpus — implied connections
exact grep misses · generated 2026-09-05 · {n} documents indexed</div></header>
<main>
<div class="stat"><b>{n}</b><span>docs indexed</span></div>
<div class="stat"><b>{len(pairs)}</b><span>pairs &gt; 0.05</span></div>
<div class="stat"><b>{pairs[0][0]:.3f}</b><span>top cosine</span></div>
<div class="note"><b>How to read:</b> pairs ranked by cosine similarity of their
TF-IDF topic vectors. A high score between docs that share few exact strings
indicates an <i>implied</i> connection — worth reading both docs together to look
for a relationship the literal-text searches missed. Top terms per doc are shown
as context.</div>
<table><thead><tr><th>Sim</th><th>Doc A</th><th>Doc B</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</main></body></html>"""
    with open("BCC_SEMANTIC_INDEX.html", "w", encoding="utf-8") as f:
        f.write(report)
    print("wrote BCC_SEMANTIC_INDEX.html + bcc_semantic_index.json")


if __name__ == "__main__":
    main()