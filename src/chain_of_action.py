#!/usr/bin/env python3
"""
AZAZAEL CHAIN-OF-ACTION AUTOMATION SCRIPT
Type 1-5 Workflow Implementation
"""

import os
import sys
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Configuration — uses environment variables with fallbacks

try:
    from CORE.paths import get_project_root_safe as _get_project_root_safe
except ImportError as exc:                               # never break the host
    note_failure("chain_of_action:core-paths-import", exc)
    _get_project_root_safe = None

#: Repo root for the env fallback below. MEASURED 2026-10-11: the fallback used to be
#: `Path.home() / "azazael"` -- a DIFFERENT, stale checkout from this repo -- so with
#: AZAZAEL_ROOT unset this script operated on the WRONG tree. `parents[1]` is this
#: file's own tree (chain_of_action.py sits at the src/ root) and answers only the case
#: where CORE.paths is unimportable.
_DEFAULT_ROOT = (str(_get_project_root_safe()) if _get_project_root_safe is not None
                 else str(Path(__file__).resolve().parents[1]))

AZAZAEL_ROOT = Path(os.environ.get("AZAZAEL_ROOT", _DEFAULT_ROOT))
BACKUP_ROOT = Path(os.environ.get("AZAZAEL_BACKUP", str(Path.home() / "azazael_backup")))
DOCS_DIR = BACKUP_ROOT / "docs"
WHEELS_DIR = AZAZAEL_ROOT / "AZAZAEL_BOOT" / "linux-wheels"

def update_log(message: str):
    """Append to update_log.txt"""
    log_file = DOCS_DIR / "update_log.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} - {message}\n")

def verification_log(message: str):
    """Append to verification_log.txt"""
    log_file = DOCS_DIR / "verification_log.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} - {message}\n")

def sync_backup():
    """Sync to backup location"""
    cmd = ["rsync", "-av", "--progress", str(AZAZAEL_ROOT) + "/", str(BACKUP_ROOT) + "/"]
    subprocess.run(cmd, check=True, timeout=1800)

# ================================================================================
# TYPE 1: NEW PACKAGE (.whl)
# ================================================================================
def add_package(whl_file: str):
    """Process new .whl package"""
    whl_path = Path(whl_file)
    package_name = whl_path.stem
    
    print(f"Processing package: {package_name}")
    
    # Step 1: COPY TO linux-wheels/
    dest = WHEELS_DIR / whl_path.name
    shutil.copy2(whl_path, dest)
    print("Copied to linux-wheels/")
    
    # Step 2: UPDATE INSTALL_GUIDE_LINUX.md (manual)
    print("Update INSTALL_GUIDE_LINUX.md manually")
    
    # Step 3: APPEND to python_modules.txt
    modules_file = DOCS_DIR / "python_modules.txt"
    with open(modules_file, "a", encoding="utf-8") as f:
        f.write(f"{package_name}\n")
    
    # Step 4: APPEND to python_modules.html (manual)
    print("Update python_modules.html manually")
    
    # Step 5: APPEND to azazael_environment.xml (manual)
    print("Update azazael_environment.xml manually")
    
    # Step 6: RUN verification test
    print("Run verification test manually")
    
    # Step 7: APPEND to verification_log.txt
    verification_log(f"Package added: {package_name}")
    
    # Step 8: AUTO-GENERATE CHAIN_OF_INFORMATION.txt (exists)
    # Step 9: AUTO-GENERATE INFORMATION_GENERATION.txt (manual)
    
    # Step 10: SYNC TO BACKUP
    sync_backup()
    
    print(f"Package workflow complete: {package_name}")

