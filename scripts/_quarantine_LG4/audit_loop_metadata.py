"""
audit_loop_metadata.py — Audit loop state enrichment with structured metadata.

Replaces string states (STALE, UNBOUND, etc.) with fields that drive
concrete recovery actions and supersession tracking.

Schema (per loop):
  loop_id: str                     # task name
  task: str                        # task name from registry
  heartbeat: str                   # last heartbeat timestamp
  what: str                        # what the loop does
  bound: float                     # cadence bound in seconds
  source: str                      # seed | discovered
  hb_status: str                   # ok | NEVER-RUN | STALE | unknown | unbound
  task_status: str                 # ok | error | warning
  run_status: str                  # ok | unknown | superseded | policy-retired
  superseded: bool                 # whether loop is superseded by chain job
  last_heartbeat: str              # ISO timestamp of last successful run
  bound_violated: float            # hours over bound (negative = within bound)
  superseded_by: str               # chain job / task that supersedes this loop
  recovery_action: str             # manual_trigger | fix_heartbeat | file_bug | investigate
  policy_retired: bool             # whether retired by operator policy
  policy_reason: str               # reason if policy_retired
  rec: dict                        # original task record

Functions:
  enrich_audit_loops()             # reads merged audit entries, adds metadata
  compute_health_score()           # 0.0-1.0 health from statuses
  filter_for_recovery()            # returns loops needing intervention
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def compute_health_score(entry: dict) -> float:
    """
    Compute health score 0.0-1.0 for audit loop.
    1.0 = healthy, 0.0 = critical failure.
    
    Factors:
    - hb_status ok: +0.4
    - task_status ok: +0.3
    - run_status ok/unknown/superseded: +0.2
    - not superseded: +0.1
    - not policy retired: +0.1 (or 0 if retired)
    - not lost: +0.1
    """
    score = 0.0
    
    hb = entry.get("hb_status", "")
    if hb == "ok":
        score += 0.4
    elif hb in ("NEVER-RUN", "STALE"):
        score += 0.0
    
    task = entry.get("task_status", "")
    if task == "ok":
        score += 0.3
    
    run = entry.get("run_status", "")
    if run in ("ok", "unknown", "superseded"):
        score += 0.2
    
    if not entry.get("superseded", False):
        score += 0.1
    else:
        score += 0.05  # superseded but tracked is better than ghost
    
    if not entry.get("policy_retired", False):
        score += 0.1
    
    if not entry.get("rec", {}).get("lost", False):
        score += 0.1
    
    return max(0.0, min(1.0, score))


def filter_for_recovery(entries: list[dict]) -> list[dict]:
    """Return loops needing recovery intervention.
    
    Criteria:
    - health_score < 0.7
    - NOT policy-retired
    - NOT superseded (or superseded but task_status != ok)
    """
    recovery = []
    for e in entries:
        if e.get("policy_retired", False):
            continue
        if e.get("health_score", 0) >= 0.7:
            continue
        if e.get("superseded", False) and e.get("task_status") == "ok":
            continue  # correctly superseded
        recovery.append(e)
    return recovery


def enrich_audit_loops(
    input_path: str,
    output_path: str = None,
) -> list[dict]:
    """
    Read merged audit entries (from audit_loop_liveness.py merge_entries output),
    enrich with metadata, write enriched entries to output_path.
    """
    with open(input_path, encoding="utf-8") as f:
        entries = json.load(f)
    
    if not isinstance(entries, list):
        raise ValueError("Expected list of audit entries")
    
    enriched = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        
        enriched_entry = {**entry}
        
        # Compute health score
        enriched_entry["health_score"] = compute_health_score(enriched_entry)
        
        # Add recovery action if needed
        if enriched_entry["health_score"] < 0.7:
            if entry.get("hb_status") in ("NEVER-RUN", "STALE"):
                enriched_entry.setdefault("recovery_action", "manual_trigger")
            elif entry.get("task_status") not in ("ok", "unknown"):
                enriched_entry.setdefault("recovery_action", "fix_heartbeat")
            elif entry.get("run_status") not in ("ok", "unknown", "superseded"):
                enriched_entry.setdefault("recovery_action", "investigate")
            else:
                enriched_entry.setdefault("recovery_action", "investigate")
        
        # Add superseded_by if superseded
        if entry.get("superseded", False) and not entry.get("superseded_by"):
            enriched_entry["superseded_by"] = "chain_job"
        
        enriched.append(enriched_entry)
    
    if output_path is None:
        output_path = str(Path(input_path).with_suffix(".enriched.json"))
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=1, ensure_ascii=False)
    
    print(f"Enriched {len(enriched)} audit loops")
    print(f"Wrote {output_path}")
    
    return enriched


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Enrich audit loop states with recovery metadata")
    parser.add_argument("input", help="Path to merged audit entries JSON")
    parser.add_argument("-o", "--output", help="Output path (default: input.enriched.json)")
    args = parser.parse_args()
    
    enrich_audit_loops(args.input, args.output)


if __name__ == "__main__":
    main()