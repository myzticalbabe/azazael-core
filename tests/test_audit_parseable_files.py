#!/usr/bin/env python3
# coding: utf-8
"""The mislabelled-file audit must actually recognise the shapes it exists for.

Why this file exists. `tools/audit_parseable_files.py` was written after a
ProPublica HTML page saved as `.pdf` aborted a 41-minute graphify run with a
single line ("EOF marker not found"). The audit is only worth having if its
signature table is right in both directions: it must FLAG a text-as-binary
impostor, and it must NOT flag a correctly-labelled file (a check that cries
wolf gets switched off). These tests pin both directions on synthetic bytes.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import audit_parseable_files as ap  # noqa: E402
from tools.audit_parseable_files import (  # noqa: E402
    SIGNATURES, TEXT_EXTS, _looks_textual)


class TestSignaturesAcceptRealFiles(unittest.TestCase):

    def test_pdf_magic(self):
        self.assertTrue(SIGNATURES[".pdf"](b"%PDF-1.7\n..."))

    def test_png_magic(self):
        self.assertTrue(SIGNATURES[".png"](b"\x89PNG\r\n\x1a\n" + b"\x00" * 8))

    def test_zip_container_for_office(self):
        self.assertTrue(SIGNATURES[".docx"](b"PK\x03\x04" + b"\x00" * 12))
        self.assertTrue(SIGNATURES[".xlsx"](b"PK\x03\x04" + b"\x00" * 12))

    def test_mp4_ftyp(self):
        self.assertTrue(SIGNATURES[".mp4"](b"\x00\x00\x00\x18ftypisom"))


class TestSignaturesRejectImpostors(unittest.TestCase):

    def test_html_labelled_pdf_is_rejected(self):
        """The exact 2026-09-21 shape: an HTML block page with a .pdf suffix."""
        head = b"<!DOCTYPE html><html><head>"
        self.assertFalse(SIGNATURES[".pdf"](head),
                         "an HTML page must not pass as a PDF")

    def test_jpeg_bytes_labelled_png_is_rejected(self):
        self.assertFalse(SIGNATURES[".png"](b"\xff\xd8\xff\xe0\x00\x10JFIF"))
        self.assertTrue(SIGNATURES[".jpg"](b"\xff\xd8\xff\xe0\x00\x10JFIF"))

    def test_empty_file_fails_every_signature(self):
        for ext, pred in SIGNATURES.items():
            self.assertFalse(pred(b""), f"a zero-byte {ext} must not validate")


class TestTextualProbe(unittest.TestCase):

    def test_html_impostor_is_named_as_text(self):
        self.assertIsNotNone(_looks_textual(b"<!doctype html><html>"))

    def test_binary_head_is_not_named_as_text(self):
        self.assertIsNone(_looks_textual(b"\x89PNG\r\n\x1a\n\x00\x00\x00\r"))

    def test_text_extensions_are_skipped_by_design(self):
        # graphify reads these as text, so a mislabel cannot abort a parse.
        for ext in (".md", ".html", ".txt"):
            self.assertIn(ext, TEXT_EXTS)


class TestDeepParseCatchesTruncatedPdf(unittest.TestCase):
    """A truncated PDF is exactly what the shallow check cannot see.

    It begins with `%PDF`, so the signature table accepts it, yet pypdf (the
    parser graphify picks for `.pdf`) rejects it at construction -- the
    2026-09-21 failure shape. Only a real parse distinguishes the two, which is
    why the audit offers `--deep` and why the startup guard asks for it.
    """

    @staticmethod
    def _write_truncated_pdf(path: Path) -> None:
        # A header every signature check accepts, with no xref table and no
        # `%%EOF` -- pypdf raises "EOF marker not found", as it did in the
        # original incident.
        path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n" + b"\x00" * 32)

    def test_shallow_scan_is_blind_to_a_truncated_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_truncated_pdf(Path(d) / "trunc.pdf")
            res = ap.scan(deep=False, root=Path(d))
            self.assertEqual(res["corrupt_pdf"], [],
                             "without --deep a truncated PDF is invisible")
            self.assertEqual(res["mismatched"], [],
                             "its header is a valid PDF signature")

    def test_deep_scan_reports_a_truncated_pdf(self):
        # The PDF engine is injected rather than assumed to be installed: the
        # contract under test is that a pypdf failure becomes a named
        # `corrupt_pdf` entry, not that this env happens to ship pypdf.
        fake = types.ModuleType("pypdf")

        def _refuse(_path):
            raise ValueError("EOF marker not found")

        fake.PdfReader = _refuse
        with tempfile.TemporaryDirectory() as d:
            self._write_truncated_pdf(Path(d) / "trunc.pdf")
            with mock.patch.dict(sys.modules, {"pypdf": fake}):
                res = ap.scan(deep=True, root=Path(d))
        self.assertEqual(len(res["corrupt_pdf"]), 1,
                         "--deep must surface the unreadable PDF")
        self.assertEqual(res["corrupt_pdf"][0]["path"], "trunc.pdf")
        self.assertIn("EOF marker not found", res["corrupt_pdf"][0]["detail"])

    def test_deep_scan_lists_a_readable_pdf(self):
        fake = types.ModuleType("pypdf")
        fake.PdfReader = lambda _p: types.SimpleNamespace(pages=[1])
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ok.pdf").write_bytes(b"%PDF-1.4\n" + b"\x00" * 32)
            with mock.patch.dict(sys.modules, {"pypdf": fake}):
                res = ap.scan(deep=True, root=Path(d))
        self.assertEqual(res["deep_pdfs"], ["ok.pdf"])
        self.assertEqual(res["corrupt_pdf"], [])
        self.assertFalse(res["deep_unavailable"])

    def test_missing_pdf_engine_is_reported_not_swallowed(self):
        """A missing pypdf must be LOUD -- a silent `--deep` is worse than none.

        `except ImportError: pass` made the deep pass a no-op that reported
        success. That is the same silent failure the audit exists to catch, so
        the contract now reports the engine as unavailable.
        """
        with tempfile.TemporaryDirectory() as d:
            self._write_truncated_pdf(Path(d) / "trunc.pdf")
            with mock.patch.dict(sys.modules, {"pypdf": None}):
                res = ap.scan(deep=True, root=Path(d))
        self.assertTrue(res["deep_unavailable"],
                        "a missing PDF engine must be surfaced, not ignored")
        self.assertEqual(res["corrupt_pdf"], [])


class TestDeepReporting(unittest.TestCase):
    """A deep pass must say WHAT it opened, not just what failed.

    `--deep` opens every PDF with pypdf. On a tree full of PDFs that is
    legitimately slow, and a run that only printed the corrupt count would be
    indistinguishable from a hang -- so the count of opened PDFs is part of the
    contract the startup guard reports on.
    """

    def test_scan_exposes_the_deep_pdf_contract(self):
        res = ap.scan(deep=False)
        self.assertIn("deep_pdfs", res)
        self.assertIn("deep_unavailable", res)
        self.assertEqual(res["deep_pdfs"], [],
                         "without --deep nothing should be deep-parsed")
        self.assertFalse(res["deep_unavailable"],
                         "no deep pass was asked for, so nothing is unavailable")

    def test_deep_pass_reports_the_number_it_opened(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ap.main(["--check", "--deep"])
        self.assertIn("deep-parsed pdfs:", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
