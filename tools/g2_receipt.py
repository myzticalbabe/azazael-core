#!/usr/bin/env python3
"""g2_receipt.py — subagent guard G2: a completion must carry a receipt.

A completion that cannot show (command, exit_code, stdout_sha256) is an
assertion, not evidence. This module provides the receipt format and a
validator that refuses a completion without one.

Usage:
    from g2_receipt import Receipt, validate_receipt
    r = Receipt(command="pytest tests/", exit_code=0, stdout="...all passed...")
    assert validate_receipt(r) is True

    # Or from a dict (e.g., parsed from a board row note):
    ok, reason = validate_receipt_dict({"command": "...", "exit_code": 0, "stdout": "..."})
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Tuple


@dataclass
class Receipt:
    """A receipt proving a task was completed with evidence."""
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    stdout_sha256: str = ""

    def __post_init__(self):
        if not self.stdout_sha256 and self.stdout:
            self.stdout_sha256 = hashlib.sha256(
                self.stdout.encode("utf-8", errors="replace")
            ).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Receipt":
        return cls(
            command=str(d.get("command", "")),
            exit_code=int(d.get("exit_code", -1)),
            stdout=str(d.get("stdout", "")),
            stderr=str(d.get("stderr", "")),
            stdout_sha256=str(d.get("stdout_sha256", "")),
        )

    @classmethod
    def from_text(cls, text: str) -> Optional["Receipt"]:
        """Parse a receipt from a board row note.

        Expected format (one per line):
            command: <command>
            exit_code: <int>
            stdout_sha256: <hex>
            stdout: <text>  (optional, may be multi-line after a marker)
        """
        if not text:
            return None
        cmd_match = re.search(r"command:\s*(.+)", text)
        code_match = re.search(r"exit_code:\s*(-?\d+)", text)
        sha_match = re.search(r"stdout_sha256:\s*([0-9a-f]{64})", text)
        if not cmd_match or not code_match:
            return None
        return cls(
            command=cmd_match.group(1).strip(),
            exit_code=int(code_match.group(1)),
            stdout="",  # stdout may be too large for a board note
            stdout_sha256=sha_match.group(1) if sha_match else "",
        )


def validate_receipt(r: Receipt) -> Tuple[bool, str]:
    """Validate a receipt. Returns (ok, reason).

    A valid receipt requires:
    - non-empty command
    - exit_code is an integer
    - stdout_sha256 is a 64-hex string (or stdout is provided and hash matches)
    """
    if not r.command or not r.command.strip():
        return False, "missing command"
    if not isinstance(r.exit_code, int):
        return False, "exit_code must be int"
    if r.stdout_sha256:
        if not re.fullmatch(r"[0-9a-f]{64}", r.stdout_sha256):
            return False, "stdout_sha256 must be 64 hex chars"
        if r.stdout:
            computed = hashlib.sha256(
                r.stdout.encode("utf-8", errors="replace")
            ).hexdigest()
            if computed != r.stdout_sha256:
                return False, "stdout_sha256 mismatch (stdout was tampered)"
    elif r.stdout:
        # stdout provided but no sha — compute and accept
        pass
    else:
        return False, "missing stdout or stdout_sha256"
    return True, "ok"


def validate_receipt_dict(d: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate a receipt from a dict (e.g., parsed from JSON)."""
    try:
        r = Receipt.from_dict(d)
    except (ValueError, TypeError) as exc:
        return False, f"invalid receipt: {exc}"
    return validate_receipt(r)


def validate_board_note(note: str) -> Tuple[bool, str]:
    """Validate that a board row note contains a valid receipt.

    The note must contain at least:
        command: <command>
        exit_code: <int>
    and either stdout or stdout_sha256.
    """
    r = Receipt.from_text(note)
    if r is None:
        return False, "no receipt found in note (missing command/exit_code)"
    return validate_receipt(r)


# --- Self-test ---------------------------------------------------------------

def _self_test() -> bool:
    ok = True

    # Positive: valid receipt with stdout
    r = Receipt(command="pytest tests/", exit_code=0, stdout="5 passed")
    assert validate_receipt(r)[0] is True, "valid receipt rejected"

    # Positive: valid receipt with sha only
    r2 = Receipt(command="echo hello", exit_code=0, stdout_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")
    assert validate_receipt(r2)[0] is True, "sha-only receipt rejected"

    # Negative: missing command
    r3 = Receipt(command="", exit_code=0, stdout="x")
    assert validate_receipt(r3)[0] is False, "empty command accepted"

    # Negative: missing evidence
    r4 = Receipt(command="echo x", exit_code=0)
    assert validate_receipt(r4)[0] is False, "no evidence accepted"

    # Negative: tampered stdout
    r5 = Receipt(command="echo x", exit_code=0, stdout="hello", stdout_sha256="0" * 64)
    assert validate_receipt(r5)[0] is False, "tampered stdout accepted"

    # Negative: board note without receipt
    ok_note, _ = validate_board_note("just some text")
    assert ok_note is False, "receipt-less note accepted"

    # Positive: board note with receipt
    note = "command: pytest tests/\nexit_code: 0\nstdout_sha256: " + hashlib.sha256(b"5 passed").hexdigest()
    ok_note2, _ = validate_board_note(note)
    assert ok_note2 is True, "valid board note rejected"

    # from_text round-trip
    r6 = Receipt.from_text(note)
    assert r6 is not None and r6.command == "pytest tests/"

    # validate_receipt_dict: the board-row path. A note is JSON on the wire and
    # a dict in memory, so the dict entry point is the one a caller actually
    # reaches -- an untested public validator is a validator nobody knows is
    # broken (measured 2026-11-02: it had zero call sites anywhere in the tree).
    ok_dict, why = validate_receipt_dict(
        {"command": "pytest tests/", "exit_code": 0, "stdout": "5 passed"})
    assert ok_dict is True, f"valid receipt dict rejected: {why}"

    bad_dict, why = validate_receipt_dict(
        {"command": "pytest tests/", "exit_code": 0})
    assert bad_dict is False, "dict without evidence accepted"

    # A dict that is not a receipt at all must be refused by construction, not
    # raise: from_dict coerces, so a missing command becomes "" and validate
    # names it.
    not_a_receipt, why = validate_receipt_dict({})
    assert not_a_receipt is False, f"empty dict accepted: {why}"

    print("G2 self-test: PASS (10/10 controls)")
    return True


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        sys.exit(0 if _self_test() else 1)
    print("Usage: python g2_receipt.py --self-test")
    print("Import as: from g2_receipt import Receipt, validate_receipt, validate_board_note")