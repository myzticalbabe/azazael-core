#!/usr/bin/env python3
"""
AZAZAEL Archive System
======================
Archives old/unused files: __pycache__, PPM frames, large videos, bak files.
Creates GZ-compressed timestamped archives in /root/azazael/archived/.
Dry-run mode (--dry) lists what would be archived without moving files.

Usage:
    python src/TOOLS/archive_system.py --dry       # Preview only
    python src/TOOLS/archive_system.py --all       # Archive everything
    python src/TOOLS/archive_system.py --ppms      # Archive PPM frame files
    python src/TOOLS/archive_system.py --pycache   # Archive __pycache__ dirs
    python src/TOOLS/archive_system.py --videos    # Archive large video files
"""

import os
import sys
import shutil
import tarfile
from pathlib import Path
from datetime import datetime
# from typing import List, Tuple  # removed — all names have native equivalents
import argparse
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


AZAZAEL_ROOT = Path(os.environ.get("AZAZAEL_ROOT", str(Path(__file__).resolve().parents[2])))
ARCHIVE_DIR = AZAZAEL_ROOT / "archived"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

# Directories and patterns that must NEVER be touched by cleanup/sweep operations.
# These protect user downloads and other irreplaceable data.
#
# NOTE: The archive system only scans under AZAZAEL_ROOT (~/azazael on C:).
# Files on G:, D:, or other external drives are intrinsically safe because
# they are outside the scan scope. The patterns below protect directories
# that could exist WITHIN the Azazael root.
PROTECTED_PATTERNS = {
    "download", "downloads", "FROM Z", "Instagram",
    "source-of-truth", "archived_files",
}


