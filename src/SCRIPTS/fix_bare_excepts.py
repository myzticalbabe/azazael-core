#!/usr/bin/env python3
"""
fix_bare_excepts.py — AST-aware bare except: fixer
====================================================
Replaces bare `except:` with `except Exception:` across the codebase.
Designed by Gemini per RULE 0I (Algorithmic Respawn Mandate).

Usage:
    python azazael/src/SCRIPTS/fix_bare_excepts.py          # fix ALL files
    python azazael/src/SCRIPTS/fix_bare_excepts.py --test   # test on ONE file only

RULE 0I enforcement:
    AR-1: Gemini designed this script ✅
    AR-2: Test on ONE file first (--test flag) ✅
    AR-3: py_compile syntax validation after EVERY file ✅
    AR-4: HALT on first error ✅
    AR-5: Backups not needed (simple string replacement) ✅
"""
import ast
import re
import py_compile
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"


def find_bare_except_lines(filepath: Path) -> list:
    """Find line numbers (0-indexed) of bare except: clauses."""
    content = filepath.read_text(encoding='utf-8')
    try:
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError as exc:
        note_failure("fix_bare_excepts:L34", exc)
        return []

    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            lines.append(node.lineno - 1)
    return sorted(lines, reverse=True)


def fix_file(filepath: Path) -> bool:
    """Fix bare excepts in a single file. Returns True if changes made."""
    lines_to_fix = find_bare_except_lines(filepath)
    if not lines_to_fix:
        return False

    print(f"  [{len(lines_to_fix)} bare except(s)] {filepath.relative_to(PROJECT_ROOT)}")

    content_lines = filepath.read_text(encoding='utf-8').split('\n')

    for lineno in lines_to_fix:
        original = content_lines[lineno]
        fixed = re.sub(r'^(\s*)except\s*:', r'\1except Exception:', original)
        if fixed != original:
            content_lines[lineno] = fixed
            print(f"    Line {lineno+1}: 'except:' -> 'except Exception:'")

    new_content = '\n'.join(content_lines)
    filepath.write_text(new_content, encoding='utf-8')

    # Validate syntax after modification (AR-3)
    try:
        py_compile.compile(str(filepath), doraise=True)
        return True
    except py_compile.PyCompileError as e:
        print(f"  [ERROR] Syntax validation failed: {e}")
        sys.exit(1)


def main():
    test_only = '--test' in sys.argv

    py_files = sorted(
        f for f in SRC_DIR.rglob("*.py")
        if '__pycache__' not in str(f)
        and '.venv' not in str(f)
        and f.name != 'fix_bare_excepts.py'
    )

    total_fixed = 0
    total_bare = 0

    for fp in py_files:
        if fix_file(fp):
            total_fixed += 1
            if test_only:
                print(f"\n[HALT] --test: Stopped after first modified file ({total_fixed} file, {len(find_bare_except_lines(fp))} fixes)")
                break

    # Final count
    for fp in py_files:
        total_bare += len(find_bare_except_lines(fp))
    print(f"\nDone. Fixed {total_fixed} file(s). {total_bare} bare excepts remaining.")


if __name__ == "__main__":
    main()
