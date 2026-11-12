"""Tests for scripts/audit_html.py — JS literal-aware balance checks.

Regression coverage for the _strip_js_code() fix: paren/brace balance must
ignore content inside strings, template literals, comments, and regex
literals, while still flagging genuine structural imbalances.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from audit_html import check_js_syntax


class TestJsBalanceChecks:
    def test_genuine_missing_close_paren_flags(self):
        issues = check_js_syntax("<script>function f(x){ return (x; }</script>", "t.html")
        assert any("unbalanced parentheses" in i for i in issues)

    def test_genuine_missing_close_brace_flags(self):
        issues = check_js_syntax("<script>if (a) { b(); </script>", "t.html")
        assert any("unbalanced braces" in i for i in issues)

    def test_parens_only_in_single_quotes_do_not_flag(self):
        issues = check_js_syntax("<script>var s = 'a(b)c'; var t = \"x(y)z\";</script>", "t.html")
        assert issues == []

    def test_braces_in_strings_and_templates_do_not_flag(self):
        issues = check_js_syntax("<script>var s = 'a{b}c'; var t = `x{y}z`;</script>", "t.html")
        assert issues == []

    def test_parens_in_regex_literal_do_not_flag(self):
        issues = check_js_syntax("<script>var r = /\\(\\d{2}\\)/.test('(12)');</script>", "t.html")
        assert issues == []

    def test_balanced_html_string_with_comment_does_not_flag(self):
        issues = check_js_syntax(
            "<script>/* note: (unbalanced */ el.innerHTML = '<div>(x)</div>';</script>", "t.html")
        assert issues == []

    def test_template_interpolation_with_code_does_not_flag(self):
        issues = check_js_syntax("<script>var n = `${1 + (2)}`;</script>", "t.html")
        assert issues == []

    def test_real_imbalance_near_strings_still_flags(self):
        issues = check_js_syntax("<script>var s='(ok)'; if(x) { return; </script>", "t.html")
        assert any("unbalanced braces" in i for i in issues)

    def test_regression_disclosure_render_style_string(self):
        # Large string literal (markdown render) with unbalanced parens inside.
        html = "<script>document.getElementById('c').innerHTML = marked.parse(\"a(b(c) d] e{f,g\");</script>"
        issues = check_js_syntax(html, "t.html")
        assert issues == []

    def test_regression_qselector_style_desc_fields(self):
        # desc strings with parens like '(Pershing/BNY?)' must not flag.
        html = ("<script>const R = [{ id:'ima', desc:'Name of manager (Pershing/BNY?), fee (FY2025 $446K)' },"
                "{ id:'schedj', desc:'(IRC \\u00a74958 rebuttable presumption)' }];</script>")
        issues = check_js_syntax(html, "t.html")
        assert issues == []