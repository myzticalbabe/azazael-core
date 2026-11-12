"""Regression tests for scripts/false_zero_audit.py.

Why these exist: the first two drafts of that analyzer were WRONG in opposite
directions, and both failures were invisible without fixtures.

  Draft 1 looked for "a key that is read but never written". That never fires on the
  real defect, because __init__ does write the key. It reported a clean tree while a
  known instance of the bug sat in it.

  Draft 2 (after fixing the criterion) had precision 0/1 on the real tree: its single
  confirmed hit was SystemManager.crawlers = {"python": PythonCrawler(), ...}, which is
  correct code. It could not distinguish a metric store from an object registry.

So the tests below pin both sides: the detector must fire on the defect shape, and must
stay silent on look-alikes that are correct.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

false_zero_audit = pytest.importorskip(
    "false_zero_audit",
    reason="scripts/false_zero_audit.py not importable",
)


def audit(source: str) -> tuple[list, list]:
    """Run the analyzer over a source string, returning (confirmed, undecidable)."""
    tree = ast.parse(source)
    confirmed, undecidable = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            ca = false_zero_audit.ClassAudit(node, "<test>")
            ca.run()
            c, u = ca.findings()
            confirmed.extend(c)
            undecidable.extend(u)
    return confirmed, undecidable


# --------------------------------------------------------------------------
# MUST FIRE
# --------------------------------------------------------------------------

THE_REAL_DEFECT = '''
class BusLike:
    def __init__(self):
        self._stats_data = {"subscriptions": 0, "published": 0}
        self._stats = {}

    def subscribe(self, pattern, callback):
        self._stats["subscriptions"] = 7

    def get_stats(self):
        snap = dict(self._stats_data)
        return {"subscriptions": snap.get("subscriptions", 0)}
'''


def test_detects_write_read_store_mismatch() -> None:
    """The exact MemoryBus shape: reported from one store, maintained in another."""
    confirmed, _ = audit(THE_REAL_DEFECT)
    keys = {k for k, _, _, _ in confirmed}
    assert "subscriptions" in keys, (
        f"known defect not detected; confirmed={confirmed}"
    )


def test_detects_dynamic_writer_that_never_receives_the_key() -> None:
    """A dynamic writer exists but is never handed this key, so it cannot update."""
    src = '''
class BusLike:
    def __init__(self):
        self._stats_data = {"subscriptions": 0}

    def _increment(self, key, delta=1):
        self._stats_data[key] = self._stats_data.get(key, 0) + delta

    def get_stats(self):
        snap = dict(self._stats_data)
        return {"subscriptions": snap.get("subscriptions", 0)}
'''
    confirmed, _ = audit(src)
    assert {k for k, _, _, _ in confirmed} == {"subscriptions"}


# --------------------------------------------------------------------------
# MUST STAY SILENT
# --------------------------------------------------------------------------


def test_silent_when_write_lands_where_read_looks() -> None:
    src = '''
class BusLike:
    def __init__(self):
        self._stats_data = {"subscriptions": 0}

    def subscribe(self, pattern, callback):
        self._stats_data["subscriptions"] = 7

    def get_stats(self):
        snap = dict(self._stats_data)
        return {"subscriptions": snap.get("subscriptions", 0)}
'''
    confirmed, _ = audit(src)
    assert confirmed == [], "control fired; detector would flood with false positives"


def test_silent_when_dynamic_writer_receives_the_key() -> None:
    src = '''
class BusLike:
    def __init__(self):
        self._stats_data = {"subscriptions": 0}

    def _increment(self, key, delta=1):
        self._stats_data[key] = self._stats_data.get(key, 0) + delta

    def subscribe(self, pattern, callback):
        self._increment("subscriptions")

    def get_stats(self):
        snap = dict(self._stats_data)
        return {"subscriptions": snap.get("subscriptions", 0)}
'''
    confirmed, _ = audit(src)
    assert confirmed == []


def test_silent_on_object_registry() -> None:
    """The precision-0/1 case: a dict of objects is not a metric store.

    SystemManager.crawlers = {"python": PythonCrawler(), ...} has a "python" key that
    is never reassigned, and a sibling self.components["python"] that is. Draft 2
    reported it as a defect; it is entirely correct code.
    """
    src = '''
class Registry:
    def __init__(self):
        self.crawlers = {"python": object(), "disk": object()}
        self.components = {}

    def setup(self):
        self.components["python"] = object()

    def get_python_status(self):
        return self.crawlers["python"]
'''
    confirmed, _ = audit(src)
    assert confirmed == [], (
        "object registry misread as a metric store -- the exact precision failure "
        "that made the first real-tree run 0/1"
    )


def test_silent_on_method_that_looks_like_a_store() -> None:
    """`self.get_status()` is a method, not a dict."""
    src = '''
class Thing:
    def __init__(self):
        self._real = {"ok": 0}

    def get_status(self):
        return {k: self._real.get(k, 0) for k in ("ok",)}
'''
    confirmed, _ = audit(src)
    assert confirmed == []
