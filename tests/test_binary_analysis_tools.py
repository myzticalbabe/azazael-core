# coding: utf-8
"""Unit tests for BINARY_ANALYSIS_TOOLS.py — text processing and utilities."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from CORE.WORMS.BINARY_ANALYSIS_TOOLS import (
    run_sed, run_awk, extract_strings, generate_hexdump,
    disassemble_binary, analyze_entropy,
)
from CORE.WORMS.BINARY_ANALYSIS_TYPES import EntropyResult


# ═══════════════════════════════════════════════════════════════════
# sed Tests
# ═══════════════════════════════════════════════════════════════════


class TestRunSed:
    """Verify run_sed stream editor."""

    def test_regex_replace(self):
        """Basic regex replace should work."""
        result = run_sed(r"hello", "world", text="hello there")
        assert result == "world there"

    def test_global_replace(self):
        """Global flag should replace all occurrences."""
        result = run_sed(r"a", "X", text="banana", global_replace=True)
        assert result == "bXnXnX"

    def test_single_replace(self):
        """Single replace should replace only first occurrence."""
        result = run_sed(r"a", "X", text="banana", global_replace=False)
        assert result == "bXnana"

    def test_case_insensitive(self):
        """Case-insensitive flag should work."""
        result = run_sed(r"hello", "world", text="Hello there", case_insensitive=True)
        assert result == "world there"

    def test_case_sensitive_default(self):
        """Default should be case-sensitive."""
        result = run_sed(r"hello", "world", text="Hello there")
        assert result == "Hello there"  # No match (case mismatch)

    def test_literal_replace(self):
        """Literal (non-regex) replace should work."""
        result = run_sed(r"hello", "world", text=".hello.", regex=False)
        assert result == ".world."

    def test_literal_replace_special_chars(self):
        """Literal replace should handle regex special chars."""
        result = run_sed(r"a.b", "X", text="a.b[a.b]", regex=False)
        assert result == "X[X]"  # dots treated literally

    def test_empty_text(self):
        """Empty text should return empty string."""
        result = run_sed(r"x", "y", text="")
        assert result == ""

    def test_no_match(self):
        """No match should return original text."""
        result = run_sed(r"xyz", "abc", text="hello world")
        assert result == "hello world"

    def test_nonexistent_file(self):
        """Non-existent file should return error message."""
        result = run_sed(r"x", "y", input_path="__nonexistent__")
        assert "Error" in result


# ═══════════════════════════════════════════════════════════════════
# awk Tests
# ═══════════════════════════════════════════════════════════════════


class TestRunAwk:
    """Verify run_awk text processor."""

    def test_column_extraction(self):
        """Extract specific columns by index (1-indexed like awk)."""
        result = run_awk(text="a b c\nd e f", output_fields=[1, 3])
        assert result == "a\tc\nd\tf"

    def test_all_fields_default(self):
        """Default (no output_fields) should print all fields."""
        result = run_awk(text="a b c")
        assert result == "a\tb\tc"

    def test_filter_pattern(self):
        """Filter lines matching a pattern."""
        result = run_awk(text="apple\nbanana\napricot", filter_pattern=r"^ap")
        assert result == "apple\napricot"

    def test_custom_separator(self):
        """Custom field separator should work."""
        result = run_awk(text="a,b,c", field_separator=",", output_fields=[2])
        assert result == "b"

    def test_custom_output_separator(self):
        """Custom output separator should work."""
        result = run_awk(text="a b c", output_fields=[1, 3], output_separator="|")
        assert result == "a|c"

    def test_negative_indexing(self):
        """Negative indices should work like Python negative indexing."""
        result = run_awk(text="a b c d", output_fields=[1, -1])
        assert result == "a\td"

    def test_out_of_range_index(self):
        """Out-of-range index should return empty string."""
        result = run_awk(text="a b", output_fields=[5])
        assert result == ""

    def test_empty_text(self):
        """Empty text should return empty string."""
        result = run_awk(text="")
        assert result == ""

    def test_nonexistent_file(self):
        """Non-existent file should return error."""
        result = run_awk(input_path="__nonexistent__")
        assert "Error" in result


# ═══════════════════════════════════════════════════════════════════
# Strings Tests
# ═══════════════════════════════════════════════════════════════════


class TestExtractStrings:
    """Verify extract_strings."""

    def test_nonexistent_file(self):
        """Non-existent file should return empty list."""
        strings = extract_strings("__nonexistent__")
        assert strings == []

    def test_python_file_has_strings(self):
        """This Python file should have printable strings."""
        strings = extract_strings(__file__, min_length=4, max_strings=50)
        assert len(strings) > 0
        assert any("def " in s for s in strings)


# ═══════════════════════════════════════════════════════════════════
# hexdump Tests
# ═══════════════════════════════════════════════════════════════════


class TestGenerateHexdump:
    """Verify generate_hexdump."""

    def test_nonexistent_file(self):
        """Non-existent file should return 'File not found'."""
        result = generate_hexdump("__nonexistent__")
        assert "not found" in result

    def test_python_file_dump(self):
        """Python file should produce a hex dump."""
        result = generate_hexdump(__file__, length=64)
        assert len(result) > 0
        # Hex dump lines have format: XXXXXXXX  XX XX XX ... |...|
        assert "|" in result


# ═══════════════════════════════════════════════════════════════════
# Entropy Tests
# ═══════════════════════════════════════════════════════════════════


class TestAnalyzeEntropy:
    """Verify analyze_entropy."""

    def test_nonexistent_file(self):
        """Non-existent file should return empty result."""
        result = analyze_entropy("__nonexistent__")
        assert isinstance(result, EntropyResult)
        assert result.overall_entropy == 0.0
        assert result.is_packed is False

    def test_python_file_entropy(self):
        """Python file should have some entropy."""
        result = analyze_entropy(__file__)
        assert isinstance(result, EntropyResult)
        assert result.overall_entropy > 0.0
        assert hasattr(result, "section_entropy")

    def test_result_structure(self):
        """Result should have expected fields."""
        result = analyze_entropy(__file__)
        assert hasattr(result, "path")
        assert hasattr(result, "overall_entropy")
        assert hasattr(result, "max_possible")
        assert hasattr(result, "is_packed")
        assert hasattr(result, "section_entropy")
        assert hasattr(result, "histogram")
        assert result.max_possible == 8.0


# ═══════════════════════════════════════════════════════════════════
# Disassembly Tests
# ═══════════════════════════════════════════════════════════════════


class TestDisassembleBinary:
    """Verify disassemble_binary."""

    def test_nonexistent_file(self):
        """Non-existent file should return error dict."""
        result = disassemble_binary("__nonexistent__")
        assert "error" in result or "not found" in str(result)

    def test_result_structure(self):
        """Result should have expected fields even for nonexistent file."""
        result = disassemble_binary("__nonexistent__")
        # Already checked error above, also check structure
        if "path" in result:
            assert result["path"] == "__nonexistent__"


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
