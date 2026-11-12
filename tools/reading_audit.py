#!/usr/bin/env python3
"""reading_audit.py -- HED-9: a reading carries its denominator.

A verdict is only a reading if it says how much was read. `0 findings` over an
unstated corpus and `0 findings` over 3 000 files are different facts, and this
tree has already paid for confusing them: the AST audit reported 2 817 where the
parse had covered 2 824 (HED-3), and a web provider that answered with a
challenge page reported `ok, 0 results` (HED-4). Both were passes whose
denominator was hidden or absent.

So this audits the artifacts rather than the code. Every JSON whose top-level
verdict is pass-ish must also carry a positive count -- files, rows, targets,
legs, tests -- somewhere in the record or its `summary`/`totals`/`counts`
neighbour, or the pass cannot be told apart from a run that read nothing.

Three findings are distinguished, because they need different repairs:

  denominator_free   a pass verdict with no count anywhere in the record
  collapsed          a pass verdict while every count present is zero or empty
  leg_without_proof  an `ok` leg with no `seconds`, no `detail`, or a state
                     outside the declared five (HED-11's contract)

The blindness index is the fraction of pass verdicts carrying no usable
denominator. It is printed with its own denominator on purpose: a number that
hides its count is the exact defect this tool exists to find.

Exit: 0 always (advisory -- HED-2's debt model: a gate red on day one is a gate
switched off), 1 only under --strict, 2 when the audit could not be performed.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _setting(key: str, fallback):
    """`config/defaults.json` through the shared reader, with a constant fallback."""
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import defaults_profile            # noqa: PLC0415
        return defaults_profile.read_default(key, fallback)
    except Exception:
        return fallback


PASSISH = {"pass", "passed", "ok", "clean", "success", "green", "healthy"}
VERDICT_KEYS = ("verdict", "result", "status")
DENOM_KEYS = (
    "files", "files_scanned", "files_total", "py_files", "py_parsed", "modules",
    "rows", "records", "entries", "items", "total", "count", "counts",
    "targets", "legs", "checked", "scanned", "tests", "collected", "findings_total",
    "checks_passed",
)
SCOPES = ("summary", "totals", "counts", "stats")
LEG_STATES = {"ok", "empty", "skipped", "error", "timeout"}


def _positive(value) -> bool:
    """True when a value can serve as a denominator (a number greater than 0)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, (str, list, tuple, dict)):
        return len(value) > 0
    return False


def _denominators(record: dict) -> list:
    """Every (key, value) in the record or a nested count scope that can count."""
    hits = []
    scopes = [record] + [record.get(s) for s in SCOPES]
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        for key in DENOM_KEYS:
            if key in scope:
                hits.append((key, scope[key]))
    return hits


def _pass_verdicts(record: dict) -> list:
    found = []
    for key in VERDICT_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip().lower() in PASSISH:
            found.append((key, value.strip()))
    return found


def audit_verdicts(record: dict, where: str, findings: list) -> int:
    """Check one record's pass verdicts for a usable denominator.

    Returns the number of pass verdicts examined, so the caller can compute the
    blindness index over verdicts rather than over files -- one file can carry
    several verdicts and one verdict is the unit a reader trusts.
    """
    verdicts = _pass_verdicts(record)
    if not verdicts:
        return 0
    denoms = _denominators(record)
    key, value = verdicts[0]
    if not denoms:
        findings.append({
            "class": "denominator_free", "where": where,
            "detail": "%s %r with no count anywhere in the record"
                      % (key, value),
        })
    elif not any(_positive(v) for _k, v in denoms):
        findings.append({
            "class": "collapsed", "where": where,
            "detail": "%s %r while every count is zero/empty (%s)"
                      % (key, value,
                         ", ".join("%s=%r" % (k, v) for k, v in denoms)),
        })
    return len(verdicts)


def audit_legs(record: dict, where: str, findings: list) -> None:
    """Check a leg ledger against HED-11's five states, without judging the run."""
    legs = record.get("legs")
    if not isinstance(legs, list):
        return
    for index, leg in enumerate(legs):
        if not isinstance(leg, dict):
            findings.append({
                "class": "leg_without_proof", "where": where,
                "detail": "legs[%d] is %s, not a record" % (index, type(leg).__name__),
            })
            continue
        name = str(leg.get("leg") or "legs[%d]" % index)
        state = str(leg.get("status", "ok")).strip().lower()
        if state not in LEG_STATES:
            findings.append({
                "class": "leg_unknown_state", "where": where,
                "detail": "%s reported state %r outside %s"
                          % (name, state, sorted(LEG_STATES)),
            })
        seconds = leg.get("seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            findings.append({
                "class": "leg_without_proof", "where": where,
                "detail": "%s has no numeric `seconds` -- a leg that did not time "
                          "itself cannot be said to have run" % name,
            })
        if state == "ok" and not str(leg.get("detail") or "").strip():
            findings.append({
                "class": "leg_without_proof", "where": where,
                "detail": "%s reported ok with an empty detail" % name,
            })


