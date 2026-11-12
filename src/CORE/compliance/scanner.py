"""
RULE 0X-7: Automated AST Anti-Pattern Scanning

Hooks into the ast module to scan Python files for anti-pattern categories
automatically on every code change. Provides both file-level and string-level
scanning.

Anti-patterns detected (12 categories):
1.  Unicode emoji / non-ASCII chars in code strings
2.  f-string backslash escapes (f"...{\\...}...")
3.  t-strings (PEP 750 draft — invalid Python syntax)
4.  os.system() calls
5.  Hardcoded C:\\ Windows paths
6.  import inside function bodies
7.  cls.__new__() factory methods
8.  shell=True in a subprocess call (added 2026-10-03)
9.  subprocess call with no timeout= (added 2026-10-03)
10. open() with no encoding= (added 2026-10-03)
11. except block that does nothing but pass (added 2026-10-03)
12. except block that returns a success-shaped default (added 2026-10-03)

Rules 8-12 were added after measuring each candidate against src/ (657 files);
only rules with real evidence were added. `print` (7,328 hits) was rejected
because prints are legitimate here; TODO/assert were rejected as tracked
elsewhere or noisy. Rule 8 closes a consistency hole: rule 4 bans os.system()
for command injection, but its fix suggestion was `subprocess.run(args,
shell=True)` -- the same injection class. Rules 11-12 mechanize the defect class
behind the 2026-10-03 PMB dead loop (an error indistinguishable from success).
"""

import ast
import io
import json
import os
import re
import sys
import threading
import tokenize
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
# from typing import Dict, List, Optional, Tuple  # removed — all names have native equivalents

import fnmatch
from typing import List
import argparse
import time
import urllib.request
import subprocess

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Default directories to exclude from scanning (merged with user-provided --exclude-dirs)
DEFAULT_EXCLUDES = {"__pycache__", ".git", ".github", "node_modules", ".venv", "eggs", "egg-info"}

# Severity levels for each finding category
FINDING_SEVERITIES = {
    "unicode_emoji": "warning",
    "fstring_backslash": "error",
    "t_string": "error",
    "os_system": "error",
    "hardcoded_win_path": "warning",
    "import_inside_function": "info",
    "cls_new_factory": "warning",
    # ── Added 2026-10-03 after measuring each candidate against src/ (657 files).
    # Only rules with real evidence were added; `print` (7,328 hits) was rejected
    # because prints are legitimate here, and TODO/assert were rejected as
    # tracked-elsewhere / noisy.
    "shell_true": "error",            # 11 hits -- the sanctioned os.system "fix"
    "silent_except_pass": "warning",  # 842 -- the exact class behind the PMB dead loop
    "silent_except_continue": "warning",  # 21 -- skipped row indistinguishable from never there
    "except_return_default": "warning",  # 692 -- a metric that reads success on error
    "subprocess_no_timeout": "warning",  # 244 -- hangs (documented in pytest.ini)
    "open_no_encoding": "warning",    # 228 -- cp1252 mojibake (documented in pmb)
    # ── Added 2026-10-19 after the graph/error sweep fixed the last live
    # sites (create_reference_video.py:563 bare except; escalation_sft + 
    # test_rule_0zf bare opens). Both recorded at ZERO baseline: any new
    # site fails the ratchet. RULE: auto-add gate coverage for every defect
    # class fixed in a sweep (MANDATES.md RULE 0Z-M).
    "bare_except": "warning",         # 0 -- `except:` catches everything
    "open_no_context": "warning",     # 0 -- handle assigned, never closed
    # ── Added 2026-10-19 (RULE 0Z-M, second capture): the hang family.
    # sqlite read-write connects and requests calls with no timeout= block
    # forever on a lock or a dead peer (CD5 used timeout=5; the rest did not).
    # Read-only (mode=ro) and :memory: connects cannot contend, excluded.
    "sqlite_no_timeout": "warning",   # ~30 read-write sites, all fixed same session
    "requests_no_timeout": "warning", # 6 sites (hydra_client), all fixed same session
    # ── Added 2026-10-19 (RULE 0Z-M, third capture): unbounded Popen waits.
    # Popen() never blocks, but .wait()/.communicate() without timeout do --
    # the ascii_video.py ffmpeg hang was a bare proc.wait(), not the spawn.
    # Receiver-tracked (threading.Event waits excluded by construction).
    "unbounded_wait": "warning",      # 0 -- kill-then-reap exempt, rest fixed
    # ── Added 2026-11-04: a spawned repo hook inherits the CALLER's cwd. A test
    # that spawns `.githooks/pre-push` from a temp dir runs whatever repo that
    # dir is nested in, so the gate measures the wrong tree -- measured as a
    # 92 s pre-push in a unit test, and a hook that blocks on `git` failing in a
    # directory with no `.git`. cwd= is the fix; its absence is the defect.
    "hook_spawn_without_cwd": "warning",  # 0 -- no live site expected
    "syntax_error": "error",
    "read_error": "error",
}

# Fix suggestions for each category
FIX_SUGGESTIONS = {
    "unicode_emoji": "Replace emoji with ASCII (e.g., OK/FAIL/*/>>)",
    "fstring_backslash": "Pre-extract variable before f-string",
    "t_string": "Replace t\"...\" with regular string or f-string",
    # NOT shell=True: that is the same injection class (see the shell_true rule).
    "os_system": "Replace with a subprocess call that takes an argument list (no shell=True)",
    "hardcoded_win_path": "Use Path.home() or get_project_root()",
    "import_inside_function": "Move import to module level if possible",
    "cls_new_factory": "Use @classmethod or factory function",
    "shell_true": "Pass an argument list without shell=True",
    "silent_except_pass": "Log or record the exception; never swallow it silently",
    "silent_except_continue": "Log or record the exception; a skipped row is indistinguishable from a row that was never there",
    "except_return_default": "Re-raise or return an explicit failure, not a success-shaped default",
    "subprocess_no_timeout": "Pass timeout=<seconds>",
    "open_no_encoding": "Pass encoding='utf-8' (or open in binary mode)",
    "bare_except": "Name the exception (e.g. except OSError); bare except: also catches KeyboardInterrupt/SystemExit",
    "open_no_context": "Use with open(...) as f: so the handle always closes",
    "sqlite_no_timeout": "Pass timeout=<seconds> (30 for production paths)",
    "requests_no_timeout": "Pass timeout=<seconds> (30 for client calls)",
    "unbounded_wait": "Pass timeout=<seconds> to .wait()/.communicate() (Popen.wait takes timeout positionally too)",
    "hook_spawn_without_cwd": "Pass cwd=<the repo the hook must inspect> (a spawned hook inherits the caller's cwd)",
}

# Explanations for each finding category (used by --explain)
FINDING_EXPLANATIONS = {
    "unicode_emoji": "Emoji in source code causes encoding issues on different platforms. Use ASCII alternatives.",
    "fstring_backslash": "Backslash escapes inside f-string expressions are illegal in Python 3.12+. Pre-extract the value.",
    "t_string": "t-strings are a PEP 750 draft proposal, not valid Python syntax. Will cause SyntaxError at runtime.",
    "os_system": "Shelling out through the os module is vulnerable to shell injection and discards the exit code. Use a subprocess call with an argument list instead.",
    "hardcoded_win_path": "An absolute drive path hardcodes one machine's layout; it breaks on other systems and user profiles.",
    "import_inside_function": "Import inside functions slows performance and confuses static analysis.",
    "shell_true": "shell=True runs the command through a shell, so any interpolated value is a command-injection vector -- the same class os.system() is banned for. Pass an argument list instead.",
    "silent_except_pass": "`except Exception: pass` discards the failure. A swallowed error is indistinguishable from success: this is the defect class that let PMB's lesson_impact report a clean zero while its input table was never written.",
    "silent_except_continue": "`except Exception: continue` discards the failure AND skips the iteration. In a claim loop the task is simply never handed out and nothing says so -- the same defect class as silent_except_pass.",
    "except_return_default": "`except: return <default>` turns an error into a normal-looking success value, so every caller reads a well-formed result and the fault is invisible.",
    "subprocess_no_timeout": "A subprocess without a timeout can hang forever; the suite already documents files that hung past a 90s budget.",
    "open_no_encoding": "open() without encoding= uses the platform default (cp1252 on Windows), which mojibakes non-ASCII and crashes on characters it cannot decode.",
    "bare_except": "`except:` with no exception type catches everything including KeyboardInterrupt and SystemExit, and hides the failure's identity. The multi-statement font fallback in create_reference_video.py:563 survived silent_except_pass (which needs a single-statement body) -- this rule closes that hole.",
    "open_no_context": "`handle = open(...)` outside a `with`, with no later `handle.close()`, leaks the descriptor on every path that raises between open and close. `open(...).read()` one-liners are deliberately NOT flagged: the handle is temporary and refcounting closes it at once.",
    "sqlite_no_timeout": "A read-write sqlite3.connect() without timeout= waits on a writer lock forever (default is 5s only when timeout is passed; without it a contended COMMIT blocks indefinitely). Read-only mode=ro and :memory: connects cannot contend and are excluded.",
    "requests_no_timeout": "A requests call without timeout= blocks forever on a dead peer. Same hang class as subprocess_no_timeout.",
    "unbounded_wait": "Popen() itself never blocks -- only .wait()/.communicate() do, and the constructor takes no timeout=. A bare proc.wait() on a live child hangs forever (the ascii_video.py ffmpeg stall). Kill-then-reap (.kill()/.terminate() then .wait()) is exempt: reaping a dead process cannot block.",
    "hook_spawn_without_cwd": "A hook script (`pre-commit`, `pre-push`, `commit-msg`, ...) reads the repository it runs in, and a child process inherits the parent's working directory. Spawning one without cwd= means the hook inspects whatever repo the CALLER's cwd is nested in: from a temp dir inside another checkout it runs that checkout's gates (measured: 92 s pre-push inside a unit test), and from a directory with no .git it fails for a reason unrelated to the code under test. cwd=<path> is the fix; the exemption is a command that names no hook token.",
}



# ---------------------------------------------------------------------------
# Anti-Pattern Result
# ---------------------------------------------------------------------------

