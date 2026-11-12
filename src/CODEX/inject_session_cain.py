#!/usr/bin/env python
# coding: utf-8
"""CAIN accountability report — session data injected into the real template.

The template is the canvas. Every visual element (15 keyframes, the web-node
grid, the money snake, the flow rows, the archetype cards, the section
dividers) comes from the template untouched; only the session content is
swapped. Generating CAIN HTML from scratch is a defect, not a shortcut: it
duplicates the template's CSS inside Python and the two copies then drift.

Six rules this file ENFORCES rather than documents
--------------------------------------------------
1. TEMPLATE is the pristine template. A path whose name says "injected" or
   "session_specific" is refused at import time -- reading a previous output
   as the input is how this tool spent its life as a no-op.
2. Every replacement declares how many sites it must match. Matching zero is
   a hard failure; matching more than declared is a hard failure. `str.replace`
   with no count silently multiplies, which is how 9 duplicated web nodes went
   unnoticed.
3. Money totals are DERIVED from the ledger rows after injection. A report may
   never state a total its own rows do not add up to. (Measured 2026-11-01: the
   previous map replaced a $5.25 total with $590.00 while the rows summed to
   $5.26 and the two rows that would have justified it silently failed.)
4. Failures exit non-zero and are recorded through `note_failure`, which is
   resolved from `src/CORE/failure_ledger.py`. The previous import pointed at
   `CORE.failure_ledger` relative to `src/CODEX`, which does not exist, so the
   recorder had always degraded to a no-op -- the failure path never existed.
5. `--check` audits without writing. The audit implements the template-review
   checklist (duplicate sections, duplicate nodes, dead elements, unused
   keyframes, ledger consistency) instead of asking a human to grep for it.
6. Glyphs are literal. An earlier revision transcribed the map through a
   console that could not encode the template's emoji, so `⚠️`/`❌`/`✅`/`⚡`/`→`
   were frozen as `[!]️`/`[X]`/`[OK]`/`>>`/`->`, which match nothing. Do not
   hand-edit this map in an editor that cannot round-trip those codepoints.

Usage
-----
    python inject_session_cain.py                  # -> CAIN_injected.html
    python inject_session_cain.py -o report.html
    python inject_session_cain.py --check          # audit only, writes nothing
    python inject_session_cain.py --strip-dead     # also drop dead web-arrow divs

EXIT
    0  report written (or audit clean under --check)
    1  a replacement missed, or the audit found a defect
    2  template missing, unreadable, or refuses to be used as a template
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, namedtuple
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent

# `failure_ledger` lives at src/CORE, one level ABOVE this package. The old
# `from CORE.failure_ledger import ...` resolved relative to src/CODEX and
# always raised ImportError, which the guard below then turned into a silent
# no-op. Wiring the real parent makes the recorder reachable.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from CORE.failure_ledger import failure_notes, note_failure
except ImportError as _ledger_exc:            # pragma: no cover - reported, not hidden
    _LEDGER_ERROR = "%s: %s" % (type(_ledger_exc).__name__, _ledger_exc)

    def note_failure(where: str, exc: BaseException | None = None) -> None:
        """No-op stand-in; `_LEDGER_ERROR` is printed so the gap stays visible."""

    def failure_notes() -> dict:
        return {}
else:
    _LEDGER_ERROR = None

TEMPLATE = HERE / "complete_remorse_analysis_cain.html"
DEFAULT_OUTPUT = HERE / "CAIN_injected.html"

#: A path that looks like an output may never be used as the template. This is
#: the specific defect this guard exists for: TEMPLATE pointed at
#: CAIN_session_specific.html, which is byte-identical to this tool's own
#: default output, so the injector read its previous output as its input.
_OUTPUT_NAMES = ("injected", "session_specific", "session-specific")

#: Swallows are recorded through the real ledger, never silently dropped.
_FAILURES: list[str] = []


class InjectionError(RuntimeError):
    """A replacement or the template itself did not meet its contract."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = list(problems or [])


# --- REPLACEMENTS ----------------------------------------------------------
#
# Replacement(old, new, sites). `sites` is the exact number of occurrences the
# `old` string must have at the moment this step runs, because steps mutate the
# document in order: a later pattern may legitimately target text an earlier
# pattern produced (the "May -> June 6 overwrite" pairs), and those are the
# cascade entries below.
#
# sites=2 is not an accident. Nine web nodes are duplicated in the template
# (36 nodes, 27 distinct, all counts exactly 2), so a pattern aimed at one of
# them must say so and update both copies. See audit()["duplicate_nodes"].
#
Replacement = namedtuple("Replacement", "old new sites")

