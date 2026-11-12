# coding: utf-8
"""
Path Manager for PERSEPHONE.

Advanced path handling for cross-platform development.
"""

from pathlib import Path
# from typing import Optional, Dict, List  # removed — all names have native equivalents
from dataclasses import dataclass
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class PathInfo:
    """Path information container."""

    name: str
    windows: str
    wsl: str
    exists: bool = False


class PathManager:
    """Manage cross-platform paths for Azazael and PERSEPHONE."""

    def __init__(self):
        self.paths: dict[str, PathInfo] = {}
        self._register_default_paths()

    def _register_default_paths(self):
        """Register default paths."""
        _home = Path.home()
        _desktop = _home / "Desktop"
        _username = _home.name
        self.register(
            "azazael",
            str(_desktop / "Azazael"),
            f"/mnt/c/Users/{_username}/Desktop/Azazael",
        )
        self.register(
            "persephone",
            str(_desktop / "PERSEPHONE"),
            f"/mnt/c/Users/{_username}/Desktop/PERSEPHONE",
        )
        self.register("wsa", "\\\\wsl$Ubuntu\\home\\", "/home/")
        self.register(
            "desktop", str(_desktop), f"/mnt/c/Users/{_username}/Desktop"
        )
        self.register(
            "documents", str(_home / "Documents"), f"/mnt/c/Users/{_username}/Documents"
        )

    def register(self, name: str, windows: str, wsl: str):
        """Register a path for cross-platform access."""
        exists = Path(windows).exists() or Path(wsl).exists()
        self.paths[name] = PathInfo(name, windows, wsl, exists)

    def get(self, name: str, platform: str | None = None) -> (PathInfo | None):
        """Get path information by name."""
        return self.paths.get(name)

    def to_wsl(self, windows_path: str) -> str:
        """Convert Windows path to WSL path."""
        if len(windows_path) >= 2 and windows_path[1] == ":":
            drive = windows_path[0].lower()
            return f"/mnt/{drive}{windows_path[2:]}".replace("\\", "/")
        return windows_path

    def to_windows(self, wsl_path: str) -> str:
        """Convert WSL path to Windows path."""
        if wsl_path.startswith("/mnt/"):
            drive = wsl_path[5].upper()
            return f"{drive}:{wsl_path[6:]}".replace("/", "\\")
        return wsl_path

    def detect(self, name: str) -> (PathInfo | None):
        """Detect if a path exists and update status."""
        if name in self.paths:
            info = self.paths[name]
            info.exists = Path(info.windows).exists() or Path(info.wsl).exists()
        return self.get(name)

    def list_all(self) -> list[PathInfo]:
        """list all registered paths."""
        return list(self.paths.values())

    def sync_paths(self) -> dict[str, bool]:
        """Sync all paths and return existence status."""
        results = {}
        for name, info in self.paths.items():
            info.exists = Path(info.windows).exists() or Path(info.wsl).exists()
            results[name] = info.exists
        return results


if __name__ == "__main__":
    pm = PathManager()
    for info in pm.list_all():
        print(f"{info.name}: {'[OK]' if info.exists else '[MISSING]'} {info.windows}")
