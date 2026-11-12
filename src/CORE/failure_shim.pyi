# failure_shim.pyi — type stubs for failure_shim.
#
# This file shadows failure_shim.py for type checkers, so it must declare the
# module's public surface itself. It used to `from .failure_shim import
# FailureRecord` — a self-import that resolves to nothing — and then re-declare
# failure_notes, reset_notes, note_failure_v2 and FailureRecord as bare
# variables at the bottom. Those two defects are behind every diagnostic this
# file produced: callers saw "unknown import symbol", and the two positions that
# used FailureRecord as a type were "variable not allowed in type expression".

from typing import Any, Dict, List, Optional, overload

class FailureRecord:
    where: str
    exc: Optional[BaseException]
    context: Dict[str, Any]
    severity: str
    tags: List[str]
    timestamp: str

    def __init__(
        self,
        where: str,
        exc: Optional[BaseException] = ...,
        context: Optional[Dict[str, Any]] = ...,
        severity: str = ...,
        tags: Optional[List[str]] = ...,
        timestamp: Optional[str] = ...,
    ) -> None: ...
    def to_ledger_key(self) -> str: ...

@overload
def note_failure(where: str, exc: Optional[BaseException] = ...) -> None: ...
@overload
def note_failure(
    where: str,
    exc: Optional[BaseException] = ...,
    *,
    context: Optional[Dict[str, Any]] = ...,
    severity: str = ...,
    tags: Optional[List[str]] = ...,
) -> None: ...
@overload
def note_failure(where: FailureRecord) -> None: ...
def note_failure(
    where: Any = ...,
    exc: Any = ...,
    *,
    context: Any = ...,
    severity: Any = ...,
    tags: Any = ...,
) -> None: ...

def note_failure_v2(record: FailureRecord) -> None: ...
def enhanced_store_errors() -> List[str]: ...
def failure_notes() -> Dict[str, int]: ...
def reset_notes() -> None: ...