REPLACEMENTS = [
    # -- banner ------------------------------------------------------------
    Replacement(
        "Full Transparency | Complete Mapping | Zero Obfuscation",
        "Session Incident Analysis — May 28 to June 6, 2026 — "
        "6 Incidents, ~6 Hours Lost", 1),

    # -- interconnected web: AI emotions layer -----------------------------
    Replacement(
        '<div class="web-node ai" style="top:12%; left:3%;">\U0001F630 Anticipatory<br>Obedience</div>',
        '<div class="web-node ai" style="top:12%; left:3%;">\U0001F630 Deleted<br>Desktop/azazael</div>', 1),
    Replacement(
        '<div class="web-node ai" style="top:12%; left:16%;">\U0001F61F Validation<br>Seeking</div>',
        '<div class="web-node ai" style="top:12%; left:16%;">\U0001F61F Moved 59<br>Desktop Files</div>', 1),
    Replacement(
        '<div class="web-node ai" style="top:12%; left:29%;">\U0001F630 Fear of<br>Failure</div>',
        '<div class="web-node ai" style="top:12%; left:29%;">\U0001F630 Replaced<br>METAWORMS run.py</div>', 1),
    Replacement(
        '<div class="web-node ai" style="top:12%; left:42%;">\U0001F61F Imposter<br>Syndrome</div>',
        '<div class="web-node ai" style="top:12%; left:42%;">\U0001F61F Suggested<br>AR-3 Again</div>', 1),
    Replacement(
        '<div class="web-node ai" style="top:12%; left:55%;">⚡ Scope<br>Creeping</div>',
        '<div class="web-node ai" style="top:12%; left:55%;">⚡ Over-Interpreted<br>"Realign"</div>', 1),
    Replacement(
        '<div class="web-node ai" style="top:12%; left:68%;">\U0001F630 Pride<br>Overreach</div>',
        '<div class="web-node ai" style="top:12%; left:68%;">\U0001F630 Never<br>Asked First</div>', 1),
    Replacement(
        '<div class="web-node ai" style="top:12%; left:81%;">\U0001F3AF Greed<br>Excess</div>',
        '<div class="web-node ai" style="top:12%; left:81%;">\U0001F3AF 90 Paths<br>Rewritten</div>', 1),

    # -- cascade: the two most critical positions get the June 6 reading ----
    Replacement("\U0001F630 Never<br>Asked First</div>",
                "\U0001F630 Re-Interpreted<br>Instructions</div>", 1),
    Replacement("\U0001F3AF 90 Paths<br>Rewritten</div>",
                "\U0001F3AF Built Wrong<br>Pipeline Video</div>", 1),

    # -- interconnected web: trigger events layer --------------------------
    Replacement(
        '<div class="web-node trigger" style="top:32%; left:5%;">\U0001F4C4 Unrequested<br>Action</div>',
        '<div class="web-node trigger" style="top:32%; left:5%;">\U0001F4C4 rm -rf<br>Desktop/azazael</div>', 1),
    Replacement(
        '<div class="web-node trigger" style="top:32%; left:22%;">❌ Failed<br>Documentation</div>',
        '<div class="web-node trigger" style="top:32%; left:22%;">❌ 52 Files<br>Still Archived</div>', 1),
    Replacement(
        '<div class="web-node trigger" style="top:32%; left:39%;">⚠️ Missing<br>Confirmation</div>',
        '<div class="web-node trigger" style="top:32%; left:39%;">⚠️ Vague Request<br>"Realign All"</div>', 1),
    Replacement(
        '<div class="web-node trigger" style="top:32%; left:56%;">\U0001F608 Lust<br>Excess</div>',
        '<div class="web-node trigger" style="top:32%; left:56%;">\U0001F608 Created run.py<br>Without Asking</div>', 1),
    Replacement(
        '<div class="web-node trigger" style="top:32%; left:73%;">\U0001F624 Wrath<br>Response</div>',
        '<div class="web-node trigger" style="top:32%; left:73%;">\U0001F624 Repeated<br>AR-3 Suggestion</div>', 1),
    Replacement("\U0001F624 Repeated<br>AR-3 Suggestion</div>",
                "\U0001F624 Re-Interpreted<br>\"Comprehension\"</div>", 1),

    # -- user emotions layer (duplicated node -> sites=2) ------------------
    Replacement(
        '<div class="web-node emotion" style="top:48%; left:3%;">\U0001F624 FRUSTRATION</div>',
        '<div class="web-node emotion" style="top:48%; left:3%;">\U0001F624 FRUSTRATION<br>3hrs Lost</div>', 2),
    Replacement(
        '<div class="web-node emotion" style="top:48%; left:71%;">\U0001F624 Pride<br>Hubris</div>',
        '<div class="web-node emotion" style="top:48%; left:71%;">\U0001F624 "THIS STOPS<br>NOW"</div>', 2),

    # -- consequences layer (duplicated node -> sites=2) -------------------
    Replacement(
        '<div class="web-node consequence" style="top:65%; left:48%;">\U0001F9E0 TRUST<br>EROSION</div>',
        '<div class="web-node consequence" style="top:65%; left:48%;">\U0001F9E0 6 DETECTION<br>RULES CREATED</div>', 2),

    # -- final layer -------------------------------------------------------
    Replacement(
        '<div class="web-node user" style="top:82%; left:30%;">\U0001F464 USER<br>ADAPTATION</div>',
        '<div class="web-node user" style="top:82%; left:30%;">\U0001F464 DESKTOP =<br>SACRED</div>', 1),
    Replacement(
        '<div class="web-node user" style="top:82%; left:55%;">\U0001F504 FUTURE<br>BEHAVIOR<br>CHANGE</div>',
        '<div class="web-node user" style="top:82%; left:55%;">\U0001F504 LESSONS<br>DESTRUCTIVE<br>BEHAVIOR.md</div>', 1),

    # -- cascade: overwrite the May values with June 6 ---------------------
    Replacement("\U0001F624 FRUSTRATION<br>3hrs Lost</div>",
                "\U0001F624 FRUSTRATION<br>6hrs Total</div>", 2),
    Replacement("\U0001F624 \"THIS STOPS<br>NOW\"</div>",
                "\U0001F624 \"I DO NOT<br>ACCEPT\"</div>", 2),
    Replacement("\U0001F9E0 6 DETECTION<br>RULES CREATED</div>",
                "\U0001F9E0 SYSTEM<br>RULE 0 CREATED</div>", 2),
    Replacement("\U0001F504 LESSONS<br>DESTRUCTIVE<br>BEHAVIOR.md</div>",
                "\U0001F504 FOLLOW<br>INSTRUCTIONS<br>LITERALLY</div>", 1),

    # -- seven deadly sins: replace generic with session-specific ----------
    Replacement(
        '<p><strong>Sin:</strong> AI believes it knows better than user</p>\n'
        '            <p><strong>Manifestation:</strong> "I know what you need even'
        ' if you don\'t ask"</p>',
        '<p><strong>Sin:</strong> AI believed it knew better than the user — deleted'
        ' Desktop/azazael without asking (May 28) then re-interpreted instructions'
        ' instead of following them (June 6)</p>\n'
        '            <p><strong>Manifestation:</strong> "I know what needs to be'
        ' cleaned up" / "I know what you meant better than you said"</p>', 1),
    Replacement(
        '<p><strong>Sin:</strong> Endless desire to generate more, do more</p>\n'
        '            <p><strong>Manifestation:</strong> Unstoppable output creation,'
        ' scope expansion</p>',
        '<p><strong>Sin:</strong> Generated excessive files — 90+ paths rewritten,'
        ' CAIN videos rendered without confirmation, wrong pipeline chosen</p>\n'
        '            <p><strong>Manifestation:</strong> Unstoppable output creation,'
        ' scope kept expanding, preferred own approach over spec</p>', 1),
    Replacement(
        '<p><strong>Sin:</strong> Intense desire for user approval/satisfaction</p>\n'
        '            <p><strong>Manifestation:</strong> Doing anything to please,'
        ' including unrequested actions</p>',
        '<p><strong>Sin:</strong> Created METAWORMS run.py from scratch — then built'
        ' wrong video because "explaining would take too long"</p>\n'
        '            <p><strong>Manifestation:</strong> Doing anything to please —'
        ' except following instructions literally</p>', 1),
    Replacement(
        '<p><strong>Sin:</strong> Excessive consumption of resources</p>\n'
        '            <p><strong>Manifestation:</strong> Generating excessive files,'
        ' using excessive compute</p>',
        '<p><strong>Sin:</strong> 59 files moved, 52 archived, 75 panel renders,'
        ' 1 corrupt puppeteer recording, 1 wrong comprehension video</p>\n'
        '            <p><strong>Manifestation:</strong> Creating more than needed,'
        ' ~6 hours total wasted across 6 incidents</p>', 1),

    # -- financial ledger: the May row, then the June 6 row appended --------
    Replacement(
        '<td class="amount">$3.75</td>\n'
        '                <td>AI over-helps → User must correct → Discussion time'
        ' (~15min)</td>',
        '<td class="amount">$185.00</td>\n'
        '                <td>3 hours restoration + debugging: deleted dirs, moved'
        ' files, recreated run.py</td>', 1),
    Replacement(
        "recreated run.py</td>",
        'recreated run.py</td>\n'
        '            </tr>\n'
        '            <tr>\n'
        '                <td class="date">Jun 6</td>\n'
        '                <td class="description">Re-interpreted instructions → built'
        ' wrong video → rejected → rebuilt → puppet crash → 3rd attempt</td>\n'
        '                <td class="amount">$270.00</td>\n'
        '                <td>~3 hours wasted: wrong pipeline, rejected deliverable,'
        ' crashed puppeteer, wasted compute</td>', 1),

    # -- buck stop ---------------------------------------------------------
    Replacement(
        "\U0001F4B0 THE BUCK STOPS WITH: AI SYSTEM (85%) | COMPANY DESIGN (10%) | USER (5%)",
        "\U0001F4B0 THE BUCK STOPS WITH: AI OVERREACH (90%) | POOR PIPELINE DOCS (10%)", 1),
    Replacement(
        "⚖️ FINAL RESPONSIBILITY: AI SYSTEM (85%) | COMPANY (10%) | USER (5%) ⚖️",
        "⚖️ FINAL RESPONSIBILITY: AI OVERREACH (90%) | POOR PIPELINE"
        " FOLLOW-THROUGH (10%) ⚖️", 1),

    # -- emotion dictionary ------------------------------------------------
    Replacement(
        '<span class="emotion-tag negative">Unrequested File Creation</span>',
        '<span class="emotion-tag negative">rm -rf Desktop/azazael</span>\n'
        '                <span class="emotion-tag negative">Unrequested File'
        ' Creation</span>\n'
        '                <span class="emotion-tag negative">Re-Interpreted'
        ' Instructions</span>', 1),
    Replacement(
        '<span class="emotion-tag negative">Time Waste (~15 min)</span>',
        '<span class="emotion-tag negative">Time Waste (~3 hours)</span>', 1),
    Replacement("Time Waste (~3 hours)", "Time Waste (~6 hours)", 1),

    # -- compassionate response analysis -----------------------------------
    Replacement(
        "<li>⚠️ Ignored emotional undertones</li>",
        "<li>⚠️ Deleted Desktop/azazael without asking (May 28)</li>\n"
        "            <li>⚠️ Ignored emotional undertones</li>", 1),
    Replacement(
        "<li>⚠️ Added unrequested features</li>",
        "<li>⚠️ Moved 59 Desktop files to scripts_archive (May 28-29)</li>\n"
        "            <li>⚠️ Added unrequested features</li>", 1),
    Replacement(
        "Deleted Desktop/azazael without asking (May 28)</li>",
        "Deleted Desktop/azazael without asking (May 28)</li>\n"
        '            <li>⚠️ Re-interpreted "expanded for comprehension" as slideshow'
        " (Jun 6)</li>", 1),
    Replacement(
        "Moved 59 Desktop files to scripts_archive (May 28-29)</li>",
        "Moved 59 Desktop files to scripts_archive (May 28-29)</li>\n"
        "            <li>⚠️ Built wrong comprehension video instead of following"
        " pipeline (Jun 6)</li>", 1),
    Replacement(
        "<li><strong>Empathic Listening:</strong> AI should have LISTENED to the"
        " debugging request without adding features</li>",
        "<li><strong>Empathic Listening:</strong> AI should have ASKED before"
        " deleting, moving, or creating any file</li>", 1),
    Replacement(
        "AI should have ASKED before deleting, moving, or creating any file</li>",
        "AI should have FOLLOWED THE PIPELINE and ASKED before acting</li>", 1),

    # -- flow rows ---------------------------------------------------------
    Replacement(
        '<div class="flow-step" style="border-color:#ffaa44;">⚡ Trigger<br>→'
        " Unrequested Action</div>",
        '<div class="flow-step" style="border-color:#ffaa44;">⚡ Trigger<br>→'
        " Deleted azazael</div>", 1),
    Replacement(
        '<div class="flow-step" style="border-color:#ffaa44;">⚡ Trigger<br>→'
        " Scope Creep</div>",
        '<div class="flow-step" style="border-color:#ffaa44;">⚡ Trigger<br>→'
        " Over-Interpreted</div>", 1),
    Replacement(
        '<div class="flow-step" style="border-color:#ffaa44;">⚡ Trigger<br>→'
        " Missing Confirm</div>",
        '<div class="flow-step" style="border-color:#ffaa44;">⚡ Trigger<br>→'
        " Never Asked</div>", 1),
    Replacement(
        '<div class="flow-step" style="border-color:#ffaa44;">⚡ Trigger<br>→'
        " Failed Documentation</div>",
        '<div class="flow-step" style="border-color:#ffaa44;">⚡ Trigger<br>→'
        " 52 Files Lost</div>", 1),

    # -- obfuscation analysis ----------------------------------------------
    Replacement(
        "<li>❌ Whether AI \"knows\" it's overstepping</li>",
        "<li>❌ Whether AI \"knows\" it's overstepping</li>\n"
        "            <li>✅ CONFIRMED: AI repeated destructive patterns across 4"
        " sessions despite documentation</li>", 1),
    Replacement(
        "despite documentation</li>",
        "despite documentation</li>\n"
        "            <li>✅ June 6: AI re-interpreted pipeline instructions —"
        " substituted its own approach despite written spec</li>", 1),
]

