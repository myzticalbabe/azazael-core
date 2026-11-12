#!/usr/bin/env python3
"""Apply the Kilo lane's typecheck slice repairs, with assertions.

Every edit is a byte-level replacement guarded by an exact-match assertion, so
an unexpected file shape fails loudly instead of writing a half-fix. Line
endings are detected per file and preserved.

Usage: apply_fixes.py <slice>
  slice1 -- 8 CODEX/VIDEOS files: `from pathlib import Path` out of the docstring
  slice2 -- 8 files: create_video PIL, OMNI_WORM typo, WORMZ_POPUP Path,
           enhanced_animations argparse, REAL_FRAMEWORK_TEST threading,
           generate_matrix dead expr, generate_dashboard font_variant
"""
import io
import os
import sys

REPO = r"C:\Azazael\azazael"

# ---------------------------------------------------------------- slice 1 ---
SLICE1 = [
    "src/CODEX/VIDEOS/create_scrolling_video.py",
    "src/CODEX/VIDEOS/make_video.py",
    "src/CODEX/VIDEOS/make_video_quad.py",
    "src/CODEX/VIDEOS/make_video_vibrant.py",
    "src/CODEX/VIDEOS/make_video_zip.py",
    "src/CODEX/VIDEOS/make_video_zipper.py",
    "src/CODEX/VIDEOS/make_video_docs.py",
    "src/CODEX/VIDEOS/make_video_v2.py",
]

# ---------------------------------------------------------------- slice 2 ---
SLICE2 = [
    "src/CODEX/VIDEOS/make_video_v3.py",
    "src/CODEX/VIDEOS/create_video.py",
    "src/CORE/WORMS/OMNI_WORM.py",
    "src/CORE/WORMS/WORMZ_POPUP.py",
    "src/CODEX/CORE/enhanced_animations.py",
    "src/REAL_FRAMEWORK_TEST.py",
    "src/CODEX/VIDEOS/generate_matrix_video.py",
    "src/CODEX/VIDEOS/generate_dashboard_video.py",
]


def abspath(rel):
    return os.path.join(REPO, rel.replace("/", "\\"))


def detect_eol(data):
    return b"\r\n" if b"\r\n" in data else b"\n"


def replace_once(data, old, new, rel, what):
    """Replace `old` exactly once. Fail closed otherwise."""
    n = data.count(old)
    if n != 1:
        raise SystemExit("%s: %s -- expected 1 occurrence, found %d" % (rel, what, n))
    return data.replace(old, new, 1)


def pull_out_of_docstring(data, eol, import_line, rel):
    """Remove an import line that was written INSIDE the module docstring."""
    old = b'"""' + eol + import_line + eol + eol
    new = b'"""' + eol
    return replace_once(data, old, new, rel, "docstring-swallowed %r" % import_line)


def add_import_after(data, eol, anchor, import_line, rel):
    """Insert an import line immediately after `anchor`."""
    old = anchor + eol
    new = anchor + eol + import_line + eol
    return replace_once(data, old, new, rel, "anchor %r" % anchor)


#: Context-qualified anchors for the import block. `import os` alone is not
#: unique -- create_scrolling_video.py has three occurrences -- so the anchor
#: always carries the following import line, which is unique in the header.
PATH_ANCHORS = (
    b"import os" + b"\n" + b"\n" + b"from PIL import Image, ImageDraw, ImageFont",
    b"import os" + b"\n" + b"\n" + b"import subprocess",
)

#: A statement whose value is discarded and which nothing reads. `WIDTH // 2 - N`
#: is the remnant of a mangled `right_x = WIDTH // 2 - N`; no identifier
#: derived from it exists anywhere in the file, so removing it is provably
#: behaviour-identical.
DEAD_EXPR = {
    "src/CODEX/VIDEOS/make_video_docs.py": b"    WIDTH // 2 - 80",
    "src/CODEX/VIDEOS/make_video_v2.py": b"    WIDTH // 2 - 60",
    "src/CODEX/VIDEOS/make_video_v3.py": b"    WIDTH // 2 - 80",
}


def fix_path_docstring(rel):
    """`from pathlib import Path` out of the docstring, into the import block."""
    p = abspath(rel)
    with io.open(p, "rb") as fh:
        data = fh.read()
    eol = detect_eol(data)
    data = pull_out_of_docstring(data, eol, b"from pathlib import Path", rel)

    anchors = [a.replace(b"\n", eol) for a in PATH_ANCHORS]
    # Anchor on the FIRST `import os` in the file -- the header block. Later
    # function-local `import os` statements must not be matched.
    first = data.find(b"import os")
    if first < 0:
        raise SystemExit("%s: no 'import os' found" % rel)
    window = data[first:first + 200]
    hits = [a for a in anchors if window.startswith(a)]
    if len(hits) != 1:
        raise SystemExit("%s: expected 1 header import anchor, got %d"
                         % (rel, len(hits)))
    old = hits[0] + eol
    new = hits[0] + eol + b"from pathlib import Path" + eol
    data = replace_once(data, old, new, rel, "import-block anchor")

    dead = DEAD_EXPR.get(rel)
    if dead:
        data = replace_once(data, dead + eol, b"", rel, "dead expression")
    with io.open(p, "wb") as fh:
        fh.write(data)
    return "path-import" + ("+dead-expr" if dead else "")


