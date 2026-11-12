#!/usr/bin/env python3
"""g1_claim_trailer.py — subagent guard G1: a claim must carry its trailer.

A claim without a trailer (Task: Board-<n>) cannot be matched to the work
that closed it. This module validates that a claim carries its trailer and
refuses claims that don't.

Usage:
    from g1_claim_trailer import validate_trailer, extract_trailer

    ok, reason = validate_trailer("Fixed the bug. Task: Board-47065")
    # (True, "ok")

    trailer = extract_trailer("Fixed the bug. Task: Board-47065")
    # "Board-47065"
"""
from __future__ import annotations

import re
from typing import Optional, Tuple


# Trailer pattern: "Task: Board-<n>" or "Task: Board-<n>, Board-<m>" etc.
TRAILER_RE = re.compile(r"Task:\s*(Board-\d+(?:\s*,\s*Board-\d+)*)", re.I)

# Also accept bare "Board-<n>" references as trailers
BARE_BOARD_RE = re.compile(r"\bBoard-(\d+)\b")


def extract_trailer(text: str) -> Optional[str]:
    """Extract the trailer from a claim text.

    Returns the trailer string (e.g., "Board-47065") or None if not found.
    Prefers explicit "Task: Board-<n>" format, falls back to bare "Board-<n>".
    """
    if not text:
        return None
    m = TRAILER_RE.search(text)
    if m:
        return m.group(1).strip()
    m = BARE_BOARD_RE.search(text)
    if m:
        return "Board-%s" % m.group(1)
    return None


def extract_all_trailers(text: str) -> list[str]:
    """Extract all board references from a claim text."""
    if not text:
        return []
    matches = BARE_BOARD_RE.findall(text)
    return ["Board-%s" % m for m in matches]


def validate_trailer(text: str) -> Tuple[bool, str]:
    """Validate that a claim carries a trailer.

    Returns (ok, reason). A valid claim must contain at least one
    "Board-<n>" reference, either as "Task: Board-<n>" or bare.
    """
    if not text or not text.strip():
        return False, "empty claim"
    trailer = extract_trailer(text)
    if trailer is None:
        return False, "missing trailer (expected 'Task: Board-<n>')"
    return True, "ok"


def validate_trailer_strict(text: str) -> Tuple[bool, str]:
    """Strict validation: requires explicit 'Task: Board-<n>' format."""
    if not text or not text.strip():
        return False, "empty claim"
    m = TRAILER_RE.search(text)
    if not m:
        return False, "missing explicit trailer (expected 'Task: Board-<n>')"
    return True, "ok"


# --- Self-test ---------------------------------------------------------------

def _self_test() -> bool:
    # Positive: explicit trailer
    ok, _ = validate_trailer("Fixed the bug. Task: Board-47065")
    assert ok is True, "explicit trailer rejected"

    # Positive: bare board reference
    ok, _ = validate_trailer("Fixed the bug. Board-47065")
    assert ok is True, "bare trailer rejected"

    # Positive: multiple trailers
    trailers = extract_all_trailers("Task: Board-47065, Board-47066")
    assert trailers == ["Board-47065", "Board-47066"], f"got {trailers}"

    # Negative: no trailer
    ok, reason = validate_trailer("Fixed the bug without a trailer")
    assert ok is False, "trailer-less claim accepted"
    assert "missing" in reason

    # Negative: empty
    ok, _ = validate_trailer("")
    assert ok is False, "empty claim accepted"

    # Strict: requires explicit format
    ok, _ = validate_trailer_strict("Fixed the bug. Board-47065")
    assert ok is False, "bare reference accepted in strict mode"

    ok, _ = validate_trailer_strict("Fixed the bug. Task: Board-47065")
    assert ok is True, "explicit trailer rejected in strict mode"

    # extract_trailer
    assert extract_trailer("Task: Board-47065") == "Board-47065"
    assert extract_trailer("no trailer here") is None

    print("G1 self-test: PASS (7/7 controls)")
    return True


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        sys.exit(0 if _self_test() else 1)
    print("Usage: python g1_claim_trailer.py --self-test")
    print("Import as: from g1_claim_trailer import validate_trailer, extract_trailer")