# coding: utf-8
"""Smoke tests for the three src/RENDERING modules.

Why these exist: the modules shipped with a raw 0x97 (cp1252 em dash) byte and
therefore raised SyntaxError on import while the anti-pattern scanner logged
them as `read_error` without reading a line. A test that imports them is the
cheapest instrument that can tell "not importable" from "fine", and the commit
gate (G1) asks for exactly that.

Run with: python -m pytest tests/test_rendering_modules.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("PAMS_URL", "http://127.0.0.1:99999")

from RENDERING.characters.oxman import oxman_prompts  # noqa: E402
from RENDERING.characters.wraith import wraith_prompts  # noqa: E402
from RENDERING.pipeline import pipeline_config  # noqa: E402


def _nonempty_mapping(mod):
    """The module's prompt table, found by suffix rather than by one spelling.

    The names are OXMAN_PROMPTS and WRAITH_PROMPTS; asserting `PROMPTS` failed
    on both and proved the test wrong, not the module.
    """
    names = [k for k in vars(mod) if k.endswith("_PROMPTS") and k.isupper()]
    assert names, "%s exposes no *_PROMPTS table" % mod.__name__
    value = getattr(mod, names[0])
    assert isinstance(value, dict), "%s.%s must be a dict, got %r" % (
        mod.__name__, names[0], type(value))
    assert value, "%s.%s is empty" % (mod.__name__, names[0])
    for key, val in value.items():
        assert isinstance(key, str) and key, "empty key in %s.%s" % (mod.__name__, names[0])
        assert val, "empty prompt for %r in %s.%s" % (key, mod.__name__, names[0])
    return value


def test_oxman_prompts_load():
    assert _nonempty_mapping(oxman_prompts)


def test_wraith_prompts_load():
    assert _nonempty_mapping(wraith_prompts)


def test_pipeline_config_loads():
    # The stage table is named PIPELINE / STAGES depending on how it was written;
    # ask the module what it exposes instead of asserting one spelling.
    public = {k: v for k, v in vars(pipeline_config).items()
              if k.isupper() and not k.startswith("_")}
    assert public, "pipeline_config exposes no public constants"
    assert any(isinstance(v, (dict, list, tuple, str, int)) for v in public.values())


def test_sources_are_utf8():
    """The defect this file exists for: a non-UTF-8 byte in a .py file."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "RENDERING"
    bad = []
    for path in root.rglob("*.py"):
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            bad.append("%s: %s" % (path.relative_to(root), exc))
    assert not bad, "non-UTF-8 python sources: %s" % bad
