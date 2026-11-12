# coding: utf-8
"""
PERSEPHONE - Cross-Platform WSL Utilities

Path translation and co-development utilities for Azazael + PERSEPHONE
running across Windows, WSL, and WSA.

Features:
- Windows <-> WSL path translation
- Azazael/PERSEPHONE path discovery
- Cross-platform file synchronization
- Environment configuration

Usage:
    >>> from persephone import Persephone
    >>> p = Persephone()
    >>> print(p.azazael_root)  # Auto-detected Azazael root
    >>> print(p.persephone_root)  # PERSEPHONE root

Author: Azazael Enhancement System
Date: 2026-02-10
Version: 1.0.0
"""

import os
import sys
import subprocess
from pathlib import Path
# from typing import Dict, List, Optional, Tuple  # removed — all names have native equivalents
from dataclasses import dataclass
from enum import Enum
from typing import List
import json

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class Platform(Enum):
    WINDOWS = "windows"
    WSL = "wsl"
    WSA = "wsa"
    LINUX = "linux"
    UNKNOWN = "unknown"


@dataclass
class CrossPath:
    """Represents a path that can be expressed in multiple formats."""

    wsl_path: str = ""
    windows_path: str = ""
    absolute_path: (Path | None) = None


class PersephoneConfig:
    """Configuration for PERSEPHONE cross-platform development."""

    # Try to import version from Azazael, fallback to defaults


try:
    from version import Azazael_VERSION, PROTOCOL_VERSION, PERSEPHONE_VERSION
