# coding: utf-8
"""Regression: EvolutionIntegrityWorm._champion_in_use must RUN, not just import.

Found 2026-10-16 by live-executing every class in CORE.WORMS.expansion:
``ScheduleConfig.evolved_param_paths()`` did not exist, so every audit pass
crashed with AttributeError. No test file referenced ``_champion_in_use`` at
all (only wiring_genome names the worm, and excludes it) -- the import-level
suite proved the module loads and nothing more.
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.scheduling import ScheduleConfig
from CORE.WORMS.expansion.evolution_audit import EvolutionIntegrityWorm


def test_evolved_param_paths_lists_state_then_legacy():
    paths = list(ScheduleConfig.evolved_param_paths())
    assert len(paths) == 2
    assert paths[0].name == "evolved_rl_params.json"
    assert paths[0].parent.name == "state"
    assert paths[1].parent.name == "config"


def test_champion_in_use_runs_and_reports_structure():
    worm = EvolutionIntegrityWorm.__new__(EvolutionIntegrityWorm)
    worm.__init__()
    result = worm._champion_in_use()
    assert result["champion_present"] in (True, False)
    assert "champion_applied" in result
    assert "champion_in_use" in result
