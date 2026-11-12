#!/usr/bin/env python3
"""
audit_links_refined.py -- dead-link + absolute-path audit for the corpus.

Scans every *.html file in the corpus root for internal relative href/src
references and verifies the target exists. Flags:

  - dead links      : href/src pointing at a relative *.html that is not present
  - absolute paths  : href/src using file:///C:/... that should be relative
  - external links  : http(s) links are reported as informational (not dead —
                      they are not fetched; offline audit only)

Output is machine-parseable for the pre-commit gate:

  Dead links: N
  Internal links (OK): N
  Broken fragments: N
  ZERO dead links            <- summary line the gate greps for

Fragment verification: any href that carries a #fragment -- same-page
(href="#id") or cross-file (href="other.html#id") -- must resolve to a real
id="..." in the target document. This catches deep links to section
anchors that rot when the section id is renamed. Third-party whole-page
capture files (saved copies of external sites, e.g. ProPublica/archive.org
pages) are exempted: their fragments are set by the site's own JS at
runtime and are reported informational, not broken.

Exit codes:
  0 = no dead links, no broken fragments
  1 = dead links or broken fragments found
  2 = usage/scan error

Run:  python3 audit_links_refined.py [corpus_dir]
      (default corpus dir: current working directory)
"""

import os
import re
import sys

# Only count href/src inside real tags that fetch resources: <a href>, <link href>,
# <script src>, <img src>, <iframe src>, <source src>, <video src>, <audio src>.
# Documentation examples inside <span class="mono"> or <code> are NOT links and
# must not be counted (real incident: AUDIT_REPORT.html shows the fix as
# href="worm_analysis_dashboard.html" inside a span and was falsely flagged).
TAG_HREF_RE = re.compile(
    r'<(?:a|link)\s[^>]*?(?:href)\s*=\s*["\']([^"\']+)["\']', re.I
)
TAG_SRC_RE = re.compile(
    r'<(?:script|img|iframe|source|video|audio)\s[^>]*?(?:src)\s*=\s*["\']([^"\'#]+)["\']', re.I
)
FILE_ABS_RE = re.compile(r'^file:///[A-Za-z]:', re.I)
REL_HTML_RE = re.compile(r'^[^:/?#]+\.html?$', re.I)
FRAG_ONLY_RE = re.compile(r'^#(.+)$')
FRAG_SUFFIX_RE = re.compile(r'^([^#]+)#(.+)$')
ID_RE = re.compile(r'\bid\s*=\s*["\']([^"\']+)["\']')

# Whole-page third-party capture files: their JS (and any fragments it toggles)
# belongs to the captured external site and cannot be verified statically.
THIRD_PARTY_CAPTURE = ("wb_datasets.html", "bcc_pp2.html")


def scan(corpus_dir: str):
    html_files = sorted(
        f for f in os.listdir(corpus_dir)
        if f.lower().endswith(".html")
    )
    # id="..." cache per file, loaded lazily so fragment checks stay cheap.
    id_cache = {}

    def ids_for(fname):
        if fname not in id_cache:
            try:
                with open(os.path.join(corpus_dir, fname), encoding="utf-8", errors="replace") as fh:
                    id_cache[fname] = set(ID_RE.findall(fh.read()))
            except OSError:
                id_cache[fname] = set()
        return id_cache[fname]

    dead = []
    broken_frags = []
    frag_external = []
    internal_ok = 0
    external = 0
    abs_paths = []

    for fname in html_files:
        path = os.path.join(corpus_dir, fname)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError as e:
            print(f"  ERROR reading {fname}: {e}")
            continue

        is_capture = fname in THIRD_PARTY_CAPTURE
        matches = list(TAG_HREF_RE.finditer(content)) + list(TAG_SRC_RE.finditer(content))
        for m in matches:
            target = m.group(1).strip()
            if not target:
                continue
            if FILE_ABS_RE.match(target):
                abs_paths.append((fname, target))
                continue
            if target.startswith(("http://", "https://", "//")):
                external += 1
                continue

            # Same-page fragment: href="#id" -> id must exist in this doc.
            fm = FRAG_ONLY_RE.match(target)
            if fm:
                frag_id = fm.group(1)
                if is_capture:
                    frag_external.append((fname, "#" + frag_id))
                elif frag_id not in ids_for(fname):
                    broken_frags.append((fname, "#" + frag_id))
                else:
                    internal_ok += 1
                continue

            # Cross-file, optionally with fragment: other.html or other.html#id.
            frag_id = None
            cm = FRAG_SUFFIX_RE.match(target)
            if cm:
                target = cm.group(1)
                frag_id = cm.group(2)
            if REL_HTML_RE.match(target):
                target_path = os.path.join(corpus_dir, target)
                if not os.path.exists(target_path):
                    dead.append((fname, m.group(1)))
                    continue
                internal_ok += 1
                if frag_id:
                    if is_capture or frag_id not in ids_for(target):
                        (frag_external if is_capture else broken_frags).append(
                            (fname, m.group(1))
                        )

    # ---- report ----
    print(f"Audited {len(html_files)} HTML files in {corpus_dir}")
    print(f"Dead links: {len(dead)}")
    print(f"Broken fragments: {len(broken_frags)}")
    print(f"Internal links (OK): {internal_ok}")
    print(f"External links (informational): {external}")
    if abs_paths:
        print(f"Absolute file:// paths (should be relative): {len(abs_paths)}")
        for src, tgt in abs_paths[:15]:
            print(f"  {src} -> {tgt}")
    if dead:
        print("\nDEAD LINKS:")
        for src, tgt in dead[:50]:
            print(f"  {src} -> {tgt}")
    if broken_frags:
        print("\nBROKEN FRAGMENTS (target id missing in destination):")
        for src, tgt in broken_frags[:50]:
            print(f"  {src} -> {tgt}")
    if frag_external:
        print(f"\nThird-party capture fragments (informational): {len(frag_external)}")
        for src, tgt in frag_external[:10]:
            print(f"  {src} -> {tgt}")
    if not dead and not broken_frags and not abs_paths:
        print("\nZERO dead links")
    return 1 if (dead or broken_frags) else (2 if abs_paths else 0)


if __name__ == "__main__":
    corpus_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    sys.exit(scan(corpus_dir))