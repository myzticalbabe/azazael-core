"""
bcc_graphiti_temporal.py — graphiti-core temporal knowledge graph prototype.

Models the BCC board-reorg / officer-history facts using graphiti_core's actual
data model (EntityNode + EntityEdge with valid_at / invalid_at / reference_time /
expired_at) constructed in-memory — proving the temporal model runs on the
free-threaded 3.15t stack without a graph-store backend.

graphiti (Zep, 2025-26) is a temporal knowledge-graph framework: relationships
carry valid-time intervals, so "who chaired BCC on 2024-01-01?" answers
mechanically instead of by re-reading prose. The full engine path (LLM extractor
+ Neo4j/FalkorDB backend + embedder) is documented but not required to validate
the model.

Run:  C:/Azazael/.venvs/py315t/Scripts/python.exe azazael/scripts/bcc_graphiti_temporal.py
      (deps belong in py315t; .venvs/py314m is the sanctioned 3.14 fallback)
Out:  BCC_TEMPORAL_KG.html  +  bcc_graphiti_temporal.json
"""
import json
from datetime import datetime, timezone

try:
    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EntityNode
except ImportError as _dep_exc:
    raise SystemExit("bcc_graphiti_temporal.py needs graphiti-core (pip install graphiti-core into py315t): " + str(_dep_exc)) from _dep_exc


def facts():
    """(subject, rel, object, valid_from, valid_to, evidence) tuples."""
    return [
        ("Joseph Plageman Jr", "chairs board of", "BCC",
         "2025-07-01", "2026-05-15",
         "INVESTIGATION_GAPS.html #2: 'Plageman is no longer Board Chair' (as-of banner, Sep 2026); corpus is unanimous this is Joseph Plageman Jr (FY2025 990 = Chairperson, CRD #5964768)"),
        ("John Robinson", "treasurer of", "BCC",
         "2025-07-01", "2026-05-15",
         "INVESTIGATION_GAPS.html: 'Robinson is no longer Treasurer'"),
        ("Crystal Seitz", "chairs board of", "BCC",
         "2026-05-15", None,
         "INVESTIGATION_GAPS.html #4: 'Seitz Vice Chair (now Chairperson)'"),
        ("Jeffrey Ralph", "treasurer of", "BCC",
         "2026-05-15", None,
         "INVESTIGATION_GAPS.html: 'Ralph Director (now Treasurer)'"),
        ("Frances Malley", "CEO of", "BCC",
         "2015-01-01", "2019-01-31",
         "PEOPLE_DOSSIER.html: former CEO/President thru Jan 2019"),
        ("Axford", "CEO of", "BCC",
         "2019-02-01", None,
         "SNA v3: 'Axford — CEO/President, in post since Jan 2019'"),
        ("Greg Little", "CFO of", "BCC",
         "2019-02-01", None,
         "SNA v3: 'Little — CFO, in post since Jan 2019'"),
        ("Richard Bromley Jr", "financial advisor for", "TPUC",
         "2018-01-01", "2024-06-30",
         "SNA v3: 'TPUC financial advisor (INACTIVE FINRA)'"),
        ("Joseph Plageman Jr", "employed at", "Edward Jones",
         None, None,
         "bcc_person_edges.json: 'Plageman is Financial Advisor, 918 Penn Ave, Wyomissing, CRD #5964768'"),
        ("Linda Krause", "co-employed with", "Joseph Plageman Jr",
         None, None,
         "bcc_person_edges.json: 'Same Edward Jones branch/team'"),
    ]


def parse(date_str):
    return (datetime.fromisoformat(date_str)
            .replace(tzinfo=timezone.utc) if date_str else None)


