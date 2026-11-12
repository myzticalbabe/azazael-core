"""auto_sync.py — RULE 0R-A polling daemon for automatic skill synchronization.

Watches azazael/skills/ for file changes and triggers sync_skills.py
within 5 seconds. Runs as a background polling daemon.

Usage:
    python auto_sync.py              # Single poll + sync
    python auto_sync.py --status     # Show daemon status and skill counts
    python auto_sync.py --watch      # Continuous polling (Ctrl+C to stop)
    python auto_sync.py --watch --interval 3  # Custom interval (default 5s)
"""

import hashlib
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Dict, Optional  # removed — all names have native equivalents


_EXIT_REQUESTED = False
_LAST_SYNC_TIME: (str | None) = None
_SYNC_COUNT = 0


def _handle_sigterm(signum, frame):
    global _EXIT_REQUESTED
    _EXIT_REQUESTED = True


signal.signal(signal.SIGTERM, _handle_sigterm)



SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
SYNC_SCRIPT = Path(__file__).resolve().parent / "sync_skills.py"


def compute_hashes(directory: Path) -> dict[str, str]:
    """Compute MD5 hashes for all .md files in a directory."""
    hashes = {}
    if not directory.exists():
        return hashes
    for fpath in directory.rglob("*.md"):
        try:
            content = fpath.read_bytes()
            hashes[str(fpath.relative_to(directory))] = hashlib.md5(content).hexdigest()
        except OSError as exc:
            note_failure("auto_sync:L50", exc)
            continue
    return hashes


def detect_changes(old: dict[str, str], new: dict[str, str]) -> list:
    """Return list of changed/new/deleted files."""
    changes = []
    for key in new:
        if key not in old:
            changes.append(f"+ {key} (new)")
        elif old[key] != new[key]:
            changes.append(f"~ {key} (modified)")
    for key in old:
        if key not in new:
            changes.append(f"- {key} (deleted)")
    return changes


def get_skill_count() -> int:
    """Return the number of .md skill files in the skills directory."""
    return len(compute_hashes(SKILLS_DIR))


