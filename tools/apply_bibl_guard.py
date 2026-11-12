#!/usr/bin/env python3
"""Apply the BIBL self-index guard to any pillar's WORMZ.py.

Why a tool and not a copy: the quartet has four environments whose WORMZ.py
copies have drifted (Kali's was dated Oct 2, Windows' Oct 31 with other edits),
so overwriting one pillar's file with another's would ship unrelated drift. This
patches only the two sites that make up the guard, refuses if the anchor text is
not found, and is idempotent so it can be re-run on every pillar.

Usage:
    python tools/apply_bibl_guard.py /root/azazael            # dry run
    python tools/apply_bibl_guard.py /root/azazael --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ANCHOR_SKIP = '''    def _should_skip(self, path: Path) -> bool:
        """Check if path should be skipped."""
        path_str = str(path)
        for pattern in self.config.skip_patterns:'''

GUARDED_SKIP = '''    def _is_own_output(self, path) -> bool:
        """True when `path` is a file WORMZ itself writes into BIBL.

        Path prefixes are compared as normalised strings rather than via
        is_relative_to so this cannot raise inside a crawl loop.
        """
        out = str(self.bibl_path).replace("\\\\", "/").rstrip("/").lower()
        return str(path).replace("\\\\", "/").lower().startswith(out + "/")

    def _should_skip(self, path: Path) -> bool:
        """Check if path should be skipped.

        The first check is the BIBL self-index guard. WORMZ writes
        BIBL/<LETTER>.md, those files are .md inside the crawl root, and each
        carries `## <term>` headers that the markdown parser turns into fresh
        entries -- so every pass re-ingested the previous pass's output and
        appended it again. Growth is quadratic in passes, and a diff summary
        hides it because millions of lines read as one large file.
        """
        path_str = str(path)
        if self._is_own_output(path):
            return True
        for pattern in self.config.skip_patterns:'''

ANCHOR_MERGE = """            for entry in result.terms_found:
                if entry.term not in self.entries:"""

GUARDED_MERGE = """            for entry in result.terms_found:
                # Defence in depth: even if a caller hands us a pre-built
                # CrawlResult from BIBL (the guard in _should_skip only covers
                # files WE walk), never adopt our own output as knowledge.
                if self._is_own_output(entry.file_path):
                    continue
                if entry.term not in self.entries:"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tree_root", help="pillar tree, e.g. /root/azazael")
    ap.add_argument("--apply", action="store_true", help="write the change")
    a = ap.parse_args(argv)

    target = Path(a.tree_root) / "src" / "CORE" / "WORMS" / "WORMZ.py"
    if not target.is_file():
        print("MISSING: %s" % target)
        return 2
    src = target.read_text(encoding="utf-8")

    if "_is_own_output" in src:
        print("ALREADY GUARDED: %s" % target)
        return 0

    patched = src
    for anchor, replacement in ((ANCHOR_SKIP, GUARDED_SKIP),
                                (ANCHOR_MERGE, GUARDED_MERGE)):
        if anchor not in patched:
            # Refuse rather than guess: a drifted copy needs a human decision
            # about which anchor moved, and a silent skip would look like success.
            print("ANCHOR NOT FOUND in %s:\n%s" % (target, anchor.splitlines()[0]))
            return 3
        patched = patched.replace(anchor, replacement, 1)

    # Syntax-check before writing; a guard that breaks the crawler is worse
    # than a crawler that grows a big file.
    try:
        compile(patched, str(target), "exec")
    except SyntaxError as exc:
        print("REFUSING: patched source does not compile: %s" % exc)
        return 4

    if not a.apply:
        print("DRY RUN ok: %s would gain the guard (%d -> %d bytes)"
              % (target, len(src), len(patched)))
        return 0

    target.write_text(patched, encoding="utf-8")
    print("PATCHED: %s (%d -> %d bytes)" % (target, len(src), len(patched)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
