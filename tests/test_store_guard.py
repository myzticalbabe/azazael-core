"""Regression guard: production store must be unwritable from tests."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_local_memory_store_refuses_default_under_pytest(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "guard")
    # conftest sets AZAZAEL_MEMORY_DIR globally, which the guard honours;
    # clear it so this test exercises the fail-closed branch (stale since
    # the guard learned the MEMORY_DIR exemption).
    monkeypatch.delenv("AZAZAEL_MEMORY_DIR", raising=False)
    from CORE.local_memory import LocalMemoryStore
    with pytest.raises(RuntimeError):
        LocalMemoryStore()


def test_local_memory_store_allows_explicit_db_path(tmp_path):
    from CORE.local_memory import LocalMemoryStore
    s = LocalMemoryStore(db_path=str(tmp_path / "t.db"))
    assert s.db_path.endswith("t.db")


def test_direct_memory_refuses_default_under_pytest(monkeypatch):
    # #974/#978: the mirrored DirectMemory guard must fail closed too.
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "guard")
    monkeypatch.delenv("AZAZAEL_MEMORY_DIR", raising=False)
    from CORE.direct_memory import DirectMemory
    with pytest.raises(RuntimeError):
        DirectMemory()


def test_direct_memory_allows_explicit_db_path(tmp_path):
    from CORE.direct_memory import DirectMemory
    dm = DirectMemory(db_path=str(tmp_path / "t.db"))
    assert dm._db_path.endswith("t.db")
