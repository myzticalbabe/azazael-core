#!/usr/bin/env python3
"""G1: CORE.atomic_store — the one D1/D2 implementation every deed book uses.

Proves what three copy-pasted copies could not guarantee uniformly:
a failed write leaves the old file intact (crash-safety), and the
lock serializes concurrent read-modify-write increments.
"""

import json
import os
import threading

from CORE.atomic_store import atomic_write_json, file_lock


def test_atomic_write_json_roundtrip(tmp_path):
    target = tmp_path / "store.json"
    atomic_write_json(target, {"generation": 3}, "t")
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 3}


def test_failed_write_leaves_old_file_intact(tmp_path, monkeypatch):
    target = tmp_path / "store.json"
    target.write_text('{"generation": 7}\n', encoding="utf-8")
    def _boom(src, dst):
        raise OSError("disk gone")
    monkeypatch.setattr(os, "replace", _boom)
    raised = False
    try:
        atomic_write_json(target, {"generation": 8}, "t")
    except OSError:
        raised = True  # the injected failure must propagate, never swallow
    assert raised
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 7}
    assert list(tmp_path.glob("*.tmp")) == []  # no stray temp files


def test_file_lock_serializes_increments(tmp_path):
    target = tmp_path / "counter.json"
    target.write_text('{"n": 0}\n', encoding="utf-8")
    lock = tmp_path / "counter.lock"
    threads = 8
    per_thread = 25

    def bump():
        for _ in range(per_thread):
            with file_lock(lock, "t"):
                n = json.loads(target.read_text(encoding="utf-8"))["n"]
                atomic_write_json(target, {"n": n + 1}, "t")

    workers = [threading.Thread(target=bump) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert json.loads(target.read_text(encoding="utf-8"))["n"] == threads * per_thread


def test_file_lock_yields_lock_path(tmp_path):
    lock = tmp_path / "x.lock"
    with file_lock(lock, "t") as got:
        assert got == lock
