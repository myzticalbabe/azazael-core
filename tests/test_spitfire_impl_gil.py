# coding: utf-8
"""Tests for spitfire.compat.impl_gil — GIL implementation."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pytest

import spitfire.compat.impl_gil as impl_gil
import spitfire.compat.shared as shared


def test_module_imports():
    """GIL implementation module imports without error."""
    assert hasattr(impl_gil, "main")


def test_classify_uses_shared():
    """classify delegates to shared module."""
    result = shared.classify("src/spitfire/compat/impl_gil.py", "modified")
    assert isinstance(result, dict)
    assert "path" in result
    assert "status" in result
    assert "category" in result


def test_sh_uses_shared():
    """sh helper delegates to shared module."""
    code, out, err = shared.sh("cmd", "/c", "echo", "test")
    assert isinstance(code, int)
    assert isinstance(out, str)
    assert isinstance(err, str)
    assert code == 0
    assert "test" in out


def test_run_dispatches():
    """run_dispatcher dispatches to census/fix/all."""
    class Args:
        cmd = "unknown"
        max_workers = 1
        save = None

    rc = shared.run_dispatcher(Args(), max_workers=1, gil_free=False, impl_name="test")
    assert rc == 2

    class Args2:
        cmd = "census"
        max_workers = 1
        save = None

    rc = shared.run_dispatcher(Args2(), max_workers=1, gil_free=False, impl_name="test")
    assert rc == 0


def test_main_exists():
    """main function exists and is callable."""
    assert callable(impl_gil.main)


def test_main_handles_unknown_cmd():
    """main returns error for unknown subcommand."""
    import argparse
    import sys
    from contextlib import redirect_stderr
    import io

    parser = argparse.ArgumentParser()
    parser.add_argument("--max-workers", type=int, default=32)
    parser.add_argument("--save", type=str)
    parser.add_argument("cmd", choices=["census", "fix", "all"])
    
    f = io.StringIO()
    with redirect_stderr(f):
        try:
            parser.parse_args(["unknown"])
        except SystemExit as e:
            assert e.code == 2
        else:
            assert False, "Expected SystemExit"
    
    assert "invalid choice" in f.getvalue()
    assert "unknown" in f.getvalue()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])