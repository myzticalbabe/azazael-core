# coding: utf-8
"""Web search: transport faked, no network. Contract over connectivity."""

import io
import json
import sys
import os
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE import web_search as ws


class _Headers(dict):
    def get_content_charset(self):
        ct = self.get("Content-Type", "")
        if "charset=" in ct:
            return ct.split("charset=")[-1].split(";")[0].strip()
        return None


class _Resp:
    def __init__(self, body=b"", status=200, ctype="text/html; charset=utf-8",
                 url="http://x/"):
        self._body = body
        self.status = status
        self.headers = _Headers({"Content-Type": ctype})
        self._url = url

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def getcode(self):
        return self.status

    def geturl(self):
        return self._url

    def close(self):
        pass


def _opener(resp=None, error=None):
    def _open(req, timeout=None):
        if error is not None:
            raise error
        return resp
    return _open


def test_fetch_ok_and_truncation():
    r = ws.fetch_url("http://x/", opener=_opener(_Resp(b"hello")))
    assert r.status == 200 and r.text == "hello" and not r.truncated
    assert r.error is None and r.retrieved_at
    big = ws.fetch_url("http://x/", max_bytes=4,
                       opener=_opener(_Resp(b"hello world")))
    assert big.truncated and big.text == "hell"


def test_fetch_refuses_scheme_and_nontext():
    r = ws.fetch_url("ftp://x/y")
    assert r.error and "scheme" in r.error
    r = ws.fetch_url("http://x/y",
                     opener=_opener(_Resp(b"PK", ctype="application/zip")))
    assert r.error and "content-type" in r.error


def test_fetch_reports_http_and_transport_errors():
    err = urllib.error.HTTPError("http://x/", 404, "nf", {}, io.BytesIO())
    r = ws.fetch_url("http://x/", opener=_opener(error=err))
    assert r.status == 404 and r.error
    r = ws.fetch_url("http://x/",
                     opener=_opener(error=TimeoutError("slow")))
    assert r.error and "TimeoutError" in r.error


def test_never_raises_on_garbage():
    assert ws.fetch_url("::::").error
    assert ws.search("", provider="nope")["error"]
    assert ws.cite(None) == ""
    assert ws.cite([{"title": "t"}]).startswith("- t <")


def test_wikipedia_search_parses_canned():
    payload = {"query": {"search": [
        {"title": "Cat", "snippet": 'a <span class="x">small</span> cat'},
        {"title": "Dog", "snippet": "big dog"}]}}
    body = json.dumps(payload).encode()
    p = ws.WikipediaProvider(opener=_opener(_Resp(body)))
    res = p.search("cat", limit=5)
    assert len(res) == 2
    assert res[0].url == "https://en.wikipedia.org/wiki/Cat"
    assert res[0].snippet == "a small cat"  # tags stripped
    assert res[0].source == "wikipedia" and res[0].retrieved_at


def test_wikipedia_handles_api_failure():
    p = ws.WikipediaProvider(opener=_opener(_Resp(b"not json")))
    assert p.search("x") == []
    p = ws.WikipediaProvider(opener=_opener(error=OSError("down")))
    assert p.search("x") == []
    assert p.fetch(ws.SearchResult()).error  # empty title


def test_registry_dispatch_loud_on_unknown():
    out = ws.search("q", provider="tavily")
    assert out["results"] == [] and "unknown provider" in out["error"]
    assert "wikipedia" in out["error"]


def test_search_and_cite_envelope():
    payload = {"query": {"search": [{"title": "Cat", "snippet": "s"}]}}
    import CORE.web_search as _m
    orig = _m.PROVIDERS["wikipedia"]
    _m.PROVIDERS["wikipedia"] = lambda **kw: ws.WikipediaProvider(
        opener=_opener(_Resp(json.dumps(payload).encode())), **kw)
    try:
        out = ws.search_and_cite("cat", limit=1)
    finally:
        _m.PROVIDERS["wikipedia"] = orig
    assert out["error"] is None and len(out["results"]) == 1
    assert "wikipedia.org/wiki/Cat" in out["citations"]
