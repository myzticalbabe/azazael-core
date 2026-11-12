# coding: utf-8
"""gather() scale-awareness — forensic trace fix.

`gather()` used to fold every reward-ledger row into one
`rewards[subj] = float(value)` dict with no scale attached, silently
absorbing the ~100 blind rows (no scale_id) as if comparable. The fix
keeps the float shape but travels the units alongside
(`reward_scales`, `n_blind_rewards`, `blind_subjects`) and marks blind
rows instead of mixing them.

These tests pin, hermetically (FakeStore, no live DB):

* scaled rows land in `rewards` WITH their scale in `reward_scales`;
* blind rows are EXCLUDED from `rewards` and COUNTED in
  `n_blind_rewards` / `blind_subjects` — never silently absorbed;
* string-encoded payloads decode (a str row carrying scale_id counts);
* non-numeric values are skipped without killing the run;
* the `rewards` float shape is unchanged for existing readers.
"""
import json
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

    def query_memories(self, source=None, event_type=None, limit=200, **kw):
        return [r for r in self._rows if r.get("source") == source][:limit]


def _row(source, data):
    return {"source": source, "data": data}


def _gather(monkeypatch, rows):
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))
    return ti.gather()


def test_scaled_rows_keep_units(monkeypatch):
    out = _gather(monkeypatch, [
        _row("reward-ledger", {"subject": "a", "value": 0.9,
                               "scale_id": "rate_01"}),
        _row("reward-ledger", {"subject": "b", "value": 7,
                               "scale_id": "count"}),
    ])
    assert out["rewards"] == {"a": 0.9, "b": 7.0}
    assert all(isinstance(v, float) for v in out["rewards"].values())
    assert out["reward_scales"] == {"a": "rate_01", "b": "count"}
    assert out["n_blind_rewards"] == 0
    assert out["blind_subjects"] == []


def test_blind_rows_marked_not_mixed(monkeypatch):
    out = _gather(monkeypatch, [
        _row("reward-ledger", {"subject": "ok", "value": 0.5,
                               "scale_id": "rate_01"}),
        _row("reward-ledger", {"subject": "blind", "value": 999.0}),
        _row("reward-ledger", {"value": 1.0}),
    ])
    assert out["rewards"] == {"ok": 0.5}
    assert out["n_blind_rewards"] == 2
    assert sorted(out["blind_subjects"]) == ["?", "blind"]
    assert "blind" not in out["reward_scales"]


def test_string_payload_decodes(monkeypatch):
    out = _gather(monkeypatch, [
        _row("reward-ledger", json.dumps({"subject": "s", "value": 3,
                                          "scale_id": "count"})),
    ])
    assert out["rewards"] == {"s": 3.0}
    assert out["reward_scales"] == {"s": "count"}
    assert out["n_blind_rewards"] == 0


def test_non_numeric_skipped(monkeypatch):
    out = _gather(monkeypatch, [
        _row("reward-ledger", {"subject": "bad", "value": "NaN-ish",
                               "scale_id": "count"}),
        _row("reward-ledger", {"subject": "good", "value": 2,
                               "scale_id": "count"}),
    ])
    assert out["rewards"] == {"good": 2.0}
    assert out["n_blind_rewards"] == 0
