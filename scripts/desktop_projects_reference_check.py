#!/usr/bin/env python3
"""
desktop_projects_reference_check.py — Standalone reference check for the
deprecated Desktop projects METAWORMS and Azazael_UNIFIED.

Confirms the Desktop project copies are untouched (byte-identical to the
canonical reference), reporting per-file UNTOUCHED / MODIFIED / ADDED /
REMOVED status.

This script is intentionally STANDALONE — pure stdlib, no azazael imports.
It can run from any environment (Windows, WSL, or a bare box) and works even
when the azazael package is not importable.

Usage:
    python scripts/desktop_projects_reference_check.py --build-manifest
    python scripts/desktop_projects_reference_check.py --check
    python scripts/desktop_projects_reference_check.py --check --project METAWORMS
    python scripts/desktop_projects_reference_check.py --check --verbose
    python scripts/desktop_projects_reference_check.py --status

Exit codes:
    0  all projects untouched
    1  drift detected (modified / added / removed files)
    2  a project copy or reference is missing / unavailable

Reference manifest files are stored under scripts/references/ as
"<project>_reference.json" (relpath -> sha256), built with --build-manifest
from the canonical reference locations below.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────
# Project definitions
# ─────────────────────────────────────────────────────────────────────────
# desktop_locations:    candidate paths for the live Desktop copy of the
#                       project (first existing one wins)
# reference_locations:  candidate paths for the canonical reference copy the
#                       Desktop copy must match (first existing one wins)

PROJECTS = {
    "METAWORMS": {
        "label": "METAWORMS (deprecated Desktop project, data only)",
        "desktop_locations": [
            Path.home() / "Desktop" / "AZAZAEL_ARCHIVE" / "METAWORMS",
            Path.home() / "Desktop" / "METAWORMS",
        ],
        "reference_locations": [
            Path("G:/Archive/METAWORMS"),
        ],
    },
    "Azazael_UNIFIED": {
        "label": "Azazael_UNIFIED (archived Desktop project)",
        "desktop_locations": [
            Path.home() / "Desktop" / "Azazael_UNIFIED",
        ],
        "reference_locations": [
            Path(__file__).resolve().parent.parent / "archived_unified",
            Path("G:/Archive/Azazael_UNIFIED"),
        ],
    },
}

# Directory / file names always excluded from manifests (build artifacts and
# self-referential manifests that would otherwise cause false drift).
_SKIP_DIRS = {"__pycache__", ".venv", "node_modules", ".git"}
_SKIP_FILES = {".hash_manifest.txt"}


def _skip_path(rel: str) -> bool:
    """Return True if a relative path should be excluded from manifests."""
    parts = rel.replace(os.sep, "/").split("/")
    if any(p in _SKIP_DIRS for p in parts):
        return True
    if parts[-1].endswith(".pyc") or parts[-1] in _SKIP_FILES:
        return True
    return False


def sha256_file(path: Path) -> str:
    """Return the lowercase hex sha256 of a file (chunked read)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: Path) -> dict:
    """Build {relpath: sha256} for every tracked file under root."""
    manifest: dict = {}
    if not root.is_dir():
        return manifest
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if _skip_path(rel):
                continue
            try:
                manifest[rel] = sha256_file(p)
            except OSError as exc:  # unreadable file -> record as drift marker
                manifest[rel] = "UNREADABLE:%s" % exc
    return manifest


# ─────────────────────────────────────────────────────────────────────────
# Manifest persistence (scripts/references/)
# ─────────────────────────────────────────────────────────────────────────
def references_dir() -> Path:
    return Path(__file__).resolve().parent / "references"


def manifest_path(project: str) -> Path:
    return references_dir() / ("%s_reference.json" % project)


def save_manifest(project: str, manifest: dict) -> Path:
    refs = references_dir()
    refs.mkdir(parents=True, exist_ok=True)
    out = manifest_path(project)
    payload = {
        "project": project,
        "file_count": len(manifest),
        "manifest": manifest,
    }
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(out)
    return out


def load_manifest(project: str) -> dict:
    p = manifest_path(project)
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload.get("manifest", {}) if isinstance(payload, dict) else {}


# ─────────────────────────────────────────────────────────────────────────
# Comparison
# ─────────────────────────────────────────────────────────────────────────
def compare_manifests(desktop: dict, reference: dict) -> dict:
    """Classify every path as untouched / modified / added / removed."""
    status: dict = {}
    for rel, ref_hash in reference.items():
        if rel not in desktop:
            status[rel] = "removed"
        elif desktop[rel] == ref_hash:
            status[rel] = "untouched"
        else:
            status[rel] = "modified"
    for rel in desktop:
        if rel not in reference:
            status[rel] = "added"
    return status


