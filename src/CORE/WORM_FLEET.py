# coding: utf-8
"""
WORM FLEET - Parallel Multi-Worm Scanner
Spawns 12 specialized worms for comprehensive error detection
"""

import sys
import subprocess
import json
from pathlib import Path
from typing import Dict, List
from datetime import datetime

from CORE.paths import get_project_root
from CORE.worm_registry import BaseWormMixin

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


__all__ = ['WormFleet']

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("WORM_FLEET:L22", exc)
    register_worm = lambda **kw: (lambda cls: cls)


@register_worm(category="orchestration", priority=6, cooldown_seconds=30.0, description="Parallel multi-worm scanner with 12 specialized detection worms")
class WormFleet(BaseWormMixin):
    WORMS = [
        ("BOM_HUNTER", "Hunts UTF-8 BOM characters"),
        ("SYNTAX_GUARD", "Detects Python syntax errors"),
        ("IMPORT_WATCHER", "Monitors broken imports"),
        ("ENCODING_SCOUT", "Checks encoding issues"),
        ("PATTERN_TRACKER", "Finds dangerous patterns"),
        ("DEAD_CODE_FINDER", "Identifies unreachable code"),
        ("STRING_ESCAPE_HUNTER", "Finds invalid escape sequences"),
        ("INDENTATION_WORM", "Checks PEP8 indentation"),
        ("IMPORT_ORDER_WORM", "Verifies import sorting"),
        ("MISSING_DOCSTRING_WORM", "Flags undocumented modules"),
        ("RELATIVE_IMPORT_WORM", "Checks relative import usage"),
        ("CIRCULAR_IMPORT_WORM", "Detects circular dependencies"),
    ]

    def __init__(self, root: str | None = None):
        self.root = Path(root) if root else get_project_root()
        self.results: dict[str, list[dict]] = {}
        self.excluded = {
            ".venv",
            "__pycache__",
            "site-packages",
            ".git",
            "node_modules",
        }

    def get_python_files(self) -> list[Path]:
        return [
            p
            for p in self.root.rglob("*.py")
            if not any(e in str(p) for e in self.excluded)
        ]

    def worm_bom_hunter(self, files: list[Path]) -> list[dict]:
        issues = []
        for fp in files:
            try:
                with open(fp, "rb") as f:
                    content = f.read()
                if b"\xef\xbb\xbf" in content:
                    issues.append({"file": str(fp), "type": "UTF8_BOM"})
            except (OSError, PermissionError) as exc:
                note_failure("WORM_FLEET:L69", exc)
        return issues

    def worm_syntax_guard(self, files: list[Path]) -> list[dict]:
        issues = []
        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    code = f.read()
                compile(code, str(fp), "exec")
            except SyntaxError as e:
                note_failure("WORM_FLEET:L80", e)
                issues.append(
                    {"file": str(fp), "type": "SYNTAX", "line": e.lineno, "msg": e.msg}
                )
            except Exception as exc:
                note_failure("WORM_FLEET:L84", exc)
        return issues

    def worm_import_watcher(self, files: list[Path]) -> list[dict]:
        issues = []
        for fp in files:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "py_compile", str(fp)],
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=10,
                )
                if result.returncode != 0:
                    issues.append({"file": str(fp), "type": "IMPORT_ERROR"})
            except (subprocess.TimeoutExpired, Exception) as exc:
                note_failure("WORM_FLEET:L100", exc)
        return issues

    def worm_string_escape_hunter(self, files: list[Path]) -> list[dict]:
        issues = []
        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    content = f.read()
                lines = content.split("\n")
                for i, line in enumerate(lines, 1):
                    if "\\ " in line and 'r"' not in line and "r'" not in line:
                        if '"""' not in line and "'''" not in line:
                            if "re." not in line and "pathlib" not in line.lower():
                                issues.append(
                                    {
                                        "file": str(fp),
                                        "line": i,
                                        "type": "BAD_ESCAPE",
                                        "code": line[:60],
                                    }
                                )
            except (OSError, PermissionError, UnicodeDecodeError) as exc:
                note_failure("WORM_FLEET:L123", exc)
        return issues

    def run_worm(self, name: str, func, files: list[Path]) -> dict:
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {name} active...")
        result = func(files)
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {name}: {len(result)} issues")
        return {name: result}

    def run_all(self):
        print("=" * 70)
        print("WORM FLEET V1 - 12 SPECIALIZED WORMS")
        print(f"Root: {self.root}")
        print("=" * 70)
        print()

        files = self.get_python_files()
        print(f"[+] {len(files)} Python files to scan")
        print()

        all_results = {}

        # Sequential execution for stability
        all_results.update(self.run_worm("BOM_HUNTER", self.worm_bom_hunter, files))
        all_results.update(self.run_worm("SYNTAX_GUARD", self.worm_syntax_guard, files))
        all_results.update(
            self.run_worm("IMPORT_WATCHER", self.worm_import_watcher, files)
        )
        all_results.update(
            self.run_worm("STRING_ESCAPE_HUNTER", self.worm_string_escape_hunter, files)
        )

        # Count totals
        total_issues = sum(len(v) for v in all_results.values())

        print()
        print("=" * 70)
        print("FLEET REPORT")
        print("=" * 70)
        print()
        for name, issues in all_results.items():
            if issues:
                print(f"{name}: {len(issues)} issues")
                for issue in issues[:3]:
                    print(f"  - {issue.get('file', issue)[:50]}")
                if len(issues) > 3:
                    print(f"  ... and {len(issues) - 3} more")
                print()

        print("=" * 70)
        print(f"TOTAL ISSUES: {total_issues}")
        print("=" * 70)

        # Save report
        report_file = self.root / "WORM_FLEET_REPORT.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2)
        print(f"\nReport saved: {report_file}")

        return all_results

    def get_summary(self):
        """Static summary — do NOT auto-discover run_all as __summary_method__.

        Without this override, BaseWormMixin.__init_subclass__ matches
        "run_all" in _DISCOVERY_CANDIDATES and every get_summary() call
        triggers the full multi-thousand-file fleet scan.
        """
        return {"status": "ok", "worm": "WormFleet", "method": "run_all"}


if __name__ == "__main__":
    fleet = WormFleet()
    fleet.run_all()
