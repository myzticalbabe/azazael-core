#!/usr/bin/env python3
"""
LSP & Environment Diagnosis for Azazael
"""
import subprocess
import sys
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

def run_cmd(args, timeout=30):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return result.stdout + result.stderr
    except Exception as e:
        note_failure("lsp_diagnosis:L13", e)
        return str(e)

print("=" * 60)
print("AZAZAEL LSP & PERMISSION DIAGNOSIS")
print("=" * 60)

# Python versions
print("\n[1] Python Installations:")
# Use subprocess with stderr capture to avoid shell=True
r = subprocess.run(["where", "python", "python3"], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
print(r.stdout + r.stderr)

# venv check
print("\n[2] Virtual Environment:")
venv_python = Path.home() / "Desktop" / "Azazael_UNIFIED" / ".venv" / "Scripts" / "python.exe"
print(f"Expected venv: {venv_python}")
print(f"Exists: {venv_python.exists()}")

# Installed LSP tools
print("\n[3] LSP Tools:")
for tool in ["pyright", "pylint", "mypy", "pylance"]:
    result = run_cmd([sys.executable, "-m", "pip", "show", tool])
    found = "INSTALLED" if "Name:" in result else "NOT FOUND"
    print(f"  {tool}: {found}")

# Check imports
print("\n[4] Common Import Failures:")
for module in ["tkinter", "pygame", "torch", "tensorflow"]:
    result = run_cmd([sys.executable, "-c", f"import {module}; print(f'{module}: OK')"])
    print(f"  {result.strip()}")

# Permissions
print("\n[5] Azazael Directory:")
azazael = Path.home() / "Desktop" / "Azazael_UNIFIED"
print(f"Path: {azazael}")
print(f"Exists: {azazael.exists()}")
if azazael.exists():
    files = list(azazael.glob("*.py"))[:5]
    print(f"Sample .py files: {[f.name for f in files]}")

# Log directory
print("\n[6] Logs Directory:")
log_dir = azazael / "UTILITIES" / "logs"
print(f"Log dir: {log_dir}")
print(f"Exists: {log_dir.exists()}")

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)
