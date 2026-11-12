import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

#!/usr/bin/env python3
"""
PERSEPHONE Setup Wizard

First-time setup for cross-platform development.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.path_manager import PathManager
from lib.environment import EnvironmentManager
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def check_prerequisites():
    """Check if prerequisites are met."""
    print("Checking prerequisites...")
    pm = PathManager()

    all_ok = True
    for info in pm.list_all():
        if info.exists:
            print(f"  [OK] {info.name}: {info.windows}")
        else:
            print(f"  [MISSING] {info.name}: {info.windows}")
            all_ok = False

    return all_ok


def setup_environment():
    """set up environment configuration."""
    print("\nSetting up environment...")
    em = EnvironmentManager()
    em.apply_all()
    print("  Environment variables applied")


def create_directories():
    """Create required directories."""
    print("\nCreating directories...")
    dirs = ["lib", "bin", "data", "models", "notebooks"]
    base = Path(__file__).parent.parent

    for d in dirs:
        path = base / d
        path.mkdir(exist_ok=True)
        print(f"  [OK] {d}/")


def setup_wsl_aliases():
    """set up WSL aliases."""
    print("\nWSL aliases can be added to ~/.bash_aliases_azazael:")
    print(
        "  alias persephone='python /mnt/c/Users/clint/Desktop/PERSEPHONE/bin/persephone_cli.py'"
    )


def main():
    """Main wizard."""
    print("=" * 50)
    print("PERSEPHONE Setup Wizard")
    print("=" * 50)

    print("\n[1/4] Checking prerequisites...")
    if not check_prerequisites():
        print("\nWarning: Some paths are missing. continuing anyway...")

        print("\n[2/4] Creating directories...")
        create_directories()

        print("\n[3/4] Setting up environment...")
        setup_environment()

        print("\n[4/4] Next steps...")
        setup_wsl_aliases()

        print("\n" + "=" * 50)
        print("Setup complete!")
        print("=" * 50)


if __name__ == "__main__":
    main()