def fix_create_video(rel):
    """PIL is an optional dep here (moviepy is imported the same way), so the
    import belongs at the point of use, not at module scope."""
    p = abspath(rel)
    with io.open(p, "rb") as fh:
        data = fh.read()
    eol = detect_eol(data)
    old = (b'def create_frame(duration_sec, resolution=(1920, 1080)):'
           + eol + b'    """Create a single video frame."""' + eol)
    new = (old + eol + b'    from PIL import Image, ImageDraw' + eol)
    data = replace_once(data, old, new, rel, "create_frame body")
    with io.open(p, "wb") as fh:
        fh.write(data)
    return "pil-local-import"


def fix_omni_worm(rel):
    """`rbPath` is a typo; `from pathlib import Path` is already imported."""
    p = abspath(rel)
    with io.open(p, "rb") as fh:
        data = fh.read()
    data = replace_once(data, b"rbPath.home()", b"Path.home()", rel, "rbPath typo")
    with io.open(p, "wb") as fh:
        fh.write(data)
    return "typo-rbPath"


def fix_wormz_popup(rel):
    p = abspath(rel)
    with io.open(p, "rb") as fh:
        data = fh.read()
    eol = detect_eol(data)
    data = add_import_after(data, eol, b"import sys", b"from pathlib import Path", rel)
    with io.open(p, "wb") as fh:
        fh.write(data)
    return "path-import"


def fix_enhanced_animations(rel):
    """`import argparse` was written into the docstring's usage example."""
    p = abspath(rel)
    with io.open(p, "rb") as fh:
        data = fh.read()
    eol = detect_eol(data)
    old = b"    from enhanced_animations import ANIMATIONS, generate_css" + eol \
        + b"    import argparse" + eol
    new = b"    from enhanced_animations import ANIMATIONS, generate_css" + eol
    data = replace_once(data, old, new, rel, "docstring argparse example")
    data = add_import_after(data, eol, b"import json", b"import argparse", rel)
    with io.open(p, "wb") as fh:
        fh.write(data)
    return "argparse-module-level"


def fix_real_framework_test(rel):
    p = abspath(rel)
    with io.open(p, "rb") as fh:
        data = fh.read()
    eol = detect_eol(data)
    data = add_import_after(data, eol, b"import time", b"import threading", rel)
    with io.open(p, "wb") as fh:
        fh.write(data)
    return "threading-import"


def fix_matrix_video(rel):
    """`x // col_width` is a statement whose value is discarded. `col_width`
    exists only to feed it, so both go; `num_columns` stays so the random
    stream is bit-identical."""
    p = abspath(rel)
    with io.open(p, "rb") as fh:
        data = fh.read()
    eol = detect_eol(data)
    old = b"                col_width = width // num_columns" + eol \
        + b"                x // col_width" + eol
    new = b""
    data = replace_once(data, old, new, rel, "dead column expression")
    with io.open(p, "wb") as fh:
        fh.write(data)
    return "dead-expression"


def fix_dashboard_video(rel):
    """`font_variant` exists only on FreeTypeFont; `load_default(size=...)`
    returns one directly and is the documented Pillow API for a sized default
    font (Pillow >= 10.1; this repo pins 12.x)."""
    p = abspath(rel)
    with io.open(p, "rb") as fh:
        data = fh.read()
    data = replace_once(
        data,
        b"ImageFont.load_default().font_variant(size=size)",
        b"ImageFont.load_default(size=size)",
        rel, "font_variant call")
    with io.open(p, "wb") as fh:
        fh.write(data)
    return "font-load-default-size"


def fix_dead_expr_only(rel):
    """Remove a discarded-value statement without touching anything else."""
    p = abspath(rel)
    with io.open(p, "rb") as fh:
        data = fh.read()
    eol = detect_eol(data)
    dead = DEAD_EXPR.get(rel)
    if not dead:
        raise SystemExit("%s: no dead-expression spec" % rel)
    data = replace_once(data, dead + eol, b"", rel, "dead expression")
    with io.open(p, "wb") as fh:
        fh.write(data)
    return "dead-expr"


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which == "slice1":
        targets = [(rel, fix_path_docstring) for rel in SLICE1]
    elif which == "slice2":
        targets = [
            ("src/CODEX/VIDEOS/make_video_v3.py", fix_path_docstring),
            ("src/CODEX/VIDEOS/create_video.py", fix_create_video),
            ("src/CORE/WORMS/OMNI_WORM.py", fix_omni_worm),
            ("src/CORE/WORMS/WORMZ_POPUP.py", fix_wormz_popup),
            ("src/CODEX/CORE/enhanced_animations.py", fix_enhanced_animations),
            ("src/REAL_FRAMEWORK_TEST.py", fix_real_framework_test),
            ("src/CODEX/VIDEOS/generate_matrix_video.py", fix_matrix_video),
            ("src/CODEX/VIDEOS/generate_dashboard_video.py", fix_dashboard_video),
        ]
    elif which == "deadexpr":
        targets = [(rel, fix_dead_expr_only)
                   for rel in (sys.argv[2:] or list(DEAD_EXPR))]
    else:
        raise SystemExit("usage: apply_fixes.py slice1|slice2|deadexpr [files]")

    for rel, fn in targets:
        note = fn(rel)
        print("  OK  %-46s %s" % (rel, note))
    print("applied %d edits" % len(targets))


if __name__ == "__main__":
    main()
