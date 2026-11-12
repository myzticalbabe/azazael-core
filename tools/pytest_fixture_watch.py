"""Name the test that CHANGES a fixture's state, for aggregate-only failures.

The recurring shape in this suite, measured four times now (Errors #193, #194,
#196, #197): a file passes alone and fails inside the aggregate run, because a
test in ANOTHER file -- or an earlier test in the same file -- leaves shared
state changed. File-level bisection cannot find it, because in every *prefix* of
the order the state is still intact; the change needs a long enough run.

So this instrument watches a value instead of a file: give it a dotted attribute
path on a fixture (``ncm.clusters``, ``state.phase``, ``state.completed_natures``)
and it reports the first test after which that value differs from the previous
observation -- which is the mutator, by construction.

Usage
-----
    PYTHONPATH=tools FIXTURE_WATCH="ncm.clusters,state.phase" \
        python -m pytest tests/test_cross_nature_orchestration.py \
        -p pytest_fixture_watch

Every watched path is resolved against each fixture value a test received
(``item.funcargs``), so any fixture that owns the path is covered without naming
it. Values are compared by a cheap summary (``list(37)``, ``{a,b}``, ``repr``),
because the point is to catch a *change*, not to snapshot the object.

The log goes to ``$FIXTURE_WATCH_LOG`` or ``logs/fixture_watch.log``. The probe
is read-only and never raises.
"""

from __future__ import annotations

import os
import sys
from typing import Any

_WATCH_ENV = "FIXTURE_WATCH"
_LOG_ENV = "FIXTURE_WATCH_LOG"
_DEFAULT_LOG = os.path.join("logs", "fixture_watch.log")

_last: dict[tuple[str, str, int], str] = {}


def _paths() -> list[str]:
    raw = os.environ.get(_WATCH_ENV) or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _log_path() -> str:
    return os.environ.get(_LOG_ENV) or _DEFAULT_LOG


def _write(line: str) -> None:
    try:
        path = _log_path()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        print("pytest_fixture_watch: write failed", file=sys.stderr)


def _resolve(root: Any, path: str) -> Any:
    current = root
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def _summary(value: Any) -> str:
    if isinstance(value, (set, frozenset)):
        return "{%s}" % ",".join(sorted(str(v) for v in value)[:6])
    try:
        return "%s(%d)" % (type(value).__name__, len(value))
    except TypeError:
        return repr(value)[:80]


def pytest_runtest_teardown(item: Any, nextitem: Any) -> None:  # noqa: ARG001
    watched = _paths()
    if not watched:
        return
    try:
        fixtures = dict(getattr(item, "funcargs", None) or {})
    except Exception:
        return
    seen: set[tuple[str, int]] = set()
    for _fixture_name, value in fixtures.items():
        for path in watched:
            target = _resolve(value, path)
            if target is None:
                continue
            key = (path, id(target))
            if key in seen:
                continue  # same object exposed by two fixtures
            seen.add(key)
            summary = _summary(target)
            previous = _last.get(key)
            if previous is None:
                _write("INITIAL %s: %s = %s"
                       % (getattr(item, "nodeid", "?"), path, summary))
            elif previous != summary:
                _write("CHANGE  after %s: %s: %s -> %s"
                       % (getattr(item, "nodeid", "?"), path, previous, summary))
            _last[key] = summary


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    try:
        if not _paths():
            _write("not armed: set %s to a comma-separated list of fixture "
                   "attribute paths" % _WATCH_ENV)
    except Exception:
        pass