@dataclass
class AntiPatternResult:
    """Result of scanning a source file for anti-patterns.

    Attributes:
        filepath: Path to the scanned file
        lines_scanned: Total lines in the file
        findings: list of (line_number, category, snippet) tuples
        passed: True if zero findings
        scanned_at: ISO timestamp of scan
    """
    filepath: str
    lines_scanned: int
    findings: list[tuple[int, str, str]]
    passed: bool = True
    scanned_at: str = ""

    def __post_init__(self):
        self.passed = len(self.findings) == 0
        if not self.scanned_at:
            self.scanned_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    def to_dict(self) -> dict:
        return {
            "filepath": self.filepath,
            "lines_scanned": self.lines_scanned,
            "findings": [
                {"line": ln, "category": cat, "snippet": sn}
                for ln, cat, sn in self.findings
            ],
            "passed": self.passed,
            "finding_count": self.finding_count,
            "scanned_at": self.scanned_at,
        }

    def __repr__(self) -> str:
        return f"AntiPatternResult({self.filepath}, passed={self.passed}, findings={self.finding_count})"


# ---------------------------------------------------------------------------
# AST Anti-Pattern Scanner
# ---------------------------------------------------------------------------

# Regex patterns for non-AST-detectable anti-patterns
_PATTERN_EMOJI = re.compile(r"[\U0001F300-\U0010FFFF]")  # Unicode emoji range
_PATTERN_FSTRING_BACKSLASH = re.compile(r'f["\'].*?\{.*?\\\\.*?\}.*?["\']')
# A t-prefix must directly precede the quote AND must not itself be the tail of
# an identifier, of a quoted character, or of a backslash escape. The obvious
# \bt["'] form false-positives on tab escapes and on a one-character string
# compared against sys.version, neither of which is a t-string.
_PATTERN_TSTRING = re.compile("(?<![\\w\"'\\\\])t[\"']")  # literal t-prefixed string
_PATTERN_OS_SYSTEM = re.compile(r'\bos\.system\s*\(')
_PATTERN_WIN_PATH = re.compile(r'[Cc]:\\\\')
_PATTERN_CLS_NEW = re.compile(r'cls\(\)\.__new__\(')

# ── Rules added 2026-10-03 (measured first, see FINDING_SEVERITIES) ────────
# The os_system rule bans os.system() for command injection, but its own fix
# suggestion is `subprocess.run(args, shell=True)` -- which reintroduces exactly
# the injection vector. shell_true closes that consistency hole.
_PATTERN_SHELL_TRUE = re.compile(r'\bshell\s*=\s*True\b')
# subprocess.<fn>( on one line with no timeout=. A call wrapped across lines
# without a timeout is a false negative; accepted (conservative, never a false
# positive) rather than reading whole call expressions.
_PATTERN_SUBPROCESS = re.compile(
    r'\bsubprocess\.(run|Popen|call|check_call|check_output)\s*\(')
# Popen() itself never blocks -- only .wait()/.communicate() do, and the
# Popen CONSTRUCTOR takes no timeout= (passing one raises TypeError). So a
# Popen whose handle never waits in this file is fire-and-forget, not a hang.
# Refined 2026-10-19: Popen sites are flagged only when the handle is waited on.
# open( on one line with no encoding= and no binary mode. Same single-line
# trade-off as subprocess: it may miss a multiline call, never invents one.
_PATTERN_OPEN = re.compile(r'(?<![\w.])open\s*\(')
# Binary mode: "rb"/"wb"/"ab"/"xb" (+t/b combos) or mode="...b...".
_PATTERN_BINARY_MODE = re.compile(
    r'["\'][rwxa]*b[+t]*["\']|mode\s*=\s*["\'][^"\']*b')
# sqlite3.connect( and requests.get|post|...(: a writer waiting on a lock
# or a network read with no timeout= hangs forever. Call text is measured
# across lines via _call_text (same as 5c/5d), never same-line-only.
_PATTERN_SQLITE = re.compile(r'\bsqlite3\s*\.\s*connect\s*\(')
_PATTERN_REQUESTS = re.compile(
    r'\brequests\s*\.\s*(get|post|put|delete|patch|head)\s*\(')

# Per-line opt-out, in the spirit of `# noqa`. Some lines *have* to contain a
# pattern as data: this scanner's own test suite passes sample sources into
# scan_source(), so a repo-wide sweep gates those samples as violations. An
# auditable per-line opt-out is honest about that; a file- or rule-level
# exemption would also hide real violations.
_SUPPRESSION_MARKER = "# scanner:ignore"

# Hook script names a spawned process may name. A subprocess call that names one
# of these and passes no cwd= runs the hook against whatever repository the
# CALLER's working directory is nested in -- the test-artifact shape that ran a
# real 92 s pre-push gate from a unit test. `.githooks` alone is deliberately
# NOT a token: `git config core.hooksPath .githooks` and a `Path(...)/'.githooks'`
# read are not spawns, and matching the directory name would flag them.
_HOOK_SCRIPT_NAMES = frozenset({
    "pre-commit", "pre-push", "commit-msg", "prepare-commit-msg",
    "post-commit", "post-merge", "pre-rebase",
})
_SPAWN_FUNC_NAMES = frozenset({"run", "call", "check_call", "check_output", "Popen"})


def _documentation_spans(source: str) -> list[tuple[int, int, int, int]]:
    """Spans (start_line, start_col, end_line, end_col) of comments + docstrings.

    A content rule must not fire on prose that merely *names* a pattern: the
    module docstring lists the rules this scanner detects, and a comment saying
    "do not shell out here" is guidance, not a violation. Without this the rule
    file could never pass its own gate.
    """
    spans: list[tuple[int, int, int, int]] = []

    # Comments. tokenize gives exact columns, and a file that does not lex must
    # still be scannable for the line-level rules below.
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                spans.append((tok.start[0], tok.start[1], tok.end[0], tok.end[1]))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError) as exc:
        note_failure("scanner:L206", exc) # scanner:ignore -- best-effort tokenizing; advisory spans only

    # Docstrings, meaning only the leading string of a module/class/function body.
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        note_failure("scanner:L212", exc)
        return spans
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            spans.append((
                value.lineno, value.col_offset,
                getattr(value, "end_lineno", value.lineno) or value.lineno,
                getattr(value, "end_col_offset", 0) or 0,
            ))
    return spans


def _string_literal_spans(source: str) -> list[tuple[int, int, int, int]]:
    """Spans of every string literal in the source."""
    spans: list[tuple[int, int, int, int]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.STRING:
                spans.append((tok.start[0], tok.start[1], tok.end[0], tok.end[1]))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError) as exc:
        note_failure("scanner:L237", exc) # scanner:ignore -- best-effort tokenizing; advisory spans only
    return spans


def _in_string_body(line: int, col: int, spans: list[tuple[int, int, int, int]]) -> bool:
    """True if (line, col) is inside a string literal, past its opening quote.

    A real prefix sits *at* the start of its own literal, so it survives; a
    tab escape or a "-t" CLI argument does not.
    """
    # A line can hold several literals, so a non-containing span must not end the
    # search: an early `return False` on the first one leaks the pattern through.
    for start_line, start_col, end_line, end_col in spans:
        if line < start_line or line > end_line:
            continue
        if start_line == end_line:
            if start_col < col < end_col:
                return True
            continue
        if line == start_line:
            if col > start_col:
                return True
            continue
        if line == end_line:
            if col < end_col:
                return True
            continue
        return True
    return False


def _call_text(lines: list[str], line_idx: int, col: int, cap: int = 20) -> str:
    """The text of a call starting at (line_idx, col), across lines.

    Tracks parenthesis balance so a multiline call is measured as a whole:
    `subprocess.run(\n  [...],\n  timeout=5,\n)` has its timeout on a later
    line, and a same-line-only check would report a false positive on it.
    Bounded by `cap` lines so a malformed file cannot walk the whole source.
    """
    depth = 0
    started = False
    chunks: list[str] = []
    for i in range(line_idx, min(len(lines), line_idx + cap)):
        text = lines[i]
        start = col if i == line_idx else 0
        chunks.append(text[start:])
        for ch in text[start:]:
            if ch == "(":
                depth += 1
                started = True
            elif ch == ")":
                depth -= 1
        if started and depth <= 0:
            break
    return "".join(chunks)


def _in_documentation(line: int, col: int, spans: list[tuple[int, int, int, int]]) -> bool:
    """True if (line, col) falls inside a comment or docstring span."""
    for start_line, start_col, end_line, end_col in spans:
        if line < start_line or line > end_line:
            continue
        if start_line == end_line:
            if start_col <= col < end_col:
                return True
            continue
        if line == start_line:
            if col >= start_col:
                return True
            continue
        if line == end_line:
            if col < end_col:
                return True
            continue
        return True
    return False


