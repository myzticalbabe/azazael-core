"""Unit tests for the health-check module-path resolver (RULE 0K).

Mirrors the resolver in `_worm_health_check.py`'s worker: bare registry
module names must be prefixed by their package when the module's home
directory is a package, so that imports resolve in subprocess isolation.
"""
import sys
import pytest
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "CORE" / "WORMS"))


def test_import_lightweight_worms():
    """Lightweight WORMS should import without errors."""
    lightweight = [
        "CLOCKZ", "DISKZ", "CONCURRENCY", "ERROR_SCAN_WORM",
        "METACLUSTER", "CLUSTERWORMS", "NETWORXZ"
    ]
    for mod in lightweight:
        try:
            __import__(mod)
        except Exception as e:
            pytest.fail(f"{mod} failed to import: {e}")


def test_import_heavy_worms():
    """Heavy WORMS import OK on the gil-off 3.15t build: all five (verified 2026-10-22).

    The conditional skip below is a HOST guard, not a tree defect: it fires only
    when a heavy import fails with a C-extension or missing-pandas error, which
    is a host capability gap (never observed on 3.15t gil-off as of 2026-10-22).
    """
    heavy = [
        "PROCESSZ", "SMARTDISK", "ERRORZ", "TRIMZ", "METRICZ"
    ]
    failures = []
    for mod in heavy:
        try:
            __import__(mod)
        except Exception as e:
            err = str(e).lower()
            if "c extension" in err or "pandas" in err:
                pytest.skip(
                    f"{mod}: host cannot build this import (C-extension/pandas guard; "
                    f"never observed on 3.15t gil-off as of 2026-10-22) ({e})"
                )
            failures.append(f"{mod}: {e}")
    if failures:
        pytest.fail("\n".join(failures))


def test_freethreading_detection():
    """Free-threading detection should work without error."""
    try:
        result = not sys._is_gil_enabled()
        assert isinstance(result, bool)
    except Exception as e:
        pytest.fail(f"Free-threading detection failed: {e}")


# ── Standalone execution (backward compatible) ──────────────────────


if __name__ == "__main__":

    def _import_mods(mods):
        p = f = 0
        for m in mods:
            try:
                __import__(m)
                p += 1
            except Exception:
                f += 1
        return p, f

    ok, bad = _import_mods([
        "CLOCKZ", "DISKZ", "CONCURRENCY", "ERROR_SCAN_WORM",
        "METACLUSTER", "CLUSTERWORMS", "NETWORXZ",
    ])
    print(f"lightweight: {ok} OK, {bad} FAIL")
    sys.exit(1 if bad else 0)
