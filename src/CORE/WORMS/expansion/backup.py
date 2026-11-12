# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: BACKUP (5 worms)
StateSnapshotter, IncrementalBackupWorm, RecoveryTester, ColdArchivist, CheckpointWorm
"""

import json
import time
import hashlib
import threading
import zipfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, List
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("backup:L21", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("backup:L30", exc)
    _FREETHREAD = False
    FreeThreadPool = None
    ThreadSafeDict = dict
    ThreadSafeList = list
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("backup:L39", exc)
            return False


@register_worm(category="backup", priority=7, cooldown_seconds=60.0,
    description="State snapshotter — periodic full-state snapshots with compression and retention")
class StateSnapshotter:
    """Creates periodic state snapshots with compression."""

    VERSION = "1.0.0"

    def __init__(self):
        self._snapshots: list[dict] = []
        self._lock = threading.Lock()

    def create_snapshot(self, source_dir: str, target_dir: str, label: str = "") -> dict[str, Any]:
        """Create a compressed snapshot of a directory."""
        src = Path(source_dir)
        dst = Path(target_dir)
        if not src.exists():
            return {"success": False, "error": "Source not found"}
        dst.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"snapshot_{label or 'auto'}_{ts}"
        archive_path = dst / archive_name
        try:
            shutil.make_archive(str(archive_path), "zip", str(src))
            snap = {"label": label, "path": str(archive_path) + ".zip", "time": ts, "source": str(src)}
            with self._lock:
                self._snapshots.append(snap)
            return {"success": True, "snapshot": snap}
        except Exception as e:
            note_failure("backup:L70", e)
            return {"success": False, "error": str(e)}

    def list_snapshots(self) -> list[dict]:
        return list(self._snapshots)

    def get_summary(self):
        return {"status":"ok","worm":"StateSnapshotter"}


@register_worm(category="backup", priority=6, cooldown_seconds=90.0,
    description="Incremental backup worm — rsync-style incremental backup with changed-file detection")
class IncrementalBackupWorm:
    """Incremental backup with file change detection via modification time and checksums."""

    VERSION = "1.0.0"

    def __init__(self):
        self._file_db: dict[str, str] = {}
        self._lock = threading.Lock()

    def compute_manifest(self, directory: str) -> dict[str, str]:
        """Compute checksums for all files in a directory."""
        manifest = {}
        for f in Path(directory).rglob("*"):
            if f.is_file():
                try:
                    manifest[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
                except Exception as exc:
                    note_failure("backup:L98", exc)
        return manifest

    def find_changes(self, old_manifest: dict[str, str], new_manifest: dict[str, str]) -> dict[str, Any]:
        """Find added, modified, and deleted files between manifests."""
        old_keys, new_keys = set(old_manifest), set(new_manifest)
        added = list(new_keys - old_keys)
        deleted = list(old_keys - new_keys)
        modified = [k for k in (old_keys & new_keys) if old_manifest[k] != new_manifest[k]]
        return {"added": added, "modified": modified, "deleted": deleted, "total_changes": len(added) + len(modified) + len(deleted)}

    def get_summary(self):
        return {"status":"ok","worm":"IncrementalBackupWorm"}


@register_worm(category="backup", priority=5, cooldown_seconds=180.0,
    description="Recovery tester — validate backup integrity and perform dry-run restorations")
class RecoveryTester:
    """Validates backup integrity and performs dry-run restorations."""

    VERSION = "1.0.0"

    def __init__(self):
        self._test_results: list[dict] = []

    def verify_archive(self, archive_path: str) -> dict[str, Any]:
        """Verify a zip archive integrity."""
        path = Path(archive_path)
        if not path.exists():
            return {"valid": False, "error": "Archive not found"}
        try:
            with zipfile.ZipFile(path, "r") as zf:
                bad = zf.testzip()
                return {"valid": bad is None, "corrupted_file": bad, "file_count": len(zf.namelist())}
        except Exception as e:
            note_failure("backup:L133", e)
            return {"valid": False, "error": str(e)}

    def test_restore(self, archive_path: str, temp_dir: str) -> dict[str, Any]:
        """Perform a dry-run restore to temp directory."""
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(temp_dir)
            return {"success": True, "restored_to": temp_dir}
        except Exception as e:
            note_failure("backup:L142", e)
            return {"success": False, "error": str(e)}

    def get_summary(self):
        return {"status":"ok","worm":"RecoveryTester"}


@register_worm(category="backup", priority=4, cooldown_seconds=300.0,
    description="Cold archivist — compress and move old files to cold storage with indexing")
class ColdArchivist:
    """Archives old files to cold storage with searchable index."""

    VERSION = "1.0.0"

    def __init__(self):
        self._archive_index: dict[str, dict] = {}

    def archive(self, source: str, archive_path: str, older_than_days: int = 90) -> dict[str, Any]:
        """Archive files older than N days."""
        cutoff = time.time() - (older_than_days * 86400)
        archived = []
        for f in Path(source).rglob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                rel = f.relative_to(source)
                archived.append((str(rel), str(f)))
                self._archive_index[str(rel)] = {"archived_at": time.time(), "original_path": str(f)}
        if archived:
            Path(archive_path).mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d")
            zip_path = str(Path(archive_path) / f"cold_archive_{ts}.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for rel_path, abs_path in archived:
                    zf.write(abs_path, rel_path)
        return {"archived_count": len(archived), "files": [a[0] for a in archived[:800]]}
    def get_summary(self):
        # The archive index IS the run record: report what was archived rather
        # than asserting OK. An empty index means nothing has been archived yet,
        # which is not the same result as a successful archive pass.
        index = self._archive_index
        if not index:
            return {"worm": "ColdArchivist", "status": "not_run",
                    "archived_total": 0, "run_recorded": False}
        return {"worm": "ColdArchivist", "status": "archived",
                "archived_total": len(index), "run_recorded": True,
                "last_archived_at": max(v.get("archived_at", 0.0)
                                        for v in index.values())}



@register_worm(category="backup", priority=6, cooldown_seconds=30.0,
    description="Checkpoint worm — save worm execution state for resume-after-failure")
class CheckpointWorm:
    """Saves worm execution state for resume-after-failure recovery."""

    VERSION = "1.0.0"

    def __init__(self):
        self._checkpoints: dict[str, dict] = {}
        self._lock = threading.Lock()

    def save_checkpoint(self, worm_id: str, state: dict[str, Any]) -> str:
        """Save a checkpoint for a worm."""
        ckpt = {"state": state, "timestamp": datetime.now().isoformat(),
                "checksum": hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:12]}
        with self._lock:
            self._checkpoints[worm_id] = ckpt
        return ckpt["checksum"]

    def restore_checkpoint(self, worm_id: str) -> (dict[str, Any] | None):
        """Restore state from last checkpoint."""
        ckpt = self._checkpoints.get(worm_id)
        return ckpt["state"] if ckpt else None


    def get_summary(self):
        return {"status":"ok","worm":"CheckpointWorm","method":"save_checkpoint"}