"""G1 test for src/eve_unleashed/mods.py.

Apply must change the values and swap the asset; revert must restore
every file byte-identical. A mod manager that cannot revert is a
corruption machine, so the roundtrip is the whole test.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.mods import apply_mod, revert_mod


def _game(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    (game / "settings.json").write_text(
        json.dumps({"video": {"resolution": "720p"}}), encoding="utf-8")
    (game / "tuning.cfg").write_text("damage=10\nspeed=5\n",
                                     encoding="utf-8")
    (game / "title.png").write_bytes(b"ORIGINAL-TITLE")
    return game


def _mod(tmp_path):
    mod = tmp_path / "hd_mod"
    mod.mkdir()
    (mod / "mod.json").write_text(json.dumps({
        "patches": [
            {"file": "settings.json", "key": "video.resolution",
             "value": "4K"},
            {"file": "tuning.cfg", "key": "damage", "value": "99"}],
        "assets": [{"src": "new_title.png", "dst": "title.png"}]}),
        encoding="utf-8")
    (mod / "new_title.png").write_bytes(b"MODDED-TITLE")
    return mod


def test_apply_then_revert_roundtrip(tmp_path):
    game, mod = _game(tmp_path), _mod(tmp_path)
    before = {f: (game / f).read_bytes() for f in
              ("settings.json", "tuning.cfg", "title.png")}
    manifest = apply_mod(game, mod)
    assert len(manifest) == 3, manifest
    assert json.loads(
        (game / "settings.json").read_text())["video"][
        "resolution"] == "4K"
    assert "damage=99" in (game / "tuning.cfg").read_text()
    assert (game / "title.png").read_bytes() == b"MODDED-TITLE"
    assert revert_mod(game) == 3
    for f, blob in before.items():
        assert (game / f).read_bytes() == blob, f


def test_evolve_selects_best_generation(tmp_path):
    from eve_unleashed.mods import evolve_mod
    game = _game(tmp_path)
    mod = tmp_path / "evo_mod"
    mod.mkdir()
    (mod / "mod.json").write_text(json.dumps({
        "patches": [{"file": "tuning.cfg", "key": "damage",
                       "value": 99}], "assets": []}),
        encoding="utf-8")
    best, score, hist = evolve_mod(
        game, mod, generations=4, seed=7,
        fitness={"tuning.cfg::damage": 50})
    assert len(hist) == 4
    assert hist[0]["score"] == -(abs(99 - 50)), hist[0]
    assert best == max(hist, key=lambda h: h["score"])["gen"]
    assert (game / ".eveevo" / "best.json").is_file()
    # the live game dir is untouched by evolution copies
    assert (game / "title.png").read_bytes() == b"ORIGINAL-TITLE"


def test_apply_best_lands_winning_values(tmp_path):
    from eve_unleashed.mods import apply_best, evolve_mod
    game = _game(tmp_path)
    mod = tmp_path / "evo_mod"
    mod.mkdir()
    (mod / "mod.json").write_text(json.dumps({
        "patches": [{"file": "tuning.cfg", "key": "damage",
                       "value": 99}], "assets": []}),
        encoding="utf-8")
    best, _, _ = evolve_mod(game, mod, generations=4, seed=7,
                            fitness={"tuning.cfg::damage": 50})
    manifest = apply_best(game)
    assert manifest == ["tuning.cfg"]
    text = (game / "tuning.cfg").read_text()
    assert text.startswith("damage=") and "damage=99" not in text
