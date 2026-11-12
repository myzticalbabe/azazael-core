#!/usr/bin/env python3
"""
# WRITERSHIP (R0 ruling 2026-10-28, keep-both; amended MS1 2026-10-28): this script
# is the STRUCTURAL WRITER of scripts/bcc_gap_index.json (tracked dict: creates keys
# and schema). Field updates (new_sources/new_evidence/last_updated/
# investigation_metadata only, never add/remove keys) belong to
# scripts/land_all_results.py. scripts/update_ddap_selenium_status.py is the blessed
# field writer for the selenium trio only (selenium_status/selenium_checked/
# last_selenium_check, per-gap scalars, never add/remove keys). Never write
# *.enriched.json here; enrichment belongs to scripts/gap_metadata.py only.
build_gap_index.py -- deterministic generator/verifier for the BCC gap index.

The machine-readable gap index in INVESTIGATION_GAPS.html is the canonical
source of truth: a <table id="gap-N"> row set plus an embedded
<script type="application/json" id="gap-index-json"> block carrying the same
data as {"N": {"topic": ..., "title": ...}}.

This script makes that block maintainable and verifiable:

  build_gap_index.py extract  DOC [--out gap_index.json]
      Pull the canonical JSON out of the doc into a standalone file.

  build_gap_index.py write    DOC
      Regenerate the <div id="gap-index"> table + JSON script block inside the
      doc from the canonical JSON embedded in the SAME doc (in-place, lossless
      if content already matches -- run with --check first to confirm).

  build_gap_index.py anchors  DOC [--corpus DIR] [--strict]
      Verify every hub-doc anchor line (<p class="hub-gap-anchor">Linked gaps:
      #N...</p>) resolves to an id present in the index. Reports dangling cites
      and exits 1 on any (or, with --strict, also on indexed-but-uncited ids).

The doc must already contain the gap-index block; `extract` is the bootstrap
step, `write` keeps the embedded JSON and the rendered table in lockstep so
future edits only touch the JSON, then run `write` to re-render.

Run:  python build_gap_index.py extract INVESTIGATION_GAPS.html
      python build_gap_index.py write  INVESTIGATION_GAPS.html --check
      python build_gap_index.py anchors INVESTIGATION_GAPS.html --corpus .
"""

import argparse
import json
import os
import re
import sys

DOC_PATH = os.environ.get("GAP_INDEX_DOC", "INVESTIGATION_GAPS.html")

# --- parsing helpers ---------------------------------------------------------

TABLE_RE = re.compile(r'<div id="gap-index">.*?</table>', re.S)
JSON_BLOCK_RE = re.compile(
    r'<script type="application/json" id="gap-index-json">(.*?)</script>', re.S
)
ROW_RE = re.compile(r'<tr id="gap-(\d+)"><td>\d+</td><td>(.*?)</td><td>(.*?)</td></tr>', re.S)
ANCHOR_RE = re.compile(r'<p class="hub-gap-anchor">Linked gaps: ([^<]+?)</p>')


