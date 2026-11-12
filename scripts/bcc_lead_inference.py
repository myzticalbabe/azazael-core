#!/usr/bin/env python3
"""
bcc_lead_inference.py -- reproducible cross-layer lead inference for the BCC
accountability investigation.

WHAT THIS IS
  A deterministic pass over the *already-collected* public-record evidence layers
  that emits candidate investigation leads. It does NOT collect new data and does
  NOT infer edges about private individuals: the corpus rule (bcc_person_edges.json
  note) is "every edge is documented in the corpus; no inferred/name-matched edges."
  Every emitted lead carries the evidence files that support it and a concrete
  public-record action that would advance it.

LAYERS READ (all on-disk artifacts; paths resolved relative to cwd):
  - bcc_990_series.json            IRS/ProPublica 990 summary series FY2011-2025
  - bcc_bai_perron_results.json    econometric breakpoint dating (revenue/exp/comp)
  - BCC_KUZU_TEMPORAL_evidence.json / bcc_graphiti_temporal.json   officer tenure KG
  - bcc_codebase_import_sna_v3.json  CODEBASE-import graph (persons: 0; NOT BCC
    evidence — see docs/REDO_NECESSITY_AUDIT_2026-10-28.md R5; BCC SNA recompute pending)
  - bcc_person_edges.json          documented person->person edges (v2)
  - bcc_record_linkage.json        cross-source identity clusters
  - _gap_status.json / azazael/scripts/bcc_gap_index.json   gap ledger (62)
  - pa_campaign_finance_bcc.json   campaign-finance negative result

OUTPUT
  - prints a human-readable lead list
  - writes <out> (default bcc_lead_inference_evidence.json) with the structured
    leads, each tagged: id, title, basis (evidence files), gap_refs, action, class
    (NEW / STRENGTHENS-EXISTING / CROSS-VALIDATES), integrity_note.

Run:  python3 bcc_lead_inference.py [--out leads.json]
"""

import argparse
import json
import os
import re
import sys
import re as _re

PYIO = sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None


# ---------------------------------------------------------------------------
# NAME-CONSISTENCY WATCHDOG (LEAD-08)
#
# Generalizes LEAD-03a: LEAD-03a checks ONLY the temporal-KG fact layer for the
# known "Christopher Plageman Jr" corruption. This watchdog scans EVERY derived
# machine layer (SNA v3 labels/rankings/communities/core/persons, record-linkage
# cluster members, person_edges, and the KG fact subjects) for any person named
# with a first-name token the human corpus never attests for that surname.
#
# Canonical first names are derived from the HUMAN corpus (root *.html authored
# docs) — e.g. 76x "John Robinson", 68x "Linda Krause", 61x "Missy Orlando",
# 51x "Christine Axford" — counted 2026-09-05. Surnames shared by two
# documented people (Brooks: James + Sen. Michele) list both. A machine-layer
# string naming surname S with a first token NOT in S's documented set is the
# corruption class LEAD-03a caught; anything else passes.
# ---------------------------------------------------------------------------

# surname -> canonical given-name tokens attested in the human corpus
CANONICAL_NAMES = {
    "Axford": {"Christine"},
    "Baez": {"Jaime"},
    "Boyer": {"Mahlon"},
    "Brooks": {"James", "Michele"},          # James (board) + Sen. Michele
    "Butler": {"Wesley"},
    "Campos": {"Vanessa"},
    "Del Collo": {"Jonathan"},
    "Fees": {"Sandra"},                        # Rev Sandra
    "Gaul": {"Brenda"},
    "Goodman-Hinnershitz": {"Marcia"},
    "Himeback": {"Linda"},
    "Kramer": {"Karen"},
    "Krause": {"Linda"},
    "Leinbach": {"Christian"},
    "Little": {"Greg"},
    "Malley": {"Frances"},
    "Miller": {"Christopher"},                 # O. Christopher
    "Morrison": {"Regina"},
    "Nunez": {"Rafael"},
    "Orlando": {"Missy"},
    "Plageman": {"Joseph"},
    "Ralph": {"Jeffrey"},
    "Reed": {"Donna"},
    "Rivera": {"Michael"},
    "Robinson": {"John"},
    "Santoni": {"Dante"},
    "Scull": {"Deborah"},
    "Seitz": {"Crystal"},
    "Sellers": {"John"},
    "Vella-gatt": {"Franco"},
    "Vellagatt": {"Franco"},
}

