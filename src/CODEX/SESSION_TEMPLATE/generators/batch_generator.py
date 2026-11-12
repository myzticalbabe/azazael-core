#!/usr/bin/env python3
"""
Batch Template Generator - Run templates individually, in multiples, or all sequentially
Usage:
  python batch_generator.py              # Generate all 9 styles
  python batch_generator.py ultimate     # Generate single style
  python batch_generator.py neon cosmic  # Generate multiple styles
  python batch_generator.py -l           # List available styles
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

STYLES = [
    "ultimate",
    "neon",
    "cosmic",
    "glass",
    "report",
    "mystical",
    "occult",
    "ethereal",
    "grimoire",
    "firehorse",
    "cain",
]

SCRIPT_DIR = Path(__file__).parent.parent
GENERATOR = SCRIPT_DIR / "generators" / "unified_generator.py"
DATA_FILE = SCRIPT_DIR / "data" / "session_data.json"


def list_styles():
    print("Available styles:")
    for i, style in enumerate(STYLES, 1):
        print(f"  {i}. {style}")


def generate_single(style, data_file=None):
    if data_file is None:
        data_file = DATA_FILE

    cmd = [sys.executable, str(GENERATOR), style, str(data_file)]
    print(f"Generating: {style}")
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if result.returncode == 0:
        print(f"  [OK] {style} generated successfully")
        return True
    else:
        print(f"  [FAIL] Error: {result.stderr}")
        return False


def generate_all(data_file=None):
    print(f"Generating all {len(STYLES)} styles...\n")
    success = 0
    failed = []

    for style in STYLES:
        if generate_single(style, data_file):
            success += 1
        else:
            failed.append(style)

    print(f"\n{'=' * 50}")
    print(f"Results: {success}/{len(STYLES)} styles generated successfully")

    if failed:
        print(f"Failed: {', '.join(failed)}")
    else:
        print("All styles generated successfully!")

    return success, failed


def main():
    if len(sys.argv) == 1:
        generate_all()
        return

        if sys.argv[1] == "-l" or sys.argv[1] == "--list":
            list_styles()
            return

            args = sys.argv[1:]

            if args[0] == "all":
                generate_all()
                return

                invalid = [a for a in args if a not in STYLES]
                if invalid:
                    print(f"Unknown styles: {', '.join(invalid)}")
                    print(f"Valid styles: {', '.join(STYLES)}")
                    return

                    print(f"Generating {len(args)} styles: {', '.join(args)}\n")
                    success = 0
                    for style in args:
                        if generate_single(style):
                            success += 1

                            print(f"\nResults: {success}/{len(args)} styles generated successfully")


if __name__ == "__main__":
    main()
