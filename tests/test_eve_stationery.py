"""G1 tests for src/eve_unleashed/stationery.py.

The face is created here, so the test asserts the creation: every
capital A-Z present with at least one stroke, digits covered, the
page carrying both the stroke table and the drawing engine, the
seal deterministic per source, and the colophon naming the limits.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import stationery as S


def test_face_covers_alphabet_and_digits():
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        assert ch in S.STROKES, ch
        assert len(S.STROKES[ch]) >= 1
        for stroke in S.STROKES[ch]:
            assert len(stroke) >= 2
            for x, y in stroke:
                assert 0 <= x <= 100 and 0 <= y <= 140


def test_seal_is_deterministic_per_source():
    a = S.seal_svg("ab" * 32)
    b = S.seal_svg("ab" * 32)
    c = S.seal_svg("cd" * 32)
    assert a == b and a != c
    assert "<svg" in a and "</svg>" in a


def test_page_carries_font_engine_and_colophon(tmp_path):
    out = tmp_path / "keep.html"
    res = S.render_stationery("Dam Manual", "audience general",
                              [("Inspection", ["Walk the crest daily."])],
                              "ab" * 32, str(out))
    page = out.read_text(encoding="utf-8")
    assert "STROKES" in page and "drawHeading" in page
    assert "IntersectionObserver" in page
    assert "class='drop'" in page
    assert "fall back to serif" in page
    assert res["sections"] == 1


def test_stationery_from_tutorial(tmp_path):
    out = tmp_path / "tut.html"
    doc = {"source": "m.md", "words": 40, "sha256": "ef" * 32,
           "format": "markdown"}
    tut = {"audience": "general", "outline": ["Setup"],
           "steps": [{"action": "1. Open it", "check": "c",
                      "mistake": "m", "from": "line 2"}]}
    res = S.stationery_from_tutorial(doc, tut, str(out))
    page = out.read_text(encoding="utf-8")
    assert "m.md" in page and "Step 1" in page
    assert res["bytes"] > 1000


def test_refusals():
    with pytest.raises(ValueError):
        S.render_stationery("", "s", [("H", ["p"])], "ab" * 32, "x.html")
    with pytest.raises(ValueError):
        S.render_stationery("T", "s", [], "ab" * 32, "x.html")
