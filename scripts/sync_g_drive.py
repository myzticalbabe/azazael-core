#!/usr/bin/env python
"""
sync_g_drive.py — Full source tree sync from azazael/ to G:/source-of-truth/azazael/

Uses robocopy for fast, resumable Windows-to-G: sync. Handles the slow
G: drive (SD card) by using /R:1 /W:1 retry settings.

Closes the 1,633 file gap between AZAZAEL_ROOT and source-of-truth.

Usage:
    python scripts/sync_g_drive.py --status     Show file counts and drift
    python scripts/sync_g_drive.py --sync       Sync all source files
    python scripts/sync_g_drive.py --sync --dry-run  Preview what would sync
    python scripts/sync_g_drive.py --verify     Check sync integrity
    python scripts/sync_g_drive.py --sync --mirror  Full mirror (WARNING: deletes extras)
    python scripts/sync_g_drive.py --sync --force  Force full re-sync (clear checkpoint)

Created: June 7, 2026 — G: drive sync automation
"""

import os
import sys
import json
import subprocess
import shutil
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from CORE.paths import get_project_root_safe  # noqa: E402

# Ensure UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Configuration ──────────────────────────────────────────────────

# Repo root via the canonical resolver, NOT `Path.home() / "azazael"`: that named a
# DIFFERENT, stale checkout, so with AZAZAEL_ROOT unset this synced the WRONG tree to
# G:. get_project_root_safe() honours AZAZAEL_ROOT first, never raises, and on a
# sentinel failure returns the tree it ships in.
# Guard: tests/test_no_stale_root_fallback.py (scan roots src/ + scripts/).
SOURCE_ROOT = Path(get_project_root_safe())
G_SOURCE_OF_TRUTH = Path("G:/source-of-truth/azazael")

# Checkpoint file for resume tracking
CHECKPOINT_FILE = Path.home() / ".azazael" / "g_sync_checkpoint.json"

# Directories to sync (relative to azazael root)
# Note: large dirs (src/, tests/) are chunked per sub-directory
SYNC_DIRS = [
    "docs/",          # Documentation
    "scripts/",       # Build/deploy scripts
    "skills/",        # Skill definitions (raw .md files)
    ".agents/",       # Pre-loaded agent skills
    "packages/",      # Package scaffolds
    "CLI/",           # CLI tools
]

# Large directories that get chunked into sub-directory syncs
LARGE_DIRS = [
    "src/",
    "tests/",
]

# Individual files to sync
SYNC_FILES = [
    "MEMORIES.md",
    "MEMORIES_PLATFORM.md",
    "ERROR_LOG.md",
    "SYSTEM_INSTRUCTIONS.md",
    "DIRECT_MEMORY_INSTRUCTIONS.md",
    "CODEBASE_AUDIT_TODO.md",
    "SKILLS_CATALOG.md",
    "LESSONS_MASTER.md",
    "LESSONS_DEV_TOOLS.md",
    "LESSONS_DESTRUCTIVE_BEHAVIOR.md",
    "LESSONS_NEURAL_ADVANCEMENT.md",
    "MASTER_TASKS.md",
    "PROJECT_ALIGNMENT.md",
    "PROJECT_STRUCTURES.md",
    "NATIVE_INTELLIGENCE_ROADMAP.md",
    "PLATFORM_DEPLOYMENT.md",
    "RESEARCH_AI_ML_RL_PROTOCOL_EVOLUTION.md",
    "azazael.yaml",
    "pyproject.toml",
    "requirements-3.14t.txt",
]

def get_file_count(path: Path, pattern: str = "*") -> int:
    """Count files matching pattern in path recursively."""
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob(pattern))


def get_dir_size(path: Path) -> int:
    """Get total size in bytes of all files in path."""
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def format_size(bytes_val: int) -> str:
    """Format bytes in human-readable form."""
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


# ── Checkpoint Tracking for Resume ─────────────────────────────────

def load_checkpoint() -> Dict[str, Any]:
    """Load sync checkpoint from disk. Returns dict of completed chunks.

    Hardened against Edge Case 4: if the checkpoint file contains valid
    JSON that is NOT a dict (e.g. list, string, number, null), returns {}
    instead of propagating a non-dict value that would cause TypeError
    downstream (e.g. cp[chunk_key] in mark_chunk_complete, or key in cp
    in is_chunk_complete).
    """
    if not CHECKPOINT_FILE.exists():
        return {}
    try:
        data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_checkpoint(completed: Dict[str, Any]):
    """Save sync checkpoint to disk."""
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        CHECKPOINT_FILE.write_text(json.dumps(completed, indent=2), encoding="utf-8")
    except OSError:
        pass


