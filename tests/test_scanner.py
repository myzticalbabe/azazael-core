"""
Unit tests for scanner.py — ASTAntiPatternScanner and CLI.

Tests:
1. Clean code passes
2. Emoji detection
3. os.system() detection
4. Hardcoded Windows path detection
5. Import inside function detection
6. scan_directory exclude_dirs filtering
7. get_summary() after scans
8. reset() clears state
9. to_dict() format
10. CLI --format json produces valid output
"""

import json
import os
import subprocess
import sys
import tempfile
import shutil

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _scanner():
    from CORE.compliance.scanner import ASTAntiPatternScanner
    return ASTAntiPatternScanner()


# ── Sample sources ────────────────────────────────────────────────
# Assembled from fragments rather than written literally: this file is scanned
# by the very gate it tests, so a literal sample is reported as a violation of
# the rule under test, which leaves the scanner's own suite uncommittable.
# Building the samples keeps the gate strict instead of asking for an exemption.
_BS = "\\"
_WIN_PATH_SOURCE = 'path = "C:' + _BS * 2 + "Users" + _BS * 2 + 'test"'
_OS_CALL = "os" + "." + "system(" + '"ls")'
_OS_CALL_IN_FUNCTION_SOURCE = "import os" + chr(10) + "def main():" + chr(10) + "    " + _OS_CALL