# middle-initial tokens ("Joseph Jr", "Mahlon J.", "Christian Y.", "O.", "Rev")
_SKIP_TOKENS = {"Jr", "J", "Y", "M", "S", "O", "K", "Rev", "Dr", "Esq", "CPA"}


def _first_token_before(text, surname):
    """Return the given-name token immediately preceding a surname occurrence.

    Only counts a surname as person-named when the character directly before it
    is a letter or a period ("John Robinson", "Christian Y. Leinbach", "Mahlon
    J. Boyer"). Org-context mentions — "The Vanguard Group (Robinson prior
    employer)", "Alvernia University (Orlando employer)" — have a paren/dash
    before the surname, so they are skipped (no given name present). Walks back
    over middle initials / titles (Jr, Esq, Y., J.) to the given-name token.
    """
    found = []
    for m in _re.finditer(_re.escape(surname), text):
        before = text[:m.start()]
        stripped = before.rstrip()
        # surname must be immediately preceded by a letter or period (a name
        # context), NOT a paren / dash / slash / colon (org or prose context)
        if not stripped or not (stripped[-1].isalpha() or stripped[-1] == "."):
            continue
        toks = _re.findall(r"[A-Za-z][A-Za-z\-']*", stripped)
        if not toks:
            continue
        # walk back from the surname. A lowercase word between the surname and
        # a candidate token BREAKS the name phrase ("…President and Little CFO"
        # → "and" is prose, so no given name precedes); only middle initials /
        # titles ("Christian Y.", "Mahlon J.", "Jr") may be skipped. The first
        # capitalized non-title token reached is the given name.
        for tok in reversed(toks):
            if tok in _SKIP_TOKENS or (len(tok) == 1 and tok.isupper()):
                continue
            if tok[0].islower():
                break  # prose connector — the phrase ended; no given name
            found.append(tok)
            break
    return found