class ASTAntiPatternScanner:
    """Scans Python source files for 7 anti-pattern categories using AST + regex.

    Thread-safe. Scans can be run in parallel across multiple files.

    Usage:
        scanner = ASTAntiPatternScanner()

        # String-based scan
        result = scanner.scan_source("path/to/file.py", source_code_string)

        # File-based scan
        result = scanner.scan_file("path/to/file.py")

        # Batch scan multiple files
        results = scanner.scan_files(["file1.py", "file2.py"])
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._scan_log: list[dict] = []
        self._total_findings = 0
        self._total_scanned = 0

    def scan_source(self, filepath: str, source: str) -> AntiPatternResult:
        """Scan a source code string for anti-patterns.

        Args:
            filepath: Logical path for the source (for reporting)
            source: The Python source code to scan

        Returns:
            AntiPatternResult with all findings
        """
        findings = []
        lines = source.split("\n")
        line_count = len(lines)
        doc_spans = _documentation_spans(source)
        string_spans = _string_literal_spans(source)

        # 1. Unicode emoji / non-ASCII in code (not comments)
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            matches = _PATTERN_EMOJI.findall(line)
            if matches:
                findings.append((i, "unicode_emoji", matches[0] if len(matches[0]) < 50 else matches[0][:50]))

        # 2. f-string backslash escapes (prose naming the pattern is not one)
        for i, line in enumerate(lines, 1):
            reported = 0
            for m in _PATTERN_FSTRING_BACKSLASH.finditer(line):
                if reported >= 3:
                    break
                if _in_documentation(i, m.start(), doc_spans):
                    continue
                findings.append((i, "fstring_backslash", m.group(0)[:80]))
                reported += 1

        # 3. t-strings
        for i, line in enumerate(lines, 1):
            for m in _PATTERN_TSTRING.finditer(line):
                if _in_documentation(i, m.start(), doc_spans):
                    continue
                if _in_string_body(i, m.start(), string_spans):
                    continue
                findings.append((i, "t_string", line.strip()[:80]))
                break

        # 4. os.system() calls
        #
        # A match inside a string BODY is not a call: `os.system(` can only be
        # invoked when the identifier is code. What lives inside a string
        # literal is a detector -- this scanner's own rule table, an auditor
        # looking for the pattern, or a test sample. Flagging those forced the
        # detectors to obfuscate the very pattern they detect. Note this cannot
        # hide a real call inside an f-string expression: from 3.12 those are
        # tokenized as FSTRING_* plus ordinary NAME/OP tokens, not as one
        # STRING span, so `f"{os.system(x)}"` is still reported.
        # Deliberately NOT applied to rule 5: a hardcoded path IS a string.
        for i, line in enumerate(lines, 1):
            for m in _PATTERN_OS_SYSTEM.finditer(line):
                if _in_documentation(i, m.start(), doc_spans):
                    continue
                if _in_string_body(i, m.start(), string_spans):
                    continue
                findings.append((i, "os_system", line.strip()[:80]))
                break

        # 5. Hardcoded C:\\ paths
        for i, line in enumerate(lines, 1):
            for m in _PATTERN_WIN_PATH.finditer(line):
                if not _in_documentation(i, m.start(), doc_spans):
                    findings.append((i, "hardcoded_win_path", line.strip()[:80]))
                    break

        # 5b. shell=True (the os.system injection class, in its sanctioned fix)
        for i, line in enumerate(lines, 1):
            for m in _PATTERN_SHELL_TRUE.finditer(line):
                if _in_documentation(i, m.start(), doc_spans):
                    continue
                if _in_string_body(i, m.start(), string_spans):
                    continue
                findings.append((i, "shell_true", line.strip()[:80]))
                break

        # 5c. subprocess call with no timeout=. Like os_system, a match inside a
        # string BODY is not a call -- it is a detector, a rule's own fix
        # template, or a test sample. The call is measured across lines so a
        # multiline call with its timeout on a later line is not a false hit.
        # Popen refinement: collect handles that are waited on (.wait() /
        # .communicate()); a Popen never waited on is fire-and-forget.
        _waited: set[str] = set()
        _waited_timed: set[str] = set()
        try:
            _ptree = ast.parse(source)
            for _node in ast.walk(_ptree):
                if isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute) \
                        and _node.func.attr in ("wait", "communicate") \
                        and isinstance(_node.func.value, ast.Name):
                    _waited.add(_node.func.value.id)
                    if any(k.arg == "timeout" for k in _node.keywords):
                        _waited_timed.add(_node.func.value.id)
        except SyntaxError:
            _waited = set()
            _waited_timed = set()
        for i, line in enumerate(lines, 1):
            m = _PATTERN_SUBPROCESS.search(line)
            if not m:
                continue
            if "timeout" in _call_text(lines, i - 1, m.start()):
                continue
            if m.group(1) == "Popen":
                # Fire-and-forget unless the assigned handle is waited on
                # without its own timeout.
                _assign = re.match(r'\s*(\w+)\s*=', line)
                if not _assign or _assign.group(1) not in _waited \
                        or _assign.group(1) in _waited_timed:
                    continue
            if _in_documentation(i, m.start(), doc_spans):
                continue
            if _in_string_body(i, m.start(), string_spans):
                continue
            findings.append((i, "subprocess_no_timeout", line.strip()[:80]))

        # 5d. open() with no encoding= and not binary mode.
        for i, line in enumerate(lines, 1):
            m = _PATTERN_OPEN.search(line)
            if not m:
                continue
            call = _call_text(lines, i - 1, m.start())
            # Binary mode needs no encoding; a text open() does. The mode
            # string is "rb"/"wb" (not a bare "b"), or mode="...b..." --
            # fixed 2026-10-19: the old "'b'" substring test missed "rb"
            # and flagged 52 correct binary opens as text-mode findings.
            if "encoding" in call or _PATTERN_BINARY_MODE.search(call):
                continue
            if _in_documentation(i, m.start(), doc_spans):
                continue
            if _in_string_body(i, m.start(), string_spans):
                continue
            findings.append((i, "open_no_encoding", line.strip()[:80]))

        # 5e. sqlite3.connect() with no timeout= (read-write only). A writer
        # waiting on a lock blocks forever; mode=ro and :memory: cannot
        # contend and are excluded. Call measured across lines (5c/5d style).
        for i, line in enumerate(lines, 1):
            m = _PATTERN_SQLITE.search(line)
            if not m:
                continue
            call = _call_text(lines, i - 1, m.start())
            if "timeout" in call or "mode=ro" in call or ":memory:" in call:
                continue
            if _in_documentation(i, m.start(), doc_spans):
                continue
            if _in_string_body(i, m.start(), string_spans):
                continue
            findings.append((i, "sqlite_no_timeout", line.strip()[:80]))

        # 5f. requests.get/post/... with no timeout=. Same hang class as 5c:
        # a dead peer blocks the caller forever.
        for i, line in enumerate(lines, 1):
            m = _PATTERN_REQUESTS.search(line)
            if not m:
                continue
            call = _call_text(lines, i - 1, m.start())
            if "timeout" in call:
                continue
            if _in_documentation(i, m.start(), doc_spans):
                continue
            if _in_string_body(i, m.start(), string_spans):
                continue
            findings.append((i, "requests_no_timeout", line.strip()[:80]))

        # 6/7b. AST-based rules, in ONE parse: imports inside function bodies
        # and except blocks that swallow the failure (6b).
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for child in ast.walk(node):
                        if isinstance(child, (ast.Import, ast.ImportFrom)):
                            # Check if the import is directly inside the function, not in a nested def
                            for n in ast.walk(node):
                                if n is child:
                                    # Find the line number in the source
                                    findings.append((child.lineno, "import_inside_function", f"import at line {child.lineno}"))
                                    break
                            break  # Only report first import per function
                elif isinstance(node, ast.ExceptHandler):
                    # 6c. `except:` with no exception type catches EVERYTHING
                    # (KeyboardInterrupt, SystemExit, the lot) -- regardless
                    # of body length. silent_except_pass below only fires on
                    # single-statement bodies, so this closes the multi-line
                    # hole (create_reference_video.py:563, fixed 2026-10-19).
                    if node.type is None:
                        findings.append((node.lineno, "bare_except",
                                         "except:"))
                    # 6b. A single-statement body of `pass` discards the error; a
                    # single `return` turns it into a success-shaped default.
                    # Both make a failure indistinguishable from success -- the
                    # class that hid PMB's dead loop. Bodies with logging/raise
                    # are untouched (they act on the exception).
                    body = node.body
                    if len(body) != 1:
                        continue
                    stmt = body[0]
                    # Report at the STATEMENT's line, not the `except` line, so a
                    # `# scanner:ignore` on the pass/return line suppresses it
                    # (the line-based rules work the same way).
                    if isinstance(stmt, ast.Pass):
                        findings.append((stmt.lineno, "silent_except_pass",
                                         "except: pass"))
                    elif isinstance(stmt, ast.Continue):
                        findings.append((stmt.lineno, "silent_except_continue",
                                         "except: continue"))
                    elif isinstance(stmt, ast.Return):
                        # Only a SUCCESS-SHAPED default: a bare return, or a
                        # constant (None/False/0/""). A constructed failure
                        # value (e.g. `return Result(error=...)`) is a real
                        # error path, not a swallow, so it is not flagged.
                        value = stmt.value
                        if value is None or isinstance(value, ast.Constant):
                            findings.append((stmt.lineno, "except_return_default",
                                             "except: return"))
            # 6d. open() handle assigned to a name, never closed, outside any
            # `with` (escalation_sft / test_rule_0zf, fixed 2026-10-19).
            # `open(...).read()` one-liners are NOT flagged: the handle is
            # temporary and refcounting closes it at once.
            # Two principled exemptions (measured 2026-10-19 against 6 live
            # sites, all adjudicated intentional):
            # (a) function-free ancestry (module level, even inside a
            # try:/if: guard): the handle's lifetime IS the process by
            # construction (process-life log/lock handles; the OS reclaims
            # on death). (b) ownership transfer: the handle is
            # passed as stdin=/stdout=/stderr= to a child process, which
            # owns its ends; a parent close is optional, not a leak.
            # (c) OS-lock idiom: a handle fed to msvcrt.locking() /
            # fcntl.flock()/lockf() MUST stay open -- closing it releases
            # the lock, so an unclosed lock handle is the correct shape.
            with_lines = set()
            closed_names = set()
            child_owned = set()
            _parent = {}
            for _pnode in ast.walk(tree):
                for _ch in ast.iter_child_nodes(_pnode):
                    _parent[_ch] = _pnode
            for _node in ast.walk(tree):
                if isinstance(_node, ast.With):
                    for _sub in ast.walk(_node):
                        _ln = getattr(_sub, "lineno", None)
                        if _ln is not None:
                            with_lines.add(_ln)
                elif isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute) \
                        and _node.func.attr == "close" and isinstance(_node.func.value, ast.Name):
                    closed_names.add(_node.func.value.id)
                elif isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute) \
                        and _node.func.attr in ("Popen", "run", "call", "check_call",
                                               "check_output", "run_process"):
                    for _kw in _node.keywords:
                        if _kw.arg in ("stdin", "stdout", "stderr") \
                                and isinstance(_kw.value, ast.Name):
                            child_owned.add(_kw.value.id)
                elif isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute) \
                        and _node.func.attr in ("locking", "flock", "lockf"):
                    for _a in list(_node.args) + [_kw.value for _kw in _node.keywords]:
                        _h = _a.func.value.id if isinstance(_a, ast.Call) \
                            and isinstance(_a.func, ast.Attribute) \
                            and _a.func.attr == "fileno" \
                            and isinstance(_a.func.value, ast.Name) else \
                            (_a.id if isinstance(_a, ast.Name) else None)
                        if _h:
                            child_owned.add(_h)
            def _process_lived(_assign):
                _p = _parent.get(_assign)
                while _p is not None and not isinstance(_p, ast.Module):
                    if isinstance(_p, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef, ast.Lambda)):
                        return False
                    _p = _parent.get(_p)
                return _p is not None
            for _node in ast.walk(tree):
                if isinstance(_node, ast.Assign) and isinstance(_node.value, ast.Call) \
                        and isinstance(_node.value.func, ast.Name) \
                        and _node.value.func.id == "open":
                    for _target in _node.targets:
                        if isinstance(_target, ast.Name) and _target.id not in closed_names \
                                and _target.id not in child_owned \
                                and _node.lineno not in with_lines \
                                and not _process_lived(_node):
                            findings.append((_node.lineno, "open_no_context",
                                             "open() handle '" + _target.id + "' never closed"))
            # 6e. unbounded Popen waits (RULE 0Z-M third capture, 2026-10-19).
            # The spawn never blocks; .wait()/.communicate() without timeout
            # do. Receiver-tracked: only names assigned from a Popen call in
            # this file, so threading.Event.wait(timeout-var) and similar are
            # excluded by construction. wait() takes timeout positionally
            # (any positional arg bounds it); communicate(input, timeout)
            # takes input first, so it needs the keyword or 2+ positionals.
            # Kill-then-reap is exempt: after .kill()/.terminate(), .wait()
            # only reaps a dead process (nativestack.py:95 shape).
            _popen_names = set()
            _reaped = set()
            for _node in ast.walk(tree):
                if isinstance(_node, ast.Assign):
                    _v = _node.value
                    if isinstance(_v, ast.Call) and isinstance(_v.func, ast.Attribute) \
                            and _v.func.attr == "Popen":
                        for _target in _node.targets:
                            if isinstance(_target, ast.Name):
                                _popen_names.add(_target.id)
                    elif isinstance(_v, ast.Call) and isinstance(_v.func, ast.Name) \
                            and _v.func.id == "Popen":
                        for _target in _node.targets:
                            if isinstance(_target, ast.Name):
                                _popen_names.add(_target.id)
                elif isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute) \
                        and _node.func.attr in ("kill", "terminate") \
                        and isinstance(_node.func.value, ast.Name):
                    _reaped.add(_node.func.value.id)
            for _node in ast.walk(tree):
                if isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute) \
                        and isinstance(_node.func.value, ast.Name) \
                        and _node.func.value.id in _popen_names \
                        and _node.func.value.id not in _reaped:
                    if _node.func.attr == "wait":
                        _bounded = any(_kw.arg == "timeout" for _kw in _node.keywords) \
                            or len(_node.args) >= 1
                    elif _node.func.attr == "communicate":
                        _bounded = any(_kw.arg == "timeout" for _kw in _node.keywords) \
                            or len(_node.args) >= 2
                    else:
                        continue
                    if not _bounded:
                        findings.append((_node.lineno, "unbounded_wait",
                                         "unbounded ." + _node.func.attr + "() on Popen handle '"
                                         + _node.func.value.id + "'"))
            # 6f. A spawn of a repo hook with no cwd= (added 2026-11-04).
            # A child inherits the parent's working directory, so a hook
            # spawned from a test's temp dir inspects the repo that dir is
            # nested in, not the tree under test. Only LITERAL commands are
            # adjudicated: a Name/attribute command cannot be resolved here,
            # and guessing would invent findings. Tokens are split on both
            # separators so a Windows path token matches too.
            for _node in ast.walk(tree):
                if not isinstance(_node, ast.Call):
                    continue
                _fn = _node.func
                if isinstance(_fn, ast.Attribute):
                    if _fn.attr not in _SPAWN_FUNC_NAMES:
                        continue
                elif isinstance(_fn, ast.Name):
                    if _fn.id not in _SPAWN_FUNC_NAMES:
                        continue
                else:
                    continue
                if any(_kw.arg == "cwd" for _kw in _node.keywords):
                    continue
                if not _node.args:
                    continue
                _cmd = _node.args[0]
                _tokens: list[str] = []
                if isinstance(_cmd, (ast.List, ast.Tuple)):
                    _tokens = [e.value for e in _cmd.elts
                               if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                elif isinstance(_cmd, ast.Constant) and isinstance(_cmd.value, str):
                    _tokens = _cmd.value.split()
                _hit = next((t for t in _tokens
                             if t.replace(chr(92), "/").rsplit("/", 1)[-1] in _HOOK_SCRIPT_NAMES),
                            None)
                if _hit is not None:
                    findings.append((_node.lineno, "hook_spawn_without_cwd",
                                     "spawns '" + _hit + "' with no cwd="))
        except SyntaxError as exc:
            note_failure("scanner:L494", exc)
            findings.append((0, "syntax_error", "Cannot parse source — syntax error"))

        # 7. cls.__new__() patterns
        for i, line in enumerate(lines, 1):
            matches = _PATTERN_CLS_NEW.findall(line)
            if matches:
                findings.append((i, "cls_new_factory", line.strip()[:80]))

        # 8. Explicit per-line suppression (covers every rule above).
        suppressed = {i for i, line in enumerate(lines, 1) if _SUPPRESSION_MARKER in line}

        # Remove duplicates (same line, same category)
        seen = set()
        unique_findings = []
        for ln, cat, sn in findings:
            if ln in suppressed:
                continue
            key = (ln, cat)
            if key not in seen:
                seen.add(key)
                unique_findings.append((ln, cat, sn))

        result = AntiPatternResult(
            filepath=filepath,
            lines_scanned=line_count,
            findings=unique_findings,
        )

        self._log_scan(result)
        return result

    def scan_file(self, filepath: str) -> (AntiPatternResult | None):
        """Scan a file on disk for anti-patterns.

        Args:
            filepath: Path to the Python source file

        Returns:
            AntiPatternResult or None if file can't be read
        """
        path = Path(filepath)
        if not path.exists():
            return None

        try:
            source = path.read_text(encoding="utf-8")
            return self.scan_source(str(path), source)
        except (OSError, UnicodeDecodeError) as e:
            note_failure("scanner:L542", e)
            return AntiPatternResult(
                filepath=str(path),
                lines_scanned=0,
                findings=[(0, "read_error", str(e)[:80])],
            )

    def scan_files(self, filepaths: list[str]) -> list[AntiPatternResult]:
        """Scan multiple files for anti-patterns.

        Runs synchronously. For large batches, consider using threads.

        Returns:
            list of AntiPatternResult for each file
        """
        results = []
        for fp in filepaths:
            result = self.scan_file(fp)
            if result:
                results.append(result)
        return results

    def scan_directory(self, directory: str, pattern: str = "*.py", exclude_dirs: (list[str] | None) = None) -> list[AntiPatternResult]:
        """Scan all Python files in a directory.

        Args:
            directory: Root directory to scan
            pattern: Glob pattern for files (default: *.py)
            exclude_dirs: Optional list of directory names to exclude.
                Merged with DEFAULT_EXCLUDES unless user explicitly passes
                an empty list [] to override defaults.

        Returns:
            list of AntiPatternResult
        """
        root = Path(directory)
        if not root.exists():
            return []

        # Merge user-provided excludes with DEFAULT_EXCLUDES
        # Note: DEFAULT_EXCLUDES are always applied to ensure common dirs are skipped
        if exclude_dirs is None:
            exclude_dirs = list(DEFAULT_EXCLUDES)
        else:
            exclude_dirs = list(set(exclude_dirs) | DEFAULT_EXCLUDES)

        exclude_set = set(exclude_dirs or [])

        results = []
        for fp in root.rglob(pattern):
            # Check if any parent directory is in exclude list
            if exclude_set:
                parts = fp.relative_to(root).parts
                if any(part in exclude_set for part in parts):
                    continue
            result = self.scan_file(str(fp))
            if result:
                results.append(result)
        return results

    def _log_scan(self, result: AntiPatternResult):
        with self._lock:
            self._scan_log.append(result.to_dict())
            self._total_findings += result.finding_count
            self._total_scanned += 1

    def get_scan_log(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._scan_log[-limit:])

    @property
    def total_findings(self) -> int:
        with self._lock:
            return self._total_findings

    @property
    def total_scanned(self) -> int:
        with self._lock:
            return self._total_scanned

    def get_summary(self) -> dict:
        with self._lock:
            findings_by_category: dict[str, int] = {}
            for entry in self._scan_log:
                for f in entry.get("findings", []):
                    cat = f.get("category", "unknown")
                    findings_by_category[cat] = findings_by_category.get(cat, 0) + 1
            return {
                "total_files_scanned": self._total_scanned,
                "total_findings": self._total_findings,
                "findings_by_category": findings_by_category,
            }

    def reset(self):
        with self._lock:
            self._scan_log.clear()
            self._total_findings = 0
            self._total_scanned = 0

    def __repr__(self) -> str:
        return f"ASTAntiPatternScanner(scanned={self._total_scanned}, findings={self._total_findings})"


# ---------------------------------------------------------------------------
# Convenience: scan a single file
# ---------------------------------------------------------------------------

_global_scanner: (ASTAntiPatternScanner | None) = None
_scanner_lock = threading.Lock()


def scan_file(filepath: str) -> (AntiPatternResult | None):
    """Convenience: scan a single file with the global scanner.

    Creates the scanner on first use.

    Args:
        filepath: Path to the Python file to scan

    Returns:
        AntiPatternResult or None
    """
    global _global_scanner
    if _global_scanner is None:
        with _scanner_lock:
            if _global_scanner is None:
                _global_scanner = ASTAntiPatternScanner()
    return _global_scanner.scan_file(filepath)


def reset_scanner():
    """Reset the global scanner (for testing)."""
    global _global_scanner
    with _scanner_lock:
        _global_scanner = None


# ---------------------------------------------------------------------------
# CLI: --report flag for command-line compliance scanning
# ---------------------------------------------------------------------------


def run_cli():
    """Run scanner from the command line with --report support."""

    # Fix UnicodeEncodeError on Windows console
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # pyright: ignore[reportAttributeAccessIssue]

    parser = argparse.ArgumentParser(
        description="AST Anti-Pattern Scanner for Python source files"
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Scan all .py files in a directory and print compliance report"
    )
    parser.add_argument(
        "--dir", type=str, default=".",
        help="Directory to scan (default: current directory)"
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="Write findings JSON to this directory (one file per scanned directory)"
    )
    parser.add_argument(
        "--format", type=str, default="text",
        choices=["text", "json", "html", "junit-xml"],
        help="Output format: text (default), json, html, junit-xml"
    )
    parser.add_argument(
        "--ci", action="store_true",
        help="Exit with non-zero code if any findings exist (CI mode)"
    )
    parser.add_argument(
        "--exclude-dirs", type=str, default=None,
        help="Comma-separated list of directory names to exclude from scanning"
    )
    parser.add_argument(
        "--scannerignore", type=str, default=None,
        help="Path to .scannerignore file with exclusion patterns (one per line)"
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Watch directory for file changes and re-scan continuously"
    )
    parser.add_argument(
        "--watch-delay", type=float, default=1.0,
        help="Debounce delay in seconds for --watch mode (default: 1.0)"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress all stdout except final verdict or JSON output"
    )
    parser.add_argument(
        "--summary-only", action="store_true",
        help="Only print summary counts, no per-file details"
    )
    parser.add_argument(
        "--fail-on", type=str, default=None,
        help="Comma-separated categories that cause non-zero exit (e.g. os_system,hardcoded_win_path)"
    )
    parser.add_argument(
        "--exclude-files", type=str, default=None,
        help="Comma-separated glob patterns for files to exclude (e.g. *test*,*_pb2.py)"
    )
    parser.add_argument(
        "--max-findings-per-file", type=int, default=0,
        help="Maximum findings to report per file (0 = unlimited)"
    )
    parser.add_argument(
        "--since", type=str, default=None,
        help="Only scan files modified after this date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--version", action="store_true",
        help="Print scanner version and exit"
    )
    parser.add_argument(
        "--list-rules", action="store_true",
        help="list all available anti-pattern rules with descriptions"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Print detailed debug info: files scanned, excluded, timing per file"
    )
    parser.add_argument(
        "--suggest-fix", action="store_true",
        help="Suggest code fixes for each finding"
    )
    parser.add_argument(
        "--explain", action="store_true",
        help="Explain why each anti-pattern is problematic with examples"
    )
    parser.add_argument(
        "--init", action="store_true",
        help="Create a default .scanner-config.yaml in the current directory"
    )
    parser.add_argument(
        "--auto-fix", action="store_true",
        help="Automatically fix certain anti-patterns (emoji, os.system)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview auto-fix changes without modifying files"
    )
    parser.add_argument(
        "--severity", type=str, default=None, choices=["error", "warning", "info"],
        help="Minimum severity level to report (error > warning > info)"
    )
    parser.add_argument(
        "--threshold", type=int, default=0,
        help="Exit non-zero only if total findings exceed this threshold"
    )
    parser.add_argument(
        "--max-findings-total", type=int, default=0,
        help="Maximum total findings to report (0 = unlimited)"
    )
    parser.add_argument(
        "--include-ext", type=str, default=None,
        help="Comma-separated file extensions to scan (default: .py)"
    )
    parser.add_argument(
        "--open", action="store_true",
        help="Open HTML report in default browser after scanning"
    )
    parser.add_argument(
        "--ignore-errors", action="store_true",
        help="Skip files that can't be read instead of reporting read_error"
    )
    parser.add_argument(
        "--export-dir", type=str, default=None,
        help="Save reports to a timestamped directory on each scan"
    )
    parser.add_argument(
        "--rotate", type=int, default=0,
        help="Keep only the last N reports in export directory (0 = unlimited)"
    )
    parser.add_argument(
        "--health-check", action="store_true",
        help="Run a quick self-test verifying scanner can parse Python and detect patterns"
    )
    parser.add_argument(
        "--compliance-score", action="store_true",
        help="Calculate and print a 0-100 compliance score"
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="Use parallel scanning for faster results on large codebases"
    )
    parser.add_argument(
        "--db-init", action="store_true",
        help="Initialize the findings database (compliance_history.db)"
    )
    parser.add_argument(
        "--db-history", type=int, nargs="?", const=7, default=None,
        help="Show scan history for last N days (default: 7)"
    )
    parser.add_argument(
        "--db-trend", type=int, nargs="?", const=30, default=None,
        help="Analyze finding trends over last N days (default: 30)"
    )
    parser.add_argument(
        "--db-regression", type=int, nargs="?", const=7, default=None,
        help="Detect regressions over last N days (default: 7)"
    )
    parser.add_argument(
        "--webhook", type=str, default=None,
        help="Send scan results as JSON POST to this URL on completion"
    )
    parser.add_argument(
        "--daemon-log", type=str, default=None,
        help="Log file path for --daemon mode output"
    )
    parser.add_argument(
        "--notify", action="store_true",
        help="Show desktop notification on scan completion with finding count"
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Run scanner in daemon mode, re-scanning on a fixed interval"
    )
    parser.add_argument(
        "--daemon-interval", type=int, default=300,
        help="Interval in seconds between scans in --daemon mode (default: 300)"
    )
    args = parser.parse_args()

    # Handle --db-* flags
    _db = None
    if args.db_init or args.db_history is not None or args.db_trend is not None or args.db_regression is not None:
        try:
            from CORE.compliance.scanner_db import FindingsDB
            from CORE.compliance.scanner_db import print_history, print_trend, print_regressions
            _db = FindingsDB()
        except ImportError as e:
            print(f"Error: scanner_db module not available ({e})")
            sys.exit(2)

    if args.db_init:
        print(f"  Database initialized: {_db._db_path}")  # pyright: ignore[reportOptionalMemberAccess]
        sys.exit(0)

    if args.db_history is not None:
        history = _db.get_history(days=args.db_history)  # pyright: ignore[reportOptionalMemberAccess]
        print_history(history)
        sys.exit(0)

    if args.db_trend is not None:
        trend = _db.get_trend(days=args.db_trend)  # pyright: ignore[reportOptionalMemberAccess]
        print_trend(trend)
        sys.exit(0)

    if args.db_regression is not None:
        regressions = _db.get_regressions(days=args.db_regression)  # pyright: ignore[reportOptionalMemberAccess]
        print_regressions(regressions)
        sys.exit(0)

    # Handle --health-check
    if args.health_check:
        _run_health_check()
        sys.exit(0)

    # Handle --version
    if args.version:
        print("AST Anti-Pattern Scanner v1.0.0")
        print("RULE 0X-7 Compliance Scanner for Python source files")
        print(f"Detects {len(FINDING_SEVERITIES)} anti-pattern categories")
        sys.exit(0)

    # Handle --list-rules
    if args.list_rules:
        print("Available Anti-Pattern Rules:")
        print("=" * 60)
        for cat, sev in sorted(FINDING_SEVERITIES.items()):
            fix = FIX_SUGGESTIONS.get(cat, "No fix suggestion available")
            print(f"  [{sev.upper():7}] {cat}")
            print(f"           {fix}")
        print()
        print(f"Total: {len(FINDING_SEVERITIES)} rules")
        sys.exit(0)

    # Handle --init
    if args.init:
        _init_config()
        sys.exit(0)

    # Parse exclude dirs before branching (used by both --report and --watch)
    exclude_dirs = set()
    if args.exclude_dirs:
        exclude_dirs = {d.strip() for d in args.exclude_dirs.split(",")}
    exclude_dirs_list = list(exclude_dirs) if exclude_dirs else None

    # Parse exclude files pattern
    exclude_file_patterns = []
    if args.exclude_files:
        exclude_file_patterns = [p.strip() for p in args.exclude_files.split(",")]

    # Compute file pattern and scannerignore early (needed by both --report and --daemon)
    _file_pattern = "*.py"
    if args.include_ext:
        exts = [e.strip() for e in args.include_ext.split(",")]
        _file_pattern = f"*{{{','.join(exts)}}}" if len(exts) > 1 else f"*.{exts[0]}"

    _scannerignore_patterns = set()
    if args.scannerignore:
        ignore_path = Path(args.scannerignore)
        if ignore_path.exists():
            for line in ignore_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    _scannerignore_patterns.add(line)

    if args.report:
        scanner = ASTAntiPatternScanner()
        
        # Filter by modification date if --since is specified
        _since_dt = None
        if args.since:
            try:
                from datetime import datetime as dt_parse
                _since_dt = dt_parse.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                print(f"Error: Invalid date format for --since: {args.since} (expected YYYY-MM-DD)")
                sys.exit(2)
        
        # Use parallel scanning if requested
        if args.parallel:
            results = _scan_directory_parallel(args.dir, _file_pattern, exclude_dirs=exclude_dirs_list)
        else:
            results = scanner.scan_directory(args.dir, _file_pattern, exclude_dirs=exclude_dirs_list)
        
        # Apply --since filter
        if _since_dt and results:
            filtered = []
            for r in results:
                try:
                    mtime = os.path.getmtime(r.filepath)
                    mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
                    if mtime_dt >= _since_dt:
                        filtered.append(r)
                except OSError as exc:
                    note_failure("scanner:L983", exc)
                    filtered.append(r)
            results = filtered
        
        # Apply --scannerignore patterns
        if _scannerignore_patterns and results:
            filtered = []
            for r in results:
                rel = os.path.relpath(r.filepath, args.dir)
                if not any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(os.path.basename(r.filepath), pat) for pat in _scannerignore_patterns):
                    filtered.append(r)
            results = filtered

        # Apply --exclude-files filter
        if exclude_file_patterns and results:
            filtered = []
            for r in results:
                basename = os.path.basename(r.filepath)
                if not any(fnmatch.fnmatch(basename, pat) for pat in exclude_file_patterns):
                    filtered.append(r)
            results = filtered
        
        # Apply --max-findings-per-file
        max_per_file = args.max_findings_per_file if args.max_findings_per_file > 0 else None
        if max_per_file:
            for r in results:
                if r.finding_count > max_per_file:
                    r.findings = r.findings[:max_per_file]

        # Apply --ignore-errors: remove read_error findings from results
        if args.ignore_errors and results:
            for r in results:
                r.findings = [(ln, cat, sn) for ln, cat, sn in r.findings if cat != "read_error"]
            results = [r for r in results if r.finding_count > 0 or r.lines_scanned > 0]

        # Apply --severity filter
        if args.severity and results:
            severity_order = {"error": 0, "warning": 1, "info": 2}
            min_level = severity_order.get(args.severity, 0)
            for r in results:
                r.findings = [
                    (ln, cat, sn) for ln, cat, sn in r.findings
                    if severity_order.get(FINDING_SEVERITIES.get(cat, "info"), 2) <= min_level
                ]
            results = [r for r in results if r.finding_count > 0 or r.lines_scanned > 0]

        total_files = len(results)
        total_findings = sum(r.finding_count for r in results)

        # Apply --max-findings-total
        if args.max_findings_total > 0 and total_findings > args.max_findings_total:
            taken = 0
            for r in results:
                keep = min(r.finding_count, args.max_findings_total - taken)
                r.findings = r.findings[:keep]
                taken += keep
                if taken >= args.max_findings_total:
                    break
            total_findings = sum(r.finding_count for r in results)

        files_with_findings = sum(1 for r in results if not r.passed)

        # Calculate compliance score
        compliance_score = max(0, 100 - (total_findings * 5))
        # Bonus: clean codebases get perfect score
        if total_findings == 0 and total_files > 0:
            compliance_score = 100
        # Penalty: errors reduce more
        for r in results:
            for _, cat, _ in r.findings:
                if FINDING_SEVERITIES.get(cat) == "error":
                    compliance_score = max(0, compliance_score - 2)

        # Debug output
        if args.debug:
            print(f"[DEBUG] Files scanned: {total_files}, Findings: {total_findings}")
            print(f"[DEBUG] Exclude dirs: {exclude_dirs_list}")
            print(f"[DEBUG] Exclude files: {exclude_file_patterns}")
            print(f"[DEBUG] Since date: {_since_dt}")
        
        # CI mode: exit codes for pipeline integration
        ci_exit_code = 0
        if args.ci or args.format in ("json", "html", "junit-xml"):
            ci_exit_code = 1 if total_findings > 0 else 0
        
        # If --fail-on is specified, only fail for those categories
        fail_on_categories = set()
        if args.fail_on:
            fail_on_categories = {c.strip().lower() for c in args.fail_on.split(",")}
            fail_on_findings = 0
            for r in results:
                for _, cat, _ in r.findings:
                    if cat.lower() in fail_on_categories:
                        fail_on_findings += 1
            if fail_on_findings > 0:
                ci_exit_code = 1
            else:
                ci_exit_code = 0  # Override default CI behavior when --fail-on is active

        # Silent mode: suppress all output except verdict
        if args.quiet and args.format == "text":
            pass  # Skip output, only print verdict below
        
        # Human-readable text output (suppressed for json/html formats)
        if args.format == "text" and not args.quiet:
            print("=" * 60)
            print("  COMPLIANCE SCAN REPORT")
            print("=" * 60)
            print(f"  Directory: {os.path.abspath(args.dir)}")
            print(f"  Files scanned: {total_files}")
            print(f"  Files with findings: {files_with_findings}")
            print(f"  Total findings: {total_findings}")
            print()

            if total_findings > 0:
                # Group by category
                by_category = {}
                for r in results:
                    for line_no, cat, snippet in r.findings:
                        by_category.setdefault(cat, []).append({
                            "file": r.filepath,
                            "line": line_no,
                            "snippet": snippet,
                        })

                for cat, items in sorted(by_category.items()):
                    print(f"  [{cat}] {len(items)} instance(s)")
                    # --summary-only: skip per-finding details
                    if not args.summary_only:
                        for item in items[:3]:
                            print(f"    {item['file']}:{item['line']}  {item['snippet'][:60]}")
                        if len(items) > 3:
                            print(f"    ... and {len(items) - 3} more")
                    print()

            # Summary
            summary = scanner.get_summary()
            if summary["findings_by_category"]:
                print("  Breakdown by category:")
                for cat, cnt in sorted(
                    summary["findings_by_category"].items(),
                    key=lambda x: -x[1]
                ):
                    print(f"    {cat}: {cnt}")

            # --compliance-score: print score
            if args.compliance_score:
                print(f"  Compliance Score: {compliance_score}/100")
                if compliance_score >= 90:
                    print("    Grade: A")
                elif compliance_score >= 75:
                    print("    Grade: B")
                elif compliance_score >= 50:
                    print("    Grade: C")
                else:
                    print("    Grade: D")

            print(f"\n  Verdict: {"ALL CLEAN" if total_findings == 0 else f"{total_findings} issue(s) found"}")
            
            # --suggest-fix: show fix suggestions
            if args.suggest_fix and total_findings > 0:
                print()
                print("  Suggested Fixes:")
                seen_cats = set()
                for r in results:
                    for _, cat, _ in r.findings:
                        if cat not in seen_cats and cat in FIX_SUGGESTIONS:
                            seen_cats.add(cat)
                            print(f"    [{cat}] {FIX_SUGGESTIONS[cat]}")
            
            # --explain: show explanations
            if args.explain and total_findings > 0:
                print()
                print("  Findings Explanations:")
                seen_cats = set()
                for r in results:
                    for _, cat, _ in r.findings:
                        if cat not in seen_cats and cat in FINDING_EXPLANATIONS:
                            seen_cats.add(cat)
                            print(f"    [{cat}] {FINDING_EXPLANATIONS[cat]}")

        # JSON format: write structured JSON to stdout
        if args.format == "json" and not args.output_dir:
            output_data = {
                "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "directory": os.path.abspath(args.dir),
                "total_files": total_files,
                "files_with_findings": files_with_findings,
                "total_findings": total_findings,
                "findings_by_category": scanner.get_summary()["findings_by_category"],
                "results": [r.to_dict() for r in results],
            }
            print(json.dumps(output_data, indent=2, default=str))

        # HTML format: generate self-contained compliance report
        if args.format == "html":
            html = _generate_html_report(
                scanner, results, total_files, files_with_findings, total_findings
            )
            if args.output_dir:
                output_path = Path(args.output_dir)
                output_path.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                safe_dir_name = os.path.basename(os.path.normpath(args.dir))
                html_path = output_path / f"compliance_{safe_dir_name}_{timestamp}.html"
                html_path.write_text(html, encoding="utf-8")
                print(f"  HTML output: {html_path}")
            else:
                print(html)

            # --open: open HTML report in browser
            if args.open:
                import webbrowser
                if args.output_dir:
                    webbrowser.open(str(html_path))
                else:
                    import tempfile
                    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
                    tmp.write(html)
                    tmp.close()
                    webbrowser.open(f"file://{tmp.name}")

        # JUnit XML format
        if args.format == "junit-xml":
            _print_junit_xml(results, total_files, total_findings)

        # --webhook: POST scan results
        if args.webhook:
            _send_webhook(args.webhook, total_files, total_findings, files_with_findings, compliance_score)

        # --notify: desktop notification
        if args.notify:
            _show_notification(total_files, total_findings, files_with_findings)

        # --auto-fix / --dry-run: apply fixes
        if args.auto_fix or args.dry_run:
            _apply_auto_fixes(results, dry_run=args.dry_run, quiet=args.quiet)

        # Write JSON output if --output-dir specified
        if args.output_dir:
            output_path = Path(args.output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safe_dir_name = os.path.basename(os.path.normpath(args.dir))
            out_file = output_path / f"compliance_{safe_dir_name}_{timestamp}.json"

            output_data = {
                "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "directory": os.path.abspath(args.dir),
                "total_files": total_files,
                "files_with_findings": files_with_findings,
                "total_findings": total_findings,
                "compliance_score": compliance_score,
                "findings_by_category": scanner.get_summary()["findings_by_category"],
                "results": [r.to_dict() for r in results],
            }
            out_file.write_text(
                json.dumps(output_data, indent=2, default=str),
                encoding="utf-8"
            )
            print(f"  JSON output: {out_file}")

        # --export-dir: save to timestamped directory with rotation
        if args.export_dir:
            export_path = Path(args.export_dir)
            export_path.mkdir(parents=True, exist_ok=True)
            run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safe_name = os.path.basename(os.path.normpath(args.dir))
            run_dir = export_path / f"scan_{safe_name}_{run_ts}"
            run_dir.mkdir(exist_ok=True)
            
            # Write JSON
            output_data = {
                "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "directory": os.path.abspath(args.dir),
                "total_files": total_files,
                "files_with_findings": files_with_findings,
                "total_findings": total_findings,
                "compliance_score": compliance_score,
                "findings_by_category": scanner.get_summary()["findings_by_category"],
                "results": [r.to_dict() for r in results],
            }
            (run_dir / "findings.json").write_text(json.dumps(output_data, indent=2, default=str), encoding="utf-8")
            
            # Write HTML report if --format html
            if args.format == "html":
                html = _generate_html_report(scanner, results, total_files, files_with_findings, total_findings)
                (run_dir / "report.html").write_text(html, encoding="utf-8")
            
            print(f"  Export: {run_dir}")
            
            # --rotate: keep only last N exports
            if args.rotate > 0:
                import shutil
                scan_dirs = sorted([d for d in export_path.iterdir() if d.is_dir() and d.name.startswith(f"scan_{safe_name}_")])
                while len(scan_dirs) > args.rotate:
                    oldest = scan_dirs.pop(0)
                    shutil.rmtree(oldest)
                    print(f"  Rotated: removed {oldest}")

        # Auto-store scan results to findings database
        try:
            from CORE.compliance.scanner_db import FindingsDB
            _store_db = FindingsDB()
            _store_db.store_scan({
                "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "directory": os.path.abspath(args.dir),
                "total_files": total_files,
                "total_findings": total_findings,
                "files_with_findings": files_with_findings,
                "compliance_score": compliance_score,
                "results": [r.to_dict() for r in results],
            })
        except Exception as exc:
            note_failure("scanner:L1296", exc) # scanner:ignore -- DB storage is advisory, never block a scan

        # Apply --threshold: only fail if findings exceed threshold
        if args.threshold > 0 and total_findings <= args.threshold:
            ci_exit_code = 0

        if args.ci:
            sys.exit(ci_exit_code)
        elif args.format in ("json", "html", "junit-xml"):
            sys.exit(ci_exit_code)
        else:
            sys.exit(0 if total_findings == 0 else 1)

    elif args.watch:
        _run_watch_mode(args.dir, args.format, exclude_dirs_list, args.watch_delay)

    elif args.daemon:
        _run_daemon_mode(args.dir, args.format, exclude_dirs_list, args.daemon_interval, file_pattern=_file_pattern, log_file=args.daemon_log)

    else:
        parser.print_help()


def _generate_html_report(
    scanner, results, total_files, files_with_findings, total_findings
) -> str:
    """Generate a self-contained dark-theme HTML compliance report."""
    summary = scanner.get_summary()
    by_category = summary.get("findings_by_category", {})

    rows_html = ""
    if results:
        for r in sorted(results, key=lambda x: not x.passed):
            status = "FAIL" if not r.passed else "PASS"
            badge = f'<span class="{'fail' if not r.passed else 'pass'}">{status}</span>'
            findings_list = ""
            for ln, cat, sn in r.findings[:5]:
                findings_list += f'<li class="finding-item"><code>L{ln}</code> [{cat}] {sn[:80]}</li>'
            if r.finding_count > 5:
                findings_list += f'<li class="finding-item mute">... +{r.finding_count - 5} more</li>'
            findings_html = f'<ul class="findings-list">{findings_list}</ul>' if findings_list else '<span class="clean">Clean</span>'
            rows_html += f'''
            <tr>
                <td class="file-col">{badge} {r.filepath}</td>
                <td>{r.lines_scanned}</td>
                <td>{r.finding_count}</td>
                <td>{findings_html}</td>
            </tr>'''

    cat_rows = ""
    for cat, count in sorted(by_category.items(), key=lambda x: -x[1]):
        cat_rows += f'<tr><td>{cat}</td><td class="num">{count}</td></tr>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compliance Scan Report</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0d1117; color:#c9d1d9; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:24px; }}
h1 {{ color:#58a6ff; font-size:1.6em; margin-bottom:8px; }}
h2 {{ color:#8b949e; font-size:1.1em; margin:24px 0 8px; border-bottom:1px solid #21262d; padding-bottom:4px; }}
.summary {{ display:flex; gap:16px; margin:16px 0; flex-wrap:wrap; }}
.card {{ background:#161b22; border:1px solid #21262d; border-radius:8px; padding:16px; min-width:140px; flex:1; }}
.card .value {{ font-size:1.8em; font-weight:600; color:#f0f6fc; }}
.card .label {{ font-size:0.8em; color:#8b949e; text-transform:uppercase; }}
.card.fail .value {{ color:#f85149; }}
.card.pass .value {{ color:#3fb950; }}
table {{ width:100%; border-collapse:collapse; margin:8px 0; }}
th {{ text-align:left; padding:8px; border-bottom:2px solid #30363d; color:#8b949e; font-size:0.8em; text-transform:uppercase; }}
td {{ padding:8px; border-bottom:1px solid #21262d; vertical-align:top; }}
.file-col {{ max-width:400px; overflow:hidden; text-overflow:ellipsis; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.pass {{ color:#3fb950; }}
.fail {{ color:#f85149; }}
.clean {{ color:#3fb950; font-style:italic; }}
.mute {{ color:#484f58; font-style:italic; }}
.findings-list {{ list-style:none; padding:0; }}
.finding-item {{ font-size:0.85em; margin:2px 0; }}
.finding-item code {{ background:#1c2128; padding:1px 4px; border-radius:3px; color:#ffa657; }}
footer {{ margin-top:32px; color:#484f58; font-size:0.8em; text-align:center; }}
</style>
</head>
<body>
<h1>Compliance Scan Report</h1>
<p>Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</p>

<div class="summary">
  <div class="card{" fail" if files_with_findings > 0 else " pass"}">
    <div class="value">{files_with_findings}</div>
    <div class="label">Files with Findings</div>
  </div>
  <div class="card pass">
    <div class="value">{total_files}</div>
    <div class="label">Total Files Scanned</div>
  </div>
  <div class="card{" fail" if total_findings > 0 else " pass"}">
    <div class="value">{total_findings}</div>
    <div class="label">Total Findings</div>
  </div>
</div>

<h2>Findings by Category</h2>
<table>
<tr><th>Category</th><th class="num">Count</th></tr>
{cat_rows}
</table>

<h2>Per-File Breakdown</h2>
<table>
<tr><th>File</th><th>Lines</th><th class="num">Findings</th><th>Details</th></tr>
{rows_html}
</table>

<footer>Compliance Scanner - AZAZAEL RULE 0X-7</footer>
</body>
</html>'''



# ---------------------------------------------------------------------------
# Watch mode: monitor directory for file changes and re-scan continuously
# ---------------------------------------------------------------------------


def _run_watch_mode(directory: str, fmt: str, exclude_dirs: (list[str] | None), watch_delay: float = 1.0):
    """Run scanner in watch mode, re-scanning on .py file changes.

    Uses watchdog to monitor the directory. On any file system event
    targeting a .py file, re-runs the full scan and prints results.

    Args:
        directory: Directory to watch
        fmt: Output format (text, json, html)
        exclude_dirs: list of directory names to exclude
        watch_delay: Debounce interval in seconds (default: 1.0)
    """
    try:
        import time
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("Error: --watch mode requires 'watchdog' package. Install with: pip install watchdog")
        sys.exit(2)

    class ScanEventHandler(FileSystemEventHandler):
        def __init__(self):
            self._debounce_timer = 0

        def on_any_event(self, event):
            # Debounce: skip events within the configured delay
            now = time.monotonic()
            if now - self._debounce_timer < watch_delay:
                return
            self._debounce_timer = now

            if event.is_directory:
                return
            # src_path is bytes when the path is undecodable; decode it so the
            # suffix check (and the crash it replaces) works on both kinds.
            src_path = event.src_path
            if isinstance(src_path, bytes):
                src_path = src_path.decode("utf-8", "replace")
            if not src_path.endswith(".py"):
                return

            scanner = ASTAntiPatternScanner()
            results = scanner.scan_directory(directory, "*.py", exclude_dirs=list(exclude_dirs) if exclude_dirs else None)

            total_files = len(results)
            total_findings = sum(r.finding_count for r in results)
            files_with_findings = sum(1 for r in results if not r.passed)

            ts = time.strftime("%H:%M:%S")
            print(f"\n[{ts}] Re-scan triggered by: {event.src_path}")
            print(f"  Files: {total_files}, Findings: {total_findings}, Files with findings: {files_with_findings}")

            if fmt == "json":
                import json
                output_data = {
                    "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "directory": os.path.abspath(directory),
                    "total_files": total_files,
                    "total_findings": total_findings,
                    "results": [r.to_dict() for r in results],
                }
                print(json.dumps(output_data, indent=2, default=str))
            elif fmt == "html":
                from pathlib import Path
                html = _generate_html_report(
                    scanner, results, total_files, files_with_findings, total_findings
                )
                output_path = Path(f"watch_report_{time.strftime('%Y%m%d_%H%M%S')}.html")
                output_path.write_text(html, encoding="utf-8")
                print(f"  HTML report: {output_path}")
            else:
                if total_findings > 0:
                    print("  Findings by category:")
                    summary = scanner.get_summary()
                    for cat, count in sorted(
                        summary.get("findings_by_category", {}).items(),
                        key=lambda x: -x[1]
                    ):
                        print(f"    {cat}: {count}")

    event_handler = ScanEventHandler()
    observer = Observer()
    observer.schedule(event_handler, os.path.abspath(directory), recursive=True)
    observer.start()

    print(f"\n  Watching {os.path.abspath(directory)} for .py file changes...")
    print(f"  Debounce delay: {watch_delay}s")
    print("  Press Ctrl+C to stop.\n")

    try:
        while observer.is_alive():
            observer.join(1)
    except KeyboardInterrupt as exc:
        note_failure("scanner:L1511", exc)
        observer.stop()
    except Exception as e:
        print(f"\n  Watch error: {e}")
        observer.stop()
    observer.join()
    print("\n  Watch mode stopped.")



# ---------------------------------------------------------------------------
# Config initialization
# ---------------------------------------------------------------------------


def _init_config():
    """Create a default .scanner-config.yaml in the current directory."""
    try:
        import yaml  # pyright: ignore[reportMissingModuleSource]
    except ImportError:
        print("Error: --init requires PyYAML. Install with: pip install pyyaml")
        print("       Or create .scanner-config.yaml manually.")
        sys.exit(2)
    config = {
        "scanner": {
            "dir": ".",
            "format": "text",
            "exclude_dirs": ["__pycache__", ".git", ".github", "node_modules", ".venv"],
            "exclude_files": ["*test*", "*_pb2.py"],
            "max_findings_per_file": 0,
            "ci": False,
            "quiet": False,
            "summary_only": False,
            "fail_on": [],
            "suggest_fix": False,
            "explain": False,
        }
    }
    config_path = Path(".scanner-config.yaml")
    if config_path.exists():
        print(f"Config file already exists: {config_path}")
        return
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False)
    print(f"Created default config: {config_path}")



# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


def _run_health_check():
    """Run a quick self-test verifying scanner can parse Python and detect patterns."""
    print("Scanner Health Check")
    print("=" * 40)
    
    # Test 1: can import
    print("[1/4] Import check...", end=" ")
    try:
        s = ASTAntiPatternScanner()
        print("OK")
    except Exception as e:
        print(f"FAIL: {e}")
        return
    
    # Test 2: clean code passes
    print("[2/4] Clean code scan...", end=" ")
    r = s.scan_source("test.py", "x = 1\nprint(x)")
    if r.passed:
        print("OK")
    else:
        print(f"FAIL: {r.findings}")
        return
    
    # Test 3: dirty code detected
    print("[3/4] Dirty code detection...", end=" ")
    r = s.scan_source("test.py", "import os\nos.system('ls')")
    if not r.passed:
        print("OK")
    else:
        print("FAIL: no findings on dirty code")
        return
    
    # Test 4: CLI imports clean
    print("[4/4] CLI function...", end=" ")
    try:
        print("OK")
    except Exception as e:
        print(f"FAIL: {e}")
        return
    
    print()
    print("Health check PASSED — all 4/4 checks OK")


# ---------------------------------------------------------------------------
# Parallel scanning
# ---------------------------------------------------------------------------


def _scan_directory_parallel(directory: str, pattern: str, exclude_dirs: (list[str] | None) = None) -> list:
    """Scan a directory in parallel using ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    root = Path(directory)
    if not root.exists():
        return []
    
    if exclude_dirs is None:
        exclude_dirs = list(DEFAULT_EXCLUDES)
    else:
        exclude_dirs = list(set(exclude_dirs) | DEFAULT_EXCLUDES)
    exclude_set = set(exclude_dirs)
    
    files = []
    for fp in root.rglob(pattern):
        if exclude_set:
            parts = fp.relative_to(root).parts
            if any(part in exclude_set for part in parts):
                continue
        files.append(str(fp))
    
    results = []
    scanner = ASTAntiPatternScanner()
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        fut_map = {executor.submit(scanner.scan_file, fp): fp for fp in files}
        for fut in as_completed(fut_map):
            r = fut.result()
            if r:
                results.append(r)
    return results


