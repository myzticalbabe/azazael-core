"""A fetched body must be the document, not the transfer encoding.

`bcc_bypass.fetch_http` advertised `gzip, deflate, br` and stored `resp.read()`
undecoded, so a compressed response was written to the raw tree as bytes that are
not the document, with sha256 taken over those bytes -- the FETCH_LOG then
pointed at a blob and called it tier A evidence (found at HEAD 2026-11-05).

The live case at the end is the one that matters: the unit tests prove the
decoder, the served-gzip test proves the *path* that was broken.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import importlib.util
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "Project_ELIZA" / "tools" / "bcc_bypass.py"


@pytest.fixture(scope="module")
def bypass(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv("BCC_RAW_DIR", str(tmp_path_factory.mktemp("bcc_raw")))
    spec = importlib.util.spec_from_file_location("bcc_bypass_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch

    patcher = MonkeyPatch()
    yield patcher
    patcher.undo()


BODY = b"<html><body>Deere &amp; Company -- FTC complaint, page 1</body></html>"


def test_gzip_body_decodes_to_the_document(bypass):
    assert bypass._decode_body(gzip.compress(BODY), "gzip") == BODY


def test_deflate_body_decodes_when_zlib_wrapped(bypass):
    assert bypass._decode_body(zlib.compress(BODY), "deflate") == BODY


def test_deflate_body_decodes_when_raw(bypass):
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    raw = compressor.compress(BODY) + compressor.flush()
    assert bypass._decode_body(raw, "deflate") == BODY


def test_identity_and_empty_encoding_pass_through(bypass):
    assert bypass._decode_body(BODY, "") == BODY
    assert bypass._decode_body(BODY, "identity") == BODY


def test_unknown_encoding_raises_rather_than_storing_a_blob(bypass):
    with pytest.raises(ValueError):
        bypass._decode_body(BODY, "compress")


def test_brotli_is_only_advertised_when_it_can_be_decoded(bypass):
    advertised = bypass.STEALTH_HEADERS["Accept-Encoding"]
    if bypass._brotli_decoder() is None:
        assert "br" not in advertised.split(", "), "advertised br with no decoder installed"
        with pytest.raises(ValueError):
            bypass._decode_body(BODY, "br")
    else:
        assert "br" in advertised.split(", ")


def test_a_served_gzip_response_is_stored_as_the_document(bypass, tmp_path):
    """The path, not just the helper: a real server that really gzips."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            payload = gzip.compress(BODY)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # silence
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        scraper = bypass.BCCFetchEngine(raw_dir=tmp_path)
        url = f"http://127.0.0.1:{server.server_address[1]}/deere.html"
        result = asyncio.run(scraper.fetch_http(url, tier="A"))
    finally:
        server.shutdown()
        server.server_close()

    assert result.error is None, result.error
    assert result.status == 200
    assert result.encoding == "gzip"
    assert result.bytes == len(BODY), "stored length is the compressed length, not the document"
    stored = Path(result.raw_path).read_bytes()
    assert stored == BODY, "raw tree holds transfer bytes instead of the document"
    assert result.sha256 == hashlib.sha256(BODY).hexdigest(), "sha covers bytes that are not the document"

