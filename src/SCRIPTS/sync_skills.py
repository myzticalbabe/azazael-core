#!/usr/bin/env python
# coding: utf-8
"""
sync_skills.py — Sync azazael/skills/*.md to .agents/skills/<name>/SKILL.md

RULE 0R + RULE 0R-A: Skills Propagation Mandate.
Every skill file in azazael/skills/ MUST be propagated to .agents/skills/
so that skill() calls load the current canonical content.

Features:
  - Scans azazael/skills/*.md for all skill source files
  - Creates/updates .agents/skills/<name>/SKILL.md for each skill
  - Reports: created, updated (content changed), skipped (unchanged), deleted (stale)
  - Supports --dry-run flag
  - Importable as module: from scripts.sync_skills import sync_all
  - Thread-safe for 3.14t free-threading

Usage:
  python azazael/src/scripts/sync_skills.py
  python azazael/src/scripts/sync_skills.py --dry-run
  python azazael/src/scripts/sync_skills.py --verbose
  python azazael/src/scripts/sync_skills.py --skill azazael-startup  # single skill

From code:
  from scripts.sync_skills import sync_all, sync_one, get_gap_report
  report = sync_all()
  gap = get_gap_report()
"""

import json
import hashlib
import shutil
import threading
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Dict, List, Optional  # removed — all names have native equivalents


# ---- Path resolution ----
_SCRIPT_DIR = Path(__file__).resolve().parent  # azazael/src/scripts/
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent  # C:/Users/clint/ (project root)
_AZAZAEL_DIR = _SCRIPT_DIR.parent.parent  # azazael/
_SKILLS_SRC = _AZAZAEL_DIR / "skills"
_AGENTS_SKILLS = _PROJECT_ROOT / ".agents" / "skills"


def _resolve_paths():
    """Resolve paths relative to project root."""
    return _SKILLS_SRC, _AGENTS_SKILLS


# ---- Hash helper ----
def _file_hash(filepath: Path) -> str:
    """SHA-256 hash of file content for change detection."""
    if not filepath.exists():
        return ""
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


