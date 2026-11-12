"""
Codebuff Compliance Enforcement Harness
Validates code quality and compliance requirements before processing.
Provided by inference model consensus.
"""


__all__ = [
    "ComplianceError",
    "check_no_debug_prints",
    "check_no_hardcoded_secrets",
    "check_no_todo_comments",
    "check_required_docstrings",
    "enforce_compliance",
    "self_test",
]


import ast
import os
import sys
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class ComplianceError(RuntimeError):
    """Exception raised when compliance checks fail. Halts execution immediately."""


def check_no_hardcoded_secrets(source_code: str, filename: str = "<unknown>") -> None:
    """Check 1: Prohibit hardcoded secrets, passwords, and API keys."""
    secret_patterns = [
        'password', 'passwd', 'pwd', 'secret', 'api_key', 'apikey',
        'token', 'credential', 'private_key', 'access_key'
    ]
    lines = source_code.split('\n')
    for line_num, line in enumerate(lines, 1):
        line_lower = line.lower()
        for pattern in secret_patterns:
            if pattern in line_lower:
                stripped = line.strip()
                if stripped.startswith('#') or stripped.startswith('//'):
                    continue
                if '=' in line and ('"' in line or "'" in line or '+' in line):
                    raise ComplianceError(
                        f"SECURITY VIOLATION: Potential hardcoded secret '{pattern}' "
                        f"found in {filename}:{line_num}"
                    )


def check_required_docstrings(source_code: str, filename: str = "<unknown>") -> None:
    """Check 2: Require docstrings for all public classes and functions."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        raise ComplianceError(f"SYNTAX ERROR: Cannot parse {filename}: {e}")
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith('_'):
                continue
            docstring = ast.get_docstring(node)
            if not docstring:
                raise ComplianceError(
                    f"DOCUMENTATION ERROR: Missing docstring for '{node.name}' "
                    f"in {filename}:{node.lineno}"
                )


def check_no_debug_prints(source_code: str, filename: str = "<unknown>") -> None:
    """Check 3: Prohibit debug print statements in production code."""
    lines = source_code.split('\n')
    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        if 'print(' in line or 'print (' in line:
            raise ComplianceError(
                f"CODE QUALITY: Debug print statement found in {filename}:{line_num}"
            )


def check_no_todo_comments(source_code: str, filename: str = "<unknown>") -> None:
    """Check 4: Prohibit TODO/FIXME comments in production code."""
    lines = source_code.split('\n')
    for line_num, line in enumerate(lines, 1):
        stripped = line.strip().lower()
        if 'todo:' in stripped or 'fixme:' in stripped or 'hack:' in stripped:
            raise ComplianceError(
                f"CODE QUALITY: TODO/FIXME comment found in {filename}:{line_num}"
            )


def enforce_compliance(filepath: str | None = None, source_code: str | None = None) -> None:
    """Main enforcement function. Runs all checks. Raises ComplianceError on violation."""
    if source_code is None:
        if filepath is None:
            raise ValueError("Either filepath or source_code must be provided")
        filepath = str(Path(filepath).resolve())
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")
        with open(filepath, 'r', encoding='utf-8') as f:
            source_code = f.read()
        filename = filepath
    else:
        filename = "<provided source>"
    check_no_hardcoded_secrets(source_code, filename)
    check_required_docstrings(source_code, filename)
    check_no_debug_prints(source_code, filename)
    check_no_todo_comments(source_code, filename)


def self_test() -> bool:
    """Run self-tests to verify the harness works correctly."""
    print("Running enforcement harness self-tests...\n")
    # Test 1: Valid code should pass
    print("Test 1: Valid code should pass all checks...")
    valid_code = '''def greet(name):
    """Return a greeting message."""
    return f"Hello, {name}"

class Calculator:
    """A simple calculator class."""
    def add(self, a, b):
        """Add two numbers."""
        return a + b
'''
    try:
        enforce_compliance(source_code=valid_code)
        print("  PASS: Valid code accepted\n")
    except ComplianceError as e:
        print(f"  FAIL: {e}\n")
        return False
    # Test 2: Missing docstring should fail
    print("Test 2: Missing docstring should raise ComplianceError...")
    invalid_code_no_docstring = '''def calculate(x, y):
    return x * y
'''
    try:
        enforce_compliance(source_code=invalid_code_no_docstring)
        print("  FAIL: Should have raised ComplianceError\n")
        return False
    except ComplianceError as e:
        print(f"  PASS: Correctly raised error: {e}\n")
    # Test 3: Hardcoded secret should fail
    print("Test 3: Hardcoded secret should raise ComplianceError...")
    invalid_code_secret = 'api_key = "sk-1234567890abcdef"\n'
    try:
        enforce_compliance(source_code=invalid_code_secret)
        print("  FAIL: Should have raised ComplianceError\n")
        return False
    except ComplianceError as e:
        print(f"  PASS: Correctly raised error: {e}\n")
    # Test 4: Debug print should fail
    print("Test 4: Debug print should raise ComplianceError...")
    invalid_code_print = '''def process():
    print("debug info")
'''
    try:
        enforce_compliance(source_code=invalid_code_print)
        print("  FAIL: Should have raised ComplianceError\n")
        return False
    except ComplianceError as e:
        print(f"  PASS: Correctly raised error: {e}\n")
    # Test 5: TODO comment should fail
    # A3 EXEMPT (2026-09-10 scope, re-verified 2026-10-28): the TODO below is
    # an intentional test fixture proving enforce_compliance rejects TODOs —
    # not tech debt. Do not "fix" it; Test 5 fails if it is removed.
    print("Test 5: TODO comment should raise ComplianceError...")
    invalid_code_todo = '''def handle():
    # TODO: fix this later
    pass
'''
    try:
        enforce_compliance(source_code=invalid_code_todo)
        print("  FAIL: Should have raised ComplianceError\n")
        return False
    except ComplianceError as e:
        print(f"  PASS: Correctly raised error: {e}\n")
    # Test 6: ComplianceError is RuntimeError subclass
    print("Test 6: ComplianceError should be RuntimeError subclass...")
    if issubclass(ComplianceError, RuntimeError):  # type: ignore[reportUnnecessaryIsInstance]
        print("  PASS: ComplianceError is RuntimeError subclass\n")
    else:
        print("  FAIL: ComplianceError is not RuntimeError subclass\n")
        return False
    print("All self-tests passed!")
    return True


if __name__ == "__main__":
    success = self_test()
    sys.exit(0 if success else 1)
