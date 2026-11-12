# coding: utf-8
"""
UVZ - uv Integration Layer for Azazael Ecosystem

Fast Python package management integration using uv.

Features:
- uv-based dependency management
- Virtual environment orchestration
- Fast package installation
- Lockfile generation
- Multi-environment support

Usage:
    >>> from METAWORMS.UVZ import UVZ
    >>> uvz = UVZ()
    >>> uvz.install("requests")
    >>> uvz.sync_requirements("requirements.txt")
"""

import subprocess
import json
import os
import time
from pathlib import Path
from typing import Any, List
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
class UVResult:
    """Result from uv operation."""

    success: bool
    command: str
    output: str = ""
    duration_ms: float = 0.0
    packages: list[str] = field(default_factory=list)
    error: str = ""


class UVZ:
    """uv Integration Layer."""

    VERSION = "1.0.0"

    def __init__(self, venv_path: (str | None) = None):
        self.uv_path = self._find_uv()
        self.venv_path = venv_path or self._detect_venv()
        self.cache_dir = Path.home() / ".cache" / "azazael" / "uv"

    def _find_uv(self) -> str:
        """Find uv executable."""
        result = subprocess.run(["where", "uv"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if result.returncode == 0:
            return result.stdout.strip().split("\n")[0]
        for path in [
            os.environ.get("LOCALAPPDATA", "") + "\\Programs\\uv\\uv.exe",
            str(Path.home() / ".local" / "bin" / "uv.exe"),
            str(Path.home() / ".local" / "bin" / "uv"),
        ]:
            if Path(path).exists():
                return path
        return "uv"

    def _detect_venv(self) -> (str | None):
        """Detect existing virtual environment."""
        for venv in [
            ".venv",
            "venv",
            str(Path.home() / ".local" / "share" / "uv" / "venv"),
        ]:
            if Path(venv).exists():
                return str(Path(venv).absolute())
        return None

    def _run_uv(self, args: list[str], cwd: (str | None) = None) -> UVResult:
        """Run uv command."""
        start = time.time()
        cmd = [self.uv_path] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                cwd=cwd or str(Path(__file__).parent.parent),
                timeout=300,
            )
            return UVResult(
                success=result.returncode == 0,
                command=" ".join(cmd),
                output=result.stdout + result.stderr,
                duration_ms=int((time.time() - start) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("UVZ:L96", exc)
            return UVResult(
                success=False,
                command=" ".join(cmd),
                error="Command timed out",
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as e:
            note_failure("UVZ:L103", e)
            return UVResult(
                success=False,
                command=" ".join(cmd),
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    def create_venv(
        self, path: str, python: str = "python3", force: bool = False
    ) -> UVResult:
        """Create virtual environment."""
        args = ["venv", path, "--python", python]
        if force:
            args.insert(1, "--clear")
        return self._run_uv(args)

    def install(
        self,
        packages: list[str],
        venv: (str | None) = None,
        dev: bool = False,
    ) -> UVResult:
        """Install packages."""
        venv_path = venv or self.venv_path
        args = ["pip", "install"]
        if dev:
            args.insert(1, "--dev")
        if venv_path:
            args.extend(["--python", f"{venv_path}/Scripts/python.exe"])
        args.extend(packages)
        return self._run_uv(args)

    def add(self, package: str, dev: bool = False) -> UVResult:
        """Add package to project."""
        args = ["add", package]
        if dev:
            args.insert(1, "--dev")
        return self._run_uv(args)

    def remove(self, package: str) -> UVResult:
        """Remove package."""
        return self._run_uv(["remove", package])

    def sync(self, requirements: (str | None) = None) -> UVResult:
        """Sync dependencies from lockfile or requirements."""
        args = ["sync"]
        if requirements:
            args.append(requirements)
        return self._run_uv(args)

    def lock(self, requirements: (str | None) = None) -> UVResult:
        """Generate lockfile."""
        args = ["lock"]
        if requirements:
            args.extend(["--requirements", requirements])
        return self._run_uv(args)

    def list_packages(self, venv: (str | None) = None) -> list[dict[str, str]]:
        """list installed packages."""
        venv_path = venv or self.venv_path
        args = ["pip", "list"]
        if venv_path:
            args.extend(["--python", f"{venv_path}/Scripts/python.exe"])
        result = self._run_uv(args)
        packages = []
        for line in result.output.split("\n"):
            if " " in line and not line.startswith("-"):
                parts = line.split()
                if len(parts) >= 2:
                    packages.append(
                        {
                            "name": parts[0],
                            "version": parts[1],
                        }
                    )
        return packages

    def export_requirements(self, output: str = "requirements.txt") -> UVResult:
        """Export installed packages to requirements.txt."""
        return self._run_uv(["pip", "export", "-o", output])

    def run_script(self, script: str, venv: (str | None) = None) -> UVResult:
        """Run Python script in uv context."""
        venv_path = venv or self.venv_path
        args = ["run", "--python", f"{venv_path}/Scripts/python.exe", script]
        return self._run_uv(args)

    def check_updates(self) -> list[dict[str, str]]:
        """Check for outdated packages."""
        result = self._run_uv(["pip", "list", "--outdated"])
        updates = []
        for line in result.output.split("\n"):
            if " " in line and not line.startswith("-"):
                parts = line.split()
                if len(parts) >= 3:
                    updates.append(
                        {
                            "name": parts[0],
                            "current": parts[1],
                            "latest": parts[2],
                        }
                    )
        return updates

    def upgrade_all(self) -> UVResult:
        """Upgrade all packages."""
        return self._run_uv(["pip", "install", "--upgrade"])

    def create_requirements_from_venv(
        self, venv: (str | None) = None
    ) -> dict[str, Any]:
        """Create pinned requirements from virtual environment."""
        packages = self.list_packages(venv)
        requirements = {
            "generated": datetime.now().isoformat(),
            "packages": packages,
            "count": len(packages),
        }
        return requirements

    def benchmark_install(self, packages: list[str]) -> dict[str, Any]:
        """Benchmark uv pip install speed."""
        start = time.time()
        result = self.install(packages)
        return {
            "packages": packages,
            "success": result.success,
            "duration_ms": int((time.time() - start) * 1000),
            "packages_per_second": len(packages) / max((time.time() - start), 0.001),
        }


def main():
    """CLI entry point."""

    parser = argparse.ArgumentParser(description="UVZ - uv Integration")
    parser.add_argument("--version", action="version", version=UVZ.VERSION)
    parser.add_argument("--list", action="store_true", help="list packages")
    parser.add_argument("--install", nargs="+", help="Install packages")
    parser.add_argument("--sync", help="Sync from requirements")
    parser.add_argument("--lock", action="store_true", help="Generate lockfile")
    parser.add_argument("--bench", nargs="+", help="Benchmark install")
    parser.add_argument("--venv", help="Virtual environment path")

    args = parser.parse_args()
    uvz = UVZ(venv_path=args.venv)

    if args.list:
        for pkg in uvz.list_packages():
            print(f"{pkg['name']}=={pkg['version']}")
    elif args.install:
        result = uvz.install(args.install)
        print(f"Success: {result.success}, Duration: {result.duration_ms}ms")
    elif args.sync:
        result = uvz.sync(args.sync)
        print(f"Sync: {result.success}")
    elif args.lock:
        result = uvz.lock()
        print(f"Lock: {result.success}")
    elif args.bench:
        bench = uvz.benchmark_install(args.bench)
        print(json.dumps(bench, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
