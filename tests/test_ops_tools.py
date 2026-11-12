# coding: utf-8
"""db_top + freeze_pool + light maintenance: unit surface."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import db_top
from CORE import data_retention as dr


def test_top_writers_shape():
    rep = db_top.top_writers(minutes=1, limit=3)
    assert rep["window_min"] == 1
    assert isinstance(rep["writers"], list)
    for w in rep["writers"]:
        assert {"source", "event", "rows", "kb"} <= set(w)


def test_top_writers_never_raises():
    rep = db_top.top_writers(minutes=1, limit=1)
    assert "error" in rep  # key present even on failure path


def test_light_maintenance_skips_heavy():
    import inspect
    sig = inspect.signature(dr.run_maintenance)
    assert sig.parameters["light"].default is False
    # Light path must not call the heavy writers: stub them and watch.
    calls = []
    orig_q, orig_v = dr.enforce_global_quota, dr.vacuum_if_needed
    dr.enforce_global_quota = lambda *a, **k: calls.append("quota") or {}
    dr.vacuum_if_needed = lambda *a, **k: calls.append("vacuum") or {}
    try:
        dr.run_maintenance(light=True)
    finally:
        dr.enforce_global_quota, dr.vacuum_if_needed = orig_q, orig_v
    assert calls == [], calls


def test_freeze_pool_file_roundtrip(tmp_path, monkeypatch):
    import freeze_pool
    out = tmp_path / "pool.json"
    monkeypatch.setattr("sys.argv", ["freeze_pool", "--limit", "5",
                                     "--out", str(out)])
    # Must not raise without a store; pool may be empty in CI.
    try:
        rc = freeze_pool.main()
    except SystemExit as e:
        rc = e.code
    assert rc in (0, 1)
    if rc == 0:
        body = json.loads(out.read_text(encoding="utf-8"))
        assert set(body) >= {"info", "alert", "meta"}
