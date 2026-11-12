"""G1 test for src/eve_unleashed/stem.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.stem import list_tracks, plan_track, referrals


def test_tracks_cover_three_learning_styles():
    assert list_tracks() == ["guided", "lab", "visual"]


def test_plan_carries_goal_and_native_commands():
    steps = plan_track("guided", "ship a Python CLI")
    assert steps[0] == "GOAL: ship a Python CLI"
    assert any("eve morph" in s for s in steps)


def test_bad_style_rejected():
    import pytest
    with pytest.raises(ValueError):
        plan_track("telepathy", "x")


def test_referrals_are_real_orgs():
    urls = [u for _, u in referrals()]
    assert "https://girlswhocode.com/" in urls
    assert "https://swe.org/" in urls
    assert len(urls) == 5
