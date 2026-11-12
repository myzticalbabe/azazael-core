#!/usr/bin/env python3
"""


__all__ = [
    "cmd_diagnose",
    "cmd_fix",
    "cmd_scan",
    "main",
]

audit_repair.py — Audit trail forensic recovery and repair.

Attempts to fix broken audit files by:
1. Using verify_chain_skip_corrupt() to parse through corrupt lines
2. Re-anchoring head hashes for files that pass chain verification
3. Providing diagnostics for manual repair when auto-repair isn't possible

Usage:
    python -m CORE.audit_repair scan          # Scan all files, show repair candidates
    python -m CORE.audit_repair fix <file>    # Attempt auto-repair of a file
    python -m CORE.audit_repair diagnose      # Full diagnostic of all files
"""

import sys
import json
from pathlib import Path
from CORE.audit_trail import (
    verify_chain, verify_chain_skip_corrupt, verify_completeness,
    verify_head_hash, anchor_head_hash, get_head_hash,
    unlock_audit_file, lock_audit_file, read_audit_lines,
    AUDIT_DIR,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _read_full_audit(fp: Path) -> list:
    """Read all entries from an audit file, returning (line_num, data, raw) tuples."""
    results = []
    with fp.open("r", encoding="utf-8") as f:
        for i, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                results.append((i, data, raw))
            except json.JSONDecodeError as exc:
                note_failure("audit_repair:L47", exc)
                results.append((i, None, raw))
    return results


def cmd_scan():
    if not AUDIT_DIR.exists():
        print("No .audit directory found")
        return
    candidates = []
    for fp in sorted(AUDIT_DIR.glob("session_*.jsonl")):
        chain_ok, err_line = verify_chain(str(fp))
        if not chain_ok:
            all_ok, corrupt, details = verify_chain_skip_corrupt(str(fp))
            candidates.append({
                "file": str(fp),
                "chain_ok": chain_ok,
                "error_line": err_line,
                "skip_corrupt_ok": all_ok,
                "corrupt_lines": len(corrupt),
            })
    print(f"=== Repair Candidates ({len(candidates)}) ===")
    for c in candidates:
        fp = c["file"][-40:]
        print(f"  {fp}")
        print(f"    Chain error at line {c['error_line']}")
        print(f"    Forensic recovery: {'possible' if c['skip_corrupt_ok'] else 'needs manual repair'}")
        print(f"    Corrupt lines: {c['corrupt_lines']}")


def cmd_diagnose():
    if not AUDIT_DIR.exists():
        print("No .audit directory found")
        return
    for fp in sorted(AUDIT_DIR.glob("session_*.jsonl")):
        fpath = str(fp)
        chain_ok, err_line = verify_chain(fpath)
        anchor_ok, stored, current = verify_head_hash(fpath)
        complete_ok, missing = verify_completeness(fpath)
        entries = len(read_audit_lines(fpath))
        size = fp.stat().st_size

        status = []
        if chain_ok:
            status.append("CHAIN_OK")
        else:
            status.append(f"CHAIN_BAD(L{err_line})")
        if anchor_ok:
            status.append("ANCHOR_OK")
        elif stored and not anchor_ok:
            status.append("ANCHOR_MISMATCH")
        else:
            status.append("NO_ANCHOR")
        if not complete_ok and missing:
            status.append(f"MISSING({len(missing)})")
        elif complete_ok:
            status.append("COMPLETE")

        print(f"  {fp.name:45s} {entries:4d} entries {size:>8,d}B  {' | '.join(status)}")


def cmd_fix(filepath: str):
    fp = Path(filepath)
    if not fp.exists():
        print(f"File not found: {filepath}")
        return
    fpath = str(fp)
    chain_ok, err_line = verify_chain(fpath)

    if chain_ok:
        print(f"  {fp.name}: chain already valid")
        anchor_ok, _, _ = verify_head_hash(fpath)
        if not anchor_ok:
            # Re-anchor
            try:
                unlock_audit_file(fpath)
                current_hash = get_head_hash(fpath)
                anchor_head_hash(head_hash=current_hash, entry_count=0, filepath=fpath)
                lock_audit_file(fpath)
                print(f"  Re-anchored: {current_hash[:16]}...")
            except Exception as e:
                print(f"  Re-anchor failed: {e}")
        else:
            print("  Anchor OK")
        return

    # Chain is broken — attempt forensic recovery
    print(f"  {fp.name}: chain broken at line {err_line}")
    all_ok, corrupt, details = verify_chain_skip_corrupt(fpath)
    if all_ok:
        print(f"  Forensic recovery succeeded after skipping {len(corrupt)} corrupt lines")
        # Re-anchor
        try:
            unlock_audit_file(fpath)
            current_hash = get_head_hash(fpath)
            if current_hash:
                anchor_head_hash(head_hash=current_hash, entry_count=0, filepath=fpath)
                lock_audit_file(fpath)
                print(f"  Re-anchored with head: {current_hash[:16]}...")
        except Exception as e:
            print(f"  Re-anchor failed: {e}")
    else:
        print(f"  Forensic recovery partially succeeded — {len(corrupt)} corrupt lines")
        for ln, reason in details:
            print(f"    Line {ln}: {reason}")
        print("  Manual repair needed: edit the file directly to fix corrupt entries")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "scan":
        cmd_scan()
    elif cmd == "diagnose":
        cmd_diagnose()
    elif cmd == "fix" and len(sys.argv) >= 3:
        cmd_fix(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")

if __name__ == "__main__":
    main()
