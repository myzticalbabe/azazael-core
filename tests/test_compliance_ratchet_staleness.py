# coding: utf-8
"""Tests for the compliance ratchet staleness gate (2026-10-12).

A ratchet verdict is only meaningful against a FRESH baseline:
an aged baseline is refused fail-closed — an age that cannot be
proven (legacy flat format, unparsable timestamp) counts as stale.
The count comparison runs only after the freshness gate passes.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

_SPEC = importlib.util.spec_from_file_location(
    "compliance_ratchet", REPO / "scripts" / "compliance_ratchet.py")
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)

KEYS = ("unicode_emoji", "import_inside_function", "hardcoded_win_path")


def _counts(delta=0):
    return {k: 10 + delta for k in KEYS}


def _write_baseline(tmp_path, recorded_at, v2=True):
    if v2:
        payload = {"recorded_at": recorded_at, "counts": _counts()}
    else:
        payload = _counts()
    path = tmp_path / "compliance_baseline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _install(monkeypatch, tmp_path, measured=None):
    monkeypatch.setattr(cr, "BASELINE",
                        tmp_path / "compliance_baseline.json")
    if measured is not None:
        monkeypatch.setattr(cr, "measure", lambda: measured)
    return measured if measured is not None else _counts()


def test_parse_v2_format():
    at = datetime.now(timezone.utc).isoformat()
    recorded_at, counts = cr.parse_baseline(
        json.dumps({"recorded_at": at, "counts": _counts()}))
    assert recorded_at is not None
    assert counts == _counts()


def test_parse_legacy_flat_format_has_no_age():
    recorded_at, counts = cr.parse_baseline(json.dumps(_counts()))
    assert recorded_at is None
    assert counts == _counts()


def test_parse_unparsable_timestamp_has_no_age():
    recorded_at, counts = cr.parse_baseline(
        json.dumps({"recorded_at": "not-a-date", "counts": _counts()}))
    assert recorded_at is None
    assert counts == _counts()


def test_age_unknown_when_recorded_at_missing():
    assert cr.baseline_age_days(None, datetime.now(timezone.utc)) is None


def test_stale_age_blocks_before_measuring(tmp_path, monkeypatch, capsys):
    old = (datetime.now(timezone.utc)
           - timedelta(days=8)).isoformat()
    _write_baseline(tmp_path, old)
    _install(monkeypatch, tmp_path, measured=_counts())

    def _forbidden():
        raise AssertionError("verdict must be refused before measuring")

    monkeypatch.setattr(cr, "measure", _forbidden)
    rc = cr.main([])
    out = capsys.readouterr().out
    assert rc == cr.STALE_EXIT
    assert "STALE baseline" in out
    assert "verdict refused" in out


def test_legacy_flat_baseline_is_stale(tmp_path, monkeypatch, capsys):
    _write_baseline(tmp_path, None, v2=False)
    monkeypatch.setattr(cr, "BASELINE",
                        tmp_path / "compliance_baseline.json")
    monkeypatch.setattr(cr, "measure", lambda: _counts())
    rc = cr.main([])
    assert rc == cr.STALE_EXIT
    assert "age unknown" in capsys.readouterr().out


def test_fresh_baseline_ok_reports_age(tmp_path, monkeypatch, capsys):
    fresh = datetime.now(timezone.utc).isoformat()
    _write_baseline(tmp_path, fresh)
    _install(monkeypatch, tmp_path, measured=_counts())
    rc = cr.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ratchet: OK" in out
    assert "baseline age" in out


def test_age_just_under_limit_passes(tmp_path, monkeypatch, capsys):
    at = (datetime.now(timezone.utc)
          - timedelta(days=7, seconds=-1)).isoformat()
    _write_baseline(tmp_path, at)
    _install(monkeypatch, tmp_path, measured=_counts())
    assert cr.main(["--max-age-days", "7"]) == 0


def test_increase_still_blocks(tmp_path, monkeypatch, capsys):
    fresh = datetime.now(timezone.utc).isoformat()
    _write_baseline(tmp_path, fresh)
    _install(monkeypatch, tmp_path, measured=_counts(delta=1))
    rc = cr.main([])
    out = capsys.readouterr().out
    assert rc == 1
    assert "BLOCKED, category increased" in out


def test_max_age_knob(tmp_path, monkeypatch, capsys):
    week_old = (datetime.now(timezone.utc)
                - timedelta(days=8)).isoformat()
    _write_baseline(tmp_path, week_old)
    _install(monkeypatch, tmp_path, measured=_counts())
    assert cr.main(["--max-age-days", "30"]) == 0
    assert "baseline age" in capsys.readouterr().out


def test_bad_max_age_falls_back_to_default():
    assert cr.max_age_days([]) == 7.0
    assert cr.max_age_days(["--max-age-days", "30"]) == 30.0
    assert cr.max_age_days(["--max-age-days", "junk"]) == 7.0


def test_record_writes_v2_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "BASELINE",
                        tmp_path / "compliance_baseline.json")
    monkeypatch.setattr(cr, "measure", lambda: _counts())
    rc = cr.main(["--record"])
    assert rc == 0
    data = json.loads(
        (tmp_path / "compliance_baseline.json").read_text(
            encoding="utf-8"))
    assert "recorded_at" in data
    assert data["counts"] == _counts()
    assert cr.parse_baseline(json.dumps(data))[0] is not None


def test_missing_baseline_skips(tmp_path, monkeypatch, capsys):
    _install(monkeypatch, tmp_path, measured=_counts())
    assert cr.main([]) == 0
    assert "no baseline" in capsys.readouterr().out
