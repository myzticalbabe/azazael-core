"""
d_grade_metadata.py — D-grade edge enrichment with batch metadata for prioritization.

Replaces "more grades" with structured metadata attached to each D-grade edge.
Metadata fields enable triage prioritization within the D-grade tier without
adding grade letters.

Schema (per edge):
  edge_id: str              # unique edge identifier
  grade: "D"                # grade tier (always "D" for this module)
  source_type: str          # "portal" | "html" | "pdf" | "json" | "api" | "manual"
  retrieval_method: str     # "headed_chrome" | "headless" | "api_call" | "manual_copy" | "ocr"
  confidence_score: float   # 0.0-1.0 (model probability, OCR confidence, etc.)
  retrieval_date: str       # ISO date when edge was retrieved
  universe: str             # "UJS" | "DOS" | "IRS" | "campaign_finance" | "courtlistener" | "propublica" | "local_html"
  verifier: str             # "splink" | "manual" | "named_entity" | "regex" | "headless_captcha"
  tier_evidence: list[str]  # ["portals_saved", "sha_verified", "manual_reviewed", "court_opinion_pdf"]
  status: str               # "unsaved" | "saved_browser_view" | "gated_unverified" | "dossier" | "dormant"
  priority: int             # 1-5 (computed from confidence + tier_evidence completeness)
  notes: str                # free text

Functions:
  enrich_d_grade_edges()    # reads D-grade edges, adds metadata, writes enriched output
  compute_priority()        # priority = f(confidence, evidence_count, verifier_strength)
  filter_for_press_pull()   # returns edges ready for press/threshold pull
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Source type taxonomy (fixed vocabulary)
SOURCE_TYPES = {
    "portal": "Government portal (UJS, DOS, IRS, campaign finance, etc.)",
    "html": "Saved HTML page (local file)",
    "pdf": "PDF document (court opinion, 990, etc.)",
    "json": "Structured JSON (API response, scraped data)",
    "api": "Direct API call",
    "manual": "Manual entry / human transcription",
}

RETRIEVAL_METHODS = {
    "headed_chrome": "Headed Chrome session (CAPTCHA-capable)",
    "headless": "Headless browser / scripted session",
    "api_call": "Direct API request",
    "manual_copy": "Manual copy-paste from browser",
    "ocr": "OCR from PDF/image",
}

VERIFIERS = {
    "splink": "Probabilistic record linkage (splink)",
    "manual": "Human adjudication",
    "named_entity": "NER extraction",
    "regex": "Regex pattern match",
    "headless_captcha": "Headless CAPTCHA breaker",
}


def compute_priority(edge: dict) -> int:
    """
    Compute priority 1-5 for D-grade edge triage.
    Higher = more urgent for press pull.
    
    Factors:
    - confidence_score (0.0-1.0): 40% weight
    - tier_evidence completeness: 30% weight
    - verifier strength: 20% weight
    - source_type value: 10% weight
    """
    confidence = edge.get("confidence_score", 0.0)
    evidence = edge.get("tier_evidence", [])
    verifier = edge.get("verifier", "manual")
    source_type = edge.get("source_type", "manual")
    
    # Confidence component (0-2)
    conf_score = min(2, int(confidence * 4))
    
    # Evidence component (0-2)
    evidence_count = len(evidence)
    evidence_score = min(2, evidence_count)
    
    # Verifier component (0-2)
    verifier_scores = {
        "splink": 2, "headless_captcha": 2,  # automated high-trust
        "named_entity": 1, "regex": 1,       # automated medium-trust
        "manual": 0,                          # human but slow
    }
    verifier_score = verifier_scores.get(verifier, 0)
    
    # Source type component (0-1)
    source_scores = {
        "portal": 1, "api": 1, "json": 1,
        "pdf": 1, "html": 0, "manual": 0,
    }
    source_score = source_scores.get(source_type, 0)
    
    total = conf_score + evidence_score + verifier_score + source_score
    return min(5, max(1, total // 1 + 1))


def filter_for_press_pull(edges: list[dict]) -> list[dict]:
    """Return D-grade edges ready for press/threshold pull.
    
    Criteria:
    - priority >= 3
    - status != "dormant"
    - universe in {"UJS", "DOS", "courtlistener", "propublica"} (public portals)
    - has retrievable source
    """
    public_universes = {"UJS", "DOS", "IRS", "campaign_finance", "courtlistener", "propublica", "pacourts"}
    
    ready = []
    for e in edges:
        if e.get("grade") != "D":
            continue
        if e.get("priority", 0) < 3:
            continue
        if e.get("status") == "dormant":
            continue
        if e.get("universe") not in public_universes:
            continue
        if e.get("source_type") not in {"portal", "html", "pdf"}:
            continue
        ready.append(e)
    return ready


def enrich_d_grade_edges(
    input_path: str,
    output_path: str = None,
    default_universe: str = "manual",
    default_verifier: str = "manual",
    default_source_type: str = "manual",
) -> list[dict]:
    """
    Read edges from input_path, enrich D-grade edges with metadata,
    write enriched edges to output_path (or input_path + ".enriched.json").
    """
    with open(input_path, encoding="utf-8") as f:
        edges = json.load(f)
    
    if not isinstance(edges, list):
        raise ValueError(f"Expected list of edges, got {type(edges)}")
    
    enriched = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        
        # Only enrich D-grade edges
        if edge.get("grade") != "D":
            enriched.append(edge)
            continue
        
        # Add metadata fields with defaults
        enriched_edge = {**edge}
        
        # Default metadata (can be overridden by existing fields)
        enriched_edge.setdefault("source_type", default_source_type)
        enriched_edge.setdefault("retrieval_method", "manual")
        enriched_edge.setdefault("confidence_score", 0.3)  # low default for D-grade
        enriched_edge.setdefault("retrieval_date", datetime.now(timezone.utc).date().isoformat())
        enriched_edge.setdefault("universe", default_universe)
        enriched_edge.setdefault("verifier", default_verifier)
        enriched_edge.setdefault("tier_evidence", [])
        enriched_edge.setdefault("status", "unsaved")
        enriched_edge.setdefault("notes", "")
        
        # Compute priority
        enriched_edge["priority"] = compute_priority(enriched_edge)
        
        enriched.append(enriched_edge)
    
    if output_path is None:
        output_path = str(Path(input_path).with_suffix(".enriched.json"))
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=1, ensure_ascii=False)
    
    print(f"Enriched {len([e for e in enriched if e.get('grade') == 'D'])} D-grade edges")
    print(f"Wrote {output_path}")
    
    return enriched


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Enrich D-grade edges with batch metadata")
    parser.add_argument("input", help="Path to edges JSON (list of edge dicts)")
    parser.add_argument("-o", "--output", help="Output path (default: input.enriched.json)")
    parser.add_argument("--universe", default="manual", help="Default universe for edges without one")
    parser.add_argument("--verifier", default="manual", help="Default verifier")
    parser.add_argument("--source-type", default="manual", help="Default source type")
    args = parser.parse_args()
    
    enrich_d_grade_edges(
        args.input,
        args.output,
        args.universe,
        args.verifier,
        args.source_type,
    )


if __name__ == "__main__":
    main()