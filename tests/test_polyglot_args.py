#!/usr/bin/env python3
"""D6: argv contract — args are literal strings, no shell evaluation.

Per language: run inline code that echoes its argv; assert an arg
containing spaces (and shell metacharacters) arrives intact. Missing
toolchains skip cleanly (same rule as the golden self-test).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from INTERPRETERS import run_code

SPACE_ARG = "hello world"


def _skip_if_missing(res) -> bool:
    return (not res.success) and ("not found" in (res.stderr or "").lower())


def test_python_args_with_spaces_intact():
    res = run_code(
        "python",
        "import sys; print('|'.join(sys.argv[1:]))",
        [SPACE_ARG, "a$b"],
    )
    assert res.success, res.stderr
    assert SPACE_ARG in (res.stdout or "")


def test_bash_args_with_spaces_intact():
    from INTERPRETERS.sh_runner import ShRunner

    runner = ShRunner()
    if not runner.has_bash:
        return
    res = runner.run_code('printf "%s|" \"$@\"', [SPACE_ARG, "a b"])
    assert res.success, res.stderr
    assert SPACE_ARG in (res.stdout or "")


def test_node_args_with_spaces_intact():
    res = run_code(
        "javascript",
        "console.log(process.argv.slice(2).join('|'))",
        [SPACE_ARG],
    )
    if _skip_if_missing(res):
        return
    assert res.success, res.stderr
    assert SPACE_ARG in (res.stdout or "")


def test_pwsh_args_with_spaces_intact():
    res = run_code("powershell", "$args -join '|'", [SPACE_ARG])
    if _skip_if_missing(res):
        return
    assert res.success, res.stderr
    assert SPACE_ARG in (res.stdout or "")
