#!/usr/bin/env python
# coding: utf-8
"""Regression tests for inject_session_cain.py (injection + audit semantics).

Every oracle here ships its NEGATIVE CONTROL: a known-healthy input the check
must NOT fire on. An oracle whose control fails is unvalidated, and its findings
may not license anything.

What each section is defending, and from what (all measured 2026-11-01):

  guard      the template path used to point at CAIN_session_specific.html,
             byte-identical to this tool's own output, so the injector read its
             previous result as its input and applied 0/50 replacements.
  sites      `str.replace` with no count silently multiplies. 9 web nodes are
             duplicated in the template; a pattern expecting 1 site and finding
             2 must fail rather than rewrite both.
  cascade    the May -> June 6 overwrite entries deliberately keep their own
             `old` inside their `new`, so a naive "did the pattern survive"
             check reports 8 false positives.
  totals     the previous map replaced a $5.25 total with $590.00 while the
             rows summed to $5.26 -- the total must be derived, never asserted.
  banner     inserting the session panel into a document that already had one
             took the panel count from 1 to 2.

Run: C:/Azazael/.venvs/py315t/Scripts/python.exe test_inject_session_cain.py
"""

import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import inject_session_cain as cain  # noqa: E402

# --- fixtures (constructed, not transcribed: these are shapes, not prose) ---

LEDGER_DOC = (
    '<table>'
    '<tr><td class="amount">$185.00</td></tr>'
    '<tr><td class="amount">$270.00</td></tr>'
    '<tr><td class="amount"><strong>$5.25+</strong></td></tr>'
    '</table>'
    '<span style="color:#ff4444; font-size:2em;">-$183.00</span>'
)

LEDGER_INCONSISTENT = (
    '<td class="amount">$3.75</td>'
    '<td class="amount">$1.00</td>'
    '<td class="amount">$0.50</td>'
    '<td class="amount"><strong>$590.00</strong></td>'
)

LEDGER_CONSISTENT = (
    '<td class="amount">$185.00</td>'
    '<td class="amount">$270.00</td>'
    '<td class="amount"><strong>$455.00</strong></td>'
)

#: Off by one cent. A 0.01 tolerance passes this; an exact comparison must not.
LEDGER_OFF_BY_A_CENT = (
    '<td class="amount">$185.00</td>'
    '<td class="amount"><strong>$185.01</strong></td>'
)

#: The template really carries a $0.001 storage row, so its rows sum to a
#: sub-cent value. Rounded to the cent this is consistent, and the row is
#: surfaced as advice rather than silently absorbed.
LEDGER_SUB_CENT = (
    '<td class="amount">$0.001</td>'
    '<td class="amount">$0.01</td>'
    '<td class="amount"><strong>$0.01</strong></td>'
)

DEAD_DOC = (
    '<style>.web-arrow { display:none; }</style>'
    '<div class="web-arrow" style="top:1%"></div>'
    '<div class="web-arrow" style="top:2%"></div>'
    '<p>keep me</p>'
)

CLEAN_DOC = '<style>.x { color:red; }</style><p>nothing dead here</p>'

#: A generic string whose replacement does NOT contain it -- the real leftover
#: case. Derived from the map rather than hand-copied, so the control cannot rot
#: into a string that no longer appears anywhere.
GENERIC_LEFTOVER = next(rep.old for rep in cain.REPLACEMENTS
                        if rep.old not in rep.new)

#: A cascade `old`: its own replacement echoes it, so it is EXPECTED to remain
#: in the document afterwards and must not be reported as a leftover.
CASCADE_SURVIVOR = next(rep.old for rep in cain.REPLACEMENTS
                        if rep.old in rep.new)


