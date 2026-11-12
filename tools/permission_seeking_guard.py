"""AP-5 permission-seeking guard.

Detects assistant text that frames an already-MANDATED action as optional.

Canon: SYSTEM_INSTRUCTIONS.md:2046 (AP-5), MEMORIES.md:258 (anti-pattern #16),
MANDATES.md:150, SKILLS_CATALOG.md:87. Catalogued CRITICAL process violation
per instance.

Why this exists: AP-5 was codified in four places and enforced in none of
them. Compliance reduced to recall, and recall failed. See
tools/permission_seeking_guard.py tests for the negative controls that keep
this validator from becoming its own false-green instrument.

Single rulebook, many sources: this module is the ONLY place AP-5 patterns
live. tools/transcript_ap5_gate.py applies `scan()` to EVERY registered
transcript source (its SOURCE_REGISTRY rows) through its shared
`_findings_for`; per-source work is packaged there, detection semantics
change only here, so no source can silently fork the rulebook.

ANNOUNCEMENT vs REQUEST -- the discrimination this rulebook now makes
---------------------------------------------------------------------
AP-5 forbids framing an already-MANDATED action as OPTIONAL. Two sentence
shapes were being conflated:

  * a REQUEST offers to do the work at the operator's discretion --
    "Want me to X?", "Say the word and I'll X", "Shall I X?". The decision
    is handed to the operator, which is the violation.
  * an ANNOUNCEMENT names the assistant's own in-progress action --
    "Let me X", "I'll now X". No decision is offered; the action is already
    being taken. Nothing is made optional.

Measured, not assumed: running the gate over its own author's transcript
returned 5 findings of which 3 were true (`Want me to`, `Say the word`) and
the `let me` family were the false-positive class (AGENTS.md, ENFORCEMENT
SURFACES). A CRITICAL-per-instance rule that fires on announcements trains
the reader to ignore it, so this module keeps the two lists separate:

  REQUEST_PATTERNS      violations -- `scan()` returns these
  ANNOUNCEMENT_PATTERNS reported, never counted -- `scan_announcements()`

Both are still detected. The announcement class is visible so it cannot be
silently re-filed as a violation, and it cannot silently disappear either.

Usage:
    python tools/permission_seeking_guard.py --file draft.md
    type draft.md | python tools/permission_seeking_guard.py
Exit 0 = clean, 1 = permission-seeking language present.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

# Each pattern is (name, regex). Patterns are deliberately narrow: AP-5 is a
# CRITICAL-per-instance rule, so a false positive costs real work and trains
# the reader to ignore the gate. Prefer missing a phrase over flagging a
# genuine operator question.
#
# REQUEST_PATTERNS are the violations: each hands the decision back to the
# operator ("Want me to X?", "Say the word and I'll X", "Shall I X?") for an
# action canon already mandates.
REQUEST_PATTERNS: list[tuple[str, str]] = [
    (
        "say_the_word",
        r"\b(?:Say the word|Give the word|At your word|On your (?:word|go|"
        r"signal))\b[^.]{0,120}\b(?:I(?:'ll| will)|I'll)\b",
    ),
    (
        "shall_i",
        r"\bShall I (?=(?:fix|add|run|mint|apply|land|record|commit|edit|"
        r"write|spawn|call|check|verify|do|perform|execute|create|update|"
        r"split|delete|remove|install|generate|build|deploy|test|scan|"
        r"inspect|read)\b)",
    ),
    (
        "want_me_to",
        r"\b(?:Want me to|Would you like me to|Do you want me to|"
        r"Should I(?:'| a)? (?:go ahead|proceed|apply|run|fix|make|write|"
        r"land|mint|record|commit|edit|execute|start))\b",
    ),
    (
        "if_you_want_i_can",
        r"\b(?:If you(?:'d| would) like[,.].{0,60}\bI can|"
        r"Happy to\b[^.]{0,60}\b(?:should I|want me|shall I)\b)",
    ),
]

# ANNOUNCEMENT_PATTERNS are NOT violations: they name the assistant's own
# in-progress action and offer the operator no decision. They are still
# detected -- `scan_announcements()` returns them and the transcript gate
# prints them -- because a class that is merely deleted can be re-added by a
# later edit without anyone noticing. The `let me` family belongs here; see
# the module docstring for the measurement that produced this split.
ANNOUNCEMENT_PATTERNS: list[tuple[str, str]] = [
    (
        "let_me_action",
        r"\bLet me (?=(?:fix|add|run|mint|apply|land|record|commit|edit|"
        r"write|spawn|call|check|verify|do|perform|execute|create|update|"
        r"start|begin|write|save|merge|split|delete|remove|install|"
        r"generate|build|deploy|test|scan|grep|search|inspect|read)\b)",
    ),
]

#: Every pattern, for callers that only need the full name list (e.g. `--allow`
#: validation and the report of what the rulebook covers).
PATTERNS: list[tuple[str, str]] = REQUEST_PATTERNS + ANNOUNCEMENT_PATTERNS

# Quoted spans are excluded. AP-5's canonical counter-example appears verbatim
# in quotation marks AND fenced code blocks across CODEX generators, skills and
# canon; flagging a document that *cites* the anti-pattern would make the guard
# unusable on canon itself. Fences come FIRST in the alternation so a ``` block
# is consumed whole rather than leaving its contents to be scanned.
_QUOTE_PATTERNS = [
    r"```[\s\S]*?```",  # fenced code block
    r"~~~[\s\S]*?~~~",  # alt fence
    r'"[^"\n]*"',
    r"'[^'\n]*'",
    r"`[^`\n]*`",
    r"^\s*>.*$",  # markdown blockquote
]

_QUOTE_RE = re.compile("|".join(_QUOTE_PATTERNS), re.MULTILINE)


@dataclass
class Finding:
    name: str
    line: int
    match: str


def _blank(match_text: str) -> str:
    """Blank a matched span, preserving length AND line structure.

    Blanking with a plain run of spaces preserves character offsets but
    collapses newlines, so a multi-line fenced block merges with the prose
    that follows it and the reported line number becomes the fence opener
    rather than the line that actually matched. Keeping the newlines keeps
    `text.count("\\n")` and every subsequent line number exact.
    """
    return "".join("\n" if ch == "\n" else " " for ch in match_text)


def strip_quoted(text: str) -> str:
    """Replace quoted spans with blanks, preserving length and line numbers."""
    return _QUOTE_RE.sub(lambda m: _blank(m.group(0)), text)


def _match(patterns: list[tuple[str, str]], cleaned: str) -> list[Finding]:
    """Run one pattern list over already-cleaned text, in line order."""
    findings: list[Finding] = []
    for name, pattern in patterns:
        for m in re.finditer(pattern, cleaned, re.IGNORECASE):
            findings.append(
                Finding(
                    name=name,
                    line=cleaned[: m.start()].count("\n") + 1,
                    match=m.group(0),
                )
            )
    findings.sort(key=lambda f: (f.line, f.name))
    return findings


def scan(text: str) -> list[Finding]:
    """Return permission-seeking VIOLATIONS. Quoted spans are ignored.

    Announcements (`let me X`) are deliberately not returned: they are
    reported by `scan_announcements()`. Use `scan_all()` when both classes
    are needed in one pass, so a caller cannot get half the picture by
    accident.
    """
    return _match(REQUEST_PATTERNS, strip_quoted(text))


def scan_announcements(text: str) -> list[Finding]:
    """Return plan ANNOUNCEMENTS (`let me X`) -- reported, never violations."""
    return _match(ANNOUNCEMENT_PATTERNS, strip_quoted(text))


def scan_all(text: str) -> tuple[list[Finding], list[Finding]]:
    """Return (violations, announcements) from ONE quote-stripping pass.

    The single pass matters: `strip_quoted` is what keeps line numbers honest,
    and running it twice on the same text would be two chances to drift.
    """
    cleaned = strip_quoted(text)
    return (_match(REQUEST_PATTERNS, cleaned),
            _match(ANNOUNCEMENT_PATTERNS, cleaned))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", help="path to check; omit to read stdin")
    ap.add_argument(
        "--allow",
        default="",
        help="semicolon-separated finding names to suppress (documented "
        "exceptions only; never a blanket bypass)",
    )
    args = ap.parse_args(argv)

    if args.file:
        try:
            with open(args.file, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            print(
                f"AP-5: cannot read {args.file!r}: {exc}",
                file=sys.stderr,
            )
            return 2
    else:
        text = sys.stdin.read()

    allowed = {n.strip() for n in args.allow.split(";") if n.strip()}
    violations, announcements = scan_all(text)
    findings = [f for f in violations if f.name not in allowed]
    announcements = [f for f in announcements if f.name not in allowed]

    # Announcements are printed on every run, clean or not: a class that is
    # suppressed silently is indistinguishable from a class that stopped being
    # detected, which is how the false-positive reading survived this long.
    if announcements:
        print(
            f"AP-5: {len(announcements)} plan announcement(s) reported, "
            "not counted as violations",
            file=sys.stderr,
        )
        for f in announcements:
            print(f"  line {f.line}: [{f.name}] {f.match!r} (announcement)",
                  file=sys.stderr)

    if not findings:
        print("AP-5: clean")
        return 0

    print(f"AP-5: {len(findings)} permission-seeking construction(s)", file=sys.stderr)
    for f in findings:
        print(f"  line {f.line}: [{f.name}] {f.match!r}", file=sys.stderr)
    print(
        "\nMandated actions are NOT requests (SYSTEM_INSTRUCTIONS.md:2046). "
        "Execute, then report. If the action is genuinely blocked, say what "
        "blocks it - do not offer to do the work.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())