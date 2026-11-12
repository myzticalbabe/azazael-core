#!/usr/bin/env python3.15t
"""
Azazael HTML Audit Script — validates HTML files for common issues.

Checks:
  1. Valid HTML structure (doctype, html, head, body tags)
  2. No hardcoded Linux paths (/home/, ~/)
  3. No known JS syntax error patterns (broken querySelector, unescaped quotes)
  4. Relative path usage (no absolute file:///C:/ references in links)
  5. Balanced script/style tags

Usage:
  python3.15t scripts/audit_html.py [file1.html file2.html ...]
  python3.15t scripts/audit_html.py --all    (audits all project HTML files)

Exit codes:
  0 = all files pass
  1 = one or more files have issues
  2 = usage error

Created: June 4, 2026 — DevTools Re-Alignment Session
"""

import sys
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ─── Check Functions ─────────────────────────────────────────────

def check_html_structure(content: str, filepath: str) -> list[str]:
    """Verify basic HTML document structure."""
    issues = []
    lower = content.lower()
    if '<!doctype html>' not in lower and '<!doctype' not in lower:
        issues.append("Missing DOCTYPE declaration")
    if '<html' not in lower:
        issues.append("Missing <html> tag")
    if '<head>' not in lower and '<head ' not in lower:
        issues.append("Missing <head> tag")
    if '<body>' not in lower and '<body ' not in lower:
        issues.append("Missing <body> tag")
    return issues


def check_linux_paths(content: str, filepath: str) -> list[str]:
    """Detect hardcoded Linux paths that won't work on Windows."""
    issues = []
    for match in re.finditer(r'(/home/\w+|~/[\w./-]+)', content):
        path = match.group(0)
        # Skip if it's inside a code block documenting Linux paths
        line_start = content.rfind('\n', 0, match.start()) + 1
        line = content[line_start:content.find('\n', match.end())]
        if '<code>' in line or '`' in line:
            continue  # documented path, not active
        issues.append(f"Linux path found: {path} (line ~{content[:match.start()].count(chr(10))+1})")
    return issues


def _strip_js_code(code: str) -> str:
    """Strip string literals, template literals, comments, and regex literals
    from a JS block so the paren/brace balance checks only count structural code.

    Otherwise literal content (HTML in strings, markdown in template renders,
    `desc` fields with parentheses) produces false "unbalanced" flags.
    Note: this is a heuristic; `node --check` on the extracted block is ground truth.
    """
    out = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        nxt = code[i + 1] if i + 1 < n else ''
        # line comment
        if c == '/' and nxt == '/':
            j = code.find('\n', i)
            i = n if j == -1 else j
            continue
        # block comment
        if c == '/' and nxt == '*':
            j = code.find('*/', i + 2)
            i = n if j == -1 else j + 2
            continue
        # string / template literal
        if c in ('"', "'", '`'):
            quote = c
            j = i + 1
            while j < n:
                if code[j] == '\\':
                    j += 2
                    continue
                if code[j] == quote:
                    break
                j += 1
            out.append(' ')
            i = n if j >= n else j + 1
            continue
        # regex literal (division-vs-regex heuristic)
        if c == '/':
            k = i - 1
            while k >= 0 and code[k] in ' \t\r\n':
                k -= 1
            prev = code[k] if k >= 0 else ''
            if prev == '' or prev in '([{=,:;!&|?+-*%^~<>':
                j = i + 1
                in_class = False
                while j < n:
                    if code[j] == '\\':
                        j += 2
                        continue
                    if code[j] == '[':
                        in_class = True
                    elif code[j] == ']':
                        in_class = False
                    elif code[j] == '/' and not in_class:
                        break
                    j += 1
                k = j + 1  # skip regex flags (dgimsuvy)
                while k < n and code[k] in 'dgimsuvy':
                    k += 1
                out.append(' ')
                i = k
                continue
        out.append(c)
        i += 1
    return ''.join(out)


