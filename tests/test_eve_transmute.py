"""G1 tests for src/eve_unleashed/transmute.py.

Recipes chain real functions; network is patched at the factcheck
boundary, the locker and grievance store are isolated to tmp.
Each test asserts the chain ran every stage in order and named
what it produced -- a dropped stage is a test failure, not a
shorter pipeline.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import transmute as T


def _wiki_ok(monkeypatch):
    import eve_unleashed.factcheck as F
    hit = {"topic": "Hoover Dam", "found": True, "via": "wikipedia",
           "title": "Hoover Dam", "description": "dam",
           "receipt": {"status": "ok", "url": "u", "fetched_utc": "t",
                       "bytes": 3, "sha256": "s"}}
    monkeypatch.setattr(F, "wikipedia_lookup", lambda t: dict(hit))


def test_lead_into_gold_chains_four_stages(monkeypatch, tmp_path):
    import eve_unleashed.factcheck as F
    import eve_unleashed.evidence as E
    _wiki_ok(monkeypatch)
    monkeypatch.setattr(E, "LOCKER_DIR", tmp_path)
    res = T.lead_into_gold("The Hoover Dam holds water.")
    assert [s["stage"] for s in res["stages"]] == [
        "debunk", "factcheck", "rebuttal", "lock"]
    assert res["verdict"] == "CORROBORATED"
    assert all(i.startswith("ev:") for i in res["locked"])
    assert "CORRECTION" in res["rebuttal"]


def test_scroll_into_school_reads_tutorial_writes(tmp_path):
    doc = tmp_path / "sop.md"
    doc.write_text("# Steps\n\n1. Open the gate\n2. Close the gate\n",
                   encoding="utf-8")
    out = tmp_path / "t.md"
    res = T.scroll_into_school(str(doc), str(out))
    assert [s["stage"] for s in res["stages"]] == ["read", "tutorial",
                                                  "write"]
    assert res["steps"] == 2 and out.is_file()


def test_scroll_names_error_stage(tmp_path):
    res = T.scroll_into_school(str(tmp_path / "missing.md"),
                               str(tmp_path / "t.md"))
    assert res["stages"][-1]["stage"] == "ERROR"
    assert res["steps"] == 0


def test_grievance_into_dossier(monkeypatch, tmp_path):
    import eve_unleashed.redress as R
    import eve_unleashed.inquest as I
    import eve_unleashed.evidence as E
    monkeypatch.setattr(R, "GRIEVANCE_DIR", tmp_path / "g")
    monkeypatch.setattr(E, "LOCKER_DIR", tmp_path / "e")
    rec = R.file_grievance("Acme Water", "environment",
                           "The Springfield Dam leaks nightly into the river.",
                           "Repair the dam.")
    res = T.grievance_into_dossier(rec["id"])
    assert [s["stage"] for s in res["stages"]] == ["prove", "inquest"]
    assert res["dossier"]["grievance"] == rec["id"]


def test_swarm_digest_ranks_refuted_first():
    res = T.swarm_into_digest(["This always works except never.",
                               "The river flows north."])
    assert res["answered"] == 2
    assert res["digest"][0]["verdict"] == "REFUTED"
