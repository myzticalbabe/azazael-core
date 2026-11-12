"""G1 test for src/eve_unleashed/evidence.py.

The locker is content-addressed: same bytes, same id; rehash must
match; custody touches must be ordered; missing items must read
MISSING, never raise.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import evidence as E


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "LOCKER_DIR", tmp_path)


def test_ingest_file_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    src = tmp_path / "photo.txt"
    src.write_bytes(b"exhibit A")
    meta = E.ingest_file(str(src))
    assert meta["id"].startswith("ev:")
    assert E.verify(meta["id"])["state"] == "INTACT"


def test_duplicate_bytes_same_id(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    a = E.ingest_text("same note", "n1")
    b = E.ingest_text("same note", "n2")
    assert a["id"] == b["id"]
    assert len(E.custody(a["id"])) >= 2


def test_tamper_detected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    meta = E.ingest_text("pristine", "n")
    blob = tmp_path / (meta["sha256"] + ".bin")
    blob.write_bytes(b"altered")
    assert E.verify(meta["id"])["state"] == "TAMPERED"


def test_missing_and_bad_inputs(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert E.verify("ev:deadbeef")["state"] == "MISSING"
    with pytest.raises(ValueError):
        E.ingest_file(str(tmp_path / "nope.txt"))
    with pytest.raises(ValueError):
        E.ingest_text("   ")
    with pytest.raises(ValueError):
        E.ingest_receipt({"url": "x"})


def test_prove_case_accepts_locker_ids(monkeypatch, tmp_path):
    import eve_unleashed.redress as R
    monkeypatch.setattr(R, "GRIEVANCE_DIR", tmp_path / "g")
    monkeypatch.setattr(E, "LOCKER_DIR", tmp_path / "e")
    meta = E.ingest_text("exhibit", "n")
    rec = R.file_grievance("Acme", "consumer",
                           "A narrative long enough to count here.",
                           "Refund.", evidence=[meta["id"]])
    bundle = R.prove_case(rec["id"])
    assert any(m["present"] for m in bundle["evidence_manifest"])


def test_resolve_and_list(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    meta = E.ingest_text("exhibit", "n")
    assert E.resolve(meta["id"]).endswith(".bin")
    assert E.resolve("/plain/path") == "/plain/path"
    assert meta["id"] in [m["id"] for m in E.list_items()]