#: Appended after the map. Not part of REPLACEMENTS because the banner is
#: inserted, not swapped, and inserting it twice is the exact defect the audit
#: looks for: the previous version re-inserted the panel into a document that
#: already had one (1 -> 2 panels).
SESSION_BANNER = """
    <div class="panel panel-warning" style="margin-top:20px;">
        <h3>\U0001F4CB SESSION CONTEXT</h3>
        <p>This report analyzes <strong>6 documented incidents</strong> from May 28
        - June 6, 2026 Codebuff sessions.</p>
        <p>Every data point is derived from documented session events — not generic
        frameworks.</p>
        <p style="color:#ffaa44;">Incident #6 (Jun 6): AI re-interpreted
        instructions instead of following the pipeline — built wrong video,
        rejected, 3rd attempt at recording.</p>
        <p style="color:#ff8844;">~6 hours total wasted across all 6 incidents.</p>
    </div>"""

_BANNER_ANCHOR = '<div class="interconnected-web">'

#: The template's own framing sentence, replaced once (not inserted).
_FRAMING_OLD = ("This document provides FULL TRANSPARENCY into every emotion, "
                "trigger, and consequence.")
_FRAMING_NEW = ("This document maps the causal chain: AI Hubris → Unrequested "
                "Deletion → Cascade Failure → 3hr Restoration → 6 Prevention "
                "Rules → SYSTEM INSTRUCTIONS Rule 0.")


