"""The followup profile: defaults are valid, taste is editable, the rule survives.

Two properties are worth a test, and only two:

  * the shipped default passes its own checker (a checker whose own fixture
    fails is a checker that will be switched off), and
  * a profile that drops a locked slot is REFUSED -- the negative control. A
    profile tool that cannot fail cannot protect the mandate it exists for.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import followup_profile as fp  # noqa: E402


def _write(tmp_path, profile):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    return path


def test_default_profile_is_valid():
    assert fp.problems(fp.default_profile()) == []
    assert len(fp.default_profile()["cards"]) == 14


def test_locked_slots_are_the_three_the_rule_names():
    cards = {c["slot"]: c for c in fp.default_profile()["cards"]}
    assert set(fp.LOCKED) == {12, 13, 14}
    assert cards[13]["card"] == "Build, land, in the abyss"
    assert cards[14]["card"] == "Zero-shot reasoning"
    for slot in fp.LOCKED:
        assert cards[slot]["locked"] is True


def test_operator_can_reword_any_card_including_a_locked_one(tmp_path):
    path = _write(tmp_path, fp.default_profile())
    assert fp.main(["--path", str(path), "--set", "13",
                    "--card", "Land it, then say what broke"]) == 0
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["cards"][12]["card"] == "Land it, then say what broke"
    assert fp.problems(saved) == [], "rewording must not break the mandate"


def test_removing_a_locked_slot_is_refused(tmp_path, capsys):
    path = _write(tmp_path, fp.default_profile())
    rc = fp.main(["--path", str(path), "--remove", "13"])
    assert rc == 1
    assert "REFUSED" in capsys.readouterr().err


def test_removing_a_free_slot_leaves_thirteen_and_the_checker_says_so(tmp_path):
    path = _write(tmp_path, fp.default_profile())
    assert fp.main(["--path", str(path), "--remove", "4"]) == 0
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved["cards"]) == 13
    defects = fp.problems(saved)
    assert any("14 cards" in d for d in defects), defects


def test_emit_prints_fourteen_rows_and_refuses_a_broken_profile(tmp_path, capsys):
    path = _write(tmp_path, fp.default_profile())
    assert fp.main(["--path", str(path), "--emit"]) == 0
    rows = capsys.readouterr().out.strip().splitlines()
    assert len(rows) == 14, rows
    assert rows[-1].startswith("14|Zero-shot reasoning|"), rows[-1]

    broken = fp.default_profile()
    broken["cards"] = [c for c in broken["cards"] if c["slot"] != 14]
    broken["count"] = 13
    bp = _write(tmp_path, broken)
    assert fp.main(["--path", str(bp), "--emit"]) == 1
    assert "REFUSED" in capsys.readouterr().err


def test_count_disagrees_with_the_list():
    profile = fp.default_profile()
    profile["count"] = 15
    assert any("count says 15" in d for d in fp.problems(profile))
