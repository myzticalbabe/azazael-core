"""Regression for the leaked memory-DB path (Error #195, 2026-09-22).
import pytest

pytestmark = pytest.mark.order

`tests/test_cli.py::_setup()` and its siblings redirect the memory store by
mutating BOTH the environment and a module global:

    os.environ["AZAZAEL_MEMORY_DIR"] = str(tmpdir)
    direct_memory.DB_PATH = direct_memory._get_db_path()

...and their `_teardown()` restores only the env var, then `rmtree`s the temp
dir. `CORE.direct_memory.DB_PATH` is computed ONCE at import
(`DB_PATH = _get_db_path()`), so the global stayed pointed at the deleted
directory for the rest of the process and every later `DirectMemory()` died:

    RuntimeError: Failed to initialize database at
    C:\\Users\\clint\\AppData\\Local\\Temp\\tmp_1cocc4t\\memory_store.db:
    unable to open database file

Four files leak this way (`test_cli`, `test_doctor`, `test_concurrency_200`,
`test_perf*`) while the failures surfaced in DIFFERENT files
(`test_cross_domain_integration::test_cd1_record_event`,
`test_worm_smoke::test_every_worm_instantiable`), which is why every one of
them passed when run alone. `conftest._restore_memory_db_globals` is the fix.

The two tests below are ORDER-DEPENDENT ON PURPOSE: `test_1` performs the
legacy-style leak, `test_2` asserts the global was put back. Delete the
conftest fixture and `test_2` fails; that is the point of the file.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Same import bootstrap as its neighbours (test_cli.py, test_doctor.py): this
# file runs standalone as well as inside the suite.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_LEAK_PERFORMED = False


def test_1_a_legacy_style_redirect_does_not_outlive_the_test():
    """Reproduce `test_cli._setup`/`_teardown` — including the missing restore."""
    global _LEAK_PERFORMED

    from CORE import direct_memory

    leak_dir = Path(tempfile.mkdtemp(prefix="azazael_leak_"))
    os.environ["AZAZAEL_MEMORY_DIR"] = str(leak_dir)
    direct_memory.DB_PATH = direct_memory._get_db_path()
    assert Path(str(direct_memory.DB_PATH)).parent == leak_dir

    # The `_teardown` that leaves the module global behind.
    shutil.rmtree(leak_dir, ignore_errors=True)
    assert not leak_dir.exists()

    _LEAK_PERFORMED = True


def test_2_the_next_test_gets_a_usable_database_path():
    """Without the restore fixture `direct_memory.DB_PATH` still points at the pen
    that test_1 deleted, and constructing DirectMemory raises RuntimeError."""
    assert _LEAK_PERFORMED, (
        "test_1 must run first — this file is order-dependent by design")
    assert os.environ.get("AZAZAEL_MEMORY_DIR"), (
        "conftest must pin a session memory dir")

    from CORE import direct_memory

    expected = Path(os.environ["AZAZAEL_MEMORY_DIR"]) / "memory_store.db"
    assert Path(str(direct_memory.DB_PATH)) == expected
    assert Path(str(direct_memory.DB_PATH)).parent.is_dir(), (
        "the restored path must exist — a deleted temp dir is the bug")

    dm = direct_memory.DirectMemory(session_id="isolation_regression")
    dm.end()