def audit_file(path: Path, findings: list) -> dict:
    """Audit one JSON artifact. A record that will not parse is its own finding."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        record = json.loads(raw)
    except (OSError, ValueError) as exc:
        # A report that will not parse is a fact about the report, and swallowing
        # it would remove it from the count this tool reports.
        findings.append({"class": "unparseable", "where": str(path),
                         "detail": "%s: %s" % (type(exc).__name__, str(exc))})
        return {"file": str(path), "verdicts": 0, "pass": False}
    if not isinstance(record, dict):
        return {"file": str(path), "verdicts": 0, "pass": True}
    before = len(findings)
    verdicts = audit_verdicts(record, str(path), findings)
    audit_legs(record, str(path), findings)
    # A per-file flag as well as the per-finding one: coverage is a property of
    # the artifact set ("did these readings say how much they read"), while a
    # finding is a property of one record. The first cannot be derived from the
    # second without counting the artifacts nobody flagged.
    denominated = any(_positive(value) for _key, value in _denominators(record))
    return {"file": str(path), "verdicts": verdicts,
            "pass": len(findings) == before, "denominated": denominated}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--glob", action="append", default=[],
                    help="glob(s) of JSON to audit; default logs/*.json")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when any finding is open")
    ap.add_argument("--json", default="", help="also write the findings here")
    ap.add_argument("--max-list", type=int, default=0,
                    help="cap the printed findings; 0 (default) prints every one. "
                         "A cap hides rows from the operator, which is the reading "
                         "defect this tool exists to find.")
    args = ap.parse_args(argv)

    patterns = args.glob or [str(ROOT / "logs" / "*.json")]
    files = sorted({p for pattern in patterns for p in glob.glob(pattern)})
    if not files:
        print("reading_audit: no artifact matched %s -- nothing audited, which is "
              "not a pass." % ", ".join(patterns))
        return 2

    findings, audited, verdicts_total = [], [], 0
    for name in files:
        row = audit_file(Path(name), findings)
        audited.append(row)
        verdicts_total += row["verdicts"]

    by_class = {}
    for row in findings:
        by_class[row["class"]] = by_class.get(row["class"], 0) + 1
    blind = sum(1 for row in findings
                if row["class"] in ("denominator_free", "collapsed"))
    index = (blind / verdicts_total) if verdicts_total else 0.0
    covered = sum(1 for row in audited if row.get("denominated"))
    coverage = (covered / len(audited)) if audited else 0.0
    min_coverage = float(_setting("reading_audit.min_coverage", 0.0))

    print("reading_audit: %d file(s), %d pass verdict(s), %d finding(s) [%s]"
          % (len(audited), verdicts_total, len(findings),
             ", ".join("%s=%d" % kv for kv in sorted(by_class.items())) or "none"))
    print("blindness index: %d/%d pass verdict(s) cannot be told apart from a run "
          "that read nothing (%.0f%%)" % (blind, verdicts_total, index * 100))
    print("coverage: %d/%d artifact(s) carry a usable denominator (%.0f%%; floor %.0f%%)"
          % (covered, len(audited), coverage * 100, min_coverage * 100))
    shown = findings if args.max_list <= 0 else findings[:args.max_list]
    for row in shown:
        print("  - %-19s %s: %s"
              % (row["class"], Path(row["where"]).name, row["detail"]))
    if len(shown) < len(findings):
        print("  ... %d more (all counted above)" % (len(findings) - len(shown)))

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "generated": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                     __import__("time").gmtime()),
            "globs": patterns, "files": len(audited),
            "pass_verdicts": verdicts_total, "findings": findings,
            "by_class": by_class, "blindness_index": round(index, 4),
            "coverage": round(coverage, 4), "min_coverage": min_coverage,
        }, indent=2) + "\n", encoding="utf-8")
        print("report: %s" % out)

    if min_coverage > 0 and coverage < min_coverage:
        print("reading_audit: coverage %.0f%% is below reading_audit.min_coverage "
              "%.0f%% -- these artifacts cannot be said to have read the corpus."
              % (coverage * 100, min_coverage * 100))
        return 1
    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
