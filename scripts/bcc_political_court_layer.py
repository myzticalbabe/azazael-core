#!/usr/bin/env python3
"""bcc_political_court_layer.py -- Expand the BCC documented network with the
political / court / state-oversight layer that the SNA maps have never included.

WHAT THIS IS
  The base cosmos graph (48 nodes: board, staff, funders, regulators) already
  links BCC -> COCA -> Goodman-Hinnershitz -> Reading City Council. What it does
  NOT contain is the people who act on BCC's oversight environment as elected
  officials / committee chairs / court actors. This script adds them as nodes
  and connects them ONLY through corpus-documented relationships:

    - committee seats (LEGISLATIVE_COMMITTEES.html tables: chair/minority-chair
      and the Berks home-district member)
    - committee jurisdiction over the agencies that fund/license BCC
      (SAMHSA/CCBHC via US HELP; OMHSAS/DDAP licensing via PA Senate/House
      Health & Human Services; charitable registration via PA State Government;
      county drug&alcohol pass-through via COCA)
    - litigation (Morrison v. BCC 2025 MDJ -> CP appeal; eviction dockets)
    - the court record-keeper channel (Prothonotary Del Collo)

  Per the corpus rule (bcc_person_edges.json note): EVERY edge must trace to a
  cited public record or corpus doc; NO inferred/name-matched edges about
  private individuals; orgs are not persons. Politicians/court actors enter as
  PUBLIC-ROLE nodes (elected office / committee seat / litigation party), not as
  private-person dossiers.

Run:  python bcc_political_court_layer.py
Out:  bcc_political_court_evidence.json  +  BCC_GOV_OVERSIGHT_MAP.html
"""

import json
import os
import re
import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bcc_network_sna as bsna  # noqa: E402

COSMOS = "Desktop/AZAZAEL_COSMOS_VIEWER_bcc/cosmos"
OUT_JSON = "bcc_political_court_evidence.json"
OUT_HTML = "BCC_GOV_OVERSIGHT_MAP.html"


