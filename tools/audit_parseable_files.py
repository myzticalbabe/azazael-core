#!/usr/bin/env python3
# coding: utf-8
"""MISLABELLED-FILE AUDIT -- does a binary file's contents match its extension?

Why this exists. On 2026-09-21 `graphify update .` died 1522s into a 2463s run
with a single reported line, "EOF marker not found", and the refresh was stuck
for the rest of the day. The cause was not the tree and not the quota: it was
one file, archive/restored/Libra_Desktop/filings/*.pdf, that was a ProPublica
HTML block page saved with a .pdf extension. pypdf refused it, and graphify
aborted the whole extraction on it.

That is a class of defect, not an incident. graphify classifies a file by its
SUFFIX alone (detect.classify_file: `path.suffix.lower()`), then hands it to a
parser chosen by that suffix. A suffix that lies -- an HTML page named .pdf, a
truncated zip named .docx, a PNG named .jpg -- sends the wrong parser at the
bytes, and the failure surfaces as a stalled run rather than a named file.

The audit is content-based and conservative: it reads only the first few KB,
checks each binary format against its magic bytes, and (optionally) asks pypdf
to open every .pdf for real. Text formats (.md/.html/.txt/...) are skipped --
graphify reads them as text, so a mislabel there cannot abort a parse.

    python tools/audit_parseable_files.py            # report
    python tools/audit_parseable_files.py --check     # exit 1 on any mismatch
    python tools/audit_parseable_files.py --deep      # also open every .pdf
    python tools/audit_parseable_files.py --fix       # rename obvious text-as-binary offenders
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]

#: Known-good offenders are recorded once and only DELTAS fail afterwards -- the
#: same regression principle as fs_hygiene.py. 63 JPEG-as-PNG fixtures in a
#: vendored onnxruntime tree are a fact (and cannot be renamed without breaking
#: that project's own tests); a check that fails on facts gets switched off, so
#: they are baselined and never re-litigated.
BASELINE = ROOT / "logs" / "parseable_files_baseline.json"

#: Directories that never hold authored content and are too large to be worth a
#: per-file read. Kept in sync with fs_hygiene.py's heavy-dir set.
SKIP_DIRS = {
    ".git", ".venv-3.14t", ".venv", ".mypy_cache", ".nox", ".pytest_cache",
    "__pycache__", "node_modules", "nuitka_build_backup",
}


def _is_zip(head: bytes) -> bool:
    return head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _is_riff(head: bytes, kind: bytes) -> bool:
    return head[:4] == b"RIFF" and head[8:12] == kind


#: extension -> predicate over the first 16 bytes. Each predicate answers the
#: only question that matters downstream: will the parser chosen for this suffix
#: be looking at the bytes it expects?
SIGNATURES: dict[str, Callable[[bytes], bool]] = {
    ".pdf":  lambda h: h[:4] == b"%PDF",
    ".png":  lambda h: h[:8] == b"\x89PNG\r\n\x1a\n",
    ".jpg":  lambda h: h[:3] == b"\xff\xd8\xff",
    ".jpeg": lambda h: h[:3] == b"\xff\xd8\xff",
    ".gif":  lambda h: h[:6] in (b"GIF87a", b"GIF89a"),
    ".webp": lambda h: _is_riff(h, b"WEBP"),
    ".docx": _is_zip,                      # OOXML is a zip container
    ".xlsx": _is_zip,
    ".mp4":  lambda h: h[4:8] == b"ftyp",
    ".m4v":  lambda h: h[4:8] == b"ftyp",
    ".m4a":  lambda h: h[4:8] == b"ftyp",
    ".mov":  lambda h: h[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free"),
    ".webm": lambda h: h[:4] == b"\x1aE\xdf\xa3",
    ".mkv":  lambda h: h[:4] == b"\x1aE\xdf\xa3",
    ".avi":  lambda h: _is_riff(h, b"AVI "),
    ".wav":  lambda h: _is_riff(h, b"WAVE"),
    ".mp3":  lambda h: h[:3] == b"ID3" or h[:2] in (
        b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa"),
    ".ogg":  lambda h: h[:4] == b"OggS",
}

#: Suffixes whose content is text and whose mislabelling is harmless to parsing.
TEXT_EXTS = {".md", ".mdx", ".qmd", ".txt", ".rst", ".html", ".yaml", ".yml", ".svg"}

#: A head that decodes cleanly as UTF-8 with these markers is almost certainly a
#: text/HTML file wearing a binary suffix -- the exact 2026-09-21 shape.
_TEXT_HINTS = (b"<!doctype html", b"<html", b"<?xml", b"{\n", b"<svg")


def _looks_textual(head: bytes) -> str | None:
    low = head.lower()
    for hint in _TEXT_HINTS:
        if hint in low:
            return hint.decode("ascii", "replace")
    return None


def _head(path: Path, n: int = 16) -> bytes:
    with path.open("rb") as fh:
        return fh.read(n)


def scan(deep: bool = False, root: Path | None = None) -> dict:
    """One bounded walk. Returns findings grouped by kind.

    `root` defaults to the repo root but is overridable so the deep-parse path
    can be exercised against a synthetic tree in tests -- a truncated PDF is
    the case the shallow signature check cannot see, and it needs a file that
    really fails to parse to prove the deep pass catches it.
    """
    base = Path(root) if root is not None else ROOT
    mismatched: list[dict] = []
    corrupt_pdf: list[dict] = []
    #: PDFs pypdf actually OPENED. Reported so a slow deep pass is attributable
    #: to a named file rather than looking like the audit simply hanging.
    deep_pdfs: list[str] = []
    checked = 0
    deep = deep or False
    # Resolve the PDF engine ONCE, and REMEMBER when it is missing. A missing
    # engine used to be swallowed per-file (`except ImportError: pass`), which
    # made `--deep` a silent no-op reporting success -- the same class of defect
    # this whole audit exists to catch.
    pdf_reader = None
    deep_unavailable = False
    if deep:
        try:
            from pypdf import PdfReader  # noqa: PLC0415
            pdf_reader = PdfReader
        except ImportError:
            deep_unavailable = True
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            ext = Path(name).suffix.lower()
            pred = SIGNATURES.get(ext)
            if pred is None:
                continue
            path = Path(dirpath) / name
            try:
                head = _head(path)
            except OSError:
                continue
            checked += 1
            if not pred(head):
                hint = _looks_textual(head)
                mismatched.append({
                    "path": str(path.relative_to(base)).replace("\\", "/"),
                    "ext": ext,
                    "head": head[:8].hex(),
                    "looks_like": ("text/" + hint) if hint else "binary (unknown)",
                })
                continue
            if deep and ext == ".pdf" and pdf_reader is not None:
                rel = str(path.relative_to(base)).replace("\\", "/")
                try:
                    reader = pdf_reader(str(path))
                    _ = len(reader.pages)          # force a real parse
                    deep_pdfs.append(rel)
                except Exception as exc:
                    corrupt_pdf.append({
                        "path": rel,
                        "error": type(exc).__name__,
                        "detail": str(exc)[:120],
                    })
    return {"checked": checked, "mismatched": mismatched,
            "corrupt_pdf": corrupt_pdf, "deep_pdfs": deep_pdfs,
            "deep_unavailable": deep_unavailable}


def _load_baseline() -> set[str]:
    """Paths already known to be mislabelled. Unreadable baseline = empty set.

    An empty set is the strict answer and the deliberate one: a missing baseline
    means every offender is new, which is a louder failure than silence.
    """
    try:
        data = json.loads(BASELINE.read_text(encoding="utf-8"))
        return set(data.get("mismatched", []))
    except Exception:
        return set()


def _save_baseline(paths: list[str]) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(
        json.dumps({"recorded": datetime.now(timezone.utc).isoformat(),
                    "mismatched": sorted(paths)}, indent=1),
        encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if a NEW file's bytes do not match its extension")
    ap.add_argument("--deep", action="store_true",
                    help="also open every .pdf with pypdf to catch truncated files")
    ap.add_argument("--baseline", action="store_true",
                    help="record the current offenders as known facts (regression baseline)")
    ap.add_argument("--fix", action="store_true",
                    help="rename binary-suffixed files whose bytes are clearly text to .html")
    args = ap.parse_args(argv)

    res = scan(deep=args.deep)

    if args.baseline:
        _save_baseline([r["path"] for r in res["mismatched"]])
        print("baselined %d known mislabelled file(s) -> %s"
              % (len(res["mismatched"]), BASELINE.name))
        return 0
    print(f"checked {res['checked']} parseable-binary file(s)")
    print(f"mislabelled: {len(res['mismatched'])}")

    # Group before listing: 66 files that are 64 copies of the same shape is a
    # finding about ONE shape, and a wall of identical lines hides that.
    groups: dict[tuple[str, str], list[str]] = {}
    for r in res["mismatched"]:
        groups.setdefault((r["ext"], r["looks_like"]), []).append(r["path"])
    for (ext, looks), paths in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  [{len(paths)}] {ext} -> {looks}")
        for p in paths[:3]:
            print(f"        {p}")
        if len(paths) > 3:
            print(f"        ... and {len(paths) - 3} more")
    if args.deep:
        # Name what was opened as well as what failed. A deep pass over a tree
        # full of PDFs is legitimately slow, and without this the run is
        # indistinguishable from a hang.
        if res["deep_unavailable"]:
            print("deep-unavailable: pypdf is not importable with this "
                  f"interpreter ({sys.executable}) -- the deep PDF check was "
                  "SKIPPED, so truncated PDFs would NOT be caught")
        pdfs = res["deep_pdfs"]
        print(f"deep-parsed pdfs: {len(pdfs)}")
        for p in pdfs[:10]:
            print(f"  ok {p}")
        if len(pdfs) > 10:
            print(f"  ... and {len(pdfs) - 10} more")
        print(f"corrupt pdfs: {len(res['corrupt_pdf'])}")
        for r in res["corrupt_pdf"]:
            print(f"  {r['path']}  ({r['error']}: {r['detail']})")

    if args.fix:
        fixed = 0
        for r in res["mismatched"]:
            if r["looks_like"].startswith("text/"):
                src = ROOT / r["path"]
                dst = src.with_suffix(".html")
                if dst.exists():
                    print(f"  skip (target exists): {r['path']}")
                    continue
                src.rename(dst)
                print(f"  renamed: {r['path']} -> {dst.name}")
                fixed += 1
        print(f"fixed {fixed} file(s)")
        return 0

    # Regression semantics: only offenders NOT in the baseline fail. The legacy
    # set is still reported, so it stays visible without being a nightly red.
    baseline = _load_baseline()
    new_mismatch = [r for r in res["mismatched"] if r["path"] not in baseline]
    legacy = len(res["mismatched"]) - len(new_mismatch)
    new_empty = [r for r in new_mismatch if not r["head"]]
    if legacy:
        print(f"legacy mislabels (baselined, not failing): {legacy}")
    if new_mismatch:
        print(f"NEW mislabels: {len(new_mismatch)}")
        for r in new_mismatch[:10]:
            print(f"  {r['path']}  ({r['ext']} -> {r['looks_like']})")
    if new_empty:
        print(f"zero-byte parseable files (no parser can read them): {len(new_empty)}")
        for r in new_empty:
            print(f"  {r['path']}")

    bad = len(new_mismatch) + len(res["corrupt_pdf"])
    if args.check and bad:
        print(f"FAIL: {bad} NEW file(s) whose contents do not match their extension")
        return 1
    print("OK: no new mislabelled files"
          if not bad else "WARN: see findings above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
