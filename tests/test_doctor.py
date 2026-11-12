"""Tests for doctor.py — self-diagnostic module."""

import sys, os, tempfile
from pathlib import Path

sys.path.insert(0, "src")


def _setup():
    old_env = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    tmpdir = Path(tempfile.mkdtemp())
    os.environ["AZAZAEL_MEMORY_DIR"] = str(tmpdir)
    from CORE import direct_memory
    direct_memory.DB_PATH = direct_memory._get_db_path()
    dm = direct_memory.DirectMemory()
    dm.record("doctor_test", {"v": 1})
    dm.end()
    return tmpdir, old_env


def _teardown(tmpdir, old_env):
    if old_env:
        os.environ["AZAZAEL_MEMORY_DIR"] = old_env
    else:
        os.environ.pop("AZAZAEL_MEMORY_DIR", None)
    import shutil
    try:
        shutil.rmtree(str(tmpdir))
    except Exception:
        pass


def test_check_db_integrity():
    tmpdir, old_env = _setup()
    try:
        from CORE.doctor import check_db_integrity
        result = check_db_integrity()
        assert result.get("ok") is True, f"Integrity check failed: {result}"
    finally:
        _teardown(tmpdir, old_env)


def test_get_db_size():
    tmpdir, old_env = _setup()
    try:
        from CORE.doctor import get_db_size
        size = get_db_size()
        assert "total_mb" in size
        assert size["total_mb"] >= 0
    finally:
        _teardown(tmpdir, old_env)


def test_check_env_vars():
    from CORE.doctor import check_env_vars
    warnings = check_env_vars()
    assert isinstance(warnings, list)


def test_run_all():
    tmpdir, old_env = _setup()
    try:
        from CORE.doctor import run_all
        report = run_all(quick=True)
        assert "checks" in report
        assert "sessions" in report["checks"]
        assert "db_size" in report["checks"]
    finally:
        _teardown(tmpdir, old_env)


def test_cmd_verify():
    tmpdir, old_env = _setup()
    try:
        from CORE.doctor import cmd_verify
        report = cmd_verify()
        assert isinstance(report, dict)
    finally:
        _teardown(tmpdir, old_env)
