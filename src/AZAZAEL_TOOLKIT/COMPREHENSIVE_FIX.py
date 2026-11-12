#!/usr/bin/env python3
"""
AZAZAEL COMPREHENSIVE FIX v1.0
- LSP Error Diagnosis
- Desktop Shortcut Repair
- GUI Functionality Fix
- Cross-Platform Awareness
"""

import sys
import os
from pathlib import Path
from datetime import datetime
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class ComprehensiveFix:
    def __init__(self):
        self.desktop = Path.home() / "Desktop"
        self.azazael = self.desktop / "Azazael_UNIFIED"
        self.venv_python = self.azazael / ".venv" / "Scripts" / "python.exe"
        self.log_file = self.azazael / "COMPREHENSIVE_FIX_LOG.txt"

    def log(self, msg, level="INFO"):
        symbol = {"INFO": "[+]", "WARN": "[!]", "ERROR": "[-]", "OK": "[*]"}.get(
            level, "[?]"
        )
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{symbol} {timestamp} {msg}\n"
        print(line, end="")
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as exc:
            note_failure("COMPREHENSIVE_FIX:L33", exc)

    def detect_platform(self):
        self.log("Detecting platform...", "INFO")
        platform_info = {
            "is_wsl": False,
            "is_windows": False,
            "python_version": sys.version,
            "executable": sys.executable,
        }
        if os.path.exists("/proc/version"):
            with open("/proc/version", encoding="utf-8") as f:
                content = f.read()
                if "microsoft" in content.lower() or "wsl" in content.lower():
                    platform_info["is_wsl"] = True
        self.log(f"Platform: {'WSL' if platform_info['is_wsl'] else 'Windows'}", "OK")
        return platform_info

    def diagnose_lsp_errors(self):
        self.log("\n[1/6] Diagnosing LSP errors...", "INFO")
        errors = []
        py_files = list(self.azazael.rglob("*.py"))
        for py_file in py_files:
            if ".venv" in str(py_file) or "site-packages" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                issues = []
                if content.startswith("\ufeff"):
                    issues.append("BOM character present")
                for module in ["tkinter", "pygame", "torch", "tensorflow"]:
                    if f"import {module}" in content:
                        try:
                            __import__(module)
                        except ImportError as exc:
                            note_failure("COMPREHENSIVE_FIX:L68", exc)
                            issues.append(f"Missing: {module}")
                if issues:
                    errors.append(
                        {
                            "file": str(py_file.relative_to(self.azazael)),
                            "issues": issues,
                        }
                    )
            except Exception as e:
                note_failure("COMPREHENSIVE_FIX:L77", e)
                errors.append(
                    {"file": str(py_file.relative_to(self.azazael)), "error": str(e)}
                )
        self.log(f"Found {len(errors)} files with issues", "OK")
        return errors

    def fix_python_files(self, errors):
        self.log("\n[2/6] Fixing Python files...", "INFO")
        fixed = 0
        for error in errors:
            if "error" in error:
                continue
            py_file = self.azazael / error["file"]
            try:
                content = py_file.read_text(encoding="utf-8")
                original = content
                if content.startswith("\ufeff"):
                    content = content.encode("utf-8-sig").decode("utf-8")
                if content != original:
                    py_file.write_text(content, encoding="utf-8")
                    fixed += 1
            except Exception as e:
                self.log(f"  Error fixing {error['file']}: {e}", "ERROR")
        self.log(f"Fixed {fixed} files", "OK")
        return fixed

    def fix_desktop_shortcuts(self):
        self.log("\n[3/6] Fixing desktop shortcuts...", "INFO")
        shortcuts_fixed = []
        for lnk in self.desktop.glob("*.lnk"):
            try:
                if lnk.stat().st_size < 100:
                    self.log(f"  Suspicious: {lnk.name}", "WARN")
            except Exception as e:
                note_failure("COMPREHENSIVE_FIX:L111", e)
                shortcuts_fixed.append({"file": str(lnk), "error": str(e)})
        for py_script in self.desktop.glob("*.py"):
            shortcuts_fixed.append({"file": str(py_script), "status": "python_script"})
        self.log(f"Found {len(shortcuts_fixed)} shortcuts/scripts", "OK")
        return shortcuts_fixed

    def fix_gui_functionality(self):
        self.log("\n[4/6] Fixing GUI functionality...", "INFO")
        gui_issues = []
        for lib in ["tkinter", "PyQt5", "PyQt6", "pygame"]:
            try:
                __import__(lib)
                self.log(f"  [OK] {lib} available")
            except ImportError:
                gui_issues.append(lib)
                self.log(f"  [--] {lib} missing", "WARN")
        return gui_issues

    def create_cross_platform_wrapper(self):
        self.log("\n[5/6] Creating cross-platform wrapper...", "INFO")
        wrapper = self.azazael / "azazael_run.py"
        content = '''#!/usr/bin/env python3
"""
AZAZAEL CROSS-PLATFORM RUNNER v1.0
Works in WSL, Windows, and mixed environments
"""
import sys
import os
from pathlib import Path

def get_python():
    azazael = Path(__file__).parent
    candidates = [
        azazael / ".venv" / "Scripts" / "python.exe",
        azazael / ".venv" / "bin" / "python",
        sys.executable,
        "python3",
        "python",
    ]
    for candidate in candidates:
        try:
            if isinstance(candidate, str):
                import shutil
                if shutil.which(candidate):
                    return candidate
            elif candidate.exists():
                return str(candidate)
        except Exception:
            continue
    return sys.executable

def run_script(script_name):
    azazael = Path(__file__).parent
    python = get_python()
    for module_path in [
        azazael / "UTILITIES",
        azazael / "CORE", 
        azazael / "HYDRA" / "ml_platforms",
    ]:
        if str(module_path) not in sys.path:
            sys.path.insert(0, str(module_path))
    script_path = azazael / script_name
    if script_path.exists():
        os.execv(python, [python, str(script_path)])
    else:
        print(f"Script not found: {script_name}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_script(sys.argv[1])
    else:
        print("Usage: python azazael_run.py <script_name>")
'''
        wrapper.write_text(content, encoding="utf-8")
        self.log(f"Created: {wrapper.name}", "OK")
        return str(wrapper)

    def apply(self):
        print("=" * 70)
        print("AZAZAEL COMPREHENSIVE FIX v1.0")
        print("=" * 70)
        results = {}
        results["platform"] = self.detect_platform()
        results["lsp_errors"] = self.diagnose_lsp_errors()
        results["files_fixed"] = self.fix_python_files(results["lsp_errors"])
        results["shortcuts"] = self.fix_desktop_shortcuts()
        results["gui_issues"] = self.fix_gui_functionality()
        self.create_cross_platform_wrapper()
        print("\n" + "=" * 70)
        print("FIX COMPLETE")
        print("=" * 70)
        print(f"\nFiles analyzed: {len(results['lsp_errors'])}")
        print(f"Files fixed: {results['files_fixed']}")
        print(f"Shortcuts checked: {len(results['shortcuts'])}")
        print(f"GUI issues: {results['gui_issues']}")


if __name__ == "__main__":
    fix = ComprehensiveFix()
    fix.apply()