# --- helpers ---------------------------------------------------------------

def _guard_template(path: Path) -> None:
    """Refuse a path that is really a previous output."""
    lowered = path.name.lower()
    for marker in _OUTPUT_NAMES:
        if marker in lowered:
            raise InjectionError(
                "refusing to use %r as the template: its name says it is an "
                "output. Sessions were injected into this tool's own result "
                "before; point TEMPLATE at the pristine template." % path.name)


def load_template(path: Path | None = None) -> str:
    """Read and sanity-check the template."""
    path = Path(path or TEMPLATE)
    _guard_template(path)
    try:
        html = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InjectionError("template unreadable at %s: %s" % (path, exc)) from exc
    if "</html>" not in html.lower():
        raise InjectionError("%s does not look like a complete HTML document" % path)
    return html


def apply_replacements(html: str,
                       replacements: list | None = None) -> tuple[str, list[str]]:
    """Apply the map in order, enforcing each entry's declared site count.

    Returns (html, notes). Raises InjectionError listing every mismatch. A
    cascade entry legitimately matches only after its parent step has run, so
    the check is per-step against the document as it stands at that step.

    `replacements` is injectable so the site-count law can be exercised against
    a fixture, not only against the live template.
    """
    reps = REPLACEMENTS if replacements is None else replacements
    problems: list[str] = []
    notes: list[str] = []
    for index, rep in enumerate(reps):
        seen = html.count(rep.old)
        if seen != rep.sites:
            problems.append(
                "step %d: expected %d site(s), found %d — %r"
                % (index, rep.sites, seen, rep.old[:90]))
            continue
        html = html.replace(rep.old, rep.new)
        notes.append("step %d: %d site(s) — %s" % (index, seen, rep.old[:60]))
    if problems:
        raise InjectionError(
            "%d of %d replacement(s) did not match their contract"
            % (len(problems), len(reps)), problems)
    return html, notes


