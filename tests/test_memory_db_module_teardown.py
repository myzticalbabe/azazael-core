"""Guard: a module-scoped DB redirect must be restored UNCONDITIONALLY.

Error #197 (2026-10-03). `tests/test_session_mappings.py` redirected the module
global `CORE.direct_memory.DB_PATH` in `setup_module()` and "restored" it in
`teardown_module()` like this:

    if _orig_db:
        dm_mod.DB_PATH = _orig_db

The guard was on the SAVED value, and the saved value was read from
`DirectMemory.__dict__` — where `DB_PATH` does not exist — so `_orig_db` was
always `''`, the restore never ran, and the module was left pointing at a
`tempfile.mktemp` path that teardown then deleted. Every later test in the
process that used the shared `get_memory_store()` store failed, in a DIFFERENT
file from the one that leaked (the four `test_thought_corpus_integrity`
"typed writer" tests, which is how it was finally found).

`conftest._restore_memory_db_globals` cannot catch this: it snapshots and
restores around EACH TEST, which deliberately preserves a redirect a test made
for itself, and it has no view of a module-scoped setup/teardown pair. So this
file makes the contract checkable by AST:

* `_redirected_globals` finds any `setup_module()` that WRITES one of
  conftest's covered path globals.
* `_restored_globals` finds the globals `teardown_module()` assigns at its TOP
  LEVEL — an assignment nested under `if`/`try`/`with`/`for`/`while` does not
  count, because that is exactly the shape that leaked.
* `test_every_module_scoped_db_redirect_is_restored_unconditionally` fails on
  any test module that redirects without an unconditional restore.
* `test_the_scan_catches_the_historical_defect` guards the guard with the real
  buggy code and its fixed form.
"""

import ast
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent


def _conftest():
    module = sys.modules.get("conftest")
    if module is None:  # pragma: no cover - pytest imports conftest first
        import conftest as module  # type: ignore[no-redef]
    return module


def _covered_globals() -> set[str]:
    """Every path global conftest already knows how to recompute/restore."""
    conftest = _conftest()
    covered: set[str] = set()
    for _mod_name, _resolver, attr in conftest._MEMORY_DB_RESOLVERS:
        covered.add(attr)
    for _mod_name, _reloader, attrs in conftest._MEMORY_DB_RELOADERS:
        covered.update(attrs)
    return covered


def _written_attributes(node: ast.AST) -> set[str]:
    """Attribute names this subtree WRITES (`x.DB_PATH = ...`), not reads."""
    written: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store):
            written.add(sub.attr)
    return written


def _top_level_writes(function: ast.FunctionDef) -> set[str]:
    """Attribute names assigned directly in the function body.

    Deliberately does NOT walk into nested statements: a restore inside an
    `if`/`try`/`with`/`for`/`while` is conditional, and a conditional restore
    is the whole defect.
    """
    written: set[str] = set()
    for stmt in function.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Attribute):
                    written.add(target.attr)
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            if isinstance(stmt.target, ast.Attribute):
                written.add(stmt.target.attr)
    return written


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _module_redirect_offenders(tree: ast.Module, covered: set[str]) -> list[str]:
    """Covered globals a module redirects but does not unconditionally restore."""
    fns = _functions(tree)
    setup = fns.get("setup_module")
    if setup is None:
        return []
    redirected = _written_attributes(setup) & covered
    if not redirected:
        return []
    teardown = fns.get("teardown_module")
    restored = _top_level_writes(teardown) if teardown else set()
    return sorted(redirected - restored)


def test_every_module_scoped_db_redirect_is_restored_unconditionally():
    covered = _covered_globals()
    offenders: dict[str, list[str]] = {}
    for path in sorted(_TESTS.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        missing = _module_redirect_offenders(tree, covered)
        if missing:
            offenders[path.name] = missing
    assert not offenders, (
        "these test modules redirect a module-scoped DB path global in "
        "setup_module() but do not restore it with an UNCONDITIONAL assignment "
        "in teardown_module(); a restore nested under `if`/`try` leaks the "
        "redirect (and any deleted temp path) into every later test: %s"
        % offenders
    )


_HISTORICAL_DEFECT = '''
import tempfile
from pathlib import Path
_orig_db = ""
def setup_module():
    global _orig_db
    import CORE.direct_memory as dm_mod
    _orig_db = str(dm_mod.DirectMemory.__dict__.get("DB_PATH", ""))
    dm_mod.DB_PATH = Path(tempfile.mktemp(suffix="_test.db"))
def teardown_module():
    import CORE.direct_memory as dm_mod
    if Path(str(dm_mod.DB_PATH)).exists():
        Path(str(dm_mod.DB_PATH)).unlink()
    if _orig_db:
        dm_mod.DB_PATH = _orig_db
'''

_FIXED_FORM = '''
import tempfile
from pathlib import Path
_orig_db = None
def setup_module():
    global _orig_db
    import CORE.direct_memory as dm_mod
    _orig_db = dm_mod.DB_PATH
    dm_mod.DB_PATH = Path(tempfile.mktemp(suffix="_test.db"))
def teardown_module():
    import CORE.direct_memory as dm_mod
    if Path(str(dm_mod.DB_PATH)).exists():
        Path(str(dm_mod.DB_PATH)).unlink()
    dm_mod.DB_PATH = _orig_db
'''


def test_the_scan_catches_the_historical_defect():
    """Guard the guard: the real bug must be flagged, and its fix must not be."""
    assert _module_redirect_offenders(
        ast.parse(_HISTORICAL_DEFECT), {"DB_PATH"}) == ["DB_PATH"]
    assert _module_redirect_offenders(
        ast.parse(_FIXED_FORM), {"DB_PATH"}) == []


def test_the_scan_ignores_reads_and_uncovered_globals():
    """A read (`_orig = dm.DB_PATH`) is not a redirect, and an uncovered name
    is not conftest's problem."""
    reads_only = ast.parse(
        "import CORE.direct_memory as dm\n"
        "def setup_module():\n"
        "    x = dm.DB_PATH\n"
        "def teardown_module():\n"
        "    pass\n"
    )
    assert _module_redirect_offenders(reads_only, {"DB_PATH"}) == []
    uncovered = ast.parse(
        "import somemod\n"
        "def setup_module():\n"
        "    somemod.SOMETHING_ELSE = 1\n"
        "def teardown_module():\n"
        "    pass\n"
    )
    assert _module_redirect_offenders(uncovered, {"DB_PATH"}) == []
