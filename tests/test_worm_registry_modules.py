"""Regression test: registry entries must not carry bare module names.

The health-check and execution-matrix workers import worms by the registry's
"module" field. Bare names (e.g. "WORMZ") only resolve when the module's own
directory happens to be on sys.path, so they break in subprocess isolation
(Error family: 37 "No module named ..." failures, fixed by resolving against
the recorded source_file directory). This test fails if a worm is registered
with a bare module name that doesn't match its source_file location.
"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.worm_registry_cache import load_registry


def test_registry_modules_are_importable():
    """Every registry entry's module must import as recorded (bare names
    allowed only when their file sits at a location importable as-is)."""
    registry = load_registry(force_rebuild=False)
    assert registry, "worm registry is empty"

    bare = []
    for name, info in registry.items():
        mod = info.get("module", "")
        src = info.get("source_file", "")
        if not mod:
            bare.append((name, "<empty module>", src))
            continue
        if "." in mod:
            continue  # package-qualified: fine
        d = os.path.dirname(src or "")
        # Bare name is only legitimate if the module is importable from the
        # src root as-is (e.g. a module directly in src/CORE would need the
        # CORE. prefix; bare means it must live at the src root itself).
        if d.endswith("WORMS") or d.endswith("CORE"):
            bare.append((name, mod, src))

    assert not bare, (
        "registry entries with bare module names that need a package prefix "
        f"({len(bare)}): "
        + ", ".join(f"{n}({m})" for n, m, _ in bare[:10])
    )


def test_every_registry_module_imports():
    """Every worm module must import via its recorded (or prefix-corrected)
    path — the same resolution the health-check worker applies."""
    registry = load_registry(force_rebuild=False)
    failures = []
    for name, info in registry.items():
        mod = info.get("module", "")
        src = info.get("source_file", "")
        if "." not in mod:
            d = os.path.dirname(src or "")
            if d.endswith("WORMS"):
                mod = "CORE.WORMS." + mod
            elif d.endswith("CORE"):
                mod = "CORE." + mod
        try:
            importlib.import_module(mod)
        except Exception as e:
            failures.append(f"{name}: {mod} -> {e}")
    assert not failures, f"{len(failures)} worm modules failed to import: " + "; ".join(failures[:5])


if __name__ == "__main__":
    test_registry_modules_are_importable()
    print("PASS test_registry_modules_are_importable")
    test_every_registry_module_imports()
    print("PASS test_every_registry_module_imports")
