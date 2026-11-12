# coding: utf-8
"""Emitter/reader alignment fixes — forensic trace.

Two misalignments where a reader consumed what no producer measures:

* `_record_calibration` stored predicted WITHOUT realised, so gather()'s
  calibration metric was permanently unscored (mean_abs_error always
  None). The failure-reflection call site measures realised (the attempt
  failed) — it now travels as 0.0, omitted (never invented) elsewhere.
* `authority_ceiling` asserted a "privilege gap" repair whenever
  exercised > 0, but no producer writes `granted_to` — absence of
  evidence presented as evidence of violation. The repair now fires
  only when grant information was actually recorded.

Hermetic throughout (fake stores, no live DB).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from CORE import structured_action as sa  # noqa: E402
from CORE import technique_runtime as tr  # noqa: E402


class FakeStore:
    def __init__(self):
        self.rows = []

    def record_event(self, source, event_type, data, **kw):
        self.rows.append({"source": source, "event_type": event_type,
                          "data": data})
        return 1


def test_calibration_row_carries_measured_realised(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr("CORE.local_memory.get_memory_store",
                        lambda: fake)
    tr._record_calibration("will it blend?", 0.8, "d-1", realised=0.0)
    assert len(fake.rows) == 1
    data = fake.rows[0]["data"]
    assert data["predicted"] == 0.8
    assert data["realised"] == 0.0


def test_calibration_row_omits_unmeasured_realised(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr("CORE.local_memory.get_memory_store",
                        lambda: fake)
    tr._record_calibration("q", 0.5, "d-2")
    assert "realised" not in fake.rows[0]["data"]


def test_no_privilege_gap_repair_without_grants(monkeypatch):
    monkeypatch.setattr(sa, "authority_ledger", lambda: {
        "granted": {}, "exercised": {"s": 3.0}, "gap": {}})
    out = sa.authority_ceiling("s", {})
    assert out["gap"] == 3.0
    assert not any("privilege gap" in r for r in out["repairs"])


def test_privilege_gap_repair_fires_with_grants(monkeypatch):
    monkeypatch.setattr(sa, "authority_ledger", lambda: {
        "granted": {"s": 1.0}, "exercised": {"s": 3.0}, "gap": {}})
    out = sa.authority_ceiling("s", {})
    assert any("privilege gap" in r for r in out["repairs"])