def _h(text: str) -> str:
    """Escape for HTML attribute/text content (keep entities readable)."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def load_canonical_json(doc_text: str) -> dict:
    """Return the canonical gap dict from the embedded JSON block."""
    m = JSON_BLOCK_RE.search(doc_text)
    if not m:
        raise SystemExit("ERROR: no id=gap-index-json script block found in doc")
    data = json.loads(m.group(1))
    if isinstance(data, dict):
        return data
    raise SystemExit("ERROR: gap-index-json is not a JSON object")


def render_block(data: dict) -> str:
    """Render the <div id=gap-index> ... </table> + JSON script block."""
    nums = sorted(int(k) for k in data)
    rows = []
    for n in nums:
        rec = data[str(n)]
        topic = rec.get("topic", "")
        title = rec.get("title", "")
        leads = rec.get("leads") or []
        lead_html = ""
        if leads:
            refs = " · ".join(
                f'<a href="BCC_LEAD_FINDINGS.html#{l}" style="color:var(--a);">{l}</a>'
                for l in leads
            )
            lead_html = (
                '<div class="gap-lead-ref" '
                'style="margin-top:3px;font-size:11px;opacity:.9;">'
                f"→ leads: {refs}</div>"
            )
        rows.append(
            f'<tr id="gap-{n}"><td>{n}</td><td>{_h(topic)}</td>'
            f'<td>{_h(title)}{lead_html}</td></tr>'
        )
    table = (
        '<div id="gap-index">\n'
        f'<h2>Machine-readable gap index (auto-generated; {len(nums)} gaps)</h2>\n'
        '<table><thead><tr><th>Gap</th><th>Topic</th><th>Title snippet</th></tr></thead><tbody>\n'
        + "\n".join(rows)
        + "\n</tbody></table>\n"
        + '<script type="application/json" id="gap-index-json">'
        + json.dumps(data, ensure_ascii=False, sort_keys=True)
        + "</script>\n</div>"
    )
    return table


# --- commands ----------------------------------------------------------------

def cmd_extract(args) -> int:
    doc = open(args.doc, encoding="utf-8").read()
    data = load_canonical_json(doc)
    out = args.out
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"extracted {len(data)} gaps -> {out}")
    return 0


def _block_is_fresh(doc_text: str, data: dict) -> bool:
    """Semantic freshness: embedded JSON equals canonical AND every canonical
    gap has a rendered <tr id=gap-N> row. Ignores cosmetic formatting drift
    (emoji headers, provenance strings) so a healthy doc is never rewritten."""
    try:
        embedded = load_canonical_json(doc_text)
    except SystemExit:
        return False
    if embedded != data:
        return False
    row_ids = {int(m.group(1)) for m in ROW_RE.finditer(doc_text)}
    return row_ids == {int(k) for k in data}


def cmd_write(args) -> int:
    doc = open(args.doc, encoding="utf-8").read()
    data = load_canonical_json(doc)

    if _block_is_fresh(doc, data):
        print(f"OK: gap-index block in {args.doc} is up to date "
              f"({len(data)} gaps, no rewrite needed)")
        return 0
    if args.check:
        print(f"STALE: gap-index block in {args.doc} needs rewrite "
              f"({len(data)} canonical gaps)")
        return 1

    block = render_block(data)
    old = TABLE_RE.search(doc)
    if not old:
        raise SystemExit("ERROR: no <div id=gap-index> block found in doc")
    new_doc = doc[: old.start()] + block + doc[old.end():]
    with open(args.doc, "w", encoding="utf-8") as f:
        f.write(new_doc)
    print(f"rewrote gap-index block in {args.doc} ({len(data)} gaps)")
    return 0


def cmd_anchors(args) -> int:
    doc_text = open(args.doc, encoding="utf-8").read()
    data = load_canonical_json(doc_text)
    indexed = {int(k) for k in data}

    corpus_dir = args.corpus or os.path.dirname(os.path.abspath(args.doc))
    docs = sorted(
        f for f in os.listdir(corpus_dir)
        if f.endswith(".html") and f != os.path.basename(args.doc)
    )

    cited: dict = {}
    for f in docs:
        try:
            text = open(os.path.join(corpus_dir, f), encoding="utf-8",
                        errors="replace").read()
        except OSError:
            continue
        for m in ANCHOR_RE.finditer(text):
            nums = [int(x) for x in re.findall(r"#(\d+)", m.group(1))]
            cited.setdefault(f, []).extend(nums)

    all_cited = sorted({n for v in cited.values() for n in v})
    dangling = [n for n in all_cited if n not in indexed]
    uncited = sorted(n for n in indexed if n not in set(all_cited))

    print(f"hub docs with anchor lines: {len(cited)}")
    print(f"indexed gaps:               {len(indexed)}")
    print(f"distinct cited ids:         {len(all_cited)} -> {all_cited}")
    if dangling:
        print(f"DANGLING cites (not indexed): {dangling}")
    if args.strict and uncited:
        print(f"uncited indexed gaps: {uncited}")
    for f, v in sorted(cited.items()):
        print(f"  {f:<40} {v}")

    bad = bool(dangling) or (args.strict and bool(uncited))
    if bad:
        print("ANCHOR CHECK FAILED")
        return 1
    print("ANCHOR CHECK PASSED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract", help="pull canonical gap JSON out of the doc")
    p.add_argument("doc", nargs="?", default=DOC_PATH)
    p.add_argument("--out", default="gap_index.json")
    p.set_defaults(fn=cmd_extract)

    p = sub.add_parser("write", help="re-render the gap-index block in the doc")
    p.add_argument("doc", nargs="?", default=DOC_PATH)
    p.add_argument("--check", action="store_true",
                   help="verify freshness without writing (exit 1 if stale)")
    p.set_defaults(fn=cmd_write)

    p = sub.add_parser("anchors", help="verify hub-doc anchor cites resolve")
    p.add_argument("doc", nargs="?", default=DOC_PATH)
    p.add_argument("--corpus", default=".",
                   help="dir containing the *.html corpus (default: doc's dir)")
    p.add_argument("--strict", action="store_true",
                   help="also fail on indexed-but-uncited gaps")
    p.set_defaults(fn=cmd_anchors)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
