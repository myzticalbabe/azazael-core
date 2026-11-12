#!/usr/bin/env python3
"""bcc_cosmos_build.py — Build the TRUE BCC people-entity cosmos.

Every node is a real BCC person/entity with corpus provenance. Every edge is
documented in the cited evidence artifact. NO Azazael codebase nodes, NO
inferred/name-matched edges, NO filler.

Sources (all on disk):
  - azazael/scripts/bcc_person_edges.json          (v2: 9 board clique + 4 typed)
  - bcc_person_edges.enriched.json                 (4 new org edges, Oct 27)
  - PEOPLE_DOSSIER.html / INVESTMENT_PEOPLE_REPORT.html / RECUSAL_EVIDENCE.html
  - FY2025 990 roster (board), PA Courts PDF 898 MDA 2017, GovTribe grants,
    BCC Annual Report 2024-25

Outputs (C:/Users/clint/BCC_COSMOS/):
  - cosmos/graph.labels.0.js  (wire format read by bcc_network_sna.load_graph)
  - cosmos/graph.links.js      (base64 uint32 pairs, same wire format)
  - cosmos_manifest.json       (every node/edge + evidence = provenance)

Considered-but-excluded (no documented edge, so NOT nodes):
  - John Sellers, Jeffrey Mogel, Karen Kramer (dossier name mentions only,
    no affiliation/role edge in any artifact)
"""
import base64
import json
import os
import struct

OUT_DIR = "C:/Users/clint/BCC_COSMOS/cosmos"

PERSONS = [
    ("person_plageman", "Joseph Plageman Jr — Board Chair (as of FY2025 990)",
     "PEOPLE_DOSSIER.html",
     ["FY2025 990 roster (Chairperson)", "PEOPLE_DOSSIER.html",
      "INVESTMENT_PEOPLE_REPORT.html"]),
    ("person_seitz", "Crystal Seitz — Vice Chair (now Chairperson)",
     "PEOPLE_DOSSIER.html",
     ["INVESTIGATION_GAPS.html #4", "PEOPLE_DOSSIER.html"]),
    ("person_robinson", "John Robinson — Treasurer (as of FY2025 990)",
     "PEOPLE_DOSSIER.html",
     ["FY2025 990 roster (Treasurer)", "INVESTIGATION_GAPS.html",
      "PEOPLE_DOSSIER.html"]),
    ("person_krause", "Linda Krause — Director",
     "PEOPLE_DOSSIER.html",
     ["FY2025 990 roster", "PEOPLE_DOSSIER.html",
      "INVESTMENT_PEOPLE_REPORT.html"]),
    ("person_gaul", "Brenda Gaul — Director (ex-Treasurer)",
     "PEOPLE_DOSSIER.html",
     ["FY2025 990 roster", "PEOPLE_DOSSIER.html"]),
    ("person_orlando", "Missy Orlando — Director",
     "PEOPLE_DOSSIER.html",
     ["FY2025 990 roster", "PEOPLE_DOSSIER.html"]),
    ("person_scull", "Deborah Scull — Director",
     "PEOPLE_DOSSIER.html",
     ["FY2025 990 roster", "PEOPLE_DOSSIER.html"]),
    ("person_brooks", "James Brooks — Director",
     "PEOPLE_DOSSIER.html",
     ["FY2025 990 roster", "PEOPLE_DOSSIER.html"]),
    ("person_ralph", "Jeffrey Ralph — Director (now Treasurer)",
     "PEOPLE_DOSSIER.html",
     ["FY2025 990 roster", "PEOPLE_DOSSIER.html"]),
    ("person_axford", "Christine Axford — CEO/President (since Jan 2019)",
     "PEOPLE_DOSSIER.html",
     ["PEOPLE_DOSSIER.html", "INVESTMENT_PEOPLE_REPORT.html",
      "BCC Annual Report 2024-25"]),
    ("person_little", "Greg Little — CFO (since Jan 2019)",
     "PEOPLE_DOSSIER.html",
     ["PEOPLE_DOSSIER.html", "INVESTMENT_PEOPLE_REPORT.html"]),
    ("person_malley", "Frances Malley — former CEO (pre-2019)",
     "PEOPLE_DOSSIER.html",
     ["PEOPLE_DOSSIER.html", "INVESTMENT_PEOPLE_REPORT.html"]),
    ("person_bromley", "Richard Bromley Jr — TPUC financial advisor (INACTIVE FINRA)",
     "PEOPLE_DOSSIER.html",
     ["PEOPLE_DOSSIER.html", "INVESTMENT_PEOPLE_REPORT.html"]),
    ("person_goodman", "Marcia Goodman-Hinnershitz (COCA/Council)",
     "RECUSAL_EVIDENCE.html",
     ["RECUSAL_EVIDENCE.html (COCA director + 20y council)"]),
    ("person_himeback", "Linda S. Himeback, CPA (Herbein lead)",
     "INVESTMENT_PEOPLE_REPORT.html",
     ["INVESTMENT_PEOPLE_REPORT.html (Herbein lead on BCC audit)"]),
]