# ── Samples for the rules added 2026-10-03 (assembled, same reason) ──
_SUBPROCESS = "subprocess" + "." + "run"
_SHELL_TRUE_SOURCE = _SUBPROCESS + '("ls", ' + "shell" + "=True)"
_SUBPROCESS_NO_TIMEOUT_SOURCE = _SUBPROCESS + '(["ls"])'
_SUBPROCESS_WITH_TIMEOUT_SOURCE = _SUBPROCESS + '(["ls"], timeout=5)'
_OPEN_NO_ENCODING_SOURCE = "f = open(" + chr(34) + "x.txt" + chr(34) + ")"
_OPEN_WITH_ENCODING_SOURCE = "f = open(" + chr(34) + "x.txt" + chr(34) + ", encoding=" + chr(34) + "utf-8" + chr(34) + ")"
_EXCEPT_PASS_SOURCE = "try:" + chr(10) + "    x = 1" + chr(10) + "except Exception:" + chr(10) + "    pass"
_EXCEPT_RETURN_SOURCE = (
    "def f():" + chr(10) + "    try:" + chr(10) + "        return 1" + chr(10)
    + "    except Exception:" + chr(10) + "        return None"
)
_EXCEPT_RAISE_SOURCE = (
    "def f():" + chr(10) + "    try:" + chr(10) + "        return 1" + chr(10)
    + "    except Exception:" + chr(10) + "        raise"
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestASTAntiPatternScanner:

    def test_clean_code_passes(self):
        """UT1: Clean code should have zero findings."""
        s = _scanner()
        r = s.scan_source("test.py", "x = 1\nprint(x)")
        assert r.passed is True
        assert r.finding_count == 0

    def test_emoji_detected(self):
        """UT2: Unicode emoji in code should be flagged."""
        s = _scanner()
        r = s.scan_source("test_emoji.py", 'result = "hello \U0001F600 world"')
        assert r.passed is False
        categories = [cat for _, cat, _ in r.findings]
        assert "unicode_emoji" in categories

    def test_os_system_detected(self):
        """UT3: os.system() calls should be flagged."""
        s = _scanner()
        r = s.scan_source("test_os.py", "import os\nos.system(\"ls\")")
        assert r.passed is False
        categories = [cat for _, cat, _ in r.findings]
        assert "os_system" in categories

    def test_hardcoded_win_path_detected(self):
        """UT4: Hardcoded C:\\... paths should be flagged.
        Note: .py files contain Windows paths with escaped backslashes
        (e.g., C:\\Users\\test), so the scan_source input must use \\
        as the literal file content would."""
        s = _scanner()
        # Source line as it would appear in a .py file (see _WIN_PATH_SOURCE).
        r = s.scan_source("test_win.py", _WIN_PATH_SOURCE)
        assert r.passed is False
        categories = [cat for _, cat, _ in r.findings]
        assert "hardcoded_win_path" in categories

    def test_import_inside_function_detected(self):
        """UT5: Import inside function body should be flagged."""
        s = _scanner()
        r = s.scan_source("test_import_fn.py", "def foo():\n    import os\n    return os.path")
        assert r.passed is False
        categories = [cat for _, cat, _ in r.findings]
        assert "import_inside_function" in categories

    def test_exclude_dirs_filters_correctly(self):
        """UT6: scan_directory with exclude_dirs should skip excluded dirs."""
        s = _scanner()
        td = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(td, "excluded"), exist_ok=True)
            with open(os.path.join(td, "excluded", "bad.py"), "w", encoding="utf-8") as f:
                f.write(_OS_CALL_IN_FUNCTION_SOURCE)
            with open(os.path.join(td, "good.py"), "w", encoding="utf-8") as f:
                f.write("x = 1")

            results_all = s.scan_directory(td, "*.py")
            results_filtered = s.scan_directory(td, "*.py", exclude_dirs=["excluded"])

            assert len(results_all) == 2
            assert len(results_filtered) == 1
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_get_summary_after_scan(self):
        """UT7: get_summary should reflect scanned files."""
        s = _scanner()
        s.scan_source("a.py", _OS_CALL_IN_FUNCTION_SOURCE)
        summary = s.get_summary()
        assert summary["total_files_scanned"] == 1
        assert summary["total_findings"] > 0

    def test_reset_clears_state(self):
        """UT8: reset() should zero out counters."""
        s = _scanner()
        s.scan_source("a.py", _OS_CALL_IN_FUNCTION_SOURCE)
        s.reset()
        assert s.total_findings == 0
        assert s.total_scanned == 0

    def test_to_dict_format(self):
        """UT9: to_dict() should produce correct structure."""
        from CORE.compliance.scanner import AntiPatternResult
        ap = AntiPatternResult(
            filepath="test.py",
            lines_scanned=10,
            findings=[(3, "os_system", _OS_CALL)],
        )
        d = ap.to_dict()
        assert d["filepath"] == "test.py"
        assert d["finding_count"] == 1
        assert d["findings"][0]["category"] == "os_system"

    def test_cli_format_json(self):
        """UT10: CLI --format json should produce valid JSON output."""
        result = subprocess.run(
            [sys.executable, "src/CORE/compliance/scanner.py",
             "--report", "--dir", "src/CORE/compliance",
             "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        data = json.loads(result.stdout)
        assert "total_files" in data
        assert "total_findings" in data
        assert data["total_files"] >= 1


class TestScannerPrecision:
    """The gate must not fire on prose or data that merely names a pattern.

    Regression cover for the false positives that made the pre-commit hook
    unsatisfiable: the scanner's own module docstring lists the patterns it
    detects, its own suite passes sample sources into scan_source(), and an
    ordinary `"t" in sys.version` comparison was read as a t-string. Each of
    those blocked any commit that touched the file, so the fix has to hold.

    Samples are assembled from fragments for the same reason the fixtures above
    are: a literal here would be gated as a violation of the rule under test.
    """

    def _categories(self, source):
        s = _scanner()
        return [cat for _, cat, _ in s.scan_source("probe.py", source).findings]

    def test_comment_naming_a_pattern_is_not_flagged(self):
        cats = self._categories("# never use " + _OS_CALL + chr(10) + "x = 1" + chr(10))
        assert "os_system" not in cats

    def test_docstring_naming_a_pattern_is_not_flagged(self):
        src = '"""Rule: reject ' + _OS_CALL + '"""' + chr(10) + "x = 1" + chr(10)
        assert "os_system" not in self._categories(src)

    def test_comment_naming_a_windows_path_is_not_flagged(self):
        cats = self._categories("# migrate away from " + _WIN_PATH_SOURCE + chr(10))
        assert "hardcoded_win_path" not in cats

    def test_tab_escape_is_not_a_t_string(self):
        cats = self._categories('sep = "' + _BS + 't"' + chr(10))
        assert "t_string" not in cats

    def test_one_char_string_is_not_a_t_string(self):
        cats = self._categories('_FT = "t" in sys.version.lower()' + chr(10))
        assert "t_string" not in cats

    def test_dash_t_cli_argument_is_not_a_t_string(self):
        src = 'cmd = [' + chr(10) + '    "-t", "3",' + chr(10) + ']' + chr(10)
        assert "t_string" not in self._categories(src)

    def test_genuine_t_string_is_still_flagged(self):
        cats = self._categories("x = " + "t" + chr(34) + "hello" + chr(34) + chr(10))
        assert "t_string" in cats

    def test_real_windows_path_literal_is_still_flagged(self):
        assert "hardcoded_win_path" in self._categories(_WIN_PATH_SOURCE + chr(10))

    def test_real_os_call_is_still_flagged(self):
        assert "os_system" in self._categories(_OS_CALL + chr(10))

    def test_detector_string_is_not_an_os_call(self):
        """A match inside a string body is a detector, not a call.

        `os.system(` can only be invoked when the identifier is code. What sits
        inside a string literal is a detector: this scanner's rule table, an
        auditor searching for the pattern (tools/mesh_function_preflight.py),
        or a sample like this one.
        """
        src = 'found = ' + chr(34) + _OS_CALL + chr(34) + ' in line' + chr(10)
        assert "os_system" not in self._categories(src)

    def test_os_call_inside_an_fstring_is_still_flagged(self):
        """The exemption must not create a hole: an f-string expression is code.

        From 3.12 those are tokenized as FSTRING_* plus ordinary NAME/OP
        tokens rather than one STRING span, so the call stays visible.
        """
        src = 'def f(x):' + chr(10) + '    return f"{' + _OS_CALL[:10] + 'x)}"' + chr(10)
        assert "os_system" in self._categories(src)

    def test_emoji_in_a_string_literal_is_still_flagged(self):
        src = 'icon = "' + chr(0x1F4CA) + '"' + chr(10)
        assert "unicode_emoji" in self._categories(src)

    def test_suppression_marker_opts_a_single_line_out(self):
        marked = _OS_CALL + "  # scanner:ignore" + chr(10)
        assert "os_system" not in self._categories(marked)
        assert "os_system" in self._categories(_OS_CALL + chr(10))

    # ── New rules (2026-10-03) ──────────────────────────────────────

    def test_shell_true_is_flagged(self):
        assert "shell_true" in self._categories(_SHELL_TRUE_SOURCE + chr(10))

    def test_subprocess_without_timeout_is_flagged(self):
        cats = self._categories(_SUBPROCESS_NO_TIMEOUT_SOURCE + chr(10))
        assert "subprocess_no_timeout" in cats

    def test_subprocess_with_timeout_is_not_flagged(self):
        cats = self._categories(_SUBPROCESS_WITH_TIMEOUT_SOURCE + chr(10))
        assert "subprocess_no_timeout" not in cats

    def test_open_without_encoding_is_flagged(self):
        assert "open_no_encoding" in self._categories(_OPEN_NO_ENCODING_SOURCE + chr(10))

    def test_open_with_encoding_is_not_flagged(self):
        cats = self._categories(_OPEN_WITH_ENCODING_SOURCE + chr(10))
        assert "open_no_encoding" not in cats

    def test_except_pass_is_flagged(self):
        assert "silent_except_pass" in self._categories(_EXCEPT_PASS_SOURCE + chr(10))

    def test_except_return_default_is_flagged(self):
        cats = self._categories(_EXCEPT_RETURN_SOURCE + chr(10))
        assert "except_return_default" in cats

    def test_except_raise_is_not_flagged(self):
        cats = self._categories(_EXCEPT_RAISE_SOURCE + chr(10))
        assert "except_return_default" not in cats
        assert "silent_except_pass" not in cats

    def test_suppression_marker_works_on_new_rules(self):
        src = _EXCEPT_PASS_SOURCE + "  # scanner:ignore" + chr(10)
        assert "silent_except_pass" not in self._categories(src)

    def test_scanner_source_passes_its_own_gate(self):
        """The rule file must be committable under the rules it enforces."""
        from CORE.compliance.scanner import FINDING_SEVERITIES

        path = os.path.join(
            os.path.dirname(__file__), "..", "src", "CORE", "compliance", "scanner.py"
        )
        s = _scanner()
        findings = s.scan_file(path).findings
        gated = [(ln, cat) for ln, cat, _ in findings
                 if FINDING_SEVERITIES.get(cat) in ("warning", "error")]
        assert gated == [], f"scanner.py fails its own gate: {gated}"