def mark_chunk_complete(chunk_key: str, detail: Dict[str, Any]):
    """Mark a chunk as completed in the checkpoint."""
    cp = load_checkpoint()
    cp[chunk_key] = {"completed_at": datetime.now().isoformat(), **detail}
    save_checkpoint(cp)


def is_chunk_complete(chunk_key: str) -> bool:
    """Check if a chunk was already completed."""
    cp = load_checkpoint()
    return chunk_key in cp


def clear_checkpoint():
    """Clear all checkpoints (for full re-sync)."""
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()


# ── Robocopy with Chunking ──────────────────────────────────────────

def robocopy(src: Path, dst: Path, extra_flags: str = "",
             dry_run: bool = False, timeout: int = 300) -> Tuple[int, str]:
    """Run robocopy with retry settings for slow G: drive.

    Uses shorter per-chunk timeout (default 300s = 5 min) so that
    individual sub-directory syncs don't block the entire operation.

    Returns (return_code, output_summary).
    robocopy return codes: 0-7 = success (0=nothing copied, 1=files copied)
    """
    flags = ["/E", "/NP", "/R:1", "/W:1", "/MT:4"]
    if dry_run:
        flags.append("/L")
    if extra_flags:
        flags.extend(extra_flags.split())

    cmd = ["robocopy", str(src), str(dst)] + flags
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
        )
        # robocopy return codes 0-7 are success
        if result.returncode > 7:
            return result.returncode, result.stderr[:500]
        # Extract summary lines (last ~8 lines of output)
        lines = result.stdout.strip().split("\n")
        summary_lines = [l for l in lines[-12:] if l.strip()]
        return result.returncode, "\n".join(summary_lines)
    except subprocess.TimeoutExpired:
        return -1, f"TIMEOUT: robocopy of {src.name} exceeded {timeout}s"
    except FileNotFoundError:
        return -1, "ERROR: robocopy not found in PATH"


def get_status() -> Dict:
    """Get sync status: file counts, sizes, drift."""
    src_py = get_file_count(SOURCE_ROOT / "src", "*.py")
    g_py = get_file_count(G_SOURCE_OF_TRUTH / "src", "*.py") if G_SOURCE_OF_TRUTH.exists() else 0

    src_total = get_file_count(SOURCE_ROOT, "*")
    g_total = get_file_count(G_SOURCE_OF_TRUTH, "*") if G_SOURCE_OF_TRUTH.exists() else 0

    src_size = get_dir_size(SOURCE_ROOT)
    g_size = get_dir_size(G_SOURCE_OF_TRUTH) if G_SOURCE_OF_TRUTH.exists() else 0

    drift = src_total - g_total

    return {
        "g_available": G_SOURCE_OF_TRUTH.exists(),
        "source_py_files": src_py,
        "g_py_files": g_py,
        "source_total_files": src_total,
        "g_total_files": g_total,
        "source_size": src_size,
        "g_size": g_size,
        "drift_files": drift,
        "drift_size": src_size - g_size,
    }


def sync_directory(rel_dir: str, dry_run: bool = False) -> Dict:
    """Sync a single directory using robocopy."""
    src = SOURCE_ROOT / rel_dir.rstrip("/")
    dst = G_SOURCE_OF_TRUTH / rel_dir.rstrip("/")

    if not src.exists():
        return {"dir": rel_dir, "status": "skipped", "reason": "source missing"}

    dst.mkdir(parents=True, exist_ok=True)
    rc, output = robocopy(src, dst, dry_run=dry_run, timeout=600)
    status = "success" if rc <= 7 else "failed"
    return {
        "dir": rel_dir,
        "status": status,
        "rc": rc,
        "output": output[:300],
    }


