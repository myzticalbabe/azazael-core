#!/usr/bin/env python3
"""
gate_write_file.py — Fully gated write_file pipeline

Wraps every write_file operation inside the PreActionGate three-phase
safety protocol, eliminating the catastrophic indentation-destruction
pattern documented in ERROR_LOG.md Error #214.

Pipeline:
1. PRE-VALIDATE: syntax check + 0-indent declaration warnings
2. SNAPSHOT: .pre_gate_backup before mutation (if file exists)
3. MUTATE: write the full file content to disk
4. POST-VALIDATE: py_compile syntax + merged-line + 0-indent runaway +
   auto-revert on failure
5. RECORD: log attempt + outcome to HeuristicErrorRecorder

This script is the PRIMARY mechanism for the Codebuff agent to perform
write_file operations. The platform write_file tool is bypassed in favor
of this gated pipeline.

Usage:
  # From a file:
  python azazael/src/SCRIPTS/gate_write_file.py <filepath> <content_file>

  # From stdin:
  cat content.txt | python azazael/src/SCRIPTS/gate_write_file.py <filepath> --stdin

  # Self-test:
  python azazael/src/SCRIPTS/gate_write_file.py --test

Exit codes: 0 = AUTHORIZED (write applied + validated), 1 = DENIED/REVERTED
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
    note_failure("gate_write_file:L53", _pag_e)
    _PAG_AVAILABLE = False
    _PAG_ERR = str(_pag_e)
    get_pre_action_gate = None

try:
    from CORE.compliance.heuristic_recorder import get_heuristic_recorder
    _HER_AVAILABLE = True
except ImportError as _her_e:
    note_failure("gate_write_file:L61", _her_e)
    _HER_AVAILABLE = False
    _HER_ERR = str(_her_e)
    get_heuristic_recorder = None


# ---------------------------------------------------------------------------
# GateWriteFile — main pipeline
# ---------------------------------------------------------------------------

class GateWriteFile:
    """Execute a write_file through the full three-phase PreActionGate.

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
        content: str,
        dry_run: bool = False,
        bypass_compliance: bool = False,
    ) -> int:
        """Execute the full gated write_file pipeline.

        Args:
            filepath: Target file path (relative to project root or absolute)
            content: Full content to write to the file
            dry_run: If True, run validation but don't actually write
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

            # --------------------------------------------------------------
            # Phase 1: PRE-VALIDATE
            # --------------------------------------------------------------
            ok, msg = self.gate.validate_write_file(target_str, content)

            if not ok:
                print(f"DENIED (Pre-Validate): {msg}")
                if self.recorder:
                    self.recorder.record_write_file_attempt(
                        target_str, content, "HALT"
                    )
                return 1

            print(f"[1/4] PRE-VALIDATE PASSED: {msg}")

            # Warn if overwriting an existing file
            if target_path.exists():
                existing_size = target_path.stat().st_size
                new_size = len(content.encode("utf-8"))
                print(
                    f"  NOTE: Overwriting existing file "
                    f"({existing_size} → {new_size} bytes)"
                )

            # Record the attempt
            attempt_id = ""
            if self.recorder:
                attempt_id = self.recorder.record_write_file_attempt(
                    target_str, content, "PROCEED"
                )

            # --------------------------------------------------------------
            # Phase 2: SNAPSHOT BACKUP
            # --------------------------------------------------------------
            backup_path = self.gate.take_snapshot(target_str)
            if backup_path:
                print(f"[2/4] BACKUP: {backup_path}")
            else:
                print("[2/4] BACKUP: New file — no backup needed")

            if dry_run:
                print("DRY-RUN: Skipping write. Gate passed.")
                return 0

            # --------------------------------------------------------------
            # Phase 3: WRITE
            # --------------------------------------------------------------
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(content, encoding="utf-8")
                print(f"[3/4] WRITE COMPLETE: {target_str}")
            except Exception as e:
                print(f"ERROR (Write): {e}", file=sys.stderr)
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

            print(f"\nAUTHORIZED — File written and validated: {target_str}")
            return 0
        finally:
            if saved_orchestrator is not None:
                self.gate.orchestrator = saved_orchestrator


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Fully gated write_file pipeline (PreActionGate v1.0)"
    )
    parser.add_argument(
        "filepath",
        nargs="?",
        help="Target file path (relative to project root or absolute)",
    )
    parser.add_argument(
        "content_file",
        nargs="?",
        help="File containing the content to write (or use --stdin)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read content from stdin instead of a file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate without writing the file",
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

    # Read content
    content = ""

    if args.stdin:
        content = sys.stdin.read()
    else:
        if not args.content_file:
            print(
                "ERROR: Must provide content_file or use --stdin",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            content = Path(args.content_file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"ERROR reading content file: {e}", file=sys.stderr)
            sys.exit(1)

    pipeline = GateWriteFile()
    exit_code = pipeline.run(
        args.filepath, content,
        dry_run=args.dry_run,
        bypass_compliance=args.bypass_compliance,
    )
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def self_test() -> int:
    """End-to-end test of the gated write_file pipeline."""

    print("=" * 60)
    print("  GATE WRITE FILE SELF-TEST")
    print("=" * 60)

    passed_all = True
    cleanup = []

    try:
        pipeline = GateWriteFile()
        if pipeline.gate is None:
            print("  SKIP: PreActionGate unavailable — self-test requires the gate")
            return 1

        # Use --bypass-compliance for self-test instead of fragile
        # state-machine transitions. The orchestrator is temporarily
        # disabled during pipeline.run() and restored afterward.
        print("  (Compliance: BYPASSED for self-test)")

        # ---------------------------------------------------------------
        # Test 1: Write new file passes
        # ---------------------------------------------------------------
        print("\n[1/4] Write new file...")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            tmp_path = f.name
        cleanup.append(Path(tmp_path))

        # First, unlink so it's a "new file" write
        Path(tmp_path).unlink()

        content = "x = 1\ny = 2\n"
        result = pipeline.run(tmp_path, content, bypass_compliance=True)
        if result != 0:
            print("  FAIL: New file write should pass")
            passed_all = False
        else:
            written = Path(tmp_path).read_text(encoding="utf-8")
            if written == content:
                print("  PASS: New file written and validated")
            else:
                print(f"  FAIL: Content mismatch: {written!r}")
                passed_all = False

        # ---------------------------------------------------------------
        # Test 2: Syntax error blocked at pre-validate
        # ---------------------------------------------------------------
        print("[2/4] Syntax error rejection...")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            bad_path = f.name
        cleanup.append(Path(bad_path))
        Path(bad_path).unlink()  # Start fresh

        bad_content = "x = \n    : bad syntax\n"
        result = pipeline.run(bad_path, bad_content, bypass_compliance=True)
        if result != 1:
            print("  FAIL: Should have blocked syntax error")
            passed_all = False
        else:
            # Verify file was NOT written (pre-validate rejected it)
            if not Path(bad_path).exists():
                print("  PASS: Syntax error correctly blocked at pre-validate")
            else:
                print("  FAIL: File was created despite syntax error")
                passed_all = False

        # ---------------------------------------------------------------
        # Test 3: Post-validate auto-revert after corruption
        # ---------------------------------------------------------------
        print("[3/4] Post-validate auto-revert...")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("x = 1\ny = 2\n")
            revert_path = f.name
        cleanup.append(Path(revert_path))

        original_content = Path(revert_path).read_text(encoding="utf-8")
        backup_path = pipeline.gate.take_snapshot(revert_path)

        # Corrupt: insert bad indentation
        Path(revert_path).write_text(
            original_content + "\n    : bad syntax\n",
            encoding="utf-8",
        )

        # Post-validate should detect and auto-revert
        ok, msg = pipeline.gate.validate_after_edit(
            str(revert_path), backup_path
        )
        if ok:
            print("  FAIL: Should have detected syntax error")
            passed_all = False
        else:
            restored = Path(revert_path).read_text(encoding="utf-8")
            if restored == original_content:
                print("  PASS: Auto-revert restored original content")
            else:
                print(
                    f"  FAIL: Content not restored correctly. "
                    f"Expected {len(original_content)} chars, "
                    f"got {len(restored)}"
                )
                passed_all = False

        # ---------------------------------------------------------------
        # Test 4: Dry-run mode
        # ---------------------------------------------------------------
        print("[4/4] Dry-run mode...")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            dry_path = f.name
        cleanup.append(Path(dry_path))
        Path(dry_path).unlink()  # Start fresh

        result = pipeline.run(dry_path, "z = 99\n", dry_run=True,
                              bypass_compliance=True)
        if result != 0:
            print("  FAIL: Dry-run should pass validation")
            passed_all = False
        elif not Path(dry_path).exists():
            print("  PASS: Dry-run validated but didn't write file")
        else:
            print("  FAIL: Dry-run wrote the file")
            passed_all = False

    finally:
        # Cleanup
        for p in cleanup:
            p.unlink(missing_ok=True)
            Path(str(p) + ".pre_gate_backup").unlink(missing_ok=True)

    print("\n" + "=" * 60)
    if passed_all:
        print("  ALL GATE WRITE FILE TESTS PASSED!")
    else:
        print("  GATE WRITE FILE TESTS FAILED!")
    print("=" * 60)
    return 0 if passed_all else 1


if __name__ == "__main__":
    main()
