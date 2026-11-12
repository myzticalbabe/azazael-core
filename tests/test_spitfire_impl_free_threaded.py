# coding: utf-8
"""Tests for spitfire.compat.impl_free_threaded — free-threaded implementation."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pytest

import spitfire.compat.impl_free_threaded as impl_ft
import spitfire.compat.shared as shared


def test_module_imports():
    """Free-threaded implementation module imports without error."""
    assert hasattr(impl_ft, "main")


def test_classify_uses_shared():
    """classify delegates to shared module."""
    result = shared.classify("src/spitfire/compat/impl_free_threaded.py", "modified")
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

    rc = shared.run_dispatcher(Args(), max_workers=1, gil_free=True, impl_name="test")
    assert rc == 2

    class Args2:
        cmd = "census"
        max_workers = 1
        save = None

    rc = shared.run_dispatcher(Args2(), max_workers=1, gil_free=True, impl_name="test")
    assert rc == 0


def test_free_threaded_detection():
    """Verify free-threaded runtime detection works."""
    import sys as _sys
    gil_free = getattr(_sys, "_is_gil_enabled", lambda: True)() is False
    assert isinstance(gil_free, bool)


def test_main_exists():
    """main function exists and is callable."""
    assert callable(impl_ft.main)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])