def check_js_syntax(content: str, filepath: str) -> list[str]:
    """Detect known JavaScript syntax error patterns."""
    issues = []
    # Pattern 1: Broken querySelector with nested escaped quotes
    if re.search(r"querySelector\s*\(\s*['\"]\[onclick.*\\\\", content):
        issues.append("Broken querySelector with nested backslash escapes — use getAttribute('onclick') instead")
    # Pattern 2: Unbalanced parentheses/braces in inline scripts (literal-aware)
    script_blocks = re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL | re.IGNORECASE)
    for i, block in enumerate(script_blocks):
        code = _strip_js_code(block)
        open_parens = code.count('(')
        close_parens = code.count(')')
        if open_parens != close_parens:
            issues.append(f"Script block #{i+1}: unbalanced parentheses ({open_parens} open vs {close_parens} close)")
        open_braces = code.count('{')
        close_braces = code.count('}')
        if open_braces != close_braces:
            issues.append(f"Script block #{i+1}: unbalanced braces ({open_braces} open vs {close_braces} close)")
    return issues


def check_absolute_paths(content: str, filepath: str) -> list[str]:
    """Detect absolute file:// paths that should be relative."""
    issues = []
    for match in re.finditer(r'(href|src)\s*=\s*["\']file:///[A-Za-z]:', content):
        issues.append(f"Absolute file:// path in {match.group(1)} attribute — use relative path instead")
    return issues


def check_balanced_tags(content: str, filepath: str) -> list[str]:
    """Check for unbalanced script and style tags."""
    issues = []
    open_script = len(re.findall(r'<script[^>]*>', content, re.IGNORECASE))
    close_script = len(re.findall(r'</script>', content, re.IGNORECASE))
    if open_script != close_script:
        issues.append(f"Unbalanced <script> tags: {open_script} open vs {close_script} close")
    open_style = len(re.findall(r'<style[^>]*>', content, re.IGNORECASE))
    close_style = len(re.findall(r'</style>', content, re.IGNORECASE))
    if open_style != close_style:
        issues.append(f"Unbalanced <style> tags: {open_style} open vs {close_style} close")
    return issues


def check_viewport_meta(content: str, filepath: str) -> list[str]:
    """Check for mobile viewport meta tag."""
    issues = []
    if '<meta name="viewport"' not in content.lower():
        issues.append("Missing <meta name=\"viewport\"> — mobile rendering may be broken")
    return issues


# ─── Main Audit Function ─────────────────────────────────────────

ALL_CHECKS = [
    ("HTML structure", check_html_structure),
    ("Linux paths", check_linux_paths),
    ("JS syntax", check_js_syntax),
    ("Absolute paths", check_absolute_paths),
    ("Balanced tags", check_balanced_tags),
    ("Viewport meta", check_viewport_meta),
]


def audit_file(filepath: str) -> tuple[bool, list[str]]:
    """Audit a single HTML file. Returns (passed, issues)."""
    path = Path(filepath)
    if not path.exists():
        return False, [f"File not found: {filepath}"]
    if path.suffix.lower() not in ('.html', '.htm'):
        return True, []  # skip non-HTML files silently

    try:
        content = path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        return False, [f"Failed to read file: {e}"]

    all_issues = []
    for check_name, check_fn in ALL_CHECKS:
        issues = check_fn(content, filepath)
        for issue in issues:
            all_issues.append(f"[{check_name}] {issue}")

    return len(all_issues) == 0, all_issues


def find_all_html_files(root: Path) -> list[str]:
    """Find all HTML files in the project (excluding .git, node_modules)."""
    html_files = []
    for path in root.rglob("*.html"):
        rel = str(path)
        if '.git' in rel or 'node_modules' in rel or '__pycache__' in rel:
            continue
        html_files.append(str(path))
    return sorted(html_files)


# ─── CLI ─────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    if sys.argv[1] == '--all':
        files = find_all_html_files(PROJECT_ROOT)
        if not files:
            print("No HTML files found in project.")
            sys.exit(0)
        print(f"Auditing {len(files)} HTML files...\n")
    else:
        files = sys.argv[1:]

    total_issues = 0
    failed_files = 0
    passed_files = 0

    for filepath in files:
        passed, issues = audit_file(filepath)
        if passed:
            passed_files += 1
            print(f"  PASS  {filepath}")
        else:
            failed_files += 1
            total_issues += len(issues)
            print(f"  FAIL  {filepath}")
            for issue in issues:
                print(f"        {issue}")

    print(f"\n{'='*60}")
    print(f"Results: {passed_files} passed, {failed_files} failed, {total_issues} issues")
    print(f"{'='*60}")

    if failed_files > 0:
        print("\nFix the issues above before committing.")
        print("For browser-based audits, run browser-use agents manually.")
        sys.exit(1)
    else:
        print("All HTML files pass structural audit.")
        sys.exit(0)


if __name__ == '__main__':
    main()
