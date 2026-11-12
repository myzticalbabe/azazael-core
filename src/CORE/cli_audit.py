#!/usr/bin/env python3
"""


__all__ = [
    "cmd_archive",
    "cmd_diff",
    "cmd_repair",
    "cmd_search",
    "cmd_status",
    "cmd_verify",
    "main",
]

cli_audit.py — Audit trail CLI for azazael-ctl.

Usage:
    azazael-ctl audit status          # Show audit trail health
    azazael-ctl audit verify <file>   # Verify chain integrity
    azazael-ctl audit search <type>   # Search entries by type
    azazael-ctl audit diff <a> <b>    # Diff two audit files
    azazael-ctl audit repair <file>   # Forensic recovery
    azazael-ctl audit archive <file>  # Archive a session
"""

import sys
from pathlib import Path
from CORE.audit_trail import (
    verify_chain, verify_on_startup, verify_completeness,
    read_audit_lines, find_entries, lock_audit_file,
    verify_chain_skip_corrupt, audit_anomaly_scan,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def cmd_status():
    broken = verify_on_startup()
    print("=== Audit Trail Status ===")
    print(f"  Broken files: {len(broken)}")
    if broken:
        for b in broken:
            fp = b.get("file", "?")[-40:]
            chain = b.get("chain_valid", "?")
            anchor = b.get("anchor_valid", "?")
            complete = b.get("completeness_valid", "?")
            print(f"  [{'CHAIN' if not chain else 'OK'}] {fp}")
            if not complete:
                for ln, etype, det in b.get("missing_expectations", []):
                    print(f"       Missing line {ln}: {etype} - {det}")


def cmd_verify(filepath: str):
    fp = Path(filepath)
    if not fp.exists():
        print(f"File not found: {filepath}")
        return
    print(f"=== Verify: {fp.name} ===")
    chain_ok, err_line = verify_chain(str(fp))
    print(f"  Chain: {'OK' if chain_ok else f'BROKEN at line {err_line}'}")
    complete_ok, missing = verify_completeness(str(fp))
    completeness_str = f"MISSING {len(missing)}" if not complete_ok else "OK"
    print(f"  Completeness: {completeness_str}")
    if missing:
        for ln, etype, det in missing:
            print(f"    Line {ln}: expected {etype} - {det}")


def cmd_search(entry_type: str):
    from CORE.audit_trail import AUDIT_DIR
    if not AUDIT_DIR.exists():
        print("No .audit directory found")
        return
    for fp in sorted(AUDIT_DIR.glob("session_*.jsonl")):
        matches = find_entries(str(fp), entry_type)
        if matches:
            for ln, d in matches[:5]:
                ts = d.get("ts", "?")
                print(f"  {fp.name}:{ln}  [{ts}]  {entry_type}")
            if len(matches) > 5:
                print(f"    ... and {len(matches) - 5} more")


def cmd_diff(file_a: str, file_b: str):
    fa = Path(file_a)
    fb = Path(file_b)
    if not fa.exists() or not fb.exists():
        print("One or both files not found")
        return
    lines_a = read_audit_lines(str(fa))
    lines_b = read_audit_lines(str(fb))
    types_a = {d.get("type", "") for _, d in lines_a}
    types_b = {d.get("type", "") for _, d in lines_b}
    only_a = types_a - types_b
    only_b = types_b - types_a
    print(f"=== Diff: {fa.name} vs {fb.name} ===")
    if only_a:
        print(f"  Only in {fa.name}: {sorted(only_a)}")
    if only_b:
        print(f"  Only in {fb.name}: {sorted(only_b)}")
    if not only_a and not only_b:
        print("  Same event type composition")
    print(f"  Entries: {len(lines_a)} vs {len(lines_b)}")


def cmd_repair(filepath: str):
    fp = Path(filepath)
    if not fp.exists():
        print(f"File not found: {filepath}")
        return
    print(f"=== Forensic Recovery: {fp.name} ===")
    all_ok, corrupt, details = verify_chain_skip_corrupt(str(fp))
    anomalies = audit_anomaly_scan(str(fp))
    print(f"  All valid: {all_ok}")
    print(f"  Corrupt lines: {len(corrupt)}")
    for ln, reason in details:
        print(f"    Line {ln}: {reason}")
    if anomalies:
        print(f"  Anomalies: {len(anomalies)}")
        for a in anomalies:
            print(f"    {a}")
    if not all_ok:
        print("\n  To repair, use: unlock_audit_file() then fix corrupt entries")


def cmd_archive(filepath: str):
    fp = Path(filepath)
    if not fp.exists():
        print(f"File not found: {filepath}")
        return
    try:
        lock_audit_file(str(fp))
        print(f"  Locked (archived): {fp.name}")
    except Exception as e:
        print(f"  Failed: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    commands = {"status": cmd_status}
    if cmd in commands:
        commands[cmd]()
    elif cmd == "verify" and len(sys.argv) >= 3:
        cmd_verify(sys.argv[2])
    elif cmd == "search" and len(sys.argv) >= 3:
        cmd_search(" ".join(sys.argv[2:]))
    elif cmd == "diff" and len(sys.argv) >= 4:
        cmd_diff(sys.argv[2], sys.argv[3])
    elif cmd == "repair" and len(sys.argv) >= 3:
        cmd_repair(sys.argv[2])
    elif cmd == "archive" and len(sys.argv) >= 3:
        cmd_archive(sys.argv[2])
    else:
        print(f"Unknown audit command: {cmd}")

if __name__ == "__main__":
    main()
