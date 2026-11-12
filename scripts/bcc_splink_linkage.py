"""
bcc_splink_linkage.py — production splink 4.0 record linkage over the real BCC
people corpus (replaces the RapidFuzz fallback).

Sources:
  * bcc_network_sna_results_v3.json  — SNA person labels (role-stripped)
  * PEOPLE_DOSSIER.html              — dossier person name mentions
  * azazael/scripts/bcc_person_edges.json — same-board roster
  * PEOPLE_SEARCH_ALT.html           — alternate-search person names

Runs on the bccpipe venv (splink 5 + duckdb on GIL-enabled 3.14):
  C:/Azazael/.venvs/bccpipe/Scripts/python.exe azazael/scripts/bcc_splink_linkage.py

Outputs:
  BCC_SPLINK_LINKAGE.html  +  bcc_splink_linkage.json
"""
import argparse
import html
import json
import re

import pandas as pd
try:
    from splink import Linker
    from splink.comparison_library import NameComparison, ForenameSurnameComparison
except ImportError as _dep_exc:
    raise SystemExit("bcc_splink_linkage.py needs splink+duckdb (bccpipe venv): " + str(_dep_exc)) from _dep_exc

ROLE_WORDS = ("CEO", "CFO", "Director", "Treasurer", "Chair", "Chairperson",
              "Advisor", "Esq", "President", "former", "ex-", "Board", "(as of",
              "(thru", "(now", "(INACTIVE", "(ex-", "financial advisor",
              "Co-Chair", "Vice Chair", "President/Owner")


def strip_role(name: str) -> str:
    for sep in ("\u2014", "\u2013"):
        if sep in name:
            name = name.split(sep)[0]
    for w in ROLE_WORDS:
        name = re.sub(rf"\s*\(?{re.escape(w)}[^)]*\)?", "", name, flags=re.I)
    name = re.sub(r"\s{2,}", " ", name).strip()
    return name


def name_mentions(path):
    """Extract capitalized person-ish name fragments from a doc."""
    try:
        raw = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text)
    out = set()
    for m in re.finditer(
        r"\b((?:Christopher|Greg|Frances|Marcia|Karen|John|Linda|Dennis|Robert|"
        r"Michael|David|James|William|Joseph|Daniel|Edward|Richard|Mark|Paul|"
        r"George|Kenneth|Steven|Brian|Brenda|Crystal|Deborah|Jeffrey|Missy|"
        r"Reverend|Mahlon|Franco|Axford)\s+[A-Z][a-zA-Z'-]+(?:\s+(?:Jr\.?|"
        r"[A-Z][a-zA-Z'-]+))?|\b[A-Z][a-zA-Z'-]+\s+(?:Jr\.?|Sr\.?|III|II)\b)",
        text):
        out.add(m.group(1).strip())
    return sorted(out)


