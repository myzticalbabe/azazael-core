# coding: utf-8
"""Unit tests for BINARY_ANALYSIS_PARSE.py — PE/ELF parsing helpers."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from pathlib import Path
from CORE.WORMS.BINARY_ANALYSIS_PARSE import (
    parse_pe_headers, parse_elf_headers, extract_symbols,
    is_pe_file, is_elf_file,
)
from CORE.WORMS.BINARY_ANALYSIS_TYPES import (
    SymbolEntry, SectionHeader, PeHeaders, ElfHeaders,
)


class TestBinaryTypeDetection:
    """Verify binary type detection functions."""

    def test_is_pe_file_nonexistent(self):
        """Non-existent file should return False."""
        assert is_pe_file(Path("nonexistent.exe")) is False

    def test_is_elf_file_nonexistent(self):
        """Non-existent file should return False."""
        assert is_elf_file(Path("nonexistent.elf")) is False

    def test_is_pe_file_txt(self):
        """.py file should not be detected as PE."""
        assert is_pe_file(Path(__file__)) is False

    def test_is_elf_file_txt(self):
        """.py file should not be detected as ELF."""
        assert is_elf_file(Path(__file__)) is False


class TestParsePeHeaders:
    """Verify PE header parsing."""

    def test_nonexistent_file(self):
        """Non-existent file should return empty result."""
        result = parse_pe_headers("nonexistent.exe")
        assert isinstance(result, PeHeaders)
        assert result.is_pe is False
        assert result.path == "nonexistent.exe"

    def test_not_pe_file(self):
        """Text file should return not-a-PE result."""
        result = parse_pe_headers(__file__)
        assert isinstance(result, PeHeaders)
        assert result.is_pe is False

    def test_result_structure(self):
        """Result should have expected fields even when not PE."""
        result = parse_pe_headers(__file__)
        assert hasattr(result, "machine")
        assert hasattr(result, "sections")
        assert hasattr(result, "imports")
        assert hasattr(result, "exports")
        assert hasattr(result, "subsystem")
        assert hasattr(result, "characteristics")
        assert result.sections == []
        assert result.imports == []


class TestParseElfHeaders:
    """Verify ELF header parsing."""

    def test_nonexistent_file(self):
        """Non-existent file should return empty result."""
        result = parse_elf_headers("nonexistent.elf")
        assert isinstance(result, ElfHeaders)
        assert result.is_elf is False

    def test_not_elf_file(self):
        """Text file should return not-an-ELF result."""
        result = parse_elf_headers(__file__)
        assert isinstance(result, ElfHeaders)
        assert result.is_elf is False

    def test_result_structure(self):
        """Result should have expected fields even when not ELF."""
        result = parse_elf_headers(__file__)
        assert hasattr(result, "elf_class")
        assert hasattr(result, "endianness")
        assert hasattr(result, "entry_point")
        assert hasattr(result, "program_headers")
        assert hasattr(result, "section_headers")
        assert result.program_headers == []
        assert result.section_headers == []


class TestExtractSymbols:
    """Verify symbol extraction."""

    def test_nonexistent_file(self):
        """Non-existent file should return empty list."""
        symbols = extract_symbols("nonexistent.exe")
        assert symbols == []

    def test_not_a_binary(self):
        """Text file should return empty list (not PE/ELF)."""
        symbols = extract_symbols(__file__)
        assert isinstance(symbols, list)
        # May get external nm results depending on platform, but never crash

    def test_result_type(self):
        """Result should be list of SymbolEntry or empty."""
        symbols = extract_symbols(__file__)
        if symbols:
            assert isinstance(symbols[0], SymbolEntry)
            assert hasattr(symbols[0], "address")
            assert hasattr(symbols[0], "name")
            assert hasattr(symbols[0], "symbol_type")
            assert hasattr(symbols[0], "section")


class TestSymbolEntry:
    """Verify SymbolEntry dataclass."""

    def test_minimal_entry(self):
        """Minimal symbol entry should have defaults."""
        entry = SymbolEntry(address=0x401000, name="main", symbol_type="T", section=".text")
        assert entry.address == 0x401000
        assert entry.name == "main"
        assert entry.symbol_type == "T"
        assert entry.section == ".text"
        assert entry.size == 0

    def test_is_exported_exported_types(self):
        """Types T, D, B, R should be exported."""
        for t in ("T", "t", "D", "d", "B", "b", "R", "r"):
            entry = SymbolEntry(address=0, name="sym", symbol_type=t, section="sec")
            assert entry.is_exported, f"Type {t} should be exported"

    def test_is_exported_undefined(self):
        """Type U should not be exported."""
        entry = SymbolEntry(address=0, name="sym", symbol_type="U", section="IMPORT")
        assert entry.is_exported is False, "Type U should not be exported"


class TestSectionHeader:
    """Verify SectionHeader dataclass."""

    def test_minimal_section(self):
        """Minimal section should have defaults."""
        sec = SectionHeader(name=".text", virtual_address=0x1000, virtual_size=1024,
                            raw_size=512, raw_offset=0x200)
        assert sec.name == ".text"
        assert sec.characteristics == []
        assert sec.entropy == 0.0


if __name__ == "__main__":
    p = f = 0
    for name, obj in list(globals().items()):
        if isinstance(obj, type) and name.startswith("Test"):
            inst = obj()
            for mn in dir(inst):
                if mn.startswith("test_"):
                    try:
                        getattr(inst, mn)()
                        print(f"  [OK] {name}.{mn}")
                        p += 1
                    except Exception as e:
                        print(f"  [FAIL] {name}.{mn}: {e}")
                        f += 1
    print(f"  Passed: {p}, Failed: {f}")
    sys.exit(0 if f == 0 else 1)
