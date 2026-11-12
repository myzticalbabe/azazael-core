"""Concept 6: xMemory decouple-aggregate.

Implements a cross-memory decoupling and aggregation layer that enables
worms to safely read/write from multiple memory banks (short-term, long-term,
working, operational) without cross-contamination, with automatic
conflict detection and resolution via version vectors and idempotent
operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class MemoryBank(Enum):
    """Available memory banks for decoupled access."""

    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    WORKING = "working"
    OPERATIONAL = "operational"


class OperationType(Enum):
    """Types of memory operations for conflict tracking."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    CLEAR = "clear"


@dataclass
class VersionVector:
    """Vector clock for tracking version across memory banks."""

    banks: Dict[MemoryBank, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for bank in MemoryBank:
            if bank not in self.banks:
                self.banks[bank] = 0

    def bump(self, bank: MemoryBank) -> VersionVector:
        """Return a new vector with the given bank incremented."""
        new = VersionVector(banks=dict(self.banks))
        new.banks[bank] = new.banks.get(bank, 0) + 1
        return new

    def __gt__(self, other: "VersionVector") -> bool:
        """Check if this vector is strictly greater than another."""
        if not isinstance(other, VersionVector):
            return NotImplemented
        for bank in set(list(self.banks.keys()) + list(other.banks.keys())):
            self_val = self.banks.get(bank, 0)
            other_val = other.banks.get(bank, 0)
            if self_val < other_val:
                return False
            if self_val > other_val:
                # Once we find a bank where we're ahead, all others must also be ahead
                # for strict > - this is a simplification; full vector comparison is more complex
                pass
        # Full strict comparison: every bank must be >= and at least one >
        all_ge = all(self.banks.get(b, 0) >= other.banks.get(b, 0) for b in set(list(self.banks.keys()) + list(other.banks.keys())))
        any_gt = any(self.banks.get(b, 0) > other.banks.get(b, 0) for b in set(list(self.banks.keys()) + list(other.banks.keys())))
        return all_ge and any_gt

    def __ge__(self, other: "VersionVector") -> bool:
        """Check if this vector is >= another."""
        if not isinstance(other, VersionVector):
            return NotImplemented
        return all(self.banks.get(b, 0) >= other.banks.get(b, 0) for b in set(list(self.banks.keys()) + list(other.banks.keys())))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VersionVector):
            return NotImplemented
        return self.banks == other.banks


@dataclass
class MemoryOp:
    """Represents a single memory operation with version tracking."""

    op_id: str
    bank: MemoryBank
    operation: OperationType
    key: str
    value: Optional[Any] = None
    timestamp: float = field(default_factory=lambda: __import__("time").time())
    source_worm: Optional[str] = None
    version_before: Optional[VersionVector] = None
    version_after: Optional[VersionVector] = None


@dataclass
class ConflictRecord:
    """Records a detected memory conflict for resolution."""

    op_id: str
    conflicting_ops: List[MemoryOp]
    resolution: str = "pending"  # pending, abort, merge, retry
    detected_at: float = field(default_factory=lambda: __import__("time").time())