def find_first(candidates) -> Path | None:
    """Return the first existing directory from candidates, else None."""
    for c in candidates:
        try:
            if Path(c).is_dir():
                return Path(c)
        except OSError:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────
# Per-project check
# ─────────────────────────────────────────────────────────────────────────
def check_project(project: str, verbose: bool = False) -> tuple:
    """Check one project. Returns (summary_lines, exit_code).

    exit_code: 0 untouched, 1 drift, 2 missing copy or reference.
    """
    cfg = PROJECTS[project]
    lines = ["=== %s (%s) ===" % (project, cfg["label"])]

    desktop_dir = find_first(cfg["desktop_locations"])
    if desktop_dir is None:
        lines.append("  Desktop copy: NOT FOUND (project archived/removed from Desktop)")
        lines.append("  Expected locations: %s" % "; ".join(str(p) for p in cfg["desktop_locations"]))
        lines.append("  STATUS: DESKTOP COPY ABSENT (exit 2)")
        return lines, 2

    ref_dir = find_first(cfg["reference_locations"])
    if ref_dir is None:
        lines.append("  Desktop copy: %s" % desktop_dir)
        lines.append("  Reference: NOT FOUND (no canonical copy available to compare against)")
        lines.append("  STATUS: REFERENCE UNAVAILABLE (exit 2)")
        return lines, 2

    desktop_manifest = build_manifest(desktop_dir)
    ref_manifest = build_manifest(ref_dir)
    status = compare_manifests(desktop_manifest, ref_manifest)

    counts = {k: 0 for k in ("untouched", "modified", "added", "removed")}
    for s in status.values():
        counts[s] += 1

    lines.append("  Desktop copy:  %s (%d tracked files)" % (desktop_dir, len(desktop_manifest)))
    lines.append("  Reference:     %s (%d tracked files)" % (ref_dir, len(ref_manifest)))
    lines.append("  untouched=%d  modified=%d  added=%d  removed=%d"
                 % (counts["untouched"], counts["modified"], counts["added"], counts["removed"]))

    if verbose and any(counts[k] for k in ("modified", "added", "removed")):
        for rel in sorted(status):
            if status[rel] != "untouched":
                lines.append("    [%s] %s" % (status[rel], rel))

    if counts["modified"] == 0 and counts["added"] == 0 and counts["removed"] == 0:
        lines.append("  STATUS: UNTOUCHED ✔  (exit 0)")
        return lines, 0
    lines.append("  STATUS: DRIFT DETECTED ✘  (exit 1)")
    return lines, 1


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────
def _build_manifests(projects) -> int:
    """Regenerate reference manifests from canonical reference copies."""
    built, missing = [], []
    for project in projects:
        cfg = PROJECTS[project]
        ref_dir = find_first(cfg["reference_locations"])
        if ref_dir is None:
            missing.append(project)
            print("SKIP  %-16s reference not found" % project)
            continue
        manifest = build_manifest(ref_dir)
        out = save_manifest(project, manifest)
        print("BUILD %-16s %5d files  ->  %s" % (project, len(manifest), out))
        built.append(project)
    if missing:
        print("MISSING reference for: %s" % ", ".join(missing))
        return 2
    return 0


def _cmd_check(projects, verbose: bool) -> int:
    worst = 0
    for project in projects:
        lines, code = check_project(project, verbose=verbose)
        for line in lines:
            print(line)
        worst = max(worst, code)
    return worst


def _cmd_status(projects) -> int:
    for project in projects:
        cfg = PROJECTS[project]
        print("=== %s ===" % project)
        for kind in ("desktop_locations", "reference_locations"):
            for p in cfg[kind]:
                exists = "EXISTS" if Path(p).is_dir() else "absent"
                print("  %-18s %-6s %s" % (kind.split("_")[0], exists, p))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="desktop_projects_reference_check.py",
        description="Standalone reference check for Desktop METAWORMS / Azazael_UNIFIED.",
    )
    parser.add_argument("--build-manifest", action="store_true",
                        help="Regenerate reference manifests from canonical reference copies")
    parser.add_argument("--check", action="store_true",
                        help="Compare Desktop project copies against reference manifests")
    parser.add_argument("--status", action="store_true",
                        help="Show where each project's copies live")
    parser.add_argument("--project", choices=sorted(PROJECTS), action="append",
                        help="Restrict to a specific project (repeatable)")
    parser.add_argument("--verbose", action="store_true", help="List every drift file")
    args = parser.parse_args(argv)

    projects = args.project or sorted(PROJECTS)

    if args.build_manifest:
        return _build_manifests(projects)
    if args.status:
        return _cmd_status(projects)
    # default: check
    return _cmd_check(projects, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
