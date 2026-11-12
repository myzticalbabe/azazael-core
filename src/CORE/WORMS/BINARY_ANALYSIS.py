# coding: utf-8
"""
BINARY_ANALYSIS — Binary Analysis & Reverse Engineering Worm

Comprehensive binary analysis, PE/ELF parsing, disassembly, and text
processing utilities for the Azazael ecosystem.

Features:
- nm — Symbol extraction from PE/ELF (exports, imports, debug symbols)
- objdump — Disassembly via dumpbin/objdump with hex + asm output
- mapfile — Memory map analysis for running processes
- pe_headers — PE/COFF header parsing (DOS, NT, Section, Import/Export tables)
- elf_headers — ELF header parsing (header, program headers, section headers)
- strings — Extract printable strings from binary files
- entropy — Calculate byte entropy for packing detection
- sed — Stream editor: regex find/replace on files/stdin
- awk — Text processing: column extraction, filtering, aggregation
- hexdump — Hex dump with ASCII sidebar
- PAMS integration — Every analysis records as a memory insight

Usage:
    >>> from CORE.WORMS.BINARY_ANALYSIS import BinaryAnalysis
    >>> ba = BinaryAnalysis()
    >>> ba.nm("target.exe")
    >>> ba.objdump("target.exe", section=".text")
    >>> ba.strings("target.exe", min_length=4)
    >>> ba.pe_headers("target.exe")
    >>> ba.entropy("target.exe")

Module Organization:
    BINARY_ANALYSIS_TYPES — Shared data classes, constants, and _get_pams()
    BINARY_ANALYSIS_PARSE — PE/ELF parsing helpers (lazy-loaded)
    BINARY_ANALYSIS_TOOLS — Text processing, disassembly, entropy (lazy-loaded)
    BINARY_ANALYSIS_CLI  — CLI entry point (lazy-loaded)
"""

import sys
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List

# Lightweight imports (shared types, no heavy parsing code)
from CORE.WORMS.BINARY_ANALYSIS_TYPES import (
    SymbolEntry, PeHeaders, ElfHeaders, EntropyResult, _get_pams,
)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry -----------------------------------------------------
try:
    from CORE.worm_registry import register_worm  # pyright: ignore[reportAssignmentType] -- optional import: except-path defines fallback decorator; selected by import success
    _REGISTRY = True
except ImportError as exc:
    note_failure("BINARY_ANALYSIS:L52", exc)
    _REGISTRY = False
    def register_worm(**kw):
        return lambda cls: cls


# -- BinaryAnalysis Worm -----------------------------------------------

@register_worm(category="analysis", priority=6, cooldown_seconds=60.0,
    description="Binary analysis & reverse engineering (PE/ELF, symbols, entropy, disasm, sed, awk)")