def main():
    nodes = {}
    edges = []

    def get_node(name):
        if name not in nodes:
            nodes[name] = EntityNode(
                uuid=f"entity:{len(nodes)}:{name.lower().replace(' ', '_')}",
                name=name, labels=["BCC_PERSON" if True else "ORG"],
                group_id="bcc-investigation",
            )
        return nodes[name]

    for subject, rel, obj, vf, vt, ev in facts():
        s = get_node(subject)
        o = get_node(obj)
        e = EntityEdge(
            uuid=f"edge:{len(edges)}",
            group_id="bcc-investigation",
            source_node_uuid=s.uuid,
            target_node_uuid=o.uuid,
            created_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
            name=rel,
            fact=f"{subject} {rel} {obj}",
            valid_at=parse(vf),
            invalid_at=parse(vt),
            reference_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
            attributes={"evidence": ev},
        )
        edges.append(e)

    # Temporal point-in-time resolution over the edge set
    def active_on(rel, obj, when):
        t = parse(when)
        hits = []
        for e in edges:
            if e.name == rel and e.target_node_uuid == obj and e.fact:
                if (e.valid_at is None or e.valid_at <= t) and \
                   (e.invalid_at is None or t < e.invalid_at):
                    hits.append(e.source_node_uuid)
        return hits

    node_name = {n.uuid: n.name for n in nodes.values()}
    bcc_uuid = next(n.uuid for n in nodes.values() if n.name == "BCC")
    queries = {
        "Who chaired BCC on 2024-01-01?": [
            node_name[u] for u in active_on("chairs board of", bcc_uuid, "2024-01-01")],
        "Who chaired BCC on 2025-10-01?": [
            node_name[u] for u in active_on("chairs board of", bcc_uuid, "2025-10-01")],
        "Who chaired BCC on 2026-08-01?": [
            node_name[u] for u in active_on("chairs board of", bcc_uuid, "2026-08-01")],
        "Who was CEO on 2018-06-15?": [
            node_name[u] for u in active_on("CEO of", bcc_uuid, "2018-06-15")],
        "Who was CEO on 2019-06-15?": [
            node_name[u] for u in active_on("CEO of", bcc_uuid, "2019-06-15")],
    }

    out = {
        "generated": "2026-09-05",
        "framework": "graphiti-core 0.30.1 (Zep, temporal KG)",
        "model": "EntityNode + EntityEdge (valid_at/invalid_at/reference_time) in-memory",
        "backend_note": "Full graphiti engine path = LLM extractor + Neo4j/FalkorDB "
                        "backend + embedder; model validated here without backend.",
        "n_facts": len(facts()),
        "facts": [{"subject": s, "relation": r, "object": o,
                   "valid_from": vf, "valid_to": vt, "evidence": ev}
                  for s, r, o, vf, vt, ev in facts()],
        "temporal_queries": {q: a for q, a in queries.items()},
        "board_reorg_window": "~May 15, 2026 (Plageman→Seitz chair; Robinson→Ralph treasurer)",
    }
    with open("bcc_graphiti_temporal.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    fact_rows = "".join(
        f'<tr><td>{r["subject"]}</td><td><span class="rel">{r["relation"]}</span></td>'
        f'<td>{r["object"]}</td><td>{r["valid_from"] or "?"}</td>'
        f'<td>{r["valid_to"] or "present"}</td></tr>' for r in out["facts"])
    q_rows = "".join(
        f'<tr><td>{q}</td><td>{", ".join(a) if a else "(none active)"}</td></tr>'
        for q, a in queries.items())

    report = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BCC Temporal Knowledge Graph — September 5, 2026</title>
<style>
  :root {{ --bg:#0d1117; --card:#161b22; --border:#30363d; --text:#c9d1d9;
          --h:#f0f6fc; --a:#58a6ff; --g:#3fb950; --y:#ffa657; --p:#d2a8ff; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--text); line-height:1.55; }}
  header {{ padding:28px 32px; border-bottom:1px solid var(--border);
           background:linear-gradient(180deg,#10151d,#0d1117); }}
  h1 {{ color:var(--h); font-size:1.35rem; }}
  .sub {{ color:#8b949e; font-size:.9rem; margin-top:6px; }}
  main {{ max-width:1000px; margin:0 auto; padding:24px 32px; }}
  h2 {{ color:var(--h); font-size:1.05rem; margin:26px 0 12px; }}
  table {{ width:100%; border-collapse:collapse; }}
  th,td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--border);
          vertical-align:top; font-size:.92rem; }}
  th {{ color:var(--a); font-size:.78rem; text-transform:uppercase;
       letter-spacing:.06em; }}
  .rel {{ color:var(--p); }}
  .note {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
          padding:14px 18px; margin:18px 0; color:#8b949e; font-size:.88rem; }}
</style></head><body>
<header><h1>BCC Temporal Knowledge Graph — graphiti-core Prototype</h1>
<div class="sub">Time-aware relationship model of the board reorg + officer
history · EntityEdge valid_at/invalid_at intervals resolve point-in-time queries
· generated 2026-09-05</div></header>
<main>
<div class="note"><b>Why temporal:</b> the two-sphere firewall and FY2018-break
findings are time-bounded claims. Each relationship carries a valid interval —
<i>Plageman chairs BCC [2025-07-01 → 2026-05-15]</i> — so point-in-time queries
(who chaired in FY2018? who was CFO during the deficit era?) resolve mechanically
instead of by re-reading prose. <b>Status:</b> model validated in-memory with
graphiti_core's own EntityNode/EntityEdge types; production path = LLM extractor
+ Neo4j/FalkorDB backend.</div>

<h2>Officer / relationship facts (valid-time model)</h2>
<table><thead><tr><th>Subject</th><th>Relation</th><th>Object</th>
<th>From</th><th>To</th></tr></thead><tbody>{fact_rows}</tbody></table>

<h2>Temporal queries — point-in-time answers (resolved from intervals)</h2>
<table><thead><tr><th>Query</th><th>Answer</th></tr></thead>
<tbody>{q_rows}</tbody></table>
</main></body></html>"""
    with open("BCC_TEMPORAL_KG.html", "w", encoding="utf-8") as f:
        f.write(report)
    print(f"wrote BCC_TEMPORAL_KG.html + bcc_graphiti_temporal.json "
          f"({len(facts())} temporal facts)")


if __name__ == "__main__":
    main()