"""
gap_metadata.py — Gap disposition enrichment with actionable metadata.

Replaces vague "disposition" strings with structured fields that drive
concrete next actions and unblocking.

Schema (per gap):
  gap_id: str                    # unique identifier (from bcc_gap_index.json)
  disposition: str               # EXEC-READY | OPEN | OPEN (advanced) | RESOLVED | MONITOR | BLOCKED
  records_action: str            # concrete records request (FOIA, RTK, subpoena, etc.)
  unblocker: str                 # what would move this forward (response, ruling, filing)
  evidence_completeness: float   # 0.0-1.0 (fraction of needed evidence gathered)
  corroboration_count: int       # number of independent sources supporting
  last_advanced: str             # ISO date when gap last changed state
  topic: str                     # Governance | Financial | Funding | Housing | Legal | Oversight | Opacity | Identity | Patient harm | Enforcement | Transparency | Workforce
  leads: list[str]               # linked LEAD-XX ids
  basis: str                     # current evidence summary
  notes: str                     # free text

Functions:
  enrich_gap_dispositions()      # reads gap index, adds metadata, writes enriched output
  compute_urgency()              # urgency 1-5 from disposition + evidence + topic
  filter_for_action()            # returns gaps ready for records request
"""
# WRITERSHIP (R0 ruling 2026-10-28, keep-both): enrich_gap_dispositions() reads the
# tracked dict (scripts/bcc_gap_index.json) and is the SOLE WRITER of the enriched
# list (bcc_gap_index.enriched.json). Never write the tracked dict here; structure
# belongs to scripts/build_gap_index.py, field updates to scripts/land_all_results.py.
# NOTE (MS1 correction 2026-10-28): scripts/d_grade_metadata.py enriches D-grade
# *evidence-edge* files only and never writes this store.

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Fixed vocabularies (closed sets)
DISPOSITIONS = {
    "EXEC-READY": "Package ready to send; no further research needed",
    "OPEN": "Active investigation; records action pending",
    "OPEN (advanced)": "Advanced but blocked on specific records",
    "RESOLVED": "Closed; question answered by public records",
    "MONITOR": "Watching for future filing/event (e.g., FY2026 990)",
    "BLOCKED": "Structurally blocked (no public access path)",
}

TOPICS = {
    "Governance", "Financial", "Funding", "Housing", "Legal",
    "Oversight", "Opacity", "Identity", "Patient harm",
    "Enforcement", "Transparency", "Workforce",
}


def compute_urgency(gap: dict) -> int:
    """
    Compute urgency 1-5 for gap triage.
    Higher = more urgent for records action.
    
    Factors:
    - disposition weight: 40%
    - evidence_completeness: 30%
    - topic severity: 20%
    - corroboration_count: 10%
    """
    disposition = gap.get("disposition", "OPEN")
    evidence = gap.get("evidence_completeness", 0.0)
    topic = gap.get("topic", "Governance")
    corr = gap.get("corroboration_count", 0)
    
    # Disposition weight (0-2)
    disp_scores = {
        "EXEC-READY": 2,
        "BLOCKED": 2,      # needs structural solution
        "OPEN (advanced)": 1,
        "OPEN": 1,
        "MONITOR": 0,
        "RESOLVED": 0,
    }
    disp_score = disp_scores.get(disposition, 1)
    
    # Evidence completeness (0-2)
    evidence_score = min(2, int(evidence * 4))
    
    # Topic severity (0-2)
    high_severity = {"Patient harm", "Enforcement", "Legal", "Financial"}
    med_severity = {"Governance", "Oversight", "Funding", "Housing"}
    if topic in high_severity:
        topic_score = 2
    elif topic in med_severity:
        topic_score = 1
    else:
        topic_score = 0
    
    # Corroboration (0-1)
    corr_score = min(1, corr)
    
    total = disp_score + evidence_score + topic_score + corr_score
    return min(5, max(1, total + 1))


def filter_for_action(gaps: list[dict]) -> list[dict]:
    """Return gaps ready for records request.
    
    Criteria:
    - urgency >= 3
    - disposition != "RESOLVED" and != "MONITOR"
    - has records_action
    - not BLOCKED without unblocker
    """
    ready = []
    for g in gaps:
        if g.get("disposition") in ("RESOLVED", "MONITOR"):
            continue
        if g.get("urgency", 0) < 3:
            continue
        if not g.get("records_action"):
            continue
        if g.get("disposition") == "BLOCKED" and not g.get("unblocker"):
            continue
        ready.append(g)
    return ready


def enrich_gap_dispositions(
    input_path: str,
    output_path: str = None,
    default_topic: str = "Governance",
) -> list[dict]:
    """
    Read gaps from input_path, enrich with metadata,
    write enriched gaps to output_path (or input_path + ".enriched.json").
    """
    with open(input_path, encoding="utf-8") as f:
        gaps_raw = json.load(f)
    
    if not isinstance(gaps_raw, dict):
        raise ValueError(f"Expected dict of gaps keyed by ID, got {type(gaps_raw)}")
    
    enriched = []
    for gap_id, gap in gaps_raw.items():
        if not isinstance(gap, dict):
            continue
        
        enriched_gap = {**gap, "gap_id": gap_id}
        
        # Add metadata fields with defaults
        enriched_gap.setdefault("records_action", "")
        enriched_gap.setdefault("unblocker", "")
        enriched_gap.setdefault("evidence_completeness", 0.3)
        enriched_gap.setdefault("corroboration_count", len(gap.get("leads", [])))
        enriched_gap.setdefault("last_advanced", gap.get("date", datetime.now(timezone.utc).date().isoformat()))
        enriched_gap.setdefault("topic", default_topic)
        
        # Compute urgency
        enriched_gap["urgency"] = compute_urgency(enriched_gap)
        
        enriched.append(enriched_gap)
    
    if output_path is None:
        output_path = str(Path(input_path).with_suffix(".enriched.json"))
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=1, ensure_ascii=False)
    
    print(f"Enriched {len(enriched)} gaps")
    print(f"Wrote {output_path}")
    
    return enriched


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Enrich gap dispositions with actionable metadata")
    parser.add_argument("input", help="Path to bcc_gap_index.json")
    parser.add_argument("-o", "--output", help="Output path (default: input.enriched.json)")
    parser.add_argument("--topic", default="Governance", help="Default topic")
    args = parser.parse_args()
    
    enrich_gap_dispositions(args.input, args.output, args.topic)


if __name__ == "__main__":
    main()