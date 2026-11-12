#!/usr/bin/env python3
"""
gate_str_replace.py — Fully gated str_replace pipeline

Wraps every str_replace operation inside the PreActionGate three-phase
safety protocol, eliminating the catastrophic indentation-destruction
pattern documented in ERROR_LOG.md Error #214.

Pipeline:
1. PRE-VALIDATE: exact match + first-line indent + inner-line relative
   indent + 0-indent catastrophe detection
2. SNAPSHOT: .pre_gate_backup before mutation
3. MUTATE: perform the actual str_replace (single occurrence by default;
   use --all to replace all occurrences)
4. POST-VALIDATE: py_compile syntax + merged-line + 0-indent runaway +
   auto-revert on failure
5. RECORD: log attempt + outcome to HeuristicErrorRecorder

This script is the PRIMARY mechanism for the Codebuff agent to perform
str_replace operations. The platform str_replace tool is bypassed in favor
of this gated pipeline.

Usage:
  # From temp files:
  python azazael/src/SCRIPTS/gate_str_replace.py <filepath> <old_file> <new_file>

  # From stdin (---GATE-SPLIT--- delimiter):
  printf "    return 1\\n---GATE-SPLIT---\\n    return 2\\n" | \\
      python azazael/src/SCRIPTS/gate_str_replace.py <filepath> --stdin

  # Self-test:
  python azazael/src/SCRIPTS/gate_str_replace.py --test

Exit codes: 0 = AUTHORIZED (edit applied + validated), 1 = DENIED/REVERTED
"""

import argparse
import sys
import traceback
from pathlib import Path
from typing import List
import tempfile

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ---------------------------------------------------------------------------
# Path resolution — ensures CORE package is importable from any directory
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

_PROJECT_ROOT = _SRC_DIR.parent  # azazael/

try:
    from CORE.compliance.pre_action_gate import get_pre_action_gate
    _PAG_AVAILABLE = True
except ImportError as _pag_e:
    note_failure("gate_str_replace:L56", _pag_e)
    _PAG_AVAILABLE = False
    _PAG_ERR = str(_pag_e)
    get_pre_action_gate = None

try:
    from CORE.compliance.heuristic_recorder import get_heuristic_recorder
    _HER_AVAILABLE = True
except ImportError as _her_e:
    note_failure("gate_str_replace:L64", _her_e)
    _HER_AVAILABLE = False
    _HER_ERR = str(_her_e)
    get_heuristic_recorder = None


# ---------------------------------------------------------------------------
# GateStrReplace — main pipeline
# ---------------------------------------------------------------------------