ORGS = [
    ("org_bcc", "Berks Counseling Center (BCC)", "PEOPLE_DOSSIER.html",
     ["FY2025 990", "BCC Annual Report 2024-25"]),
    ("org_coca", "COCA — Council on Chemical Abuse", "RECUSAL_EVIDENCE.html",
     ["RECUSAL_EVIDENCE.html ($3.3M funder of BCC)"]),
    ("org_ej", "Edward Jones (Investments)", "INVESTMENT_PEOPLE_REPORT.html",
     ["INVESTMENT_PEOPLE_REPORT.html (Plageman/Krause branch)"]),
    ("org_tpuc", "TPUC — PA Universalist Convention", "PEOPLE_DOSSIER.html",
     ["PEOPLE_DOSSIER.html (Plageman president, Bromley advisor)"]),
    ("org_herbein", "Herbein + Company, Inc. (Auditor/990)",
     "INVESTMENT_PEOPLE_REPORT.html",
     ["INVESTMENT_PEOPLE_REPORT.html (BCC auditor)"]),
    ("org_pershing", "Pershing LLC / BNY Mellon (Custodian)",
     "INVESTMENT_PEOPLE_REPORT.html",
     ["INVESTMENT_PEOPLE_REPORT.html (BCC custodian)"]),
    ("org_court", "PA Superior Court", "fwa_opinion_898_mda_2017.pdf",
     ["PA Courts PDF 898 MDA 2017 (affirmed BCC victory Nov 7 2018)",
      "CourtListener cross-reference"]),
    ("org_hud", "U.S. Dept of Housing and Urban Development",
     "govtribe_grants.json",
     ["GovTribe/USAspending.gov (2 CoC grants $204,870)"]),
    ("org_nextgen", "NextGen Healthcare (EHR vendor)", "BCC_Annual_Report_24-25.pdf",
     ["Annual Report (go-live Mar 2 2025)"]),
    ("org_mhdd", "Berks County MH/DD", "BCC_Annual_Report_24-25.pdf",
     ["Annual Report (Mobile Med Program partner)"]),
    ("org_cc", "Community Care Behavioral Health", "fwa_opinion_898_mda_2017.pdf",
     ["PA Courts PDF 898 MDA 2017 (counterparty, $81,704.47 retractions)"]),
    ("org_council", "Reading City Council", "RECUSAL_EVIDENCE.html",
     ["RECUSAL_EVIDENCE.html (Goodman 20y member)"]),
]

BOARD = ["person_plageman", "person_seitz", "person_robinson", "person_krause",
         "person_gaul", "person_orlando", "person_scull", "person_brooks",
         "person_ralph"]

# (source, target, kind, evidence) — every edge documented, no exceptions.
EDGES = []
# 1) same-board clique: 36 pairs, co-serve per FY2025 990 roster + live site Sep 2026
for i in range(len(BOARD)):
    for j in range(i + 1, len(BOARD)):
        EDGES.append((BOARD[i], BOARD[j], "same_board",
                      ["FY2025 990 roster", "live site Sep 2026 (PEOPLE_DOSSIER)"]))
# 2) typed person-person (person_edges v2, all corpus-documented)
EDGES += [
    ("person_plageman", "person_krause", "co_employment",
     ["PEOPLE_NETWORK_CARD.html", "INVESTMENT_PEOPLE_REPORT.html",
      "PEOPLE_DOSSIER.html"]),
    ("person_plageman", "person_bromley", "same_org_committee",
     ["PEOPLE_DOSSIER.html", "INVESTMENT_PEOPLE_REPORT.html"]),
    ("person_axford", "person_little", "co_executive",
     ["PEOPLE_DOSSIER.html", "INVESTMENT_PEOPLE_REPORT.html"]),
    ("person_malley", "person_axford", "succession",
     ["PEOPLE_DOSSIER.html", "INVESTMENT_PEOPLE_REPORT.html"]),
    ("person_goodman", "person_plageman", "oversight_relationship",
     ["RECUSAL_EVIDENCE.html (COCA funds BCC; recusal cluster)"]),
]
# 3) board membership (documented 990 roster)
for b in BOARD:
    EDGES.append((b, "org_bcc", "board_member",
                  ["FY2025 990 roster", "PEOPLE_DOSSIER.html"]))
