"""Coverage for the "cached a path from the environment" defect (Error #196).

`AZAZAEL_MEMORY_DIR` is read at IMPORT time by several modules, which store the
answer in a module global:

    CORE/direct_memory.py:73     DB_PATH = _get_db_path()
    CORE/record_session.py       DB_PATH
    CORE/MemoryBus.py            _MEMORYBUS_DB_PATH
    CORE/cd3_exporter.py         _DB_DIR, DB_PATH
    CORE/backup.py               _backup_base, BACKUP_DIR

A test that redirects the env var and then deletes its temp directory leaves
those globals pointing into a deleted path, so every later `DirectMemory()` in
the process dies with `unable to open database file` -- in a DIFFERENT file from
the one that redirected. `tests/conftest.py` therefore restores them after every
test through each module's own resolver (`_restore_memory_db_globals`).

That list was hand-written, and a hand-written list is exactly how this recurs:
the next module to cache a path is invisible until something fails three files
later. So this file MAKES the list checkable:

* `_cached_env_globals` finds them by AST: a module-level assignment whose value
  reads `AZAZAEL_MEMORY_DIR`, or calls a function in the same file that does.
* `test_every_cached_path_global_is_restored` fails on any module that does this
  and is not covered by conftest.
* `test_the_restore_list_is_not_stale` fails if a covered module no longer has
  the resolver conftest expects, so entries cannot rot into decoration.
"""

import ast
import os
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
_ENV = "AZAZAEL_MEMORY_DIR"


def _module_name(path: Path) -> str:
    rel = path.relative_to(_SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _mentions_env(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and sub.value == _ENV:
            return True
    return False


def _module_source(mod_name: str) -> Path | None:
    """The source file for a dotted module name, without importing it."""
    rel = Path(*mod_name.split("."))
    for candidate in (_SRC / rel.with_suffix(".py"), _SRC / rel / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _defines(mod_name: str, name: str) -> bool:
    """True if the module source defines `name` at any level (static, no import).

    Exists because `test_the_restore_list_is_not_stale` used to `continue` past
    any module not already in `sys.modules` -- so its verdict depended on what
    another test happened to import, and it passed in isolation while failing
    in a subset run. A coverage check whose coverage is a side effect of test
    order measures the order, not the code.
    """
    src = _module_source(mod_name)
    if src is None:
        return False
    try:
        tree = ast.parse(src.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        note_failure("tests/test_memory_dir_caching.py", exc)
        return False
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
               for n in ast.walk(tree))


def _cached_env_globals(path: Path) -> set[str]:
    """Module-level names that receive a value derived from the env dir."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()

    # Transitive, not one hop. `TELEMETRY_DB_PATH = _get_telemetry_db_dir() / ...`
    # where that helper returns `_get_db_dir()` is env-derived, but the direct
    # reader is two levels down; matching only direct readers missed it and the
    # miss was invisible unless something had imported cd3_exporter first.
    calls: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls[node.name] = {sub.func.id
                                for sub in ast.walk(node)
                                if isinstance(sub, ast.Call)
                                and isinstance(sub.func, ast.Name)}
    env_functions = {name for name, node in
                     ((n.name, n) for n in tree.body
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
                     if _mentions_env(node)}
    changed = True
    while changed:
        changed = False
        for name, callees in calls.items():
            if name not in env_functions and (callees & env_functions):
                env_functions.add(name)
                changed = True
    # Deliberately NOT `if not env_functions: return set()`: a module may read the
    # env var inline at module level with no helper function at all, and that
    # shape is the most dangerous one because it looks like a constant.

    def derives(value: ast.AST, already: set[str]) -> bool:
        if _mentions_env(value):
            return True
        for sub in ast.walk(value):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                if sub.func.id in env_functions:
                    return True
            # Second order counts too: `BACKUP_DIR = _backup_base / "backups"`
            # is just as stale as `_backup_base` once the env var moves.
            if isinstance(sub, ast.Name) and sub.id in already:
                return True
        return False

    def assignments():
        for node in tree.body:
            if isinstance(node, ast.Assign):
                yield node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                yield (node.target,), node.value

    # Fixpoint, because a cache can be derived from a cache.
    cached: set[str] = set()
    changed = True
    while changed:
        changed = False
        for targets, value in assignments():
            if not derives(value, cached):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in cached:
                    cached.add(target.id)
                    changed = True
    return cached


def _scan() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        parts = path.relative_to(_SRC).parts
        if parts and parts[0] == "tests":
            continue
        cached = _cached_env_globals(path)
        if cached:
            found[_module_name(path)] = cached
    return found


def _conftest():
    module = sys.modules.get("conftest")
    if module is None:  # pragma: no cover - pytest imports conftest first
        import conftest as module  # type: ignore[no-redef]
    return module


def test_the_scan_finds_the_known_cachers():
    """The instrument itself is checked, so a silent scan cannot pass."""
    found = _scan()
    for expected in ("CORE.direct_memory", "CORE.record_session",
                     "CORE.MemoryBus", "CORE.backup", "CORE.cd3_exporter"):
        assert expected in found, (expected, sorted(found))
    assert "DB_PATH" in found["CORE.direct_memory"]
    assert {"_backup_base", "BACKUP_DIR"} <= found["CORE.backup"]
    assert {"_DB_DIR", "DB_PATH"} <= found["CORE.cd3_exporter"]


def test_every_cached_path_global_is_restored():
    """A newly cached env-derived global must not be able to hide."""
    covered = set(_conftest().MEMORY_DIR_CACHING_MODULES)
    uncovered = {mod: sorted(attrs) for mod, attrs in _scan().items()
                 if mod not in covered}
    assert not uncovered, (
        "these modules cache a path read from %s at import time and are NOT "
        "restored after each test by conftest._restore_memory_db_globals; add "
        "them (with their resolver) to conftest.MEMORY_DIR_CACHING_MODULES: %s"
        % (_ENV, uncovered))



try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

def test_the_restore_list_is_not_stale():
    """Every covered module must still expose what conftest will call."""
    conftest = _conftest()
    scanned = _scan()
    for mod_name, resolver, attr in conftest._MEMORY_DB_RESOLVERS:
        module = sys.modules.get(mod_name)
        # Static when unimported, runtime when imported: same verdict either way.
        exists = (callable(getattr(module, resolver, None)) if module is not None
                  else _defines(mod_name, resolver))
        assert exists, (
            "%s.%s no longer exists, so conftest cannot recompute %s"
            % (mod_name, resolver, attr))
        assert attr in scanned.get(mod_name, set()), (
            "%s.%s is no longer a cached env-derived global" % (mod_name, attr))
    for mod_name, reloader, attrs in conftest._MEMORY_DB_RELOADERS:
        module = sys.modules.get(mod_name)
        exists = (callable(getattr(module, reloader, None)) if module is not None
                  else _defines(mod_name, reloader))
        assert exists, (
            "%s.%s no longer exists, so conftest cannot restore its cached paths"
            % (mod_name, reloader))
        # The snapshot/restore fixture names every global by hand, so the names
        # must still be the ones the module actually reloads, and must still be
        # env-derived globals the AST scan can see.
        assert attrs, "%s declares no snapshot attributes" % mod_name
        for attr in attrs:
            assert attr in scanned.get(mod_name, set()), (
                "%s.%s is no longer a cached env-derived global, so the "
                "snapshot would restore a name that no longer exists"
                % (mod_name, attr))
