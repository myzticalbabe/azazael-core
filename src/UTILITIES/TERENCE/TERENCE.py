# coding: utf-8
"""
TERENCE - Error Resolution Protocol
Auto-fixes common Python errors with pattern matching and fixes.

Usage:
    >>> from TERENCE import TERENCE
    >>> t = TERENCE()
    >>> t.VERSION
    '1.0.0'
"""

import os
import re
import json
import time
import contextlib
from datetime import datetime
from pathlib import Path
# from typing import Dict, List, Optional  # removed — all names have native equivalents
from dataclasses import dataclass
from collections import defaultdict
import argparse
from typing import List
import json as _json

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = ["TERENCE", "Fix", "FixStats"]


@dataclass
class Fix:
    """Single fix for an error."""

    file: str
    line: int
    error_type: str
    original: str
    fixed: str
    description: str


@dataclass
class FixStats:
    """Fix statistics."""

    files_scanned: int = 0
    errors_found: int = 0
    fixes_applied: int = 0
    fixes_previewed: int = 0
    errors_remaining: int = 0
    duration_ms: float = 0.0


class TERENCE:
    """Error Resolution Protocol."""

    VERSION = "1.0.0"

    # TERENCE must never scan or edit its own package. Several rule
    # DESCRIPTIONS contain the very pattern they detect (for example
    # "Use 'is None' instead of '== None'"), so an unrestricted training
    # pass rewrote its own rule table on 2026-09-11: the None rule's
    # description became "Use 'is None' instead of 'is None'", the
    # assertEquals pattern lost its 's', and the list(d.keys()) rule was
    # shortened. The guard below makes that class of self-corruption
    # impossible, and it is enforced at three points (scan, quick check,
    # and apply) so no queue or replay can bypass it.
    SELF_DIR = Path(__file__).resolve().parent

    def __init__(self):
        self.fixes: list[Fix] = []
        self.stats = FixStats()
        # Append the corrected comparison rules (2026-09-11).
        self.FIXES = list(self.FIXES) + list(self.EXTRA_RULES)

    # === ERROR PATTERNS AND FIXES ===

    FIXES = [
        # Bare except -> Specific exception
        {
            "pattern": r"except\s*:",
            "fix": "except Exception as e:",
            "desc": "Replace bare except with Exception handling",
        },
        # print(x) -> logging
        {
            "pattern": r"print\s*\(\s*(?![\"'])",
            "match": "Pattern matching - Python 3.10+ 'print('",
            "fix": "# print(  # Debug",
            "desc": "Comment out print statement",
        },
        # Tab indentation -> Spaces
        {
            "pattern": r"^\t+",
            "match": "Pattern matching - Python 3.10+ tab",
            "fix": "    ",
            "desc": "Replace tabs with 4 spaces",
        },
        # Return outside function -> Pass statement
        {
            "pattern": r"^\s+return\s+(?![^,\s])",
            "match": "Pattern matching - Python 3.10+ return",
            "fix": "# return  # TODO: Move to function",
            "desc": "Comment out return outside function",
        },
        # Continue/break outside loop
        {
            "pattern": r"^\s+(continue|break)\s*$",
            "match": "Pattern matching - Python 3.10+ continue/break",
            "fix": "# TODO: Move to loop",
            "desc": "Comment out continue/break outside loop",
        },
        # Empty try block (2026-09-10: the old template emitted a bare
        # `except: pass` — the tool taught the anti-pattern. Emit a
        # logging handler instead.)
        {
            "pattern": r"try\s*:\s*\n\s*(?:pass|\n|$)",
            "match": "Pattern matching - Python 3.10+ try: pass",
            "fix": "try:\n    # TODO: handle expected errors narrowly\nexcept Exception as e:\n    print(f\"unhandled: {e}\")",
            "desc": "Add exception handling to empty try",
        },
        # assert without message
        {
            "pattern": r"assert\s+(?![^\(]*,)",
            "match": "Pattern matching - Python 3.10+ assert x",
            "fix": "assert ",
            "desc": "Add message to assert",
        },
        # isinstance with single type
        {
            "pattern": r"isinstance\s*\(\s*\w+\s*,\s*\w+\s*\)",
            "match": "isinstance(x, str)",
            "fix": "isinstance(x, (str,))",
            "desc": "Use tuple for isinstance",
        },
        # None comparison
        {
            "pattern": r"==\s*None|!=\s*None",
            "match": "== None",
            "fix": "is None",
            "desc": "Use 'is None' instead of '== None'",
        },
        # eval() usage
        {
            "pattern": r"eval\s*\(",
            "match": "eval(",
            "fix": "# eval(  # Security risk",
            "desc": "Comment out eval()",
        },
        # exec() usage
        {
            "pattern": r"exec\s*\(",
            "match": "exec(",
            "fix": "# exec(  # Security risk",
            "desc": "Comment out exec()",
        },
        # __import__() usage
        {
            "pattern": r"__import__\s*\(",
            "match": "__import__(",
            "fix": "# __import__(  # Use importlib",
            "desc": "Replace __import__ with importlib",
        },
        # Wildcard import
        {
            "pattern": r"import\s+\*\s*$",
            "match": "import *",
            "fix": "# import specific modules  # Avoid wildcard",
            "desc": "Replace wildcard import",
        },
        # from x import *
        {
            "pattern": r"from\s+\w+\s+import\s+\*",
            "match": "from x import *",
            "fix": "# from x import specific  # Avoid wildcard",
            "desc": "Replace wildcard from import",
        },
        # Open without context manager
        {
            "pattern": r"open\s*\(\s*[^,\)]+\s*\)",
            "match": "open(file)",
            "fix": "with open(file) as f:",
            "desc": "Use context manager for open()",
        },
        # .get() without default
        {
            "pattern": r"\.get\s*\(\s*\)",
            "match": ".get()",
            "fix": ".get(key, default)",
            "desc": "Add default to dict.get()",
        },
        # list() on dict.keys()
        {
            "pattern": r"list\s*\(\s*\.keys\s*\(\s*\)\s*\)",
            "match": "list(d.keys())",
            "fix": "list(d)  # Or use list(d)",
            "desc": "Simplify list(d.keys())",
        },
        # str(list) instead of ', '.join()
        {
            "pattern": r"str\s*\(\s*\w+\.keys\s*\(\s*\)\s*\)",
            "match": "str(d.keys())",
            "fix": "', '.join(d.keys())",
            "desc": "Use join for string conversion",
        },
        # Using + for string concatenation
        {
            "pattern": r"\"[^\"]*\"\s*\+\s*\"[^\"]*\"",
            "match": "concat example",
            "fix": "use concatenation",
            "desc": "Consider string formatting",
        },
        # for i in range(len(x))
        {
            "pattern": r"for\s+\w+\s+in\s+range\s*\(\s*len\s*\(\s*\w+\s*\)\s*\)\s*:",
            "match": "for i in range(len(x))",
            "fix": "for i, item in enumerate(x):",
            "desc": "Use enumerate() instead of range(len())",
        },
        # x[0] == y instead of x[0] is y for None check
        {
            "pattern": r"==\s*True|==\s*False",
            "match": "== True",
            "fix": "is True",
            "desc": "Use 'is' for boolean comparison",
        },
        # Using mutable default arguments
        {
            "pattern": r"def\s+\w+\s*\([^)]*=\s*\[\s*\]",
            "match": "def f(x=[])",
            "fix": "def f(x=None):\n    if x is None:\n        x = []",
            "desc": "Replace mutable default argument",
        },
        # Shadowing builtins
        {
            "pattern": r"^(?!def\s)\s*(list|dict|str|int|float|bool|set|sum|min|max)\s*=",
            "match": "list = ",
            "fix": "# list =  # Shadowing builtin",
            "desc": "Don't shadow builtins",
        },
        # Magic numbers
        {
            "pattern": r"(?<![.\w])(\d{3,})(?![.\w])",
            "match": "1000",
            "fix": "# 1000  # TODO: Replace magic number",
            "desc": "Replace magic numbers with constants",
        },
        # Long lines
        {
            "pattern": r".{121,}",
            "match": "Long line",
            "fix": "# Line too long - split",
            "desc": "Split long lines (>120 chars)",
        },
        # Deprecated assert_ equals
        {
            "pattern": r"assertEquals|assert_",
            "match": "assertEquals",
            "fix": "assertEqual",
            "desc": "Use modern unittest methods",
        },
    ]

    # === RULE SAFETY (2026-09-11) ==========================================
    # Two defects made this tool net-destructive and made its training signal
    # actively wrong:
    #
    # 1. FIX SEMANTICS. `_scan_file` stored `original = line.strip()[:50]` and
    #    `_apply_verified` did a whole-line `.replace()`. So a rule whose
    #    replacement was a COMMENT (e.g. "print(" -> "# print(  # Debug")
    #    deleted the entire statement. py_compile still passed, so
    #    `_record_outcome` recorded success=True and the loop REINFORCED code
    #    deletion. "Magic numbers" (any 3+ digit number) and "Long lines" had
    #    the same shape, which would have gutted large parts of the repo.
    #
    # 2. INVERTED COMPARISONS. `==\s*None|!=\s*None` with a single fix of
    #    "is None" turned `x is not None` into `x is None` — the exact opposite
    #    test. Same for `== True|== False` -> "is True".
    #
    # Fixes: (a) fixes are SPAN substitutions, never whole-line replacements;
    # (b) every auto-appliable rule has an explicit correct substitution in
    # RULE_SUBSTITUTIONS; (c) rules that delete/comment out code are advisory
    # only (UNSAFE_RULES) and can never be auto-applied; (d) `_apply_verified`
    # refuses any edit that removes all executable content from the line.
    UNSAFE_RULES = {
        "Comment out print statement",
        "Comment out return outside function",
        "Comment out continue/break outside loop",
        "Comment out eval()",
        "Comment out exec()",
        "Replace __import__ with importlib",
        "Replace wildcard import",
        "Replace wildcard from import",
        "Don't shadow builtins",
        "Replace magic numbers with constants",
        "Split long lines (>120 chars)",
        "Use context manager for open()",
        "Add default to dict.get()",
        "Replace mutable default argument",
        "Use enumerate() instead of range(len())",
        "Consider string formatting",
        "Replace tabs with 4 spaces",
        "Add message to assert",
        "Add exception handling to empty try",
        # --- Second safety pass (2026-09-11): rules that LOOK equivalent but
        # are not. Auto-applying any of these would change behaviour while
        # verifying clean, which is the same failure mode as the comment-out
        # rules - just quieter.
        #
        # `str(d.keys())` yields "dict_keys([...])" whereas ', '.join(d.keys())
        # yields the joined contents: a different STRING, not a modernization.
        "Use join for string conversion",
        # `except:` also catches KeyboardInterrupt/SystemExit; narrowing to
        # `except Exception:` changes which failures are caught. The work order
        # (Class A5) requires logging + a DLQ counter at those sites, not a
        # silent mechanical narrowing.
        "Replace bare except with Exception handling",
        # `x == True` is TRUE for x = 1 and `x == False` is TRUE for x = 0,
        # but `is True`/`is False` require the singletons. The correct rewrite
        # is `if x:` / `if not x:`, which needs judgement. Not equivalent.
        "Use 'is' for boolean comparison",
        "Use 'is False' for boolean comparison",
        # Pure no-op: `isinstance(x, T)` and `isinstance(x, (T,))` are
        # identical at runtime. 202 of these would otherwise dominate the
        # applied-count and dilute the learning signal with non-events.
        "Use tuple for isinstance",
    }

    # Explicit, CORRECT substitution for every rule worth auto-applying. The
    # value REPLACES the rule's own pattern+fix, so a rule that matched too
    # broadly (or whose replacement inverted the logic) is narrowed here.
    RULE_SUBSTITUTIONS = {
        "Replace bare except with Exception handling":
            (r"except\s*:", "except Exception:"),
        # narrowed to equals-only: the original pattern also matched `is not None`
        # and rewrote it to `is None`, inverting the condition.
        "Use 'is None' instead of '== None'":
            (r"==\s*None", "is None"),
        # narrowed to True-only: the original also matched `== False`.
        "Use 'is' for boolean comparison":
            (r"==\s*True", "is True"),
        "Use modern unittest methods":
            (r"\bassertEquals\b", "assertEqual"),
        "Simplify list(d.keys())":
            (r"list\s*\(\s*(\w+)\.keys\s*\(\s*\)\s*\)", r"list(\1)"),
    }

    # Cases the original rule set got WRONG by omission, added as their own
    # rules so neither comparison can be rewritten to its opposite.
    EXTRA_RULES = [
        {
            "pattern": r"!=\s*None",
            "fix": "is not None",
            "desc": "Use 'is not None' instead of '!= None'",
        },
        {
            "pattern": r"==\s*False",
            "fix": "is False",
            "desc": "Use 'is False' for boolean comparison",
        },
    ]

    @classmethod
    def _is_self(cls, file_path) -> bool:
        """True when `file_path` lives inside TERENCE's own package."""
        try:
            return cls.SELF_DIR in Path(file_path).resolve().parents
        except OSError as exc:
            note_failure("TERENCE:L366", exc)
            return False

    @staticmethod
    def _gutted(original, fixed):
        """True when an edit removes ALL executable content from the line.

        `x == None` -> `x is None` shortens the line but keeps code (allowed).
        `print(x)` -> `# print(...)` and `x = 500` -> `# 1000` leave nothing
        but a comment (refused). This is the guard that stops py_compile from
        rewarding code deletion.
        """
        def _code(s):
            i = s.find('#')
            return (s[:i] if i >= 0 else s).strip()
        oc, fc = _code(original or ""), _code(fixed or "")
        return bool(oc) and not fc

    # === MAIN FUNCTIONS ===

    def scan(self) -> FixStats:
        """Scan files for errors that can be fixed."""
        print("=" * 70)
        print("  TERENCE - Error Resolution Protocol")
        print("=" * 70)
        print(f"Start: {datetime.now().isoformat()}")
        print()

        start = time.time()
        self.fixes = []
        files_scanned = 0

        # Determine root from env var or auto-detect
        root = os.environ.get("AZAZAEL_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        # Sweep stale Terence lock files (crash litter; live locks are
        # held open by running processes, and we only reap older than 1h).
        try:
            import time as _t
            for _lf in Path(root).rglob("*.terence.lock"):
                try:
                    if _t.time() - _lf.stat().st_mtime > 3600:
                        _lf.unlink()
                except OSError as exc:
                    note_failure("TERENCE:L408", exc)
        except Exception as exc:
            note_failure("TERENCE:L410", exc)
        target_folders = [
            os.path.join(root, "ORCHESTRATION"),
            os.path.join(root, "src"),
            os.path.join(root, "UTILITIES"),
            os.path.join(root, "META"),
            os.path.join(root, "CORE"),
        ]

        for folder in target_folders:
            p = Path(folder)
            if not p.exists():
                continue

            folder_name = p.name
            file_count = 0
            fix_count = 0

            for f in p.rglob("*.py"):
                if any(s in str(f) for s in ["__pycache__", ".git", ".venv"]):
                    continue
                if self._is_self(f):
                    continue

                file_count += 1
                fixes = self._scan_file(f)
                fix_count += len(fixes)
                self.fixes.extend(fixes)

            # files_scanned is a reported statistic; the local was never
            # accumulated, so every run reported 0 files scanned (fixed
            # 2026-10-03 alongside the --status persistence).
            files_scanned += file_count

            if fix_count > 0:
                print(f"{folder_name}: {file_count} files, {fix_count} potential fixes")

        self.stats.files_scanned = files_scanned
        self.stats.errors_found = len(self.fixes)
        self.stats.duration_ms = (time.time() - start) * 1000

        self._print_summary()
        self._persist_state()

        return self.stats

    def _scan_file(self, file_path: Path) -> list[Fix]:
        """Scan a single file for fixable errors.

        SPAN semantics (2026-09-11): `original` is the MATCHED SPAN and `fixed`
        is that span after substitution - never the whole stripped line. The
        old form replaced an entire statement whenever a rule matched, so a
        rule whose replacement was a comment silently deleted code that still
        compiled (and was therefore recorded as a success).

        Strict UTF-8 decode: `errors="ignore"` silently dropped undecodable
        bytes, so a 'fix' could rewrite a file it had mis-read.
        """
        fixes = []
        # Single-file guard as well as the loop guard in scan(): this is the
        # one function that turns a file into Fix objects, so refusing here
        # makes self-corruption impossible even for direct callers.
        if self._is_self(file_path):
            return fixes

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (UnicodeDecodeError, OSError) as exc:
            note_failure("TERENCE:L479", exc)
            return fixes

        lines = content.split("\n")
        for line_num, line in enumerate(lines, 1):
            for fix_rule in self.FIXES:
                desc = fix_rule["desc"]
                pattern, repl = self.RULE_SUBSTITUTIONS.get(
                    desc, (fix_rule["pattern"], fix_rule["fix"]))
                try:
                    m = re.search(pattern, line)
                except re.error as exc:
                    note_failure("TERENCE:L490", exc)
                    continue
                if not m or not m.group(0):
                    continue
                try:
                    fixed_span = re.sub(pattern, repl, m.group(0))
                except re.error as exc:
                    note_failure("TERENCE:L496", exc)
                    continue
                if fixed_span == m.group(0):
                    continue  # genuine no-op; do not report it as a fix
                unsafe = desc in self.UNSAFE_RULES
                fixes.append(
                    Fix(
                        file=str(file_path),
                        line=line_num,
                        error_type=desc,
                        original=m.group(0),
                        fixed=fixed_span,
                        description=("[ADVISORY] " if unsafe else "") + desc,
                    )
                )
        return fixes

    def _print_summary(self):
        """Print scan summary."""
        print()
        print("=" * 70)
        print("  SCAN SUMMARY")
        print("=" * 70)
        print()
        print(f"Errors Found:    {self.stats.errors_found}")
        print(f"Duration:        {self.stats.duration_ms:.0f}ms")
        print()

        if self.fixes:
            # Group by error type
            by_type = defaultdict(int)
            for fix in self.fixes:
                by_type[fix.error_type] += 1

            print("Top Fixable Issues:")
            for error_type, count in sorted(
                by_type.items(), key=lambda x: x[1], reverse=True
            )[:10]:
                print(f"  {error_type}: {count}")

            print()
            print("Preview fixes with: terence.preview()")
            print("Apply fixes with: terence.fix_all()")

        print()
        print("=" * 70)

    def preview(self, file_filter: (str | None) = None,
                show_withheld: int = 0) -> int:
        """Preview the APPLY QUEUE - the fixes `fix_all(apply=True)` would try.

        Honesty split (2026-10-03). The old version iterated `self.fixes` and
        printed the raw pattern+fix for EVERY finding, including rules in
        UNSAFE_RULES that `fix_all` filters out BEFORE the loop. That made the
        preview actively misleading: it showed 475 `Use tuple for isinstance`
        rows whose displayed substitution was `isinstance(v, str)` ->
        `isinstance(x, (str,))` - which RENAMES the variable (NameError if ever
        applied) for a change that is a pure runtime no-op. A reader sized the
        run from the preview and concluded corrupting edits were queued, when
        the real queue was 31. A preview must describe what WOULD be applied,
        so this now mirrors `fix_all`: applicable findings in full, advisory
        findings only summarised. `show_withheld=N` prints the first N advisory
        spans for rule auditing, clearly labelled as never-applied.
        """
        print()
        print("=" * 70)
        print("  FIX PREVIEW (apply queue)")
        print("=" * 70)
        print()

        applicable = [f for f in self.fixes
                      if f.error_type not in self.UNSAFE_RULES]
        withheld = [f for f in self.fixes
                    if f.error_type in self.UNSAFE_RULES]
        if file_filter:
            applicable = [f for f in applicable if file_filter in f.file]
            withheld = [f for f in withheld if file_filter in f.file]

        count = 0
        for fix in applicable:
            count += 1
            print(f"File: {fix.file}")
            print(f"Line: {fix.line}")
            print(f"Type: {fix.error_type}")
            print(f"Original: {fix.original}")
            print(f"Fixed:    {fix.fixed}")
            print("-" * 50)

        self.stats.fixes_previewed = count
        print()
        print(f"Total fixes in the apply queue: {count}")

        if withheld:
            print()
            print(f"WITHHELD (advisory-only, NEVER auto-applied): {len(withheld)}")
            _by_type = defaultdict(int)
            for fix in withheld:
                _by_type[fix.error_type] += 1
            for _type, _n in sorted(_by_type.items(),
                                    key=lambda kv: kv[1], reverse=True)[:10]:
                print(f"  {_type}: {_n}")
            print("These delete code or are not behaviour-preserving. Their raw")
            print("patterns are NOT shown above because they are not fixes.")
            if show_withheld:
                print()
                print(f"First {show_withheld} withheld spans (audit only):")
                for fix in withheld[:show_withheld]:
                    print(f"  {fix.file}:{fix.line} [{fix.error_type}]")
                    print(f"    rule span: {fix.original} -> {fix.fixed}")

        print("=" * 70)

        return count

    def pattern_scores(self, min_samples: int = 5) -> dict:
        """Per-pattern success rates from Terence's own thought rows.

        This is Terence's evolution: patterns that verify poorly are
        selected against. Returns {error_type: {n, ok, rate}}. Fails
        open (empty dict) when the store is unreachable.
        """
        try:
            import sys as _sys
            _src = os.path.join(os.path.dirname(
                os.path.abspath(__file__)), "..", "..")
            if _src not in _sys.path:
                _sys.path.insert(0, _src)
            from CORE.direct_memory import DirectMemory
            rows = DirectMemory().fetch_thoughts(limit=1000)
        except Exception as exc:
            note_failure("TERENCE:L625", exc)
            return {}
        import re as _re
        stats: dict = {}
        for r in rows:
            d = r.get("data", {})
            if isinstance(d, str):
                try:
                    d = _json.loads(d)
                except Exception as exc:
                    note_failure("TERENCE:L635", exc)
                    continue
            if not isinstance(d, dict):
                continue
            if r.get("source") != "Terence" and "auto-fix" not in str(d.get("task", "")):
                continue
            m = _re.match(r"auto-fix (.+) at .+:\d+", str(d.get("task", "")))
            if not m:
                continue
            s = stats.setdefault(m.group(1), {"n": 0, "ok": 0})
            s["n"] += 1
            s["ok"] += 1 if d.get("success") else 0
        # min_samples is a real floor, not decoration. Without it a pattern
        # seen ONCE and failed reports rate=0.0, which reads as "this pattern
        # never works" and gets selected against on a single observation —
        # a false zero that self-reinforces, because selection then stops
        # producing the successes that would raise n back above the floor.
        floor = max(1, int(min_samples))
        return {k: {"n": v["n"], "ok": v["ok"],
                    "rate": round(v["ok"] / v["n"], 3)}
                for k, v in stats.items() if v["n"] >= floor}

    def fix_all(self, apply: bool = False, max_fixes: (int | None) = None) -> int:
        """Apply all fixes (or just count if apply=False).

        Proficiency upgrade (2026-09-10): applied fixes are verified
        (py_compile) with per-file backup + rollback, and every outcome
        is recorded as a thought_process row so the learning loop trains
        on Terence's own successes and failures.

        max_fixes (2026-09-11): bound a training pass. Sweeping an entire
        codebase in one run makes the batch unreviewable and the learning
        signal unreadable; a capped pass keeps each batch attributable.
        """
        print()
        print("=" * 70)
        print("  APPLYING FIXES" if apply else "  WOULD APPLY FIXES")
        print("=" * 70)
        print()

        applied = 0
        skipped = 0

        # Evolution (2026-09-10): select against patterns with a proven
        # bad record (<50% verify over >=5 samples). Logged loudly, never
        # silent — a skipped pattern is a hypothesis, not a verdict.
        _scores = self.pattern_scores() if apply else {}
        _dead = {k for k, v in _scores.items()
                 if v["n"] >= 5 and v["rate"] < 0.5}
        if _dead:
            print("  evolved skip (poor verify history): %s"
                  % sorted(_dead))

        # Advisory rules are filtered BEFORE the loop (2026-09-11). They can
        # never apply, so leaving them queued exhausted a capped training pass
        # on non-attempts (observed: 39 skips, 1 real fix, cap spent).
        # Advisory filter applies to BOTH the dry run and the real run
        # (2026-10-03). The dry run used to iterate every finding and count
        # advisory rows as "would apply", so the same run reported 8,019 in
        # dry-run and 31 in apply mode. A dry run that disagrees with the
        # real run is worse than no dry run.
        advisory = sum(1 for f in self.fixes
                       if f.error_type in self.UNSAFE_RULES)
        if advisory:
            print("  %d advisory findings withheld from apply "
                  "(report-only: they delete code or are not equivalent)"
                  % advisory)
        queue = [f for f in self.fixes
                 if f.error_type not in self.UNSAFE_RULES]

        for fix in queue:
            if apply:
                if fix.error_type in _dead:
                    skipped += 1
                    print(f"[EVOLVED-SKIP] {fix.file}:{fix.line} - {fix.error_type}")
                    continue
                if max_fixes is not None and applied >= max_fixes:
                    print("  training cap reached (%d) - remaining fixes deferred"
                          % max_fixes)
                    break
                ok, reason = self._apply_verified(fix)
                if ok:
                    applied += 1
                    print(f"[APPLIED] {fix.file}:{fix.line} - {fix.error_type}")
                else:
                    skipped += 1
                    print(f"[SKIPPED] {fix.file}:{fix.line} - {reason[:50]}")
                self._record_outcome(fix, ok, reason)
            else:
                print(f"[WOULD FIX] {fix.file}:{fix.line} - {fix.error_type}")
                applied += 1

        self.stats.fixes_applied = applied

        print()
        if apply:
            print(f"Fixes Applied: {applied}")
            print(f"Fixes Skipped: {skipped}")
        else:
            print(f"Total fixes that would be applied: {applied}")
            print("Use fix_all(apply=True) to actually apply")

        print("=" * 70)
        self._persist_state()

        return applied

    def _apply_verified(self, fix) -> tuple:
        """Apply one fix with backup, py_compile verify, and rollback.

        Hardened (2026-09-10): strict UTF-8 gate (the old errors="ignore"
        read silently dropped undecodable bytes on write-back, corrupting
        files it never meant to change), per-file msvcrt lock (two
        Terence processes must not interleave writes), stale-line
        rejection. Returns (ok: bool, reason: str). A fix that breaks
        compilation is reverted and reported, never left half-applied.
        """
        import py_compile
        # Never write TERENCE's own package, whatever queue produced the fix.
        if self._is_self(fix.file):
            return False, "refused: target is TERENCE's own package"
        try:
            with open(fix.file, "rb") as f:
                raw = f.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                note_failure("TERENCE:L761", exc)
                return False, "skipped non-UTF8 file"
            lines = text.split("\n")
            if fix.line > len(lines):
                return False, "line out of range"
            # Advisory-only rules must never mutate code (2026-09-11).
            if fix.error_type in self.UNSAFE_RULES:
                return False, "advisory-only rule (would delete code)"
            if fix.original not in lines[fix.line - 1]:
                return False, "original text drifted - stale fix"
            # Content-loss guard: py_compile passes on a comment, so it cannot
            # tell 'fixed' from 'deleted'. Refuse any edit that leaves no code.
            if self._gutted(fix.original, fix.fixed):
                return False, "refused: edit removes all code from the line"
            backup = text
            lines[fix.line - 1] = lines[fix.line - 1].replace(
                fix.original, fix.fixed, 1)
            try:
                import msvcrt
                _lfh = open(fix.file + ".terence.lock", "w", encoding="utf-8")
                try:
                    msvcrt.locking(_lfh.fileno(), msvcrt.LK_NBLCK, 1)
                    _locked = True
                except (OSError, IOError) as exc:
                    note_failure("TERENCE:L784", exc)
                    _locked = False
            except Exception as exc:
                note_failure("TERENCE:L786", exc)
                _lfh, _locked = None, True
            try:
                if not _locked:
                    return False, "file locked by another Terence"
                with open(fix.file, "w", encoding="utf-8", newline="") as f:
                    f.write("\n".join(lines))
                try:
                    py_compile.compile(fix.file, doraise=True)
                except Exception as ce:
                    note_failure("TERENCE:L795", ce)
                    with open(fix.file, "w", encoding="utf-8", newline="") as f:
                        f.write(backup)
                    return False, "verify failed, rolled back: %s" % str(ce)[:80]
            finally:
                try:
                    if _lfh:
                        _lfh.close()
                    os.remove(fix.file + ".terence.lock")
                except Exception as exc:
                    note_failure("TERENCE:L804", exc)
            return True, "applied+verified"
        except Exception as e:
            note_failure("TERENCE:L807", e)
            return False, str(e)[:80]

    def _record_outcome(self, fix, ok: bool, reason: str) -> None:
        """Record the fix outcome as chain-of-thought training data.

        This is Terence's learning loop: successes teach the patterns
        that verify, failures (stale lines, verify breaks, rollbacks)
        teach what to avoid. DPO pairs them automatically.
        """
        try:
            import sys as _sys
            _src = os.path.join(os.path.dirname(
                os.path.abspath(__file__)), "..", "..")
            if _src not in _sys.path:
                _sys.path.insert(0, _src)
            from CORE.direct_memory import DirectMemory
            DirectMemory().record_thought(
                task="auto-fix %s at %s:%s" % (
                    fix.error_type, fix.file, fix.line),
                reasoning=(
                    "Pattern '%s' matched; applied '%s'. %s." % (
                        fix.original[:120], fix.fixed[:120], reason)),
                actions=["scan", "apply", "verify",
                         "rollback" if not ok else "keep"],
                success=bool(ok),
                source="Terence",
                tags=["terence"],
                session_id="terence-%s" % time.strftime("%Y-%m-%d"),
            )
        except Exception as exc:
            note_failure("TERENCE:L837", exc)

    def _state_path(self) -> Path:
        """Path to the persisted last-run record (logs/terence_last_run.json).

        The repo root, NOT the scan root: `scan()` deliberately roots at `src/`
        (its targets -- CORE, ORCHESTRATION, META, UTILITIES -- live there), but
        the run record belongs beside `logs/anti_pattern_baseline.json` at the
        project root. Using the scan root here wrote to `src/logs/`, a directory
        that did not otherwise exist (caught during the 2026-10-03 runtime run).
        """
        root = os.environ.get(
            "AZAZAEL_ROOT", str(Path(__file__).resolve().parents[3]))
        return Path(root) / "logs" / "terence_last_run.json"

    def _persist_state(self) -> None:
        """Persist this run's stats so `--status` can report a REAL run.

        Defect fixed 2026-10-03: `--status` returned all zeros forever. Stats
        live on the instance and were never written anywhere, and the CLI
        builds a fresh instance per invocation, so `--status` always described
        an object that had just been constructed - not the scan that had just
        found 8,019 findings. Status must describe the last run.
        """
        try:
            p = self._state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": self.VERSION,
                "files_scanned": self.stats.files_scanned,
                "errors_found": self.stats.errors_found,
                "fixes_applied": self.stats.fixes_applied,
                "fixes_previewed": self.stats.fixes_previewed,
                "duration_ms": round(self.stats.duration_ms, 1),
                "apply_queue": sum(
                    1 for f in self.fixes
                    if f.error_type not in self.UNSAFE_RULES),
                "advisory_withheld": sum(
                    1 for f in self.fixes
                    if f.error_type in self.UNSAFE_RULES),
                "when": datetime.now().isoformat(timespec="seconds"),
            }
            p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            note_failure("terence:883", exc)

    def status(self) -> dict:
        """Show the LAST RUN's status.

        Prefers this instance's in-memory stats when it actually ran; otherwise
        falls back to the persisted last run so `--status` is informative
        instead of a fresh set of zeros.
        """
        if (self.stats.errors_found or self.stats.fixes_applied
                or self.stats.fixes_previewed or self.stats.files_scanned):
            return {
                "version": self.VERSION,
                "errors_found": self.stats.errors_found,
                "fixes_applied": self.stats.fixes_applied,
                "fixes_previewed": self.stats.fixes_previewed,
                "duration_ms": self.stats.duration_ms,
                "source": "this-instance",
            }
        data = None
        try:
            p = self._state_path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            note_failure("terence:921", exc)
        if data is not None:
            data["source"] = "last-run"
            return data
        return {
            "version": self.VERSION,
            "errors_found": 0,
            "fixes_applied": 0,
            "fixes_previewed": 0,
            "duration_ms": 0.0,
            "source": "no-run-recorded",
        }

    def fix_syntax_error(self, file_path: str, line_num: int, error_msg: str) -> bool:
        """Fix a specific syntax error."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Common syntax fixes
            if "was never closed" in error_msg:
                # Missing closing bracket
                if "(" in error_msg:
                    lines.append(")\n")
                elif "[" in error_msg:
                    lines.append("]\n")
                elif "{" in error_msg:
                    lines.append("}\n")

            with open(file_path, "w", encoding="utf-8") as f:
                f.writelines(lines)

            return True
        except Exception as exc:
            note_failure("TERENCE:L936", exc)
            return False

    def run_quick_fix(self):
        """Run quick auto-fix on common errors."""
        print("=" * 70)
        print("  TERENCE Quick Fix")
        print("=" * 70)
        print()

        # Scan the local project for quick fixes
        root = os.environ.get("AZAZAEL_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        checked = 0
        for py_file in Path(root).rglob("*.py"):
            if "__pycache__" in str(py_file) or ".git" in str(py_file):
                continue
            if self._is_self(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                issues = []
                if "open(" in content and "with open" not in content:
                    issues.append("open() without context manager")
                if "except:" in content and "except Exception" not in content:
                    issues.append("bare except clause")
                if issues:
                    print(f"  {py_file.name}: {', '.join(issues)}")
                    checked += 1
            except Exception as exc:
                note_failure("TERENCE:L964", exc)

        print()
        print(f"Quick check: {checked} files with potential issues")
        print("Run 'terence.scan()' for full analysis")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="TERENCE - Error Resolution Protocol")
    parser.add_argument("--scan", action="store_true", help="Scan for errors")
    parser.add_argument("--preview", action="store_true", help="Preview fixes")
    parser.add_argument("--fix", action="store_true", help="Apply fixes")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap the number of applied fixes (bounded "
                             "training pass; default: no cap)")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--quick", action="store_true", help="Quick fix check")

    args = parser.parse_args()

    terence = TERENCE()

    if args.scan:
        terence.scan()
    elif args.preview:
        terence.scan()
        terence.preview()
    elif args.fix:
        terence.scan()
        terence.fix_all(apply=True, max_fixes=args.limit)
    elif args.status:
        print(json.dumps(terence.status(), indent=2))
    elif args.quick:
        terence.run_quick_fix()
    else:
        print("TERENCE - Error Resolution Protocol")
        print()
        print("Usage:")
        print("  python TERENCE.py --scan    Scan for errors")
        print("  python TERENCE.py --preview Preview fixes")
        print("  python TERENCE.py --fix     Apply fixes")
        print("  python TERENCE.py --status  Show status")
        print("  python TERENCE.py --quick   Quick fix check")
        print()
        print("Python API:")
        print("  >>> import terence")
        print("  >>> terence.scan()")
        print("  >>> terence.fix_all(apply=True)")


if __name__ == "__main__":
    main()