def sync_large_directory_chunked(rel_dir: str, dry_run: bool = False) -> List[Dict]:
    """Sync a large directory by chunking into sub-directory syncs with resume.

    Each immediate sub-directory is synced independently with its own
    timeout and checkpoint tracking. This prevents the entire operation
    from failing if one sub-directory times out on the slow G: drive.

    On resume (re-run), already-completed chunks are skipped.
    """
    results = []
    src_base = SOURCE_ROOT / rel_dir.rstrip("/")
    dst_base = G_SOURCE_OF_TRUTH / rel_dir.rstrip("/")

    if not src_base.exists():
        results.append({"dir": rel_dir, "status": "skipped", "reason": "source missing"})
        return results

    dst_base.mkdir(parents=True, exist_ok=True)

    # Collect all immediate sub-dirs + files at root
    chunks = []
    for item in sorted(src_base.iterdir()):
        if item.name.startswith("."):
            continue
        if item.name == "__pycache__":
            continue
        chunks.append(item.name)

    total_chunks = len(chunks)
    for i, chunk_name in enumerate(chunks):
        chunk_key = f"{rel_dir}/{chunk_name}"

        # Resume: skip if already completed
        if not dry_run and is_chunk_complete(chunk_key):
            results.append({
                "dir": chunk_key,
                "status": "resumed",
                "skipped": True,
            })
            continue

        src_chunk = src_base / chunk_name
        dst_chunk = dst_base / chunk_name

        # Only create directory for sub-directory chunks, not files
        # mkdir on a file path fails when a file already exists there (e.g. on G: drive)
        if src_chunk.is_dir():
            dst_chunk.mkdir(parents=True, exist_ok=True)

        print(f"  [{i+1}/{total_chunks}] Syncing {chunk_key}...")

        if src_chunk.is_dir():
            rc, output = robocopy(src_chunk, dst_chunk, dry_run=dry_run, timeout=300)
        else:
            # Single file at root level — parent dir already exists from dst_base.mkdir
            if dry_run:
                rc, output = 1, "[DRY RUN] Would copy file"
            else:
                try:
                    shutil.copy2(str(src_chunk), str(dst_chunk))
                    rc, output = 1, "File copied"
                except OSError as e:
                    rc, output = 16, str(e)

        status = "success" if rc <= 7 else "failed"
        chunk_result = {
            "dir": chunk_key,
            "status": status,
            "rc": rc,
            "output": output[:200],
        }
        results.append(chunk_result)

        # Mark checkpoint on success
        if status == "success" and not dry_run:
            mark_chunk_complete(chunk_key, {"rc": rc})

    return results


def sync_files(dry_run: bool = False, use_checkpoint: bool = True) -> List[Dict]:
    """Sync individual tracked files with optional checkpoint resume."""
    results = []
    for filename in SYNC_FILES:
        chunk_key = f"_files/{filename}"

        # Resume: skip if already completed
        if use_checkpoint and not dry_run and is_chunk_complete(chunk_key):
            results.append({"file": filename, "status": "resumed", "skipped": True})
            continue

        src = SOURCE_ROOT / filename
        dst = G_SOURCE_OF_TRUTH / filename
        if not src.exists():
            results.append({"file": filename, "status": "skipped", "reason": "source missing"})
            continue

        if dry_run:
            needs_sync = not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime
            results.append({
                "file": filename,
                "status": "would_sync" if needs_sync else "up_to_date",
            })
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            results.append({"file": filename, "status": "synced"})
            mark_chunk_complete(chunk_key, {"mtime": src.stat().st_mtime})
        except OSError as e:
            results.append({"file": filename, "status": "failed", "reason": str(e)[:100]})

    return results


