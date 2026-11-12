
"""backup.py — Incremental backup, manifest, integrity check, and restore.

Covers followups: #23 (incremental backup), #36 (backup manifest),
#37 (backup integrity check), #38 (backup restore).

Usage:
    from CORE.backup import incremental_backup, list_backups, \
        verify_backup_integrity, restore_backup
"""


__all__ = [
    "BACKUP_DIR",
    "incremental_backup",
    "list_backups",
    "restore_backup",
    "verify_backup_integrity",
]


import json
import os
import shutil
import hashlib
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


def _get_backup_base() -> Path:
    """Resolve the backup root from the environment, fresh on every call.

    Named for the same reason `direct_memory._get_db_path` is: the module-level
    values below are a CACHE of this answer, and a caller that redirects
    `AZAZAEL_MEMORY_DIR` (a test, or a Docker split) needs a way to recompute
    them. Without a resolver the cached global is a one-way door.
    """
    return Path(os.environ.get("AZAZAEL_MEMORY_DIR", Path.home() / ".azazael"))


_backup_base = _get_backup_base()
BACKUP_DIR = _backup_base / "backups"


def _reload_paths() -> None:
    """Recompute the cached paths after `AZAZAEL_MEMORY_DIR` changed."""
    global _backup_base, BACKUP_DIR
    _backup_base = _get_backup_base()
    BACKUP_DIR = _backup_base / "backups"