except ImportError as exc:
    note_failure("persephone:L59", exc)
    Azazael_VERSION = "2.1.0"
    PROTOCOL_VERSION = "2.0.0"
    PERSEPHONE_VERSION = "1.0.0"

    # Path constants — dynamically resolved from home dir
    _home = Path.home()
    _desktop = _home / "Desktop"
    _username = _home.name
    PATHS = {
        "azazael": {
            "windows": str(_desktop / "Azazael"),
            "wsl": f"/mnt/c/Users/{_username}/Desktop/Azazael",
            "alias": "azazael",
        },
        "persephone": {
            "windows": str(_desktop / "PERSEPHONE"),
            "wsl": f"/mnt/c/Users/{_username}/Desktop/PERSEPHONE",
            "alias": "persephone",
        },
        "tarbaby": {
            "windows": str(_desktop / "TARBABY"),
            "wsl": f"/mnt/c/Users/{_username}/Desktop/TARBABY",
        },
        "horus": {
            "windows": str(_desktop / "HORUS"),
            "wsl": f"/mnt/c/Users/{_username}/Desktop/HORUS",
        },
        "hydra": {
            "windows": str(_desktop / "HYDRA"),
            "wsl": f"/mnt/c/Users/{_username}/Desktop/HYDRA",
        },
    }

    # For backward compatibility
    DEFAULT_PATHS = PATHS

    def __init__(self):
        self.config_dir = Path.home() / ".persephone"
        self.config_file = self.config_dir / "config.json"
        self.environ_file = self.config_dir / ".env"
        self.paths = self._load_config()

    def _load_config(self) -> dict:
        """Load configuration."""
        paths = self.DEFAULT_PATHS.copy()

        if self.config_file.exists():
            import json

            with open(self.config_file, encoding="utf-8") as f:
                user_config = json.load(f)
                paths.update(user_config)

        return paths

    def save(self):
        """Save configuration."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.paths, f, indent=2)

    def get_path(self, name: str, platform: (str | None) = None) -> str:
        """Get path for a named location."""
        if platform is None:
            platform = detect_platform().value

        if name in self.paths:
            return self.paths[name].get(platform, self.paths[name].get("windows", ""))

        return ""

    def set_path(self, name: str, windows_path: str, wsl_path: (str | None) = None):
        """set path for a named location."""
        if name not in self.paths:
            self.paths[name] = {}

        self.paths[name]["windows"] = windows_path
        if wsl_path:
            self.paths[name]["wsl"] = wsl_path
        else:
            self.paths[name]["wsl"] = wsl_to_wsl(windows_path)

        self.save()


class PathTranslator:
    """Translate paths between Windows, WSL, and WSA."""

    @staticmethod
    def wsl_to_windows(wsl_path: str) -> str:
        """Convert WSL path to Windows path.

        Args:
            wsl_path: Path in WSL format (e.g., /mnt/c/Users/...)

        Returns:
            Windows path (e.g., C:\\Users\\...)
        """
        if not wsl_path:
            return ""

        if wsl_path.startswith("/mnt/"):
            drive = wsl_path[5].upper()
            windows_path = f"{drive}:{wsl_path[6:]}"
            return windows_path.replace("/", "\\")

        if wsl_path.startswith("/"):
            return f"\\\\wsl$\\{wsl_path[1:]}"

        return wsl_path

    @staticmethod
    def windows_to_wsl(windows_path: str) -> str:
        """Convert Windows path to WSL path.

        Args:
            windows_path: Path in Windows format

        Returns:
            WSL path (e.g., /mnt/c/Users/...)
        """
        if not windows_path:
            return ""

        if len(windows_path) >= 2 and windows_path[1] == ":":
            drive = windows_path[0].lower()
            wsl_path = f"/mnt/{drive}{windows_path[2:]}"
            return wsl_path.replace("\\", "/")

        if windows_path.startswith("\\\\wsl$\\"):
            return windows_path.replace("\\\\wsl$\\", "/")

        return windows_path

    @staticmethod
    def windows_to_wsa(windows_path: str) -> str:
        """Convert Windows path to WSA accessible path.

        Args:
            windows_path: Windows path

        Returns:
            Path accessible from WSA via ADB
        """
        if not windows_path:
            return ""

        drive = windows_path[0].upper()
        fixed_path = windows_path[2:].replace("\\", "/")
        return f"/mnt/sdcard/{drive}{fixed_path}"

    @staticmethod
    def detect_azazael() -> CrossPath:
        """Detect Azazael installation path.

        Searches:
        1. Azazael_ROOT environment variable
        2. Common Windows locations
        3. Common WSL locations
        """
        _username = Path.home().name
        _desktop = Path.home() / "Desktop"
        wsl_path = f"/mnt/c/Users/{_username}/Desktop/Azazael"
        windows_path = str(_desktop / "Azazael")

        env_path = os.environ.get("Azazael_ROOT", "")
        if env_path:
            if env_path.startswith("/mnt/"):
                wsl_path = env_path
                windows_path = PathTranslator.wsl_to_windows(env_path)
            else:
                windows_path = env_path
                wsl_path = PathTranslator.windows_to_wsl(env_path)

        if Path(wsl_path).exists() or Path(windows_path).exists():
            abs_path = Path(wsl_path) if Path(wsl_path).exists() else Path(windows_path)
            return CrossPath(
                wsl_path=wsl_path, windows_path=windows_path, absolute_path=abs_path
            )

        return CrossPath(wsl_path=wsl_path, windows_path=windows_path)

    @staticmethod
    def detect_persephone() -> CrossPath:
        """Detect PERSEPHONE installation path.

        Searches:
        1. PERSEPHONE_ROOT environment variable
        2. Common Windows locations
        3. Common WSL locations
        """
        _username = Path.home().name
        _desktop = Path.home() / "Desktop"
        wsl_path = f"/mnt/c/Users/{_username}/Desktop/PERSEPHONE"
        windows_path = str(_desktop / "PERSEPHONE")

        env_path = os.environ.get("PERSEPHONE_ROOT", "")
        if env_path:
            if env_path.startswith("/mnt/"):
                wsl_path = env_path
                windows_path = PathTranslator.wsl_to_windows(env_path)
            else:
                windows_path = env_path
                wsl_path = PathTranslator.windows_to_wsl(env_path)

        if Path(wsl_path).exists() or Path(windows_path).exists():
            abs_path = Path(wsl_path) if Path(wsl_path).exists() else Path(windows_path)
            return CrossPath(
                wsl_path=wsl_path, windows_path=windows_path, absolute_path=abs_path
            )

        return CrossPath(wsl_path=wsl_path, windows_path=windows_path)


class Persephone:
    """
    Main class for PERSEPHONE cross-platform development.

    Provides utilities for:
    - Path translation
    - Azazael integration
    - File synchronization
    - Environment management
    """

    def __init__(self):
        self.translator = PathTranslator()
        self.config = PersephoneConfig()
        self.azazael = self.translator.detect_azazael()
        self.persephone = self.translator.detect_persephone()
        self.platform = detect_platform()

    @property
    def pythonpath_extensions(self) -> list[str]:
        """Get Python path extensions for cross-platform development."""
        paths = []

        if self.azazael.absolute_path:
            ml_path = self.azazael.wsl_path + "/ml_platforms"
            if self.platform == Platform.WINDOWS:
                ml_path = self.azazael.windows_path + "\\ml_platforms"
            paths.append(ml_path)

            scripts_path = self.azazael.wsl_path + "/scripts"
            if self.platform == Platform.WINDOWS:
                scripts_path = self.azazael.windows_path + "\\scripts"
            paths.append(scripts_path)

        if self.persephone.absolute_path:
            lib_path = self.persephone.wsl_path + "/lib"
            if self.platform == Platform.WINDOWS:
                lib_path = self.persephone.windows_path + "\\lib"
            paths.append(lib_path)

        return paths

    def sync_to_wsl(self, source: (str | None) = None, dry_run: bool = True) -> dict:
        """Sync files from Windows to WSL.

        Args:
            source: Source path (defaults to Azazael)
            dry_run: Print actions without executing

        Returns:
            Sync operation results
        """
        if source is None:
            source = self.azazael.windows_path

        results = {
            "source": source,
            "target": self.translator.windows_to_wsl(source),
            "files_synced": 0,
            "errors": [],
        }

        source_path = Path(source)
        if not source_path.exists():
            results["errors"].append(f"Source not found: {source}")
            return results

        try:
            cmd = ["wsl", "rsync", "-avz", "--exclude='__pycache__'"]
            if dry_run:
                cmd.append("--dry-run")

            cmd.extend(
                [source.replace("\\", "/"), self.translator.windows_to_wsl(source)]
            )

            if not dry_run:
                subprocess.run(cmd, check=True, timeout=1800)
                results["files_synced"] = "multiple"

        except subprocess.CalledProcessError as e:
            note_failure("persephone:L356", e)
            results["errors"].append(str(e))

        return results

    def run_in_wsl(
        self, command: str, python_env: (str | None) = None
    ) -> tuple[str, str, int]:
        """Run command in WSL.

        Args:
            command: Command to run
            python_env: Python environment name

        Returns:
            tuple of (stdout, stderr, returncode)
        """
        full_command = command
        if python_env:
            full_command = (
                f"source ~/.bashrc && conda activate {python_env} && {command}"
            )

        try:
            result = subprocess.run(
                ["wsl", "bash", "-c", full_command], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1800)
            return result.stdout, result.stderr, result.returncode
        except Exception as e:
            note_failure("persephone:L384", e)
            return "", str(e), -1

    def setup_environment(self, shell: str = "bash") -> str:
        """Generate environment setup script.

        Args:
            shell: Shell type (bash, zsh, powershell)

        Returns:
            Generated script content
        """
        if shell in ["bash", "zsh"]:
            return f'''# PERSEPHONE Environment Setup
# Add to ~/.bashrc or ~/.zshrc

# Paths
export Azazael_ROOT="{self.azazael.wsl_path}"
export PERSEPHONE_ROOT="{self.persephone.wsl_path}"

# Python paths
export PYTHONPATH="$Azazael_ROOT/ml_platforms:$PYTHONPATH"
export PYTHONPATH="$PERSEPHONE_ROOT/lib:$PYTHONPATH"

# Path additions
export PATH="$Azazael_ROOT/scripts:$PATH"
export PATH="$PERSEPHONE_ROOT/bin:$PATH"

# Aliases
alias azazael="python $Azazael_ROOT/scripts/azazael_cli.py"
alias persephone="python $PERSEPHONE_ROOT/bin/persephone.py"
alias sync="rsync -avz $Azazael_ROOT/ $PERSEPHONE_ROOT/ --exclude='__pycache__'"
alias ws="wsl"
'''
        elif shell == "powershell":
            return f'''# PERSEPHONE Environment Setup
# Add to $PROFILE

# Paths
$env:Azazael_ROOT = "{self.azazael.windows_path}"
$env:PERSEPHONE_ROOT = "{self.persephone.windows_path}"

# Python paths
$env:PYTHONPATH = "$env:Azazael_ROOT\\ml_platforms;$env:PYTHONPATH"
$env:PYTHONPATH = "$env:PERSEPHONE_ROOT\\lib;$env:PYTHONPATH"

# Aliases
set-Alias -Name azazael -Value "python $env:Azazael_ROOT\\scripts\\azazael_cli.py"
set-Alias -Name persephone -Value "python $env:PERSEPHONE_ROOT\\bin\\persephone.py"
'''

        return ""

    def create_shortcuts(self):
        """Create desktop shortcuts for cross-platform access."""

        shortcuts = [
            {
                "name": "Azazael (WSL)",
                "target": f"wsl python {self.azazael.wsl_path}/scripts/azazael_cli.py",
                "description": "Run Azazael CLI in WSL",
            },
            {
                "name": "PERSEPHONE",
                "target": f"wsl python {self.persephone.wsl_path}/bin/persephone.py",
                "description": "Run PERSEPHONE in WSL",
            },
            {
                "name": "Sync Azazael -> PERSEPHONE",
                "target": f"wsl rsync -avz {self.azazael.wsl_path}/ {self.persephone.wsl_path}/ --exclude=__pycache__",
                "description": "Sync files from Azazael to PERSEPHONE",
            },
        ]

        results = []
        desktop = Path.home() / "Desktop"

        for shortcut in shortcuts:
            name = shortcut["name"] + ".lnk"
            path = desktop / name

            import win32com.client  # type: ignore[reportMissingModuleSource]

            shell = win32com.client.Dispatch("WScript.Shell")
            ws = shell.CreateShortCut(str(path))
            ws.Targetpath = "wsl"
            ws.Arguments = "bash -c" if "rsync" in shortcut["target"] else ""
            ws.Description = shortcut["description"]
            ws.Save()

            results.append(str(path))

        return results

    def get_azazael_scripts(self) -> list[str]:
        """Get list of Azazael scripts with cross-platform paths."""
        scripts = []
        scripts_dir = (
            self.azazael.absolute_path / "scripts"
            if self.azazael.absolute_path
            else None
        )

        if scripts_dir and scripts_dir.exists():
            for py in scripts_dir.glob("*.py"):
                scripts.append(
                    {
                        "name": py.stem,
                        "wsl": f"{self.azazael.wsl_path}/scripts/{py.name}",
                        "windows": self.azazael.windows_path + "\\scripts\\" + py.name,
                    }
                )

        return scripts

    def get_persephone_libs(self) -> list[str]:
        """Get list of PERSEPHONE libraries with cross-platform paths."""
        libs = []
        lib_dir = (
            self.persephone.absolute_path / "lib"
            if self.persephone.absolute_path
            else None
        )

        if lib_dir and lib_dir.exists():
            for py in lib_dir.glob("*.py"):
                libs.append(
                    {
                        "name": py.stem,
                        "wsl": f"{self.persephone.wsl_path}/lib/{py.name}",
                        "windows": self.persephone.windows_path + "\\lib\\" + py.name,
                    }
                )

        return libs


def detect_platform() -> Platform:
    """Detect current platform."""
    if sys.platform == "win32":
        if (
            "microsoft" in sys.version.lower()
            or "wsl" in os.environ.get("WSL_DISTRO_NAME", "").lower()
        ):
            return Platform.WSL
        return Platform.WINDOWS

    if sys.platform == "linux":
        if os.environ.get("ANDROID_ROOT"):
            return Platform.WSA
        return Platform.LINUX

    return Platform.UNKNOWN


def wsl_to_wsl(path: str) -> str:
    """Alias for PathTranslator.wsl_to_windows."""
    return PathTranslator.wsl_to_windows(path)


def windows_to_wsl(path: str) -> str:
    """Alias for PathTranslator.windows_to_wsl."""
    return PathTranslator.windows_to_wsl(path)


def demo():
    """Demonstrate PERSEPHONE utilities."""
    print("=== PERSEPHONE Cross-Platform Demo ===\n")

    p = Persephone()
    platform = detect_platform()

    print(f"Platform detected: {platform.value}")

    print("\nAzazael Paths:")
    print(f"  WSL:      {p.azazael.wsl_path}")
    print(f"  Windows:  {p.azazael.windows_path}")

    print("\nPERSEPHONE Paths:")
    print(f"  WSL:      {p.persephone.wsl_path}")
    print(f"  Windows:  {p.persephone.windows_path}")

    print("\nPython Path Extensions:")
    for path in p.pythonpath_extensions:
        print(f"  {path}")

    print("\nPath Translation Examples:")
    test_path = str(Path.home() / "Desktop" / "Azazael" / "scripts")
    print(f"  Windows: {test_path}")
    print(f"  WSL:     {PathTranslator.windows_to_wsl(test_path)}")

    wsl_path = "/mnt/c/Users/clint/Desktop/PERSEPHONE"
    print(f"  WSL:     {wsl_path}")
    print(f"  Windows: {PathTranslator.wsl_to_windows(wsl_path)}")

    print("\nBash Environment Setup:")
    bash_script = p.setup_environment("bash")
    print(bash_script[:500] + "..." if len(bash_script) > 500 else bash_script)

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    demo()
