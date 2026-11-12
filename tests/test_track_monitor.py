# coding: utf-8
"""track_monitor: the rotation authority flows end-to-end, undriftable.

The archived live_monitor.py carried TRAIN_ALGOS (GRPOTUNE in, ALLPAR out)
while track_registry.ROTATION said the opposite, and nothing failed. These
tests pin the property that prevents recurrence: every track list the
monitor emits IS the registry's rotation, so a registry edit propagates
with no edit here — verified by injecting a synthetic track.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import track_monitor as tm
import track_registry


def _db(rows=()):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, timestamp TEXT,"
                " event_type TEXT, source TEXT, data TEXT)")
    for i, (ts, et, src, data) in enumerate(rows):
        con.execute("INSERT INTO memories VALUES (?,?,?,?,?)",
                    (i, ts, et, src, data))
    return con


def test_tracks_cover_rotation_exactly_in_order():
    con = _db()
    got = [r["track"] for r in tm.track_status(con)]
    assert got == list(track_registry.ROTATION)


def test_grid_keys_are_rotation_not_a_second_list():
    con = _db()
    grid = tm.sb3_training(con)
    algos = [k for k in grid if k not in ("series", "counts", "next_rotation")]
    assert algos == list(track_registry.ROTATION)
    assert "GRPOTUNE" not in grid
    assert "ALLPAR" in grid
    assert grid["next_rotation"] in track_registry.ROTATION


def test_synthetic_track_propagates_with_no_edit_here():
    import importlib
    track_registry.TRACKS["ZZPROBE"] = {
        "kind": "cycle", "runner": "run_zz",
        "event": ["zz_training_orchestrated", "x", None]}
    track_registry.ROTATION = list(track_registry.TRACKS)
    importlib.reload(tm)
    try:
        con = _db()
        assert [r["track"] for r in tm.track_status(con)][-1] == "ZZPROBE"
        assert "ZZPROBE" in tm.sb3_training(con)
        assert "zz_training_orchestrated" in tm._track_events()
    finally:
        del track_registry.TRACKS["ZZPROBE"]
        track_registry.ROTATION = [t for t in track_registry.ROTATION
                                   if t != "ZZPROBE"]
        importlib.reload(tm)


def test_collect_explain_bottom_line_shapes():
    con = _db([
        ("2026-10-03T22:00:00+00:00", "evolution_result", "s",
         '{"trajectory": [1.0, 1.1], "best_genes": {"epsilon": 0.05}}'),
        ("2026-10-03T22:00:00+00:00", "strategy_steered", "s",
         '{"strategy": "steady"}'),
    ])
    data = tm.collect(con)
    for key in ("tracks", "freshness", "evo", "strategy", "steer_24h",
                "activity", "last_flip", "training", "meta", "gosp"):
        assert key in data, key
    assert len(data["tracks"]) == len(track_registry.ROTATION)
    means = tm.explain(data)
    assert all(len(m) == 2 for m in means)
    assert isinstance(tm.bottom_line(data), str)


def test_web_dashboard_consumes_new_module():
    import live_monitor_web as w
    assert w.lm is tm
    assert callable(w.collect_snapshot)
