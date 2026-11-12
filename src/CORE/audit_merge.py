#!/usr/bin/env python3
"""audit_merge.py — utility functions (merge_sessions)"""


__all__ = [
    "merge_sessions",
]

import json
from pathlib import Path
from datetime import datetime
from CORE.audit_trail import read_audit_lines, AUDIT_DIR
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

def merge_sessions(filepaths: list, output: str | None = None) -> str:
    """Merge multiple audit files sorted by timestamp."""
    all_entries = []
    for fp in filepaths:
        for ln, d in read_audit_lines(fp):
            d["_source"] = Path(fp).name
            all_entries.append(d)

    all_entries.sort(key=lambda x: x.get("ts", ""))
    if not output:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = str(AUDIT_DIR / f"merged_{ts}.jsonl")

    with Path(output).open("w", encoding="utf-8") as f:
        for entry in all_entries:
            entry.pop("hash", None)
            entry.pop("prev_hash", None)
            entry.pop("signature", None)
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    return output

if __name__ == "__main__":
    import sys
    fps = sys.argv[1:] if len(sys.argv) > 1 else list(AUDIT_DIR.glob("session_*.jsonl"))[:3]
    out = merge_sessions([str(f) for f in fps])
    print(f"Merged {len(fps)} files -> {out}")
