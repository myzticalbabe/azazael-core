#!/usr/bin/env python3
# coding: utf-8
"""Gate: a rule exists when canon gives it a heading and a body, nothing less.

The failure this file pins is specific and was live: `MANDATES.md` recorded that
0B-B and 0B-D were removed on 2026-09-30, two `Related:` pointers named them
again, and nine corpus statutes cited them. Both checkers asked "does the string
appear in the file?", got yes, and passed. A citation to a rule that was never
written must not be able to reach the corpus through a footnote.

Run:  python -m pytest tests/test_canon_rules.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import canon_rules as C


def test_a_heading_with_a_body_defines_the_rule():
    blocks = C.rule_blocks("## RULE 0B-D: A CONTROL IS NOT IN PLACE\n\n"
                           "Any claim needs all four: exercised, measured, honest, "
                           "uninherited.\n")
    assert C.rule_problem("RULE 0B-D", blocks) is None


def test_a_mention_is_not_a_definition():
    text = ("## RULE 0B-E: NO MISREPRESENTATION\n\nA claim must not exceed its "
            "evidence, ever.\n\n**Related:** RULE 0B-B, RULE 0B-D.\n")
    blocks = C.rule_blocks(text)
    assert "0B-D" not in blocks
    problem = C.rule_problem("RULE 0B-D", blocks, text)
    assert problem and "has no section" in problem
    # The distinction matters: "repealed" and "never existed" are different
    # reports, and only the first is what happened here.
    assert "no section" in C.rule_problem("RULE 0B-D", blocks, text)


def test_a_heading_with_no_body_is_not_a_rule():
    blocks = C.rule_blocks("## RULE 0Z-Z: AN EMPTY LAW\n\n**Related:** RULE 0B.\n")
    problem = C.rule_problem("RULE 0Z-Z", blocks)
    assert problem and "no body" in problem


def test_an_absent_rule_says_it_was_never_there():
    blocks = C.rule_blocks("## RULE 0B: FOLLOW INSTRUCTIONS\n\nLiterally, and "
                           "always, without re-interpretation.\n")
    problem = C.rule_problem("RULE 99X", blocks, "## RULE 0B: x\n")
    assert problem and "is not in MANDATES.md" in problem


def test_a_citation_with_no_rule_in_it_is_refused():
    assert "names no RULE" in (C.rule_problem("a law", {}) or "")


def test_sub_rules_and_dotted_ids_are_parsed():
    blocks = C.rule_blocks(
        "### RULE 0H-A: ANTI-PATTERN VERIFICATION\n\nThe scan runs at runtime, "
        "never on the source text alone, because source lies.\n")
    assert "0H-A" in blocks


def test_the_two_repealed_statutes_are_now_defined_in_canon():
    """The regression pin. Nine verses cite these; if either loses its body the
    corpus is citing a repealed rule and this must fail before the corpus does."""
    blocks = C.load()
    assert blocks, "MANDATES.md defines no rules at all -- the fixture is wrong"
    for rule_id in ("0B-B", "0B-D"):
        assert rule_id in blocks, "RULE %s has no section in MANDATES.md" % rule_id
        assert C.rule_problem("RULE " + rule_id, blocks) is None


def test_every_statute_in_every_corpus_lands_on_a_rule():
    """Whole-tree control: no shipped corpus may cite an unreadable rule."""
    blocks = C.load()
    corpus_paths = [ROOT / "docs/bible/bible.json"]
    corpus_paths += sorted((ROOT / "docs/bible/themes").glob("*.json"))
    corpus_paths += sorted((ROOT / "docs/bible/emotional").glob("*.json"))
    seen = 0

    def walk(node):
        nonlocal seen
        if isinstance(node, dict):
            statute = node.get("statute")
            if isinstance(statute, dict) and isinstance(statute.get("rule"), str):
                seen += 1
                defect = C.rule_problem(statute["rule"], blocks,
                                        C.MANDATES.read_text(encoding="utf-8",
                                                             errors="replace"))
                assert defect is None, "%r: %s" % (statute["rule"], defect)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    import json
    for path in corpus_paths:
        if path.is_file():
            walk(json.loads(path.read_text(encoding="utf-8")))
    assert seen >= 9, "expected the corpus's statutes to be walked; found %d" % seen
