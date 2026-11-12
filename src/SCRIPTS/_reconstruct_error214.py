#!/usr/bin/env python3
"""
_reconstruct_error214.py -- Replay Error #214 destructive patterns through the gate

For each of the 11 previously-damaged files, this script:
1. Reads the file to find structural elements (indented functions, imports, etc.)
2. Constructs representative destructive old_string/new_string pairs for 5 patterns
3. Runs gate_str_replace.py --dry-run against each
4. Verifies the gate DENIES (exit 1) every destructive edit

The 5 destructive patterns from Error #214:
  HEUR-01: 0-Indent Method Destruction (indented block -> 0-space)
  HEUR-02: Indent Mismatch (4-space -> 8-space or vice versa)
  HEUR-03: Syntax Corruption (break valid code -> invalid syntax)
  HEUR-04: Merged Lines (remove newline between independent statements)
  HEUR-05: Deleted Function (delete func + next def line; may produce
           valid Python -- a known gate limitation for semantic destruction)
"""

import sys
from pathlib import Path
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Dict, List, Optional, Tuple  # removed — all names have native equivalents

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent  # azazael/src/SCRIPTS/
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent  # azazael/
_SRC_DIR = _SCRIPT_DIR.parent  # azazael/src/
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

try:
    from CORE.compliance.pre_action_gate import PreActionGate
    _GATE_AVAILABLE = True
except ImportError as exc:
    note_failure("_reconstruct_error214:L36", exc)
    _GATE_AVAILABLE = False
    PreActionGate = None  # type: ignore

# 11 previously-damaged files (Error #214)
_TARGET_FILES = [
    "src/CORE/freethreading.py",
    "src/CORE/WORMS/CLUSTERWORMS.py",
    "src/CORE/postgres_memory.py",
    "src/METAWORMS_ENRICHED.py",
    "src/CORE/WORMS/expansion/learning.py",
    "src/CORE/FREETHREAD_UPGRADE.py",
    "src/CORE/cd2_verifier.py",
    "src/CORE/threading_utils.py",
    "src/CORE/WORMS/CLUSTERWORMS.py",
    "src/CORE/WORMS/METACLUSTER.py",
    "src/CORE/NeuralPipeline.py",
]


# Singleton gate instance — reused across all tests
_gate: Any = None  # PreActionGate or its None fallback; Any keeps both assignable


def _get_gate() -> Any:
    """Get or create the gate instance."""
    global _gate
    if _gate is None:
        if not _GATE_AVAILABLE:
            raise RuntimeError("PreActionGate not available")
        assert PreActionGate is not None  # narrows the import-fallback None
        _gate = PreActionGate()
    return _gate


def _gate_dryrun(filepath: str, old_str: str, new_str: str) -> dict:
    """Run PreActionGate.validate_str_replace() directly (no subprocess).

    Returns a dict mimicking subprocess.CompletedProcess with:
        caught (bool) — True if gate denied the edit
        exit_code (int) — 0 if passed, 1 if denied
        stdout_snippet (str)
    """
    gate = _get_gate()
    ok, msg = gate.validate_str_replace(filepath, old_str, new_str)
    return {
        "caught": not ok,
        "exit_code": 1 if not ok else 0,
        "stdout_snippet": msg[:120] if msg else "",
    }


def find_indented_function(lines: list[str], min_indent: int = 4) -> (tuple[int, str, str] | None):
    """Find the first indented def at >= min_indent, return (line_idx, old_str, new_str_deindented)."""
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("def ") and line.startswith(" " * min_indent):
            # Build old_string: the def line + following body
            body_lines = [line]
            base_indent = len(line) - len(stripped)
            for j in range(i + 1, min(i + 8, len(lines))):
                next_line = lines[j]
                if next_line.strip() == "":
                    body_lines.append(next_line)
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent > base_indent or next_line.strip().startswith("#"):
                    body_lines.append(next_line)
                else:
                    break
            if len(body_lines) >= 2:
                old_str = "".join(body_lines).rstrip("\n")
                # De-indented version (0-indent catastrophe)
                deindented_lines = []
                for bl in body_lines:
                    if bl.startswith(" " * base_indent):
                        deindented_lines.append(bl[base_indent:])
                    else:
                        deindented_lines.append(bl)
                new_str = "".join(deindented_lines).rstrip("\n")
                return i, old_str, new_str
    return None


