"""
sync_canonical_docs.py — RULE 0Z-J: Canonical Document Sync & Drift Detection

Gemini-architected solution (June 21, 2026) to the Quartet pillar document drift
problem. Provides cryptographic hash verification, cross-pillar sync, and drift
reporting for ALL 8 canonical documents across 4 Quartet pillars.

Architecture:
  W11 (C:/Azazael/azazael/) is the SOLE authoritative source (master).
  KALI, UBUNTU, and G: are sync targets.
  Every sync computes SHA-256 hashes and compares before overwriting.

Usage:
  python sync_canonical_docs.py                        # Dry run: report drift only
  python sync_canonical_docs.py --drift-report         # Same as above (explicit)
  python sync_canonical_docs.py --sync                 # Sync ALL pillars to W11 master
  python sync_canonical_docs.py --sync --pillar KALI   # Sync only KALI pillar
  python sync_canonical_docs.py --verify               # Verify all pillars match W11
  python sync_canonical_docs.py --verify --pillar G:   # Verify only G: pillar
  python sync_canonical_docs.py --hash                 # Print W11 document hashes
  python sync_canonical_docs.py --self-test            # Run internal consistency checks
"""

import hashlib
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List
import subprocess
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# === CONFIGURATION ===

# Canonical documents (relative to project root)
# Per RULE 0K canonical reading order, plus COMPLIANCE_CHECKLIST.md for gate enforcement
CANONICAL_DOCS: list[str] = [
    "SYSTEM_INSTRUCTIONS.md",    # RULE 0K #1 — Core SOP, gate assertion, all RULEs 0A-0Z
    "SOP.md",                    # RULE 0K #2 — Error recording SOP, enforcement procedures
    "ERROR_LOG.md",              # RULE 0K #3 — All documented errors with root cause + fix
    "MEMORIES.md",               # RULE 0K #4 — Cross-session continuity, persistent knowledge
    "PRODUCTION_TODO.md",        # RULE 0K #7 — Production wiring tasks, build targets
    "AZAZAEL_INSIGHTS.md",       # Architectural insights, neural advancement patterns
    "WORM_CONFIG_TRACE.md",      # Worm configuration and traceability
    "COMPLIANCE_CHECKLIST.md",   # Compliance gate checklist, pre-action enforcement
]

# Master pillar (sole authoritative source)
MASTER_ROOT = Path("C:/Azazael/azazael")

# Sync target pillars
# WSL UNC paths (//wsl$/DistroName/...) work on Windows when WSL distro is Running.
# When Stopped, the path will be unreachable — handled gracefully by verify/sync functions.
TARGET_PILLARS: dict[str, Path] = {
    "KALI":    Path("//wsl$/kali-linux/root/azazael"),
    "UBUNTU":  Path("//wsl$/Ubuntu-26.04/root/azazael"),
    "G:":      Path("G:/AZAZAEL_SOURCE_OF_TRUTH"),
}