# 4) executive / employment / advisory
EDGES += [
    ("person_axford", "org_bcc", "ceo",
     ["PEOPLE_DOSSIER.html", "BCC Annual Report 2024-25"]),
    ("person_little", "org_bcc", "cfo", ["PEOPLE_DOSSIER.html"]),
    ("person_malley", "org_bcc", "former_ceo", ["PEOPLE_DOSSIER.html"]),
    ("person_plageman", "org_ej", "employed",
     ["INVESTMENT_PEOPLE_REPORT.html (CRD #5964768, 918 Penn Ave)"]),
    ("person_krause", "org_ej", "employed",
     ["INVESTMENT_PEOPLE_REPORT.html (Sr Branch Office Admin)"]),
    ("person_plageman", "org_tpuc", "president",
     ["PEOPLE_DOSSIER.html (elected Apr 2026)"]),
    ("person_bromley", "org_tpuc", "advisor",
     ["PEOPLE_DOSSIER.html (Financial Committee advisor)"]),
    ("person_goodman", "org_coca", "director", ["RECUSAL_EVIDENCE.html"]),
    ("person_goodman", "org_council", "council_member",
     ["RECUSAL_EVIDENCE.html (20y)"]),
    ("person_himeback", "org_herbein", "lead_cpa",
     ["INVESTMENT_PEOPLE_REPORT.html"]),
]
# 5) org-org (documented funding/audit/custody/litigation/grants/vendor)
EDGES += [
    ("org_coca", "org_bcc", "funds", ["RECUSAL_EVIDENCE.html ($3.3M)"]),
    ("org_herbein", "org_bcc", "auditor",
     ["INVESTMENT_PEOPLE_REPORT.html"]),
    ("org_pershing", "org_bcc", "custodian",
     ["INVESTMENT_PEOPLE_REPORT.html"]),
    ("org_bcc", "org_court", "litigant",
     ["PA Courts PDF 898 MDA 2017", "CourtListener"]),
    ("org_bcc", "org_cc", "litigation_counterparty",
     ["PA Courts PDF 898 MDA 2017"]),
    ("org_bcc", "org_hud", "grant_recipient",
     ["GovTribe/USAspending.gov (PA0703L3T062309, PA0159L3T062316)"]),
    ("org_bcc", "org_nextgen", "ehr_vendor",
     ["BCC Annual Report 2024-25 (go-live Mar 2 2025)"]),
    ("org_bcc", "org_mhdd", "program_partner",
     ["BCC Annual Report 2024-25 (Mobile Med Program)"]),
]


def main():
    nodes = []
    for pid, label, f, _ev in PERSONS:
        nodes.append({"id": pid, "label": label, "file": f, "type": "person"})
    for oid, label, f, _ev in ORGS:
        nodes.append({"id": oid, "label": label, "file": f, "type": "org"})
    idx = {n["id"]: i for i, n in enumerate(nodes)}

    pairs = []
    manifest_edges = []
    for s, t, kind, ev in EDGES:
        if s not in idx or t not in idx:
            raise SystemExit(f"edge references unknown node: {s} -> {t}")
        pairs += [idx[s], idx[t]]
        manifest_edges.append({"source": s, "target": t, "kind": kind,
                               "evidence": ev})

    # degree for label deg field
    deg = [0] * len(nodes)
    for a, b in zip(pairs[0::2], pairs[1::2]):
        deg[a] += 1
        deg[b] += 1
    for i, n in enumerate(nodes):
        n["deg"] = deg[i]

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "graph.labels.0.js"), "w",
              encoding="utf-8") as f:
        f.write("window.GV_LABELS_CHUNKS.push(")
        json.dump(nodes, f, ensure_ascii=False)
        f.write(");")

    blob = base64.b64encode(struct.pack("<%dI" % len(pairs), *pairs)).decode()
    with open(os.path.join(OUT_DIR, "graph.links.js"), "w",
              encoding="utf-8") as f:
        f.write("window.GV_LINKS=Uint32Array.from(atob('")
        f.write(blob)
        f.write("'),c=>c.charCodeAt(0));")

    manifest = {
        "built": "2026-10-27",
        "standard": "documented-edges-only; no inferred/name-matched edges",
        "n_nodes": len(nodes),
        "n_persons": len(PERSONS),
        "n_orgs": len(ORGS),
        "n_edges": len(manifest_edges),
        "nodes": [{"id": n["id"], "label": n["label"], "type": n["type"],
                   "file": n["file"]} for n in nodes],
        "edges": manifest_edges,
        "excluded": [
            {"name": n, "reason": "dossier mention only; no documented edge"}
            for n in ["John Sellers", "Jeffrey Mogel", "Karen Kramer"]
        ],
    }
    with open("C:/Users/clint/BCC_COSMOS/cosmos_manifest.json", "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    print(f"cosmos: {len(nodes)} nodes ({len(PERSONS)} persons, "
          f"{len(ORGS)} orgs), {len(manifest_edges)} documented edges")
    print("wrote", OUT_DIR + "/graph.labels.0.js + graph.links.js")
    print("wrote C:/Users/clint/BCC_COSMOS/cosmos_manifest.json")


if __name__ == "__main__":
    main()
