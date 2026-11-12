# coding: utf-8
"""
BINARY_ANALYSIS_TYPES — Shared Types, Constants, and Helpers

Shared data classes, constants, and utility functions used by
BINARY_ANALYSIS, BINARY_ANALYSIS_PARSE, BINARY_ANALYSIS_TOOLS,
and BINARY_ANALYSIS_CLI.

This module is intentionally lightweight to minimize import cost.
"""

import sys
import threading
# from typing import Dict, List  # removed — all names have native equivalents
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from CORE.PamsRecorder import PamsRecorder

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

IS_WINDOWS: bool = sys.platform == "win32"
IS_LINUX: bool = sys.platform.startswith("linux")


# -- Lazy PAMS Recorder (thread-safe) -------------------------------

_pams = None
_pams_lock = threading.Lock()


def _get_pams() -> "PamsRecorder | None":
    """Lazy PAMS recorder — double-checked locking for Python 3.14t."""
    global _pams
    if _pams is None:
        with _pams_lock:
            if _pams is None:
                try:
                    from CORE.PamsRecorder import get_recorder
                    _pams = get_recorder()
                except ImportError as exc:
                    note_failure("BINARY_ANALYSIS_TYPES:L36", exc)
                    _pams = False  # Sentinel: don't retry
    if _pams is None or isinstance(_pams, bool):
        return None
    return _pams


# -- Data Classes ----------------------------------------------------

@dataclass
class SymbolEntry:
    """Single symbol from nm/dumpbin."""
    address: int
    name: str
    symbol_type: str  # T=text, D=data, B=bss, U=undefined, etc.
    section: str
    size: int = 0

    @property
    def is_exported(self) -> bool:
        return self.symbol_type in ("T", "t", "D", "d", "B", "b", "R", "r")


@dataclass
class SectionHeader:
    """PE/ELF section header."""
    name: str
    virtual_address: int
    virtual_size: int
    raw_size: int
    raw_offset: int
    characteristics: list[str] = field(default_factory=list)
    entropy: float = 0.0


@dataclass
class PeHeaders:
    """Complete PE header information."""
    path: str
    is_pe: bool
    machine: str
    timestamp: str
    entry_point: int
    image_base: int
    sections: list[SectionHeader]
    imports: list[str]
    exports: list[str]
    subsystem: str
    characteristics: list[str]


@dataclass
class ElfHeaders:
    """Complete ELF header information."""
    path: str
    is_elf: bool
    elf_class: str  # 32-bit or 64-bit
    endianness: str
    entry_point: int
    phoff: int  # Program header offset
    shoff: int  # Section header offset
    program_headers: list[dict]
    section_headers: list[dict]


@dataclass
class EntropyResult:
    """Byte entropy analysis result."""
    path: str
    overall_entropy: float
    max_possible: float
    is_packed: bool
    section_entropy: dict[str, float] = field(default_factory=dict)
    histogram: list[int] = field(default_factory=list)


# -- Constants -------------------------------------------------------

MACHINE_TYPES = {
    0x014C: "i386",
    0x8664: "AMD64",
    0x01C4: "ARM",
    0xAA64: "ARM64",
    0x0200: "IA64",
}

CHARACTERISTICS = {
    0x0002: "EXECUTABLE_IMAGE",
    0x0020: "LARGE_ADDRESS_AWARE",
    0x0100: "32BIT_MACHINE",
    0x2000: "DLL",
}

SUBSYSTEMS = {
    1: "NATIVE",
    2: "WINDOWS_GUI",
    3: "WINDOWS_CUI",
    5: "OS2_CUI",
    7: "POSIX_CUI",
    9: "WINDOWS_CE_GUI",
}

SECTION_FLAGS = {
    0x00000020: "CODE",
    0x00000040: "INITIALIZED_DATA",
    0x00000080: "UNINITIALIZED_DATA",
    0x02000000: "DISCARDABLE",
    0x04000000: "NOT_CACHED",
    0x08000000: "NOT_PAGED",
    0x10000000: "SHARED",
    0x20000000: "EXECUTE",
    0x40000000: "READ",
    0x80000000: "WRITE",
}