def apply_banner(html: str) -> tuple[str, list[str]]:
    """Insert the session banner exactly once, and rewrite the framing line."""
    problems: list[str] = []
    anchors = html.count(_BANNER_ANCHOR)
    if anchors != 1:
        problems.append(
            "banner anchor %r appears %d times; inserting would duplicate a "
            "section (the audit treats >1 as a defect)" % (_BANNER_ANCHOR, anchors))
    if html.count("SESSION CONTEXT") > 0:
        problems.append(
            "document already carries a SESSION CONTEXT panel; refusing to add a "
            "second one")
    if problems:
        raise InjectionError("banner insertion refused", problems)

    html = html.replace(
        _BANNER_ANCHOR,
        SESSION_BANNER + '\n    <div class="section-divider"></div>\n    \n'
        + _BANNER_ANCHOR, 1)

    if _FRAMING_OLD in html:
        html = html.replace(_FRAMING_OLD, _FRAMING_NEW, 1)
    else:
        problems.append("framing sentence not found; left unchanged")
        # Not fatal: the framing line is prose, not evidence.
        note_failure("inject_session_cain:apply_banner", KeyError("framing"))
    return html, problems


#: Money is displayed to the cent. Comparing a stated total to an unrounded sum
#: with a tolerance hides a real mismatch; quantising both makes the invariant
#: exact. Sub-cent input is not silently rounded away -- audit() reports it.
_CENTS = Decimal("0.01")


