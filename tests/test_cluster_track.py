# coding: utf-8
"""CLUSTER track acceptance (gap-4 resolve, Engram #1655).

`build_cluster_records` normalises ClusterWORMZ execution rows into the
same trainer-consumable record contract as the intake builder; the CLUSTER
orchestrator track trains RLTrainLoop on them. Hermetic via FakeStore.

- measured success/failure rows -> records with producer severity untouched
  and data.success set, ok_rate computed
- unmeasured rows (no bool, info severity) skipped, counted, never invented
- producer-declared failure severities rescued as marked negatives
- starved pool -> run_cluster_cycle reports success=False, writes nothing
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import CORE.training_intake as ti  # noqa: E402


class FakeStore:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def query_memories(self, event_type=None, limit=500, **kw):
        return [r for r in self._rows
                if event_type is None or r.get("event_type") == event_type][:limit]


def _crow(ok=None, severity="info", cluster_type="custom", source="ClusterWORMZ.custom"):
    data = {"cluster_type": cluster_type}
    if ok is not None:
        data["success"] = ok
    return {"source": source, "event_type": "cluster_execution_completed",
            "severity": severity, "data": data,
            "timestamp": "2026-10-22T00:00:00+00:00", "session_id": "t"}


def test_measured_rows_become_records(monkeypatch):
    rows = [_crow(True), _crow(True),
            _crow(False, severity="error")]
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))
    built = ti.build_cluster_records(limit=500)
    assert built["n"] == 3, built
    assert built["ok_rate"] == round(2 / 3, 4), built
    rec = built["records"][0]
    assert rec["severity"] == "info", rec
    assert rec["data"]["success"] is True, rec
    assert rec["data"]["hook"] == "custom", rec
    neg = built["records"][2]
    assert neg["data"]["success"] is False, neg
    assert neg["data"]["nat_negative"] is True, neg


def test_unmeasured_rows_skipped_not_invented(monkeypatch):
    rows = [_crow(None, severity="info"), _crow(True)]
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))
    built = ti.build_cluster_records(limit=500)
    assert built["n"] == 1, built
    assert built["n_unmeasured"] == 1, built


def test_warning_without_bool_rescued_as_negative(monkeypatch):
    rows = [_crow(None, severity="warning")]
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))
    built = ti.build_cluster_records(limit=500)
    assert built["n"] == 1, built
    assert built["n_nat_declared"] == 1, built
    assert built["records"][0]["data"]["success"] is False, built


def test_producer_count_form_maps_to_verdict(monkeypatch):
    # Transcribed producer semantics (CLUSTERWORMS.py:1288): results = total
    # runs, success = runs with r.get("success"). Full sweep ok; partial is
    # a measured negative with the producer's own rate carried, not invented.
    def _count_row(success, results):
        return {"source": "ClusterWORMZ.custom",
                "event_type": "cluster_execution_completed",
                "severity": "info",
                "data": {"cluster_type": "custom", "results": results,
                         "success": success},
                "timestamp": "2026-10-22T00:00:00+00:00", "session_id": "t"}
    rows = [_count_row(8, 8), _count_row(7, 8), _count_row(0, 0)]
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))
    built = ti.build_cluster_records(limit=500)
    assert built["n"] == 2, built
    assert built["n_unmeasured"] == 1, built  # empty run: no verdict
    full, partial = built["records"]
    assert full["data"]["success"] is True, full
    assert full["data"]["success_rate"] == 1.0, full
    assert partial["data"]["success"] is False, partial
    assert partial["data"]["success_rate"] == 0.875, partial
    assert partial["data"]["near_miss"] is True, partial


def test_starved_pool_reports_skip(monkeypatch):
    import run_training_orchestrator as rto
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore([]))
    monkeypatch.setattr(rto, "get_memory_store", lambda: FakeStore([]),
                        raising=False)
    # run_cluster_cycle imports get_memory_store from CORE.local_memory at call
    # time via training_intake's namespace; patch the intake module's binding.
    import CORE.local_memory as lm
    monkeypatch.setattr(lm, "get_memory_store", lambda: FakeStore([]))
    r = rto.run_cluster_cycle()
    assert r["algo"] == "CLUSTER", r
    assert r["success"] is False, r
    assert r["records_used"] == 0, r
