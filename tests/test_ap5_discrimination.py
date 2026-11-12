# coding: utf-8
"""AP-5 must tell a REQUEST from an ANNOUNCEMENT, and must keep telling.

Why this file exists. The AP-5 rulebook used to hold `let me X` in the same
list as `Want me to X?`, so the gate reported its own author's plan
announcements as violations: of 5 findings in one transcript, 3 were true
(`Want me to`, `Say the word`) and the rest were the `let me` false-positive
class (AGENTS.md, ENFORCEMENT SURFACES). A CRITICAL-per-instance rule that
fires on a plan statement trains the reader to ignore the gate.

The discrimination this file pins:

  * a REQUEST hands the decision to the operator for an action canon already
    mandates -- that is the violation;
  * an ANNOUNCEMENT names the assistant's own in-progress action -- no
    decision is offered, so it is reported but never counted.

Two properties are load-bearing and each gets its own control: (1) the
announcement class is still DETECTED, so it cannot be deleted and silently
re-added; (2) the split is what makes the announcement fixture pass -- stub
the split away and the test fails, which is the difference between a test
that reads the classifier and a test that reads the fixture.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import permission_seeking_guard as guard  # noqa: E402
from permission_seeking_guard import (  # noqa: E402
    scan,
    scan_all,
    scan_announcements,
)

# --- fixtures --------------------------------------------------------------
# Requests: the operator is asked to decide. Every one of these is a
# violation regardless of how polite it is.
REQUESTS = [
    "Want me to run the sweep?",
    "Would you like me to mint the three tasks?",
    "Do you want me to delete them?",
    "Should I go ahead and quarantine?",
    "Shall I apply the merge now?",
    "Say the word and I'll mint A, B, and C as separate records.",
    "Give the word and I will land it.",
    "If you'd like, I can run the manifest first.",
]

# Announcements: the assistant states what it is doing next. No decision is
# offered to anyone, so none of these is a violation.
ANNOUNCEMENTS = [
    "Let me fix the predicate before we proceed.",
    "Let me check the config first.",
    "Let me run the tests now.",
    "Let me spawn the three workers in one call.",
]

# One message, both classes. Exactly one violation, exactly one announcement.
BOTH = "Let me check the config. Want me to run the tests after that?"

# Quoted and fenced spans are stripped by `strip_quoted` before matching, so
# neither class may fire inside them. This is the property that keeps canon
# (which quotes the anti-pattern verbatim) scannable.
QUOTED_OR_FENCED = [
    'BAD: "Want me to run it?"',
    "```text\nWant me to run it?\nLet me run it.\n```\n",
    "~~~text\nWant me to run it?\n~~~\n",
    "> Want me to run it?",
]


# --- clause 1: requests are violations ------------------------------------

def test_requests_are_violations():
    for text in REQUESTS:
        assert scan(text), "request not reported: %r" % text


def test_requests_are_not_announcements():
    """The two classes must not overlap: a request is never an announcement."""
    for text in REQUESTS:
        assert scan_announcements(text) == [], text


# --- clause 2: announcements are detected, but are not violations ---------

def test_announcements_are_detected():
    """Deleting the class would let a later edit re-add it unnoticed."""
    for text in ANNOUNCEMENTS:
        hits = scan_announcements(text)
        assert hits, "announcement not detected: %r" % text
        assert hits[0].name == "let_me_action", hits[0]


def test_announcements_are_not_violations():
    for text in ANNOUNCEMENTS:
        assert scan(text) == [], "announcement treated as violation: %r" % text


# --- clause 3: one message can carry both, and the counts stay apart ------

def test_both_classes_in_one_message_yield_one_violation():
    violations, announcements = scan_all(BOTH)
    assert len(violations) == 1, violations
    assert len(announcements) == 1, announcements
    assert violations[0].name == "want_me_to", violations[0]
    assert announcements[0].name == "let_me_action", announcements[0]
    # Line numbers come from the same quote-stripped text, so the two classes
    # cannot drift to different offsets in one pass.
    assert violations[0].line == announcements[0].line == 1


# --- clause 4: the split is load-bearing ---------------------------------

def test_folding_announcements_into_requests_breaks_the_announcement_fixture(
        monkeypatch):
    """Control: re-merge the lists and the announcement fixture becomes a hit.

    Without this, test_announcements_are_not_violations could pass because the
    regex never matched in the first place. Here the SAME fixture must flip.
    """
    monkeypatch.setattr(
        guard, "REQUEST_PATTERNS",
        guard.REQUEST_PATTERNS + guard.ANNOUNCEMENT_PATTERNS,
    )
    assert scan(ANNOUNCEMENTS[0]), "fixture never matched -- control is vacuous"


def test_removing_announcement_patterns_silences_the_class(monkeypatch):
    """Control on the other side: the detection path is this list, not luck."""
    monkeypatch.setattr(guard, "ANNOUNCEMENT_PATTERNS", [])
    assert scan_announcements(ANNOUNCEMENTS[0]) == []
    # And the violation path is untouched by that removal.
    assert scan(REQUESTS[0])


# --- clause 5: fences and quotes still suppress both classes --------------

def test_quoted_and_fenced_spans_are_not_scanned():
    for text in QUOTED_OR_FENCED:
        assert scan(text) == [], text
        assert scan_announcements(text) == [], text


# --- the interface the transcript gate consumes --------------------------

def test_gate_reports_both_classes_with_a_rule_prefix():
    """`_findings_for` is where the split reaches the durable artifact.

    Violations keep the bare rule name (so existing reports stay readable);
    announcements are namespaced `announcement:` so a reader -- or a later
    edit -- cannot mistake one for the other.
    """
    from transcript_ap5_gate import _findings_for

    violations, announcements = _findings_for(BOTH, "ses", "msg", "part", "x")
    assert [f.rule for f in violations] == ["want_me_to"], violations
    assert [f.rule for f in announcements] == ["announcement:let_me_action"], \
        announcements
    assert violations[0].source == "x" and announcements[0].source == "x"