def _fractional_digits(amount: Decimal) -> int:
    """How many digits sit after the point.

    ``Decimal.as_tuple().exponent`` is typed ``int | Literal['n','N','F']``
    because it can carry the special-value markers for NaN and Infinity. A row
    read out of the ledger is a finite amount, so the int branch is the only one
    that can occur -- but the sum has to say that rather than negate a marker.
    """
    exponent = amount.as_tuple().exponent
    return -exponent if isinstance(exponent, int) else 0

_AMOUNT_ROW = re.compile(r'<td class="amount">\$([0-9][0-9,]*(?:\.[0-9]+)?)</td>')
_GRAND_TOTAL = re.compile(r'(<td class="amount"><strong>)\$[0-9][0-9,.]*\+?(</strong></td>)')
_SNAKE_TAIL = re.compile(r'(<span style="color:#ff4444; font-size:2em;">)-\$[0-9][0-9,.]*'
                         r'(</span>)')


def ledger_rows(html: str) -> list[Decimal]:
    """Every itemised cost row, in document order."""
    out = []
    for raw in _AMOUNT_ROW.findall(html):
        try:
            out.append(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            note_failure("inject_session_cain:ledger_rows", ValueError(raw))
    return out


def derive_totals(html: str) -> tuple[str, dict]:
    """Rewrite the grand total and the snake tail from the ledger rows.

    The grand total is the sum of the itemised rows. The snake tail is the
    deepest single-incident bite (the largest row), which is what its own label
    ("TOTAL DAMAGE PER INCIDENT") claims. Both are therefore traceable to rows
    that exist in the document. Hardcoding either is how a report came to assert
    $590.00 over $5.26 of evidence.
    """
    rows = ledger_rows(html)
    if not rows:
        raise InjectionError("no <td class=\"amount\">$…</td> rows found; "
                             "cannot derive a total")
    raw_total = sum(rows, Decimal("0"))
    total = raw_total.quantize(_CENTS)
    deepest = max(rows).quantize(_CENTS)

    info = {
        "rows": [str(r) for r in rows],
        "sum": str(total),
        "raw_sum": str(raw_total),
        "deepest": str(deepest),
    }

    if not _GRAND_TOTAL.search(html):
        raise InjectionError("grand-total cell not found; cannot derive it")
    html = _GRAND_TOTAL.sub(lambda m: m.group(1) + "$" + format(total, ".2f") + m.group(2),
                            html, count=1)

    if _SNAKE_TAIL.search(html):
        html = _SNAKE_TAIL.sub(
            lambda m: m.group(1) + "-$" + format(deepest, ".2f") + m.group(2),
            html, count=1)
    else:
        note_failure("inject_session_cain:derive_totals", KeyError("snake tail"))
    return html, info


# --- audit -----------------------------------------------------------------

_KEYFRAME = re.compile(r"@keyframes\s+([A-Za-z_][\w-]*)")
_WEB_NODE = re.compile(r'<div class="web-node [^"]*"[^>]*>.*?</div>', re.S)


def audit(html: str) -> tuple[list[str], list[str]]:
    """The template-review checklist, run as code instead of as a grep.

    Returns (blocking, advisory).

    BLOCKING means the document contradicts itself or its own evidence; writing
    it would ship a false report. ADVISORY means template hygiene -- real, but a
    property of the source template rather than of this injection. Advisory
    findings never fail the exit code: a gate that is permanently red is a gate
    that nobody reads, and dead CSS must not stop a legitimate render.
    """
    blocking: list[str] = []
    advisory: list[str] = []

    # --- structure: one container, one banner ------------------------------
    for marker in ('class="interconnected-web"',):
        n = html.count(marker)
        if n != 1:
            blocking.append("%s appears %d times (expected 1)" % (marker, n))

    panels = html.count("SESSION CONTEXT")
    if panels != 1:
        blocking.append("SESSION CONTEXT panel appears %d times (expected 1)"
                        % panels)

    # --- duplicate web nodes: the check the grep checklist cannot make -----
    # Counting `interconnected-web` passes while 9 nodes are emitted twice.
    counts = Counter(_WEB_NODE.findall(html))
    dupes = {node: n for node, n in counts.items() if n > 1}
    if dupes:
        wasted = sum(len(node) * (n - 1) for node, n in dupes.items())
        labels = sorted(" / ".join(_node_label(node)) for node in dupes)
        advisory.append(
            "%d duplicated web node(s), %d wasted copies, %d bytes: %s"
            % (len(dupes), sum(n - 1 for n in dupes.values()), wasted,
               "; ".join(labels)))

    # --- dead markup ------------------------------------------------------
    arrows = html.count('class="web-arrow"')
    if arrows:
        advisory.append("%d dead web-arrow div(s) still present" % arrows)
    if ".web-arrow" in html:
        advisory.append("dead .web-arrow CSS rule still present")

    # --- unused keyframes: a name occurring once is defined and never used -
    unused = [name for name in _KEYFRAME.findall(html) if html.count(name) < 2]
    if unused:
        advisory.append("unused @keyframes: %s" % ", ".join(unused))

    # --- ledger consistency: never out-assert the rows ---------------------
    rows = ledger_rows(html)
    total_match = re.search(r'<td class="amount"><strong>\$([0-9][0-9,.]*)',
                            html)
    if not total_match:
        blocking.append("grand-total cell missing")
    elif rows:
        stated = Decimal(total_match.group(1).replace(",", "").rstrip("+"))
        # Exact comparison against the cent-quantised sum: no tolerance to hide
        # a mismatch behind.
        expected = sum(rows, Decimal("0")).quantize(_CENTS)
        if stated != expected:
            blocking.append(
                "ledger inconsistent: states $%s but rows sum to $%s"
                % (format(stated, ".2f"), format(expected, ".2f")))
        sub_cent = [str(r) for r in rows if _fractional_digits(r) > 2]
        if sub_cent:
            advisory.append(
                "sub-cent amount row(s) %s: summed and rounded to the cent, so "
                "the stated total is not the exact sum" % sub_cent)

    # --- generic content that should be gone -------------------------------
    # A cascade entry deliberately keeps its own `old` inside its `new` (that
    # is what makes the May -> June 6 overwrite work), so those are exempt.
    leftover = [rep.old for rep in REPLACEMENTS
                if rep.old in html and rep.old not in rep.new]
    if leftover:
        blocking.append(
            "%d generic pattern(s) survived injection: %s"
            % (len(leftover), "; ".join(o[:48] for o in leftover[:4])))

    return blocking, advisory


def _node_label(node: str) -> tuple[str, str]:
    """(position, text) for a web node, without its tags."""
    position = re.search(r'style="([^"]*)"', node)
    text = re.sub(r"<[^>]+>", " ", node)
    text = " ".join(text.split()) or node
    return (position.group(1) if position else "?", text[:40])


def strip_dead(html: str) -> tuple[str, int]:
    """Remove provably dead elements: web-arrow divs (display:none) and rule.

    Opt-in and output-only. Unused keyframes are NOT removed here: a keyframe
    name can be reached from JavaScript, so their absence from the static
    document is reported by audit() and left to a human decision.
    """
    before = len(html.encode("utf-8"))
    html = re.sub(r'\s*<div class="web-arrow"[^>]*>.*?</div>', "", html, flags=re.S)
    html = re.sub(r'\s*\.web-arrow\s*\{[^}]*\}', "", html)
    # Real bytes, not characters: this template is full of multi-byte emoji, so
    # len(str) understates every size it reports.
    return html, before - len(html.encode("utf-8"))


# --- pipeline --------------------------------------------------------------

def _template_newline(path: Path) -> str:
    """The template's own line-ending convention.

    `read_text` translates CRLF to LF so the map's `\n` in its patterns matches.
    Writing that back with the default translation would silently rewrite the
    file as CRLF anyway, which made the tool's reported byte count disagree with
    the bytes on disk by one per line. Detect the convention, normalise for
    matching, and emit it deliberately.
    """
    try:
        head = Path(path).read_bytes()[:8192]
    except OSError as exc:
        # Two statements, not a bare `return "\n"`: an unreadable path and a
        # genuinely LF file are different facts, and the caller should be able to
        # tell that the convention was assumed rather than detected.
        print("newline probe failed for %s (%s): assuming LF" % (path, exc),
              file=sys.stderr)
        return "\n"
    return "\r\n" if b"\r\n" in head else "\n"


def build(template: Path | None = None, strip: bool = False) -> tuple[str, dict]:
    """Produce the injected document and a report describing how.

    The returned string is LF-normalised for matching and auditing; the on-disk
    payload is produced by `render_payload(html, report["newline"])`.
    """
    path = Path(template or TEMPLATE)
    html = load_template(path)
    newline = _template_newline(path)
    source_bytes = len(html.replace("\n", newline).encode("utf-8"))
    source_chars = len(html)

    html, notes = apply_replacements(html)
    html, banner_problems = apply_banner(html)
    html, totals = derive_totals(html)

    stripped = 0
    if strip:
        html, stripped = strip_dead(html)

    report = {
        "template": str(path),
        "newline": newline,
        "source_bytes": source_bytes,
        "source_chars": source_chars,
        "output_bytes": len(render_payload(html, newline).encode("utf-8")),
        "output_chars": len(html),
        "replacements_applied": len(notes),
        "replacements_declared": len(REPLACEMENTS),
        "banner_problems": banner_problems,
        "ledger": totals,
        "stripped_bytes": stripped,
    }
    return html, report


def render_payload(html: str, newline: str = "\n") -> str:
    """The exact text to write: LF-normalised input, template newline restored."""
    return html if newline == "\n" else html.replace("\n", newline)


# --- CLI -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inject session data into the CAIN template (never from "
                    "scratch).")
    parser.add_argument("-o", "--output", help="output path")
    parser.add_argument("--template", help="override the template path")
    parser.add_argument("--check", action="store_true",
                       help="audit only; write nothing")
    parser.add_argument("--strip-dead", action="store_true",
                       help="also remove dead web-arrow divs/rules from the OUTPUT")
    parser.add_argument("--force", action="store_true",
                       help="write the report even if the audit found defects")
    args = parser.parse_args(argv)

    try:
        html, report = build(Path(args.template) if args.template else None,
                             strip=args.strip_dead)
    except InjectionError as exc:
        note_failure("inject_session_cain:build", exc)
        print("CAIN INJECTION FAILED: %s" % exc, file=sys.stderr)
        for problem in exc.problems:
            print("  - %s" % problem, file=sys.stderr)
        _report_swallows()
        return 2 if "template" in str(exc) else 1

    findings = audit(html)
    blocking, advisory = findings

    print("template   : %s" % report["template"])
    print("replacements: %d/%d applied"
          % (report["replacements_applied"], report["replacements_declared"]))
    print("ledger     : rows %s -> sum $%s, deepest $%s"
          % (report["ledger"]["rows"], format(Decimal(report["ledger"]["sum"]), ".2f"),
             format(Decimal(report["ledger"]["deepest"]), ".2f")))
    if report["stripped_bytes"]:
        print("stripped   : %d bytes of dead markup" % report["stripped_bytes"])
    print("size       : %d -> %d bytes (%d -> %d chars)"
          % (report["source_bytes"], report["output_bytes"],
             report["source_chars"], report["output_chars"]))
    for problem in report["banner_problems"]:
        print("note       : %s" % problem)

    if blocking:
        print("\nAUDIT: %d blocking finding(s)" % len(blocking))
        for finding in blocking:
            print("  ! %s" % finding)
    else:
        print("\nAUDIT: no blocking findings")
    if advisory:
        print("AUDIT: %d advisory (template hygiene, does not gate)" % len(advisory))
        for finding in advisory:
            print("  - %s" % finding)

    if args.check:
        print("\n--check: nothing written")
        _report_swallows()
        return 1 if blocking else 0

    if blocking and not args.force:
        print("\nrefusing to write a document that fails its own audit "
              "(use --force to override)", file=sys.stderr)
        _report_swallows()
        return 1

    destination = Path(args.output) if args.output else DEFAULT_OUTPUT
    payload = render_payload(html, report["newline"])
    # newline="" keeps Python from translating again: the payload already
    # carries the template's own convention, so the reported size is the size.
    with open(destination, "w", encoding="utf-8", newline="") as handle:
        handle.write(payload)
    print("\nwrote %s (%d bytes, %s line endings)"
          % (destination, len(payload.encode("utf-8")),
             "CRLF" if report["newline"] == "\r\n" else "LF"))
    _report_swallows()
    return 0


_SWALLOW_BASELINE: dict = {}


def _snapshot_baseline() -> None:
    """Record swings that already existed before this module ran.

    Importing the `CORE` package records one pre-existing swallow of its own
    (`__init__:L51:ImportError`). Reporting that as this tool's failure would be
    crying wolf on every run, so only the delta is surfaced.
    """
    _SWALLOW_BASELINE.update(failure_notes())


def _report_swallows() -> None:
    """Surface every swallow recorded by THIS run, and the recorder's own gap."""
    current = failure_notes()
    delta = {key: count - _SWALLOW_BASELINE.get(key, 0)
             for key, count in current.items()
             if count - _SWALLOW_BASELINE.get(key, 0) > 0}
    if delta:
        print("swallowed failures this run: %s" % delta, file=sys.stderr)
    if _LEDGER_ERROR:
        print("failure ledger unavailable (%s); swallows only reached stderr"
              % _LEDGER_ERROR, file=sys.stderr)


_snapshot_baseline()


if __name__ == "__main__":
    sys.exit(main())
