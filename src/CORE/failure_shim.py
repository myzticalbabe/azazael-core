#!/usr/bin/env python3
# coding: utf-8
"""
failure_shim.py — Enhanced note_failure with structured metadata.

Backward-compatible shim that adds typed overloads and structured failure
recording while preserving the exact behavior of the original failure_ledger.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, overload

# Import the canonical ledger implementation
from CORE.failure_ledger import (
    note_failure as _ledger_note_failure,
    failure_notes,
    reset_notes,
    _FAILURE_NOTES,
    _FAILURE_LOCK,
    _LOST_NOTES,
)


#: Reasons the optional enhanced store refused a write, newest last, bounded to
#: 50. Empty is the healthy state and is also what a silently-swallowing
#: recorder reports, which is why the swallow above now records instead.
_ENHANCED_STORE_ERRORS: List[str] = []


def enhanced_store_errors() -> List[str]:
    """Return the record of enhanced-store write failures (copy, newest last)."""
    return list(_ENHANCED_STORE_ERRORS)


@dataclass
class FailureRecord:
    """Structured failure record for enhanced note_failure calls.
    
    Use with note_failure_v2() or pass as first positional argument to note_failure().
    """
    where: str
    exc: Optional[BaseException] = None
    context: Dict[str, Any] = field(default_factory=dict)
    severity: str = "medium"  # low, medium, high, critical
    tags: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_ledger_key(self) -> str:
        """Convert to legacy ledger key format for backward compatibility."""
        exc_type = type(self.exc).__name__ if self.exc is not None else "unknown"
        base = f"{self.where}:{exc_type}"
        if self.severity != "medium":
            base += f":{self.severity}"
        if self.tags:
            base += f":{','.join(self.tags)}"
        return base


# --- Enhanced note_failure with keyword-only structured metadata ---

@overload
def note_failure(where: str, exc: Optional[BaseException] = None) -> None:
    """Legacy signature: note_failure(where, exc=None). Never raises."""
    ...


@overload
def note_failure(where: str, exc: Optional[BaseException] = None,
                 *, context: Optional[Dict[str, Any]] = None,
                 severity: str = "medium",
                 tags: Optional[List[str]] = None) -> None:
    """Enhanced signature with structured metadata (keyword-only).
    
    Args:
        where: Location identifier (e.g., "module:function" or "file:line")
        exc: Exception that was caught and swallowed, or None
        context: Optional dict of contextual data for debugging
        severity: One of "low", "medium", "high", "critical"
        tags: Optional list of categorization tags
    """
    ...


@overload
def note_failure(where: FailureRecord) -> None:
    """Structured record signature: note_failure(FailureRecord(...)).

    The parameter is named ``where``, not ``record``: an overload's parameter
    names have to match the implementation's, and the implementation cannot be
    called ``record`` for the two string overloads. Callers pass the record
    positionally, so the name is not part of any call site.
    """
    ...


def note_failure(where: Union[str, FailureRecord],
                 exc: Optional[BaseException] = None,
                 *, context: Optional[Dict[str, Any]] = None,
                 severity: str = "medium",
                 tags: Optional[List[str]] = None) -> None:
    """Enhanced note_failure with structured metadata.

    The first parameter is ``Union[str, FailureRecord]``, not ``str``: the third
    overload accepts a record, and an overload whose parameter set is wider than
    its implementation is inconsistent by definition -- which is what the type
    gate was reporting. The body already branches on the type.
    
    Backward compatible: note_failure(where, exc) works exactly as before.
    New callers can pass context, severity, tags as keyword-only args.
    
    All signatures delegate to the canonical failure_ledger implementation
    for the actual recording, ensuring identical thread-safety and
    fail-open behavior.
    """
    # Handle FailureRecord as first positional argument
    if isinstance(where, FailureRecord):
        record = where
        _record_to_ledger(record)
        return
    
    # Build legacy key for backward compatibility
    exc_type = type(exc).__name__ if exc is not None else "unknown"
    key = f"{where}:{exc_type}"
    
    if severity != "medium":
        key += f":{severity}"
    if tags:
        key += f":{','.join(tags)}"
    
    # Delegate to canonical ledger (never raises, thread-safe)
    try:
        with _FAILURE_LOCK:
            _FAILURE_NOTES[key] = _FAILURE_NOTES.get(key, 0) + 1
    except Exception:
        _LOST_NOTES[0] += 1
    
    # Store enhanced metadata separately if provided
    if context or severity != "medium" or tags:
        _store_enhanced_failure(where, exc, context, severity, tags)


def note_failure_v2(record: FailureRecord) -> None:
    """Explicit v2 API for structured failure recording.
    
    Equivalent to: note_failure(record)
    Provides explicit entry point for new code.
    """
    _record_to_ledger(record)


def _record_to_ledger(record: FailureRecord) -> None:
    """Convert FailureRecord to ledger key and delegate."""
    key = record.to_ledger_key()
    try:
        with _FAILURE_LOCK:
            _FAILURE_NOTES[key] = _FAILURE_NOTES.get(key, 0) + 1
    except Exception:
        _LOST_NOTES[0] += 1
    
    if record.context or record.severity != "medium" or record.tags:
        _store_enhanced_failure(
            record.where, record.exc, record.context,
            record.severity, record.tags
        )


def _store_enhanced_failure(where: str, exc: Optional[BaseException],
                            context: Optional[Dict[str, Any]],
                            severity: str, tags: Optional[List[str]]) -> None:
    """Store enhanced failure metadata for querying/analysis.
    
    Best-effort: failures here don't affect the primary ledger.
    Implementation writes to a structured JSONL file for later analysis.
    """
    try:
        log_path = Path.home() / ".azazael" / "failure_enhanced.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "where": where,
            "exc_type": type(exc).__name__ if exc is not None else "unknown",
            "exc_msg": str(exc) if exc else "",
            "severity": severity,
            "tags": tags or [],
            "context": context or {},
        }
        
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        # Best-effort by contract -- the primary ledger must never be broken by
        # the optional record -- but a silent `pass` makes a recorder that has
        # been failing for weeks indistinguishable from a working one. Keep the
        # reason, bounded, so `enhanced_store_errors()` can report it.
        del _ENHANCED_STORE_ERRORS[:-49]
        _ENHANCED_STORE_ERRORS.append("%s: %s" % (where, type(exc).__name__))


# --- Re-export canonical functions ---

__all__ = [
    "note_failure",
    "note_failure_v2",
    "enhanced_store_errors", 
    "failure_notes",
    "reset_notes",
    "FailureRecord",
]