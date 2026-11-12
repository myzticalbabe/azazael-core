#!/usr/bin/env python3
"""
enforce_pre_action_gate.py — Programmatic enforcement of the pre-action gate.

This script is the ONLY way file edits should be applied. Direct str_replace
or write_file tool calls bypass ALL heuristic and compliance checks.

Usage:
    # str_replace: read edit spec from stdin
    echo '{"filepath": "src/file.py", "old_string": "old text", "new_string": "new text"}' \\
        | python enforce_pre_action_gate.py

    # str_replace: read edit spec from file
    python enforce_pre_action_gate.py /tmp/edit_spec.json

    # write_file: read from stdin or file with "content" key
    echo '{"filepath": "src/new.py", "content": "print(1)\\n"}' \\
        | python enforce_pre_action_gate.py --write

    # write_file from file
    python enforce_pre_action_gate.py --write /tmp/edit_spec.json

Edit spec JSON format:
{
    "filepath": "relative/path/to/file.py",
    "old_string": "exact text to replace (for str_replace)",
    "new_string": "replacement text (for str_replace)",
    "content": "full file content (for write_file)"
}

The script runs the full PreActionGate pipeline:
  1. RULE 0X/0Y Compliance check
  2. Pre-edit validation (indentation, exact match, HEUR-04)
  3. Snapshot backup
  4. Apply the edit
  5. Post-edit validation (py_compile, HEUR-03 through HEUR-10)
  6. Auto-revert from backup on failure

Exit codes:
  0 — Edit applied and validated successfully
  1 — Pre-gate denied (compliance or validation failed)
  2 — Post-gate failed (edit was reverted from backup)
  3 — Invalid input (JSON parse error, missing fields)
"""

import json
import os
import sys
from pathlib import Path

# Ensure src/ is on sys.path
# Script is at azazael/src/SCRIPTS/enforce_pre_action_gate.py
# parent = SCRIPTS/, parent.parent = src/
_SRC_DIR = Path(__file__).resolve().parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from CORE.compliance.pre_action_gate import get_pre_action_gate
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _read_spec(args: list) -> dict:
    """Read edit spec from file arg or stdin."""
    if len(args) > 1 and not args[1].startswith("--"):
        # Read from file
        filepath = Path(args[1])
        if not filepath.exists():
            print(json.dumps({
                "success": False,
                "error": f"Spec file not found: {filepath}",
                "exit_code": 3,
            }))
            sys.exit(3)
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        # Read from stdin
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({
                "success": False,
                "error": "No input provided. Pipe or pass a JSON edit spec.",
                "exit_code": 3,
            }))
            sys.exit(3)
        return json.loads(raw)


def main():
    args = sys.argv
    is_write = "--write" in args

    # Read edit spec
    try:
        spec = _read_spec(args)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "success": False,
            "error": f"JSON parse error: {e}",
            "exit_code": 3,
        }))
        sys.exit(3)

    # Validate required fields
    filepath = spec.get("filepath", "")
    if not filepath:
        print(json.dumps({
            "success": False,
            "error": "Missing required field: 'filepath'",
            "exit_code": 3,
        }))
        sys.exit(3)

    # Ensure filepath is relative to project root or absolute
    # If relative, assume it's relative to azazael project root
    gate_path = filepath
    if not os.path.isabs(filepath):
        # Try to resolve relative to src/ parent (project root)
        project_root = _SRC_DIR.parent
        gate_path = str(project_root / filepath)

    gate = get_pre_action_gate()

    if is_write:
        # write_file mode
        content = spec.get("content", "")
        if not content:
            print(json.dumps({
                "success": False,
                "error": "Missing required field: 'content' (for --write mode)",
                "exit_code": 3,
            }))
            sys.exit(3)

        result = gate.apply_write_file(gate_path, content)
    else:
        # str_replace mode
        old_string = spec.get("old_string", "")
        new_string = spec.get("new_string", "")
        if not old_string or not new_string:
            print(json.dumps({
                "success": False,
                "error": "Missing required fields: 'old_string' and/or 'new_string'",
                "exit_code": 3,
            }))
            sys.exit(3)

        result = gate.apply_str_replace(gate_path, old_string, new_string)

    # Add exit_code based on result
    if result.get("success"):
        result["exit_code"] = 0
    elif result.get("reverted"):
        result["exit_code"] = 2
    else:
        result["exit_code"] = 1

    # Print result as JSON to stdout
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # Also print summary to stderr for readability
    if result.get("success"):
        backup = result.get("backup_path", "")
        backup_note = f" (backup: {backup})" if backup else ""
        print(f"✅ GATE PASSED — edit applied and validated{backup_note}", file=sys.stderr)
    elif result.get("reverted"):
        print(f"❌ GATE FAILED — edit REVERTED from backup: {result.get('error', '')}", file=sys.stderr)
    else:
        print(f"GATE DENIED - {result.get('error', '')}", file=sys.stderr)

    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