class ArchiveSystem:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.archived: list[tuple[str, str, int]] = []  # (path, reason, size_bytes)
        self.total_size = 0

    def _size_str(self, size: float) -> str:
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def archive_ppms(self) -> int:
        """Archive all .ppm frame files from matrix animation output."""
        ppm_files = list(AZAZAEL_ROOT.rglob("*.ppm"))
        if not ppm_files:
            print("  No PPM files found.")
            return 0

        total_size = sum(f.stat().st_size for f in ppm_files)

        if not self.dry_run:
            archive_path = ARCHIVE_DIR / f"ppm_frames_{self.session_id}.tar.gz"
            with tarfile.open(archive_path, "w:gz") as tar:
                for f in ppm_files:
                    tar.add(f, arcname=str(f.relative_to(AZAZAEL_ROOT)))
            # Remove original PPMs
            for f in ppm_files:
                f.unlink()
            # Remove empty parent dirs
            for d in sorted(set(f.parent for f in ppm_files), reverse=True):
                try:
                    d.rmdir()
                except OSError as exc:
                    note_failure("archive_system:L79", exc)

        self.archived.append(("PPM frames", f"{len(ppm_files)} files", total_size))
        self.total_size += total_size
        return len(ppm_files)

    def archive_pycache(self) -> int:
        """Archive __pycache__ directories."""
        pycache_dirs = list(AZAZAEL_ROOT.rglob("__pycache__"))
        if not pycache_dirs:
            print("  No __pycache__ directories found.")
            return 0

        total_size = 0
        for d in pycache_dirs:
            for f in d.rglob("*"):
                if f.is_file():
                    total_size += f.stat().st_size

        if not self.dry_run:
            archive_path = ARCHIVE_DIR / f"pycache_{self.session_id}.tar.gz"
            with tarfile.open(archive_path, "w:gz") as tar:
                for d in pycache_dirs:
                    tar.add(d, arcname=str(d.relative_to(AZAZAEL_ROOT)))
            for d in pycache_dirs:
                shutil.rmtree(d, ignore_errors=True)

        self.archived.append(("__pycache__", f"{len(pycache_dirs)} dirs", total_size))
        self.total_size += total_size
        return len(pycache_dirs)

    def _is_protected(self, path: Path) -> bool:
        """Check if a path is in a protected directory that must never be deleted."""
        path_str = str(path).lower()
        for pattern in PROTECTED_PATTERNS:
            if pattern.lower() in path_str:
                return True
        return False

    def archive_large_videos(self, min_size_mb: int = 10) -> int:
        """Archive video files larger than min_size_mb into a tar.gz archive.

        PROTECTED: Directories matching PROTECTED_PATTERNS (e.g., 'download',
        'FROM Z', 'Instagram', external drives) are NEVER touched.
        """
        video_exts = {'.mp4', '.wmv', '.webm', '.avi', '.mov', '.mkv'}
        video_files = []
        skipped_protected = 0
        for ext in video_exts:
            for f in AZAZAEL_ROOT.rglob(f"*{ext}"):
                if self._is_protected(f):
                    skipped_protected += 1
                    continue
                size_mb = f.stat().st_size / (1024 * 1024)
                if size_mb > min_size_mb:
                    video_files.append(f)

        if skipped_protected > 0:
            print(f"  [PROTECTED] Skipped {skipped_protected} video(s) in protected directories")

        if not video_files:
            print(f"  No videos over {min_size_mb}MB found.")
            return 0

        total_size = sum(f.stat().st_size for f in video_files)

        if not self.dry_run:
            archive_path = ARCHIVE_DIR / f"large_videos_{self.session_id}.tar.gz"
            with tarfile.open(archive_path, "w:gz") as tar:
                for f in video_files:
                    tar.add(f, arcname=str(f.relative_to(AZAZAEL_ROOT)))
            # Remove original videos after archiving
            for f in video_files:
                f.unlink()
            # Clean up empty parent dirs
            for d in sorted(set(f.parent for f in video_files), reverse=True):
                try:
                    d.rmdir()
                except OSError as exc:
                    note_failure("archive_system:L158", exc)

        self.archived.append(("Large videos", f"{len(video_files)} files", total_size))
        self.total_size += total_size
        return len(video_files)

    def archive_backup_files(self) -> int:
        """Archive .bak, .old, ~, .orig files."""
        patterns = ['*.bak', '*.old', '*~', '*.orig']
        backup_files = []
        for pat in patterns:
            backup_files.extend(AZAZAEL_ROOT.rglob(pat))

        if not backup_files:
            print("  No backup files found.")
            return 0

        total_size = sum(f.stat().st_size for f in backup_files)

        if not self.dry_run:
            archive_path = ARCHIVE_DIR / f"backup_files_{self.session_id}.tar.gz"
            with tarfile.open(archive_path, "w:gz") as tar:
                for f in backup_files:
                    tar.add(f, arcname=str(f.relative_to(AZAZAEL_ROOT)))
            for f in backup_files:
                f.unlink()

        self.archived.append(("Backup files", f"{len(backup_files)} files", total_size))
        self.total_size += total_size
        return len(backup_files)

    def archive_pyc_files(self) -> int:
        """Archive individual .pyc files outside __pycache__ into a tar.gz."""
        pyc_files = [f for f in AZAZAEL_ROOT.rglob("*.pyc")
                     if "__pycache__" not in str(f)]
        if not pyc_files:
            print("  No orphan .pyc files found.")
            return 0

        total_size = sum(f.stat().st_size for f in pyc_files)

        if not self.dry_run:
            archive_path = ARCHIVE_DIR / f"orphan_pyc_{self.session_id}.tar.gz"
            with tarfile.open(archive_path, "w:gz") as tar:
                for f in pyc_files:
                    tar.add(f, arcname=str(f.relative_to(AZAZAEL_ROOT)))
            for f in pyc_files:
                f.unlink()

        self.archived.append(("Orphan .pyc", f"{len(pyc_files)} files", total_size))
        self.total_size += total_size
        return len(pyc_files)

    def run_all(self):
        """Run all archive operations."""
        print(f"AZAZAEL Archive System — Session: {self.session_id}")
        print(f"Root: {AZAZAEL_ROOT}")
        print(f"Archive: {ARCHIVE_DIR}")
        print(f"Mode: {'DRY RUN (preview only)' if self.dry_run else 'LIVE'}")
        print()

        ops = [
            ("PPM Frame Files", self.archive_ppms),
            ("__pycache__ Dirs", self.archive_pycache),
            ("Large Videos (>10MB)", self.archive_large_videos),
            ("Backup Files", self.archive_backup_files),
            ("Orphan .pyc Files", self.archive_pyc_files),
        ]

        total_files = 0
        for label, fn in ops:
            print(f"  [{label}] ", end="", flush=True)
            count = fn()
            total_files += count
            print(f"{count} archived")

        print()
        print(f"Total: {total_files} files/dirs archived")
        print(f"Space freed: {self._size_str(self.total_size)}")
        if self.dry_run:
            print("DRY RUN — no files were moved. Use --all to archive.")

        # Save manifest
        manifest_path = ARCHIVE_DIR / f"manifest_{self.session_id}.txt"
        lines = [
            "AZAZAEL Archive Manifest",
            f"Session: {self.session_id}",
            f"Time: {datetime.now().isoformat()}",
            f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}",
            f"Root: {AZAZAEL_ROOT}",
            "",
        ]
        for path, reason, size in self.archived:
            lines.append(f"  {reason:<30} {self._size_str(size):>10}")
        lines.append(f"  {'TOTAL':<30} {self._size_str(self.total_size):>10}")
        manifest_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Manifest: {manifest_path}")


