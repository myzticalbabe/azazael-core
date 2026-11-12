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
import re
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
        symbol = {"INFO": "[+]", "WARN": "[!]", "ERROR": "[-]", "OK": "[*]"}.get(level, "[?]")
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{symbol} {timestamp} {msg}\n"
        print(line, end="")
        with open(self.log_file, 'a', encoding="utf-8") as f:
            f.write(line)
    
    def detect_platform(self):
        """Detect WSL vs Windows execution"""
        self.log("Detecting platform...", "INFO")
        
        platform_info = {
            "is_wsl": False,
            "is_windows": False,
            "python_version": sys.version,
            "executable": sys.executable,
            "wsl_distro": None
        }
        
        # Check for WSL
        if os.path.exists("/proc/version"):
            with open("/proc/version", encoding="utf-8") as f:
                content = f.read()
                if "microsoft" in content.lower() or "wsl" in content.lower():
                    platform_info["is_wsl"] = True
                    platform_info["wsl_distro"] = content.strip()
        
        # Check for Windows Python
        if "python" in sys.executable.lower():
            if "WindowsApps" in sys.executable or "Programs" in sys.executable:
                platform_info["is_windows"] = True
        
        self.log(f"Platform: {'WSL' if platform_info['is_wsl'] else 'Windows'}", "OK")
        return platform_info
    
    def diagnose_lsp_errors(self):
        """Find and analyze LSP errors in Azazael Python files"""
        self.log("\n[1/6] Diagnosing LSP errors...", "INFO")
        
        errors = []
        py_files = list(self.azazael.rglob("*.py"))
        
        for py_file in py_files:
            if ".venv" in str(py_file) or "site-packages" in str(py_file):
                continue
            
            try:
                content = py_file.read_text(encoding="utf-8")
                
                # Check for common issues
                issues = []
                
                # BOM check
                if content.startswith("\ufeff"):
                    issues.append("BOM character present")
                
                # Missing imports
                for module in ["tkinter", "pygame", "torch", "tensorflow"]:
                    if f"import {module}" in content:
                        try:
                            __import__(module)
                        except ImportError as exc:
                            note_failure("COMPREHENSIVE_FIX:L85", exc)
                            issues.append(f"Missing: {module}")
                
                # Syntax patterns
                if re.search(r"print\s*\([^)]*%\s*\)", content):
                    issues.append("Old-style print formatting")
                
                # Path issues
                if re.search(r"C:\\Users.*?Desktop", content):  # scanner:ignore deliberate: this detector looks for those paths
                    issues.append("Hardcoded Windows paths")
                
                if issues:
                    errors.append({
                        "file": str(py_file.relative_to(self.azazael)),
                        "issues": issues
                    })
                    
            except Exception as e:
                note_failure("COMPREHENSIVE_FIX:L102", e)
                errors.append({
                    "file": str(py_file.relative_to(self.azazael)),
                    "error": str(e)
                })
        
        self.log(f"Found {len(errors)} files with issues", "OK")
        return errors
    
    def fix_python_files(self, errors):
        """Auto-fix common Python issues"""
        self.log("\n[2/6] Fixing Python files...", "INFO")
        
        fixed = 0
        for error in errors:
            if "error" in error:
                continue
                
            py_file = self.azazael / error["file"]
            
            try:
                content = py_file.read_text(encoding="utf-8")
                original = content
                
                # Remove BOM
                if content.startswith("\ufeff"):
                    content = content.encode("utf-8-sig").decode("utf-8")
                
                # Fix hardcoded paths (WSL-friendly)
                win_path = re.search(r'["\']C:\\Users[^"\']+["\']', content)  # scanner:ignore deliberate: this detector looks for those paths
                if win_path:
                    # Don't auto-fix paths - mark for manual review
                    self.log(f"  Hardcoded path in: {error['file']}", "WARN")
                
                if content != original:
                    py_file.write_text(content, encoding="utf-8")
                    fixed += 1
                    
            except Exception as e:
                self.log(f"  Error fixing {error['file']}: {e}", "ERROR")
        
        self.log(f"Fixed {fixed} files", "OK")
        return fixed
    
    def fix_desktop_shortcuts(self):
        """Fix broken desktop shortcuts"""
        self.log("\n[3/6] Fixing desktop shortcuts...", "INFO")
        
        shortcuts_fixed = []
        
        for lnk in self.desktop.glob("*.lnk"):
            try:
                # Simple .lnk repair - recreate if broken
                lnk.resolve() if lnk.is_symlink() else None
                
                # Check if it's a Python script disguised as shortcut
                if lnk.stat().st_size < 100:  # Suspiciously small
                    self.log(f"  Suspicious: {lnk.name}", "WARN")
                    shortcuts_fixed.append({
                        "file": str(lnk),
                        "status": "suspicious"
                    })
                    
            except Exception as e:
                note_failure("COMPREHENSIVE_FIX:L165", e)
                shortcuts_fixed.append({
                    "file": str(lnk),
                    "error": str(e)
                })
        
        # Check for actual Python scripts with .lnk extension
        for py_script in self.desktop.glob("*.py"):
            shortcuts_fixed.append({
                "file": str(py_script),
                "status": "python_script",
                "fix": "Rename .lnk to .py if needed"
            })
        
        self.log(f"Found {len(shortcuts_fixed)} shortcuts/scripts", "OK")
        return shortcuts_fixed
    
    def fix_gui_functionality(self):
        """Fix GUI issues (tkinter, pygame, etc.)"""
        self.log("\n[4/6] Fixing GUI functionality...", "INFO")
        
        gui_issues = []
        
        # Check GUI libraries
        for lib in ["tkinter", "PyQt5", "PyQt6", "pygame"]:
            try:
                __import__(lib)
                self.log(f"  [OK] {lib} available")
            except ImportError:
                gui_issues.append(lib)
                self.log(f"  [--] {lib} missing", "WARN")
        
        # Fix tkinter mainloop patterns
        gui_patterns = [
            (r"root\s*=\s*tk\(\)", "tk.Tk() needs root.mainloop()"),
            (r"root\.mainloop\(\)", "Ensure this is last statement"),
        ]
        
        for py_file in self.azazael.rglob("*.py"):
            if ".venv" in str(py_file):
                continue
                
            try:
                content = py_file.read_text(encoding="utf-8")
                
                # Check for common GUI mistakes
                if "import tkinter" in content or "from tkinter" in content:
                    if "mainloop" not in content:
                        gui_issues.append(f"{py_file.name}: missing mainloop()")
                        
            except Exception as exc:
                note_failure("COMPREHENSIVE_FIX:L215", exc)
        
        return gui_issues
    
    def create_cross_platform_wrapper(self):
        """Create wrapper that works in WSL and Windows"""
        self.log("\n[5/6] Creating cross-platform wrapper...", "INFO")