#!/usr/bin/env python
# coding: utf-8
"""
session_start.py — Unified Azazael session startup.

Combines two mandatory startup operations into one command:
  1. Sync skills: azazael/skills/*.md -> .agents/skills/<name>/SKILL.md
  2. Initialize DirectMemory for session recording

Usage:
  python azazael/src/scripts/session_start.py

Exit codes:
  0 = both operations succeeded
  1 = partial failure (one operation succeeded)
  2 = total failure (neither operation succeeded)
"""


__all__ = ["main"]
import sys
import subprocess
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

_SRC_DIR = Path(__file__).resolve().parent.parent  # azazael/src/
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


def main() -> int:
    sync_ok = False
    mem_ok = False
    audit_ok = False

    print("=== Azazael Session Start ===")

    # ---- 0. Start the JSONL audit trail (hash-chained, Merkle-proved) ----
    # Every session gets a .audit/session_<ts>_<uuid>.jsonl file in addition to
    # the DirectMemory record. Idempotent - only creates one file per process.
    audit_path = ""
    try:
        from CORE.audit_trail import audit_session_start
        audit_path = audit_session_start() or ""
        audit_ok = bool(audit_path)
        if audit_ok:
            print(f"  Audit trail: {audit_path}")
        else:
            print("  Audit trail: FAILED - audit_session_start() returned no file")
    except Exception as e:
        print(f"  Audit trail: FAILED - {e}")

    # ---- 1. Sync skills (via subprocess — no package imports needed) ----
    sync_script = _SRC_DIR / "scripts" / "sync_skills.py"
    try:
        result = subprocess.run(
            [sys.executable, str(sync_script)],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace"
        )
        sync_output = result.stdout.strip()
        if result.returncode == 0:
            sync_ok = True
            # Extract summary line for compact display
            for line in sync_output.splitlines():
                if "Skills Sync Complete" in line or "Created:" in line:
                    print(f"  Skills sync: {line.strip()}")
                    break
            else:
                print(f"  Skills sync: OK")
        else:
            err = result.stderr.strip()[:120] if result.stderr else "unknown error"
            print(f"  Skills sync: FAILED - {err}")
    except Exception as e:
        print(f"  Skills sync: FAILED - {e}")

    # ---- 2. Initialize DirectMemory ----
    dm = None
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()
        dm.record("session_startup", {
            "action": "unified_startup",
            "sync_ok": sync_ok,
        }, tags=["startup"])
        mem_ok = True
        print(f"  DirectMemory: session {dm.session_id}, {dm.count()} events")
    except Exception as e:
        print(f"  DirectMemory: FAILED - {e}")

    # ---- Summary ----
    print("=============================")
    if sync_ok and mem_ok and audit_ok:
        print(f"Startup OK - {dm.session_id if dm else '?'} | audit={audit_path}")
        return 0
    elif sync_ok and mem_ok:
        print("Startup: AUDIT NOT RECORDED")
        return 0
    elif sync_ok or mem_ok:
        print("Startup: PARTIAL FAILURE")
        return 1
    else:
        print("Startup: TOTAL FAILURE")
        return 2


if __name__ == "__main__":
    sys.exit(main())