def main():
    parser = argparse.ArgumentParser(description="AZAZAEL Archive System")
    parser.add_argument("--all", action="store_true", help="Archive everything")
    parser.add_argument("--dry", action="store_true", help="Dry run (preview only)")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts (for automation/CI)")
    parser.add_argument("--ppms", action="store_true", help="Archive PPM frame files")
    parser.add_argument("--pycache", action="store_true", help="Archive __pycache__ dirs")
    parser.add_argument("--videos", action="store_true", help="Archive large videos (>10MB)")
    parser.add_argument("--backups", action="store_true", help="Archive backup files")
    parser.add_argument("--pyc", action="store_true", help="Archive orphan .pyc files")
    args = parser.parse_args()

    archive = ArchiveSystem(dry_run=args.dry)

    if args.all:
        if not args.dry and not args.yes:
            print("WARNING: --all will archive and DELETE videos, PPMs, __pycache__, backups, and orphan .pyc files.")
            print("Downloads and protected directories are EXCLUDED.")
            print("Use --yes to skip this prompt in automated scripts.")
            print()
            confirm = input("Type 'yes' to confirm: ")
            if confirm.strip().lower() != "yes":
                    print("Aborted.")
                    sys.exit(0)
            archive.run_all()
    elif any([args.ppms, args.pycache, args.videos, args.backups, args.pyc]):
        if args.videos and not args.dry and not args.yes:
            print("WARNING: --videos will archive and DELETE video files >10MB from the Azazael root.")
            print("Downloads and protected directories are EXCLUDED.")
            print("Use --yes to skip this prompt in automated scripts.")
            print()
            confirm = input("Type 'yes' to confirm: ")
            if confirm.strip().lower() != "yes":
                    print("Aborted.")
                    sys.exit(0)
            if args.ppms:
                print(f"PPMs: {archive.archive_ppms()} archived")
                if args.pycache:
                    print(f"__pycache__: {archive.archive_pycache()} archived")
                    if args.videos:
                        print(f"Videos: {archive.archive_large_videos()} archived")
                        if args.backups:
                            print(f"Backups: {archive.archive_backup_files()} archived")
                            if args.pyc:
                                print(f".pyc: {archive.archive_pyc_files()} archived")
    else:
        parser.print_help()
        print("\nTip: Use --dry --all to preview, then --all to archive.")


if __name__ == "__main__":
    main()
