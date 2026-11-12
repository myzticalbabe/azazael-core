# coding: utf-8
"""Censored write-back tests for the AOA arm router (2026-09-27).

The router's exception path fed EVERY failure into the arm reliability
Q-table, so an arm was demoted for faults that were never its own -- a
missing binary, a refused connection, a hung upstream (the same poison
class the mesh router's env_error path closed). These tests pin the typed
failure contract:

- OSError-family exceptions are INFRA: censored (counted, never learned)
- everything else is real execution evidence: learned as before
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from AOA.core.router import CrossArmRouter


class _FakeSpine:
    def __init__(self):
        self.calls = []

    def record_outcome(self, nature, worm, success, duration_ms=0.0,
                       error=None, reward=None, delta=None, censored=False):
        self.calls.append({"nature": nature, "worm": worm,
                           "success": bool(success), "censored": censored})


def test_is_infra_error_classifies_the_oserror_family():
    assert CrossArmRouter._is_infra_error(ConnectionRefusedError()) is True
    assert CrossArmRouter._is_infra_error(TimeoutError()) is True
    assert CrossArmRouter._is_infra_error(FileNotFoundError("binary")) is True
    assert CrossArmRouter._is_infra_error(OSError("device gone")) is True


def test_is_infra_error_treats_logic_failures_as_signal():
    assert CrossArmRouter._is_infra_error(ValueError("bad payload")) is False
    assert CrossArmRouter._is_infra_error(TypeError("bad shape")) is False
    assert CrossArmRouter._is_infra_error(RuntimeError("arm logic")) is False
    assert CrossArmRouter._is_infra_error(KeyError("missing")) is False


def test_write_back_passes_censored_through_to_the_spine(monkeypatch):
    import AOA.core.router as router_mod

    fake = _FakeSpine()
    monkeypatch.setattr(router_mod, "_spine", lambda: fake)
    router = CrossArmRouter()

    stored = router._write_back("cap", "arm-a", False, 12.0, censored=True)
    assert stored is True
    assert fake.calls[-1]["censored"] is True, \
        "infra noise must reach the ledger censored"

    stored = router._write_back("cap", "arm-a", True, 8.0)
    assert stored is True
    assert fake.calls[-1]["censored"] is False, \
        "real outcomes must stay learnable"
