#!/usr/bin/env python3
"""audit_watch.py - utility functions (watch_audit).

Real-time audit trail watcher - tail -f for new audit entries.
"""


__all__ = [
    "watch_audit",
]

import time
import json
from CORE.audit_trail import AUDIT_DIR

def watch_audit(interval: float = 1.0):
    positions = {}
    print(f"=== Watching audit trail (polling every {interval}s) ===")
    print("  Press Ctrl+C to stop")
    try:
        while True:
            for fp in sorted(AUDIT_DIR.glob("session_*.jsonl")):
                current_size = fp.stat().st_size
                prev_size = positions.get(str(fp), 0)
                if current_size > prev_size:
                    with fp.open("r", encoding="utf-8") as f:
                        f.seek(prev_size)
                        new_lines = f.read().strip()
                        if new_lines:
                            ts = datetime.now().isoformat()
                            for line in new_lines.split("\n"):
                                line = line.strip()
                                if not line: continue
                                try:
                                    d = json.loads(line)
                                    print(f"  [{ts}] {fp.name}: {d.get('type','?')}")
                                except json.JSONDecodeError:
                                    print(f"  [{ts}] {fp.name}: (raw) {line[:80]}")
                positions[str(fp)] = current_size
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  Stopped")

from datetime import datetime
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
if __name__ == "__main__":
    watch_audit()
