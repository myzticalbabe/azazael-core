# coding: utf-8
"""
Environment Manager for PERSEPHONE.

Cross-platform environment configuration.
"""

import os
import sys
from pathlib import Path
# from typing import Dict, Optional  # removed — all names have native equivalents
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
class EnvVar:
    """Environment variable container."""

    name: str
    value: str
    platform: str = "all"  # windows, wsl, all

    def apply(self):
        """Apply environment variable."""
        os.environ[self.name] = self.value


class EnvironmentManager:
    """Manage cross-platform environment configuration."""

    def __init__(self):
        self.variables: dict[str, EnvVar] = {}
        self._register_defaults()

    def _register_defaults(self):
        """Register default environment variables."""
        _home = Path.home()
        _desktop = _home / "Desktop"
        _username = _home.name
        self.set("Azazael_ROOT", str(_desktop / "Azazael"), "all")
        self.set("PERSEPHONE_ROOT", str(_desktop / "PERSEPHONE"), "all")
        self.set(
            "PYTHONPATH",
            str(_desktop / "Azazael" / "ml_platforms") + ";" + str(_desktop / "PERSEPHONE" / "lib"),
            "windows",
        )
        self.set(
            "PYTHONPATH",
            f"/mnt/c/Users/{_username}/Desktop/Azazael/ml_platforms:/mnt/c/Users/{_username}/Desktop/PERSEPHONE/lib",
            "wsl",
        )

    def set(self, name: str, value: str, platform: str = "all"):
        """set an environment variable."""
        self.variables[name] = EnvVar(name, value, platform)

    def get(self, name: str) -> (str | None):
        """Get environment variable value."""
        if name in self.variables:
            var = self.variables[name]
            if var.platform in ["all", self._current_platform()]:
                return var.value
        return os.environ.get(name)

    def _current_platform(self) -> str:
        """Detect current platform."""
        if sys.platform == "win32":
            return "windows"
        return "wsl"

    def apply_all(self):
        """Apply all registered environment variables."""
        for name, var in self.variables.items():
            if var.platform in ["all", self._current_platform()]:
                os.environ[name] = var.value

    def get_bashrc_snippet(self) -> str:
        """Generate bashrc snippet for WSL."""
        lines = ["# PERSEPHONE Environment"]
        for name, var in self.variables.items():
            if var.platform in ["all", "wsl"]:
                lines.append(f'export {name}="{var.value}"')
        return "\n".join(lines)

    def get_powershell_snippet(self) -> str:
        """Generate PowerShell snippet for Windows."""
        lines = ["# PERSEPHONE Environment"]
        for name, var in self.variables.items():
            if var.platform in ["all", "windows"]:
                lines.append(f'$env:{name}="{var.value}"')
        return "\n".join(lines)


if __name__ == "__main__":
    em = EnvironmentManager()
    print("Environment variables:")
    for name, var in em.variables.items():
        print(f"  {name} = {var.value} ({var.platform})")
