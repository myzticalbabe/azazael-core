# coding: utf-8
"""Tests for spitfire.spawn_agents — subagent spawning."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pytest

import spitfire.spawn_agents as spawn


def test_target_specs_exist():
    """All 5 targets have specs defined."""
    expected = {"win7", "win81", "win10", "macos", "linux"}
    assert set(spawn.TARGET_SPECS.keys()) == expected


def test_target_specs_structure():
    """Each target spec has required fields."""
    required = {"os", "python", "arch", "config", "artifact", "label"}
    for target, spec in spawn.TARGET_SPECS.items():
        assert set(spec.keys()) >= required, f"{target} missing fields"
        assert isinstance(spec["os"], str)
        assert isinstance(spec["python"], str)


def test_build_subagent_prompt():
    """Prompt generation works for each target."""
    for target, spec in spawn.TARGET_SPECS.items():
        prompt = spawn.build_subagent_prompt(target, spec)
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert target in prompt
        assert spec["label"] in prompt
        assert spec["config"] in prompt
        assert spec["artifact"] in prompt


def test_spawn_modes():
    """Spawn modes return expected structures."""
    targets = ["win10", "macos"]
    
    # agent_manager mode
    results = spawn.spawn_via_agent_manager(targets)
    assert len(results) == 2
    for r in results:
        assert "target" in r
        assert "spec" in r
        assert "prompt" in r
    
    # kilo_task mode
    results = spawn.spawn_via_kilo_task_tool(targets)
    assert len(results) == 2
    for r in results:
        assert "target" in r
        assert "description" in r
        assert "prompt" in r
        assert "subagent_type" in r
        assert r["subagent_type"] == "general"


def test_main_handles_unknown_target():
    """main returns error for unknown target."""
    import io
    from contextlib import redirect_stderr
    import sys
    
    f = io.StringIO()
    old_argv = sys.argv
    try:
        sys.argv = ["spawn_agents.py", "--targets", "unknown"]
        with redirect_stderr(f):
            rc = spawn.main()
    finally:
        sys.argv = old_argv
    assert rc == 1
    assert "Unknown target" in f.getvalue()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])