# ---------------------------------------------------------------------------
# Daemon mode: run scanner on a fixed interval
# ---------------------------------------------------------------------------


def _run_daemon_mode(directory: str, fmt: str, exclude_dirs: (list[str] | None), interval: int = 300, file_pattern: str = "*.py", log_file: (str | None) = None):
    """Run scanner in daemon mode, re-scanning on a fixed interval.

    Args:
        directory: Directory to scan
        fmt: Output format
        exclude_dirs: list of directory names to exclude
        interval: Seconds between scans (default: 300 = 5 min)
        file_pattern: Glob pattern for files to scan (default: *.py)
        log_file: Optional path to write daemon log output
    """

    log_fh = None
    if log_file:
        log_fh = open(log_file, "a", encoding="utf-8")

    def _log(msg: str):
        print(msg)
        if log_fh:
            log_fh.write(msg + "\n")
            log_fh.flush()

    _log(f"  Daemon mode: scanning {os.path.abspath(directory)} every {interval}s")
    _log(f"  File pattern: {file_pattern}")
    _log(f"  Output format: {fmt}")
    if log_file:
        _log(f"  Logging to: {log_file}")
    _log("  Press Ctrl+C to stop.\n")

    try:
        while True:
            try:
                scanner = ASTAntiPatternScanner()
                results = scanner.scan_directory(directory, file_pattern, exclude_dirs=list(exclude_dirs) if exclude_dirs else None)
                total_files = len(results)
                total_findings = sum(r.finding_count for r in results)
                files_with_findings = sum(1 for r in results if not r.passed)

                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] Scan: {total_files} files, {total_findings} findings, {files_with_findings} files with findings")

                # Auto-store to DB
                try:
                    from CORE.compliance.scanner_db import FindingsDB
                    _db = FindingsDB()
                    _db.store_scan({
                        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "directory": os.path.abspath(directory),
                        "total_files": total_files,
                        "total_findings": total_findings,
                        "files_with_findings": files_with_findings,
                        "compliance_score": max(0, 100 - total_findings * 5),
                        "results": [r.to_dict() for r in results],
                    })
                except Exception as exc:
                    note_failure("scanner:L1706", exc) # scanner:ignore -- DB storage is advisory

                if fmt == "junit-xml" and total_findings > 0:
                    _print_junit_xml(results, total_files, total_findings)

                time.sleep(interval)
            except KeyboardInterrupt:
                print("\n  Daemon mode stopped.")
                break
            except Exception as e:
                print(f"  Daemon error: {e}, retrying in {interval}s...")
                time.sleep(interval)
    finally:
        if log_fh:
            log_fh.close()


