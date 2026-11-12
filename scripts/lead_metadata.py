"""
lead_metadata.py — Lead triage enrichment with structured metadata.

Replaces vague "class" + "status" strings with fields that drive
concrete records actions and cross-validation.

Schema (per lead):
  id: str                      # LEAD-XX
  class: str                   # STRENGTHENS-EXISTING | CROSS-VALIDATES | NEW | RESOLVED | CORRUPTION-FOUND-AND-FIXED | CORRUPTION-FOUND
  status: str                  # OPEN | IN-PROGRESS | RESOLVED
  title: str
  basis: list[str]             # evidence files
  gap_refs: list[str]          # linked gap IDs
  action: str                  # concrete records request
  integrity_note: str
  corroboration_layers: list[str]  # which layers corroborate (e.g., ["990_series", "bai_perron", "temporal_KG"])
  records_action: str          # specific records to request
  verification_method: str     # cross_layer_pass | temporal_kg | record_linkage | manual_review
  evidence_strength: float     # 0.0-1.0 (confidence in lead assertion)
  created: str                 # ISO date
  updated: str                 # ISO date

Functions:
  enrich_leads()               # reads leads, adds metadata, writes enriched output
  compute_triage_priority()    # priority 1-5 from class + evidence + actionability
  filter_for_verification()    # returns leads ready for cross-validation
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEAD_CLASSES = {
    "STRENGTHENS-EXISTING": "Adds weight to existing documented finding",
    "CROSS-VALIDATES": "Confirmed by independent evidence layer",
    "NEW": "Previously undocumented finding",
    "RESOLVED": "Question answered; no further action",
    "CORRUPTION-FOUND-AND-FIXED": "Machine-layer corruption detected and corrected",
    "CORRUPTION-FOUND": "Machine-layer corruption detected; fix pending",
}

LEAD_STATUSES = {"OPEN", "IN-PROGRESS", "RESOLVED"}

VERIFICATION_METHODS = {
    "cross_layer_pass": "Cross-layer evidence pass (default)",
    "temporal_kg": "Temporal KG point-in-time query",
    "record_linkage": "Probabilistic record linkage (splink/RapidFuzz)",
    "manual_review": "Human adjudication of primary sources",
}


def compute_triage_priority(lead: dict) -> int:
    """
    Compute triage priority 1-5 for lead.
    Higher = more urgent for verification/action.
    
    Factors:
    - class severity: 40%
    - evidence_strength: 30%
    - actionability: 20%
    - gap linkage: 10%
    """
    lead_class = lead.get("class", "NEW")
    evidence = lead.get("evidence_strength", 0.5)
    has_action = bool(lead.get("records_action", ""))
    gap_count = len(lead.get("gap_refs", []))
    
    # Class severity (0-2)
    class_scores = {
        "CORRUPTION-FOUND": 2,
        "CORRUPTION-FOUND-AND-FIXED": 2,
        "NEW": 1,
        "STRENGTHENS-EXISTING": 1,
        "CROSS-VALIDATES": 0,
        "RESOLVED": 0,
    }
    class_score = class_scores.get(lead_class, 1)
    
    # Evidence strength (0-2)
    evidence_score = min(2, int(evidence * 4))
    
    # Actionability (0-1)
    action_score = 1 if has_action else 0
    
    # Gap linkage (0-1)
    gap_score = min(1, gap_count)
    
    total = class_score + evidence_score + action_score + gap_score
    return min(5, max(1, total + 1))


def filter_for_verification(leads: list[dict]) -> list[dict]:
    """Return leads ready for cross-validation.
    
    Criteria:
    - triage_priority >= 3
    - class in {STRENGTHENS-EXISTING, CROSS-VALIDATES, NEW}
    - has basis evidence
    - status != RESOLVED
    """
    ready = []
    for l in leads:
        if l.get("status") == "RESOLVED":
            continue
        if l.get("triage_priority", 0) < 3:
            continue
        if l.get("class") not in ("STRENGTHENS-EXISTING", "CROSS-VALIDATES", "NEW"):
            continue
        if not l.get("basis"):
            continue
        ready.append(l)
    return ready


def enrich_leads(
    input_path: str,
    output_path: str = None,
    default_verification: str = "cross_layer_pass",
) -> list[dict]:
    """
    Read leads from input_path (bcc_lead_inference output JSON),
    enrich with metadata, write enriched leads to output_path.
    """
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)
    
    if isinstance(data, dict) and "leads" in data:
        leads = data["leads"]
    elif isinstance(data, list):
        leads = data
    else:
        raise ValueError("Expected leads list or dict with 'leads' key")
    
    enriched = []
    for lead in leads:
        if not isinstance(lead, dict):
            continue
        
        enriched_lead = {**lead}
        
        # Add metadata fields with defaults
        enriched_lead.setdefault("corroboration_layers", [])
        enriched_lead.setdefault("records_action", "")
        enriched_lead.setdefault("verification_method", default_verification)
        enriched_lead.setdefault("evidence_strength", 0.7)
        enriched_lead.setdefault("created", datetime.now(timezone.utc).date().isoformat())
        enriched_lead.setdefault("updated", datetime.now(timezone.utc).date().isoformat())
        
        # Compute triage priority
        enriched_lead["triage_priority"] = compute_triage_priority(enriched_lead)
        
        enriched.append(enriched_lead)
    
    if output_path is None:
        output_path = str(Path(input_path).with_suffix(".enriched.json"))
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=1, ensure_ascii=False)
    
    print(f"Enriched {len(enriched)} leads")
    print(f"Wrote {output_path}")
    
    return enriched


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Enrich lead triage with structured metadata")
    parser.add_argument("input", help="Path to bcc_lead_inference output JSON")
    parser.add_argument("-o", "--output", help="Output path (default: input.enriched.json)")
    parser.add_argument("--verification", default="cross_layer_pass", help="Default verification method")
    args = parser.parse_args()
    
    enrich_leads(args.input, args.output, args.verification)


if __name__ == "__main__":
    main()