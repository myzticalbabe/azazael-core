# coding: utf-8
"""Write-on-change: repeat registrations must not spam the store."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE.WORMS.expansion.learning_versioner import ModelVersioner


class _Store:
    def __init__(self):
        self.rows = []

    def record_event(self, source="", event_type="", data=None,
                     severity="info", tags=None):
        self.rows.append({"source": source, "event_type": event_type,
                          "data": data or {}, "timestamp": "%09d" % len(self.rows)})
        return True

    def query_memories(self, source="", event_type="", limit=200):
        out = [r for r in self.rows
               if r["source"] == source and r["event_type"] == event_type]
        return out[-limit:]


def _mv():
    mv = ModelVersioner.__new__(ModelVersioner)
    import threading
    mv._models = {}
    mv._lock = threading.Lock()
    mv._store = _Store()
    return mv


def test_repeat_registration_writes_once():
    mv = _mv()
    mv.register_model("m", "1", {"a": 1.0}, {"k": "v"})
    mv.register_model("m", "1", {"a": 1.0}, {"k": "v"})
    mv.register_model("m", "1", {"a": 1.0}, {"k": "v"})
    assert len(mv._store.rows) == 1
    assert len(mv._models["m"]) == 3  # in-process history intact


def test_changed_metrics_writes_again():
    mv = _mv()
    mv.register_model("m", "1", {"a": 1.0}, {})
    mv.register_model("m", "1", {"a": 2.0}, {})
    assert len(mv._store.rows) == 2


def test_changed_version_writes_again():
    mv = _mv()
    mv.register_model("m", "1", {"a": 1.0}, {})
    mv.register_model("m", "2", {"a": 1.0}, {})
    assert len(mv._store.rows) == 2


def test_reads_still_merge_store_history():
    mv = _mv()
    mv.register_model("m", "1", {"a": 1.0}, {})
    assert mv.list_versions("m")[0]["version"] == "1"
    assert mv.get_latest_version("m")["version"] == "1"
