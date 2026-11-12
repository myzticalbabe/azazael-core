# coding: utf-8
"""
Sync Manager for PERSEPHONE.

File synchronization utilities between Windows, WSL, and WSA.

Features:
- Windows ↔ WSL synchronization
- WSA via ADB
- Bidirectional sync
- Progress tracking
- Dry run mode
- Exclude patterns

Usage:
    >>> from sync_manager import SyncManager
    >>> sm = SyncManager()
    >>> result = sm.sync_azazael_to_persephone()
"""

import subprocess
import json
from pathlib import Path
from typing import Callable, List
from dataclasses import dataclass, field
from datetime import datetime
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class SyncResult:
    """Result of a sync operation."""

    source: str
    target: str
    success: bool
    files_synced: int = 0
    files_deleted: int = 0
    bytes_transferred: int = 0
    duration: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "success": self.success,
            "files_synced": self.files_synced,
            "files_deleted": self.files_deleted,
            "bytes_transferred": self.bytes_transferred,
            "duration": self.duration,
            "errors": self.errors,
            "warnings": self.warnings,
            "timestamp": self.timestamp,
        }


@dataclass
class SyncStats:
    """Sync statistics."""

    total_files: int = 0
    synced_files: int = 0
    deleted_files: int = 0
    skipped_files: int = 0
    errors: int = 0


