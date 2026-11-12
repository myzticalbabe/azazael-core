# coding: utf-8
"""Hermetic tests for expansion/anchor_retrieval.py (Lane C, #1683).

No network, no providers, no live store writes. Store access goes through
a fake; the re-rank engine is only touched on the no-candidates skip path
(which returns before import).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from CORE.WORMS.expansion.anchor_retrieval import (  # noqa: E402
    Anchors,
    AnchorRetrievalWorm,
    extract_anchors,
    grounded_check,
    rerank_candidates,
    retrieve_for_anchors,
)


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows
        self.seen = []

    def search_memories(self, query, limit=50):
        self.seen.append(query)
        return [dict(r) for r in self._rows[:limit]]


def _anchors():
    return Anchors(
        path="sample.exe",
        arch="x86",
        bits=64,
        strings=["CreateRemoteThread", "payload_alpha"],
        imports=["CreateRemoteThread", "WriteProcessMemory"],
        exports=[],
        functions=[{"name": "sub_mal_401000", "offset": 0, "size": 10}],
        symbols=["sub_mal_401000"],
        hashes={"md5": "0" * 32},
    )


# -- extract (fail-open, shaped) -------------------------------------------

def test_extract_missing_path_returns_shaped_empty():
    a = extract_anchors("no_such_file_xyz_123.exe")
    assert isinstance(a, Anchors)
    assert a.path == "no_such_file_xyz_123.exe"
    assert isinstance(a.functions, list) and isinstance(a.imports, list)


def test_terms_filters_short_and_dedupes():
    a = Anchors(path="x", imports=["abc", "CreateRemoteThread",
                                   "CreateRemoteThread", "zz"])
    terms = a.terms()
    assert "CreateRemoteThread" in terms
    assert "abc" not in terms and "zz" not in terms
    assert len(terms) == len(set(terms))


# -- retrieve ---------------------------------------------------------------

def test_retrieve_none_store_returns_empty():
    assert retrieve_for_anchors(None, _anchors()) == []


def test_retrieve_fake_store_attaches_anchor_term():
    store = _FakeStore([{"data": "row1"}, {"data": "row2"}])
    hits = retrieve_for_anchors(store, _anchors(), per_anchor=2, max_terms=2)
    assert hits and all("_anchor_term" in h for h in hits)
    assert len(store.seen) == 2


# -- rerank -----------------------------------------------------------------

def test_rerank_no_candidates_skips_without_engine():
    out = rerank_candidates("prompt", [])
    assert out["skipped"] is True and out["reason"] == "no-candidates"


# -- grounding --------------------------------------------------------------

def test_grounded_veto_no_anchor_reference():
    g = grounded_check(_anchors(), [{"text": "totally unrelated weather report"}], [])
    assert g["n_kept"] == 0 and g["n_vetoed"] == 1
    assert "domain" in g["vetoed"][0]["reason"]


def test_grounded_keep_localized_and_flag_attribution():
    claims = [{"text": "sample.exe calls CreateRemoteThread from sub_mal_401000"}]
    hits = [{"data": "CreateRemoteThread seen in prior triage"}]
    g = grounded_check(_anchors(), claims, hits)
    assert g["n_kept"] == 1 and g["n_vetoed"] == 0
    assert g["kept"][0]["attributed"] is True
    g2 = grounded_check(_anchors(), claims, [])
    assert g2["kept"][0]["attributed"] is False  # kept, flagged, not vetoed


# -- worm facade ------------------------------------------------------------

def test_worm_execute_never_raises_missing_binary(monkeypatch):
    import CORE.WORMS.expansion.anchor_retrieval as ar
    monkeypatch.setattr(ar, "LocalMemoryStore", None, raising=False)

    real_import = __import__

    def _guarded(name, *args, **kwargs):
        if name == "CORE.local_memory":
            raise ImportError("guarded for hermetic test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _guarded)
    out = AnchorRetrievalWorm().execute(
        "no_such_file_xyz_123.exe",
        claims=[{"text": "CreateRemoteThread payload_alpha activity"}],
        rerank=False)
    # Missing binary -> thin anchors -> success False, but shaped, never raised.
    assert out["success"] is False
    assert out["n_hits"] == 0
    assert out["rerank"]["reason"] == "rerank-disabled"
    # Thin bundles skip MARS (no fabrication from absence).
    assert out["mars"]["evaluated"] is False
    assert out["mars"]["reason"] == "thin-anchors"


def test_worm_execute_absorbs_mars_on_rich_bundle(monkeypatch):
    import CORE.WORMS.expansion.anchor_retrieval as ar
    rich = ar.Anchors(
        path="rich_fixture.exe",
        imports=["ws2_32.dll"],
        sections=[".text", ".rdata"],
        subsystem="WINDOWS_CUI",
        entropy_overall=6.8,
        entropy_packed=False,
    )
    monkeypatch.setattr(ar, "extract_anchors", lambda path, limit=200: rich)
    out = AnchorRetrievalWorm().execute(
        "rich_fixture.exe",
        claims=[{"text": "binary uses ws2_32 sockets"}],
        rerank=False)
    assert out["success"] is True
    mars = out["mars"]
    assert mars["evaluated"] is True
    # import_dll_ws2_32 + entropy_overall_elevated fire deterministically.
    assert mars["n_claims"] >= 2
    assert isinstance(mars["verdict"], str) and mars["verdict"]
    # Both families emitted live unity-ledger rows (isolated store).
    assert mars["emitted"] == [True, True]
    assert out["grounding"]["n_vetoed"] == 1