class BinaryAnalysis:
    """Binary Analysis & Reverse Engineering Worm.

    Provides cross-platform binary analysis including symbol extraction,
    disassembly, PE/ELF parsing, string extraction, entropy calculation,
    and sed/awk-style text processing.
    All analysis results are automatically recorded to PAMS memory.
    """

    VERSION = "1.0.0"

    def __init__(self):
        pass

    def _record(self, event_type: str, data: dict[str, Any], severity: str = "info"):
        pams = _get_pams()
        if pams:
            try:
                 pams.record_event(  # pyright: ignore[reportAttributeAccessIssue]
                    event_type=f"binanalysis.{event_type}",
                    data=data, source="BinaryAnalysis", severity=severity,
                )
            except Exception as exc:
                note_failure("BINARY_ANALYSIS:L84", exc)

    # -- nm (Symbol Extraction) ------------------------------------------------

    def nm(self, path: str, demangle: bool = False) -> list[SymbolEntry]:
        """Extract symbols from a binary (PE exports/imports or ELF symbols)."""
        from CORE.WORMS.BINARY_ANALYSIS_PARSE import extract_symbols
        symbols = extract_symbols(path, demangle=demangle)
        self._record("nm", {
            "path": path,
            "symbols_found": len(symbols),
            "exported": sum(1 for s in symbols if s.is_exported),
        })
        return symbols

    # -- objdump (Disassembly) -------------------------------------------------

    def objdump(
        self,
        path: str,
        section: (str | None) = None,
        max_instructions: int = 500,
    ) -> dict[str, Any]:
        """Disassemble a binary section."""
        from CORE.WORMS.BINARY_ANALYSIS_TOOLS import disassemble_binary
        return disassemble_binary(path, section=section, max_instructions=max_instructions)

    # -- pe_headers (PE/COFF Header Parser) ------------------------------------

    def pe_headers(self, path: str) -> PeHeaders:
        """Parse PE/COFF headers from a Windows executable."""
        from CORE.WORMS.BINARY_ANALYSIS_PARSE import parse_pe_headers
        return parse_pe_headers(path)

    # -- elf_headers (ELF Header Parser) ---------------------------------------

    def elf_headers(self, path: str) -> ElfHeaders:
        """Parse ELF headers from a Linux binary."""
        from CORE.WORMS.BINARY_ANALYSIS_PARSE import parse_elf_headers
        return parse_elf_headers(path)

    # -- Strings ---------------------------------------------------------------

    def strings(
        self,
        path: str,
        min_length: int = 4,
        encoding: str = "ascii",
        max_strings: int = 200,
    ) -> list[str]:
        """Extract printable strings from a binary file."""
        from CORE.WORMS.BINARY_ANALYSIS_TOOLS import extract_strings
        return extract_strings(path, min_length=min_length, encoding=encoding, max_strings=max_strings)

    # -- Entropy ---------------------------------------------------------------

    def entropy(self, path: str) -> EntropyResult:
        """Calculate byte entropy for packing/malware detection."""
        from CORE.WORMS.BINARY_ANALYSIS_TOOLS import analyze_entropy
        return analyze_entropy(path)

    # -- mapfile (Memory Map Analysis) -----------------------------------------

    def mapfile(self, pid: (int | None) = None) -> dict[int, list[dict[str, Any]]]:
        """Analyze memory maps for a process (or all processes)."""
        from CORE.WORMS.BINARY_ANALYSIS_TOOLS import analyze_memory_maps
        return analyze_memory_maps(pid=pid)

    # -- hexdump --------------------------------------------------------------

    def hexdump(
        self,
        path: str,
        offset: int = 0,
        length: int = 512,
        bytes_per_line: int = 16,
    ) -> str:
        """Produce a hex dump with ASCII sidebar."""
        from CORE.WORMS.BINARY_ANALYSIS_TOOLS import generate_hexdump
        return generate_hexdump(path, offset=offset, length=length, bytes_per_line=bytes_per_line)

    # -- sed (Stream Editor) --------------------------------------------------

    def sed(
        self,
        pattern: str,
        replacement: str,
        input_path: (str | None) = None,
        text: (str | None) = None,
        regex: bool = True,
        global_replace: bool = True,
        case_insensitive: bool = False,
    ) -> str:
        """Stream editor: regex or literal find/replace."""
        from CORE.WORMS.BINARY_ANALYSIS_TOOLS import run_sed
        return run_sed(
            pattern, replacement,
            input_path=input_path, text=text,
            regex=regex, global_replace=global_replace,
            case_insensitive=case_insensitive,
        )

    # -- awk (Text Processing) -------------------------------------------------

    def awk(
        self,
        input_path: (str | None) = None,
        text: (str | None) = None,
        field_separator: str = r"\s+",
        output_fields: (list[int] | None) = None,
        filter_pattern: (str | None) = None,
        output_separator: str = "\t",
    ) -> str:
        """Text processing: column extraction, filtering, aggregation."""
        from CORE.WORMS.BINARY_ANALYSIS_TOOLS import run_awk
        return run_awk(
            input_path=input_path, text=text,
            field_separator=field_separator,
            output_fields=output_fields,
            filter_pattern=filter_pattern,
            output_separator=output_separator,
        )

    # -- Combined Analysis -----------------------------------------------------

    def analyze_file(self, path: str) -> dict[str, Any]:
        """Alias for analyze_binary — used by execute_all."""
        return self.analyze_binary(path)

    def get_summary(self) -> dict[str, Any]:
        """Return a lightweight status summary for MetaWormSpawner fleet reports."""
        return {
            "version": self.VERSION,
            "platform": sys.platform,
            "tools": ["nm", "objdump", "pe_headers", "elf_headers", "strings", "entropy",
                       "mapfile", "hexdump", "sed", "awk"],
            "tool_count": 10,
            "pams_connected": _get_pams() is not None,
        }

    def analyze_binary(self, path: str) -> dict[str, Any]:
        """Run a full binary analysis: headers, symbols, strings, entropy, hexdump."""
        from CORE.WORMS.BINARY_ANALYSIS_PARSE import is_pe_file, is_elf_file

        report: dict[str, Any] = {
            "path": path,
            "timestamp": datetime.now().isoformat(),
            "version": self.VERSION,
        }

        full_path = Path(path)
        if not full_path.exists():
            return {"error": "File not found"}

        # Detect type
        is_pe = is_pe_file(full_path)
        is_elf = is_elf_file(full_path)
        report["type"] = "PE" if is_pe else ("ELF" if is_elf else "UNKNOWN")

        # Headers
        if is_pe:
            pe = self.pe_headers(str(full_path))
            report["machine"] = pe.machine
            report["sections"] = len(pe.sections)
            report["pe_details"] = {
                "machine": pe.machine,
                "subsystem": pe.subsystem,
                "entry_point": hex(pe.entry_point),
                "image_base": hex(pe.image_base),
                "sections": [{"name": s.name, "entropy": s.entropy} for s in pe.sections],
            }
        elif is_elf:
            elf = self.elf_headers(str(full_path))
            report["class"] = elf.elf_class
            report["elf_details"] = {
                "class": elf.elf_class,
                "endianness": elf.endianness,
                "entry_point": hex(elf.entry_point),
            }

        # Symbols
        symbols = self.nm(str(full_path))
        report["symbols"] = len(symbols)

        # Strings (URLs, paths, etc.)
        strs = self.strings(str(full_path))
        urls = [s for s in strs if re.search(r"https?://", s)]
        paths = [s for s in strs if re.search(r"[A-Z]:\\|/[a-z]+/", s)]
        report["strings"] = {
            "total": len(strs),
            "urls": urls[:20],
            "paths": paths[:20],
        }

        # Entropy
        ent = self.entropy(str(full_path))
        report["entropy"] = ent.overall_entropy
        report["is_packed"] = ent.is_packed

        # Hexdump (first 256 bytes)
        report["hex_preview"] = self.hexdump(str(full_path), length=256)

        self._record("full_analysis", report)
        return report


# -- CLI Entry Point (delegated to BINARY_ANALYSIS_CLI.py) --------
def main():
    """Delegate CLI to BINARY_ANALYSIS_CLI to avoid importing argparse at worm import time."""
    from CORE.WORMS.BINARY_ANALYSIS_CLI import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()