class SyncManager:
    """Manage file synchronization between platforms."""

    # Dynamic default paths
    _home = Path.home()
    _desktop = _home / "Desktop"
    _username = _home.name
    _DEFAULT_WIN_AZAZAEL = str(_desktop / "Azazael")
    _DEFAULT_WSL_AZAZAEL = f"/mnt/c/Users/{_username}/Desktop/Azazael"
    _DEFAULT_WIN_PERSEPHONE = str(_desktop / "PERSEPHONE")
    _DEFAULT_WSL_PERSEPHONE = f"/mnt/c/Users/{_username}/Desktop/PERSEPHONE"
    _DEFAULT_WIN_DESKTOP = str(_desktop)
    _DEFAULT_WSL_DESKTOP = f"/mnt/c/Users/{_username}/Desktop"

    def __init__(self):
        self.default_excludes = [
            "__pycache__",
            "*.pyc",
            "*.pyo",
            ".git",
            ".gitignore",
            "node_modules",
            ".venv",
            "venv",
            ".env",
            "*.log",
            "*.tmp",
            "*.swp",
            ".DS_Store",
            "Thumbs.db",
        ]
        self.progress_callback: (Callable | None) = None

    def _rsync_command(
        self, source: str, target: str, dry_run: bool = True, delete: bool = False
    ) -> list[str]:
        """Build rsync command."""
        cmd = ["wsl", "rsync", "-avz"]
        for exclude in self.default_excludes:
            cmd.extend(["--exclude", exclude])
        if delete:
            cmd.append("--delete")
        if dry_run:
            cmd.append("--dry-run")
        cmd.extend([source.replace("\\", "/"), target])
        return cmd

    def _robocopy_command(
        self, source: str, target: str, mirror: bool = False
    ) -> list[str]:
        """Build robocopy command."""
        cmd = ["robocopy", source, target, "/E", "/Z", "/B"]
        for exclude in self.default_excludes:
            cmd.extend(["/XD", exclude])
        if mirror:
            cmd.extend(["/MIR"])
        cmd.extend(["/MT:16", "/R:3", "/W:10"])
        return cmd

    def sync_windows_to_wsl(
        self,
        source: str | None = None,
        target: str | None = None,
        dry_run: bool = True,
        delete: bool = False,
    ) -> SyncResult:
        """Sync files from Windows to WSL."""
        if source is None:
            source = self._DEFAULT_WIN_AZAZAEL
        if target is None:
            target = source.replace(
                self._DEFAULT_WIN_DESKTOP, self._DEFAULT_WSL_DESKTOP
            )
            target = target.replace("\\", "/")

        result = SyncResult(source=source, target=target, success=False)
        start_time = datetime.now()

        if not Path(source).exists():
            result.errors.append(f"Source not found: {source}")
            result.duration = (datetime.now() - start_time).total_seconds()
            return result

        cmd = self._rsync_command(source, target, dry_run, delete)

        try:
            if dry_run:
                result.success = True
                result.warnings.append("Dry run - no files transferred")
            else:
                result_obj = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
                if result_obj.returncode in [0, 24]:
                    result.success = True
                    result.files_synced = -1  # Multiple
                else:
                    result.errors.append(
                        result_obj.stderr or f"Exit code: {result_obj.returncode}"
                    )
        except subprocess.CalledProcessError as e:
            note_failure("sync_manager:L173", e)
            result.errors.append(str(e))
        except FileNotFoundError as exc:
            note_failure("sync_manager:L175", exc)
            result.errors.append("rsync not found. Install via: sudo apt install rsync")

        result.duration = (datetime.now() - start_time).total_seconds()
        return result

    def sync_wsl_to_windows(
        self,
        source: str | None = None,
        target: str | None = None,
        dry_run: bool = True,
        mirror: bool = False,
    ) -> SyncResult:
        """Sync files from WSL to Windows."""
        if source is None:
            source = self._DEFAULT_WSL_AZAZAEL
        if target is None:
            target = source.replace(
                self._DEFAULT_WSL_DESKTOP, self._DEFAULT_WIN_DESKTOP
            )
            target = target.replace("/", "\\")

        result = SyncResult(source=source, target=target, success=False)

        if not dry_run:
            Path(target).mkdir(parents=True, exist_ok=True)
            cmd = self._robocopy_command(source, target, mirror)
            try:
                result_obj = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
                result.success = result_obj.returncode in [0, 1]
                result.files_synced = -1
            except Exception as e:
                note_failure("sync_manager:L206", e)
                result.errors.append(str(e))
        else:
            result.success = True
            result.warnings.append("Dry run - no files transferred")

        return result

    def sync_windows_to_wsa(
        self, source: str | None = None, wsa_path: str = "/mnt/sdcard/", dry_run: bool = True
    ) -> SyncResult:
        """Sync files from Windows to WSA via ADB."""
        if source is None:
            source = self._DEFAULT_WIN_AZAZAEL

        result = SyncResult(source=source, target=wsa_path, success=False)

        try:
            cmd = ["adb", "push", source, wsa_path]
            if dry_run:
                cmd.insert(2, "-n")

            if dry_run:
                result.success = True
                result.warnings.append("Dry run - no files transferred via ADB")
            else:
                result_obj = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
                if result_obj.returncode == 0:
                    result.success = True
                    result.files_synced = -1
                else:
                    result.errors.append(result_obj.stderr)
        except FileNotFoundError as exc:
            note_failure("sync_manager:L238", exc)
            result.errors.append("ADB not found. Install Android SDK platform tools.")

        return result

    def sync_bidirectional(
        self,
        windows_path: str = str(Path.home() / "Desktop" / "Azazael"),
        wsl_path: str = "/mnt/c/Users/clint/Desktop/Azazael",
        dry_run: bool = True,
    ) -> dict[str, SyncResult]:
        """Perform bidirectional synchronization."""
        results = {}

        results["windows_to_wsl"] = self.sync_windows_to_wsl(
            windows_path, wsl_path, dry_run
        )
        results["wsl_to_windows"] = self.sync_wsl_to_windows(
            wsl_path, windows_path, dry_run
        )

        return results

    def sync_azazael_to_persephone(
        self, dry_run: bool = True, bidirectional: bool = False
    ) -> dict[str, SyncResult]:
        """Sync Azazael to PERSEPHONE."""
        azazael_win = self._DEFAULT_WIN_AZAZAEL
        azazael_wsl = self._DEFAULT_WSL_AZAZAEL
        persephone_win = self._DEFAULT_WIN_PERSEPHONE
        persephone_wsl = self._DEFAULT_WSL_PERSEPHONE

        results = {}

        results["azazael_to_persephone"] = self.sync_windows_to_wsl(
            azazael_win, persephone_wsl, dry_run
        )

        if bidirectional:
            results["persephone_to_azazael"] = self.sync_windows_to_wsl(
                persephone_win, azazael_wsl, dry_run
            )

        return results

    def sync_ecosystem(self, dry_run: bool = True) -> dict[str, SyncResult]:
        """Sync complete ecosystem (Azazael ↔ HYDRA ↔ HORUS ↔ BIBL)."""
        results = {}

        base_win = self._DEFAULT_WIN_DESKTOP
        base_wsl = self._DEFAULT_WSL_DESKTOP

        sync_pairs = [
            ("Azazael", "Azazael"),
            ("HYDRA", "HYDRA"),
            ("HORUS", "HORUS"),
            ("PERSEPHONE", "PERSEPHONE"),
        ]

        for src, dst in sync_pairs:
            src_win = base_win + "\\" + src
            dst_wsl = f"{base_wsl}/{dst}"
            results[f"{src}_to_{dst}"] = self.sync_windows_to_wsl(
                src_win, dst_wsl, dry_run
            )

        return results

    def get_sync_preview(self, source: str) -> dict:
        """Get preview of what would be synced."""
        result = {
            "source": source,
            "files_count": 0,
            "total_size": 0,
            "excluded_patterns": self.default_excludes,
            "would_sync": [],
            "would_delete": [],
        }

        if not Path(source).exists():
            result["error"] = "Source not found"
            return result

        for item in Path(source).rglob("*"):
            if item.is_file():
                rel_path = str(item.relative_to(source))
                excluded = any(
                    exc in rel_path or rel_path.endswith(exc.replace("*", ""))
                    for exc in self.default_excludes
                )
                if not excluded:
                    result["files_count"] += 1
                    result["total_size"] += item.stat().st_size
                    result["would_sync"].append(rel_path)

        result["total_size_mb"] = result["total_size"] / (1024 * 1024)

        return result

    def save_sync_log(self, result: SyncResult, log_path: str | Path | None = None):
        """Save sync result to log file."""
        if log_path is None:
            log_path = Path.home() / ".persephone" / "sync_log.json"

        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict(), indent=2) + "\n")

    def get_sync_history(self, limit: int = 10) -> list[SyncResult]:
        """Get recent sync history."""
        log_path = Path.home() / ".persephone" / "sync_log.json"

        if not log_path.exists():
            return []

        results = []
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f.readlines()[-limit:]:
                data = json.loads(line)
                results.append(SyncResult(**data))

        return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PERSEPHONE Sync Manager")
    parser.add_argument(
        "--azazael", action="store_true", help="Sync Azazael to PERSEPHONE"
    )
    parser.add_argument(
        "--ecosystem", action="store_true", help="Sync complete ecosystem"
    )
    parser.add_argument("--preview", action="store_true", help="Preview sync")
    parser.add_argument(
        "--bidirectional", "-b", action="store_true", help="Bidirectional sync"
    )
    parser.add_argument(
        "--execute", "-x", action="store_true", help="Execute sync (not dry run)"
    )
    parser.add_argument("--wsa", action="store_true", help="Sync to WSA via ADB")

    args = parser.parse_args()

    sm = SyncManager()

    if args.azazael:
        results = sm.sync_azazael_to_persephone(
            dry_run=not args.execute, bidirectional=args.bidirectional
        )
        for name, result in results.items():
            print(f"{name}: {'OK' if result.success else 'FAILED'}")

    elif args.ecosystem:
        results = sm.sync_ecosystem(dry_run=not args.execute)
        for name, result in results.items():
            print(f"{name}: {'OK' if result.success else 'FAILED'}")

    elif args.preview:
        preview = sm.get_sync_preview(SyncManager._DEFAULT_WIN_AZAZAEL)
        print(f"Files: {preview['files_count']}")
        print(f"Size: {preview.get('total_size_mb', 0):.2f} MB")

    elif args.wsa:
        result = sm.sync_windows_to_wsa(dry_run=not args.execute)
        print(f"WSA Sync: {'OK' if result.success else 'FAILED'}")

    else:
        parser.print_help()