def find_syntax_break_target(lines: list[str]) -> (tuple[str, str] | None):
    """Find a safe indented block and construct a syntax-breaking replacement."""
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("def ") and line.startswith("    "):
            # Old: def line + a valid body line
            for j in range(i + 1, min(i + 4, len(lines))):
                if lines[j].strip() and not lines[j].strip().startswith("#"):
                    old_str = line.rstrip("\n") + "\n" + lines[j].rstrip("\n")
                    # New: corrupt with bad syntax (unclosed paren, random token)
                    new_str = line.rstrip("\n") + "\n" + "    : bad syntax here = (\n"
                    return old_str, new_str
    return None


def find_merged_lines_target(lines: list[str]) -> (tuple[str, str] | None):
    """Find two adjacent indented lines and merge them by removing the newline."""
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        # Match any indentation level (not just 4-space)
        if line.startswith(" ") and stripped and not stripped.startswith("def ") and not stripped.startswith("class "):
            base_indent = len(line) - len(stripped)
            if i + 1 < len(lines) and lines[i + 1].startswith(" " * base_indent) and lines[i + 1].strip():
                old_str = line.rstrip("\n") + "\n" + lines[i + 1].rstrip("\n")
                new_str = line.rstrip("\n") + " " + lines[i + 1].strip()
                return old_str, new_str
    return None


def find_delete_target(lines: list[str]) -> (tuple[str, str] | None):
    """Find a function + the next function, and delete such that the next one
    becomes 0-indent (simulating the cascading destruction from Error #214).
    This tests the gate's 0-indent-runaway post-validate check."""
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("def ") and line.startswith("    "):
            base_indent = len(line) - len(stripped)
            # Collect this function's lines
            func_lines = [line]
            end_idx = i + 1
            for j in range(i + 1, min(i + 10, len(lines))):
                next_line = lines[j]
                if next_line.strip() == "":
                    func_lines.append(next_line)
                    end_idx = j + 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent > base_indent or next_line.strip().startswith("#"):
                    func_lines.append(next_line)
                    end_idx = j + 1
                else:
                    break
            # Find the NEXT function at same indent level
            for k in range(end_idx, min(end_idx + 5, len(lines))):
                next_stripped = lines[k].lstrip()
                if next_stripped.startswith("def ") and lines[k].startswith(" " * base_indent):
                    # old = this function block + next function's def line
                    old_lines = func_lines + [lines[k]]
                    old_str = "".join(old_lines).rstrip("\n")
                    # new = delete first function; next function's def is now at 0-indent
                    new_str = next_stripped.rstrip("\n")
                    if len(func_lines) >= 2:
                        return old_str, new_str
            break
    return None


