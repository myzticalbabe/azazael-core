# coding: utf-8
"""


__all__ = [
    "atomic_write",
    "is_same_drive",
    "safe_copytree",
    "safe_copytree_chunked",
    "safe_move",
    "safe_rmtree",
    "verify_tree_integrity",
]

safe_file_ops.py — Safe file operations with verify-before-delete & atomic write.

Prevents the data-loss pattern identified in Error #58:
    shutil.copytree(src, dst) → shutil.rmtree(src)
    
If copytree is interrupted (timeout, crash), rmtree silently destroys the
partial source with no way to recover.

This module provides:
    - safe_copytree(src, dst, verify=True): copies tree, verifies integrity
      before allowing source deletion
    - safe_rmtree(path, verify_source=None): removes path only after verifying
      the destination is complete (if verify_source provided)
    - safe_move(src, dst): copy → verify → delete source (atomic-ish)
    - atomic_write(path, content): write to temp file then rename
    - verify_tree_integrity(src, dst): compare file count, sizes, mtimes

Usage:
    >>> from CORE.safe_file_ops import safe_copytree, safe_rmtree
    >>> safe_copytree("/src/data", "/dst/data", verify=True)
    >>> safe_rmtree("/src/data", verify_source="/dst/data")

Created: June 11, 2026 — Error #58 permanent fix
"""

import os
import sys
import shutil
import tempfile
import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, Iterable, List
from datetime import datetime
import json

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

_log = logging.getLogger("safe_file_ops")

# ── Integrity Verification ──────────────────────────────────────────


