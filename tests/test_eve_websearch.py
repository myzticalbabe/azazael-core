"""G1 tests for src/eve_unleashed/websearch.py.

Every provider parses one canned payload; a dead provider reports
error without sinking the bundle; error and empty are never
merged; duplicates collapse by URL; unknown providers and empty
queries are refused.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import websearch as W


def _payloads():
    return {
        "wikipedia": ({"query": {"search": [
            {"title": "Hoover Dam", "snippet": "a dam"}]}}, None),
        "loc": ({"results": [{"title": "Dam photo", "url": "http://loc/x",
                               "date": "1935"}]}, None),
        "openlibrary": ({"docs": [{"title": "Dams",
                                   "key": "/works/1",
                                   "author_name": ["Roe"]}]}, None),
        "crossref": ({"message": {"items": [
            {"title": ["Dam safety"], "URL": "http://doi/x"}]}}, None),
        "stackexchange": ({"items": [{"title": "Q", "link": "http://se/1",
                                      "score": 3, "answer_count": 2}]}, None),
    }


def _fake_get(payloads):
    def fake(url):
        for key, (data, err) in payloads.items():
            if (key == "wikipedia" and "wikipedia.org" in url
                    or key == "loc" and "loc.gov" in url
                    or key == "openlibrary" and "openlibrary" in url
                    or key == "crossref" and "crossref" in url
                    or key == "stackexchange" and "stackexchange" in url):
                return data, err
        raise AssertionError("unexpected url " + url)
    return fake


def test_all_five_answer():
    with patch.object(W, "_get", side_effect=_fake_get(_payloads())):
        res = W.search("dam safety")
    assert res["answered"] == 5
    assert len(res["hits"]) == 5
    assert {h["via"] for h in res["hits"]} == set(W.PROVIDERS)


def test_dead_provider_is_error_not_empty():
    payloads = _payloads()
    payloads["loc"] = (None, "connection reset")
    with patch.object(W, "_get", side_effect=_fake_get(payloads)):
        res = W.search("dam safety")
    loc = [l for l in res["legs"] if l["via"] == "loc"][0]
    assert loc["state"] == "error"
    assert res["answered"] == 4 and len(res["hits"]) == 4


def test_empty_answer_is_empty_not_error():
    payloads = _payloads()
    payloads["crossref"] = ({"message": {"items": []}}, None)
    with patch.object(W, "_get", side_effect=_fake_get(payloads)):
        res = W.search("dam safety")
    cr = [l for l in res["legs"] if l["via"] == "crossref"][0]
    assert cr["state"] == "empty"
    assert res["answered"] == 5


def test_duplicates_collapse_by_url():
    payloads = _payloads()
    with patch.object(W, "_get", side_effect=_fake_get(payloads)):
        res = W.search("dam safety", providers=("wikipedia", "wikipedia"))
    assert len(res["hits"]) == 1


def test_refusals():
    with pytest.raises(ValueError):
        W.search("   ")
    with pytest.raises(ValueError):
        W.search("x", providers=("alta-vista",))
