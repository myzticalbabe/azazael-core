# coding: utf-8
"""Tests for the `src/CODEX/CORE/azazael_pygame.py` deprecation shim (rows D-5/D-3).

That path held a near-copy of the canonical `src/azazael_pygame.py` and NOTHING in
the tree imports either copy, so the duplicate was retired to a re-export. The
negative controls below are the point of this file:

  1. IDENTITY, not equality. A shim that re-IMPLEMENTS instead of re-exporting
     passes value-equality checks and fails these — which is the exact failure
     mode the dedup exists to prevent.
  2. The delegation must be real: a name the shim does not enumerate has to resolve
     to the canonical module's object, and a name in NEITHER must raise
     AttributeError rather than hand back a silent `None`.
  3. The shim must not regain an implementation. The source is asserted to contain
     no class and no top-level `def` beyond `__getattr__`, so a future paste-back
     of the retired copy fails here instead of quietly re-forking the pair.
"""
import importlib.util
import pathlib
import re
import sys
import warnings

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SHIM_PATH = SRC / "CODEX" / "CORE" / "azazael_pygame.py"

#: What the shim's `__all__` promises.
PUBLIC = (
    "AnimationState",
    "AzazaelVisualizer",
    "Badge",
    "COLORS",
    "EnrichmentDisplay",
    "EnrichmentField",
    "EnrichmentType",
    "FPS",
    "HEIGHT",
    "Particle",
    "SYSTEMS",
    "Tag",
    "TimelineEvent",
    "WIDTH",
)


def _load_shim(*, suppress_warning: bool):
    """Load the shim by FILE PATH, the way an out-of-package caller must.

    A fresh module object each call, so the module-level FutureWarning is emitted
    again instead of being served from a cached import.
    """
    spec = importlib.util.spec_from_file_location(
        "_shim_azazael_pygame_probe", SHIM_PATH
    )
    module = importlib.util.module_from_spec(spec)
    if suppress_warning:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            spec.loader.exec_module(module)
    else:
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def canonical():
    import azazael_pygame  # the top-level name the shim re-exports

    return azazael_pygame


@pytest.fixture(scope="module")
def shim():
    return _load_shim(suppress_warning=True)


def test_shim_reexports_the_canonical_objects_by_identity(shim, canonical):
    """`is`, not `==`: the shim must hand back the canonical module's objects."""
    missing = [name for name in PUBLIC if not hasattr(shim, name)]
    assert not missing, f"shim is missing promised names: {missing}"

    for name in PUBLIC:
        assert getattr(shim, name) is getattr(canonical, name), (
            f"{name} is not the canonical object (the shim re-implemented it "
            f"rather than re-exporting it)"
        )


def test_shim_all_matches_what_it_promises(shim):
    assert set(shim.__all__) == set(PUBLIC), (
        "shim.__all__ and the names this test checks have drifted apart"
    )


def test_the_pygame_available_flag_is_reexported(shim, canonical):
    """Private-but-contractual: the canonical init gate and `__main__` read it."""
    assert shim._PYGAME_AVAILABLE is canonical._PYGAME_AVAILABLE


def test_shim_emits_a_future_warning_on_import():
    with pytest.warns(FutureWarning, match="NON-canonical copy"):
        _load_shim(suppress_warning=False)


def test_unenumerated_names_delegate_to_the_canonical_module(shim, canonical):
    """Names the retired copy exposed from its own namespace still resolve.

    `note_failure` (bound by the canonical's guarded import) and `math` (imported
    by the canonical) are both absent from this module's `__all__`, so resolving
    them exercises the `__getattr__` delegation rather than a listed re-export.
    """
    assert shim.note_failure is canonical.note_failure
    assert shim.math is canonical.math


def test_an_unknown_name_raises_attribute_error(shim):
    with pytest.raises(AttributeError, match="no attribute"):
        shim.definitely_not_a_real_name_xyz


def test_the_shim_holds_no_implementation():
    """Structural guard: a paste-back of the retired copy fails here.

    Without this, the re-export could be undone by a well-meaning edit that
    restores "the missing code", silently re-forking the pair this retired.
    """
    text = SHIM_PATH.read_text(encoding="utf-8")

    classes = re.findall(r"^class\s+(\w+)", text, re.M)
    assert classes == [], f"shim defines class(es) {classes}; it must only re-export"

    defs = re.findall(r"^def\s+(\w+)", text, re.M)
    assert defs == ["__getattr__"], (
        f"shim defines top-level functions {defs}; only __getattr__ is allowed"
    )

    assert "pygame.draw" not in text, (
        "shim contains pygame rendering calls; the implementation is back"
    )
