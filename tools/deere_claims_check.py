"""Quote and digest checker for the Deere accountability ledger.

ARC's entry rule (``ARC/README.md``) asks three things of an inhabitant: a mark,
an evidence state, and a checker.  The evidence state is
``ARC/deere/claims.json``; this file is the third.

What it checks, and why in this order
-------------------------------------

A repair-restriction claim is worth exactly its quote.  So the ledger is not
scored -- it is *re-read*: every SUPPORTED claim names a capture and carries a
verbatim substring of that capture's text, and this checker goes back to the
bytes on disk and looks for the substring again.  Nothing is taken from the
ledger's own word.

Three questions, in increasing order of how badly a wrong answer hurts:

1. **Digests.** Does the file on disk still hash to the digest the ledger
   recorded?  A capture whose digest moved is not stale evidence, it is *other
   bytes*: the quote may still be found while the document has changed around it.
   That is BLIND, not a pass, and never repaired by updating the digest here --
   a checker that rewrites its own subject is measuring itself.
2. **Quotes.** Is the claim's quote actually in the text it names?  A quote that
   cannot be found in place is INCOMPLETE: the claim's support was carried, not
   read.
3. **Gaps.** A claim graded UNSUPPORTED or BLOCKED must say *why* in ``reason``.
   A named gap is a declared gap and is fine -- this corpus is allowed to have
   them, and having them visible is the point.  An unnamed gap is a defect and
   forces GAPS, because a reader cannot tell it from an oversight.

A fourth, cheap check keeps the ledger from drifting away from the document a
human actually reads: every claim id must appear in ``WHITEPAPER.md``.  A claim
the narrative never mentions is a claim nobody will contest.

Normalisation is declared, not hidden
-------------------------------------

PDF text extraction mangles typography, not words: ligatures, curly quotes,
en/em dashes, and runs of whitespace.  ``normalize`` folds exactly those (NFKC,
quote and dash folding, whitespace collapse, case folding) and nothing else.  It
does not de-hyphenate, stem, or fuzzy-match: a quote either survives the fold and
appears, or it fails.

Verdicts and exit codes follow the ARC instrument contract:

    0  READY       every digest matches, every SUPPORTED quote is found in place,
                   every gap is named, every claim id appears in the narrative
    1  GAPS        a gap is unnamed (or the ledger is structurally malformed)
    1  INCOMPLETE  no defect in arithmetic, but a claim's support is not in place
                   (quote not found, source missing, claim orphaned from the paper)
    2  BLIND       the ledger or a capture cannot be read, or a digest does not
                   match the bytes: the bytes are not the bytes that were read

Usage:
    python tools/deere_claims_check.py             # full check
    python tools/deere_claims_check.py --digests   # provenance table only
    python tools/deere_claims_check.py --json logs/deere_claims.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

VERDICT_READY = "READY"
VERDICT_GAPS = "GAPS"
VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_BLIND = "BLIND"

EXIT_CODES = {
    VERDICT_READY: 0,
    VERDICT_GAPS: 1,
    VERDICT_INCOMPLETE: 1,
    VERDICT_BLIND: 2,
}

# Worst verdict wins when several defects are present at once.
_RANK = {
    VERDICT_READY: 0,
    VERDICT_INCOMPLETE: 1,
    VERDICT_GAPS: 2,
    VERDICT_BLIND: 3,
}

REQUIRED_CLAIM_KEYS = ("id", "tier", "status", "locator", "claim", "quote")
GRADES = ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "BLOCKED")
OPEN_GRADES = ("UNSUPPORTED", "BLOCKED")

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = DEFAULT_ROOT / "ARC" / "deere" / "claims.json"
DEFAULT_WHITEPAPER = DEFAULT_ROOT / "ARC" / "deere" / "WHITEPAPER.md"

_QUOTE_FOLD = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
    "\u00a0": " ",
    "\u202f": " ",
    "\u2009": " ",
}


class Blinded(Exception):
    """The ledger or a capture cannot be read. Never a pass."""


@dataclass
class Report:
    verdict: str = VERDICT_READY
    issues: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    narrative_missing: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.verdict]

    def fail(self, verdict: str, message: str) -> None:
        self.issues.append("%s: %s" % (verdict, message))
        if _RANK[verdict] > _RANK[self.verdict]:
            self.verdict = verdict


def normalize(text: str) -> str:
    """Fold typography that PDF extraction mangles, and nothing else."""
    text = unicodedata.normalize("NFKC", text)
    for src, dst in _QUOTE_FOLD.items():
        text = text.replace(src, dst)
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_ledger(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Blinded("ledger unreadable: %s (%s)" % (path, exc)) from exc
    try:
        ledger = json.loads(raw)
    except ValueError as exc:
        raise Blinded("ledger is not JSON: %s (%s)" % (path, exc)) from exc
    if not isinstance(ledger, dict):
        raise Blinded("ledger is not an object: %s" % (path,))
    for key in ("sources", "claims"):
        if not isinstance(ledger.get(key), (dict, list)) or not ledger.get(key):
            raise Blinded("ledger has no usable '%s'" % key)
    return ledger


def check_sources(root: Path, ledger: dict, report: Report) -> dict:
    """Recompute every digest. Returns {source_id: normalized_text} for found files."""
    texts: dict[str, str] = {}
    for name, src in ledger["sources"].items():
        row = {"id": name, "tier": src.get("tier"), "state": "ok", "detail": ""}
        report.sources.append(row)
        for key, count in (("path", None), ("sha256", None), ("text", None)):
            if not src.get(key):
                report.fail(VERDICT_BLIND, "source %s has no '%s'" % (name, key))
                row["state"] = "unreadable"
                return texts
        capture = root / src["path"]
        if not capture.is_file():
            report.fail(VERDICT_BLIND, "source %s capture missing: %s" % (name, src["path"]))
            row["state"] = "missing"
            continue
        try:
            actual = digest_file(capture)
        except OSError as exc:
            report.fail(VERDICT_BLIND, "source %s unreadable: %s" % (name, exc))
            row["state"] = "unreadable"
            continue
        if actual != str(src["sha256"]).lower():
            report.fail(
                VERDICT_BLIND,
                "source %s digest mismatch: recorded %s, on disk %s"
                % (name, str(src["sha256"])[:16], actual[:16]),
            )
            row["state"] = "digest-mismatch"
            row["detail"] = actual
            continue
        text_path = root / src["text"]
        if not text_path.is_file():
            report.fail(VERDICT_BLIND, "source %s text missing: %s" % (name, src["text"]))
            row["state"] = "text-missing"
            continue
        try:
            texts[name] = normalize(text_path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            report.fail(VERDICT_BLIND, "source %s text unreadable: %s" % (name, exc))
            row["state"] = "text-unreadable"
            continue
        row["detail"] = actual
    return texts


def check_claims(ledger: dict, texts: dict, report: Report) -> None:
    seen: set[str] = set()
    for claim in ledger["claims"]:
        if not isinstance(claim, dict):
            report.fail(VERDICT_GAPS, "a claim is not an object")
            continue
        cid = str(claim.get("id") or "?")
        row = {
            "id": cid,
            "tier": claim.get("tier"),
            "status": claim.get("status"),
            "source": claim.get("source"),
            "state": "ok",
        }
        report.claims.append(row)
        missing = [k for k in REQUIRED_CLAIM_KEYS if k not in claim]
        if missing:
            report.fail(VERDICT_GAPS, "claim %s is missing %s" % (cid, ", ".join(missing)))
            row["state"] = "malformed"
            continue
        if cid in seen:
            report.fail(VERDICT_GAPS, "claim id %s is duplicated" % cid)
        seen.add(cid)
        if claim["status"] not in GRADES:
            report.fail(VERDICT_GAPS, "claim %s has grade %r" % (cid, claim["status"]))
            row["state"] = "bad-grade"
            continue
        if claim["status"] in OPEN_GRADES:
            if not str(claim.get("reason") or "").strip():
                report.fail(
                    VERDICT_GAPS,
                    "claim %s is %s but names no reason" % (cid, claim["status"]),
                )
                row["state"] = "unnamed-gap"
            else:
                row["state"] = claim["status"].lower()
            continue
        if claim["status"] != "SUPPORTED":
            row["state"] = claim["status"].lower()
            if not str(claim.get("reason") or "").strip():
                report.fail(VERDICT_GAPS, "claim %s is %s without a reason" % (cid, claim["status"]))
                row["state"] = "unnamed-gap"
            continue
        source = claim.get("source")
        if not source or source not in texts:
            report.fail(
                VERDICT_INCOMPLETE,
                "claim %s cites source %r, which has no readable text" % (cid, source),
            )
            row["state"] = "no-source-text"
            continue
        quote = normalize(str(claim["quote"]))
        if len(quote) < 20:
            report.fail(VERDICT_INCOMPLETE, "claim %s quote is too short to be a quote" % cid)
            row["state"] = "short-quote"
            continue
        if quote not in texts[source]:
            report.fail(
                VERDICT_INCOMPLETE,
                "claim %s quote not found in %s: %r" % (cid, source, str(claim["quote"])[:70]),
            )
            row["state"] = "quote-not-found"


def check_narrative(ledger: dict, paper: Path, report: Report) -> None:
    if not paper.is_file():
        report.fail(VERDICT_INCOMPLETE, "narrative missing: %s" % paper)
        return
    try:
        body = paper.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.fail(VERDICT_INCOMPLETE, "narrative unreadable: %s (%s)" % (paper, exc))
        return
    for claim in ledger["claims"]:
        cid = str(claim.get("id") or "")
        if cid and not re.search(r"\b%s\b" % re.escape(cid), body):
            report.narrative_missing.append(cid)
    if report.narrative_missing:
        report.fail(
            VERDICT_INCOMPLETE,
            "claim(s) absent from the narrative: %s" % ", ".join(report.narrative_missing),
        )


def check(
    ledger_path: Path = DEFAULT_LEDGER,
    paper_path: Path | None = None,
    root: Path | None = None,
) -> Report:
    """Read the ledger, re-read every capture, and grade the result."""
    report = Report()
    if paper_path is None:
        paper_path = DEFAULT_WHITEPAPER
    # Capture paths in the ledger are relative to the ledger's own directory
    # (``ARC/deere/``), so the ledger can be moved or copied with its captures
    # and still resolve.  Resolving against the repo root instead silently
    # reports every source "missing" -- a BLIND verdict for a healthy corpus.
    if root is None:
        root = ledger_path.resolve().parent
    try:
        ledger = load_ledger(ledger_path)
    except Blinded as exc:
        report.fail(VERDICT_BLIND, str(exc))
        return report
    texts = check_sources(root, ledger, report)
    check_claims(ledger, texts, report)
    check_narrative(ledger, paper_path, report)

    supported = sum(1 for c in ledger["claims"] if c.get("status") == "SUPPORTED")
    report.counts = {
        "claims": len(ledger["claims"]),
        "sources": len(ledger["sources"]),
        "supported": supported,
        "found": sum(1 for row in report.claims if row["state"] == "ok"),
        "digests_ok": sum(1 for row in report.sources if row["state"] == "ok"),
        "open_claims": sum(1 for c in ledger["claims"] if c.get("status") in OPEN_GRADES),
        "narrative_cited": len(ledger["claims"]) - len(report.narrative_missing),
    }
    return report


def digest_table(report: Report) -> None:
    print("")
    print("%-12s %-5s %-16s %s" % ("source", "tier", "state", "sha256"))
    for row in report.sources:
        print(
            "%-12s %-5s %-16s %s"
            % (row["id"], row.get("tier") or "-", row["state"], (row.get("detail") or "")[:64])
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--whitepaper", type=Path, default=None)
    parser.add_argument("--digests", action="store_true", help="provenance table only")
    parser.add_argument("--json", type=Path, default=None, help="write the report")
    args = parser.parse_args(argv)

    report = check(
        ledger_path=args.ledger,
        paper_path=args.whitepaper,
    )

    counts = report.counts
    print("deere claims: %s claims, %s sources" % (counts.get("claims", 0), counts.get("sources", 0)))
    if counts:
        print(
            "digests:      %d/%d match"
            % (counts.get("digests_ok", 0), counts.get("sources", 0))
        )
        print(
            "quotes:       %d/%d SUPPORTED claims found in place"
            % (counts.get("found", 0), counts.get("supported", 0))
        )
        print("gaps:         %d declared (named reason required)" % counts.get("open_claims", 0))
        print(
            "narrative:    %d/%d claim ids cited in WHITEPAPER.md"
            % (counts.get("narrative_cited", 0), counts.get("claims", 0))
        )
    for issue in report.issues:
        print("  [issue] %s" % issue)
    if args.digests:
        digest_table(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "verdict": report.verdict,
                    "counts": report.counts,
                    "issues": report.issues,
                    "sources": report.sources,
                    "claims": report.claims,
                    "narrative_missing": report.narrative_missing,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print("wrote %s" % args.json)
    print("verdict: %s" % report.verdict)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
