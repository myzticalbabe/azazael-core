# coding: utf-8
"""
BINARY_ANALYSIS_PARSE — PE/ELF Binary Parsing Helpers

Extracted from BINARY_ANALYSIS.py for modularity.
Provides functions for PE/COFF and ELF header parsing, symbol extraction,
and binary type detection.

Usage:
    >>> from CORE.WORMS.BINARY_ANALYSIS_PARSE import parse_pe_headers, parse_elf_headers, extract_symbols
"""

import struct
import math
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List
from collections import Counter

_LOCK = threading.Lock()

from CORE.WORMS.BINARY_ANALYSIS_TYPES import (
    SymbolEntry, SectionHeader, PeHeaders, ElfHeaders,
    _get_pams, IS_WINDOWS, IS_LINUX,
    MACHINE_TYPES, CHARACTERISTICS, SUBSYSTEMS, SECTION_FLAGS,
)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# -- Helpers -----------------------------------------------------------

def _record(event_type: str, data: dict[str, Any], severity: str = "info"):
    """Record event to PAMS memory (standalone helper). Thread-safe via module lock."""
    with _LOCK:
        pams = _get_pams()
        if pams:
            try:
                pams.record_event(
                    event_type=f"binanalysis.{event_type}",
                    data=data, source="BinaryAnalysis", severity=severity,
                )
            except Exception as exc:
                note_failure("BINARY_ANALYSIS_PARSE:L43", exc)


def _shannon_entropy(data: bytes) -> float:
    """Calculate Shannon entropy of byte data."""
    if not data:
        return 0.0
    counter = Counter(data)
    total = len(data)
    entropy = 0.0
    for count in counter.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    return entropy


# -- Binary Type Detection ---------------------------------------------

def is_pe_file(path: Path) -> bool:
    """Check if file is a PE (Portable Executable)."""
    try:
        with open(path, "rb") as f:
            dos_sig = f.read(2)
            if dos_sig != b"MZ":
                return False
            f.seek(0x3C)
            pe_offset = struct.unpack("<I", f.read(4))[0]
            f.seek(pe_offset)
            pe_sig = f.read(4)
            return pe_sig == b"PE\x00\x00"
    except Exception as exc:
        note_failure("BINARY_ANALYSIS_PARSE:L75", exc)
        return False


