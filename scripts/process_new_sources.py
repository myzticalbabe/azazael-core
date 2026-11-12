"""
process_new_sources.py — Process all new web sources and create enriched metadata entries.

Creates:
- New gap entries with full gap_metadata schema
- New lead entries with full lead_metadata schema  
- New edge entries with full d_grade_metadata schema
- Enriches all with respective metadata schemas
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, 'scripts')
from gap_metadata import enrich_gap_dispositions, compute_urgency
from lead_metadata import enrich_leads, compute_triage_priority
from d_grade_metadata import enrich_d_grade_edges, compute_priority

def load_existing_enriched():
    """Load existing enriched files to append to."""
    gaps = []
    leads = []
    edges = []
    
    # Load enriched gaps
    if os.path.exists('bcc_gap_index.enriched.json'):
        with open('bcc_gap_index.enriched.json') as f:
            gaps = json.load(f)
    
    # Load enriched leads
    if os.path.exists('scripts/bcc_lead_inference_evidence.enriched.json'):
        with open('scripts/bcc_lead_inference_evidence.enriched.json') as f:
            leads = json.load(f)
    
    # Load enriched edges
    if os.path.exists('bcc_person_edges.enriched.json'):
        with open('bcc_person_edges.enriched.json') as f:
            edges = json.load(f)
    
    return gaps, leads, edges

def create_gap_entries():
    """Create new gap entries from new sources."""
    today = datetime.now(timezone.utc).date().isoformat()
    
    new_gaps = [
        {
            "gap_id": "63",
            "title": "PA Superior Court opinion on BCC v Community Care (898 MDA 2017)",
            "basis": "PA Courts official PDF: BCC won declaratory judgment cancelling $81,704.47 in retractions; Community Care appealed 8 ways; Superior Court affirmed (Panella J., Nov 7 2018). Underlying docket: Berks CP Civil 15-14644.",
            "disposition": "EXEC-READY",
            "status": "EXEC_READY",
            "topic": "Legal",
            "records_action": "Request full trial transcript from Berks CCP (15-14644) via Pa.R.J.A. 509",
            "unblocker": "Prothonotary response to records request",
            "evidence_completeness": 0.9,
            "corroboration_count": 2,
            "last_advanced": datetime.now(timezone.utc).date().isoformat(),
            "leads": ["LEAD-01", "LEAD-02"],
            "notes": "PA Courts PDF downloaded; CourtListener HTML cross-reference available"
        },
        {
            "gap_id": "64",
            "title": "BCC Annual Report 2024-25 financials and client counts",
            "basis": "BCC Annual Report 2024-25 PDF: 251,868 mental health clients, 868 drug & alcohol, 919 dual diagnosis, 211 medication management. CEO Christine Axford, Board Chair Joe Plageman. NextGen EHR implementation March 2025. Mobile Medication Program pilot for high-risk SMI patients.",
            "disposition": "OPEN",
            "status": "OPEN",
            "topic": "Financial",
            "records_action": "Cross-reference client counts with FY2024 990 (13.9M revenue, 12.25M expenses); request audited financials",
            "unblocker": "Audited financial statements from BCC (gap #31)",
            "evidence_completeness": 0.7,
            "corroboration_count": 1,
            "last_advanced": datetime.now(timezone.utc).date().isoformat(),
            "leads": [],
            "notes": "Annual Report PDF extracted; cross-reference with FY2024 990 and T1 report"
        },
        {
            "gap_id": "65",
            "title": "HUD Continuum of Care grants to BCC (2 grants, $204,870 total)",
            "basis": "GovTribe/USAspending.gov: PA0703L3T062309 ($113,179, Nov 2024) + PA0159L3T062316 ($91,691, Apr 2024). Both HUD CoC program (CFDA 14.267) to Berks Counseling Center Inc. UEI: VC1XNFDJTV33.",
            "disposition": "EXEC-READY",
            "status": "EXEC_READY",
            "topic": "Funding",
            "records_action": "Verify grant terms in SAM.gov; cross-reference with BCC FY2024 990 revenue (13.9M total)",
            "unblocker": "SAM.gov grant details + BCC audited financials",
            "evidence_completeness": 0.85,
            "corroboration_count": 2,
            "last_advanced": datetime.now(timezone.utc).date().isoformat(),
            "leads": [],
            "notes": "GovTribe/USAspending.gov data; UEI VC1XNFDJTV33; cross-reference with COCA grants to BCC (724K TY2024)"
        },
        {
            "gap_id": "66",
            "title": "BCC NextGen EHR implementation and Mobile Medication Program",
            "basis": "Annual Report 2024-25: NextGen EHR go-live March 2, 2025. Mock Go Live Jan 22, 2025. Mobile Medication Program pilot for high-risk SMI patients (home-based psychiatric care). Cybersecurity upgrades, equipment upgrades.",
            "disposition": "OPEN (advanced)",
            "status": "OPEN",
            "topic": "Patient harm",
            "records_action": "Request NextGen implementation timeline and Mobile Medication Program protocol from BCC",
            "unblocker": "BCC operational records / next quarterly board meeting minutes",
            "evidence_completeness": 0.6,
            "corroboration_count": 1,
            "last_advanced": datetime.now(timezone.utc).date().isoformat(),
            "leads": [],
            "notes": "Annual Report mentions but no public technical specs; patient safety implications for Mobile Med Program"
        }
    ]
    return new_gaps

def create_lead_entries():
    """Create new lead entries from new sources."""
    today = datetime.now(timezone.utc).date().isoformat()
    
    new_leads = [
        {
            "id": "LEAD-09",
            "class": "STRENGTHENS-EXISTING",
            "status": "OPEN",
            "title": "PA Superior Court 898 MDA 2017 affirms BCC victory; retraction remedy affirmed",
            "basis": ["research/bcc_analysis/fwa_opinion_text.txt", "research/bcc_analysis/fwa_opinion_courtlistener.html"],
            "gap_refs": ["63"],
            "action": "Use court opinion as primary evidence for gap #63; cross-reference with trial court record (15-14644)",
            "integrity_note": "Official PA Courts PDF + CourtListener cross-reference; opinion is non-precedential but binding on parties",
            "corroboration_layers": ["pa_courts_pdf", "courtlistener_html", "docket_15_14644"],
            "records_action": "Request trial transcript (15-14644) for full factual record",
            "verification_method": "cross_layer_pass",
            "evidence_strength": 0.95,
            "created": datetime.now(timezone.utc).date().isoformat(),
            "updated": datetime.now(timezone.utc).date().isoformat()
        },
        {
            "id": "LEAD-10",
            "class": "STRENGTHENS-EXISTING",
            "status": "OPEN",
            "title": "HUD CoC grants ($204,870) to BCC not visible in FY2024 990 revenue breakdown",
            "basis": ["research/bcc_analysis/govtribe_grants.json"],
            "gap_refs": ["65"],
            "action": "Cross-reference HUD grants with BCC FY2024 990 Part VIII revenue (13.9M total); determine if reported as program service revenue or contributions",
            "integrity_note": "GovTribe data from USAspending.gov; UEI VC1XNFDJTV33 matches BCC EIN 23-2043478",
            "corroboration_layers": ["govtribe_usa_spending", "bcc_990_fy2024"],
            "records_action": "Request BCC audited financials (gap #31) for grant revenue classification",
            "verification_method": "cross_layer_pass",
            "evidence_strength": 0.9,
            "created": datetime.now(timezone.utc).date().isoformat(),
            "updated": datetime.now(timezone.utc).date().isoformat()
        },
        {
            "id": "LEAD-11",
            "class": "NEW",
            "status": "OPEN",
            "title": "Mobile Medication Program pilot for high-risk SMI patients — patient safety protocol unverified",
            "basis": ["research/bcc_analysis/BCC_Annual_Report_24-25.pdf (extracted text)"],
            "gap_refs": ["66"],
            "action": "Request Mobile Medication Program protocol, staffing ratios, and adverse event reporting from BCC",
            "integrity_note": "Annual Report mentions pilot for 'high-risk patients with serious mental illness' but no public protocol; patient safety implications significant",
            "corroboration_layers": ["bcc_annual_report_2025"],
            "records_action": "Request program protocol, staffing plan, adverse event log from BCC",
            "verification_method": "manual_review",
            "evidence_strength": 0.7,
            "created": datetime.now(timezone.utc).date().isoformat(),
            "updated": datetime.now(timezone.utc).date().isoformat()
        },
        {
            "id": "LEAD-12",
            "class": "STRENGTHENS-EXISTING",
            "status": "OPEN",
            "title": "BCC NextGen EHR implementation March 2025 — data migration and continuity risks",
            "basis": ["research/bcc_analysis/BCC_Annual_Report_24-25.pdf (extracted text)"],
            "gap_refs": ["64"],
            "action": "Request NextGen implementation plan, data migration validation report, and downtime contingency plan",
            "integrity_note": "Annual Report states March 2, 2025 go-live; Mock Go Live Jan 22, 2025; patient data migration is critical risk",
            "corroboration_layers": ["bcc_annual_report_2025"],
            "records_action": "Request NextGen implementation plan and data migration validation report",
            "verification_method": "manual_review",
            "evidence_strength": 0.75,
            "created": datetime.now(timezone.utc).date().isoformat(),
            "updated": datetime.now(timezone.utc).date().isoformat()
        }
    ]
    return new_leads

def create_edge_entries():
    """Create new edge entries from new sources."""
    new_edges = [
        {
            "source": "Berks Counseling Center",
            "target": "PA Superior Court",
            "type": "litigation",
            "detail": "BCC v Community Care Behavioral Health, 898 MDA 2017, affirmed Nov 7 2018 (Panella J.)",
            "evidence": "PA Courts PDF (898 MDA 2017), CourtListener HTML",
            "grade": "D",
            "source_type": "pdf",
            "retrieval_method": "headed_chrome",
            "confidence_score": 0.95,
            "retrieval_date": datetime.now(timezone.utc).date().isoformat(),
            "universe": "pacourts",
            "verifier": "manual",
            "tier_evidence": ["sha_verified", "court_opinion_pdf"],
            "status": "dossier",
            "notes": "Official PA Courts PDF + CourtListener cross-reference"
        },
        {
            "source": "Berks Counseling Center",
            "target": "U.S. Dept of Housing and Urban Development",
            "type": "grant_recipient",
            "detail": "HUD CoC grants PA0703L3T062309 ($113,179) + PA0159L3T062316 ($91,691); CFDA 14.267",
            "evidence": "GovTribe/USAspending.gov",
            "grade": "D",
            "source_type": "json",
            "retrieval_method": "api_call",
            "confidence_score": 0.9,
            "retrieval_date": datetime.now(timezone.utc).date().isoformat(),
            "universe": "federal_grants",
            "verifier": "api_call",
            "tier_evidence": ["sha_verified", "usa_spending_gov"],
            "status": "dossier",
            "notes": "UEI VC1XNFDJTV33 matches BCC EIN 23-2043478"
        },
        {
            "source": "Berks Counseling Center",
            "target": "NextGen Healthcare",
            "type": "vendor_relationship",
            "detail": "NextGen EHR implementation go-live March 2, 2025; Mock Go Live Jan 22, 2025",
            "evidence": "BCC Annual Report 2024-25",
            "grade": "D",
            "source_type": "pdf",
            "retrieval_method": "headed_chrome",
            "confidence_score": 0.8,
            "retrieval_date": datetime.now(timezone.utc).date().isoformat(),
            "universe": "vendor",
            "verifier": "manual",
            "tier_evidence": ["annual_report"],
            "status": "unsaved",
            "notes": "March 2, 2025 go-live; Mock Go Live Jan 22, 2025; data migration critical risk"
        },
        {
            "source": "Berks Counseling Center",
            "target": "Berks County MH/DD",
            "type": "contract_partner",
            "detail": "Annual Report lists as key collaborator; Mobile Medication Program pilot with Berks County MHDD",
            "evidence": "BCC Annual Report 2024-25",
            "grade": "D",
            "source_type": "pdf",
            "retrieval_method": "headed_chrome",
            "confidence_score": 0.85,
            "retrieval_date": datetime.now(timezone.utc).date().isoformat(),
            "universe": "government",
            "verifier": "manual",
            "tier_evidence": ["annual_report"],
            "status": "unsaved",
            "notes": "Mobile Medication Program pilot for high-risk SMI patients (home-based psychiatric care)"
        }
    ]
    return new_edges

def main():
    print("=== Processing New Sources ===\n")
    
    # Load existing enriched data
    gaps, leads, edges = load_existing_enriched()
    print(f"Loaded: {len(gaps)} gaps, {len(leads)} leads, {len(edges)} edges")
    
    # Create new entries
    new_gaps = create_gap_entries()
    new_leads = create_lead_entries()
    new_edges = create_edge_entries()
    
    print(f"Created: {len(new_gaps)} gaps, {len(new_leads)} leads, {len(new_edges)} edges")
    
    # Add to existing
    all_gaps = gaps + new_gaps
    all_leads = leads + new_leads
    all_edges = edges + new_edges
    
    # Enrich new gaps
    for g in new_gaps:
        g["urgency"] = compute_urgency(g)
    
    # Enrich new leads
    for l in new_leads:
        l["triage_priority"] = compute_triage_priority(l)
    
    # Enrich new edges
    for e in new_edges:
        e["priority"] = compute_priority(e)
    
    # Combine
    all_gaps = gaps + new_gaps
    all_leads = leads + new_leads
    all_edges = edges + new_edges
    
    # Save enriched combined
    with open('bcc_gap_index.enriched.json', 'w') as f:
        json.dump(all_gaps, f, indent=1, ensure_ascii=False)
    
    with open('scripts/bcc_lead_inference_evidence.enriched.json', 'w') as f:
        json.dump(all_leads, f, indent=1, ensure_ascii=False)
    
    with open('bcc_person_edges.enriched.json', 'w') as f:
        json.dump(all_edges, f, indent=1, ensure_ascii=False)
    
    print(f"\nSaved: {len(all_gaps)} gaps, {len(all_leads)} leads, {len(all_edges)} edges")
    
    # Run filters
    from gap_metadata import filter_for_action
    from lead_metadata import filter_for_verification
    from d_grade_metadata import filter_for_press_pull
    
    ready_gaps = filter_for_action(all_gaps)
    ready_leads = filter_for_verification(all_leads)
    ready_edges = filter_for_press_pull(all_edges)
    
    print(f"\n=== FILTER RESULTS ===")
    print(f"Gaps ready for action: {len(ready_gaps)}")
    for g in ready_gaps:
        print(f"  #{g['gap_id']}: {g['title'][:60]} (urgency={g['urgency']})")
    
    print(f"\nLeads ready for verification: {len(ready_leads)}")
    for l in ready_leads:
        print(f"  {l['id']}: {l['title'][:60]} (priority={l['triage_priority']})")
    
    print(f"\nEdges ready for press pull: {len(ready_edges)}")
    for e in ready_edges:
        print(f"  {e['source']} -> {e['target']} (priority={e['priority']})")

if __name__ == "__main__":
    main()