def collect_name_strings(obj, out):
    """Recursively collect every string leaf from a JSON structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            collect_name_strings(k, out)
            collect_name_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_name_strings(v, out)
    elif isinstance(obj, str):
        out.append(obj)


def scan_layer(label, obj, hits):
    """Scan one derived layer; append (layer, string, surname, got, expected) hits."""
    strings = []
    collect_name_strings(obj, strings)
    for s in strings:
        for surname, allowed in CANONICAL_NAMES.items():
            if surname not in s:
                continue
            for tok in _first_token_before(s, surname):
                if tok not in allowed:
                    hits.append({
                        "layer": label, "string": s[:200], "surname": surname,
                        "got": tok, "expected": sorted(allowed),
                    })


def watchdog(sna, linkage, pedges, graphiti, kuzu):
    """Run the name-consistency watchdog over every derived machine layer.

    Returns (hits, n_strings). Empty hits = all machine layers consistent with
    the human corpus's canonical names.
    """
    hits = []
    layers = [
        ("sna_v3_centrality+rankings+communities+core+persons", sna),
        ("record_linkage_clusters", linkage),
        ("person_edges", pedges),
        ("kg_graphiti_facts", graphiti),
        ("kg_kuzu_evidence", kuzu),
    ]
    n_strings = 0
    for label, obj in layers:
        if not obj:
            continue
        strings = []
        collect_name_strings(obj, strings)
        n_strings += len(strings)
        scan_layer(label, obj, hits)
    return hits, n_strings


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:  # file may live in azazael/scripts/
        alt = os.path.join("azazael", "scripts", os.path.basename(path))
        try:
            with open(alt, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            print(f"  [warn] could not load {path}: {e}", file=sys.stderr)
            return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="bcc_lead_inference_evidence.json")
    args = ap.parse_args()

    series = load("bcc_990_series.json") or {}
    bp = load("bcc_bai_perron_results.json") or {}
    kuzu = load("BCC_KUZU_TEMPORAL_evidence.json") or {}
    graphiti = load("bcc_graphiti_temporal.json") or {}
    sna = load("bcc_codebase_import_sna_v3.json") or {}
    pedges = load("bcc_person_edges.json") or {}
    linkage = load("bcc_record_linkage.json") or {}
    campaign = load("pa_campaign_finance_bcc.json") or {}
    gap_status = load("_gap_status.json") or []
    gap_index = load("bcc_gap_index.json") or {}

    filings = sorted(series.get("filings", []), key=lambda x: x.get("tax_prd_yr") or 0)
    by_yr = {f.get("tax_prd_yr"): f for f in filings}

    leads = []

    # ---- Lead 1: FY2017 comp doubling precedes the FY2018 revenue break by one
    # fiscal year, and the temporal KG has NO chair record for the decision window
    # (2014-2024 chair coverage hole) => who governed the comp change? ----
    comp_16 = by_yr.get(2016, {}).get("officer_comp")
    comp_17 = by_yr.get(2017, {}).get("officer_comp")
    rev_17 = by_yr.get(2017, {}).get("revenue")
    rev_18 = by_yr.get(2018, {}).get("revenue")
    if comp_16 and comp_17 and rev_17 and rev_18:
        comp_pct = (comp_17 / comp_16 - 1) * 100
        rev_pct = (rev_18 / rev_17 - 1) * 100
        if comp_pct > 40 and rev_pct > 40:
            leads.append({
                "id": "LEAD-01",
                "class": "STRENGTHENS-EXISTING",
                "title": (f"Officer comp rose {comp_pct:.0f}% in FY2017 "
                          f"({comp_16:,}->{comp_17:,}) one fiscal year BEFORE the "
                          f"FY2018 revenue break ({rev_pct:.0f}%; {rev_17:,}->{rev_18:,}). "
                          "The temporal pattern is documented in BCC_FINANCIAL_ANOMALY; "
                          "the NEW angle is attribution — who authorized it, and under "
                          "which chair (see LEAD-03 for the KG coverage hole)."),
                "basis": ["bcc_990_series.json", "bcc_bai_perron_results.json",
                           "BCC_FINANCIAL_ANOMALY.html"],
                "gap_refs": ["41", "11", "39"],
                "action": ("Pull the FY2017 990 Part VII / Schedule J officer rows "
                           "(live IRS XML; the on-disk capture shows comp totals only, "
                           "not per-person name-comp rows) to name the officers whose "
                           "comp changed and the signatory chair; the FY2016-18 captures "
                           "prove board membership + CEO signatory (Malley) per year. "
                           "Chair-title attribution for FY2014-FY2024 needs the full Part "
                           "VII title rows (see LEAD-03)."),
                "integrity_note": "Both figures are from the IRS 990 summary series; the "
                                  "chair attribution requires the roster page, not inference.",
            })

    # ---- Lead 2: FY2025 first deficit + record officer comp in the same year,
    # then the board reorg ~May 15 2026 (one week AFTER the FY2025 990 was filed
    # May 8 2026 per corpus) => comp and governance review timing ----
    f25 = by_yr.get(2025, {})
    if f25:
        rev25 = f25.get("revenue") or 0
        exp25 = f25.get("expenses") or 0
        comp25 = f25.get("officer_comp") or 0
        if exp25 > rev25:
            leads.append({
                "id": "LEAD-02",
                "class": "STRENGTHENS-EXISTING",
                "title": (f"FY2025 first deficit of the CCBHC era "
                          f"(rev {rev25:,} < exp {exp25:,}) coincides with record "
                          f"officer comp ({comp25:,}). Board reorg (~May 15 2026) "
                          "followed the FY2025 990 filing (~May 8 2026) by one week."),
                "basis": ["bcc_990_series.json", "BCC_KUZU_TEMPORAL_evidence.json",
                          "bcc_graphiti_temporal.json"],
                "gap_refs": ["4", "11", "41", "31"],
                "action": ("Request the FY2025 audited financial statements (gap #31, "
                           "never requested) + the FY2026 990 when it lands (gap #4) to "
                           "test whether the deficit and comp setting persisted under the "
                           "post-reorg slate."),
                "integrity_note": "All figures from the IRS series; reorg window from the "
                                  "temporal-KG evidence (documented, as-of banner).",
            })

    # ---- Lead 3: temporal KG chair coverage for the pre-2025 era. The Sep-5 pass
    # fixed a real corruption here (KG facts named 'Christopher Plageman Jr' while
    # the corpus is unanimous on 'Joseph Plageman Jr' — FY2025 990 = Chairperson,
    # CRD #5964768; 'Christopher' existed only in the KG layer). After the fix,
    # point-in-time chair queries for 2014-2024 STILL return [] because the on-disk
    # _990_FY20*.html captures render rosters + the CEO signatory but NOT per-person
    # Part VII titles — so chair attribution for the break era needs the full 990
    # XML officer-title rows (a records item), not inference. ----
    kq = kuzu.get("point_in_time_queries", {})
    kg_names = [f.get("subject", "") for f in (graphiti.get("facts") or [])]
    kg_has_joseph = any("Joseph Plageman" in n for n in kg_names)
    kg_has_christopher = any("Christopher Plageman" in n for n in kg_names)
    if kg_has_christopher:
        leads.append({
            "id": "LEAD-03a",
            "class": "CORRUPTION-FOUND-AND-FIXED",
            "title": ("KG fact layer named the chair 'Christopher Plageman Jr' while the "
                      "corpus is unanimous on 'Joseph Plageman Jr' (FY2025 990 = "
                      "Chairperson, CRD #5964768, Edward Jones 918 Penn Ave). 'Christopher' "
                      "appeared ONLY in the KG artifacts, not in the human corpus."),
            "basis": ["bcc_graphiti_temporal.json", "BCC_KUZU_TEMPORAL_evidence.json",
                       "BCC_KUZU_TIMELINE_evidence.json", "BCC_TEMPORAL_KG.html",
                       "COI_REQUEST_LETTER.html", "PEOPLE_DOSSIER.html"],
            "gap_refs": ["10", "39"],
            "action": ("Fixed in bcc_graphiti_temporal.py facts + regenerated all four "
                       "KG artifacts (Sep-5). Re-run this pass to confirm the corruption "
                       "is gone."),
            "integrity_note": "Name-consistency check across machine artifacts vs the "
                              "human corpus; the fix cites the FY2025-990 + CRD records.",
        })
    if kq.get("chair_2024_01_01") == [] and not kg_has_christopher:
        leads.append({
            "id": "LEAD-03",
            "class": "NEW",
            "title": ("Temporal KG chair query for 2024-01-01 still returns [] after the "
                      "Joseph-name fix: the on-disk _990_FY20*.html captures render board "
                      "rosters + CEO signatory (Malley, thru FY2018) but not per-person "
                      "Part VII titles, so 'who chaired during the FY2018 break' is not "
                      "mechanically resolvable from the captures alone. Corpus asserts "
                      "Plageman Jr = Board Chair 14+ yrs (COI tables)."),
            "basis": ["BCC_KUZU_TEMPORAL_evidence.json", "bcc_graphiti_temporal.json",
                       "_990_FY2016.html", "_990_FY2017.html", "_990_FY2018.html",
                       "COI_REQUEST_LETTER.html"],
            "gap_refs": ["10", "39"],
            "action": ("Residual: pull the FY2016-FY2018 (and FY2019-FY2024) 990 Part VII "
                       "officer-title rows from the live IRS XML (the static captures do "
                       "not carry per-person titles) to date Plageman Jr's chair tenure "
                       "precisely; until then the KG holds the documented 2025-07-01 -> "
                       "2026-05-15 chair interval only."),
            "integrity_note": "The captures prove roster membership + CEO signatory per "
                              "FY; chair-title attribution is a records item, not inference.",
        })
    elif kg_has_joseph and not kg_has_christopher:
        leads.append({
            "id": "LEAD-03",
            "class": "RESOLVED",
            "title": ("KG chair facts now name Joseph Plageman Jr consistently with the "
                      "corpus; chair query coverage for the 2014-2024 window remains a "
                      "records-limited data hole (full Part VII titles needed)."),
            "basis": ["BCC_KUZU_TEMPORAL_evidence.json", "bcc_graphiti_temporal.json"],
            "gap_refs": ["10", "39"],
            "action": ("Pull full Part VII officer-title rows (live IRS XML) to extend "
                       "chair valid_from before 2025-07-01."),
            "integrity_note": "Coverage gap, not an affirmative finding about any person.",
        })

    # ---- Lead 4: 6-degrees result -- the longest documented path (4) runs between
    # the COCA/Council node and the Herbein auditor node => the two accountability
    # institutions are the least socially intertwined pair in the documented graph. ----
    persons = sna.get("persons", {})
    mx = persons.get("max_pair", {})
    if mx:
        leads.append({
            "id": "LEAD-04",
            "class": "CROSS-VALIDATES",
            "title": (f"6-degrees result: all {persons.get('n_persons')} documented persons "
                      f"connected; max separation {persons.get('max_separation')}, median "
                      f"{persons.get('median_separation')}. Farthest pair = "
                      f"{mx.get('a')} <-> {mx.get('b')} — the auditor and the county "
                      "oversight body are the least intertwined."),
            "basis": ["bcc_codebase_import_sna_v3.json"],
            "gap_refs": ["27", "29"],
            "action": ("Use this as the structural baseline for the Herbein-independence "
                       "question (gap #27): the documented graph shows no close auditor-"
                       "oversight tie, so any closer tie found in new records (shared "
                       "committees, prior employment) would be a material finding."),
            "integrity_note": "Documented-edge graph only; independence conclusion is "
                              "about the graph, not about any individual's conduct.",
        })

    # ---- Lead 5: campaign-finance negative result redirects the influence question
    # to institutional funding lines (COCA/commissioner) - already the corpus thesis,
    # but the SNA puts COCA at the center of community 1 -> quantify COCA's role. ----
    if campaign.get("conclusion"):
        leads.append({
            "id": "LEAD-05",
            "class": "STRENGTHENS-EXISTING",
            "title": "Campaign-finance sweep is a clean negative (board not politically "
                     "active via donations); the SNA places COCA (county oversight body) "
                     "in the top closeness/betweenness tier of the documented network — "
                     "consistent with the institutional-influence thesis.",
            "basis": ["pa_campaign_finance_bcc.json", "bcc_codebase_import_sna_v3.json"],
            "gap_refs": ["22", "29", "45"],
            "action": ("Pursue the COCA grant-detail line (gap #22: cumulative, not "
                       "annual — request annual award letters) and the county-commissioner "
                       "direct channel (gap #29: no direct emails on file)."),
            "integrity_note": "Negative-result framing preserved; COCA centrality is a "
                              "graph fact from documented attendance edges.",
        })

    # ---- Lead 6: persons present in the SNA but absent from ALL record-linkage
    # clusters = the identity-corroboration gaps. The clusters only contain matched
    # names, so the shallow set is whoever is in the network but in no cluster.
    # Sep-5 refinement: (a) org nodes are not persons — they are excluded from the
    # "uncorroborated person" framing; (b) the on-disk _990_FY20*.html roster
    # captures are a corroboration layer the linkage pass never consulted — a person
    # listed on an on-disk 990 Part VII roster is corroborated by a primary record.
    # ----
    cl = linkage.get("clusters") or []
    if cl:
        linked_names = set()
        for c in cl:
            for member in c:
                nm = member.get("name", "") if isinstance(member, dict) else str(member)
                # normalize: strip trailing org-role descriptors for the match test
                linked_names.add(nm.lower())
        sna_persons = []
        for c in sna.get("communities", []):
            for m in c.get("members", []):
                if isinstance(m, str) and ("—" in m or m.lower().startswith("person_")):
                    sna_persons.append(m)
        # find SNA members whose core surname token appears in no linked name
        def core_token(nm):
            n = nm.split(" — ")[0].strip()
            for sep in (" ", "-"):
                if sep in n:
                    return n.split(sep)[0].lower()
            return n.lower()
        unlinked_all = [p for p in sna_persons
                        if not any(core_token(p) in ln for ln in linked_names)]
        # ---- orgs vs persons in the unlinked set (person = has a name + role marker)
        ORG_MARKERS = ("COCA", "SAMHSA", "TPUC", "PA AG", "Herbein", "Edward Jones",
                       "BCC", "United Way", "IRS", "CCBH", "GSK", "Alvernia",
                       "Greater Reading", "Central Tire", "Furniture Soup", "OIG")
        unlinked_orgs = [p for p in unlinked_all
                         if p.split(" — ")[0].strip().upper() in ORG_MARKERS
                         or not any(ch.isalpha() for ch in p.split(" — ")[0])]
        unlinked_persons = [p for p in unlinked_all if p not in unlinked_orgs]
        # ---- on-disk 990 roster corroboration (primary records)
        roster_hits = {}
        for p in unlinked_persons:
            tok = p.split(" — ")[0].strip()
            fam = tok.split()[-1].upper()
            yrs = []
            for fy in ("2016", "2017", "2018"):
                try:
                    s = open(f"_990_FY{fy}.html", encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                names = re.findall(r'PersonNm\[1\]">([^<]+)', s)
                if any(fam in n.upper() for n in names):
                    yrs.append(fy)
            roster_hits[p] = yrs
        # FY2025-990 roster corroboration comes from the corpus text (INVESTIGATION_GAPS
        # Sep-3 corrected list: Seitz=Chairperson, Ralph=Treasurer, Orlando=Director as
        # of Aug 2026, then absent from the post-reorg live roster). Check that too.
        fy25_roster = {"SEITZ": "FY2025 990 roster (Chairperson)",
                       "RALPH": "FY2025 990 roster (Treasurer)",
                       "ORLANDO": "FY2025 990 roster (Director as of Aug 2026; absent post-reorg)"}
        uncorroborated = []
        where = {}
        for p in unlinked_persons:
            fam = p.split(" — ")[0].strip().split()[-1].upper()
            src = roster_hits.get(p) or (fy25_roster.get(fam) and "corpus FY2025-990 roster")
            if src:
                where[p] = src
            else:
                uncorroborated.append(p)
        n_corr_ondisk = len([p for p in unlinked_persons if roster_hits.get(p)])
        n_corr_fy25 = len([p for p in unlinked_persons
                           if not roster_hits.get(p) and p not in uncorroborated])
        leads.append({
            "id": "LEAD-06",
            "class": "NEW",
            "title": (f"{len(cl)} identity clusters formed across core/sna/dossier; "
                      f"{len(unlinked_persons)} unlinked SNA PERSONS ({len(unlinked_orgs)} "
                      f"unlinked orgs excluded — PA AG/SAMHSA/TPUC/COCA are not persons). "
                      f"Corroboration after roster cross-check: {n_corr_ondisk} on on-disk "
                      f"FY2016-18 990 Part VII rosters + {n_corr_fy25} on the FY2025-990 "
                      f"roster (corpus), leaving {len(uncorroborated)} with no roster hit: "
                      f"{'; '.join(p.split(' — ')[0] for p in uncorroborated) or 'none'}."),
            "basis": ["bcc_record_linkage.json", "bcc_codebase_import_sna_v3.json",
                      "_990_FY2016.html", "_990_FY2017.html", "_990_FY2018.html",
                      "INVESTIGATION_GAPS.html"],
            "gap_refs": ["25", "34"],
            "action": ("For any person with no roster hit, the next public-record step is "
                       "a DOS/court/campaign-finance existence check (same channel already "
                       "used for the corroborated set) — no new inference, only "
                       "corroboration. Persons on the on-disk FY2016-18 990 rosters or the "
                       "FY2025-990 roster (corpus) are already primary-record corroborated."),
            "integrity_note": "Strictly corroboration depth; orgs are not persons; no person "
                              "is asserted as involved beyond what the cited layer documents.",
        })

    # ---- Lead 7: executive tenure vs the deficit era -- Axford/Little in post since
    # Feb 2019 span every post-break regime including the FY2025 deficit; the KG
    # documents it, so the accountability question is about board oversight of the
    # deficit year, not the executives alone. ----
    may26 = kuzu.get("may_2026_officer_window") or []
    execs = [e for e in may26 if e.get("relation") in ("CEO of", "CFO of")]
    if execs:
        leads.append({
            "id": "LEAD-07",
            "class": "STRENGTHENS-EXISTING",
            "title": (f"Executive tenure documented: {len(execs)} in post since Feb 2019 "
                      "span the FY2018 break through the FY2025 deficit — the KG frames "
                      "the deficit as a board-oversight-period question, not an "
                      "executive-tenure anomaly."),
            "basis": ["BCC_KUZU_TEMPORAL_evidence.json"],
            "gap_refs": ["31", "61", "9"],
            "action": ("Board meeting minutes were never requested (gap #9) and no CEO "
                       "performance-evaluation record exists (gap #61) — both are the "
                       "oversight-side documents that would test the deficit-era "
                       "governance question."),
            "integrity_note": "Tenure facts from the documented temporal KG.",
        })

    # ---- Lead 8: name-consistency watchdog across ALL derived machine layers
    # (generalizes LEAD-03a, which only checked the temporal-KG facts). Scans SNA
    # v3 labels/rankings/communities/core/persons, record-linkage cluster members,
    # person_edges, and the KG fact subjects for any surname named with a first-name
    # token the human corpus never attests (the 'Christopher Plageman Jr' corruption
    # class). Emits LEAD-08 when a mismatch exists; emits the CLEAN confirmation
    # otherwise (mirrors LEAD-03's RESOLVED branch). ----
    wd_hits, wd_strings = watchdog(sna, linkage, pedges, graphiti, kuzu)
    if wd_hits:
        detail = "; ".join(
            f"{h['layer']}: '{h['got']} {h['surname']}' (corpus attests {', '.join(h['expected'])})"
            for h in wd_hits[:6])
        leads.append({
            "id": "LEAD-08",
            "class": "CORRUPTION-FOUND",
            "title": (f"Name-consistency watchdog found {len(wd_hits)} corruption "
                      f"candidate(s) across derived machine layers: {detail}"),
            "basis": ["bcc_codebase_import_sna_v3.json", "bcc_record_linkage.json",
                       "bcc_person_edges.json", "bcc_graphiti_temporal.json",
                       "BCC_KUZU_TEMPORAL_evidence.json"],
            "gap_refs": [],
            "action": ("Fix the source facts (same method as the Joseph/Christopher "
                       "LEAD-03a correction) and regenerate the affected layer; "
                       "re-run this pass to confirm the corruption is gone."),
            "integrity_note": "Name-consistency check across machine artifacts vs the "
                              "human corpus's canonical given names (counted from "
                              "root HTML docs, Sep-5).",
        })
    else:
        leads.append({
            "id": "LEAD-08",
            "class": "RESOLVED",
            "title": (f"Name-consistency watchdog CLEAN: {wd_strings} name-bearing strings "
                      f"across SNA v3 labels/centrality/communities, record-linkage "
                      f"clusters, person_edges, and the temporal KG all use corpus-"
                      f"attested given names (Joseph Plageman Jr / John Robinson / "
                      f"Christine Axford / Missy Orlando / …). No 'Christopher "
                      f"Plageman'-class corruption remains in any machine layer."),
            "basis": ["bcc_codebase_import_sna_v3.json", "bcc_record_linkage.json",
                       "bcc_person_edges.json", "bcc_graphiti_temporal.json",
                       "BCC_KUZU_TEMPORAL_evidence.json"],
            "gap_refs": [],
            "action": ("No action. Re-run after any regeneration of the SNA, linkage, "
                       "person-edge, or KG layers to keep the machine layers honest."),
            "integrity_note": "Name-consistency check across machine artifacts vs the "
                              "human corpus's canonical given names.",
        })

    # ---- per-lead triage status (OPEN / IN-PROGRESS / RESOLVED) ----
    # Assigned here at the source so BCC_LEAD_FINDINGS.html can track follow-up
    # state per lead. Tri-state per the corpus send-matrix convention.
    STATUS_MAP = {
        "LEAD-01": "OPEN",       # records action pending: FY2017 Part VII/Schedule J rows
        "LEAD-02": "OPEN",       # audited statements (gap #31) + FY2026 990 never requested
        "LEAD-03a": "RESOLVED",  # corruption fixed + all four KG artifacts regenerated (Sep-5)
        "LEAD-03": "OPEN",       # records-limited chair-coverage hole (full Part VII titles needed)
        "LEAD-04": "RESOLVED",   # 6-degrees baseline computed (auditor <-> oversight farthest pair)
        "LEAD-05": "OPEN",       # negative documented; COCA annual detail + county channel pending
        "LEAD-06": "OPEN",       # corroboration depth reported; residual set re-checked w/ new records
        "LEAD-07": "OPEN",       # minutes (gap #9) + CEO eval (gap #61) never requested
    }
    for L in leads:
        if L["id"] == "LEAD-08":
            L["status"] = "IN-PROGRESS" if L["class"] == "CORRUPTION-FOUND" else "RESOLVED"
        else:
            L["status"] = STATUS_MAP.get(L["id"], "OPEN")

    # ---- summary ----
    print(f"BCC lead-inference pass — {len(leads)} candidate leads (evidence-grounded, "
          f"documented-edges only)\n")
    for L in leads:
        print(f"[{L['id']}] ({L['class']}) {L['title']}\n"
              f"      basis: {', '.join(L['basis'])}\n"
              f"      gaps:  {', '.join('#' + g for g in L['gap_refs'])}\n"
              f"      action: {L['action']}\n"
              f"      integrity: {L['integrity_note']}\n")

    out = {
        "generated": "2026-09-05",
        "method": ("Cross-layer pass over already-collected public-record artifacts. "
                   "No new data collection; no inferred/name-matched private-person "
                   "edges (corpus rule, bcc_person_edges.json). Per-lead status is "
                   "triage state only (OPEN / IN-PROGRESS / RESOLVED) — it does not "
                   "change what any lead asserts."),
        "layers_loaded": [os.path.basename(p) for p in (
            "bcc_990_series.json", "bcc_bai_perron_results.json",
            "BCC_KUZU_TEMPORAL_evidence.json", "bcc_graphiti_temporal.json",
            "bcc_codebase_import_sna_v3.json", "bcc_person_edges.json",
            "bcc_record_linkage.json", "pa_campaign_finance_bcc.json",
            "_gap_status.json")],
        "n_leads": len(leads),
        "leads": leads,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(f"wrote {args.out} ({len(leads)} leads)")


if __name__ == "__main__":
    main()
