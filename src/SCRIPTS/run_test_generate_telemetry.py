#!/usr/bin/env python
"""Standalone test runner for generate_telemetry.py — RULE 0Z-F telemetry export.

Usage:
    python azazael/src/SCRIPTS/run_test_generate_telemetry.py
    python azazael/src/SCRIPTS/run_test_generate_telemetry.py -v
"""
import sys
import os
import json
import tempfile
import unittest.mock
from pathlib import Path

TEST_DIR = os.path.dirname(__file__)
SCRIPTS_DIR = os.path.join(TEST_DIR, '..', 'scripts')
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import generate_telemetry
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

passed = 0
failed = 0


def test(name, func):
    global passed, failed
    try:
        func()
        print(f"  [PASS] {name}")
        passed += 1
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        failed += 1


def make_temp_dir():
    d = tempfile.mkdtemp()
    return Path(d)


# --- Tests ---

def test_output_format():
    """telemetry.json contains required fields with correct types."""
    out = make_temp_dir() / "telemetry.json"
    saved = sys.argv
    sys.argv = ["gen.py", "--output", str(out)]
    try:
        code = generate_telemetry.main()
        assert code == 0, f"exit code {code}"
        assert out.exists(), "file not created"
        data = json.loads(out.read_text(encoding="utf-8"))
        for key in ("parallel_batches", "sequential_violations", "edit_count", "compliance_ratio", "total_spawns", "timestamp", "status"):
            assert key in data, f"missing key: {key}"
        assert isinstance(data["parallel_batches"], int)
        assert isinstance(data["sequential_violations"], int)
    finally:
        sys.argv = saved


def test_custom_output_path():
    """--output flag writes to custom nested path."""
    custom = make_temp_dir() / "nested" / "deep" / "custom.json"
    saved = sys.argv
    sys.argv = ["gen.py", "--output", str(custom)]
    try:
        code = generate_telemetry.main()
        assert code == 0, f"exit code {code}"
        assert custom.exists(), "custom path not created"
        data = json.loads(custom.read_text(encoding="utf-8"))
        assert "parallel_batches" in data
    finally:
        sys.argv = saved


def test_fallback_import_error():
    """When PreActionGate unavailable, writes baseline with gate_unavailable status."""
    out = make_temp_dir() / "fallback.json"

    # Mock the import to raise ImportError
    with unittest.mock.patch(
        'CORE.compliance.pre_action_gate.get_pre_action_gate',
        side_effect=ImportError("Simulated missing gate")
    ):
        # Re-import generate_telemetry fresh so mock takes effect
        if 'generate_telemetry' in sys.modules:
            del sys.modules['generate_telemetry']
        import importlib
        gen2 = importlib.import_module('generate_telemetry')
        importlib.reload(gen2)

        saved = sys.argv
        sys.argv = ["gen.py", "--output", str(out)]
        try:
            code = gen2.main()
            assert code == 0, f"exit code {code}"
            data = json.loads(out.read_text(encoding="utf-8"))
            assert data["status"] == "gate_unavailable", f"expected gate_unavailable, got {data['status']}"
            assert "error" in data
        finally:
            sys.argv = saved


def test_valid_json():
    """Always produces valid JSON."""
    out = make_temp_dir() / "valid.json"
    saved = sys.argv
    sys.argv = ["gen.py", "--output", str(out)]
    try:
        code = generate_telemetry.main()
        assert code == 0
        json.loads(out.read_text(encoding="utf-8"))
    finally:
        sys.argv = saved


if __name__ == "__main__":
    print("=" * 60)
    print("  RULE 0Z-F GENERATE TELEMETRY TESTS (standalone)")
    print("=" * 60)
    test("Output format with required fields", test_output_format)
    test("Custom nested --output path", test_custom_output_path)
    test("ImportError fallback writes gate_unavailable", test_fallback_import_error)
    test("Always produces valid JSON", test_valid_json)
    total = passed + failed
    print(f"\n  {passed}/{total} tests passed")
    sys.exit(0 if failed == 0 else 1)