def run_sync() -> bool:
    """Run sync_skills.py and return True if successful."""
    global _LAST_SYNC_TIME, _SYNC_COUNT
    if not SYNC_SCRIPT.exists():
        print(f"[auto_sync] ERROR: {SYNC_SCRIPT} not found")
        return False
    try:
        result = subprocess.run(
            [sys.executable, str(SYNC_SCRIPT)],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            _LAST_SYNC_TIME = time.strftime("%Y-%m-%d %H:%M:%S")
            _SYNC_COUNT += 1
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[auto_sync] Error running sync: {e}")
        return False



def poll_once(verbose: bool = True) -> bool:
    """Poll skills directory, sync if changes detected. Returns True if sync ran."""
    global _last_hashes
    _last_hashes = _ensure_hashes_warmed()
    current = compute_hashes(SKILLS_DIR)
    changes = detect_changes(_last_hashes, current)
    if changes:
        if verbose:
            print(f"[auto_sync] Detected {len(changes)} change(s):")
            for c in changes:
                print(f"  {c}")
        ok = run_sync()
        if ok and verbose:
            print(f"[auto_sync] Sync complete ({len(current)} skills)")
        _last_hashes = current
        return ok
    return True  # No changes = success


_last_hashes: dict[str, str] = {}


def _ensure_hashes_warmed() -> dict[str, str]:
    """Return the current hashes, warming the cache on first call."""
    global _last_hashes
    if not _last_hashes:
        _last_hashes = compute_hashes(SKILLS_DIR)
    return _last_hashes


def watch_loop(interval: int = 5, quiet: bool = False, watchdog: bool = False):
    """Continuous polling loop. Ctrl+C or SIGTERM to stop.

    Args:
        interval: Polling interval in seconds (default: 5)
        quiet: If True, suppress non-error output
        watchdog: If True, restart on crash (max 5 consecutive restarts)
    """
    global _EXIT_REQUESTED
    if not quiet:
        print(f"[auto_sync] Watching {SKILLS_DIR} every {interval}s. Ctrl+C to stop.")
        if watchdog:
            print("[auto_sync] Watchdog mode: max 5 restart attempts before giving up")

    restart_count = 0
    max_restarts = 5

    while not _EXIT_REQUESTED:
        try:
            poll_once(verbose=not quiet)
            restart_count = 0  # Reset counter on successful poll
            for _ in range(interval):
                if _EXIT_REQUESTED:
                    break
                time.sleep(1)
        except KeyboardInterrupt as exc:
            note_failure("auto_sync:L150", exc)
            break
        except Exception as e:
            if not quiet:
                print(f"[auto_sync] Watchdog caught: {e}")
            if not watchdog:
                raise
            restart_count += 1
            if restart_count > max_restarts:
                if not quiet:
                    print(f"[auto_sync] Too many restarts ({restart_count}). Exiting.")
                break
            if not quiet:
                print(f"[auto_sync] Restarting... (attempt {restart_count}/{max_restarts})")
            time.sleep(2 ** restart_count)  # Exponential backoff

    if not quiet:
        print("[auto_sync] Stopped")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RULE 0R-A auto-sync daemon")
    parser.add_argument(
        "--watch", action="store_true",
        help="Continuous polling mode (default: single poll)"
    )
    parser.add_argument(
        "--interval", type=int, default=5,
        help="Polling interval in seconds (default: 5)"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress non-error output (for daemon mode)"
    )
    parser.add_argument(
        "--watchdog", action="store_true",
        help="Auto-restart on crash (only with --watch)"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show daemon status: skill count, last sync time, sync count"
    )
    parser.add_argument(
        "--update-skills", action="store_true",
        help="Force sync all skills even if no changes detected"
    )
    parser.add_argument(
        "--telemetry", action="store_true",
        help="Write sync metrics to telemetry.json (compatible with RULE 0Z-F dashboard)"
    )
    args = parser.parse_args()

    if args.status:
        skill_count = get_skill_count()
        status = {
            "status": "idle" if _LAST_SYNC_TIME is None else "synced",
            "skills_directory": str(SKILLS_DIR),
            "skill_files": skill_count,
            "last_sync": _LAST_SYNC_TIME or "never",
            "total_syncs": _SYNC_COUNT,
        }
        print(json.dumps(status, indent=2))
        sys.exit(0)

    if args.update_skills:
        if not args.quiet:
            print("[auto_sync] Force-syncing all skills...")
        ok = run_sync()
        if ok:
            # Refresh hash cache
            _last_hashes = compute_hashes(SKILLS_DIR)
            if not args.quiet:
                print(f"[auto_sync] Sync complete ({len(_last_hashes)} skills)")
        sys.exit(0 if ok else 1)

    if args.telemetry:
        # Run sync and export telemetry metrics to telemetry.json
        _last_hashes = compute_hashes(SKILLS_DIR)
        ok = poll_once(verbose=not args.quiet)
        telemetry_data = {
            "status": "synced" if ok else "failed",
            "skills_directory": str(SKILLS_DIR),
            "skill_files": len(_last_hashes),
            "last_sync": _LAST_SYNC_TIME or "never",
            "total_syncs": _SYNC_COUNT,
            "sync_ok": ok,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        telemetry_path = Path(__file__).resolve().parent / "telemetry.json"
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        telemetry_path.write_text(json.dumps(telemetry_data, indent=2), encoding="utf-8")
        if not args.quiet:
            print(f"[auto_sync] Telemetry written to {telemetry_path}")
        sys.exit(0 if ok else 1)

    # Warm the hash cache
    _last_hashes = compute_hashes(SKILLS_DIR)
    if not args.quiet:
        print(f"[auto_sync] Current skills: {len(_last_hashes)} files")

    if args.watch:
        watch_loop(args.interval, quiet=args.quiet, watchdog=args.watchdog)
    else:
        ok = poll_once(verbose=not args.quiet)
        sys.exit(0 if ok else 1)