def main() -> int:
    failures: list[str] = []
    total = 0

    def expect(name, got, want):
        nonlocal total
        total += 1
        if got == want:
            print("ok   %s" % name)
        else:
            failures.append(name)
            print("FAIL %s\n     got:  %r\n     want: %r" % (name, got, want))

    def raises(name, fn, exc_type=Exception, contains=None):
        """Expect fn() to raise; optionally check the message."""
        nonlocal total
        total += 1
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - any escape is graded below
            # One handler, not two: `exc_type` defaults to Exception, so a
            # `except exc_type` clause followed by `except Exception` made the
            # second unreachable and silently turned "raised the wrong type"
            # into a pass.
            if not isinstance(exc, exc_type):
                failures.append(name)
                print("FAIL %s\n     raised %s, expected %s"
                      % (name, type(exc).__name__, exc_type.__name__))
            elif contains and contains not in str(exc):
                failures.append(name)
                print("FAIL %s\n     raised %s but message lacks %r:\n     %s"
                      % (name, type(exc).__name__, contains, exc))
            else:
                print("ok   %s" % name)
        else:
            failures.append(name)
            print("FAIL %s\n     did not raise" % name)

    # --- guard: an output may never be the template ------------------------
    raises("guard: refuses a path named *injected*",
           lambda: cain.load_template(Path("CAIN_injected.html")),
           cain.InjectionError, "output")
    raises("guard: refuses a path named *session_specific*",
           lambda: cain.load_template(Path("CAIN_session_specific.html")),
           cain.InjectionError, "output")
    # negative control: the real template loads
    expect("guard: accepts the pristine template",
           "</html>" in cain.load_template().lower(), True)

    # --- site-count law ----------------------------------------------------
    fixture = "<p>alpha</p><p>beta</p>"
    out, _ = cain.apply_replacements(
        fixture, [cain.Replacement("alpha", "ALPHA", 1)])
    expect("sites: declared 1, found 1 -> applied", "ALPHA" in out, True)

    raises("sites: declared 2, found 1 -> refused",
           lambda: cain.apply_replacements(
               fixture, [cain.Replacement("alpha", "ALPHA", 2)]),
           cain.InjectionError, "did not match")
    raises("sites: declared 1, found 0 -> refused",
           lambda: cain.apply_replacements(
               fixture, [cain.Replacement("absent", "X", 1)]),
           cain.InjectionError, "did not match")

    dup = "<i>x</i><i>x</i>"
    raises("sites: declared 1, found 2 -> refused (silent multiplication)",
           lambda: cain.apply_replacements(
               dup, [cain.Replacement("<i>x</i>", "<i>y</i>", 1)]),
           cain.InjectionError, "did not match")
    # negative control: same fixture, honest declaration -> both rewritten
    out, _ = cain.apply_replacements(
        dup, [cain.Replacement("<i>x</i>", "<i>y</i>", 2)])
    expect("sites: declared 2, found 2 -> both rewritten",
           out.count("<i>y</i>"), 2)

    # --- cascade exemption vs real leftover --------------------------------
    # Both fixtures are derived, so assert the map still contains each shape.
    expect("fixtures: map has a non-echoing pattern",
           bool(GENERIC_LEFTOVER), True)
    expect("fixtures: map has an echoing (cascade) pattern",
           bool(CASCADE_SURVIVOR), True)

    blocking, _ = cain.audit(LEDGER_CONSISTENT + CASCADE_SURVIVOR)
    expect("cascade: surviving cascade string is NOT a leftover finding",
           any(CASCADE_SURVIVOR in finding for finding in blocking), False)

    blocking, _ = cain.audit(LEDGER_CONSISTENT + GENERIC_LEFTOVER)
    # negative control for the check above: a genuine generic string must fire
    expect("cascade: genuine generic leftover IS reported",
           any("survived injection" in finding for finding in blocking), True)

    # --- money: derived, never asserted ------------------------------------
    doc, info = cain.derive_totals(LEDGER_DOC)
    expect("totals: grand total derived from rows",
           "$%s" % format(Decimal(info["sum"]), ".2f"), "$455.00")
    expect("totals: grand total cell rewritten", "<strong>$455.00</strong>" in doc, True)
    expect("totals: snake tail derived from deepest row", "-$270.00" in doc, True)
    expect("totals: the hardcoded $183 is gone", "-$183.00" in doc, False)

    # idempotence: deriving twice must not drift
    doc2, info2 = cain.derive_totals(doc)
    expect("totals: derivation is idempotent", info2["sum"], info["sum"])

    blocking, _ = cain.audit(LEDGER_INCONSISTENT)
    expect("totals: inconsistent ledger is blocking",
           any("ledger inconsistent" in finding for finding in blocking), True)
    # negative control
    blocking, _ = cain.audit(LEDGER_CONSISTENT)
    expect("totals: consistent ledger is NOT blocking",
           any("ledger inconsistent" in finding for finding in blocking), False)

    # A one-cent gap must fail: a 0.01 tolerance would have hidden it.
    blocking, _ = cain.audit(LEDGER_OFF_BY_A_CENT)
    expect("totals: one-cent mismatch IS blocking (exact, no tolerance)",
           any("ledger inconsistent" in finding for finding in blocking), True)

    # Sub-cent rows: consistent after rounding, but never silently absorbed.
    blocking, advisory = cain.audit(LEDGER_SUB_CENT)
    expect("totals: sub-cent rows are NOT blocking after rounding",
           any("ledger inconsistent" in finding for finding in blocking), False)
    expect("totals: sub-cent rows are reported as advisory",
           any("sub-cent" in finding for finding in advisory), True)

    # --- banner duplication ------------------------------------------------
    blocking, _ = cain.audit(LEDGER_CONSISTENT + "SESSION CONTEXT SESSION CONTEXT")
    expect("banner: two panels is blocking",
           any("SESSION CONTEXT panel appears 2" in f for f in blocking), True)
    # negative control
    blocking, _ = cain.audit(LEDGER_CONSISTENT + "SESSION CONTEXT")
    expect("banner: one panel is NOT blocking",
           any("SESSION CONTEXT" in finding for finding in blocking), False)

    # --- dead markup stripping ---------------------------------------------
    stripped, saved = cain.strip_dead(DEAD_DOC)
    expect("strip: dead arrows removed", stripped.count('class="web-arrow"'), 0)
    expect("strip: dead CSS rule removed", ".web-arrow" in stripped, False)
    expect("strip: reported a positive saving", saved > 0, True)
    expect("strip: live content preserved", "<p>keep me</p>" in stripped, True)
    # negative control: a clean document is untouched
    unchanged, saved_clean = cain.strip_dead(CLEAN_DOC)
    expect("strip: clean document unchanged", unchanged, CLEAN_DOC)
    expect("strip: clean document saves nothing", saved_clean, 0)

    # --- end to end on the real template -----------------------------------
    html, report = cain.build()
    blocking, advisory = cain.audit(html)
    expect("e2e: every declared replacement applied",
           report["replacements_applied"], report["replacements_declared"])
    expect("e2e: no blocking findings", blocking, [])
    expect("e2e: output differs from template",
           report["output_bytes"] != report["source_bytes"], True)
    # The invariant is stated == sum quantised to the cent (money is cents);
    # the raw sum is kept separately as ledger['raw_sum'] so a sub-cent row is
    # visible rather than rounded away.
    expect("e2e: stated total equals cent-quantised row sum",
           Decimal(report["ledger"]["sum"]),
           sum(cain.ledger_rows(html), Decimal("0")).quantize(Decimal("0.01")))
    expect("e2e: one session banner",
           html.count("SESSION CONTEXT"), 1)
    expect("e2e: advisory findings are a list (may be non-empty)", 
           isinstance(advisory, list), True)

    print()
    if failures:
        print("FAILED: %d of %d checks" % (len(failures), total))
        return 1
    print("all %d CAIN injection checks passed" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
