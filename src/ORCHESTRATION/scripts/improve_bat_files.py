# coding: utf-8
"""
Improve All BAT Files with Standardized Template
"""

from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import List, Dict  # removed — all names have native equivalents

IMPROVED_BAT_TEMPLATE = """@echo off
REM ============================================================================
REM {name}
REM Location: {path}
REM ============================================================================
REM Generated: 2026-02-10
REM ============================================================================

setlocal EnableDelayedExpansion

REM Configuration
set "BASE_DIR=%USERPROFILE%\\Desktop\\Azazael"
set "PYTHON_CMD=python"
set "LOG_DIR=%BASE_DIR%\\logs"

REM Create log directory if not exists
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ============================================================================
REM Main Execution
REM ============================================================================

echo [%TIME%] Starting {name}... >> "%LOG_DIR%\\bat_execution.log"

cd /d "%BASE_DIR%"

REM Validate environment
if not exist "%BASE_DIR%" (
    echo [%TIME%] ERROR: Base directory not found!
    echo [%TIME%] ERROR: Base directory not found! >> "%LOG_DIR%\\bat_execution.log"
    goto :ERROR
)

{commands}

echo [%TIME%] Completed successfully >> "%LOG_DIR%\\bat_execution.log"
exit /b 0

:ERROR
echo [%TIME%] Failed with exit code %ERRORLEVEL% >> "%LOG_DIR%\\bat_execution.log"
exit /b 1
"""


def improve_bat_file(bat_path: Path) -> dict:
    """Improve a single BAT file."""
    result = {"path": str(bat_path), "improved": False, "changes": []}

    try:
        content = bat_path.read_text(encoding="utf-8")

        # Extract name
        name = bat_path.stem

        # Extract Python commands
        python_cmds = []
        for line in content.split("\n"):
            stripped = line.strip()
            if "python" in stripped.lower() and not stripped.startswith("REM"):
                python_cmds.append(f"REM {stripped[:50]}")
                python_cmds.append(line)

        if not python_cmds:
            # Use default command
            python_cmds = [
                f"REM Execute {name}",
                f"%PYTHON_CMD% ml_platforms\\{name}.py %*",
            ]

        # Build improved content
        commands_block = "\n".join(python_cmds)
        improved = IMPROVED_BAT_TEMPLATE.format(
            name=name.replace("_", " ").title(),
            path=str(bat_path),
            commands=commands_block,
        )

        # Check if already has improvements
        if "setlocal" in content and "EnableDelayedExpansion" in content:
            result["changes"].append("Already improved - skipped")
            return result

        # Write improved version
        bat_path.write_text(improved, encoding="utf-8")
        result["improved"] = True
        result["changes"].append("Added setlocal and error handling")
        result["changes"].append("Added logging to logs directory")
        result["changes"].append("Added environment validation")
        result["changes"].append("Added error handling with :ERROR label")

    except Exception as e:
        note_failure("improve_bat_files:L99", e)
        result["error"] = str(e)

    return result


def improve_all_bat_files(shorty_dir: str) -> list[dict]:
    """Improve all BAT files in SHORTY directory."""
    results = []

    for bat_path in Path(shorty_dir).rglob("*.bat"):
        print(f"Improving: {bat_path.name}")
        result = improve_bat_file(bat_path)
        results.append(result)
        if result["improved"]:
            print("  [OK] Improved")
        else:
            print("  [--] Skipped")

    return results


def main():
    print("=" * 70)
    print("BAT FILE IMPROVER")
    print("=" * 70)

    shorty_dir = str(Path.home() / "Desktop" / "SHORTY")

    print(f"\nImproving BAT files in: {shorty_dir}\n")
    results = improve_all_bat_files(shorty_dir)

    improved = sum(1 for r in results if r.get("improved"))
    skipped = sum(1 for r in results if not r.get("improved"))

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"Improved: {improved}")
    print(f"Skipped: {skipped}")

    # Save report
    report = "# BAT Improvement Report\n\n"
    report += f"Improved: {improved}\n"
    report += f"Skipped: {skipped}\n\n"
    report += "## Files Improved\n\n"

    for r in results:
        if r.get("improved"):
            report += f"### {Path(r['path']).name}\n"
            for change in r.get("changes", []):
                    report += f"- {change}\n"
            report += "\n"

            Path("BAT_IMPROVEMENTS_REPORT.md").write_text(report, encoding="utf-8")
            print("\nReport: BAT_IMPROVEMENTS_REPORT.md")


if __name__ == "__main__":
    main()
