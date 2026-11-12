"""Tests for the challenge ladder: detection names the wall, the ladder
climbs, and a surviving challenge reads `blocked` -- never empty, never
invented. One live test renders a real page headless."""

import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from eve_unleashed import websearch as _w

# Skip tests requiring optional dependencies
try:
    import curl_cffi
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

try:
    import scrapling
    HAS_SCRAPLING = True
except ImportError:
    HAS_SCRAPLING = False

try:
    import playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


def test_detect_cloudflare_iuam():
    body = ("<html><head><title>Just a moment...</title></head><body>"
            "<form id='cf-chl'>checking your browser</form></body></html>")
    assert _w.detect_challenge(403, {}, body) == "cloudflare-iuam"


def test_detect_turnstile():
    body = ('<div class="cf-turnstile" data-sitekey="x"></div>'
            '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js">')
    assert _w.detect_challenge(200, {}, body) == "cloudflare-iuam"


def test_detect_cf_ray_403():
    assert _w.detect_challenge(403, {"Server": "cloudflare",
                                     "CF-RAY": "abc123"}, "<html>deny</html>"
                             ) == "cloudflare-deny"


def test_detect_imperva_incapsula():
    body = ("<html><body><script>var z='visid_incap_123';</script>"
            "<iframe src='/._incap_/res/'>Request unsuccessful. "
            "Incapsula incident ID</iframe></body></html>")
    assert _w.detect_challenge(403, {"X-Iinfo": "1-2-3"}, body) == \
        "imperva"


def test_clean_page_is_none():
    body = "<html><body><h1>Example Domain</h1></body></html>"
    assert _w.detect_challenge(200, {}, body) is None


def test_bare_403_is_not_challenge():
    assert _w.detect_challenge(403, {}, "<html>forbidden</html>") is None


def test_fetch_plain_page_ok_first_rung():
    res = _w.fetch_visible("https://example.com")
    assert res["state"] == "ok", res
    assert res["rung"] == "plain"
    assert "Example Domain" in res["text"]


def test_fetch_empty_url_rejected():
    import pytest
    with pytest.raises(ValueError):
        _w.fetch_visible("   ")


@pytest.mark.skipif(not HAS_CURL_CFFI, reason="curl_cffi not installed")
def test_tls_impersonation_rung_live():
    body, profile, err = _w._curl_cffi_fetch("https://example.com")
    assert err is None, err
    assert profile in ("chrome124", "chrome120", "safari15_5",
                       "edge101")
    assert "documentation examples" in body


def test_archive_lookup_url_shape():
    api = _w._archive_urls("https://example.com/a?b=c")
    assert api.startswith(
        "https://archive.org/wayback/available?url=")
    assert "example.com" in api


@pytest.mark.skipif(True, reason="network timeout - archive.org fallback is slow")
def test_archive_fallback_live_snapshot():
    import pytest
    body, snap_url, err = _w._archive_fallback("https://example.com")
    if err and "429" in err:
        pytest.skip("archive.org throttling this IP tonight: %s" % err)
    assert err is None, err
    assert "web.archive.org" in snap_url
    assert len(body) > 500


def test_proxy_rotation_picks_from_list(monkeypatch):
    monkeypatch.setenv("PROXY_ROTATE", "http://a:1, http://b:2")
    monkeypatch.delenv("PROXY_URL", raising=False)
    seen = {_w._pick_proxy() for _ in range(20)}
    assert seen <= {"http://a:1", "http://b:2"}
    monkeypatch.delenv("PROXY_ROTATE")
    monkeypatch.setenv("PROXY_URL", "http://solo:3")
    assert _w._pick_proxy() == "http://solo:3"
    monkeypatch.delenv("PROXY_URL")
    assert _w._pick_proxy() is None


def test_cc_pick_selects_html_200():
    nd = ('{"url":"x","status":"404","mime":"text/html",'
          '"filename":"a.warc.gz","offset":"0","length":"99"}\n'
          '{"url":"x","status":"200","mime":"application/pdf",'
          '"filename":"b.warc.gz","offset":"10","length":"99"}\n'
          '{"url":"x","status":"200","mime":"text/html",'
          '"filename":"c.warc.gz","offset":"20","length":"1234"}\n')
    rec = _w._cc_pick(nd)
    assert rec and rec["filename"] == "c.warc.gz"
    assert _w._cc_pick("not json\n{}") is None


def test_cc_extract_unpacks_gzip_warc():
    import gzip
    inner = b"<h1>from the crawl</h1>"
    blob = (b"WARC/1.0\r\nWARC-Type: response\r\n\r\n"
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
            + inner)
    body, err = _w._cc_extract(gzip.compress(blob))
    assert err is None, err
    assert "from the crawl" in body
    body2, err2 = _w._cc_extract(blob)
    assert err2 is None, err2
    assert "from the crawl" in body2
    _, err3 = _w._cc_extract(b"garbage without structure")
    assert err3 is not None


def test_commoncrawl_live_shape():
    import pytest
    body, cid, err = _w._commoncrawl_fetch("https://example.com")
    if err and ("HTTP Error" in err or "timed out" in err
                or "unreachable" in err or "no usable capture" in err):
        pytest.skip("crawl index unreachable tonight: %s" % err)
    assert err is None, err
    assert cid.startswith("CC-MAIN-")
    assert len(body) > 500


@pytest.mark.skipif(not HAS_SCRAPLING, reason="scrapling not installed")
def test_stealth_render_live():
    body, err = _w._scrapling_fetch("https://example.com")
    assert err is None, err
    assert "documentation examples" in body


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")
def test_live_headless_render_executes():
    text, err = _w._render_fetch("https://example.com")
    assert err is None, err
    assert "documentation examples" in text