# ---- Core sync ----
class SkillsSync:
    """Skills synchronization engine. Thread-safe."""

    def __init__(self):
        self._lock = threading.RLock()
        self._stats = {"created": 0, "updated": 0, "skipped": 0, "deleted": 0, "errors": 0}
        self._details: list[dict] = []

    @property
    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    @property
    def details(self) -> list[dict]:
        with self._lock:
            return list(self._details)

    def _record(self, action: str, skill: str, detail: str = ""):
        with self._lock:
            self._stats[action] = self._stats.get(action, 0) + 1
            self._details.append({
                "skill": skill,
                "action": action,
                "detail": detail,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

    def sync_one(self, skill_name: str, dry_run: bool = False) -> bool:
        """Sync a single skill from source to destination.

        Args:
            skill_name: Skill name without .md extension (e.g., 'azazael-startup')
            dry_run: If True, report what would happen without writing

        Returns:
            True if sync was successful (or would be in dry-run mode)
        """
        src_dir, dest_base = _resolve_paths()
        src_file = src_dir / f"{skill_name}.md"

        if not src_file.exists():
            self._record("errors", skill_name, f"Source not found: {src_file}")
            return False

        dest_dir = dest_base / skill_name
        dest_file = dest_dir / "SKILL.md"

        # Read source content
        try:
            src_content = src_file.read_text(encoding="utf-8")
        except Exception as e:
            self._record("errors", skill_name, f"Read error: {e}")
            return False

        # Check if destination needs update
        if dest_file.exists():
            if _file_hash(dest_file) == _file_hash(src_file):
                self._record("skipped", skill_name, "Content unchanged")
                return True
            action = "updated"
        else:
            action = "created"

        # Write destination
        if not dry_run:
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file.write_text(src_content, encoding="utf-8")
            except Exception as e:
                self._record("errors", skill_name, f"Write error: {e}")
                return False

        self._record(action, skill_name)
        return True

    def sync_all(self, dry_run: bool = False) -> dict:
        """Sync ALL skills from azazael/skills/ to .agents/skills/.

        Also cleans up stale skills in .agents/skills/ that no longer
        have a source file in azazael/skills/.

        Args:
            dry_run: If True, report what would happen without writing

        Returns:
            Dict with stats and details
        """
        src_dir, dest_base = _resolve_paths()

        # Reset stats
        with self._lock:
            self._stats = {"created": 0, "updated": 0, "skipped": 0, "deleted": 0, "errors": 0}
            self._details = []

        # Find all source skills
        source_skills = set()
        if src_dir.exists():
            for f in src_dir.glob("*.md"):
                skill_name = f.stem
                source_skills.add(skill_name)
                self.sync_one(skill_name, dry_run=dry_run)

        # Find and clean up stale destination skills
        if dest_base.exists():
            for dest_dir in dest_base.iterdir():
                if dest_dir.is_dir() and not dest_dir.name.startswith("."):
                    skill_name = dest_dir.name
                    if skill_name not in source_skills:
                        # Stale — source no longer exists
                        if not dry_run:
                            try:
                                shutil.rmtree(dest_dir)
                            except Exception as e:
                                self._record("errors", skill_name, f"Cleanup error: {e}")
                                continue
                        self._record("deleted", skill_name, "Source file no longer exists")

        return self.stats

    def get_gap_report(self) -> dict:
        """Get a gap report: skills in source but not in destination, and vice versa.

        Returns:
            Dict with 'missing_in_dest', 'stale_in_dest', 'total_source', 'total_dest'
        """
        src_dir, dest_base = _resolve_paths()

        # Source skills
        source_skills = set()
        if src_dir.exists():
            source_skills = {f.stem for f in src_dir.glob("*.md")}

        # Destination skills
        dest_skills = set()
        if dest_base.exists():
            dest_skills = {d.name for d in dest_base.iterdir()
                          if d.is_dir() and not d.name.startswith(".") and (d / "SKILL.md").exists()}

        missing = sorted(source_skills - dest_skills)
        stale = sorted(dest_skills - source_skills)

        return {
            "total_source": len(source_skills),
            "total_dest": len(dest_skills),
            "missing_in_dest": missing,
            "missing_count": len(missing),
            "stale_in_dest": stale,
            "stale_count": len(stale),
            "sync_pct": round(len(dest_skills) / max(len(source_skills), 1) * 100, 1),
        }


# ---- Module-level convenience ----
_sync_instance: (SkillsSync | None) = None
_sync_lock = threading.Lock()


def _get_sync() -> SkillsSync:
    global _sync_instance
    if _sync_instance is None:
        with _sync_lock:
            if _sync_instance is None:
                _sync_instance = SkillsSync()
    return _sync_instance


def sync_all(dry_run: bool = False) -> dict:
    """Sync all skills. Returns stats dict."""
    return _get_sync().sync_all(dry_run=dry_run)


def sync_one(skill_name: str, dry_run: bool = False) -> bool:
    """Sync a single skill. Returns True on success."""
    return _get_sync().sync_one(skill_name, dry_run=dry_run)


def get_gap_report() -> dict:
    """Get full gap report."""
    return _get_sync().get_gap_report()


# ---- CLI ----
def main():
    parser = argparse.ArgumentParser(
        description="Sync azazael/skills/*.md to .agents/skills/<name>/SKILL.md"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen without writing")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-skill details")
    parser.add_argument("--skill", type=str, help="Sync a single skill by name")
    parser.add_argument("--gap-report", action="store_true", help="Show gap report only, no sync")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    sync = _get_sync()

    if args.gap_report:
        gap = sync.get_gap_report()
        if args.json:
            print(json.dumps(gap, indent=2))
        else:
            print("=== SKILL GAP REPORT ===")
            print(f"  Source: {gap['total_source']} skills in azazael/skills/")
            print(f"  Dest:   {gap['total_dest']} skills in .agents/skills/")
            print(f"  Sync:   {gap['sync_pct']}%")
            print(f"  Missing ({gap['missing_count']}): {gap['missing_in_dest'][:20]}")
            if gap['stale_count']:
                print(f"  Stale   ({gap['stale_count']}): {gap['stale_in_dest'][:20]}")
        return

    if args.skill:
        ok = sync.sync_one(args.skill, dry_run=args.dry_run)
        if args.json:
            print(json.dumps({"skill": args.skill, "ok": ok, "stats": sync.stats}))
        else:
            print(f"  {'[DRY RUN] ' if args.dry_run else ''}Sync '{args.skill}': {'OK' if ok else 'FAILED'}")
        return

    # Full sync
    stats = sync.sync_all(dry_run=args.dry_run)
    prefix = "[DRY RUN] " if args.dry_run else ""

    if args.json:
        print(json.dumps({"stats": stats, "details": sync.details}, indent=2, default=str))
    else:
        total = sum(stats.values())
        print(f"\n  {prefix}Skills Sync Complete — {total} total")
        print(f"    Created: {stats.get('created', 0)}")
        print(f"    Updated: {stats.get('updated', 0)}")
        print(f"    Skipped: {stats.get('skipped', 0)}")
        print(f"    Deleted: {stats.get('deleted', 0)}")
        if stats.get('errors', 0):
            print(f"    Errors:  {stats['errors']}")

        if args.verbose and sync.details:
            print()
            for d in sync.details:
                icon = {"created": "+", "updated": "~", "skipped": "=", "deleted": "-", "errors": "!"}.get(d["action"], "?")
                print(f"    {icon} {d['skill']}  ({d['action']})")


if __name__ == "__main__":
    main()
