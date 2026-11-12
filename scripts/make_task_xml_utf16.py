#!/usr/bin/env python3
"""Normalise Task Scheduler XML to the encoding schtasks.exe actually accepts.

`schtasks /Create /XML` fails with

    ERROR: The task XML is malformed.
    (1,40)::ERROR: unable to switch the encoding

whenever the declared encoding disagrees with the bytes on disk.  Task
Scheduler wants **UTF-16 with a BOM**, so a perfectly valid UTF-8 file is
rejected.  Both scheduler files in this repo shipped as UTF-8 while declaring
`encoding="UTF-16"` — invalid XML *and* unimportable, which is why
worm_matrix_scheduler.xml had never been registered.

This rewrites the file as UTF-16LE + BOM and points the declaration at it, so
the declaration and the bytes agree.

NOTE: write_file / most editors will save UTF-8 again. Re-run this after editing
      a task XML by hand.

Usage:
    python azazael/scripts/make_task_xml_utf16.py FILE [FILE ...]
    python azazael/scripts/make_task_xml_utf16.py --check FILE [FILE ...]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DECL_RE = re.compile(rb'^(<\?xml[^>]*?encoding=")([^"]+)(")', re.IGNORECASE)


def detect(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        return "utf-16le+BOM"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16be+BOM"
    # A UTF-8 declaration is only visible as bytes at the start of the file.
    head = raw[:120] if not raw.startswith(b"\xef\xbb\xbf") else raw[3:123]
    m = DECL_RE.match(head)
    bom = "+BOM" if raw.startswith(b"\xef\xbb\xbf") else "/no-BOM"
    return f"utf-8{bom} (declares {m.group(2).decode('ascii', 'replace') if m else '?'})"


def convert(path: Path) -> str:
    raw = path.read_bytes()
    # Read with the right decoder so we do not double-convert an already-UTF-16 file.
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le").lstrip("\ufeff")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be").lstrip("\ufeff")
    else:
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        text = raw.decode("utf-8")

    # Point the declaration at what we are about to write, so the declaration
    # and the bytes agree (which is the whole reason schtasks rejected them).
    text = re.sub(r'(<\?xml[^>]*?encoding=")[^"]+(")', r"\1UTF-16\2",
                  text, count=1, flags=re.IGNORECASE)

    path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
    return "utf-16le+BOM (declares UTF-16)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--check", action="store_true",
                    help="report encoding without rewriting; exit 1 if not UTF-16+BOM")
    args = ap.parse_args()

    rc = 0
    for path in args.files:
        if not path.exists():
            print(f"MISSING {path}", file=sys.stderr)
            rc = 1
            continue
        before = detect(path)
        if args.check:
            ok = before.startswith("utf-16") and "BOM" in before
            print(f"  {'OK  ' if ok else 'BAD '} {path.name}: {before}")
            if not ok:
                rc = 1
            continue
        convert(path)
        after = detect(path)
        print(f"  {path.name}: {before} -> {after}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