def verify_sync() -> Dict:
    """Verify that key files exist and are up to date on G: drive."""
    results = {}
    checks = [
        "src/CORE/direct_memory.py",
        "src/CORE/local_memory.py",
        "src/CORE/paths.py",
        "SYSTEM_INSTRUCTIONS.md",
        "DIRECT_MEMORY_INSTRUCTIONS.md",
        "CODEBASE_AUDIT_TODO.md",
    ]
    for rel_path in checks:
        src = SOURCE_ROOT / rel_path
        dst = G_SOURCE_OF_TRUTH / rel_path
        if not dst.exists():
            results[rel_path] = "MISSING"
        elif not src.exists():
            results[rel_path] = "SOURCE_MISSING"
        elif src.stat().st_mtime <= dst.stat().st_mtime:
            results[rel_path] = "OK"
        else:
            results[rel_path] = "STALE"
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Azazael G: Drive Sync — Full source tree sync to source-of-truth"
    )
    parser.add_argument("--status", action="store_true",
                        help="Show file counts and drift")
    parser.add_argument("--sync", action="store_true",
                        help="Sync all source files to G: drive")
    parser.add_argument("--verify", action="store_true",
                        help="Verify sync integrity")
    parser.add_argument("--mirror", action="store_true",
                        help="Full mirror (deletes extras on G:)")
    parser.add_argument("--force", action="store_true",
                        help="Force full re-sync by clearing checkpoint")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be synced")
    parser.add_argument("--force-test", action="store_true",
                        help="Clear checkpoint and show local-only status (no G: drive access)")

    args = parser.parse_args()

    if not (args.status or args.sync or args.verify or args.force_test):
        parser.print_help()
        return

    ts = datetime.now().isoformat()
    print(f"Azazael G: Drive Sync — {ts}")
    print(f"  Source: {SOURCE_ROOT}")
    print(f"  Target: {G_SOURCE_OF_TRUTH}")
    print()

    # --force-test mode: clear checkpoint + show local-only status (no G: drive access)
    if args.force_test:
        print("  [!!] --force-test: Clearing checkpoint (test mode, no G: drive access)")
        clear_checkpoint()
        print()
        src_py = get_file_count(SOURCE_ROOT / "src", "*.py")
        src_total = get_file_count(SOURCE_ROOT, "*")
        src_size = get_dir_size(SOURCE_ROOT)
        print("=== STATUS (local only) ===")
        print(f"  Source .py:   {src_py}")
        print(f"  Source total: {src_total} files ({format_size(src_size)})")
        print(f"  G: drive:     NOT ACCESSED (test mode)")
        return

    if args.force and args.sync:
        print("  [!!] --force: Clearing checkpoint for full re-sync")
        clear_checkpoint()
        print()

    if args.status:
        s = get_status()
        print("=== STATUS ===")
        print(f"  G: available: {s['g_available']}")
        print(f"  Source .py:   {s['source_py_files']}")
        print(f"  G: .py:       {s['g_py_files']}")
        print(f"  Source total: {s['source_total_files']} files ({format_size(s['source_size'])})")
        if s['g_available']:
            print(f"  G: total:     {s['g_total_files']} files ({format_size(s['g_size'])})")
        print(f"  Drift:        {s['drift_files']} files ({format_size(abs(s['drift_size']))})")
        return

    if args.verify:
        v = verify_sync()
        all_ok = all(x == "OK" for x in v.values())
        print(f"=== VERIFY: {'ALL OK' if all_ok else 'ISSUES FOUND'} ===")
        for path, status in v.items():
            icon = "OK" if status == "OK" else "!!" if status == "MISSING" else "??"
            print(f"  [{icon}] {path}: {status}")
        return

    if args.sync:
        mirror_flag = " /MIR" if args.mirror else ""
        print(f"=== SYNC{' (DRY RUN)' if args.dry_run else ''}{' (MIRROR)' if args.mirror else ''} ===")

        if args.dry_run:
            # Dry run: skip checkpoint resume
            clear_checkpoint()

        # Sync small directories (whole-dir robocopy)
        for rel_dir in SYNC_DIRS:
            r = sync_directory(rel_dir, dry_run=args.dry_run)
            icon = "OK" if r["status"] == "success" else "XX" if r["status"] == "failed" else "--"
            print(f"  [{icon}] {r['dir']}: {r['status']}")

        # Sync large directories chunked with resume
        for rel_dir in LARGE_DIRS:
            print(f"\n  Syncing {rel_dir} (chunked)...")
            chunk_results = sync_large_directory_chunked(rel_dir, dry_run=args.dry_run)
            ok = sum(1 for r in chunk_results if r["status"] in ("success", "resumed"))
            fail = sum(1 for r in chunk_results if r["status"] == "failed")
            print(f"    Chunks: {ok} ok, {fail} failed")
            for r in chunk_results:
                if r["status"] == "failed":
                    print(f"      FAIL: {r['dir']} (rc={r.get('rc', '?')})")

        # Sync individual files
        file_results = sync_files(dry_run=args.dry_run)
        synced = sum(1 for r in file_results if r["status"] in ("synced", "would_sync"))
        resumed = sum(1 for r in file_results if r["status"] == "resumed")
        skipped = sum(1 for r in file_results if r["status"] == "up_to_date")
        failed = sum(1 for r in file_results if r["status"] == "failed")
        print(f"\n  Files: {synced} synced, {resumed} resumed, {skipped} up-to-date, {failed} failed")

        # Sync memory store DB
        db_src = Path.home() / ".azazael" / "memory_store.db"
        db_dst = Path("G:/source-of-truth/persistent-ai-memory/memory_data/")
        if db_src.exists():
            db_dst.mkdir(parents=True, exist_ok=True)
            if not args.dry_run:
                # Create timestamped backup
                ts_file = f"memory_store_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                shutil.copy2(db_src, db_dst / ts_file)
                # Also update latest
                shutil.copy2(db_src, db_dst / "memory_store_latest.db")
                print(f"  [OK] memory_store.db -> {db_dst / ts_file}")
            else:
                print(f"  [>>] memory_store.db -> {db_dst}")

        if not args.dry_run:
            # Write sync timestamp
            ts_file = G_SOURCE_OF_TRUTH / ".last_sync"
            ts_file.write_text(f"Synced: {ts}\nSource: {SOURCE_ROOT}\n", encoding="utf-8")
            # Clear checkpoint on full success so future syncs detect changes
            if failed == 0:
                clear_checkpoint()
            else:
                print(f"\n  Checkpoint saved. Run --sync again to resume from where it stopped.")
            print(f"\n  Sync complete at {ts}")

        if args.mirror and not args.dry_run:
            print("\n  WARNING: --mirror was used. Extras on G: have been deleted.")


if __name__ == "__main__":
    main()