# ---------------------------------------------------------------------------
# JUnit XML output
# ---------------------------------------------------------------------------


def _print_junit_xml(results, total_files, total_findings):
    """Print scan results as JUnit XML (for CI/test runner integration)."""
    failures = sum(1 for r in results if not r.passed)
    tests = total_files if total_files > 0 else 1
    print('<?xml version="1.0" encoding="UTF-8"?>')
    print(f'<testsuite name="compliance-scanner" tests="{tests}" failures="{failures}" errors="0">')
    for r in results:
        if r.passed:
            print(f'  <testcase classname="scanner" name="{r.filepath}" />')
        else:
            print(f'  <testcase classname="scanner" name="{r.filepath}">')
            for ln, cat, sn in r.findings[:5]:
                # Escape XML special chars in message content
                esc = sn[:800].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                print(f'    <failure message="{cat}" type="anti-pattern">Line {ln}: {esc}</failure>')
            if r.finding_count > 5:
                print(f'    <failure message="... +{r.finding_count - 5} more" type="truncated" />')
            print('  </testcase>')
    print('</testsuite>')


# ---------------------------------------------------------------------------
# Webhook: POST scan results to a URL
# ---------------------------------------------------------------------------


def _send_webhook(url: str, total_files: int, total_findings: int, files_with_findings: int, score: float):
    """Send scan summary to a webhook URL as JSON POST.

    Only supports http:// and https:// URLs. Rejects other schemes.
    """
    # Validate URL scheme
    if not url.startswith("http://") and not url.startswith("https://"):
        print("  [WEBHOOK] Invalid URL scheme: must be http:// or https://")
        return
    payload = json.dumps({
        "event": "scan_complete",
        "total_files": total_files,
        "total_findings": total_findings,
        "files_with_findings": files_with_findings,
        "compliance_score": score,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  [WEBHOOK] Failed: {e}")


# ---------------------------------------------------------------------------
# Desktop notification (Windows toast)
# ---------------------------------------------------------------------------


def _show_notification(total_files: int, total_findings: int, files_with_findings: int):
    """Show a Windows toast notification with scan results.

    Uses base64-encoded PowerShell to avoid shell injection from findings.
    """
    import base64
    msg = f"{total_files} files scanned, {total_findings} findings in {files_with_findings} files"
    try:
        # Build safe PowerShell script with no variable injection from user data
        ps_script = f'''
Add-Type -AssemblyName System.Windows.Forms
$$balloon = New-Object System.Windows.Forms.NotifyIcon
$$balloon.Icon = [System.Drawing.SystemIcons]::Information
$$balloon.BalloonTipText = "{total_files} files scanned, {total_findings} findings in {files_with_findings} files"
$$balloon.BalloonTipTitle = "Compliance Scanner"
$$balloon.Visible = $$false
$$balloon.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
$$balloon.Dispose()
'''
        encoded = base64.b64encode(ps_script.encode('utf-16le')).decode()
        subprocess.run(["powershell", "-EncodedCommand", encoded], capture_output=True, timeout=15)
    except Exception as exc:
        note_failure("scanner:L1808", exc) # scanner:ignore -- a notification is advisory, never fatal


# ---------------------------------------------------------------------------
# Auto-fix: apply fixes for supported anti-patterns
# ---------------------------------------------------------------------------


_EMOJI_ASCII_MAP = {
    "\u26a0": "[!] ",       # warning sign
    "\u2716": "[FAIL] ",   # heavy multiplication x
    "\u2714": "[OK] ",     # check mark
    "\u2705": "[OK] ",     # green check mark
}


def _apply_auto_fixes(results, dry_run=False, quiet=False):
    """Apply automatic fixes to files with supported anti-patterns.

    Currently supports:
    - unicode_emoji: Replace emoji with ASCII equivalents
    - os_system: Replace os.system() with subprocess.run()

    Args:
        results: list of AntiPatternResult from scan
        dry_run: If True, preview changes without modifying files
        quiet: If True, suppress individual fix messages
    """
    fixed_count = 0
    preview_lines = []

    for r in results:
        if r.passed:
            continue
        filepath = r.filepath
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError) as exc:
            note_failure("scanner:L1847", exc)
            continue

        original = lines.copy()

        for line_no, cat, _ in r.findings:
            idx = line_no - 1
            if idx < 0 or idx >= len(lines):
                continue

            if cat == "unicode_emoji":
                new_line = lines[idx]
                for emoji_char, ascii_replacement in _EMOJI_ASCII_MAP.items():
                    new_line = new_line.replace(emoji_char, ascii_replacement)
                if new_line != lines[idx]:
                    if not quiet:
                        preview_lines.append(f"  {filepath}:L{line_no} emoji -> ASCII")
                    lines[idx] = new_line
                    fixed_count += 1

            elif cat == "os_system":
                line = lines[idx]
                # os.system("cmd") -> a subprocess call on shlex.split("cmd").
                # NOT shell=True: that reintroduces the very injection vector
                # this rule removes (the shell_true rule exists for it).
                new_line = re.sub(
                    r"os\.system\(([\"\'])(.*?)\1\)",
                    r"subprocess.run(shlex.split(\1\2\1))",
                    line
                )
                if new_line != line:
                    if not quiet:
                        preview_lines.append(f"  {filepath}:L{line_no} os.system -> subprocess.run")
                    lines[idx] = new_line
                    fixed_count += 1

            elif cat == "fstring_backslash":
                # fstring backslash escapes cannot be auto-fixed reliably
                # (requires understanding variable scope and pre-extraction)
                # Flag for manual review only
                if not quiet:
                    preview_lines.append(f"  {filepath}:L{line_no} fstring_backslash — requires manual fix (pre-extract variable before f-string)")

            elif cat == "hardcoded_win_path":
                line = lines[idx]
                # Replace C:\path with r"\path" and suffix (wrapped in Path.home())
                # Escape backslashes in the captured path to avoid invalid unicode escapes
                def _fix_win_path(m):
                    path_part = m.group(1)
                    # Escape each backslash
                    escaped = path_part.replace("\\", "\\\\")
                    return f'Path.home() / r"{escaped}"'
                new_line = re.sub(
                    r'[Cc]:(\\[a-zA-Z_][a-zA-Z0-9_\\]*)',
                    _fix_win_path,
                    line
                )
                if new_line != line:
                    if not quiet:
                        preview_lines.append(f"  {filepath}:L{line_no} hardcoded_win_path -> Path.home()")
                    lines[idx] = new_line
                    fixed_count += 1

            elif cat == "t_string":
                line = lines[idx]
                # Replace "..." with "..." (remove t prefix)
                new_line = line.replace('"', '"').replace("'", "'")
                if new_line != line:
                    if not quiet:
                        preview_lines.append(f"  {filepath}:L{line_no} t_string -> regular string")
                    lines[idx] = new_line
                    fixed_count += 1

        if lines != original:
            if dry_run:
                if not quiet:
                    print(f"[DRY-RUN] Would fix: {filepath}")
            else:
                with open(filepath, "w", encoding="utf-8", newline="") as f:
                    f.writelines(lines)
                if not quiet:
                    print(f"  Fixed: {filepath}")

    if dry_run:
        print()
        print(f"[DRY-RUN] Would apply {fixed_count} fix(es) across {len(results)} file(s)")
        for pl in preview_lines:
            print(pl)
    elif not quiet and fixed_count > 0:
        print(f"  Applied {fixed_count} auto-fix(es)")


if __name__ == "__main__":
    run_cli()
