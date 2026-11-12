#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build an ARC/cain evidence packet from FETCH_LOG rows and the saved bytes.

The packet is what a judge is allowed to see: a claim, and for each claim the
sources retrieved for it with their status, tier, and a **verbatim quote**.

Two rules make the quote trustworthy, and both are enforced here rather than
promised:

1. **The quote is sliced, never typed.**  The anchor string must be present in
   the saved bytes; if it is not, the build aborts.  A quote cannot be recalled
   from memory into evidence.
2. **Entities are decoded before slicing.**  Round 1 shipped quotes containing
   ``&mdash;`` and ``&rsquo;``; a judge copying such a quote produced text that
   failed the grounding check in ``arena_llm_judge.py`` for 6 of 6 C6 rows, so
   eight apparently-ungrounded quotes were an extraction defect, not six
   hallucinating judges.  ``html.unescape`` runs on the extracted text.

The spec is data (``--spec``) so a re-exploration round adds sources without
editing this tool::

    {"pins": [{"pin": "C6", "vendor": "John Deere", "claim": "...",
               "evidence": [{"sha16": "c8a59a4566c4afd7",
                             "anchor": "only fully functional software repair tool"}]}]}

Usage::

    python tools/cain_evidence_packet.py --spec ARC/cain/evidence_spec_r2.json \
        --out ARC/cain/evidence_packet_r2.json
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RAW = Path("ARC/cain/raw")
LOG_NAME = "FETCH_LOG.jsonl"


def text_of(path: Path) -> str:
    """Readable text from a saved HTML page, with entities decoded."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = body.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", body)


def load_log(raw_dir: Path) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    log_path = raw_dir / LOG_NAME
    if not log_path.exists():
        raise SystemExit("no FETCH_LOG at %s" % log_path)
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        sha = row.get("sha256") or ""
        if sha and row.get("raw_path"):
            index.setdefault(sha[:16], row)
    return index


def cite(index: Dict[str, Dict[str, Any]], sha16: str, anchor: str,
         before: int, after: int, gloss: Optional[str] = None) -> Dict[str, Any]:
    row = index.get(sha16)
    if row is None:
        raise SystemExit("no FETCH_LOG row with a saved body for sha16=%s" % sha16)
    path = Path(row["raw_path"])
    if not path.exists():
        raise SystemExit("raw body missing on disk: %s" % path)
    body = text_of(path)
    pos = body.lower().find(anchor.lower())
    if pos < 0:
        raise SystemExit("anchor %r ABSENT from %s: refusing to cite it" % (anchor, path.name))
    saved = path.read_bytes()
    ev: Dict[str, Any] = {
        "url": row["url"],
        "final_url": row.get("final_url", ""),
        "status": row["status"],
        "bytes": row["bytes"],
        "sha256": row["sha256"],
        "tier": row["tier"],
        "fetched_at": row["fetched_at"],
        "retrieved_by": row["method"],
        "raw": path.name,
        "actor": "publisher" if row["tier"] in ("A", "B") else "secondary",
        "anchor": anchor,
        "window": [before, after],
        "quote": body[max(0, pos - before):pos + after].strip(),
    }
    # A translation is an aid, not evidence: the quoted original stays the
    # thing graded, and the gloss is labelled so no judge can mistake the
    # analyst's rendering for the publisher's words.
    if gloss:
        ev["gloss"] = gloss
        ev["gloss_label"] = ("analyst translation of the quoted passage; the quoted "
                             "original in its own language is the evidence")
    return ev


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build a CAIN evidence packet from saved bytes")
    ap.add_argument("--spec", required=True, help="JSON spec: pins with claim + anchored evidence")
    ap.add_argument("--out", required=True, help="packet path to write")
    ap.add_argument("--raw", default=str(RAW))
    ap.add_argument("--before", type=int, default=180)
    ap.add_argument("--after", type=int, default=420)
    args = ap.parse_args(argv)

    raw_dir = Path(args.raw)
    index = load_log(raw_dir)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))

    pins: List[Dict[str, Any]] = []
    for entry in spec["pins"]:
        evidence = [cite(index, ev["sha16"], ev["anchor"],
                         ev.get("before", args.before), ev.get("after", args.after),
                         ev.get("gloss"))
                    for ev in entry["evidence"]]
        pin: Dict[str, Any] = {
            "pin": entry["pin"],
            "vendor": entry["vendor"],
            "claim": entry["claim"],
            "elements": entry.get("elements", []),
            "evidence": evidence,
        }
        # A countable fact carries its method, so a judge grades a measurement
        # rather than an assurance that one was made.
        if entry.get("proof"):
            pin["proof"] = entry["proof"]
        pins.append(pin)

    packet = {
        "generated_at": spec.get("generated_at", ""),
        "round": spec.get("round", ""),
        "note": ("Every quote is sliced from bytes saved under ARC/cain/raw/ after its anchor "
                 "was found in the file; FETCH_LOG.jsonl carries the retrieval row and sha256. "
                 "Tier A = primary document, B = official page (vendor/agency), C = credible "
                 "secondary. 'actor' says whether the publisher is the party the claim is about."),
        "pins": pins,
    }
    blob = json.dumps(packet, indent=1, ensure_ascii=False)
    out = Path(args.out)
    out.write_text(blob + "\n", encoding="utf-8")
    print("wrote %s  %d bytes  sha256:%s" % (out, len(blob), hashlib.sha256(blob.encode("utf-8")).hexdigest()))
    for pin in pins:
        print("  %-3s ev=%d  %s" % (pin["pin"], len(pin["evidence"]),
                                    " | ".join("%s/%s %dB" % (e["tier"], e["status"], e["bytes"])
                                               for e in pin["evidence"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
