# coding: utf-8
"""PERSEPHONE worm registration + Loggers production-wiring pins.

- PERSEPHONEWORM is discoverable in the sync cluster and its execute()
  proves the Windows<->WSL round-trip with no home-dir I/O.
- UTILITIES.Loggers re-exports the canonical DynamicLogger (single face;
  the UTILITIES-level compressed duplicate is gone).
- src/memory_status.py consumes the logger through the Loggers package.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE.worm_registry import WORM_REGISTRY  # noqa: E402
from CORE.WORMS.PERSEPHONE_WORM import PERSEPHONEWORM  # noqa: E402


class TestPersephoneWorm:
    def test_registered_in_sync_cluster(self):
        assert "PERSEPHONEWORM" in WORM_REGISTRY
        assert WORM_REGISTRY["PERSEPHONEWORM"]["category"] == "sync"

    def test_execute_proves_round_trip(self):
        result = PERSEPHONEWORM().execute()
        assert result["worm"] == "PERSEPHONEWORM"
        assert result["round_trip_ok"] is True
        assert result["passed"] is True
        assert result["platform"]
        assert result["azazael_windows"] or result["azazael_wsl"]

    def test_no_bare_swallows(self):
        import inspect
        from CORE.WORMS import PERSEPHONE_WORM
        src = inspect.getsource(PERSEPHONE_WORM)
        assert "except ImportError" not in src  # utility owns its fallbacks


class TestLoggersWiring:
    def test_package_reexports_canonical_class(self):
        from UTILITIES.Loggers import DynamicLogger
        from AzazaelDynamicLogger import DynamicLogger as Canonical
        assert DynamicLogger is Canonical

    def test_package_instances_are_canonical_class(self):
        from UTILITIES.Loggers import DynamicLogger
        from AzazaelDynamicLogger import DynamicLogger as Canonical
        first, second = DynamicLogger(), DynamicLogger()
        assert type(first) is Canonical and type(second) is Canonical

    def test_memory_status_consumes_package(self):
        import pathlib
        text = pathlib.Path(__file__).resolve().parent.parent.joinpath(
            "src", "memory_status.py").read_text(encoding="utf-8")
        assert "from UTILITIES.Loggers import DynamicLogger" in text
        assert "from AzazaelDynamicLogger import" not in text