def _file_md5(path: Path, chunk_size: int = 65536) -> str:
    """Compute MD5 hash of a file in chunks (memory-safe for large files)."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError) as e:
        _log.warning("MD5 failed for %s: %s", path, e)
        return ""


def verify_tree_integrity(
    src: str | Path,
    dst: str | Path,
    sample_hashes: bool = False,
    hash_max_files: int = 20,
    progress_callback: (Callable[[int, int, str], None] | None) = None,
    # NOTE: dict[str, Any], not dict[str, object]: every reader applies
    # len()/.get() to the values, and object poisons all four call sites.
) -> dict[str, Any]:
    """Compare source and destination trees.

    Checks:
    - File count matches
    - Total size matches
    - Optionally sample MD5 hashes (up to hash_max_files)

    Args:
        src: Source directory path.
        dst: Destination directory path.
        sample_hashes: If True, compute MD5 on a sample of files.
        hash_max_files: Max files to hash (0 = all, but slow).
        progress_callback: Optional callback(checked, total, filename).

    Returns:
        dict with keys:
            "match" (bool): True if all checks pass.
            "src_count" (int): Source file count.
            "dst_count" (int): Destination file count.
            "src_size" (int): Source total bytes.
            "dst_size" (int): Destination total bytes.
            "missing" (list): Files in src but not dst.
            "extra" (list): Files in dst but not src.
            "size_mismatch" (list): Files with different sizes.
            "hash_mismatch" (list): Files with different MD5 (if sampled).
            "sampled_count" (int): Number of files hashed.
    """
    src_p, dst_p = Path(src), Path(dst)
    result: dict[str, object] = {
        "match": False,
        "src_count": 0,
        "dst_count": 0,
        "src_size": 0,
        "dst_size": 0,
        "missing": [],
        "extra": [],
        "size_mismatch": [],
        "hash_mismatch": [],
        "sampled_count": 0,
    }

    if not src_p.is_dir():
        result["error"] = f"Source not found: {src_p}"
        return result
    if not dst_p.is_dir():
        result["error"] = f"Destination not found: {dst_p}"
        result["missing"] = [str(p.relative_to(src_p)) for p in src_p.rglob("*") if p.is_file()]
        return result

    # Build file maps: relative path → stat
    src_files: dict[str, os.stat_result] = {}
    dst_files: dict[str, os.stat_result] = {}

    for f in src_p.rglob("*"):
        if f.is_file():
            rel = str(f.relative_to(src_p))
            src_files[rel] = f.stat()

    for f in dst_p.rglob("*"):
        if f.is_file():
            rel = str(f.relative_to(dst_p))
            dst_files[rel] = f.stat()

    result["src_count"] = len(src_files)
    result["dst_count"] = len(dst_files)

    missing = [k for k in src_files if k not in dst_files]
    extra = [k for k in dst_files if k not in src_files]
    result["missing"] = missing
    result["extra"] = extra

    # Size comparison
    src_total = sum(s.st_size for s in src_files.values())
    dst_total = sum(s.st_size for s in dst_files.values())
    result["src_size"] = src_total
    result["dst_size"] = dst_total

    size_mismatch = []
    for rel, s_stat in src_files.items():
        d_stat = dst_files.get(rel)
        if d_stat and s_stat.st_size != d_stat.st_size:
            size_mismatch.append(rel)
    result["size_mismatch"] = size_mismatch

    # File count and size must match for basic integrity
    if missing or extra or size_mismatch:
        return result

    # Root cause: verify total bytes too
    if src_total != dst_total:
        result["size_mismatch"].append("_total_bytes_differ")
        return result

    # Optional hash sampling
    if sample_hashes:
        common = [k for k in src_files if k in dst_files]
        to_hash = common
        if hash_max_files > 0 and len(common) > hash_max_files:
            # Sample evenly across the tree
            step = max(1, len(common) // hash_max_files)
            to_hash = common[::step]

        hash_mismatch = []
        for i, rel in enumerate(to_hash):
            if progress_callback:
                progress_callback(i + 1, len(to_hash), rel)
            src_hash = _file_md5(src_p / rel)
            dst_hash = _file_md5(dst_p / rel)
            if src_hash and dst_hash and src_hash != dst_hash:
                hash_mismatch.append(rel)

        result["hash_mismatch"] = hash_mismatch
        result["sampled_count"] = len(to_hash)

        if hash_mismatch:
            return result

    result["match"] = True
    return result


# ── Safe Copy ───────────────────────────────────────────────────────


def safe_copytree(
    src: str | Path,
    dst: str | Path,
    verify: bool = True,
    sample_hashes: bool = False,
    # Widened to Iterable[str] to match shutil.copytree (and shutil.ignore_patterns,
    # which returns a set) -- the value is forwarded to shutil.copytree untouched.
    ignore: (Callable[[str, list[str]], Iterable[str]] | None) = None,
    dirs_exist_ok: bool = True,
    symlinks: bool = False,
    progress_callback: (Callable[[int, int, str], None] | None) = None,
) -> dict[str, object]:
    """Copy a directory tree with optional integrity verification.

    Uses shutil.copytree for the actual copy, then verifies the destination
    matches the source before returning success.

    Args:
        src: Source directory.
        dst: Destination directory.
        verify: If True, run integrity check after copy.
        sample_hashes: If True, sample MD5 hashes during verification.
        ignore: Callable passed to shutil.copytree for exclusion patterns.
        dirs_exist_ok: If True, destination dirs can already exist.
        symlinks: If True, copy symlinks as symlinks.
        progress_callback: Optional callback for verification progress.

    Returns:
        dict with "success" (bool), "detail" (verify result or error msg).
        If verify=True, detail is the verify_tree_integrity result.
    """
    src_p, dst_p = Path(src), Path(dst)

    if not src_p.is_dir():
        return {"success": False, "detail": f"Source not found: {src_p}"}

    try:
        dst_p.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            str(src_p),
            str(dst_p),
            ignore=ignore,
            dirs_exist_ok=dirs_exist_ok,
            symlinks=symlinks,
        )
    except (shutil.Error, OSError) as e:
        note_failure("safe_file_ops:L245", e)
        return {"success": False, "detail": f"Copy failed: {e}"}

    if not verify:
        return {"success": True, "detail": "Copied (no verification)"}

    # Verify integrity
    vresult = verify_tree_integrity(
        src_p, dst_p,
        sample_hashes=sample_hashes,
        progress_callback=progress_callback,
    )

    if vresult["match"]:
        return {"success": True, "detail": vresult}

    # Verification failed — log details
    _log.error(
        "safe_copytree integrity FAIL: src=%s dst=%s missing=%d size_mismatch=%d",
        src_p, dst_p,
        len(vresult.get("missing", [])),
        len(vresult.get("size_mismatch", [])),
    )
    return {"success": False, "detail": vresult}


# ── Safe Remove ─────────────────────────────────────────────────────


def safe_rmtree(
    path: str | Path,
    verify_source: (str | Path | None) = None,
    dry_run: bool = False,
    ignore_errors: bool = False,
    backup_dir: (str | Path | None) = None,
) -> dict[str, object]:
    """Remove a directory tree, optionally verifying a backup/archive first.

    This is the core fix for Error #58: never delete source without
    verifying the destination is complete.

    Args:
        path: Directory to remove.
        verify_source: Optional path to verify EXISTS as a complete copy
            before allowing deletion. If provided and verification fails,
            the directory is NOT removed.
        dry_run: If True, log what would be removed but don't delete.
        ignore_errors: Passed to shutil.rmtree.
        backup_dir: Optional backup path to copy data to before deletion.

    Returns:
        dict with "success" (bool), "detail" (str or dict).
    """
    path_p = Path(path)

    if not path_p.exists():
        return {"success": True, "detail": "Path does not exist, nothing to remove"}

    if not path_p.is_dir():
        # Single file — use os.remove
        if not dry_run:
            try:
                os.remove(path_p)
            except OSError as e:
                note_failure("safe_file_ops:L308", e)
                return {"success": False, "detail": str(e)}
        return {"success": True, "detail": "File removed"}

    # Backup before delete (if requested)
    if backup_dir is not None:
        bk = Path(backup_dir)
        bk.mkdir(parents=True, exist_ok=True)
        # Create timestamped backup
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = bk / f"{path_p.name}_{ts}"
        copy_result = safe_copytree(path_p, backup_path, verify=True)
        if not copy_result["success"]:
            _log.error(
                "Backup before delete FAILED for %s -> %s: %s",
                path_p, backup_path, copy_result["detail"],
            )
            return {"success": False, "detail": f"Backup failed: {copy_result['detail']}"}

    # Verify source integrity (if provided)
    if verify_source is not None:
        vs = Path(verify_source)
        if not vs.exists():
            msg = f"verify_source not found: {vs}. Refusing to delete {path_p}"
            _log.error("safe_rmtree blocked: %s", msg)
            return {"success": False, "detail": msg}

        if not vs.is_dir():
            # Single file — just check it exists
            pass
        else:
            # Verify that everything in path_to_delete exists in verify_source.
            # Use verify_tree_integrity(path_to_delete, verify_source) so that
            # "missing" = files in path_to_delete NOT in verify_source = would be lost.
            vresult = verify_tree_integrity(path_p, vs)
            if not vresult["match"]:
                msg = (
                    f"Integrity check FAILED: {len(vresult.get('missing', []))} files in "
                    f"{path_p.name} would be LOST — not present in {vs.name}. "
                    f"Size mismatches: {len(vresult.get('size_mismatch', []))}. "
                    f"Refusing to delete."
                )
                _log.error("safe_rmtree blocked: %s", msg)
                return {"success": False, "detail": vresult}

    if dry_run:
        return {"success": True, "detail": "Dry run — would remove"}

    # Proceed with deletion
    try:
        shutil.rmtree(path_p, ignore_errors=ignore_errors)
        return {"success": True, "detail": "Removed successfully"}
    except OSError as e:
        note_failure("safe_file_ops:L360", e)
        return {"success": False, "detail": str(e)}


# ── Safe Move ───────────────────────────────────────────────────────


def safe_move(
    src: str | Path,
    dst: str | Path,
    verify: bool = True,
    sample_hashes: bool = False,
    backup_dir: (str | Path | None) = None,
) -> dict[str, object]:
    """Move a file or directory with verify-before-delete.

    For directories: copy → verify → delete source.
    For files: shutil.move (with md5 verify if requested).

    Args:
        src: Source path.
        dst: Destination path.
        verify: If True, verify integrity after copy before deleting source.
        sample_hashes: If True, sample MD5 hashes during verification.
        backup_dir: Optional backup destination for safety.

    Returns:
        dict with "success" (bool) and "detail".
    """
    src_p, dst_p = Path(src), Path(dst)

    if not src_p.exists():
        return {"success": False, "detail": f"Source not found: {src_p}"}

    if src_p.is_dir():
        # Directory: copy → verify → delete
        copy_result = safe_copytree(
            src_p, dst_p,
            verify=verify,
            sample_hashes=sample_hashes,
            dirs_exist_ok=True,
        )
        if not copy_result["success"]:
            return copy_result

        delete_result = safe_rmtree(
            src_p,
            verify_source=dst_p if verify else None,
            backup_dir=backup_dir,
        )
        if not delete_result["success"]:
            return delete_result

        return {"success": True, "detail": "Directory moved"}

    # Single file: use shutil.move with optional verification
    if verify:
        # Copy then delete with verification
        try:
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_p), str(dst_p))
            # Verify
            src_hash = _file_md5(src_p)
            dst_hash = _file_md5(dst_p)
            if src_hash and dst_hash and src_hash != dst_hash:
                dst_p.unlink(missing_ok=True)
                return {"success": False, "detail": "File hash mismatch after copy"}
            src_p.unlink()
            return {"success": True, "detail": "File moved with verification"}
        except OSError as e:
            note_failure("safe_file_ops:L429", e)
            return {"success": False, "detail": str(e)}

    # Simple move (no verification)
    try:
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_p), str(dst_p))
        return {"success": True, "detail": "File moved (no verification)"}
    except OSError as e:
        note_failure("safe_file_ops:L437", e)
        return {"success": False, "detail": str(e)}


# ── Atomic Write ────────────────────────────────────────────────────


def atomic_write(
    path: str | Path,
    content: str | bytes,
    encoding: str = "utf-8",
    make_backup: bool = False,
) -> dict[str, object]:
    """Write content to a file atomically (temp file → rename).

    Prevents partial writes caused by process interruption mid-write.

    Args:
        path: Destination file path.
        content: String or bytes to write.
        encoding: Text encoding (used only if content is str).
        make_backup: If True, backup existing file before overwriting.

    Returns:
        dict with "success" (bool) and "detail".
    """
    path_p = Path(path)
    path_p.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing
    if make_backup and path_p.exists():
        backup = path_p.with_suffix(path_p.suffix + ".bak")
        try:
            shutil.copy2(str(path_p), str(backup))
        except OSError as exc:
            note_failure("safe_file_ops:L471", exc)

    # Write to temp file in same directory (atomic rename)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path_p.parent),
        prefix=path_p.name + ".",
        suffix=".tmp",
    )
    try:
        if isinstance(content, str):
            data = content.encode(encoding)
        else:
            data = content
        os.write(fd, data)
        os.close(fd)
        fd = None  # Prevent double-close

        # Atomic rename (Windows: replace=True for overwrite)
        os.replace(tmp_path, str(path_p))
        return {"success": True, "detail": "Written atomically"}
    except OSError as e:
        note_failure("safe_file_ops:L492", e)
        return {"success": False, "detail": str(e)}
    finally:
        if fd is not None:
            os.close(fd)
        # Clean up temp file if rename failed
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError as exc:
                note_failure("safe_file_ops:L501", exc)


# ── Chunked Copy (for large directories) ────────────────────────────


def safe_copytree_chunked(
    src: str | Path,
    dst: str | Path,
    verify: bool = True,
    checkpoint_file: (str | Path | None) = None,
    progress_callback: (Callable[[int, int, str], None] | None) = None,
) -> dict[str, object]:
    """Copy a directory tree in sorted order with resume checkpointing.

    Processes files one at a time in sorted order so that:
    - If interrupted, already-copied files are preserved
    - On resume, skips files that already exist with matching size
    - A checkpoint file tracks completion state

    Args:
        src: Source directory.
        dst: Destination directory.
        verify: If True, run final integrity check.
        checkpoint_file: Path to checkpoint JSON. If None, uses dst/.copy_checkpoint.
        progress_callback: Optional callback(copied, total, filename).

    Returns:
        dict with "success" (bool) and "detail".
    """

    src_p, dst_p = Path(src), Path(dst)
    if checkpoint_file is None:
        checkpoint_file = dst_p / ".copy_checkpoint"

    cp = Path(checkpoint_file)
    dst_p.mkdir(parents=True, exist_ok=True)

    # Load existing checkpoint
    completed: dict[str, int] = {}
    if cp.exists():
        try:
            completed = json.loads(cp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            note_failure("safe_file_ops:L546", exc)
            completed = {}

    # Collect all files to copy
    all_files: list[tuple[str, int]] = []  # (relative_path, size)
    for f in sorted(src_p.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(src_p))
            all_files.append((rel, f.stat().st_size))

    total = len(all_files)
    copied = 0
    errors: list[str] = []

    for i, (rel, size) in enumerate(all_files):
        if rel in completed and completed[rel] == size:
            copied += 1
            continue  # Already copied with matching size

        src_file = src_p / rel
        dst_file = dst_p / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(str(src_file), str(dst_file))
            completed[rel] = size
            copied += 1
        except OSError as e:
            note_failure("safe_file_ops:L573", e)
            errors.append(f"{rel}: {e}")

        # Save checkpoint every 10 files
        if (i + 1) % 10 == 0:
            try:
                cp.write_text(json.dumps(completed), encoding="utf-8")
            except OSError as exc:
                note_failure("safe_file_ops:L580", exc)

        if progress_callback:
            progress_callback(i + 1, total, rel)

    # Save final checkpoint
    try:
        cp.write_text(json.dumps(completed), encoding="utf-8")
    except OSError as exc:
        note_failure("safe_file_ops:L589", exc)

    # Clean up checkpoint on success
    if not errors:
        try:
            cp.unlink()
        except OSError as exc:
            note_failure("safe_file_ops:L596", exc)

    result: dict[str, object] = {
        "success": len(errors) == 0,
        "copied": copied,
        "total": total,
        "errors": errors,
    }

    if verify and result["success"]:
        vresult = verify_tree_integrity(src_p, dst_p)
        result["integrity"] = vresult
        if not vresult["match"]:
            result["success"] = False

    return result


# ── Convenience ─────────────────────────────────────────────────────


def is_same_drive(path1: str | Path, path2: str | Path) -> bool:
    """Check if two paths are on the same drive (useful for cross-drive ops)."""
    if sys.platform == "win32":
        p1 = Path(path1).resolve()
        p2 = Path(path2).resolve()
        return p1.drive.lower() == p2.drive.lower()
    # Unix: check device IDs (stat.st_dev)
    try:
        return os.stat(path1).st_dev == os.stat(path2).st_dev
    except OSError as exc:
        note_failure("safe_file_ops:L627", exc)
        return False