TRUE_SNA = "C:/Users/clint/BCC_COSMOS/bcc_network_sna_true.json"
PERSON_EDGES = "C:/Azazael/azazael/scripts/bcc_person_edges.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sna-results", default=TRUE_SNA)
    args = ap.parse_args()
    records = []

    # SNA person labels (TRUE BCC people-entity cosmos)
    with open(args.sna_results, encoding="utf-8") as f:
        d = json.load(f)
    for c in d.get("communities", []):
        for m in c.get("members", []):
            if any(k in m for k in ("CEO", "CFO", "Director", "Treasurer",
                                    "Chair", "Advisor", "Esq", "President",
                                    "\u2014", "\u2013")):
                stripped = strip_role(m)
                if stripped and len(stripped) > 2:
                    records.append({"source": "sna", "name": stripped})

    # board roster
    pe = json.load(open(PERSON_EDGES, encoding="utf-8"))
    for p in pe.get("same_board_clique", []):
        records.append({"source": "roster", "name": p.replace("person_", "").title()})

    # dossier + people-search mentions
    for doc, src in (("PEOPLE_DOSSIER.html", "dossier"),
                     ("PEOPLE_SEARCH_ALT.html", "search_alt")):
        for n in name_mentions(doc):
            records.append({"source": src, "name": n})

    # dedupe identical (source, name)
    seen = set()
    deduped = []
    for r in records:
        k = (r["source"], r["name"])
        if k not in seen:
            seen.add(k)
            deduped.append(r)
    records = deduped
    print(f"{len(records)} person records from "
          f"{sorted({r['source'] for r in records})}")

    df = pd.DataFrame(records)
    df["unique_id"] = [f"r{i}" for i in range(len(df))]

    # split name into forename/surname (suffixes Jr/Sr/II/III stay with surname)
    SUFFIXES = {"JR", "SR", "II", "III", "IV"}

    def split_name(n):
        parts = re.sub(r"\.", "", n).split()
        if not parts:
            return "", ""
        if len(parts) >= 2 and parts[-1].upper() in SUFFIXES:
            surname = " ".join(parts[-2:])
            forename = " ".join(parts[:-2])
        else:
            surname = parts[-1]
            forename = " ".join(parts[:-1])
        return forename, surname

    df["forename"] = df["name"].apply(lambda n: split_name(n)[0])
    df["surname"] = df["name"].apply(lambda n: split_name(n)[1])
    # normalized surname for blocking: drop Jr/Sr/II/III suffixes so the
    # surname-only roster rows match the full SNA names
    df["surname_norm"] = df["surname"].apply(
        lambda s: re.sub(r"\b(Jr|Sr|II|III|IV)$", "", s.strip()).strip())

    settings = {
        "link_type": "dedupe_only",
        "unique_id_column_name": "unique_id",
        "probability_two_random_records_match": 0.02,
        "comparisons": [
            NameComparison("name"),
            ForenameSurnameComparison("forename", "surname"),
        ],
        "blocking_rules_to_generate_predictions": [
            "l.surname_norm = r.surname_norm",
            "levenshtein(l.name, r.name) <= 2",
        ],
    }

    from splink.internals.duckdb.database_api import DuckDBAPI
    db_api = DuckDBAPI()
    linker = Linker(db_api.register(df), settings)
    try:
        linker.training.estimate_u_using_random_sampling(
            max_pairs=1e7, seed=1)
    except Exception as e:
        print(f"[warn] u-estimation skipped: {str(e)[:100]}")
    try:
        linker.training.estimate_parameters_using_expectation_maximisation(
            "l.surname_norm = r.surname_norm")
    except Exception as e:
        print(f"[warn] EM skipped: {str(e)[:100]}")
    res = linker.inference.predict()
    pairs = res.as_pandas_dataframe()

    # dedupe pair mirroring (a<->b vs b<->a)
    pair_map = {}
    for _, r in pairs.iterrows():
        a, b = sorted((r["unique_id_l"], r["unique_id_r"]))
        pair_map[(a, b)] = r["match_probability"]

    # cluster via transitive closure over pairs >= 0.75
    THRESH = 0.75
    clusters = []
    used = set()
    name_of = {row["unique_id"]: row["name"] for _, row in df.iterrows()}
    src_of = {row["unique_id"]: row["source"] for _, row in df.iterrows()}

    # union-find
    parent = {i: i for i in df["unique_id"]}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x, y):
        parent[find(x)] = find(y)

    for (a, b), p in pair_map.items():
        if p >= THRESH:
            union(a, b)

    comps = {}
    for uid in df["unique_id"]:
        comps.setdefault(find(uid), []).append(uid)

    out_clusters = []
    for members in comps.values():
        if len(members) < 2:
            continue
        members_sorted = sorted(members,
                                key=lambda u: (name_of[u], src_of[u]))
        out_clusters.append([{"source": src_of[u], "name": name_of[u]}
                             for u in members_sorted])
    out_clusters.sort(key=len, reverse=True)
    print(f"{len(out_clusters)} linkage clusters (splink, threshold {THRESH})")

    out = {
        "generated": "2026-09-05",
        "tool": "splink 5 (DuckDB backend, bccpipe venv, Python 3.14)",
        "n_records": len(records),
        "threshold": THRESH,
        "clusters": out_clusters,
    }
    with open("bcc_splink_linkage.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    rows = []
    for ci, c in enumerate(out_clusters):
        members = "".join(
            f'<li><span class="src">{html.escape(m["source"])}</span> '
            f'{html.escape(m["name"])}</li>' for m in c)
        rows.append(
            f'<tr><td>C{ci + 1}</td><td>{len(c)}</td><td><ul>{members}</ul></td></tr>')

    report = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BCC Splink Record Linkage — September 5, 2026</title>
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
<header><h1>BCC Record Linkage — splink 4.0 (production)</h1>
<div class="sub">Probabilistic linkage (DuckDB backend, name + forename-surname
comparisons) across SNA labels, board roster, dossier, and people-search docs ·
generated 2026-09-05</div></header>
<main>
<div class="stat"><b>{len(records)}</b><span>person records</span></div>
<div class="stat"><b>{len(out_clusters)}</b><span>linkage clusters</span></div>
<div class="stat"><b>{THRESH}</b><span>match threshold</span></div>
<div class="note"><b>Read:</b> splink assigns each pair a match probability from
the trained model; clusters group records across sources that the model judges
the same person (transitive closure at ≥ {THRESH}). Larger clusters = the
individual appears under variant names across corpus sources — consolidation
targets for the dossiers. Runs on the 3.13 tools venv because splink's duckdb
dependency ships no cp314t wheel.</div>
<table><thead><tr><th>Cluster</th><th>n</th><th>Records</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</main></body></html>"""
    with open("BCC_SPLINK_LINKAGE.html", "w", encoding="utf-8") as f:
        f.write(report)
    print("wrote BCC_SPLINK_LINKAGE.html + bcc_splink_linkage.json")


if __name__ == "__main__":
    main()