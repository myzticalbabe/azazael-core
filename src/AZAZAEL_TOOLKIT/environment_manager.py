# coding: utf-8
"""
Azazael Environment Manager v2.0
Correct persistence via project-local venv + shell integration
"""

import sys
import subprocess
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class EnvironmentManager:
    def __init__(self):
        self.project_root = Path(__file__).parent
        self.venv_path = self.project_root / ".venv"
        self.venv_python = self.venv_path / "Scripts" / "python.exe"

    def create_venv_if_missing(self):
        """Create venv if doesn't exist"""
        if not self.venv_python.exists():
            print("[1/4] Creating virtual environment...")
            subprocess.run(
                [sys.executable, "-m", "venv", str(self.venv_path)], check=True,
            timeout=1800)
            print(f"   [OK] Created: {self.venv_path}")
            return True
        print("[1/4] Virtual environment exists")
        return False

    def install_dependencies(self):
        """Install dependencies in venv"""
        print("\n[2/4] Installing dependencies...")
        pip = self.venv_python.parent / "pip.exe"

        # Core dependencies for Azazael
        deps = [
            "pyright",  # LSP
            "pylint",  # Linting
            "mypy",  # Type checking
        ]

        for dep in deps:
            result = subprocess.run(
                [str(pip), "install", "--quiet", dep], capture_output=True,
            timeout=1800)
            status = "[OK]" if result.returncode == 0 else "[--]"
            print(f"   {status} {dep}")

    def create_shell_integration(self):
        """Create shell alias for Azazael Python"""
        print("\n[3/4] Creating shell integration...")

        alias_script = self.project_root / "azazael_env.sh"

        content = f'''# Azazael Shell Integration - Source this file
export AZAZAEL_ROOT="{self.project_root}"
export AZAZAEL_PYTHON="{self.venv_python}"
export PATH="$AZAZAEL_PYTHON:$PATH"

# Convenience alias
azpython() {{
    "$AZAZAEL_PYTHON" "$@"
}}

# Quick activation
azactivate() {{
    source "$AZAZAEL_ROOT/azazael_env.sh"
}}
'''
        alias_script.write_text(content, encoding="utf-8")
        print(f"   [OK] Created: {alias_script.name}")

        # Add to bashrc if not present
        bashrc = Path.home() / ".bashrc"
        marker = "# Azazael Environment"

        if bashrc.exists() and marker not in bashrc.read_text(encoding="utf-8"):
            bashrc.write_text(
                bashrc.read_text(encoding="utf-8")
                + f"\n{marker}\nsource {alias_script}\n",
                encoding="utf-8",
            )
            print("   [OK] Added to ~/.bashrc")

    def create_vscode_config(self):
        """Create VS Code settings that persist"""
        print("\n[4/4] Creating VS Code configuration...")

        # .vscode/settings.json
        settings = self.project_root / ".vscode" / "settings.json"
        settings.parent.mkdir(exist_ok=True)

        settings.write_text("""{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe",
  "python.languageServer": "Pylance",
  "python.analysis.typeCheckingMode": "basic",
  "python.analysis.diagnosticMode": "workspace",
  "python.linting.enabled": true,
  "python.linting.pylintEnabled": true,
  "python.autoComplete.addBrackets": true,
  "files.associations": {"*.py": "python"},
  "python.envFile": "${workspaceFolder}/.env",
  "terminal.integrated.env.windows": {
    "PYTHONPATH": "${workspaceFolder}/UTILITIES:${workspaceFolder}/CORE:${workspaceFolder}/HYDRA/ml_platforms"
  }
}
""", encoding="utf-8")
        print("   [OK] Created: .vscode/settings.json")

        # .env file for environment
        env_file = self.project_root / ".env"
        env_file.write_text(f"""PYTHONPATH={self.project_root}/UTILITIES:{self.project_root}/CORE:{self.project_root}/HYDRA/ml_platforms
AZAZAEL_ROOT={self.project_root}
""", encoding="utf-8")
        print("   [OK] Created: .env")

    def apply(self):
        """Apply all persistence fixes"""
        print("=" * 60)
        print("AZAZAEL ENVIRONMENT MANAGER v2.0")
        print("=" * 60)

        self.create_venv_if_missing()
        self.install_dependencies()
        self.create_shell_integration()
        self.create_vscode_config()

        print("\n" + "=" * 60)
        print("COMPLETE")
        print("=" * 60)
        print("\nNEXT STEPS:")
        print("1. Restart VS Code")
        print("2. Source shell: source azazael_env.sh")
        print("3. Or add to ~/.bashrc manually")
        print("\nUSAGE:")
        print("  azpython your_script.py    # Run with Azazael Python")
        print("  source azazael_env.sh      # Activate environment")


if __name__ == "__main__":
    manager = EnvironmentManager()
    manager.apply()
