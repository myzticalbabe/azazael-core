# coding: utf-8
"""INTAKE-wire pin tests — Engram #798 item 1 unblock.

`evolution_cycle._blend_intake_pool` appends technique-outcome records to
the GA info pool. These tests pin the contract that makes the wire safe
rather than decorative:

* ratio math: blend 0.5 on a 100-row base appends exactly 50 intake rows;
* 0 disables the wire AND skips the intake import entirely;
* garbage env falls back to the documented default (0.5), never raises;
* a builder explosion returns the base pool unchanged (fail-open);
* empty base short-circuits before touching the intake;
* the caller's list is never mutated.

Hermetic: the intake builder is monkeypatched, no store, no clock.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import CORE.training_intake as ti  # noqa: E402
import evolution_cycle as evc  # noqa: E402


def _rows(n, tag):
    return [{"event_type": "t", "data": {"tag": "%s-%d" % (tag, i)}}
            for i in range(n)]


def _patch_builder(monkeypatch, records=None, explode=None):
    calls = []

    def fake(limit=50):
        calls.append(limit)
        if explode is not None:
            raise explode
        return {"records": list(records or []), "ok_rate": 1.0,
                "n": len(records or [])}

    monkeypatch.setattr(ti, "build_training_records", fake)
    return calls


def test_blend_default_appends_half_base(monkeypatch):
    monkeypatch.delenv("INTAKE_POOL_BLEND", raising=False)
    calls = _patch_builder(monkeypatch, _rows(500, "in"))
    out = evc._blend_intake_pool(_rows(100, "base"))
    assert len(out) == 150
    assert out[100]["data"]["tag"] == "in-0"
    assert calls == [50]


def test_blend_zero_disables_wire_without_calling_builder(monkeypatch):
    monkeypatch.setenv("INTAKE_POOL_BLEND", "0")
    calls = _patch_builder(monkeypatch, _rows(500, "in"))
    base = _rows(100, "base")
    out = evc._blend_intake_pool(base)
    assert out == base
    assert calls == []


def test_blend_garbage_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("INTAKE_POOL_BLEND", "lots")
    calls = _patch_builder(monkeypatch, _rows(500, "in"))
    out = evc._blend_intake_pool(_rows(100, "base"))
    assert len(out) == 150
    assert calls == [50]


def test_blend_builder_explosion_returns_base_unchanged(monkeypatch):
    monkeypatch.delenv("INTAKE_POOL_BLEND", raising=False)
    _patch_builder(monkeypatch, explode=RuntimeError("store down"))
    base = _rows(100, "base")
    out = evc._blend_intake_pool(base)
    assert out == base  # fail-open: the loop never breaks on intake


def test_blend_empty_base_short_circuits(monkeypatch):
    monkeypatch.delenv("INTAKE_POOL_BLEND", raising=False)
    calls = _patch_builder(monkeypatch, _rows(500, "in"))
    assert evc._blend_intake_pool([]) == []
    assert calls == []


def test_blend_does_not_mutate_caller_list(monkeypatch):
    monkeypatch.delenv("INTAKE_POOL_BLEND", raising=False)
    _patch_builder(monkeypatch, _rows(500, "in"))
    base = _rows(100, "base")
    snapshot = list(base)
    evc._blend_intake_pool(base)
    assert base == snapshot


def test_blend_one_appends_full_base_size(monkeypatch):
    monkeypatch.setenv("INTAKE_POOL_BLEND", "1")
    _patch_builder(monkeypatch, _rows(500, "in"))
    out = evc._blend_intake_pool(_rows(100, "base"))
    assert len(out) == 200