# ---------------------------------------------------------------------------
# Documented new nodes (public-role actors) -> (id, label, type, source doc)
# ---------------------------------------------------------------------------
NEW_NODES = [
    # ---- Berks County elected (county funding / drug&alcohol pass-through) ----
    ("gov_leinbach", "Christian Y. Leinbach — Chairman, Berks County Commissioners", "person", "COMPLAINT_LETTER.html TO line"),
    # ---- PA Senate (HHS = CCBHC/OMHSAS/MMJ oversight; Approp = DHS budget) ----
    ("gov_sen_brooks", "Sen. Michele Brooks — Chair, PA Senate Health & Human Services (R-50)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_haywood", "Sen. Art Haywood — Minority Chair, PA Senate H&HS (D-4)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_schwank", "Sen. Judith Schwank — Berks home-district member, Senate H&HS (D-11)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_martin", "Sen. Scott Martin — Chair, Senate Appropriations (R-13)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_dush", "Sen. Cris Dush — Chair, Senate State Government (R-25)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_santarsiero", "Sen. Steven J. Santarsiero — Minority Chair, Senate State Gov (D-10)", "person", "LEGISLATIVE_COMMITTEES.html"),
    # ---- PA House ----
    ("gov_rep_williams", "Rep. Dan K. Williams — Chair, House Human Services (D-74)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_heffley", "Rep. Doyle Heffley — Minority Chair, House Human Services (R-122)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_frankel", "Rep. Dan Frankel — Chair, House Health (D-23)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_rapp", "Rep. Kathy L. Rapp — Minority Chair, House Health (R-65)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_hill_evans", "Rep. Carol Hill-Evans — Chair, House State Government (D-95)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_roae", "Rep. Brad Roae — Minority Chair, House State Government (R-6)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_harris", "Rep. Jordan A. Harris — Chair, House Appropriations (D-186)", "person", "LEGISLATIVE_COMMITTEES.html"),
    # ---- Federal (SAMHSA/CCBHC authorization + grant oversight) ----
    ("gov_sen_casey", "Sen. Robert P. Casey Jr. — US Senate HELP (D-PA)", "person", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_fetterman", "Sen. John Fetterman (D-PA)", "person", "COMPLAINT_LETTER.html"),
    # ---- Court / litigation actors (public docket facts) ----
    ("court_morrison", "Regina Morrison — plaintiff, Morrison v. BCC (2025 MDJ -> CP appeal)", "person", "PACOURTS_CP_RECORDS_REQUEST.html"),
    ("court_prothonotary", "Jonathan K. Del Collo — Prothonotary, Berks CP (record keeper)", "person", "PACOURTS_CP_RECORDS_REQUEST.html"),
    # ---- Oversight bodies already partially present; add the committee seats
    # as org nodes so jurisdiction edges are explicit ----
    ("org_sen_hhs", "PA Senate Health & Human Services Committee", "org", "LEGISLATIVE_COMMITTEES.html"),
    ("org_sen_approp", "PA Senate Appropriations Committee", "org", "LEGISLATIVE_COMMITTEES.html"),
    ("org_sen_stategov", "PA Senate State Government Committee", "org", "LEGISLATIVE_COMMITTEES.html"),
    ("org_house_humansvcs", "PA House Human Services Committee", "org", "LEGISLATIVE_COMMITTEES.html"),
    ("org_house_health", "PA House Health Committee", "org", "LEGISLATIVE_COMMITTEES.html"),
    ("org_house_stategov", "PA House State Government Committee", "org", "LEGISLATIVE_COMMITTEES.html"),
    ("org_house_approp", "PA House Appropriations Committee", "org", "LEGISLATIVE_COMMITTEES.html"),
    ("org_us_help", "US Senate HELP Committee", "org", "LEGISLATIVE_COMMITTEES.html"),
    # ---- open records / evidence channels (the datapoint venues the gaps seek) ----
    ("rec_county_openrecords", "Berks County Open Records Officer — RTKL records channel (county contracts / MOUs / payments w/ BCC + OMHSAS-audit variant)", "org", "BERKS_RTK_REQUEST.txt (gaps #13/#18)"),
    ("rec_county_minutes", "Berks County Commissioners' public meeting minutes — contract-award / funding-approval record", "org", "INVESTIGATION_GAPS.html gap #13 (RTK seeks meeting minutes on award/renewal/amendment)"),
    ("rec_coca_detail", "COCA grant-detail records — annual amounts of the 5 pass-through grants (2019-2023, $3.3M cumulative)", "org", "INVESTIGATION_GAPS.html gap #22"),
    # ---- approval-layer datapoints from COUNTY_APPROVAL_MINING (Sep 5, 2026) ----
    ("rec_consent_docket", "Berks Commissioners' consent-agenda approval docket — Dec 4, 2025: block-grant INE attestation + 15-agreement contract packet + ~$5.5M procurement, voice vote, no per-item roll calls", "org", "COUNTY_APPROVAL_MINING.html Layer 1 (Citizen Portal transcript Dec 4, 2025)"),
    ("rec_hs_budget_acclaim", "Berks Commissioners' human-services budget authorization — Jun 25/Jul 2, 2026: $16,239,797 block grant approved by acclamation, no provider-level scrutiny on record", "org", "COUNTY_APPROVAL_MINING.html Layer 1 (Reading Eagle Jun 25, 2026)"),
    ("rec_hs_plan_entrench", "Berks DHS Human Services Block Grant Plans FY18-19→FY22-23 — designate BCC as Forensic Housing (15-17 units) / High Risk Team / ICWC hub / Tower PES operator; county purchased BCC's forensic housing with retained earnings after the 17-lease landlord exits", "org", "COUNTY_APPROVAL_MINING.html Layer 2 (pa.gov DHS plan library)"),
    ("rec_osf_ledger", "Berks Opioid Settlement Fund expenditures ledger — Mar 2026: COCA-administered disbursements itemized by recipient; BCC direct rows $172,220 budgeted (Treatment/Counseling $100,000 + Contingency Mgmt $60,000 + Harm Reduction $12,220)", "org", "BERKS_OSF_ITEMIZATION.html (COCA Mar 2026 PDF)"),
]


# ---------------------------------------------------------------------------
# Documented edges: the source/target of each tuple may be a NEW_NODES id or
# an EXISTING cosmos id
# (org_coca / funder_county / reg_omhsas / reg_ddap / reg_bco / org_reading_council
# / bcc / reg_oig / reg_samhsa etc.)
# ---------------------------------------------------------------------------
NEW_EDGES = [
    # ---- committee seats (documented role: chairs / minority chairs / member) ----
    ("gov_sen_brooks", "org_sen_hhs", "committee_seat", "Chair (Majority)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_haywood", "org_sen_hhs", "committee_seat", "Minority Chair (D)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_schwank", "org_sen_hhs", "committee_seat", "Berks home-district member (D-11)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_martin", "org_sen_approp", "committee_seat", "Chair", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_dush", "org_sen_stategov", "committee_seat", "Chair (Majority)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_santarsiero", "org_sen_stategov", "committee_seat", "Minority Chair (D)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_williams", "org_house_humansvcs", "committee_seat", "Chair (Majority)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_heffley", "org_house_humansvcs", "committee_seat", "Minority Chair (R)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_frankel", "org_house_health", "committee_seat", "Chair (Majority)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_rapp", "org_house_health", "committee_seat", "Minority Chair (R)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_hill_evans", "org_house_stategov", "committee_seat", "Chair (Majority)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_roae", "org_house_stategov", "committee_seat", "Minority Chair (R)", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_rep_harris", "org_house_approp", "committee_seat", "Chair", "LEGISLATIVE_COMMITTEES.html"),
    ("gov_sen_casey", "org_us_help", "committee_seat", "PA delegation member", "LEGISLATIVE_COMMITTEES.html"),
    # ---- committee jurisdiction over funders/regulators already in the graph ----
    ("org_sen_hhs", "reg_omhsas", "oversight_jurisdiction", "OMHSAS behavioral-health + CCBHC certification oversight", "LEGISLATIVE_COMMITTEES.html"),
    ("org_sen_hhs", "reg_ddap", "oversight_jurisdiction", "drug & alcohol licensing oversight", "LEGISLATIVE_COMMITTEES.html"),
    ("org_sen_hhs", "funder_samhsa", "oversight_jurisdiction", "CCBHC funding + MMJ-program intersection", "LEGISLATIVE_COMMITTEES.html"),
    ("org_house_humansvcs", "reg_omhsas", "oversight_jurisdiction", "mental health / SUD / county human services contracting", "LEGISLATIVE_COMMITTEES.html"),
    ("org_house_humansvcs", "reg_ddap", "oversight_jurisdiction", "drug & alcohol", "LEGISLATIVE_COMMITTEES.html"),
    ("org_house_health", "reg_omhsas", "oversight_jurisdiction", "behavioral-health facility licensing", "LEGISLATIVE_COMMITTEES.html"),
    ("org_sen_stategov", "reg_bco", "oversight_jurisdiction", "charitable registration oversight (BCO-10)", "LEGISLATIVE_COMMITTEES.html"),
    ("org_house_stategov", "reg_bco", "oversight_jurisdiction", "nonprofit corporate governance/transparency", "LEGISLATIVE_COMMITTEES.html"),
    ("org_sen_approp", "funder_county", "oversight_jurisdiction", "DHS budget / block grants", "LEGISLATIVE_COMMITTEES.html"),
    ("org_house_approp", "funder_samhsa", "oversight_jurisdiction", "state budget for CCBHC/behavioral health", "LEGISLATIVE_COMMITTEES.html"),
    ("org_us_help", "funder_samhsa", "oversight_jurisdiction", "SAMHSA authorization / CCBHC certification", "LEGISLATIVE_COMMITTEES.html"),
    # ---- county elected -> county human services (pass-through chain) ----
    ("gov_leinbach", "funder_county", "county_oversight", "Chairman, Berks County Commissioners — oversees county contracts w/ BCC (RTK)", "COMPLAINT_TODO_LIST.html / BERKS_RTK_REQUEST.txt"),
    ("funder_county", "org_coca", "funds", "county drug & alcohol pass-through to COCA ($3.3M to BCC)", "LEGISLATIVE_COMMITTEES.html"),
    # ---- court / litigation (public docket facts) ----
    ("court_morrison", "bcc", "litigant", "Morrison v. BCC — MDJ MJ-23103-CV-0000160-2025 (judgment for defendant 2025-09-24) + CP appeal filed 2025-10-15", "PACOURTS_CP_RECORDS_REQUEST.html"),
    ("court_prothonotary", "bcc", "record_keeper", "Berks CP civil index — BCC matters incl. No. 15-14644 (underlying 898 MDA 2017)", "PACOURTS_CP_RECORDS_REQUEST.html"),
    # ---- records channels hold the datapoints the open gaps seek ----
    ("rec_county_openrecords", "funder_county", "record_keeper", "holds county HS contracts/MOUs/payment records involving BCC (gap #13)", "BERKS_RTK_REQUEST.txt"),
    ("rec_county_openrecords", "bcc", "records_sought", "RTKL request items 1-5: contracts, MOUs, payments, audit docs, correspondence", "BERKS_RTK_REQUEST.txt"),
    ("rec_county_minutes", "funder_county", "records_venue", "commissioner approvals of county HS pass-through contracts w/ BCC", "INVESTIGATION_GAPS.html gap #13"),
    ("rec_coca_detail", "org_coca", "records_holder", "annual detail of the 5 COCA grants to BCC (cumulative $3.3M, 2019-2023)", "INVESTIGATION_GAPS.html gap #22"),
    # ---- approval-layer edges from COUNTY_APPROVAL_MINING / BERKS_OSF_ITEMIZATION (Sep 5, 2026) ----
    ("rec_consent_docket", "funder_county", "records_venue", "consent-agenda approval record for HS block-grant INE attestation + retained-earnings housing purchase + contract packet (Dec 4, 2025 voice vote)", "COUNTY_APPROVAL_MINING.html Layer 1"),
    ("rec_osf_ledger", "org_coca", "records_holder", "COCA-administered OSF disbursement ledger (Mar 2026) — BCC direct rows $172,220 budgeted; TASC $267,752 current cycle", "BERKS_OSF_ITEMIZATION.html"),
    ("org_coca", "funder_county", "funds", "county D&A pass-through to COCA; COCA board (SCA-appointed) allocates opioid-settlement funds — county bought BCC's housing with retained earnings (FY22-23 plan)", "COUNTY_APPROVAL_MINING.html Layer 2/3"),
    # ---- settlement-fund edge (OSF ledger cross-check vs Layer 3, Sep 5, 2026) ----
    ("org_coca", "bcc", "funds", "direct OSF settlement recipient — Mar 2026 ledger names BCC on 3 rows: Treatment/Counseling $100,000 (current cycle, shared row w/ Jail) + Contingency Mgmt $60,000 + Harm Reduction $12,220 (2023/24) = $172,220 budgeted, $0 expended on the 2023/24 rows as of ledger date", "BERKS_OSF_ITEMIZATION.html (COCA Mar 2026 PDF)"),
]


DATAPOINTS = [
    # (id, label, holds, gap, evidence, next_action)
    ("rec_county_openrecords",
     "County RTK records channel (Berks Open Records Officer)",
     "BCC county contracts/MOUs/payments; OMHSAS-audit variant",
     "#13/#18", "BERKS_RTK_REQUEST.txt",
     "FILE the RTK (draft complete Aug 2) — county + OMHSAS package"),
    ("rec_county_minutes",
     "County commissioners' public meeting minutes",
     "Contract-award / funding-approval record for county HS pass-through",
     "#13", "INVESTIGATION_GAPS.html gap #13",
     "Sub-contract allocations to BCC within the ~$16.2M county HS budget"),
    ("rec_coca_detail",
     "COCA grant-detail records",
     "Annual amounts of the 5 pass-through grants (cumulative $3.3M, 2019-2023)",
     "#22", "INVESTIGATION_GAPS.html gap #22",
     "Annual breakdown — COCA is a PA DHS/D&A pass-through; Goodman-Hinnershitz planning nexus"),
    ("rec_consent_docket",
     "Berks commissioners' consent-agenda approval docket",
     "Dec 4, 2025: block-grant INE attestation + 15-agreement packet + ~$5.5M procurement (voice vote, no per-item roll calls)",
     "#13", "COUNTY_APPROVAL_MINING.html Layer 1",
     "Pull per-resolution transcripts from Citizen Portal for HS pass-through items"),
    ("rec_osf_ledger",
     "Berks Opioid Settlement Fund expenditure ledger (COCA)",
     "Mar 2026: recipient-itemized disbursements; BCC direct rows $172,220 budgeted (Treatment/Counseling $100K + Contingency $60K + Harm Reduction $12,220)",
     "#22", "BERKS_OSF_ITEMIZATION.html",
     "Re-pull each ledger update to track BCC/TASC rows across cycles; cross-check vs COCA annual detail"),
]


def main() -> int:
    G, nodes = bsna.load_graph(COSMOS)
    base_ids = {n["id"] for n in nodes}
    idx = {n["id"]: i for i, n in enumerate(nodes)}

    added_nodes = []
    for nid, label, ntype, src in NEW_NODES:
        if nid in base_ids:
            continue
        G.add_node(len(G.nodes), id=nid, label=label, type=ntype, file=src)
        idx[nid] = len(nodes)
        nodes.append({"id": nid, "label": label, "type": ntype, "file": src})
        added_nodes.append(nid)

    added_edges = []
    for src, dst, etype, detail, ev in NEW_EDGES:
        s, t = idx.get(src), idx.get(dst)
        if s is None or t is None:
            print(f"  [skip] {src}->{dst}: missing node", file=sys.stderr)
            continue
        if s == t:
            continue
        if G.has_edge(s, t):
            G.edges[s, t].setdefault("etype", []).append(etype)
            G.edges[s, t].setdefault("detail", []).append(detail)
            G.edges[s, t].setdefault("evidence", []).append(ev)
        else:
            G.add_edge(s, t, etype=[etype], detail=[detail], evidence=[ev])
        added_edges.append({"source": src, "target": dst, "type": etype,
                            "detail": detail, "evidence": ev})

    # ---- SNA on the merged graph (reuse the v3 analyzer) ----
    results = bsna.analyze(G)

    # shortest paths: every political/court person -> BCC
    bcc_i = idx["bcc"]
    paths = {}
    for nid in idx:
        if nid.startswith(("gov_", "court_")):
            i = idx[nid]
            try:
                sp = nx.shortest_path(G, i, bcc_i)
                paths[nid] = {
                    "hops": len(sp) - 1,
                    "path": [G.nodes[j]["label"] for j in sp],
                }
            except nx.NetworkXNoPath:
                # No corpus-documented edge chain. For letter-recipient actors this
                # means the committee seat itself is undocumented in the corpus
                # (role exists in reality; the corpus simply has not recorded it),
                # not that no relationship of any kind exists.
                paths[nid] = {"hops": None, "path": [],
                              "note": ("letter-recipient / prose role only; no "
                                       "committee-seat edge documented in corpus")}

    out = {
        "generated": "2026-09-05",
        "method": ("Documented public-role expansion of the BCC network: political/"
                   "court/oversight actors added ONLY where the corpus documents the "
                   "role (committee seat / jurisdiction / litigation / county funding "
                   "channel). No inferred edges about private individuals."),
        "base_nodes": 48,
        "added_nodes": len(added_nodes),
        "base_edges": G.number_of_edges() - len(added_edges),
        "added_edges": len(added_edges),
        "total_nodes": len(nodes),
        "total_edges": G.number_of_edges(),
        "added_nodes_list": added_nodes,
        "added_edges_list": added_edges,
        "shortest_paths_to_bcc": paths,
        "sna": {
            "network": results.get("network", {}),
            "communities": results.get("communities", []),
        },
    }
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)

    render_html(G, nodes, paths, out)
    print(f"wrote {OUT_JSON} + {OUT_HTML} "
          f"({len(nodes)} nodes, {G.number_of_edges()} edges, "
          f"{len(added_nodes)} new actors, {len(added_edges)} new documented edges)")
    return 0


