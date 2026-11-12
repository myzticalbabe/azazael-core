# coding: utf-8
"""
ERROR SCAN WORM v2 - Fast Error Detection for Azazael
Skips .venv and site-packages for speed
"""

import sys
from pathlib import Path
# from typing import List, Dict, Tuple  # removed — all names have native equivalents
import json
import subprocess
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("ERROR_SCAN_WORM:L16", exc)
    register_worm = lambda **kw: (lambda cls: cls)


@register_worm(category="diagnostic", priority=4, cooldown_seconds=45.0, description="Fast error scanner for BOM, syntax, and import issues")
class FastErrorScanner:
    def __init__(self, root_dir: str = "."):
        self.root = Path(root_dir)
        self.errors: list[dict] = []
        self.fixed = 0
        self.skipped_dirs = {".venv", "__pycache__", "site-packages", ".git"}

    def find_python_files(self) -> list[Path]:
        files = []
        for p in self.root.rglob("*.py"):
            skip = False
            for sd in self.skipped_dirs:
                if sd in str(p):
                    skip = True
                    break
            if not skip:
                files.append(p)
        print(
            f"[+] Found {len(files)} Python files (excluding .venv, __pycache__, site-packages)"
        )
        return files

    def check_bom(self, file_path: Path) -> bool:
        try:
            with open(file_path, "rb") as f:
                first_bytes = f.read(4)
            return (
                first_bytes.startswith(b"\xef\xbb\xbf")
                or first_bytes.startswith(b"\xff\xfe")
                or first_bytes.startswith(b"\xfe\xff")
            )
        except Exception as exc:
            note_failure("ERROR_SCAN_WORM:L52", exc)
            return False

    def remove_bom(self, file_path: Path) -> bool:
        try:
            with open(file_path, "rb") as f:
                content = f.read()
            content = content.replace(b"\xef\xbb\xbf", b"")
            with open(file_path, "wb") as f:
                f.write(content)
            return True
        except Exception as exc:
            note_failure("ERROR_SCAN_WORM:L63", exc)
            return False

    def check_syntax(self, file_path: Path) -> tuple[bool, str]:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
            compile(code, str(file_path), "exec")
            return True, ""
        except SyntaxError as e:
            note_failure("ERROR_SCAN_WORM:L72", e)
            return False, f"Line {e.lineno}: {e.msg}"
        except Exception as e:
            note_failure("ERROR_SCAN_WORM:L74", e)
            return False, str(e)

    def check_imports(self, file_path: Path) -> list[str]:
        missing = []
        try:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(file_path)],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=10,
            )
            if result.returncode != 0:
                for line in result.stderr.split("\n"):
                    if "ModuleNotFoundError" in line or "ImportError" in line:
                        missing.append(line.strip())
        except Exception as e:
            note_failure("ERROR_SCAN_WORM:L90", e)
            missing.append(str(e))
        return missing

    def scan_file(self, file_path: Path) -> dict:
        report = {"path": str(file_path), "errors": []}

        if self.check_bom(file_path):
            report["errors"].append({"type": "BOM", "severity": "HIGH"})
            if self.remove_bom(file_path):
                self.fixed += 1

        syntax_ok, syntax_msg = self.check_syntax(file_path)
        if not syntax_ok:
            report["errors"].append(
                {"type": "SYNTAX", "severity": "CRITICAL", "detail": syntax_msg}
            )

        missing_imports = self.check_imports(file_path)
        if missing_imports:
            report["errors"].append(
                {"type": "IMPORT", "severity": "MEDIUM", "detail": missing_imports}
            )

        return report

    def scan_all(self):
        print("=" * 70)
        print("ERROR SCAN WORM v2 - Fast Scan")
        print("=" * 70)
        print()

        files = self.find_python_files()

        total_errors = {"BOM": 0, "SYNTAX": 0, "IMPORT": 0}
        critical = []
        medium = []

        for i, fp in enumerate(files):
            if i % 100 == 0:
                print(f"  Scanning {i}/{len(files)}...")

            report = self.scan_file(fp)

            for err in report["errors"]:
                if err["type"] == "BOM":
                    total_errors["BOM"] += 1
                elif err["type"] == "SYNTAX":
                    total_errors["SYNTAX"] += 1
                    critical.append({**err, "file": report["path"]})
                elif err["type"] == "IMPORT":
                    total_errors["IMPORT"] += 1
                    medium.append({**err, "file": report["path"]})

        print()
        print("=" * 70)
        print("SCAN COMPLETE")
        print("=" * 70)
        print(f"Files scanned: {len(files)}")
        print(f"BOMs fixed: {self.fixed}")
        print()
        print("ERROR COUNTS:")
        print(f"  CRITICAL (Syntax): {total_errors['SYNTAX']}")
        print(f"  HIGH (BOM): {total_errors['BOM']}")
        print(f"  MEDIUM (Imports): {total_errors['IMPORT']}")
        print()

        if critical:
            print("=" * 70)
            print("CRITICAL SYNTAX ERRORS:")
            print("=" * 70)
            for err in critical[:15]:
                print(f"  {err['file']}")
                print(f"    {err['detail']}")
            if len(critical) > 15:
                print(f"  ... and {len(critical) - 15} more")
            print()

        if medium:
            print("=" * 70)
            print("IMPORT ERRORS (first 10):")
            print("=" * 70)
            shown = set()
            for err in medium[:10]:
                file = err["file"]
                if file not in shown:
                    shown.add(file)
                    print(f"  {file}")
                    for detail in err.get("detail", []):
                        print(f"    {detail[:80]}")
            print()

        return {"total_errors": total_errors, "critical": critical, "medium": medium}

    def save_report(self, report):
        with open(self.root / "ERROR_SCAN_REPORT.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"Report saved: {self.root / 'ERROR_SCAN_REPORT.json'}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Error Scan Worm")
    parser.add_argument("path", nargs="?", default=None, help="Path to scan")
    args = parser.parse_args()
    
    # Default to script location if no path provided
    if args.path:
        scan_path = args.path
    else:
        # Find Azazael root - go up from WORMS folder
        script_dir = Path(__file__).parent
        azazael_root = script_dir.parent.parent
        scan_path = str(azazael_root)
    
    scanner = FastErrorScanner(scan_path)
    report = scanner.scan_all()
    scanner.save_report(report)