def hash_file(filepath: Path) -> str:
    """Compute SHA-256 hash of a file. Returns hex digest."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            sha.update(chunk)
    return sha.hexdigest()


def get_master_hashes() -> dict[str, str]:
    """Compute hashes for all canonical docs on the W11 master."""
    hashes: dict[str, str] = {}
    for doc in CANONICAL_DOCS:
        path = MASTER_ROOT / doc
        if path.exists():
            hashes[doc] = hash_file(path)
        else:
            hashes[doc] = "MISSING"
    return hashes


def get_pillar_state(pillar_path: Path) -> dict[str, Any]:
    """Get the state of all canonical docs on a pillar.

    Returns dict mapping doc name -> {exists, size, lines, hash, status}
    """
    state: dict[str, Any] = {}
    for doc in CANONICAL_DOCS:
        path = pillar_path / doc
        if path.exists():
            state[doc] = {
                "exists": True,
                "size": path.stat().st_size,
                "hash": hash_file(path),
            }
        else:
            state[doc] = {"exists": False, "size": 0, "hash": "MISSING"}
    return state


def _is_wsl_path(path: Path) -> bool:
    """Check if a path is a WSL UNC path (//wsl$/...)."""
    return str(path).startswith("//wsl$") or str(path).startswith(r"\\wsl$")


def _wsl_distro_running(name: str) -> bool | None:
    """Quick check if a WSL distro is Running via wsl.exe --list.

    Returns:
        True if distro is Running
        False if distro is Stopped or not found
        None if wsl.exe is unavailable (not a WSL environment)
    """
    try:
        result = subprocess.run(
            ["wsl.exe", "--list", "--verbose"],
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace"
        )
        for line in result.stdout.splitlines():
            if name.lower() in line.lower():
                return "running" in line.lower()
        return False  # Distro not found in list
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        note_failure("sync_canonical_docs:L123", exc)
        return None  # wsl.exe unavailable or timeout — can't determine


def verify_pillar(pillar_name: str, pillar_path: Path,
                  master_hashes: dict[str, str]) -> tuple[bool, list[str]]:
    """Verify a pillar's canonical docs match the master.

    Returns (all_match, list_of_mismatched_docs).
    Handles WSL UNC paths gracefully when distros are stopped.
    """
    if not pillar_path.exists():
        # Provide actionable guidance for WSL paths
        if _is_wsl_path(pillar_path):
            is_stopped = _wsl_distro_running(pillar_name) is False
            if is_stopped:
                detail = "WSL distro is currently stopped"
            else:
                detail = "WSL path unreachable (distro may not be installed)"
            return False, [
                f"{pillar_name}: PILLAR UNREACHABLE — {detail}"
                f" (path: {pillar_path})",
                f"  ℹ Run 'wsl -d {pillar_name}' to start it, or use --sync when Running"
            ]
        return False, [f"{pillar_name}: PILLAR UNREACHABLE (path: {pillar_path})"]

    mismatches: list[str] = []
    for doc in CANONICAL_DOCS:
        path = pillar_path / doc
        master_hash = master_hashes.get(doc, "MISSING")

        if not path.exists():
            mismatches.append(f"{pillar_name}/{doc}: MISSING (master hash: {master_hash[:16]}...)")
        else:
            pillar_hash = hash_file(path)
            if pillar_hash != master_hash:
                size_diff = path.stat().st_size - (MASTER_ROOT / doc).stat().st_size
                mismatches.append(
                    f"{pillar_name}/{doc}: HASH MISMATCH "
                    f"(size delta: {size_diff:+,} bytes, "
                    f"pillar: {pillar_hash[:16]}..., master: {master_hash[:16]}...)"
                )

    return (len(mismatches) == 0), mismatches


def sync_pillar(pillar_name: str, pillar_path: Path,
                master_hashes: dict[str, str], dry_run: bool = True) -> list[str]:
    """Sync canonical docs from master to a pillar.

    Args:
        dry_run: If True, report what would be done without doing it.

    Returns list of sync actions taken.
    """
    actions: list[str] = []

    if not pillar_path.exists():
        if _is_wsl_path(pillar_path):
            actions.append(f"{pillar_name}: SKIPPED — WSL distro unreachable")
            actions.append(f"  ⚠ Start WSL: 'wsl -d {pillar_name}' then retry --sync")
        else:
            actions.append(f"{pillar_name}: SKIPPED — pillar unreachable (path: {pillar_path})")
        return actions

    for doc in CANONICAL_DOCS:
        src = MASTER_ROOT / doc
        dst = pillar_path / doc
        master_hash = master_hashes.get(doc, "MISSING")

        if not src.exists():
            actions.append(f"{pillar_name}/{doc}: SKIPPED — missing from master")
            continue

        if not dst.exists():
            action = f"{pillar_name}/{doc}: SYNC (new file, {src.stat().st_size:,} bytes)"
        else:
            dst_hash = hash_file(dst)
            if dst_hash == master_hash:
                continue  # Already in sync
            size_delta = src.stat().st_size - dst.stat().st_size
            action = f"{pillar_name}/{doc}: SYNC (drift: {size_delta:+,} bytes)"

        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            action += " [DONE]"

        actions.append(action)

    return actions


def print_drift_report(master_hashes: dict[str, str]):
    """Print a comprehensive drift report for all pillars."""
    print("=" * 70)
    print("  CANONICAL DOCUMENT DRIFT REPORT")
    print(f"  Master: {MASTER_ROOT}")
    print(f"  Time:   {datetime.now(timezone.utc).isoformat(timespec='seconds')}Z")
    print("=" * 70)

    # Master state
    print(f"\n  MASTER (W11):")
    for doc in CANONICAL_DOCS:
        h = master_hashes.get(doc, "MISSING")
        path = MASTER_ROOT / doc
        if path.exists():
            print(f"    {doc}: {path.stat().st_size:,} bytes  SHA256:{h[:16]}...")
        else:
            print(f"    {doc}: MISSING")

    # Pillar states
    total_mismatches = 0
    for name, path in TARGET_PILLARS.items():
        all_match, mismatches = verify_pillar(name, path, master_hashes)
        if not path.exists():
            print(f"\n  {name}: UNREACHABLE")
        elif all_match:
            print(f"\n  {name}: IN SYNC")
        else:
            print(f"\n  {name}: DRIFT DETECTED ({len(mismatches)} docs)")
            for m in mismatches:
                print(f"    {m}")
            total_mismatches += len(mismatches)

    print("\n" + "-" * 70)
    if total_mismatches == 0:
        print("  RESULT: ALL PILLARS IN SYNC")
    else:
        print(f"  RESULT: {total_mismatches} document(s) out of sync")
        print("  ACTION: Run with --sync to propagate master to all pillars")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Canonical Document Sync — RULE 0Z-J enforcement"
    )
    parser.add_argument("--sync", action="store_true",
                        help="Sync ALL pillars to W11 master (writes files)")
    parser.add_argument("--verify", action="store_true",
                        help="Verify all pillars match W11 (exit 1 on drift)")
    parser.add_argument("--hash", action="store_true",
                        help="Print W11 master document hashes")
    parser.add_argument("--pillar", type=str,
                        help="Sync/verify only a specific pillar (KALI, UBUNTU, G:)")
    parser.add_argument("--drift-report", action="store_true",
                        help="Print drift report (default behavior, explicit flag)")
    parser.add_argument("--self-test", action="store_true",
                        help="Run internal consistency checks")
    args = parser.parse_args()

    if args.self_test:
        print("Running self-test...")
        master_hashes = get_master_hashes()
        w11_ok = all(h != "MISSING" for h in master_hashes.values())
        print(f"  W11 master: {len(master_hashes)} docs, {sum(1 for h in master_hashes.values() if h != 'MISSING')} present")
        for doc, h in master_hashes.items():
            status = "OK" if h != "MISSING" else "MISSING"
            print(f"    {doc}: {status}")
        print(f"  Targets: {len(TARGET_PILLARS)} pillars defined")
        for name, path in TARGET_PILLARS.items():
            reachable = path.exists()
            status = "REACHABLE" if reachable else "UNREACHABLE"
            print(f"    {name}: {path} — {status}")
        print("\nSelf-test complete.")
        sys.exit(0 if w11_ok else 1)

    master_hashes = get_master_hashes()

    if args.hash:
        print("W11 CANONICAL DOCUMENT HASHES (SHA-256):")
        for doc, h in master_hashes.items():
            print(f"  {doc}: {h}")
        return

    if args.sync:
        targets = TARGET_PILLARS
        if args.pillar:
            targets = {args.pillar: TARGET_PILLARS[args.pillar]}

        print("=" * 70)
        print("  SYNCING CANONICAL DOCUMENTS TO PILLARS")
        print("=" * 70)

        for name, path in targets.items():
            actions = sync_pillar(name, path, master_hashes, dry_run=False)
            for a in actions:
                print(f"  {a}")

        print("\n" + "=" * 70)
        print("  SYNC COMPLETE")
        print("=" * 70)

        # Verify after sync
        print("\n  Post-sync verification:")
        for name, path in targets.items():
            all_match, mismatches = verify_pillar(name, path, master_hashes)
            if all_match:
                print(f"    {name}: VERIFIED (in sync)")
            else:
                for m in mismatches:
                    print(f"    {m}")
        return

    if args.verify:
        exit_code = 0
        targets = TARGET_PILLARS
        if args.pillar:
            targets = {args.pillar: TARGET_PILLARS[args.pillar]}

        for name, path in targets.items():
            all_match, mismatches = verify_pillar(name, path, master_hashes)
            if all_match:
                print(f"{name}: VERIFIED (in sync)")
            else:
                print(f"{name}: DRIFT ({len(mismatches)} docs)")
                for m in mismatches:
                    print(f"  {m}")
                exit_code = 1

        sys.exit(exit_code)

    # Default: drift report
    if args.drift_report:
        print_drift_report(master_hashes)
        return

    print_drift_report(master_hashes)


if __name__ == "__main__":
    main()