def render_html(G, nodes, paths, out):
    label_of = {n["id"]: n["label"] for n in nodes}
    # bridge stats
    hubs = sorted(G.degree(), key=lambda x: -x[1])[:8]
    hub_rows = "".join(
        f"<tr><td>{nodes[i]['label']}</td><td>{d}</td></tr>" for i, d in hubs)
    path_rows = ""
    for nid in sorted(paths, key=lambda x: (paths[x]["hops"] is None, paths[x]["hops"] or 0)):
        p = paths[nid]
        if p["hops"] is None:
            path_rows += (f"<tr><td>{label_of.get(nid,nid)}</td>"
                          f"<td>—</td><td>{p.get('note','no documented path')}</td></tr>")
        else:
            path_rows += (f"<tr><td>{label_of.get(nid,nid)}</td><td>{p['hops']}</td>"
                          f"<td>{' → '.join(p['path'])}</td></tr>")
    edge_rows = ""
    for e in out["added_edges_list"]:
        edge_rows += (f"<tr><td>{label_of.get(e['source'], e['source'])}</td>"
                      f"<td>{label_of.get(e['target'], e['target'])}</td>"
                      f"<td>{e['type']}</td><td>{e['detail']}</td>"
                      f"<td>{e['evidence']}</td></tr>")
    dp_rows = ""
    for _id, lbl, holds, gap, ev, action in DATAPOINTS:
        dp_rows += (f"<tr><td>{lbl}</td><td>{holds}</td><td>{gap}</td>"
                    f"<td>{ev}</td><td>{action}</td></tr>")

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BCC Government & Oversight Network Map — September 5, 2026</title>
<style>
  :root {{ --bg:#0d1117; --card:#161b22; --border:#30363d; --text:#c9d1d9;
          --h:#f0f6fc; --a:#58a6ff; --g:#3fb950; --y:#ffa657; --r:#f78166; --p:#d2a8ff; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
         background:var(--bg); color:var(--text); line-height:1.55; }}
  header {{ padding:28px 34px; border-bottom:1px solid var(--border);
           background:linear-gradient(180deg,#10151d,#0d1117); }}
  h1 {{ color:var(--h); font-size:1.4rem; }}
  .sub {{ color:#8b949e; font-size:.9rem; margin-top:6px; }}
  main {{ max-width:1150px; margin:0 auto; padding:26px 34px 60px; }}
  h2 {{ color:var(--h); font-size:1.1rem; margin:26px 0 12px; }}
  table {{ width:100%; border-collapse:collapse; font-size:.88rem; }}
  th,td {{ text-align:left; padding:8px 11px; border-bottom:1px solid var(--border);
          vertical-align:top; }}
  th {{ color:var(--a); font-size:.74rem; text-transform:uppercase;
       letter-spacing:.06em; background:#21262d; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin:14px 0; }}
  .box {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
         padding:14px 18px; }}
  .box .num {{ font-size:1.7rem; font-weight:800; color:var(--a); }}
  .box .lbl {{ font-size:.74rem; color:#8b949e; margin-top:4px; }}
  .note {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
          padding:12px 16px; margin:14px 0; color:#8b949e; font-size:.86rem; }}
  .ok {{ color:var(--g); }} .warn {{ color:var(--y); }}
</style></head><body>
<header><h1>BCC Government &amp; Oversight Network — Documented Expansion</h1>
<div class="sub">Political / court / state-oversight actors added to the 48-node BCC
network, edge-by-edge with corpus citations · generated 2026-09-05 ·
method: public-role nodes only (committee seats, jurisdiction, litigation, county
funding channel) — no inferred private-person edges</div></header>
<main>
<div class="grid">
  <div class="box"><div class="num">{out['total_nodes']}</div><div class="lbl">Total Nodes (48 base + {out['added_nodes']} new)</div></div>
  <div class="box"><div class="num">{out['total_edges']}</div><div class="lbl">Total Edges ({out['added_edges']} new documented)</div></div>
  <div class="box"><div class="num">{len([p for p in paths.values() if p['hops'] is not None])}</div><div class="lbl">Political/Court Actors With a Documented Path to BCC</div></div>
  <div class="box"><div class="num">{len([p for p in paths.values() if p['hops'] is None])}</div><div class="lbl">No Documented Path (recipients only)</div></div>
</div>
<div class="note"><b>Reading this map:</b> an actor having a <i>documented path</i> here
means their public role sits on an oversight/funding/litigation channel that reaches
BCC — it is a structural-influence map of <b>public-record relationships</b>, not an
allegation about any person's conduct. Members whose only connection is being a
complaint <i>recipient</i> (no committee/funding/litigation edge on file) are reported
as <b>no documented path</b>, which is the honest state until a public record shows
more.</div>

<h2>Network Bridges — highest-degree nodes (merged graph)</h2>
<table><thead><tr><th>Node</th><th>Degree</th></tr></thead><tbody>{hub_rows}</tbody></table>

<h2>Documented Paths: Political / Court Actors → BCC</h2>
<table><thead><tr><th>Actor</th><th>Hops</th><th>Documented path</th></tr></thead>
<tbody>{path_rows}</tbody></table>

<h2>New Documented Edges ({len(out['added_edges_list'])})</h2>
<table><thead><tr><th>Source</th><th>Target</th><th>Type</th><th>Detail</th><th>Evidence</th></tr></thead>
<tbody>{edge_rows}</tbody></table>

<h2>Open Records Datapoints the Map Tracks (gaps #13 / #18 / #22)</h2>
<table><thead><tr><th>Channel</th><th>Holds</th><th>Gap</th><th>Evidence</th><th>Next action</th></tr></thead>
<tbody>{dp_rows}</tbody></table>
</main></body></html>"""
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(html)


if __name__ == "__main__":
    raise SystemExit(main())
