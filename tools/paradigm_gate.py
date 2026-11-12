#!/usr/bin/env python3
# coding: utf-8
"""Testing-paradigm gate -- Engram #784 standing rule 1, enforced at commit time.

Why this exists: Engram #889 measured that the #784 paradigm (property tests for
pure functions, race tests for shared structures, mutation gates on new modules)
was written down as prose and never enforced. 33 later commits added 13 test
files with zero paradigm techniques. A rule with no gate decays to its examples.

What is checked (rule 1 only -- see the limits below):
    A commit that ADDS a new top-level function to src/ must also stage at least
    one test file that contains a Hypothesis property (``@given``).

Limits, stated rather than hidden:
    * It is a presence heuristic. It cannot tell a pure function from an impure
      one, so it cannot prove a function NEEDED a property test.
    * Rules 2 (race tests) and 3 (mutation gate) are not checked here: they need
      a running suite or mutation tool, which is too slow for a pre-commit hook.
    * Advisory by default. Set PARADIGM_GATE_STRICT=1 to make violations exit 1.

Usage:
    python tools/paradigm_gate.py --check        # staged index (pre-commit)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

_NEW_DEF = re.compile(r"^\+def ([A-Za-z_][A-Za-z0-9_]*)\(")
_PROPERTY_MARK = re.compile(r"@given\b")


def new_functions(diff_text: str) -> list[str]:
    """Names of top-level functions ADDED by a unified diff (``+def name(``)."""
    return [m.group(1) for line in diff_text.splitlines()
            if (m := _NEW_DEF.match(line))]


def evaluate(new_funcs_by_file: dict[str, list[str]],
             staged_test_texts: dict[str, str]) -> list[str]:
    """Pure rule-1 verdict. Returns violation messages (empty = pass)."""
    added = {path: names for path, names in new_funcs_by_file.items() if names}
    if not added:
        return []
    has_property = any(_PROPERTY_MARK.search(text)
                       for text in staged_test_texts.values())
    if has_property:
        return []
    return [f"{path}: adds {', '.join(names)} but no staged test file carries a "
            f"Hypothesis property (@given). Rule 1 (Engram #784)."
            for path, names in sorted(added.items())]


def _git(args: list[str]) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True,
                            encoding="utf-8", check=False, timeout=120)
    if result.returncode != 0:
        # A failed git call must not read as an empty index (a silent pass).
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def staged_inputs() -> tuple[dict[str, list[str]], dict[str, str]]:
    """Read the INDEX (not the worktree), matching staged_scanner_gate's rule."""
    names = _git(["diff", "--cached", "--name-only", "--diff-filter=AM"]).splitlines()
    funcs: dict[str, list[str]] = {}
    tests: dict[str, str] = {}
    for path in names:
        if path.startswith("src/") and path.endswith(".py"):
            diff = _git(["diff", "--cached", "-U0", "--", path])
            funcs[path] = new_functions(diff)
        if path.startswith("tests/") and path.endswith(".py"):
            tests[path] = _git(["show", f":{path}"])
    return funcs, tests


def main(argv: list[str]) -> int:
    if "--check" not in argv:
        print(__doc__)
        return 0
    try:
        funcs, tests = staged_inputs()
    except RuntimeError as exc:
        print(f"[paradigm] ERROR: {exc}")
        return 2
    violations = evaluate(funcs, tests)
    strict = os.environ.get("PARADIGM_GATE_STRICT") == "1"
    if not violations:
        print("[paradigm] rule 1 ok")
        return 0
    label = "BLOCKED" if strict else "ADVISORY"
    for line in violations:
        print(f"[paradigm] {label}: {line}")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