def _ensure_backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _file_hash(filepath: str) -> str:
    """SHA-256 hash of a file for change detection."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_manifest_path(ts: str) -> Path:
    return BACKUP_DIR / f"backup_{ts}.json"


def _get_backup_path(ts: str) -> Path:
    return BACKUP_DIR / f"backup_{ts}"


def list_backups() -> list:
    """list all available backups with full metadata from manifests.

    Covers followup #36: backup manifest.
    """
    _ensure_backup_dir()
    backups = []
    for manifest in sorted(BACKUP_DIR.glob("backup_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            files_info = data.get("files", [])
            backups.append({
                "manifest": manifest.name,
                "timestamp": data.get("timestamp", "?"),
                "type": data.get("type", "?"),
                "total_files": data.get("total_files", len(files_info)),
                "total_size_mb": round(sum(f.get("size", 0) for f in files_info) / (1024*1024), 2),
                "based_on": data.get("based_on", None),
                "file_list": [f.get("path", "")[-40:] for f in files_info[:10]],
                "integrity_verified": data.get("integrity_verified", False),
            })
        except Exception as exc:
            note_failure("backup:L94", exc)
            continue
    return backups


def incremental_backup(output_dir: str | None = None) -> dict:
    """Create an incremental backup of DB and audit files.
    
    Only copies files that have changed since the last backup
    (compared by SHA-256 hash stored in the manifest).
    
    Args:
        output_dir: Custom output directory. Auto-named if not provided.
    
    Returns:
        dict with backup path, files copied, total size.
    """
    _ensure_backup_dir()
    
    # Find last manifest to compare
    last_manifest = None
    manifests = sorted(BACKUP_DIR.glob("backup_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    last_hashes = {}
    if manifests:
        last_manifest = manifests[0]
        try:
            last_data = json.loads(last_manifest.read_text(encoding="utf-8"))
            for f in last_data.get("files", []):
                last_hashes[f["path"]] = f["hash"]
        except Exception as exc:
            note_failure("backup:L123", exc)
    
    # Collect files
    from CORE.direct_memory import DB_PATH
    from CORE.audit_trail import AUDIT_DIR
    
    files = []
    db_path = Path(str(DB_PATH))
    if db_path.exists():
        for ext in ["", "-wal", "-shm"]:
            p = db_path.parent / (db_path.name + ext)
            if p.exists():
                files.append(str(p))
    
    if AUDIT_DIR.exists():
        files.extend(str(p) for p in sorted(AUDIT_DIR.glob("*.jsonl")))
        files.extend(str(p) for p in sorted(AUDIT_DIR.glob("*.jsonl.gz")))
    
    # Determine which files are new/changed (followup #23: SHA-256 change detection)
    new_files = []
    for fp in files:
        try:
            h = _file_hash(fp)
            rel = str(Path(fp).relative_to(Path.home()))
            if rel not in last_hashes or last_hashes[rel] != h:
                new_files.append((fp, rel, h))
        except Exception as exc:
            note_failure("backup:L150", exc)
            new_files.append((fp, str(fp), "unknown"))
    
    if not new_files:
        return {"type": "incremental", "status": "no_changes", "files_copied": 0}
    
    # Create backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"backup_{ts}"
    backup_path.mkdir(parents=True, exist_ok=True)
    
    manifest_path = _get_manifest_path(ts)
    manifest = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "incremental",
        "based_on": str(last_manifest) if last_manifest else None,
        "files": [],
        "total_files": 0,
        "total_size": 0,
    }
    
    total_size = 0
    for src, rel, h in new_files:
        dest = backup_path / Path(src).name
        shutil.copy2(src, str(dest))
        size = os.path.getsize(src)
        manifest["files"].append({"path": rel, "hash": h, "size": size})
        total_size += size
    
    manifest["total_files"] = len(new_files)
    manifest["total_size"] = total_size
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    
    return {
        "type": "incremental",
        "backup_path": str(backup_path),
        "manifest_path": str(manifest_path),
        "files_copied": len(new_files),
        "total_size_mb": round(total_size / (1024*1024), 2),
    }


def verify_backup_integrity(manifest_path: str) -> dict:
    """Verify backup integrity: check all files against manifest hashes.

    Covers followup #37: backup integrity check.

    Args:
        manifest_path: Path to backup_*.json manifest.

    Returns:
        dict with 'valid', 'total_files', 'verified_files',
        'mismatched' (list of paths whose hashes don't match),
        'missing' (list of paths in manifest but not on disk).
    """
    mfp = Path(manifest_path)
    if not mfp.exists():
        return {"error": "Manifest not found"}

    try:
        manifest = json.loads(mfp.read_text(encoding="utf-8"))
    except Exception as e:
        note_failure("backup:L211", e)
        return {"error": f"Cannot parse manifest: {e}"}

    backup_dir = mfp.parent / mfp.stem.replace("backup_", "backup_")
    # Actually the backup dir is: BACKUP_DIR / f"backup_{ts}" where ts is in the filename
    ts = mfp.stem.replace("backup_", "")
    backup_dir = _get_backup_path(ts)

    files = manifest.get("files", [])
    mismatched = []
    missing = []
    verified = 0

    for entry in files:
        rel_path = entry.get("path", "")
        expected_hash = entry.get("hash", "")
        # Find the file in the backup dir (by name, not full path)
        fname = Path(rel_path).name
        fp = backup_dir / fname
        if not fp.exists():
            missing.append(rel_path)
            continue
        try:
            actual_hash = _file_hash(str(fp))
            if actual_hash != expected_hash:
                mismatched.append({"path": rel_path, "expected": expected_hash[:16], "actual": actual_hash[:16]})
            else:
                verified += 1
        except Exception as exc:
            note_failure("backup:L239", exc)
            mismatched.append({"path": rel_path, "error": "Cannot read file"})

    return {
        "valid": len(mismatched) == 0 and len(missing) == 0,
        "manifest": mfp.name,
        "total_files": len(files),
        "verified_files": verified,
        "mismatched": mismatched,
        "missing": missing,
    }


def restore_backup(manifest_path: str, target_dir: str = "", dry_run: bool = False) -> dict:
    """Restore files from a backup to their original locations.

    Covers followup #38: backup restore.

    Args:
        manifest_path: Path to backup_*.json manifest.
        target_dir: If provided, restore to this directory instead of original locations.
        dry_run: If True, only print what would be restored.

    Returns:
        dict with 'restored_files', 'total_files', 'restored_bytes',
        'errors' list.
    """
    mfp = Path(manifest_path)
    if not mfp.exists():
        return {"error": "Manifest not found"}

    try:
        manifest = json.loads(mfp.read_text(encoding="utf-8"))
    except Exception as e:
        note_failure("backup:L272", e)
        return {"error": f"Cannot parse manifest: {e}"}

    ts = mfp.stem.replace("backup_", "")
    backup_dir = _get_backup_path(ts)
    if not backup_dir.exists():
        return {"error": f"Backup directory not found: {backup_dir}"}

    target = Path(target_dir) if target_dir else None
    files = manifest.get("files", [])
    restored = 0
    total_bytes = 0
    errors = []

    for entry in files:
        rel_path = entry.get("path", "")
        fname = Path(rel_path).name
        src = backup_dir / fname
        if not src.exists():
            errors.append(f"File not found in backup: {fname}")
            continue
        if target:
            dest = target / fname
        else:
            dest = Path.home() / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)

        if dry_run:
            print(f"  Would restore: {src} -> {dest}")
        else:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dest))
                restored += 1
                total_bytes += entry.get("size", 0)
            except Exception as e:
                note_failure("backup:L307", e)
                errors.append(f"Failed to restore {fname}: {e}")

    return {
        "restored_files": restored,
        "total_files": len(files),
        "restored_bytes": total_bytes,
        "restored_mb": round(total_bytes / (1024*1024), 2),
        "errors": errors,
    }
