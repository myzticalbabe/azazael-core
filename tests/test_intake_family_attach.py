# coding: utf-8
"""Family attach on intake records (#855 E1 acceptance).

EP455's `family_index` input side wired to training: every record
carries its producer family, dotless hooks land "unassigned", and the
pool summary travels in the output. Hermetic via FakeStore (same shape
as test_training_intake_records: rows transcribed from record_link).
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

    def query_memories(self, source=None, event_type=None, limit=50, **kw):
        return [r for r in self._rows
                if source is None or r.get("source") == source][:limit]


def _row(ok, hook, severity=None):
    sev = severity or ("info" if ok else "warning")
    return {"source": "technique-link", "event_type": "hook:%s" % hook,
            "severity": sev,
            "data": {"subject": "s", "ok": ok, "citation": "",
                     "decision_id": ""},
            "timestamp": "2026-10-16T00:00:00+00:00", "session_id": "t"}


def test_dotted_hooks_carry_namespace_family(monkeypatch):
    rows = [_row(True, "technique_runtime.verify_boundary"),
            _row(False, "algorithm_lab.diversity_qd_nsga3")]
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))
    out = ti.build_training_records()
    fams = {r["data"]["hook"]: r["data"]["family"] for r in out["records"]}
    assert fams == {"technique_runtime.verify_boundary": "technique_runtime",
                    "algorithm_lab.diversity_qd_nsga3": "algorithm_lab"}
    assert out["n_families"] == 2
    assert out["families"]["unassigned"] == []


def test_dotless_hooks_land_unassigned(monkeypatch):
    rows = [_row(True, "technique_runtime.verify_boundary"),
            _row(True, "lonely")]
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))
    out = ti.build_training_records()
    fams = {r["data"]["hook"]: r["data"]["family"] for r in out["records"]}
    assert fams["lonely"] == "unassigned"
    assert "lonely" in out["families"]["unassigned"]
    assert out["n_families"] == 1


def test_existing_keys_untouched(monkeypatch):
    rows = [_row(True, "a.b"), _row(False, "c.d")]
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))
    out = ti.build_training_records()
    assert out["n_negative"] == 1
    assert out["n_near_miss"] == 1
    assert out["ok_rate"] == 0.5
    rec = out["records"][0]
    assert rec["data"]["success"] is True
    assert rec["data"]["nat_negative"] is False