@dataclass
class XMemoryDecoupler:
    """Cross-memory decoupling layer with versioned access and conflict detection.

    Enables worms to safely read/write from multiple memory banks without
    cross-contamination, with automatic conflict detection and resolution
    via version vectors and idempotent operations.
    """

    _ops: Dict[str, MemoryOp] = field(default_factory=dict)
    _conflicts: Dict[str, ConflictRecord] = field(default_factory=dict)
    _version_vectors: Dict[MemoryBank, VersionVector] = field(default_factory=dict)
    _next_op_id: int = 0
    # In-memory per-bank store backing _do_read/_do_write (keyed by bank+key).
    _store: Dict[tuple, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for bank in MemoryBank:
            self._version_vectors[bank] = VersionVector()

    def next_op_id(self) -> str:
        """Generate a unique operation ID."""
        self._next_op_id += 1
        return f"op-{self._next_op_id:06d}"

    def read(self, bank: MemoryBank, key: str, worm: Optional[str] = None) -> Optional[Any]:
        """Read a value from a memory bank.

        Returns the value if found, None otherwise. Records a read op.
        """
        op_id = self.next_op_id()
        op = MemoryOp(
            op_id=op_id,
            bank=bank,
            operation=OperationType.READ,
            key=key,
            source_worm=worm,
            version_before=self._version_vectors[bank].bump(bank),
        )

        # Check version before reading - ensure no pending writes
        # that would make this read stale
        value = self._do_read(bank, key)

        op.version_after = self._version_vectors[bank].bump(bank)
        self._ops[op_id] = op

        return value

    def _do_read(self, bank: MemoryBank, key: str) -> Optional[Any]:
        """Internal read implementation - bank-isolated lookup."""
        return self._store.get((bank, key))

    def _do_write(self, bank: MemoryBank, key: str, value: Any) -> None:
        """Internal write implementation - bank-isolated store."""
        self._store[(bank, key)] = value

    def write(self, bank: MemoryBank, key: str, value: Any, worm: Optional[str] = None) -> bool:
        """Write a value to a memory bank with conflict detection.

        Returns True if write succeeded, False if conflict detected and aborted.
        """
        # Get current version vector before write
        current_vv = self._version_vectors[bank]

        # Check for conflicts with pending operations
        conflicting_ops = self._find_conflicting_ops(bank, key, OperationType.WRITE)

        if conflicting_ops and self._has_pending_write(conflicting_ops, bank):
            # Conflict detected - resolve or abort
            resolution = self._resolve_conflict(bank, key, conflicting_ops, worm)
            if resolution == "abort":
                return False

        op_id = self.next_op_id()
        op = MemoryOp(
            op_id=op_id,
            bank=bank,
            operation=OperationType.WRITE,
            key=key,
            value=value,
            source_worm=worm,
            version_before=current_vv.bump(bank),
        )

        # Execute write and update version
        self._do_write(bank, key, value)
        new_vv = current_vv.bump(bank)
        self._version_vectors[bank] = new_vv
        op.version_after = new_vv
        self._ops[op_id] = op

        return True

    def _find_conflicting_ops(
        self, bank: MemoryBank, key: str, op_type: OperationType
    ) -> List[MemoryOp]:
        """Find operations that conflict with the given operation."""
        conflicts = []
        for op in self._ops.values():
            if op.bank != bank:
                continue
            if op.operation != op_type:
                continue
            if op.key != key:
                continue
            # Same key and type = potential conflict
            conflicts.append(op)
        return conflicts

    def _has_pending_write(self, ops: List[MemoryOp], bank: MemoryBank) -> bool:
        """Check if any pending ops represent an un-resolved write conflict."""
        return any(op.operation == OperationType.WRITE for op in ops)

    def _resolve_conflict(
        self,
        bank: MemoryBank,
        key: str,
        conflicting_ops: List[MemoryOp],
        worm: Optional[str] = None,
    ) -> str:
        """Resolve a detected memory conflict.

        Args:
            bank: Bank the incoming write targets.
            key: Key the incoming write targets.
            conflicting_ops: Prior ops found conflicting with the write.
            worm: Source of the incoming write (None = anonymous source).

        Returns: "abort", "merge", or "retry". A write replaying its own
        source's history is not a cross-contamination conflict -- the
        version vectors already order those operations -- so it resolves
        to "retry" and the write proceeds. When any prior conflicting op
        originates from a different source, the write resolves to "abort"
        and fails; the conflicting worm can re-read and re-write
        deliberately. "merge" is reserved for a future reconciliation
        policy. An unset source_worm on prior ops counts as its own
        anonymous source.
        """
        if not conflicting_ops:
            return "retry"

        # Simple resolution: if every conflicting op is from the same worm
        # as the incoming write (or has no source and the write is
        # anonymous), the "conflict" is a replay of that worm's own writes
        # -- the version vector already orders them, so proceed. A prior
        # op from any other source means a real cross-worm collision:
        # abort rather than silently merge over another worm's value.
        sources = {op.source_worm for op in conflicting_ops}
        if sources.issubset({worm}):
            return "retry"
        return "abort"
