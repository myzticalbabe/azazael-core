#!/usr/bin/env python3
"""Process entries 0-43 (first slice of 173) from NEURAL_MESH_WIRE_OR_CUT.json.

For each pending or dispatched entry in the target modules, independently
verify whether a real production caller exists by cross-referencing the
probe's own _reference_index(), string_references(), and test_references().

Decision logic:
  - If the function name is in prod_used (ast.Name/Attribute in production) -> WIRED
  - If only string literals reach it (string_references hit) -> DISPATCHED (confirmed)
  - If only tests reach it -> PENDING (test_is_the_wire decider)
  - If nothing reaches it -> PENDING (dead_candidate) or RETIRE (if queued)

Output: state/wire_or_cut_slice_0_43.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import mesh_function_preflight as mfp  # noqa: E402

TARGET_MODULES = {
    "allocation", "axon", "backup", "chorus", "communication", "config",
    "consolidation", "container", "db_monitor", "dendrite", "dreaming",
    "driver", "error_recovery", "evidence",
}


def main() -> int:
    verdict_path = REPO / "NEURAL_MESH_WIRE_OR_CUT.json"
    table = json.loads(verdict_path.read_text(encoding="utf-8"))
    declared = table.get("declared", {})

    # Get the ordered list of keys (JSON preserves insertion order in Python 3.7+)
    keys = list(declared.keys())
    slice_keys = keys[:44]  # indices 0-43

    # Build the reference indices (these are cached internally)
    funcs, counts = mfp.build(None)
    unreferenced = {fn.label for fn in funcs if "PF16" in fn.failures}
    prod_used = mfp._reference_index()[2]  # production Name|Attribute set
    string_refs = mfp.string_references()
    test_refs = mfp.test_references()
    data_hits = mfp.data_index(
        {str(declared[k].get("qualname", "")).rsplit(".", 1)[-1] for k in slice_keys}
    )

    # Load the operator's queue for proposed retirements
    queue_path = REPO / "state" / "wire_or_cut_queue.json"
    queued = set()
    if queue_path.exists():
        q = json.loads(queue_path.read_text(encoding="utf-8"))
        for item in q.get("batch", []):
            queued.add(item.get("target", ""))

    results = []
    for idx, key in enumerate(slice_keys):
        entry = declared[key]
        qualname = str(entry.get("qualname", ""))
        name = qualname.rsplit(".", 1)[-1]
        status = str(entry.get("status", "pending"))

        if status not in ("pending", "dispatched"):
            continue

        module = str(entry.get("module", ""))
        if module not in TARGET_MODULES:
            continue

        # Independent verification: check prod_used for the bare name
        # (PF16 uses this set; if name is in prod_used, PF16 would NOT fire)
        in_prod_used = name in prod_used

        # Check string references
        str_hits = string_refs.get(name) or string_refs.get(qualname) or []

        # Check test references
        test_hits = test_refs.get(name) or test_hits_get(qualname) or []

        # Check data index
        data_hits_for = data_hits.get(name, [])

        # Check if this function is in the unreferenced set
        is_pf16_unreferenced = key in unreferenced

        # Determine decision
        if in_prod_used and not is_pf16_unreferenced:
            decision = "wired"
            evidence = ["Real static production caller found via _reference_index"]
        elif in_prod_used:
            decision = "wired"
            evidence = ["Name appears in prod_used but PF16 still fired -- verifying"]
        elif str_hits:
            decision = "dispatched"
            evidence = [f"String reference at {h}" for h in str_hits[:3]]
        elif not is_pf16_unreferenced:
            decision = "wired"
            evidence = ["Not in unreferenced set -- has a production caller"]
        elif test_hits:
            decision = "pending"
            evidence = [f"Test reference at {h}" for h in test_hits[:3]]
            evidence.append(f"decider: {entry.get('decider', 'unknown')}")
        elif key in queued:
            decision = "retire"
            evidence = ["Queued in state/wire_or_cut_queue.json for retirement"]
            evidence.append(f"decider: {entry.get('decider', 'unknown')}")
        else:
            decision = "pending"
            evidence = ["No production caller, no string reference, no test reference"]
            evidence.append(f"decider: {entry.get('decider', 'unknown')}")
            evidence.append(f"triage: {entry.get('triage', 'unknown')}")

        results.append({
            "index": idx,
            "key": key,
            "module": module,
            "qualname": qualname,
            "line": entry.get("line"),
            "current_status": status,
            "pf16_unreferenced": is_pf16_unreferenced,
            "in_prod_used": in_prod_used,
            "string_references": str_hits[:3],
            "test_references": test_hits[:3],
            "data_references": data_hits_for[:3],
            "queued_for_retirement": key in queued,
            "existing_decider": entry.get("decider"),
            "existing_triage": entry.get("triage"),
            "decision": decision,
            "decision_evidence": evidence,
        })

    counts_out = {"wired": 0, "retired": 0, "dispatched": 0, "pending": 0}
    for r in results:
        counts_out[r["decision"]] = counts_out.get(r["decision"], 0) + 1

    output = {
        "slice": "0-43",
        "total_in_slice": len(slice_keys),
        "processed": len(results),
        "target_modules": sorted(TARGET_MODULES),
        "verdict_generated_at": table.get("generated_at"),
        "probe": "PF16 + independent reference-index verification",
        "decision_counts": counts_out,
        "entries": results,
    }

    out_path = REPO / "state" / "wire_or_cut_slice_0_43.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {out_path}")
    print(json.dumps(counts_out, indent=2))
    for r in results:
        print(f"  [{r['index']:2d}] {r['key']:60s} "
              f"status={r['current_status']:12s} -> {r['decision']}")
    return 0


def test_hits_get(qualname: str) -> list[str]:
    """Helper: check test_references for full qualname."""
    refs = mfp.test_references()
    # qualname like "Foo.bar" -> check "bar" and "Foo.bar"
    name = qualname.rsplit(".", 1)[-1]
    return refs.get(name) or refs.get(qualname) or []


if __name__ == "__main__":
    sys.exit(main())
