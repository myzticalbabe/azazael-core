"""G1 tests for the eve justice CLI handlers.

Modules are covered elsewhere; this file covers the wiring: each
cmd_* parses its namespace, prints the human lines, and returns
the right exit code. Network is patched out everywhere.
"""
import sys
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import cli as C


def ns(**kw):
    return types.SimpleNamespace(**kw)


def test_cmd_debunk_refutes(capsys):
    rc = C.cmd_debunk(ns(claim="This always stands except when it falls."))
    assert rc == 0
    assert "VERDICT: REFUTED" in capsys.readouterr().out


def test_cmd_factcheck_corroborates(monkeypatch, capsys):
    import eve_unleashed.factcheck as F
    hit = {"topic": "Hoover Dam", "found": True, "title": "Hoover Dam",
           "description": "dam",
           "receipt": {"status": "ok", "url": "u", "fetched_utc": "t",
                       "bytes": 3, "sha256": "s"}}
    monkeypatch.setattr(F, "wikipedia_lookup", lambda t: dict(hit))
    rc = C.cmd_factcheck(ns(claim="The Hoover Dam stands.",
                            grievance=None, url=None, lock=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "VERDICT: CORROBORATED" in out


def test_cmd_factcheck_url_failure(monkeypatch, capsys):
    import eve_unleashed.factcheck as F
    monkeypatch.setattr(F.urllib.request, "urlopen",
                        _boom("down"))
    rc = C.cmd_factcheck(ns(claim="", grievance=None,
                            url="http://example.test/x", lock=False))
    assert rc == 1


def _boom(msg):
    def raising(*a, **k):
        raise TimeoutError(msg)
    return raising


def test_cmd_locker_roundtrip(monkeypatch, tmp_path, capsys):
    import eve_unleashed.evidence as E
    monkeypatch.setattr(E, "LOCKER_DIR", tmp_path)
    assert C.cmd_locker(ns(locker_action="note", text="memo",
                           label="n")) == 0
    lid = E.list_items()[0]["id"]
    assert C.cmd_locker(ns(locker_action="verify", id=lid)) == 0
    assert "INTACT" in capsys.readouterr().out
    assert C.cmd_locker(ns(locker_action="list")) == 0
    assert "items=1" in capsys.readouterr().out


def test_cmd_watch_add_check(monkeypatch, tmp_path, capsys):
    import eve_unleashed.watch as W
    import eve_unleashed.redress as R
    monkeypatch.setattr(W, "WATCH_DIR", tmp_path)
    assert C.cmd_watch(ns(add="lead pipes", remove=None,
                          check=False)) == 0
    monkeypatch.setattr(R, "search_dockets",
                        lambda term: [{"id": "d9", "title": "t"}])
    assert C.cmd_watch(ns(add=None, remove=None, check=True)) == 0
    out = capsys.readouterr().out
    assert "new=1" in out
    assert C.cmd_watch(ns(add=None, remove="lead pipes",
                          check=False)) == 0


def test_cmd_rebuttal_drafts(monkeypatch, capsys):
    import eve_unleashed.factcheck as F
    res = {"claim": "c", "verdict": "UNCORROBORATED",
           "debunk": {"fallacy_flags": [], "contradictions": [],
                      "opinions": []},
           "entity_lookups": [], "receipts": [], "opinion": ""}
    monkeypatch.setattr(F, "factcheck_claim", lambda *a: res)
    assert C.cmd_rebuttal(ns(claim="c", grievance=None)) == 0
    out = capsys.readouterr().out
    assert "CORRECTION" in out and "under your own name" in out


def test_cmd_sanctuary_modes(capsys):
    assert C.cmd_sanctuary(ns(counsel="heavy heart", accommodate=None,
                              study=None, movements=False)) == 0
    assert "not a chaplain" in capsys.readouterr().out
    assert C.cmd_sanctuary(ns(counsel=None, accommodate=None,
                              study=None, movements=True)) == 0
    assert "OPINION" in capsys.readouterr().out
    rc = C.cmd_sanctuary(ns(counsel=None,
                            accommodate=("X", "temple", "y"),
                            study=None, movements=False))
    assert rc == 1


def test_cmd_inquest_binds(monkeypatch, tmp_path, capsys):
    import eve_unleashed.redress as R
    import eve_unleashed.evidence as E
    monkeypatch.setattr(R, "GRIEVANCE_DIR", tmp_path / "g")
    monkeypatch.setattr(E, "LOCKER_DIR", tmp_path / "e")
    rec = R.file_grievance("Acme Water", "environment",
                           "The Springfield Dam leaks nightly into the river.",
                           "Repair the dam.")
    rc = C.cmd_inquest(ns(id=rec["id"], locker=[], claim=None))
    assert rc == 0
    out = capsys.readouterr().out
    assert "INQUEST" in out and "LIMITS" in out
    rc = C.cmd_inquest(ns(id="grv-nope", locker=[], claim=None))
    assert rc == 1
