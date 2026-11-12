"""Copy the dirty part of a worktree somewhere a restore cannot reach, and put it back.

Written after a bulk restore at 09:56:43-09:57:03 EST on 2026-11-05 reverted
uncommitted edits and untracked files, and nothing in the tree had a copy of
them: git's object store never held them (they were never `git add`ed), the audit
trail recorded no event for the paths, and the G:/WSL mirrors had no copy. See
docs/INCIDENT_2026-11-05_WORKTREE_RESTORE.md.

The design follows from why that recovery failed. A snapshot of the *dirty* set
is the only artifact that helps, because the clean set is already in git. So:

- Only paths `git status --porcelain` reports are copied. A clean tree produces a
  manifest and no files, which is a cheap and honest "nothing to protect".
- A staged deletion has no file to copy, so the manifest records the *intent*
  (status `D`), and `--restore` re-applies the deletion only under `--force`.
- Every copied file carries its sha256 in the manifest, so a restore can be
  verified rather than assumed.
- **Ignored paths are not covered.** This reads `git status`, so a file matched
  by `.gitignore` is invisible to it -- a `*.tmp` smoke file was silently outside
  the first snapshot (measured 2026-11-05: `git check-ignore -v` named
  `.gitignore:51:*.tmp`). `git clean -xfd` deletes those too; protect them by not
  leaving evidence in an ignored path, not by trusting this tool.
- Large files are skipped by default and *named as skipped* in the manifest. A
  silent skip reads as "protected" later; on this disk (9.7 GB free, 99% used)
  a 5 GB store would otherwise fill it.

Usage:
    python tools/worktree_snapshot.py --snapshot --label before-clean
    python tools/worktree_snapshot.py --list
    python tools/worktree_snapshot.py --restore 20261105T100900 [--force]
    python tools/worktree_snapshot.py --verify 20261105T100900

Exit codes: 0 done, 1 nothing to do or a failed verification, 2 could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

SNAPSHOT_DIR = ".worktree-snapshots"
MANIFEST = "manifest.json"
DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 512 * 1024 * 1024


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,  # push-gate FailureEvidence: a new call must time out
    )
    if proc.returncode != 0:
        raise SystemExit(f"REFUSED: git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _root(start: Path) -> Path:
    return Path(_git(start, "rev-parse", "--show-toplevel").strip())


def dirty_entries(root: Path) -> list[tuple[str, str]]:
    """Return (status, path) for every non-ignored path git reports as dirty."""
    raw = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries: list[tuple[str, str]] = []
    fields = raw.split("\0")
    i = 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if not field or len(field) < 4:
            continue
        status = field[:2]
        path = field[3:]
        # A rename/copy carries the original path in the next field.
        if status[0] in ("R", "C") and i < len(fields):
            i += 1
        entries.append((status, path))
    return entries


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def take_snapshot(
    root: Path,
    label: str = "",
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> dict:
    entries = dirty_entries(root)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    target = root / SNAPSHOT_DIR / stamp
    target.mkdir(parents=True, exist_ok=True)

    head = _git(root, "rev-parse", "HEAD").strip()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    files: list[dict] = []
    skipped: list[dict] = []
    total = 0
    for status, rel in entries:
        src = root / rel
        if not src.is_file():
            # Staged deletion, or a path that no longer exists: intent only.
            files.append({"status": status, "path": rel, "copied": False, "reason": "absent"})
            continue
        size = src.stat().st_size
        if size > max_file_bytes:
            skipped.append({"status": status, "path": rel, "bytes": size, "reason": "over max_file_bytes"})
            continue
        if total + size > max_total_bytes:
            skipped.append({"status": status, "path": rel, "bytes": size, "reason": "max_total_bytes reached"})
            continue
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        digest = _sha256(dest)
        total += size
        files.append({"status": status, "path": rel, "copied": True, "bytes": size, "sha256": digest})

    manifest = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": label,
        "root": str(root),
        "head": head,
        "branch": branch,
        "counts": {
            "dirty": len(entries),
            "copied": sum(1 for f in files if f.get("copied")),
            "absent": sum(1 for f in files if not f.get("copied")),
            "skipped": len(skipped),
            "bytes": total,
        },
        "files": files,
        "skipped": skipped,
    }
    (target / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Named in the output, not swallowed: a snapshot that skipped a file without
    # saying so is indistinguishable from a snapshot that protected it.
    print(f"snapshot {stamp} -> {target}")
    print(f"  dirty {len(entries)}  copied {manifest['counts']['copied']}  "
          f"absent {manifest['counts']['absent']}  skipped {len(skipped)}  bytes {total}")
    for item in skipped:
        print(f"  SKIPPED {item['path']} ({item['reason']}, {item['bytes']} bytes)")
    return manifest


def _load(root: Path, stamp: str) -> tuple[Path, dict]:
    target = root / SNAPSHOT_DIR / stamp
    manifest_path = target / MANIFEST
    if not manifest_path.is_file():
        raise SystemExit(f"REFUSED: no snapshot manifest at {manifest_path}")
    return target, json.loads(manifest_path.read_text(encoding="utf-8"))


def verify(root: Path, stamp: str) -> int:
    target, manifest = _load(root, stamp)
    bad = []
    for entry in manifest["files"]:
        if not entry.get("copied"):
            continue
        path = target / entry["path"]
        if not path.is_file():
            bad.append((entry["path"], "missing from snapshot"))
            continue
        if _sha256(path) != entry["sha256"]:
            bad.append((entry["path"], "sha256 mismatch"))
    if bad:
        for path, why in bad:
            print(f"  FAIL {path}: {why}")
        print(f"verify {stamp}: FAILED ({len(bad)} of {manifest['counts']['copied']})")
        return 1
    print(f"verify {stamp}: OK ({manifest['counts']['copied']} files, sha256 match)")
    return 0


def restore(root: Path, stamp: str, force: bool = False) -> int:
    target, manifest = _load(root, stamp)
    restored = 0
    for entry in manifest["files"]:
        dest = root / entry["path"]
        if entry.get("copied"):
            src = target / entry["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            restored += 1
            continue
        # An absent path in the manifest is a deletion that was intended at
        # snapshot time. Re-applying it destroys whatever is there now, so it
        # needs --force and says what it is removing.
        if entry.get("reason") == "absent" and dest.exists():
            if not force:
                print(f"  HELD {entry['path']}: snapshot recorded it absent; pass --force to delete")
                continue
            dest.unlink()
            print(f"  DELETED {entry['path']} (recorded absent at snapshot time, --force)")
            restored += 1
    print(f"restore {stamp}: {restored} paths written back into {root}")
    return 0 if restored else 1


def list_snapshots(root: Path) -> int:
    base = root / SNAPSHOT_DIR
    if not base.is_dir():
        print("no snapshots")
        return 1
    for child in sorted(base.iterdir()):
        manifest_path = child / MANIFEST
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        counts = manifest["counts"]
        print(f"{child.name}  {manifest['created']}  head {manifest['head'][:8]}  "
              f"copied {counts['copied']}  skipped {counts['skipped']}  "
              f"bytes {counts['bytes']}  {manifest.get('label') or ''}".rstrip())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", help="any path inside the worktree")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", action="store_true")
    group.add_argument("--restore", metavar="STAMP")
    group.add_argument("--verify", metavar="STAMP")
    group.add_argument("--list", action="store_true")
    parser.add_argument("--label", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    args = parser.parse_args(argv)

    root = _root(Path(args.root))
    if args.snapshot:
        take_snapshot(root, args.label, args.max_file_bytes, args.max_total_bytes)
        return 0
    if args.restore:
        return restore(root, args.restore, args.force)
    if args.verify:
        return verify(root, args.verify)
    return list_snapshots(root)


if __name__ == "__main__":
    sys.exit(main())
