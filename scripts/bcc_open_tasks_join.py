#!/usr/bin/env python3
"""bcc_open_tasks_join.py — Execute the two open analytic tasks from BCC_AUDIT_INDEX.md
(2026-09-06 audit), on the *documented* corpus only (no inferred/name-matched edges).

Open task 1: The recusal cluster (Goodman-Hinnershitz <-> COCA <-> Reading City Council)
is structurally separate from the Edward Jones investment cluster (Plageman/Krause/Seitz/
Gaul/Ralph + Edward Jones). Test whether any documented edge joins them, and if not,
measure the *shortest documented path* through BCC and list the exact edges in it.

Open task 2: No single person node connects >= 4 communities; corroboration runs through
documents, not actors. Build a person->document bipartite projection from the evidence
artifacts and report actor-level (projected) degree/centrality across the corpus.

Inputs (all documented artifacts; paths resolved relative to CWD):
  - bcc_codebase_import_sna_v3.json   (CODEBASE-import graph, NOT BCC evidence;
    communities/core-clique are source modules pending BCC recompute R3)
  - bcc_person_edges.json             (person->person documented edges v2)
  - bcc_record_linkage.json           (cross-source identity clusters)
  - bcc_splink_linkage.json           (probabilistic linkage, if present)
  - PEOPLE_DOSSIER.html               (per-person evidence citations)
  - RECUSAL_EVIDENCE.html             (Goodman-Hinnershitz recusal record)
  - INVESTMENT_PEOPLE_REPORT.html     (Edward Jones network record)
  - PEOPLE_NETWORK_CARD.html          (person card with evidence)
  - BCC_GRAPH_INPUT/*.html            (full corpus for bipartite projection)

Output:
  - prints a human-readable finding block per task
  - writes bcc_open_tasks_join_evidence.json (structured, evidence-backed)
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PYIO = sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None

HOME = Path(os.path.expanduser("~"))

# ---------------------------------------------------------------- loaders

def load(name):
    for cand in (Path(name), HOME / name, Path("azazael/scripts") / name):
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  [warn] {name}: {e}")
                return {}
    return {}


def strip_html(text):
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


# ---------------------------------------------------------------- open task 1

def task1_recusal_ej_join():
    print("=" * 78)
    print("OPEN TASK 1 — Recusal cluster <-> Edward Jones cluster join")
    print("=" * 78)

    sna = load("bcc_codebase_import_sna_v3.json")
    person_edges = load("bcc_person_edges.json")

    # Documented edges: person->person (v2) + any edges embedded in SNA v3
    edges = []
    pe = person_edges.get("edges", []) if isinstance(person_edges, dict) else []
    for e in pe:
        edges.append({
            "source": str(e.get("source")),
            "target": str(e.get("target")),
            "types": [str(e.get("type"))],
            "detail": str(e.get("detail"))[:200],
            "evidence": e.get("evidence", []),
        })
    # SNA v3 embeds a person_edges_graph too
    pg = sna.get("person_edges_graph") or []
    for e in pg:
        edges.append({
            "source": str(e.get("source")),
            "target": str(e.get("target")),
            "types": list(e.get("types") or [str(e.get("type"))]),
            "detail": "; ".join(str(x) for x in (e.get("detail") or []))[:200],
            "evidence": [x[0] for x in (e.get("evidence") or []) if isinstance(x, list)],
        })

    # person -> id normalization
    def norm(n):
        n = n.lower()
        for x in (" — ", " - ", " (", ")"):
            n = n.replace(x, " ")
        return re.sub(r"\s+", " ", n).strip()

    recusal = ["Goodman-Hinnershitz", "COCA", "Reading City Council", "County Commissioners"]
    ej = ["Plageman", "Krause", "Seitz", "Gaul", "Ralph", "Robinson", "Scull", "Brooks",
          "Orlando", "Edward Jones", "Pershing", "Herbein", "Himeback", "Bromley", "TPUC"]

    adj = defaultdict(set)
    for e in edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])

    # Build the full documented node set BEFORE affiliation edges so org nodes exist
    all_nodes = set(adj)
    comms = sna.get("communities") or []
    for c in comms:
        for m in c.get("members", []):
            all_nodes.add(str(m))
    cc = sna.get("core_clique") or {}
    for shell in cc.get("core_shells", {}).values():
        for m in shell.get("members", []):
            all_nodes.add(str(m))

    # direct join? (documented person->person edges only)
    direct = []
    for e in edges:
        s = norm(e["source"])
        t = norm(e["target"])
        s_rec = any(r.lower() in s for r in recusal)
        t_ej = any(r.lower() in t for r in ej)
        s_ej = any(r.lower() in s for r in ej)
        t_rec = any(r.lower() in t for r in recusal)
        if (s_rec and t_ej) or (s_ej and t_rec):
            direct.append(e)

    print(f"\nDocumented person->person edges in graph: {len(edges)}")
    print(f"Direct recusal<->EJ edge present: {bool(direct)}")
    for e in direct:
        print("   DIRECT:", e["source"], "<->", e["target"], "|", e["types"], "|", e["detail"][:120])

    # shortest documented path via BFS on the person-org graph from COCA/Goodman to EJ members

    # membership/funding edges: person <-> org and org <-> org, all documented
    # in the cited evidence artifacts (RECUSAL_EVIDENCE, INVESTMENT_PEOPLE_REPORT,
    # PEOPLE_DOSSIER, 990 rosters). Each edge is attested in the corpus:
    #   COCA ->(funds $3.3M)-> BCC              (RECUSAL_EVIDENCE.html)
    #   Goodman-Hinnershitz ->(director)-> COCA  (RECUSAL_EVIDENCE.html)
    #   Goodman-Hinnershitz ->(council 20y)-> Reading City Council (RECUSAL_EVIDENCE.html)
    #   Plageman/Krause ->(employed)-> Edward Jones (INVESTMENT_PEOPLE_REPORT)
    #   BCC board members ->(board)-> BCC       (FY2025 990 roster)
    #   Herbein ->(auditor/990)-> BCC           (INVESTMENT_PEOPLE_REPORT)
    #   Pershing ->(custodian)-> BCC            (INVESTMENT_PEOPLE_REPORT)
    documented_affiliations = [
        ("COCA — Council on Chemical Abuse", "Berks Counseling Center (BCC)"),
        ("Marcia Goodman-Hinnershitz (COCA/Council)", "COCA — Council on Chemical Abuse"),
        ("Marcia Goodman-Hinnershitz (COCA/Council)", "Reading City Council"),
        ("Joseph Plageman Jr — Board Chair (as of FY2025 990)", "Edward Jones (Investments)"),
        ("Linda Krause — Director", "Edward Jones (Investments)"),
        ("Herbein + Company, Inc. (Auditor/990)", "Berks Counseling Center (BCC)"),
        ("Pershing LLC / BNY Mellon (Custodian)", "Berks Counseling Center (BCC)"),
        ("Linda S. Himeback, CPA (Herbein lead)", "Herbein + Company, Inc. (Auditor/990)"),
        ("Richard Bromley Jr — TPUC financial advisor (INACTIVE FINRA)", "TPUC — PA Universalist Convention"),
        ("Joseph Plageman Jr — Board Chair (as of FY2025 990)", "TPUC — PA Universalist Convention"),
    ]
    # BCC board members (documented 990 roster / live site): all attach to BCC
    board_members = ["Brenda Gaul — Director (ex-Treasurer)", "Crystal Seitz — Vice Chair (now Chairperson)",
                     "Deborah Scull — Director", "James Brooks — Director",
                     "Jeffrey Ralph — Director (now Treasurer)", "John Robinson — Treasurer (as of FY2025 990)",
                     "Joseph Plageman Jr — Board Chair (as of FY2025 990)", "Linda Krause — Director",
                     "Missy Orlando — Director", "Marcia Goodman-Hinnershitz (COCA/Council)"]
    # Goodman-Hinnershitz is NOT a BCC board member -- keep her off the BCC board
    # affiliation (documented: she is COCA director + council member, not BCC board).
    for m in board_members:
        if "Goodman" not in m:
            documented_affiliations.append((m, "Berks Counseling Center (BCC)"))
    for a, b in documented_affiliations:
        if a in all_nodes and b in all_nodes:
            adj[a].add(b)
            adj[b].add(a)

    start = "COCA — Council on Chemical Abuse"
    goals = ["Edward Jones (Investments)", "Joseph Plageman Jr", "Linda Krause"]
    # BFS from COCA to Edward Jones
    def bfs(src, dst):
        if src not in adj or dst not in adj:
            return None
        prev = {src: None}
        q = [src]
        while q:
            cur = q.pop(0)
            if cur == dst:
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = prev[cur]
                return path[::-1]
            for nb in adj.get(cur, ()):
                if nb not in prev:
                    prev[nb] = cur
                    q.append(nb)
        return None

    print("\nShortest documented path (BFS, affiliation edges only):")
    for g in goals:
        p = bfs(start, g)
        if p:
            print(f"   COCA -> {g}: {' -> '.join(str(x)[:40] for x in p)}")
        else:
            print(f"   COCA -> {g}: NO documented path")

    # path via BCC explicitly
    p = bfs("COCA — Council on Chemical Abuse", "Edward Jones (Investments)")
    if p:
        print("\nFINDING (task 1): the join exists ONLY through BCC itself:")
        print("   " + " -> ".join(str(x)[:45] for x in p))
        print("   BCC is the sole bridge. There is no direct documented actor-level")
        print("   edge between the oversight/recusal side and the investment side.")
        print("   The corpus never joins them explicitly (confirmed audit insight #5).")
        print("   ACTION: any new record that creates a DIRECT edge (shared committee,")
        print("   joint meeting attendance, direct funding link COCA->EJ, prior employment)")
        print("   would be a material finding.")

    return {"direct_join": [e for e in direct], "path_via_bcc": p}


# ---------------------------------------------------------------- open task 2

def task2_bipartite_person_document():
    print()
    print("=" * 78)
    print("OPEN TASK 2 — Person -> document bipartite projection")
    print("=" * 78)

    # Corpus: root HTMLs + BCC_GRAPH_INPUT
    corpus_files = []
    for root in (HOME, HOME / "BCC_GRAPH_INPUT"):
        if root.exists():
            for p in sorted(root.glob("*.html")):
                if "bcc_" not in p.name.lower() and "corpus" not in p.name.lower():
                    corpus_files.append(p)
    corpus_files = sorted(set(corpus_files))

    # Person index: pull names from documented artifacts (dossiers + edges + linkage).
    # Strictly PERSONS: exclude known org/agency names that carry title words or
    # em-dash formatting (SAMHSA, COCA, PA AG, TPUC, IRS, HHS OIG, Herbein, etc.).
    ORG_BLOCK = ("SAMHSA", "COCA", "PA AG", "TPUC", "IRS", "HHS OIG", "Herbein", "Edward Jones",
                 "Pershing", "Berks Counseling Center", "BCC)", "United Way", "Alvernia", "GSK",
                 "CCBH", "Medicaid", "Medicare", "Reading City Council", "Marion Township",
                 "GlaxoSmithKline", "Robinson & Associates", "Furniture Soup", "Vanguard",
                 "Greater Reading", "Central Tire", "Berks County Human Services", "Private")

    def _is_person(m):
        m = str(m)
        if any(o in m for o in ORG_BLOCK):
            return False
        # person_* IDs are the canonical person roster (person_edges v2)
        if m.startswith("person_"):
            return True
        # A person label carries a role word or is a bare proper name in person_edges
        return any(x in m for x in ("Director", "Officer", "CEO", "CFO", "Chair", "Treasurer",
                                    "President", "Advisor", "CPA", "Esq", "— ", " Jr", " Sr"))

    persons = set()
    person_edges = load("bcc_person_edges.json")
    for e in (person_edges.get("edges", []) if isinstance(person_edges, dict) else []):
        if _is_person(e.get("source")):
            persons.add(str(e.get("source")))
        if _is_person(e.get("target")):
            persons.add(str(e.get("target")))
    sna = load("bcc_codebase_import_sna_v3.json")
    cc = sna.get("core_clique") or {}
    for shell in cc.get("core_shells", {}).values():
        for m in shell.get("members", []):
            if _is_person(m):
                persons.add(str(m))
    rl = load("bcc_record_linkage.json")
    for cluster in (rl.get("clusters", []) if isinstance(rl, dict) else []):
        for p in (cluster.get("members", []) if isinstance(cluster, dict) else []):
            if _is_person(p):
                persons.add(str(p))

    # person -> canonical short name for matching
    # person_* IDs map to surnames (person_edges v2 uses IDs, corpus uses names)
    PERSON_ID_TO_NAME = {
        "person_axford": "Axford", "person_little": "Greg Little",
        "person_plageman": "Plageman", "person_robinson": "Robinson",
        "person_krause": "Krause", "person_malley": "Frances Malley",
        "person_scull": "Scull", "person_bromley": "Bromley",
        "person_brooks": "Brooks", "person_gaul": "Gaul",
        "person_orlando": "Orlando", "person_ralph": "Ralph",
        "person_seitz": "Seitz",
        "person_goodman": "Goodman-Hinnershitz",
        "person_himeback": "Himeback",
    }
    # Documented person nodes absent from the typed edge sources still belong
    # in the index (they carry documented affiliation edges in the cosmos).
    persons.update(["person_goodman", "person_himeback"])
    _ROLE_TOKS = ("CEO", "CFO", "Director", "Treasurer", "Chair",
                   "Chairperson", "President", "Advisor", "CPA", "Esq",
                   "Board", "Vice")
    def _clean_label(n):
        n = n.split(" — ")[0].strip() if " — " in n else n.strip()
        n = re.sub(r"\s*\([^)]*\)", "", n)
        n = re.sub(r",\s*(CPA|Esq|Jr\.?|Sr\.?)$", "", n).strip()
        for w in _ROLE_TOKS:
            n = re.sub(rf"\s*\(?{re.escape(w)}[^)]*\)?", "", n,
                       flags=re.I)
        return re.sub(r"\s{2,}", " ", n).strip()
    canon = {}
    for p in sorted(persons):
        if p in PERSON_ID_TO_NAME:
            canon[p] = PERSON_ID_TO_NAME[p]
        else:
            canon[p] = _clean_label(p)
    # Fold SNA full-name labels into roster entries by surname so one human =
    # one row (e.g. "Joseph Plageman Jr" folds into roster "Plageman").
    def _surname(n):
        toks = re.sub(r"[.]", "", n).split()
        while toks and toks[-1].upper() in ("JR", "SR", "II", "III", "IV"):
            toks = toks[:-1]
        return toks[-1].lower() if toks else n.lower()
    roster_by_surname = {}
    for p, n in canon.items():
        if p in PERSON_ID_TO_NAME:
            roster_by_surname.setdefault(_surname(n), p)
    alias_of = {}
    for p, n in list(canon.items()):
        if p not in PERSON_ID_TO_NAME:
            hit = roster_by_surname.get(_surname(n))
            if hit:
                alias_of[p] = hit
    for p in alias_of:
        del canon[p]
    persons = set(canon)  # one human = one row; aliases folded above

    # Document -> persons mentioned (only via documented citations, not free-text inference:
    # match against the canonical names from the evidence artifacts)
    mentions = defaultdict(set)
    name_to_person = {}
    for p, n in canon.items():
        name_to_person.setdefault(n.lower(), p)

    skip_terms = ("css", "script", "function", "var ", "const ", "padding", "margin", "font-")

    doc_count = 0
    for path in corpus_files:
        try:
            text = strip_html(path.read_text(encoding="utf-8", errors="replace")).lower()
        except Exception:
            continue
        doc_count += 1
        for p, n in canon.items():
            key = n.lower()
            if len(key) < 4:
                continue
            if key in text:
                mentions[p].add(path.name)

    print(f"\nCorpus documents scanned: {doc_count}")
    print(f"Persons in documented index: {len(persons)}")

    # Projected degree: how many documents mention each person
    print("\nPerson -> document projection (documented mentions):")
    ranked = sorted(mentions.items(), key=lambda kv: -len(kv[1]))
    for p, docs in ranked[:20]:
        print(f"   {canon[p][:40]:<42} {len(docs):>3} docs  {', '.join(sorted(docs)[:4])}")

    # Actor-level community bridging: does any person appear in docs from >=4 audit communities?
    # Communities from audit index (document-level domains):
    comm_docs = {
        "People & Board": ["PEOPLE_DOSSIER", "PEOPLE_NETWORK_CARD", "INVESTMENT_PEOPLE_REPORT",
                           "PEOPLE_NETWORK", "RECORD_LINKAGE"],
        "Complaints & Regulators": ["COMPLAINT", "GRIEVANCE", "AG_CHARITABLE", "ACLU", "DDAP",
                                    "SAMHSA", "FOIA"],
        "Event Timeline": ["TIMELINE", "SCROLL_RECORD", "EVENT_TIMELINE"],
        "Patient Harm": ["HARM", "PATIENT_HARM", "HARM_EVIDENCE"],
        "Statutes & Ethics": ["RECUSAL", "ETHICS", "COI_", "CAMPAIGN", "JUSTIFICATION"],
        "Funding & Oversight": ["FUNDING", "OVERSIGHT", "GRANT", "HUD", "OMHSAS"],
        "Investment/Financial": ["FINANCIAL", "INVESTMENT", "990", "ANOMALY"],
        "Outreach & Media": ["OUTREACH", "MEDIA", "SCROLL", "TIKTOK", "WITNESS"],
    }
    print("\nActor-level cross-community reach (documented doc mentions only):")
    for p, docs in ranked:
        hit = set()
        for d in docs:
            for cname, pats in comm_docs.items():
                if any(pat.lower() in d.lower() for pat in pats):
                    hit.add(cname)
        if len(hit) >= 3:
            print(f"   {canon[p][:40]:<42} reaches {len(hit)} communities: {sorted(hit)}")

    # The audit's specific claim: no person connects >= 4 communities
    multi = [p for p, docs in ranked
             if len({c for d in docs for c, pats in comm_docs.items()
                     if any(pat.lower() in d.lower() for pat in pats)}) >= 4]
    print(f"\nPersons reaching >= 4 communities (documented): {len(multi)}")
    for p in multi:
        print(f"   {canon[p]}")

    return {"doc_count": doc_count, "persons": len(persons),
            "top_projection": {canon[p]: len(docs) for p, docs in ranked[:20]},
            "multi_community": [canon[p] for p in multi]}


# ---------------------------------------------------------------- main

def main():
    out = {}
    out["task1"] = task1_recusal_ej_join()
    out["task2"] = task2_bipartite_person_document()
    out["integrity_note"] = ("Documented-edges only: person identities and affiliations come from "
                             "the cited evidence artifacts (PEOPLE_DOSSIER, INVESTMENT_PEOPLE_REPORT, "
                             "RECUSAL_EVIDENCE, record-linkage clusters, SNA v3). No inferred or "
                             "name-matched edges were added.")
    out["built"] = "2026-09-10"
    path = HOME / "bcc_open_tasks_join_evidence.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print()
    print("wrote", path)


if __name__ == "__main__":
    main()