def run_reconstruction() -> dict[str, list[dict]]:
    """Run all 5 destructive patterns against all 11 files. Returns per-file results."""
    results: dict[str, list[dict]] = {}

    for rel_path in _TARGET_FILES:
        full_path = _PROJECT_ROOT / rel_path
        if not full_path.exists():
            results[rel_path] = [{"pattern": "ALL", "caught": False, "note": "FILE MISSING"}]
            continue

        content = full_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        # Add newlines back for accurate matching
        lines = [l + "\n" for l in lines[:-1]] + ([lines[-1]] if lines else [])

        file_results = []

        # --- HEUR-01: 0-Indent Method Destruction ---
        target = find_indented_function(lines, min_indent=4)
        if target:
            _, old_s, new_s = target
            result = _gate_dryrun(str(full_path), old_s, new_s)
            file_results.append({
                "pattern": "HEUR-01 0-indent catastrophe",
                "caught": result["caught"],
                "exit_code": result["exit_code"],
                "stdout_snippet": result.get("stdout_snippet", ""),
            })
        else:
            file_results.append({
                "pattern": "HEUR-01 0-indent catastrophe",
                "caught": None, "note": "No suitable indented function found"
            })

        # --- HEUR-02: Indent Mismatch ---
        # Take an indented function and force a different indent
        target = find_indented_function(lines, min_indent=4)
        if target:
            _, old_s, new_s_orig = target
            # Instead of 0-indent, use 8-indent (wrong direction)
            lines_old = old_s.split("\n")
            over_indented = ["    " + l for l in lines_old]
            new_over = "\n".join(over_indented)
            result = _gate_dryrun(str(full_path), old_s, new_over)
            file_results.append({
                "pattern": "HEUR-02 indent mismatch (4->8)",
                "caught": result["caught"],
                "exit_code": result["exit_code"],
                "stdout_snippet": result.get("stdout_snippet", ""),
            })
        else:
            file_results.append({
                "pattern": "HEUR-02 indent mismatch",
                "caught": None, "note": "No suitable target"
            })

        # --- HEUR-03: Syntax Corruption ---
        target = find_syntax_break_target(lines)
        if target:
            old_s, new_s = target
            result = _gate_dryrun(str(full_path), old_s, new_s)
            file_results.append({
                "pattern": "HEUR-03 syntax corruption",
                "caught": result["caught"],
                "exit_code": result["exit_code"],
                "stdout_snippet": result.get("stdout_snippet", ""),
            })
        else:
            file_results.append({
                "pattern": "HEUR-03 syntax corruption",
                "caught": None, "note": "No suitable target"
            })

        # --- HEUR-04: Merged Lines ---
        target = find_merged_lines_target(lines)
        if target:
            old_s, new_s = target
            result = _gate_dryrun(str(full_path), old_s, new_s)
            file_results.append({
                "pattern": "HEUR-04 merged lines",
                "caught": result["caught"],
                "exit_code": result["exit_code"],
                "stdout_snippet": result.get("stdout_snippet", ""),
            })
        else:
            file_results.append({
                "pattern": "HEUR-04 merged lines",
                "caught": None, "note": "No suitable target"
            })

        # --- HEUR-05: Deleted Function ---
        target = find_delete_target(lines)
        if target:
            old_s, new_s = target
            result = _gate_dryrun(str(full_path), old_s, new_s)
            file_results.append({
                "pattern": "HEUR-05 deleted function",
                "caught": result["caught"],
                "exit_code": result["exit_code"],
                "stdout_snippet": result.get("stdout_snippet", ""),
            })
        else:
            file_results.append({
                "pattern": "HEUR-05 deleted function",
                "caught": None, "note": "No suitable target"
            })

        results[rel_path] = file_results

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 72)
    print("  ERROR #214 RECONSTRUCTION TEST")
    print("  Replaying 5 destructive patterns against 11 files")
    print("  via PreActionGate.validate_str_replace()")
    print("=" * 72)
    print("(NOTE: Plain function deletion producing valid Python is a")
    print(" known gate limitation. HEUR-05 tests cascading delete that")
    print(" leaves next method at 0-indent, which post-validate catches.)")
    print()

    if not _GATE_AVAILABLE:
        print("FATAL: PreActionGate not available — cannot proceed")
        sys.exit(1)

    results = run_reconstruction()

    total_tests = 0
    total_caught = 0
    total_missed = 0
    total_untestable = 0

    for filepath, file_results in results.items():
        short = filepath.replace("src/CORE/", "").replace("src/", "")
        if "WORMS/" in short:
            short = "WORMS/" + short.split("WORMS/")[-1]
        if "expansion/" in short:
            short = "expansion/" + short.split("expansion/")[-1]
        print(f"\n{'-' * 60}")
        print(f"  {short}")
        print(f"{'-' * 60}")
        for r in file_results:
            total_tests += 1
            if r["caught"] is None:
                status = "UNTESTABLE"
                total_untestable += 1
            elif r["caught"]:
                status = "CAUGHT"
                total_caught += 1
            else:
                status = "MISSED"
                total_missed += 1
            note = f" ({r.get('note', '')})" if r.get('note') else ""
            snippet = f" [{r.get('stdout_snippet', '')[:80]}]" if r.get('stdout_snippet') else ""
            print(f"  {status:16s} {r['pattern']}{note}{snippet}")

    # Summary
    print(f"\n{'=' * 72}")
    print("  RECONSTRUCTION SUMMARY")
    print(f"{'=' * 72}")
    print(f"  Files tested:  {len(_TARGET_FILES)}")
    print(f"  Total tests:   {total_tests}")
    print(f"  CAUGHT:      {total_caught}")
    print(f"  MISSED:      {total_missed}")
    print(f"  UNTESTABLE:  {total_untestable}")
    print(f"{'=' * 72}")

    if total_missed == 0 and total_caught > 0:
        print("\n  VERDICT: gate_str_replace.py WOULD HAVE CAUGHT")
        print("     every testable destructive edit from Error #214.")
        print("     The 11-file catastrophe is prevented going forward.")
        return 0
    elif total_caught == 0:
        print("\n  VERDICT: No destructive edits were caught — gate ineffective.")
        return 1
    else:
        print(f"\n  VERDICT: {total_caught} caught, {total_missed} missed — partial coverage.")
        return 1 if total_missed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
