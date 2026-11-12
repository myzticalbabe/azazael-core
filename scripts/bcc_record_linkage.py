"""
bcc_record_linkage.py — RapidFuzz record linkage across the BCC people records.

splink is not installable on this stack (needs duckdb+igraph sdists), so this
uses RapidFuzz for fuzzy person-name linkage across:
  * bcc_codebase_import_sna_v3.json person labels (currently VOID: persons=0;
    linkage yields nothing until R1 repopulates the person layer)
  * PEOPLE_DOSSIER.html name mentions
  * PEOPLE_SEARCH_ALT.html / INVESTIGATION_GAPS person cites (optional)

Outputs:
  BCC_RECORD_LINKAGE.html  — interactive dedupe report
  bcc_record_linkage.json  — machine-readable clusters

Run:  C:/Azazael/.venvs/py315t/Scripts/python.exe azazael/scripts/bcc_record_linkage.py
      (deps belong in py315t; .venvs/py314m is the sanctioned 3.14 fallback)
"""
import html
import json
import os
import re
import sys

from rapidfuzz import fuzz

CORE = ["person_axford", "person_little", "person_plageman", "person_robinson",
        "person_krause", "person_malley", "person_scull", "person_bromley",
        "person_brooks", "person_gaul", "person_orlando", "person_ralph",
        "person_seitz"]

CANON = {
    "person_axford": "Axford", "person_little": "Greg Little",
    "person_plageman": "Christopher Plageman Jr.", "person_robinson": "Robinson",
    "person_krause": "Krause", "person_malley": "Frances Malley",
    "person_scull": "Scull", "person_bromley": "Bromley",
    "person_brooks": "Brooks", "person_gaul": "Gaul",
    "person_orlando": "Orlando", "person_ralph": "Ralph", "person_seitz": "Seitz",
}


ROLE_MARKERS = ("CEO", "CFO", "Director", "Treasurer", "Chair", "Advisor",
                "Esq", "President", "\u2014", "\u2013")

ROLE_WORDS = ("CEO", "CFO", "Director", "Treasurer", "Chair", "Chairperson",
              "Advisor", "Esq", "President", "former", "ex-", "Board", "(as of",
              "(thru", "(now", "(INACTIVE", "(ex-", "financial advisor")


def person_labels():
    """Collect person labels from SNA v3 communities + persons sections,
    returning the bare name (role suffix stripped) for linkage."""
    labels = set()
    with open("bcc_codebase_import_sna_v3.json", encoding="utf-8") as f:
        d = json.load(f)
    for c in d.get("communities", []):
        for m in c.get("members", []):
            if any(k in m for k in ROLE_MARKERS):
                labels.add(strip_role(m))
    return labels


def strip_role(name: str) -> str:
    """Strip the role/context suffix from a SNA label, keeping the person name."""
    for sep in ("\u2014", "\u2013"):
        if sep in name:
            name = name.split(sep)[0]
    for w in ROLE_WORDS:
        name = re.sub(rf"\s*\(?{re.escape(w)}[^)]*\)?", "", name, flags=re.I)
    name = re.sub(r"\s*Jr\.?$", " Jr.", name)
    name = re.sub(r"\s{2,}", " ", name).strip()
    return name


def dossier_mentions(path="PEOPLE_DOSSIER.html"):
    """Extract person-name-ish mentions from the dossier."""
    if not os.path.exists(path):
        return set()
    raw = open(path, encoding="utf-8", errors="replace").read()
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text)
    names = set()
    for m in re.finditer(
        r"\b((?:Christopher|Greg|Frances|Marcia|Karen|John|Linda|Dennis|Robert|"
        r"Michael|David|James|William|Joseph|Daniel|Edward|Richard|Mark|Paul|"
        r"George|Kenneth|Steven|Brian)\s+[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+)?"
        r"|\b[A-Z][a-zA-Z'-]+\s+(?:Jr\.?|Sr\.?|III|II)\b)", text):
        names.add(m.group(1).strip())
    return names