# ================================================================================
# TYPE 2: NEW FIX/WORKAROUND
# ================================================================================
def add_fix(description: str, bug_source: str = "", root_cause: str = "", solution: str = ""):
    """Record fix/workaround"""
    print(f"Recording fix: {description}")
    
    # Step 1: IDENTIFY (parameters provided)
    
    # Step 2: UPDATE INSTALL_GUIDE_LINUX.md (manual)
    print("Update INSTALL_GUIDE_LINUX.md manually")
    
    # Step 3: APPEND to update_log.txt
    msg = f"FIX: {description}"
    if bug_source:
        msg += f" | Bug: {bug_source}"
    if root_cause:
        msg += f" | Cause: {root_cause}"
    if solution:
        msg += f" | Solution: {solution}"
    update_log(msg)
    
    # Step 4: AUTO-GENERATE CHAIN_OF_INFORMATION.txt
    
    # Step 5: SYNC TO BACKUP
    sync_backup()
    
    print("Fix workflow complete")

# ================================================================================
# TYPE 3: NEW DOCUMENTATION
# ================================================================================
def add_doc(doc_file: str, doc_type: str = ""):
    """Process new documentation"""
    doc_path = Path(doc_file)
    
    print(f"Processing documentation: {doc_path.name}")
    
    # Step 1: COPY TO docs/
    dest = DOCS_DIR / doc_path.name
    shutil.copy2(doc_path, dest)
    
    # Step 2: DETERMINE TARGET AND APPEND
    update_log(f"New document added: {doc_path.name} (type: {doc_type})")
    
    # Step 3: AUTO-GENERATE INFORMATION_GENERATION.txt
    
    # Step 4: SYNC TO BACKUP
    sync_backup()
    
    print("Documentation workflow complete")

# ================================================================================
# TYPE 4: VERIFICATION/TEST RESULTS
# ================================================================================
def add_verification(test_name: str, result: str):
    """Record verification/test results"""
    print(f"Recording verification: {test_name} - {result}")
    
    # Step 1: RUN test (external)
    
    # Step 2: APPEND to verification_log.txt
    verification_log(f"TEST: {test_name} | RESULT: {result}")
    
    # Step 3: UPDATE python_modules.html (manual)
    print("Update python_modules.html manually")
    
    # Step 4: AUTO-GENERATE CHAIN_OF_INFORMATION.txt
    
    # Step 5: SYNC TO BACKUP
    sync_backup()
    
    print("Verification workflow complete")

# ================================================================================
# TYPE 5: BACKUP SYNC
# ================================================================================
def run_backup_sync():
    """Execute backup sync"""
    print("Starting backup sync...")
    
    # Step 1: RUN rsync
    sync_backup()
    
    # Step 2: VERIFY
    source_count = len(list(AZAZAEL_ROOT.rglob("*.*")))
    backup_count = len(list(BACKUP_ROOT.rglob("*.*")))
    
    # Step 3: APPEND to update_log.txt
    update_log(f"BACKUP SYNC | Source files: {source_count} | Backup files: {backup_count} | Date: {datetime.now()}")
    
    # Step 4: AUTO-GENERATE CHAIN_OF_INFORMATION.txt
    
    print("Backup sync complete")

# ================================================================================
# MAIN
# ================================================================================
def main():
    if len(sys.argv) < 2:
        print("Usage: chain_of_action.py <command> [arguments]")
        print("")
        print("Commands:")
        print("  package <whl_file>              - Add new .whl package")
        print("  fix <description> [source] [cause] [solution] - Record fix/workaround")
        print("  doc <file> [type]               - Add new documentation")
        print("  verify <test_name> <result>    - Record verification results")
        print("  backup                          - Run backup sync")
        sys.exit(1)
    
    command = sys.argv[1]
    
    try:
        if command == "package" and len(sys.argv) >= 3:
            add_package(sys.argv[2])
        elif command == "fix":
            args = sys.argv[2:] + [""] * (4 - len(sys.argv[2:]))
            add_fix(*args[:4])
        elif command == "doc" and len(sys.argv) >= 3:
            doc_type = sys.argv[3] if len(sys.argv) >= 4 else ""
            add_doc(sys.argv[2], doc_type)
        elif command == "verify" and len(sys.argv) >= 4:
            add_verification(sys.argv[2], sys.argv[3])
        elif command == "backup":
            run_backup_sync()
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
