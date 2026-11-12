"""G1 test for src/eve_unleashed/morph.py.

Roundtrip must be lossless on keys and values; mutation must be
deterministic per seed and touch exactly one byte per variant; the
Markdown morph must carry the judgment and the opinion.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.morph import convert_text, morph_markdown, mutate_file


def test_csv_json_roundtrip(tmp_path):
    src = tmp_path / "a.csv"
    src.write_text("p,v\ngroq,ANSWERED\n", encoding="utf-8")
    mid = tmp_path / "a.json"
    assert convert_text(src, mid) == 1
    back = tmp_path / "b.csv"
    assert convert_text(mid, back) == 1
    assert "groq,ANSWERED" in back.read_text(encoding="utf-8").replace(
        "\r\n", "\n")


def test_bad_pair_rejected(tmp_path):
    import pytest
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        convert_text(src, tmp_path / "b.xml")


def test_mutate_deterministic_single_byte(tmp_path):
    src = tmp_path / "in.bin"
    src.write_bytes(b"hello world, this is eve")
    m1 = mutate_file(src, tmp_path / "m1", seed=7, count=3)
    m2 = mutate_file(src, tmp_path / "m2", seed=7, count=3)
    assert [m["byte"] for m in m1] == [m["byte"] for m in m2]
    assert len(m1) == 3
    for m in m1:
        raw = Path(m["variant"]).read_bytes()
        assert len(raw) == len(b"hello world, this is eve")
        assert sum(1 for a, b in zip(raw, b"hello world, this is eve")
                   if a != b) == 1


def test_markdown_carries_judgment_and_opinion():
    md = morph_markdown({"judgment": "5/5 machine checks passed",
                         "checks": [{"check": "c", "passed": True,
                                     "detail": "d"}],
                         "opinion": "OPINION (not machine-checked)"},
                        title="Prove bundle")
    assert "**Judgment:** 5/5" in md and "OPINION" in md
    assert "| check | passed | detail |" in md