def is_elf_file(path: Path) -> bool:
    """Check if file is an ELF binary."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            return magic == b"\x7fELF"
    except Exception as exc:
        note_failure("BINARY_ANALYSIS_PARSE:L85", exc)
        return False


# -- PE Parsing --------------------------------------------------------

def _rva_to_offset(f, rva: int) -> (int | None):
    """Convert RVA to file offset using section headers."""
    try:
        f.seek(0x3C)
        pe_offset = struct.unpack("<I", f.read(4))[0]
        f.seek(pe_offset + 4)
        coff = f.read(20)
        num_sections = struct.unpack_from("<H", coff, 2)[0]
        size_opt = struct.unpack_from("<H", coff, 16)[0]
        section_start = pe_offset + 24 + size_opt

        for i in range(num_sections):
            f.seek(section_start + i * 40)
            sec = f.read(40)
            sec_vsize = struct.unpack_from("<I", sec, 8)[0]
            sec_vaddr = struct.unpack_from("<I", sec, 12)[0]
            struct.unpack_from("<I", sec, 16)[0]
            sec_raw_offset = struct.unpack_from("<I", sec, 20)[0]

            if sec_vaddr <= rva < sec_vaddr + sec_vsize:
                return (rva - sec_vaddr) + sec_raw_offset
    except Exception as exc:
        note_failure("BINARY_ANALYSIS_PARSE:L112", exc)
    return None


def _parse_exports(f, dir_info: tuple[int, int]) -> list[SymbolEntry]:
    """Parse PE export directory."""
    symbols = []
    rva, size = dir_info
    try:
        f.seek(0)
        export_offset = _rva_to_offset(f, rva)
        if export_offset is None:
            return symbols

        f.seek(export_offset)
        export_dir = f.read(40)
        num_names = struct.unpack_from("<I", export_dir, 24)[0]
        addr_of_names = struct.unpack_from("<I", export_dir, 32)[0]
        addr_of_funcs = struct.unpack_from("<I", export_dir, 28)[0]

        names_offset = _rva_to_offset(f, addr_of_names)
        funcs_offset = _rva_to_offset(f, addr_of_funcs)

        if names_offset is None or funcs_offset is None:
            return symbols

        for i in range(min(num_names, 500)):
            f.seek(names_offset + i * 4)
            name_rva = struct.unpack("<I", f.read(4))[0]
            name_off = _rva_to_offset(f, name_rva)

            f.seek(funcs_offset + i * 4)
            func_rva = struct.unpack("<I", f.read(4))[0]

            if name_off:
                f.seek(name_off)
                name = b""
                while True:
                    ch = f.read(1)
                    if ch == b"\x00" or not ch:
                        break
                    name += ch
                name_str = name.decode("utf-8", errors="replace")

                symbols.append(SymbolEntry(
                    address=func_rva,
                    name=name_str,
                    symbol_type="T",
                    section=f"export_{i}",
                ))
    except Exception as exc:
        note_failure("BINARY_ANALYSIS_PARSE:L163", exc)
    return symbols


def _parse_imports(f, dir_info: tuple[int, int]) -> list[SymbolEntry]:
    """Parse PE import directory."""
    symbols = []
    rva, size = dir_info
    try:
        f.seek(0)
        import_offset = _rva_to_offset(f, rva)
        if import_offset is None:
            return symbols

        f.seek(import_offset)
        while True:
            entry = f.read(20)
            if all(b == 0 for b in entry):
                break
            name_rva = struct.unpack_from("<I", entry, 12)[0]
            name_off = _rva_to_offset(f, name_rva)

            if name_off:
                f.seek(name_off)
                dll_name = b""
                while True:
                    ch = f.read(1)
                    if ch == b"\x00" or not ch:
                        break
                    dll_name += ch
                dll_str = dll_name.decode("utf-8", errors="replace")

                symbols.append(SymbolEntry(
                    address=0,
                    name=dll_str,
                    symbol_type="U",
                    section="IMPORT",
                ))
    except Exception as exc:
        note_failure("BINARY_ANALYSIS_PARSE:L202", exc)
    return symbols


def _nm_pe(path: Path) -> list[SymbolEntry]:
    """Extract PE export and import symbols."""
    symbols = []
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return symbols
            f.seek(0x3C)
            pe_offset = struct.unpack("<I", f.read(4))[0]
            f.seek(pe_offset)
            if f.read(4) != b"PE\x00\x00":
                return symbols

            coff = f.read(20)
            struct.unpack_from("<H", coff, 2)[0]
            struct.unpack_from("<H", coff, 16)[0]
            opt_header_start = f.tell()
            magic = struct.unpack("<H", f.read(2))[0]
            f.seek(opt_header_start)

            # NumberOfRvaAndSizes precedes the DataDirectory array: +108
            # (PE32+) / +92 (PE32) per the PE spec. Seeking +112/+96 instead
            # reads DataDirectory[0].VirtualAddress as the count and every
            # tuple one DWORD late, so every real PE parsed to 0 rows
            # (2026-10-22 lane-B root-cause #4, applied here by the owner).
            if magic == 0x20B:  # PE32+
                f.seek(opt_header_start + 108)
            else:
                f.seek(opt_header_start + 92)

            num_rvas = struct.unpack("<I", f.read(4))[0]
            directories = []
            for _ in range(min(num_rvas, 16)):
                rva = struct.unpack("<I", f.read(4))[0]
                size = struct.unpack("<I", f.read(4))[0]
                directories.append((rva, size))

            if len(directories) > 0 and directories[0][0] > 0:
                symbols.extend(_parse_exports(f, directories[0]))
            if len(directories) > 1 and directories[1][0] > 0:
                symbols.extend(_parse_imports(f, directories[1]))
    except Exception as e:
        _record("nm_pe_error", {"path": str(path), "error": str(e)}, severity="error")
    return symbols


def _nm_linux(path: Path, demangle: bool = False) -> list[SymbolEntry]:
    """Use nm command on Linux."""
    symbols = []
    try:
        cmd = ["nm", "-S"]
        if demangle:
            cmd.append("--demangle")
        cmd.append(str(path))
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 3)
            if len(parts) < 3:
                continue
            try:
                addr = int(parts[0], 16) if parts[0] != "U" else 0
            except ValueError as exc:
                note_failure("BINARY_ANALYSIS_PARSE:L266", exc)
                addr = 0
            size_str = parts[1]
            try:
                size = int(size_str, 16)
            except ValueError as exc:
                note_failure("BINARY_ANALYSIS_PARSE:L271", exc)
                size = 0
            sym_type = parts[2]
            name = parts[3] if len(parts) > 3 else ""
            symbols.append(SymbolEntry(
                address=addr if isinstance(addr, int) else 0,  # type: ignore[reportUnnecessaryIsInstance]
                name=name,
                symbol_type=sym_type,
                section=sym_type,
                size=size,
            ))
    except Exception as e:
        _record("nm_linux_error", {"path": str(path), "error": str(e)}, severity="error")
    return symbols


def _nm_external(path: Path) -> list[SymbolEntry]:
    """Try external nm/dumpbin."""
    symbols = []
    for cmd_name in [["nm", str(path)], ["dumpbin", "/EXPORTS", str(path)], ["objdump", "-T", str(path)]]:
        try:
            proc = subprocess.run(cmd_name, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] not in ("File", "Dump", "Microsoft"):
                        try:
                            addr = int(parts[0], 16)
                            name = parts[-1]
                            symbols.append(SymbolEntry(
                                address=addr, name=name,
                                symbol_type="?", section="external",
                            ))
                        except ValueError as exc:
                            note_failure("BINARY_ANALYSIS_PARSE:L304", exc)
                if symbols:
                    break
        except Exception as exc:
            note_failure("BINARY_ANALYSIS_PARSE:L308", exc)
    return symbols


def extract_symbols(path: str, demangle: bool = False) -> list[SymbolEntry]:
    """Extract symbols from a binary (PE exports/imports or ELF symbols)."""
    symbols = []
    full_path = Path(path)
    if not full_path.exists():
        _record("nm_error", {"path": path, "error": "File not found"}, severity="error")
        return symbols

    if IS_WINDOWS and is_pe_file(full_path):
        symbols = _nm_pe(full_path)
    elif IS_LINUX and is_elf_file(full_path):
        symbols = _nm_linux(full_path, demangle=demangle)
    else:
        symbols = _nm_external(full_path)

    _record("nm", {
        "path": str(full_path),
        "symbols_found": len(symbols),
        "exported": sum(1 for s in symbols if s.is_exported),
    })
    return symbols


# -- PE Headers -------------------------------------------------------

def parse_pe_headers(path: str) -> PeHeaders:
    """Parse PE/COFF headers from a Windows executable."""
    full_path = Path(path)
    result = PeHeaders(
        path=str(full_path), is_pe=False, machine="",
        timestamp="", entry_point=0, image_base=0,
        sections=[], imports=[], exports=[],
        subsystem="", characteristics=[],
    )
    if not full_path.exists():
        return result

    try:
        with open(full_path, "rb") as f:
            if f.read(2) != b"MZ":
                return result
            f.seek(0x3C)
            pe_offset = struct.unpack("<I", f.read(4))[0]
            f.seek(pe_offset)
            if f.read(4) != b"PE\x00\x00":
                return result

            result.is_pe = True
            coff = f.read(20)
            machine_id = struct.unpack_from("<H", coff, 0)[0]
            num_sections = struct.unpack_from("<H", coff, 2)[0]
            timestamp_val = struct.unpack_from("<I", coff, 4)[0]
            characteristics_val = struct.unpack_from("<H", coff, 18)[0]

            result.machine = MACHINE_TYPES.get(machine_id, f"0x{machine_id:04X}")
            result.timestamp = datetime.fromtimestamp(timestamp_val).isoformat()

            for flag, name in CHARACTERISTICS.items():
                if characteristics_val & flag:
                    result.characteristics.append(name)

            opt_start = f.tell()
            magic = struct.unpack("<H", f.read(2))[0]
            f.seek(opt_start)

            if magic == 0x20B:  # PE32+
                opt = f.read(112)
                result.image_base = struct.unpack_from("<Q", opt, 24)[0]
                result.entry_point = struct.unpack_from("<I", opt, 16)[0]
                result.subsystem = SUBSYSTEMS.get(struct.unpack_from("<H", opt, 68)[0], "UNKNOWN")
            else:
                opt = f.read(96)
                result.image_base = struct.unpack_from("<I", opt, 28)[0]
                result.entry_point = struct.unpack_from("<I", opt, 16)[0]
                result.subsystem = SUBSYSTEMS.get(struct.unpack_from("<H", opt, 68)[0], "UNKNOWN")

            # Section headers
            section_start = opt_start + struct.unpack_from("<H", coff, 16)[0]
            for i in range(num_sections):
                f.seek(section_start + i * 40)
                sec = f.read(40)
                name = sec[:8].rstrip(b"\x00").decode("ascii", errors="replace").rstrip("\x00")
                vsize = struct.unpack_from("<I", sec, 8)[0]
                vaddr = struct.unpack_from("<I", sec, 12)[0]
                rsize = struct.unpack_from("<I", sec, 16)[0]
                roff = struct.unpack_from("<I", sec, 20)[0]
                chars = struct.unpack_from("<I", sec, 36)[0]

                sec_data = b""
                if rsize > 0:
                    current = f.tell()
                    f.seek(roff)
                    sec_data = f.read(min(rsize, 65536))
                    f.seek(current)

                ent = _shannon_entropy(sec_data) if sec_data else 0.0
                flags = [flg_name for flg, flg_name in SECTION_FLAGS.items() if chars & flg]

                result.sections.append(SectionHeader(
                    name=name,
                    virtual_address=vaddr,
                    virtual_size=vsize,
                    raw_size=rsize,
                    raw_offset=roff,
                    characteristics=flags,
                    entropy=round(ent, 3),
                ))

            # Exports/imports via nm
            try:
                symbols = _nm_pe(full_path)
            except Exception as exc:
                note_failure("BINARY_ANALYSIS_PARSE:L424", exc)
                symbols = []
            for s in symbols:
                if s.symbol_type == "T":
                    result.exports.append(s.name)
                elif s.symbol_type == "U":
                    result.imports.append(s.name)

    except Exception as e:
        _record("pe_headers_error", {"path": str(full_path), "error": str(e)}, severity="error")

    _record("pe_headers", {
        "path": str(full_path), "is_pe": result.is_pe,
        "machine": result.machine, "sections": len(result.sections),
        "exports": len(result.exports), "imports": len(result.imports),
    })
    return result


# -- ELF Headers ------------------------------------------------------

def parse_elf_headers(path: str) -> ElfHeaders:
    """Parse ELF headers from a Linux binary."""
    full_path = Path(path)
    result = ElfHeaders(
        path=str(full_path), is_elf=False, elf_class="",
        endianness="", entry_point=0, phoff=0, shoff=0,
        program_headers=[], section_headers=[],
    )
    if not full_path.exists():
        return result

    try:
        with open(full_path, "rb") as f:
            if f.read(4) != b"\x7fELF":
                return result
            result.is_elf = True
            elf_class = f.read(1)[0]
            result.elf_class = "32-bit" if elf_class == 1 else "64-bit"
            endian = f.read(1)[0]
            result.endianness = "little-endian" if endian == 1 else "big-endian"
            is_64 = elf_class == 2
            fmt = "<" if endian == 1 else ">"

            f.read(1)  # ELF version
            f.read(1)  # OS ABI
            f.read(1)  # ABI version
            f.read(7)  # Padding
            f.read(2)  # e_type

            struct.unpack(f"{fmt}H", f.read(2))[0]
            f.read(4)  # e_version

            if is_64:
                result.entry_point = struct.unpack(f"{fmt}Q", f.read(8))[0]
                result.phoff = struct.unpack(f"{fmt}Q", f.read(8))[0]
                result.shoff = struct.unpack(f"{fmt}Q", f.read(8))[0]
                f.read(4)
                f.read(2)
                phentsize = struct.unpack(f"{fmt}H", f.read(2))[0]
                phnum = struct.unpack(f"{fmt}H", f.read(2))[0]
                shentsize = struct.unpack(f"{fmt}H", f.read(2))[0]
                shnum = struct.unpack(f"{fmt}H", f.read(2))[0]
            else:
                result.entry_point = struct.unpack(f"{fmt}I", f.read(4))[0]
                result.phoff = struct.unpack(f"{fmt}I", f.read(4))[0]
                result.shoff = struct.unpack(f"{fmt}I", f.read(4))[0]
                f.read(4)
                f.read(2)
                phentsize = struct.unpack(f"{fmt}H", f.read(2))[0]
                phnum = struct.unpack(f"{fmt}H", f.read(2))[0]
                shentsize = struct.unpack(f"{fmt}H", f.read(2))[0]
                shnum = struct.unpack(f"{fmt}H", f.read(2))[0]

            # Read program headers
            if result.phoff > 0 and phnum > 0:
                f.seek(result.phoff)
                for _ in range(min(phnum, 100)):
                    ph_data = f.read(phentsize)
                    if is_64:
                        p_type = struct.unpack_from(f"{fmt}I", ph_data, 0)[0]
                        p_offset = struct.unpack_from(f"{fmt}Q", ph_data, 8)[0]
                        p_vaddr = struct.unpack_from(f"{fmt}Q", ph_data, 16)[0]
                        p_filesz = struct.unpack_from(f"{fmt}Q", ph_data, 32)[0]
                        p_memsz = struct.unpack_from(f"{fmt}Q", ph_data, 40)[0]
                    else:
                        p_type = struct.unpack_from(f"{fmt}I", ph_data, 0)[0]
                        p_offset = struct.unpack_from(f"{fmt}I", ph_data, 4)[0]
                        p_vaddr = struct.unpack_from(f"{fmt}I", ph_data, 8)[0]
                        p_filesz = struct.unpack_from(f"{fmt}I", ph_data, 16)[0]
                        p_memsz = struct.unpack_from(f"{fmt}I", ph_data, 20)[0]
                    result.program_headers.append({
                        "type": hex(p_type), "offset": p_offset,
                        "vaddr": hex(p_vaddr), "filesz": p_filesz, "memsz": p_memsz,
                    })

            # Read section headers
            if result.shoff > 0 and shnum > 0:
                f.seek(result.shoff)
                for _ in range(min(shnum, 100)):
                    sh_data = f.read(shentsize)
                    sh_name_idx = struct.unpack_from(f"{fmt}I", sh_data, 0)[0]
                    if is_64:
                        sh_addr = struct.unpack_from(f"{fmt}Q", sh_data, 16)[0]
                        sh_offset = struct.unpack_from(f"{fmt}Q", sh_data, 24)[0]
                        sh_size = struct.unpack_from(f"{fmt}Q", sh_data, 32)[0]
                    else:
                        sh_addr = struct.unpack_from(f"{fmt}I", sh_data, 12)[0]
                        sh_offset = struct.unpack_from(f"{fmt}I", sh_data, 16)[0]
                        sh_size = struct.unpack_from(f"{fmt}I", sh_data, 20)[0]
                    result.section_headers.append({
                        "name_idx": sh_name_idx, "addr": hex(sh_addr),
                        "offset": sh_offset, "size": sh_size,
                    })
    except Exception as e:
        _record("elf_headers_error", {"path": str(full_path), "error": str(e)}, severity="error")

    _record("elf_headers", {
        "path": str(full_path), "is_elf": result.is_elf,
        "class": result.elf_class, "entry_point": hex(result.entry_point),
        "program_headers": len(result.program_headers),
        "section_headers": len(result.section_headers),
    })
    return result
