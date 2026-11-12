"""Tests for CORE.failure_shim -- the structured-metadata layer over failure_ledger.

The shim is best-effort by contract: the optional enhanced store must never break
the primary ledger. That contract is exactly why the recorder has to say when it
failed -- a silent `pass` made "the store has been broken for weeks" and "nothing
was ever recorded" the same reading, and no test could tell them apart. These
tests pin both halves: the ledger keeps counting when the store fails, and the
failure itself is visible through `enhanced_store_errors()`.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def shim():
    """Fresh module per test: the ledger dict and the error list are module state."""
    import CORE.failure_shim as module

    module = importlib.reload(module)
    module.reset_notes()
    return module


def test_note_failure_counts_into_the_ledger(shim):
    shim.note_failure("unit.test", ValueError("boom"))
    notes = shim.failure_notes()
    assert any("unit.test" in key for key in notes), notes
    assert shim.enhanced_store_errors() == []


def test_failure_record_is_accepted_positionally(shim):
    record = shim.FailureRecord(where="unit.test.record", exc=RuntimeError("x"),
                                severity="high")
    shim.note_failure(record)
    keys = shim.failure_notes()
    assert any("unit.test.record" in k for k in keys), keys


def test_store_failure_is_recorded_not_swallowed(shim, tmp_path, monkeypatch):
    """A write that cannot succeed must be reported, and must not raise.

    The parent of the log path is created with mkdir(parents=True), so pointing
    Path.home() at an existing *file* makes the store fail for a real reason
    (NotADirectoryError) rather than a mock's whim.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(shim.Path, "home", classmethod(lambda cls: blocker))

    shim.note_failure("unit.test.store", ValueError("boom"), severity="high")

    # The ledger still counted it: the primary path never depends on the store.
    assert any("unit.test.store" in k for k in shim.failure_notes())
    errors = shim.enhanced_store_errors()
    assert errors, "a failed enhanced-store write must leave a record"
    assert "unit.test.store" in errors[-1]


def test_enhanced_store_errors_returns_a_copy(shim):
    shim._ENHANCED_STORE_ERRORS.append("seeded")
    got = shim.enhanced_store_errors()
    got.append("mutated-by-caller")
    assert "mutated-by-caller" not in shim.enhanced_store_errors()


def test_error_record_is_bounded(shim):
    """A recorder failing on every call must not grow without limit."""
    for i in range(120):
        shim._ENHANCED_STORE_ERRORS.append("e%d" % i)
        del shim._ENHANCED_STORE_ERRORS[:-49]
    shim.note_failure("unit.test.bound", ValueError("x"), severity="high")
    assert len(shim.enhanced_store_errors()) <= 50
