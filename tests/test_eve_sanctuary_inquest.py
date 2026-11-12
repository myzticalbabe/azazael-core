"""G1 tests for sanctuary.py + inquest.py.

Sanctuary: counsel carries referrals and disclaims chaplaincy;
accommodation drafts stay drafts with counsel-review line; study
is receipted; movements separate checkable from OPINION. Inquest:
the dossier binds record checks, custody states, receipts and an
ordered timeline, and refuses unknown grievances.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import sanctuary as S
from eve_unleashed import inquest as Q


def test_counsel_holds_and_refers():
    text = S.counsel("facing eviction sunday")
    assert "not a chaplain" in text
    assert "988" in text
    assert "Becket" in text or "ACLU" in text
    with pytest.raises(ValueError):
        S.counsel("   ")


def test_accommodation_draft_only():
    for ctx in ("workplace", "school", "prison"):
        d = S.draft_accommodation("R. Singh", ctx, "uncut hair and turban")
        assert "DRAFT" in d and "only you file" in d
    with pytest.raises(ValueError):
        S.draft_accommodation("X", "temple", "y")
    with pytest.raises(ValueError):
        S.draft_accommodation("", "school", "y")


def test_study_receipted_under_patch():
    fake = {"topic": "Quakers", "found": True, "title": "Quakers",
            "description": "people", "extract": "Quakers are people.",
            "receipt": {"status": "ok"}}
    with patch("eve_unleashed.factcheck.wikipedia_lookup",
               return_value=fake):
        e = S.study("Quakers")
    assert e["found"] and e["receipt"]["status"] == "ok"
    assert "Eve has none" in e["note"]


def test_movements_checkable_vs_opinion():
    for m in S.movements():
        assert m["checkable"] and m["opinion"].startswith("OPINION")


def test_inquest_binds_dossier(monkeypatch, tmp_path):
    import eve_unleashed.redress as R
    import eve_unleashed.evidence as E
    monkeypatch.setattr(R, "GRIEVANCE_DIR", tmp_path / "g")
    monkeypatch.setattr(E, "LOCKER_DIR", tmp_path / "e")
    ev = E.ingest_text("photo page", "intake")
    rec = R.file_grievance("Acme Water", "environment",
                           "The Springfield Dam leaks nightly into the river.",
                           "Repair the dam.")
    fc = {"receipts": [{"status": "ok", "url": "http://x",
                        "fetched_utc": "2026-01-02T00:00:00Z",
                        "bytes": 5, "sha256": "s"}]}
    d = Q.open_inquest(rec["id"], [ev["id"]], [fc])
    assert d["grievance"] == rec["id"]
    assert d["custody"] == "1/1 INTACT"
    assert d["live_receipts"] == 1
    stamps = [t["stamp"] for t in d["timeline"]]
    assert stamps == sorted(stamps)
    assert "accuses no" in d["limits"]


def test_inquest_refuses_unknown():
    import eve_unleashed.redress as R
    with patch.object(R, "list_grievances", return_value=[]):
        with pytest.raises(ValueError):
            Q.open_inquest("grv-nope")