def main():
    labels = sorted(person_labels())
    mentions = sorted(dossier_mentions())
    print(f"SNA person labels: {len(labels)}; dossier name mentions: {len(mentions)}")

    # Build cluster graph: nodes are (source, name); edge if fuzzy >= threshold
    nodes = []
    for p in CORE:
        nodes.append({"src": "core", "name": CANON[p]})
    for l in labels:
        nodes.append({"src": "sna", "name": l})
    for m in mentions:
        nodes.append({"src": "dossier", "name": m})

    THRESH = 82
    pairs = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            if a["src"] == b["src"]:
                continue
            r = fuzz.token_sort_ratio(a["name"], b["name"])
            if r >= THRESH:
                pairs.append({"a": a, "b": b, "score": r})

    # connected components = linkage clusters
    parent = {i: i for i in range(len(nodes))}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for p in pairs:
        union(nodes.index(p["a"]), nodes.index(p["b"]))
    comps = {}
    for i, n in enumerate(nodes):
        comps.setdefault(find(i), []).append(n)

    clusters = [v for v in comps.values() if len(v) > 1]
    clusters.sort(key=len, reverse=True)
    print(f"{len(clusters)} linkage clusters above threshold {THRESH}")

    out = {"threshold": THRESH, "n_nodes": len(nodes),
           "clusters": [[{"src": n["src"], "name": n["name"]} for n in c]
                        for c in clusters]}
    with open("bcc_record_linkage.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    rows = []
    for ci, c in enumerate(clusters):
        members = "".join(
            f'<li><span class="src">{html.escape(m["src"])}</span> '
            f'{html.escape(m["name"])}</li>' for m in c)
        rows.append(
            f'<tr><td>C{ci + 1}</td><td>{len(c)}</td><td><ul>{members}</ul></td></tr>')

    report = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BCC Record Linkage — September 5, 2026</title>
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
  main {{ max-width:1000px; margin:0 auto; padding:24px 32px; }}
  .stat {{ display:inline-block; background:var(--card); border:1px solid var(--border);
          border-radius:10px; padding:14px 20px; margin:0 10px 18px 0; }}
  .stat b {{ color:var(--h); font-size:1.5rem; display:block; }}
  .stat span {{ color:#8b949e; font-size:.78rem; }}
  table {{ width:100%; border-collapse:collapse; }}
  th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--border);
          vertical-align:top; }}
  th {{ color:var(--a); font-size:.8rem; text-transform:uppercase;
       letter-spacing:.06em; }}
  .src {{ display:inline-block; background:var(--card); border:1px solid var(--border);
         border-radius:6px; padding:1px 8px; margin-right:8px;
         color:var(--y); font-size:.75rem; }}
  ul {{ list-style:none; }}
  li {{ margin:6px 0; }}
  .note {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
          padding:14px 18px; margin:18px 0; color:#8b949e; font-size:.88rem; }}
</style></head><body>
<header><h1>BCC Record Linkage — Person Dedupe</h1>
<div class="sub">RapidFuzz token-sort similarity (threshold {THRESH}) across
SNA network persons, dossier name mentions, and the core person roster ·
generated 2026-09-05</div></header>
<main>
<div class="stat"><b>{len(nodes)}</b><span>person records</span></div>
<div class="stat"><b>{len(clusters)}</b><span>linkage clusters</span></div>
<div class="stat"><b>{THRESH}</b><span>fuzzy threshold</span></div>
<div class="note"><b>Read:</b> each cluster groups records across sources that
likely refer to the same person. Cluster size &gt; 1 means the same individual
appears under variant names — candidate consolidation targets for the dossiers.
Singletons (no cross-source match) are the least-documented people: review for
profile gaps. <i>Note: splink (the production-grade linker) is not installable on
the free-threaded stack — duckdb/igraph ship source-only; RapidFuzz is the fallback.</i>
</div>
<table><thead><tr><th>Cluster</th><th>n</th><th>Records</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</main></body></html>"""
    with open("BCC_RECORD_LINKAGE.html", "w", encoding="utf-8") as f:
        f.write(report)
    print("wrote BCC_RECORD_LINKAGE.html + bcc_record_linkage.json")


if __name__ == "__main__":
    main()