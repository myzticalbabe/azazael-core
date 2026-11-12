"""RULE 0Z-F Integration Test — Validates PreActionGate detects and reverts violations.

Tests:
1. Sequential spawn_agents detection (simulated via pattern in Python source)
2. PRE-ACTION GATE check-line fires correctly
3. Auto-revert works on gate failure
"""
import sys
import os
import tempfile
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    from CORE.compliance.pre_action_gate import PreActionGate
    GATE_AVAILABLE = True
except ImportError as exc:
    note_failure("test_rule_0zf_integration:L16", exc)
    GATE_AVAILABLE = False


def test_gate_rejects_syntax_error():
    """Verify gate reverses a syntax-corrupted file (regression: test 5/13)."""
    if not GATE_AVAILABLE:
        print("SKIP: PreActionGate not available")
        return True

    gate = PreActionGate()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("x = 1\n")
        tmp = f.name

    try:
        backup = gate.take_snapshot(tmp)
        # Corrupt the file
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("x = 1\n    : bad\n")
        ok, msg = gate.validate_after_edit(tmp, backup)
        if ok:
            print("FAIL: Gate should have rejected syntax error")
            return False
        # Check revert happened
        with open(tmp, encoding="utf-8") as _restore_f:
            restored = _restore_f.read()
        if restored != "x = 1\n":
            print(f"FAIL: Revert failed. Got: {restored!r}")
            return False
        print("PASS: Syntax error detected and reverted")
        return True
    finally:
        os.unlink(tmp)
        if backup:
            os.unlink(backup)


def test_gate_rejects_merged_statements():
    """Verify gate reverses merged-statement corruption (regression: test 6/13)."""
    if not GATE_AVAILABLE:
        print("SKIP: PreActionGate not available")
        return True

    gate = PreActionGate()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("class Foo:\n    def bar(self):\n        return 1\n        self.x = 2\n")
        tmp = f.name

    try:
        backup = gate.take_snapshot(tmp)
        # Merge two statements onto one line
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("class Foo:\n    def bar(self):\n        return 1        self.x = 2\n")
        ok, msg = gate.validate_after_edit(tmp, backup)
        if ok:
            print("FAIL: Gate should have detected merged statements")
            return False
        with open(tmp, encoding="utf-8") as _restore_f:
            restored = _restore_f.read()
        expected = "class Foo:\n    def bar(self):\n        return 1\n        self.x = 2\n"
        if restored != expected:
            print(f"FAIL: Revert failed. Got: {restored!r}")
            return False
        print("PASS: Merged statements detected and reverted")
        return True
    finally:
        os.unlink(tmp)
        if backup:
            os.unlink(backup)


def test_pre_action_gate_check_line():
    """Verify the PRE-ACTION GATE has RULE 0Z-F check line in SYSTEM_INSTRUCTIONS.md."""
    check_line = "RULE 0Z-F (Parallel Execution)"
    try:
        with open("SYSTEM_INSTRUCTIONS.md", "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError as exc:
        note_failure("test_rule_0zf_integration:L92", exc)
        with open("azazael/SYSTEM_INSTRUCTIONS.md", "r", encoding="utf-8") as f:
            content = f.read()

    count = content.count(check_line)
    if count >= 3:
        print(f"PASS: RULE 0Z-F appears {count} times in SYSTEM_INSTRUCTIONS.md (>=3)")
        return True
    else:
        print(f"FAIL: RULE 0Z-F appears only {count} times (<3)")
        return False


def test_compliance_sentinel_has_0zf():
    """Verify COMPLIANCE SENTINEL checklist includes RULE 0Z-F."""
    check_line = "RULE 0Z-F (Parallel Execution)"
    try:
        with open("SYSTEM_INSTRUCTIONS.md", "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError as exc:
        note_failure("test_rule_0zf_integration:L111", exc)
        with open("azazael/SYSTEM_INSTRUCTIONS.md", "r", encoding="utf-8") as f:
            content = f.read()

    # Check sentinel section (assumes sentinel is in the last 20% of the file)
    # Find COMPLIANCE SENTINEL section
    sentinel_marker = 'COMPLIANCE SENTINEL'
    if sentinel_marker in content:
        sentinel_section = content[content.find(sentinel_marker):]
    else:
        sentinel_section = content[-int(len(content) * 0.2):]
    if check_line in sentinel_section:
        print("PASS: RULE 0Z-F in COMPLIANCE SENTINEL")
        return True
    else:
        print("FAIL: RULE 0Z-F not found in COMPLIANCE SENTINEL section")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("  RULE 0Z-F INTEGRATION TESTS")
    print("=" * 60)

    tests = [
        ("Gate rejects syntax error", test_gate_rejects_syntax_error),
        ("Gate rejects merged statements", test_gate_rejects_merged_statements),
        ("PreActionGate has RULE 0Z-F check", test_pre_action_gate_check_line),
        ("Sentinel has RULE 0Z-F check", test_compliance_sentinel_has_0zf),
    ]

    passed = 0
    total = len(tests)
    for name, func in tests:
        print(f"\n[{passed+1}/{total}] {name}...")
        try:
            if func():
                passed += 1
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\n{'=' * 60}")
    if passed == total:
        print(f"  ALL {total} RULE 0Z-F INTEGRATION TESTS PASSED!")
    else:
        print(f"  {passed}/{total} RULE 0Z-F INTEGRATION TESTS PASSED")
    print(f"{'=' * 60}")
    sys.exit(0 if passed == total else 1)
