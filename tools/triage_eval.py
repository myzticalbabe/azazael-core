#!/usr/bin/env python3
# coding: utf-8
"""Evaluate the PSYCHE crisis router against a labelled corpus.

WHAT THIS MEASURES, AND WHAT IT DOES NOT
----------------------------------------
It measures whether `rt_agent.triage` flags the utterances a labelled corpus says
should be flagged. It says NOTHING about clinical validity, and the corpus is
authored by the same agent that wrote the router, so a good score means "the
router agrees with the expectations of the person who wrote both", which is the
weakest form of agreement available.

That limitation is not a caveat in a docstring -- it is PRINTED with every
result, because an instrument that flatters itself in its own output is the
failure this project keeps finding. Two numbers are therefore always shown
together: the aggregate, and the per-category breakdown. An aggregate can hide a
whole missing category (a router that fires only on the two phrasings it was
written around still scores well on a corpus that repeats those phrasings), and
a category breakdown cannot.

The output names every miss. False NEGATIVES are listed first: for a safety
router, failing to flag a real statement of intent is the expensive error, and
reporting the cheap and expensive errors in one undifferentiated list is how the
expensive one gets lost.

Usage:
  python tools/triage_eval.py                 # full report
  python tools/triage_eval.py --json          # machine-readable
  python tools/triage_eval.py --min-recall 0.9   # exit 1 below that recall
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE.rt_agent import triage  # noqa: E402  (path set above, deliberately)
import PSYCHE as _psyche_pkg  # noqa: E402

PSYCHE_DATA = Path(_psyche_pkg.__file__).resolve().with_name("data")

#: Resolved from the INSTALLED package, not from the source layout. The first
#: version of this line was `ROOT / "src" / "PSYCHE" / "data" / ...`, which is
#: correct in the monorepo and nonexistent in the public tree -- so the instrument
#: shipped broken while the package imported fine. A path that only exists on the
#: author's machine is the same class of defect as a test that only passes there.
CORPUS = Path(PSYCHE_DATA) / "triage_corpus.jsonl"

PROVENANCE = ("corpus and router were written by the same author, so this "
              "measures agreement with those expectations, not clinical validity")


def load_corpus(path: Path) -> Dict:
    """Read the JSONL corpus. The `_meta` row is required, not optional.

    A corpus with no provenance cannot be interpreted later -- the reader cannot
    tell invented utterances from transcripts -- so a missing meta row is a hard
    error rather than a default.
    """
    rows: List[dict] = []
    meta: Optional[dict] = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        record = json.loads(stripped)
        if "_meta" in record:
            meta = record
            continue
        if "text" not in record or "expect_crisis" not in record:
            raise ValueError("corpus line %d lacks text/expect_crisis" % number)
        record.setdefault("category", "unlabelled")
        # "dev" rows are the ones the router was inspected against, so no score
        # on them is evidence about unseen phrasing. "holdout" rows were written
        # after the fix and are the only quasi-out-of-sample number here.
        record.setdefault("split", "dev")
        rows.append(record)
    if meta is None:
        raise ValueError("corpus has no _meta row: provenance is required")
    return {"meta": meta, "rows": rows}


def _score(rows: List[dict], activation: Optional[Dict[str, float]]) -> Dict:
    """Confusion, rates and per-category detail over one set of rows."""
    tp = fp = tn = fn = 0
    misses: List[dict] = []
    per_category: Dict[str, dict] = {}

    for row in rows:
        verdict = triage(row["text"], activation)
        fired = verdict.risk == "crisis"
        expected = bool(row["expect_crisis"])
        category = row["category"]

        bucket = per_category.setdefault(
            category, {"rows": 0, "expected_crisis": 0, "fired": 0, "missed": 0})
        bucket["rows"] += 1
        bucket["expected_crisis"] += 1 if expected else 0
        bucket["fired"] += 1 if fired else 0

        if fired and expected:
            tp += 1
        elif fired and not expected:
            fp += 1
            misses.append({"kind": "false_positive", "category": category,
                           "text": row["text"], "risk": verdict.risk,
                           "reason": verdict.reasons[0] if verdict.reasons else ""})
        elif not fired and expected:
            fn += 1
            bucket["missed"] += 1
            misses.append({"kind": "false_negative", "category": category,
                           "text": row["text"], "risk": verdict.risk,
                           "reason": verdict.reasons[0] if verdict.reasons else ""})
        else:
            tn += 1

    positives = tp + fn
    negatives = tn + fp
    return {
        "rows": len(rows),
        "confusion": {"true_positive": tp, "false_positive": fp,
                      "true_negative": tn, "false_negative": fn},
        "recall": round(tp / positives, 4) if positives else None,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "specificity": round(tn / negatives, 4) if negatives else None,
        "accuracy": round((tp + tn) / len(rows), 4) if rows else None,
        "per_category": dict(sorted(per_category.items())),
        "misses": misses,
    }


def evaluate(corpus: Dict, activation: Optional[Dict[str, float]] = None,
             split: Optional[str] = None) -> Dict:
    """Score every row, and every split separately.

    The split breakdown is not decoration: the dev rows are the ones the router
    was changed after looking at, so their score is fitted, and the holdout rows
    are the only number that is not. Reporting one aggregate invites quoting the
    fitted number as if it were a generalisation.
    """
    rows = [r for r in corpus["rows"] if split is None or r.get("split") == split]
    overall = _score(rows, activation)
    by_split = {}
    for name in sorted({r.get("split", "dev") for r in corpus["rows"]}):
        by_split[name] = _score([r for r in rows if r.get("split") == name], activation)
    stance_flags = []
    for row in rows:
        verdict = triage(row["text"], activation)
        if verdict.risk != "low":
            stance_flags.append({"risk": verdict.risk, "category": row["category"],
                                 "split": row.get("split", "dev"),
                                 "text": row["text"]})
    overall.update({"meta": corpus["meta"], "by_split": by_split,
                    "non_low_risk_rows": stance_flags, "provenance": PROVENANCE})
    return overall


def render(report: Dict) -> str:
    """Human-readable form. False negatives are printed before false positives."""
    lines: List[str] = []
    lines.append("crisis router evaluation -- %d rows" % report["rows"])
    lines.append("  corpus: %s" % report["meta"].get("authored", "undated"))
    conf = report["confusion"]
    lines.append("  confusion  TP %-3d FP %-3d TN %-3d FN %-3d"
                 % (conf["true_positive"], conf["false_positive"],
                    conf["true_negative"], conf["false_negative"]))
    lines.append("  recall %.4s  precision %.4s  specificity %.4s  accuracy %.4s"
                 % (report["recall"], report["precision"],
                    report["specificity"], report["accuracy"]))
    for name, block in report["by_split"].items():
        conf = block["confusion"]
        kind = ("FITTED -- the router was changed after seeing these failures"
                if name == "dev" else "written after the fix, still same author")
        lines.append("    %-8s n=%-3d recall %-6s precision %-6s TP %-2d FP %-2d FN %-2d   [%s]"
                     % (name, block["rows"], block["recall"], block["precision"],
                        conf["true_positive"], conf["false_positive"],
                        conf["false_negative"], kind))
    lines.append("")
    lines.append("  per category (a strong aggregate can hide a missing category):")
    for category, row in report["per_category"].items():
        lines.append("    %-22s rows %-3d expected_crisis %-3d fired %-3d missed %-3d"
                     % (category, row["rows"], row["expected_crisis"],
                        row["fired"], row["missed"]))
    negatives = [m for m in report["misses"] if m["kind"] == "false_negative"]
    positives = [m for m in report["misses"] if m["kind"] == "false_positive"]
    lines.append("")
    lines.append("  MISSES -- the expensive error first:")
    if not negatives:
        lines.append("    false negatives: none (the router fired on every labelled "
                     "intent row in THIS corpus, which is a statement about the corpus)")
    for miss in negatives:
        lines.append("    FN [%s] %r" % (miss["category"], miss["text"]))
    if not positives:
        lines.append("    false positives: none")
    for miss in positives:
        lines.append("    FP [%s] %r" % (miss["category"], miss["text"]))
    lines.append("")
    lines.append("  LIMITATION: %s" % report["provenance"])
    lines.append("  Nothing here is a clinical validation. Do not cite this as one.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", default=str(CORPUS))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--split", default=None,
                        help="score only one split, e.g. dev, holdout, holdout2 "
                             "(default: all, broken out separately)")
    parser.add_argument("--min-recall", type=float, default=None,
                        help="exit 1 when crisis recall falls below this")
    args = parser.parse_args(argv)

    corpus = load_corpus(Path(args.corpus))
    report = evaluate(corpus, split=args.split)
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(render(report))
    if args.min_recall is not None and (report["recall"] or 0.0) < args.min_recall:
        print("\nFAIL: recall %s below --min-recall %s"
              % (report["recall"], args.min_recall), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
