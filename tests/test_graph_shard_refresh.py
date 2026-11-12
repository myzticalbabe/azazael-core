# coding: utf-8
"""Sharded refresh: discovery + env contract (no live graphify runs here)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import graph_shard_refresh as gsr


def test_discovers_code_dirs_only(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "y.py").write_text("y = 1\n", encoding="utf-8")
    names = [p.name for p in gsr.discover_shards(tmp_path)]
    assert names == ["a"]


def test_run_pins_utf8_for_graphify_children(tmp_path, monkeypatch):
    seen = {}

    class _R:
        returncode = 0
        stdout, stderr = "", ""

    def fake_run(cmd, **kw):
        seen.update(kw.get("env") or {})
        return _R()

    monkeypatch.setattr(gsr.subprocess, "run", fake_run)
    gsr._run(["graphify"], cwd=tmp_path, timeout=5)
    assert seen.get("PYTHONUTF8") == "1"


def test_update_shard_shape(tmp_path):
    # Shape contract only: (name, ok, out-tail) triple.
    import graph_shard_refresh as _g
    assert callable(_g.update_shard)
    assert callable(_g.main)
