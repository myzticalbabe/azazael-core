"""Validation edge case tests for DirectMemory methods."""

import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, "src")


def _setup():
    old_env = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    tmpdir = Path(tempfile.mkdtemp())
    os.environ["AZAZAEL_MEMORY_DIR"] = str(tmpdir)
    from CORE import direct_memory
    direct_memory.DB_PATH = direct_memory._get_db_path()
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


def test_empty_source_rejected():
    """Empty source should raise ValueError."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()
        try:
            dm.record("test", {"v": 1}, source="")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
        dm.end()
    finally:
        _teardown(tmpdir, old_env)


def test_long_source_rejected():
    """Overly long source should raise ValueError."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()
        try:
            dm.record("test", {"v": 1}, source="x" * 200)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
        dm.end()
    finally:
        _teardown(tmpdir, old_env)


def test_empty_event_type_rejected():
    """Empty event_type should raise ValueError."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()
        try:
            dm.record("", {"v": 1})
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
        dm.end()
    finally:
        _teardown(tmpdir, old_env)


def test_invalid_severity_rejected():
    """Invalid severity string should raise ValueError."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()
        try:
            dm.record("test", {"v": 1}, severity="invalid_severity_level")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
        dm.end()
    finally:
        _teardown(tmpdir, old_env)


def test_purge_nonexistent_session():
    """Purging a non-existent session should not crash."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()  # Init DB first
        dm.end()
        deleted = DirectMemory.purge_session("nonexistent_session_xyz")
        assert deleted == 0, f"Expected 0 deleted, got {deleted}"
    finally:
        _teardown(tmpdir, old_env)


def test_merge_nonexistent_session():
    """Merging from non-existent session should not crash."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()  # Init DB first
        dm.end()
        moved = DirectMemory.merge_sessions("no_source", "no_target")
        assert moved == 0, f"Expected 0 moved, got {moved}"
    finally:
        _teardown(tmpdir, old_env)


def test_export_empty_session():
    """Export events for non-existent session returns empty array."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()  # Init DB first
        dm.end()
        result = DirectMemory.replay_session("no_such_session_abc")
        assert isinstance(result, list)
        assert len(result) == 0
    finally:
        _teardown(tmpdir, old_env)


def test_session_duration_empty():
    """Session duration for non-existent session returns zero duration."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()  # Init DB first
        dm.end()
        result = DirectMemory.session_duration("no_such_session_abc")
        assert result["duration_s"] == 0
        assert result["events"] == 0
    finally:
        _teardown(tmpdir, old_env)


def test_link_invalid_path():
    """Linking with invalid filepath should not crash."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory
        dm = DirectMemory()
        result = dm.link_audit_session("", "uuid-test", mapping_type="manual")
        assert result is False, "Linking with empty path should fail"
        dm.end()
    finally:
        _teardown(tmpdir, old_env)