class GateStrReplace:
    """Execute a str_replace through the full three-phase PreActionGate.

    This class encapsulates the entire pipeline so it can be called
    programmatically (e.g., from other scripts) or via the CLI.
    """

    def __init__(self):
        self.gate = (get_pre_action_gate()
                     if (_PAG_AVAILABLE and get_pre_action_gate is not None)
                     else None)
        self.recorder = (get_heuristic_recorder()
                         if (_HER_AVAILABLE and get_heuristic_recorder is not None)
                         else None)

    def resolve_path(self, filepath: str) -> Path:
        """Resolve a filepath relative to project root or as absolute."""
        p = Path(filepath)
        if p.is_absolute():
            return p
        return (_PROJECT_ROOT / p).resolve()

    def run(
        self,
        filepath: str,
        old_string: str,
        new_string: str,
        dry_run: bool = False,
        replace_all: bool = False,
        bypass_compliance: bool = False,
    ) -> int:
        """Execute the full gated str_replace pipeline.

        Args:
            filepath: Target file path (relative to project root or absolute)
            old_string: Exact string to replace
            new_string: Replacement string
            dry_run: If True, run validation but don't apply changes
            replace_all: If True, replace ALL occurrences (like the platform
                str_replace with allowMultiple: true). If False (default),
                replace only the first occurrence.
            bypass_compliance: If True, disable the orchestrator compliance
                check (RULE 0X/0Y). Use ONLY for self-tests and trusted
                interactive sessions. Mutations bypassing compliance are
                NOT permitted in automated pipelines.

        Returns:
            0 on success, 1 on failure (denied or reverted)
        """
        if not self.gate:
            print(
                f"ERROR: PreActionGate unavailable: {_PAG_ERR}",
                file=sys.stderr,
            )
            return 1

        # Save/restore orchestrator for --bypass-compliance flag.
        # When bypassed, orchestrator is temporarily disabled during the
        # pipeline and restored afterward. This is the ONLY supported way
        # to skip compliance checks — setting orchestrator=None directly
        # is reserved for initialization errors.
        saved_orchestrator = None
        if bypass_compliance and self.gate and self.gate.orchestrator:
            saved_orchestrator = self.gate.orchestrator
            self.gate.orchestrator = None

        try:
            target_path = self.resolve_path(filepath)
            target_str = str(target_path)

            if not target_path.exists():
                print(
                    f"ERROR: File not found: {target_str}",
                    file=sys.stderr,
                )
                return 1

            # --------------------------------------------------------------
            # Phase 1: PRE-VALIDATE
            # --------------------------------------------------------------
            ok, msg = self.gate.validate_str_replace(
                target_str, old_string, new_string
            )

            if not ok:
                print(f"DENIED (Pre-Validate): {msg}")
                if self.recorder:
                    self.recorder.record_attempt(
                        "str_replace", target_str, old_string, new_string, "HALT"
                    )
                return 1

            print(f"[1/4] PRE-VALIDATE PASSED: {msg}")

            # Check occurrence count — warn if multiple without --all
            try:
                content = target_path.read_text(encoding="utf-8")
                count = content.count(old_string)
                if count > 1 and not replace_all:
                    print(
                        f"  WARNING: old_string appears {count} times. "
                        f"Only the FIRST occurrence will be replaced. "
                        f"Use --all to replace ALL {count} occurrences."
                    )
            except Exception as exc:
                note_failure("gate_str_replace:L174", exc) # Count check is advisory; don't block on read error

            # Record the attempt
            attempt_id = ""
            if self.recorder:
                attempt_id = self.recorder.record_attempt(
                    "str_replace", target_str, old_string, new_string, "PROCEED"
                )

            # --------------------------------------------------------------
            # Phase 2: SNAPSHOT BACKUP
            # --------------------------------------------------------------
            backup_path = self.gate.take_snapshot(target_str)
            print(f"[2/4] BACKUP: {backup_path}")

            if dry_run:
                print("DRY-RUN: Skipping mutation. Gate passed.")
                return 0

            # --------------------------------------------------------------
            # Phase 3: MUTATE
            # --------------------------------------------------------------
            try:
                original = target_path.read_text(encoding="utf-8")
                if replace_all:
                    new_content = original.replace(old_string, new_string)
                    print(f"[3/4] MUTATION APPLIED (ALL occurrences): {target_str}")
                else:
                    new_content = original.replace(old_string, new_string, 1)
                    print(f"[3/4] MUTATION APPLIED: {target_str}")
                target_path.write_text(new_content, encoding="utf-8")
            except Exception as e:
                print(f"ERROR (Mutation): {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                if self.recorder and attempt_id:
                    self.recorder.record_outcome(
                        attempt_id, target_str, success=False, error_msg=str(e)
                    )
                return 1

            # --------------------------------------------------------------
            # Phase 4: POST-VALIDATE + AUTO-REVERT
            # --------------------------------------------------------------
            post_ok, post_msg = self.gate.validate_after_edit(
                target_str, backup_path
            )

            if not post_ok:
                print(f"[4/4] POST-VALIDATE FAILED (AUTO-REVERTED): {post_msg}")
                if self.recorder and attempt_id:
                    self.recorder.record_outcome(
                        attempt_id,
                        target_str,
                        success=False,
                        error_msg=post_msg,
                    )
                return 1

            print(f"[4/4] POST-VALIDATE PASSED: {post_msg}")

            # Success — record to HeuristicErrorRecorder
            if self.recorder and attempt_id:
                self.recorder.record_outcome(
                    attempt_id, target_str, success=True
                )

            print(f"\nAUTHORIZED — Edit applied and validated for: {target_str}")
            return 0
        finally:
            if saved_orchestrator is not None:
                self.gate.orchestrator = saved_orchestrator


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Fully gated str_replace pipeline (PreActionGate v1.0)"
    )
    parser.add_argument(
        "filepath",
        nargs="?",
        help="Target file path (relative to project root or absolute)",
    )
    parser.add_argument(
        "old_file",
        nargs="?",
        help="File containing the old string (or use --stdin)",
    )
    parser.add_argument(
        "new_file",
        nargs="?",
        help="File containing the new string (or use --stdin)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read old/new strings from stdin separated by "
             "'---GATE-SPLIT---' line",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate without applying changes",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="replace_all",
        help="Replace ALL occurrences of old_string (like platform "
             "str_replace with allowMultiple: true). WARNING: may "
             "cause unintended replacements if old_string is ambiguous.",
    )
    parser.add_argument(
        "--test", action="store_true", help="Run self-tests"
    )
    parser.add_argument(
        "--bypass-compliance",
        action="store_true",
        help="Disable orchestrator compliance check (RULE 0X/0Y) for "
             "self-tests and trusted interactive sessions. Mutations "
             "bypassing compliance are NOT permitted in automated pipelines.",
    )

    args = parser.parse_args()

    # Self-test mode
    if args.test:
        sys.exit(self_test())

    # Require filepath
    if not args.filepath:
        print("ERROR: filepath is required", file=sys.stderr)
        parser.print_usage()
        sys.exit(1)

    # Read strings
    old_string = ""
    new_string = ""

    if args.stdin:
        text = sys.stdin.read()
        parts = text.split("\n---GATE-SPLIT---\n", 1)
        if len(parts) != 2:
            print(
                "ERROR: --stdin requires old and new strings separated "
                "by a line containing only '---GATE-SPLIT---'",
                file=sys.stderr,
            )
            sys.exit(1)
        old_string = parts[0]
        new_string = parts[1].rstrip("\n")
    else:
        if not args.old_file or not args.new_file:
            print(
                "ERROR: Must provide old_file and new_file, or use --stdin",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            old_string = Path(args.old_file).read_text(encoding="utf-8")
            new_string = Path(args.new_file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"ERROR reading input files: {e}", file=sys.stderr)
            sys.exit(1)

    pipeline = GateStrReplace()
    exit_code = pipeline.run(
        args.filepath, old_string, new_string,
        dry_run=args.dry_run,
        replace_all=args.replace_all,
        bypass_compliance=args.bypass_compliance,
    )
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def self_test() -> int:
    """End-to-end test of the gated str_replace pipeline."""

    print("=" * 60)
    print("  GATE STR_REPLACE SELF-TEST")
    print("=" * 60)

    passed_all = True
    cleanup = []

    try:
        # Create a test Python file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("class Foo:\n    def bar(self):\n        return 1\n")
            temp_path = f.name
        cleanup.append(Path(temp_path))

        pipeline = GateStrReplace()
        if pipeline.gate is None:
            print("  SKIP: PreActionGate unavailable — self-test requires the gate")
            return 1

        # Use --bypass-compliance for self-test instead of fragile
        # state-machine transitions. The orchestrator is temporarily
        # disabled during pipeline.run() and restored afterward.
        print("  (Compliance: BYPASSED for self-test)")

        # ---------------------------------------------------------------
        # Test 1: Valid replacement passes
        # ---------------------------------------------------------------
        print("\n[1/4] Valid replacement...")
        result = pipeline.run(temp_path, "        return 1", "        return 2",
                              bypass_compliance=True)
        if result != 0:
            print("  FAIL: Valid replacement should pass")
            passed_all = False
        else:
            content = Path(temp_path).read_text(encoding="utf-8")
            if "        return 2" in content:
                print("  PASS: Valid replacement applied and validated")
            else:
                print("  FAIL: Replacement not applied")
                passed_all = False

        # ---------------------------------------------------------------
        # Test 2: 0-indent catastrophe DENIED + auto-reverted
        # ---------------------------------------------------------------
        print("[2/4] 0-indent catastrophe rejection...")
        # File currently has "        return 2" — try to replace with 0-indent
        result = pipeline.run(
            temp_path,
            "    def bar(self):\n        return 2",
            "def bar(self):\n    return 2",
            bypass_compliance=True,
        )
        if result != 1:
            print("  FAIL: Should have DENIED 0-indent replacement")
            passed_all = False
        else:
            # Verify file was NOT changed (pre-validate rejected it)
            content = Path(temp_path).read_text(encoding="utf-8")
            if "    def bar(self):" in content:
                print("  PASS: 0-indent replacement correctly denied")
            else:
                print("  FAIL: File was modified despite denial")
                passed_all = False

        # ---------------------------------------------------------------
        # Test 3: Dry-run mode
        # ---------------------------------------------------------------
        print("[3/4] Dry-run mode...")
        result = pipeline.run(
            temp_path, "        return 2", "        return 3", dry_run=True,
            bypass_compliance=True,
        )
        if result != 0:
            print("  FAIL: Dry-run should pass validation")
            passed_all = False
        else:
            content = Path(temp_path).read_text(encoding="utf-8")
            if "        return 3" not in content:
                print("  PASS: Dry-run validated but didn't modify file")
            else:
                print("  FAIL: Dry-run modified the file")
                passed_all = False

        # ---------------------------------------------------------------
        # Test 4: Post-validate auto-revert after corruption
        # ---------------------------------------------------------------
        print("[4/4] Post-validate auto-revert...")
        # Manually corrupt the file to simulate a bad edit
        original_content = Path(temp_path).read_text(encoding="utf-8")
        backup_path = pipeline.gate.take_snapshot(temp_path)

        # Corrupt: insert bad indentation
        Path(temp_path).write_text(
            original_content + "\n    : bad syntax\n",
            encoding="utf-8",
        )

        # Post-validate should detect and auto-revert
        # We call the gate directly since the full pipeline would
        # catch this at pre-validate if we used run()
        ok, msg = pipeline.gate.validate_after_edit(
            str(temp_path), backup_path
        )
        if ok:
            print("  FAIL: Should have detected syntax error")
            passed_all = False
        else:
            restored = Path(temp_path).read_text(encoding="utf-8")
            if restored == original_content:
                print("  PASS: Auto-revert restored original content")
            else:
                print(
                    f"  FAIL: Content not restored correctly. "
                    f"Expected {len(original_content)} chars, "
                    f"got {len(restored)}"
                )
                passed_all = False

    finally:
        # Cleanup
        for p in cleanup:
            p.unlink(missing_ok=True)
            Path(str(p) + ".pre_gate_backup").unlink(missing_ok=True)

    print("\n" + "=" * 60)
    if passed_all:
        print("  ALL GATE STR_REPLACE TESTS PASSED!")
    else:
        print("  GATE STR_REPLACE TESTS FAILED!")
    print("=" * 60)
    return 0 if passed_all else 1


if __name__ == "__main__":
    main()
