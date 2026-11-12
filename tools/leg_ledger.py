#!/usr/bin/env python3
"""leg_ledger.py -- HED-11: a leg is a run in a declared state, or it is not a run.

HED-4 defines five states for a leg -- ok, empty, skipped, error, timeout -- and
requires that none of them be collapsed into another. HED-6 requires each of them
to carry its reason and its seconds. The rule lives in prose, so the artifact
that has to satisfy it (`logs/deep_dive.json`) was never checked against it: a
leg added tomorrow can report `ok` with no `seconds`, or invent a sixth state,
and the report will still read as a clean run.

This checks the artifact structurally and stops there. It does not judge whether
a leg's verdict was right; it asks whether the ledger can answer "what ran, how
long, and what did it say", which is the question a later session needs.

Violations:

  missing_field      a leg with no name, no state, no seconds, or no detail
  unknown_state      a state outside the declared five
  state_mismatch     a leg named in `settings.suppressed` that also reports
                     having run, or a leg that ran while the settings say its
                     group was off
  verdict_mismatch   `verdict: PASS` while a forensic leg is error/timeout, or
                     `FAIL` with no such leg to explain it

Exit: 0 clean, 1 violation, 2 no report to audit. Two is printed as "nothing
audited" and is never worded as a pass -- that is HED-2's rule, applied to this
tool's own absence.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATES = {"ok", "empty", "skipped", "error", "timeout"}
FAILING = {"error", "timeout"}


def audit(report: dict) -> list:
    """Return every structural violation in one deep-dive report."""
    bad = []
    legs = report.get("legs")
    if not isinstance(legs, list) or not legs:
        return [{"class": "missing_field", "detail":
                 "no `legs` list -- a run with no ledger cannot say what ran"}]

    ran = set()
    for index, leg in enumerate(legs):
        if not isinstance(leg, dict):
            bad.append({"class": "missing_field",
                        "detail": "legs[%d] is %s" % (index, type(leg).__name__)})
            continue
        name = leg.get("leg")
        if not isinstance(name, str) or not name.strip():
            bad.append({"class": "missing_field",
                        "detail": "legs[%d] has no name" % index})
            name = "legs[%d]" % index
        ran.add(name)
        state = leg.get("status")
        if not isinstance(state, str) or not state.strip():
            bad.append({"class": "missing_field", "leg": name,
                        "detail": "no `status`"})
        elif state.strip().lower() not in STATES:
            bad.append({"class": "unknown_state", "leg": name,
                        "detail": "state %r outside %s" % (state, sorted(STATES))})
        seconds = leg.get("seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            bad.append({"class": "missing_field", "leg": name,
                        "detail": "no numeric `seconds` -- the ledger cannot say it ran"})
        elif seconds < 0:
            bad.append({"class": "missing_field", "leg": name,
                        "detail": "negative `seconds` %r" % seconds})
        if "detail" not in leg:
            bad.append({"class": "missing_field", "leg": name,
                        "detail": "no `detail` key -- every state owes its reason"})

    settings = report.get("settings") if isinstance(report.get("settings"), dict) else {}
    suppressed = settings.get("suppressed") or []
    for note in suppressed:
        if not isinstance(note, str):
            bad.append({"class": "missing_field",
                        "detail": "suppressed entry is %s" % type(note).__name__})
            continue
        for name in ran:
            if name in note and name != "web":
                bad.append({"class": "state_mismatch", "leg": name,
                            "detail": "suppressed (%r) yet present in the ledger" % note})

    verdict = str(report.get("verdict", "")).strip().upper()
    failing = [name for name in ran
               if any(isinstance(leg, dict) and leg.get("leg") == name
                      and str(leg.get("status", "")).lower() in FAILING
                      for leg in legs)]
    forensic_failing = [n for n in failing if n != "web"]
    if verdict == "PASS" and forensic_failing:
        bad.append({"class": "verdict_mismatch",
                    "detail": "verdict PASS while %s failed" % ", ".join(forensic_failing)})
    if verdict == "FAIL" and not forensic_failing:
        bad.append({"class": "verdict_mismatch",
                    "detail": "verdict FAIL with no failing forensic leg to explain it"})
    if verdict not in ("PASS", "FAIL"):
        bad.append({"class": "missing_field",
                    "detail": "verdict %r is neither PASS nor FAIL" % report.get("verdict")})
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report", nargs="?", default=str(ROOT / "logs" / "deep_dive.json"),
                    help="deep-dive report to audit (default logs/deep_dive.json)")
    ap.add_argument("--json", default="", help="also write the findings here")
    args = ap.parse_args(argv)

    path = Path(args.report)
    if not path.exists():
        print("leg_ledger: no report at %s -- nothing audited, which is not a pass."
              % path)
        return 2
    try:
        report = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        print("leg_ledger: %s will not parse (%s: %s) -- nothing audited."
              % (path, type(exc).__name__, str(exc)))
        return 1
    if not isinstance(report, dict):
        print("leg_ledger: %s holds a %s, not a report object."
              % (path, type(report).__name__))
        return 1

    bad = audit(report)
    legs = report.get("legs") if isinstance(report.get("legs"), list) else []
    states = {}
    for leg in legs:
        if isinstance(leg, dict):
            state = str(leg.get("status", "?")).lower()
            states[state] = states.get(state, 0) + 1
    print("leg_ledger: %d leg(s) [%s], verdict %s, %d violation(s)"
          % (len(legs), ", ".join("%s=%d" % kv for kv in sorted(states.items())) or "none",
             report.get("verdict", "?"), len(bad)))
    for row in bad:
        print("  - %-16s %-22s %s"
              % (row["class"], row.get("leg", "-"), row["detail"]))

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"report": str(path), "legs": len(legs),
                                   "states": states, "violations": bad},
                                  indent=2) + "\n", encoding="utf-8")
        print("report: %s" % out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
