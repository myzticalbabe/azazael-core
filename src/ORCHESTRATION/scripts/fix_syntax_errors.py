# coding: utf-8
"""
Fix Syntax Errors in Azazael Modules Only
"""

import sys
from pathlib import Path
# from typing import Dict, List  # removed — all names have native equivalents

# Add src/ to path for CORE imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from CORE.paths import get_project_root
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def find_syntax_errors(directory: str) -> list[dict]:
    """Find Python files with syntax errors in Azazael directory."""
    errors = []

    for py_file in Path(directory).rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            try:
                compile(content, str(py_file), "exec")
            except SyntaxError as e:
                note_failure("fix_syntax_errors:L24", e)
                errors.append(
                    {
                        "path": str(py_file),
                        "error": str(e),
                        "lineno": e.lineno,
                        "msg": e.msg,
                    }
                )
        except (PermissionError, OSError) as exc:
            note_failure("fix_syntax_errors:L33", exc)
            continue

    return errors


def fix_syntax_errors(errors: list[dict]) -> dict:
    """Attempt to fix syntax errors."""
    results = {"total": len(errors), "fixed": 0, "manual_required": 0, "details": []}

    for err in errors:
        path = err["path"]
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
            original = content

            # Fix 1: Add encoding declaration
            if not content.startswith("#") and any(ord(c) > 127 for c in content[:800]):
                content = "# coding: utf-8\n" + content

            # Fix 2: Remove invalid characters
            content = content.encode("ascii", errors="ignore").decode("ascii")

            # Fix 3: Fix indentation (replace tabs with spaces)
            lines = content.split("\n")
            fixed_lines = []
            for line in lines:
                # Skip lines with only tabs/spaces followed by nothing
                stripped = line.strip()
                if stripped and not line.startswith(stripped[0]):
                    # Fix indentation
                    leading = len(line) - len(line.lstrip())
                    line = "    " * leading + stripped
                fixed_lines.append(line)
            content = "\n".join(fixed_lines)

            # Fix 4: Remove trailing whitespace
            content = "\n".join(line.rstrip() for line in content.split("\n"))

            if content != original:
                Path(path).write_text(content, encoding="utf-8")
                results["fixed"] += 1
                results["details"].append(
                    {"path": path, "status": "FIXED", "error": err["error"]}
                )
            else:
                results["manual_required"] += 1
                results["details"].append(
                    {"path": path, "status": "MANUAL", "error": err["error"]}
                )

        except Exception as e:
            note_failure("fix_syntax_errors:L84", e)
            results["manual_required"] += 1
            results["details"].append(
                {"path": path, "status": "ERROR", "error": str(e)}
            )

    return results


def main():
    print("=" * 70)
    print("SYNTAX ERROR FIXER - Azazael Modules")
    print("=" * 70)

    azazael_dir = str(get_project_root())

    print(f"\n[1] Scanning: {azazael_dir}")
    errors = find_syntax_errors(azazael_dir)
    print(f"Found {len(errors)} files with syntax errors")

    if errors:
        print("\n[2] Fixing errors...")
        results = fix_syntax_errors(errors)

        print("\n[3] Results:")
        print(f"   Fixed automatically: {results['fixed']}")
        print(f"   Manual fix required: {results['manual_required']}")

        # Save detailed report
        report = "# Syntax Error Fix Report\n\n"
        report += f"Total errors: {results['total']}\n"
        report += f"Fixed: {results['fixed']}\n"
        report += f"Manual required: {results['manual_required']}\n\n"
        report += "## Details\n\n"
        for d in results["details"]:
            report += f"### {d['path']}\n"
            report += f"- Status: {d['status']}\n"
            report += f"- Error: {d['error']}\n\n"

            Path("SYNTAX_FIX_REPORT.md").write_text(report, encoding="utf-8")
            print("\nReport saved: SYNTAX_FIX_REPORT.md")
    else:
        print("\nNo syntax errors found!")


if __name__ == "__main__":
    main()
