# coding: utf-8
"""Themed editions: the rule is the citation, and every refusal has a control.

A theme is a *register*, not a second source. The one rule that keeps that true
is: **every themed verse cites at least one verse of the base corpus.** A verse
with no citation is new doctrine in an old voice, and no human review reliably
catches it -- a themed verse reads like scripture whether or not it restates
anything.

So the tests below are mostly negative controls. Each one takes a corpus that
passes, changes exactly one thing, and asserts the specific refusal. A validator
whose controls are not exercised is an oracle with no record (RULE 0B-D), and the
failure mode here is silent: a bad theme renders beautifully and cites nothing.

The shipped themes are checked too, both for validity and for drift against their
rendered markdown, because a stale `.md` is a second, wrong source next to a
correct one.
"""
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "bible_theme.py"
THEMES = REPO / "docs" / "bible" / "themes"


def _load_tool():
    """Import ``tools/bible_theme.py`` by path (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("bible_theme_under_test", TOOL)
    assert spec and spec.loader, "bible_theme.py could not be loaded from %s" % TOOL
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


theme_tool = _load_tool()
BASE = theme_tool.load_base()


# --------------------------------------------------------------------------- #
# Fixtures: a corpus that PASSES, so each control changes one thing only
# --------------------------------------------------------------------------- #

def _verse(number=1, **overrides):
    verse = {"number": number, "text": "And the reading was made.", "cites": ["genesis 1:1"]}
    verse.update(overrides)
    return verse


def _corpus(verses=None, **overrides):
    data = {
        "schema": "azazael/bible-theme@1",
        "title": "The Book of the Test",
        "theme": "test",
        "register": "a test register",
        "source_corpus": "docs/bible/bible.json",
        "status": "seed",
        "updated": "2026-11-02",
        "books": [{
            "id": "awakening",
            "name": "Awakening",
            "subtitle": "The Book of the Test",
            "genre": "etiology",
            "introduction": {"paragraphs": ["A paragraph."],
                             "sources": ["docs/bible/bible.json"]},
            "chapters": [{"number": 1, "verses": verses if verses is not None else [_verse()]}],
        }],
    }
    data.update(overrides)
    return data


def _problems(data):
    return theme_tool.validate(data, BASE)["problems"]


def test_the_fixture_itself_passes():
    """Without this, every refusal below could be a refusal of something else."""
    assert _problems(_corpus()) == []


# --------------------------------------------------------------------------- #
# The rule: a theme adds no doctrine
# --------------------------------------------------------------------------- #

def test_a_verse_with_no_citation_is_refused():
    reason = " ".join(_problems(_corpus(verses=[_verse(cites=[])])))
    assert "cites nothing" in reason, reason


def test_a_missing_cites_field_is_refused():
    verse = _verse()
    del verse["cites"]
    assert "cites nothing" in " ".join(_problems(_corpus(verses=[verse])))


def test_a_citation_to_a_verse_the_corpus_does_not_have_is_refused():
    reason = " ".join(_problems(_corpus(verses=[_verse(cites=["genesis 9:9"])])))
    assert "genesis 9:9" in reason and "not a verse of the base corpus" in reason


def test_a_citation_to_another_themes_book_is_refused():
    """The base corpus is the authority. A theme citing itself proves nothing."""
    reason = " ".join(_problems(_corpus(verses=[_verse(cites=["awakening 1:1"])])))
    assert "not a verse of the base corpus" in reason, reason


def test_a_malformed_citation_is_refused():
    reason = " ".join(_problems(_corpus(verses=[_verse(cites=["the book of logic"])])))
    assert "must read" in reason, reason


# --------------------------------------------------------------------------- #
# Numbering, so a themed reference is as stable as a corpus one
# --------------------------------------------------------------------------- #

def test_a_gap_in_verse_numbering_is_refused():
    verses = [_verse(1), _verse(3)]
    reason = " ".join(_problems(_corpus(verses=verses)))
    assert "verses must run 1..n" in reason, reason


def test_a_gap_in_chapter_numbering_is_refused():
    data = _corpus()
    data["books"][0]["chapters"][0]["number"] = 2
    assert "chapters must run 1..n" in " ".join(_problems(data))


def test_a_duplicate_book_id_is_refused():
    data = _corpus()
    data["books"].append(json.loads(json.dumps(data["books"][0])))
    assert "used twice" in " ".join(_problems(data))


def test_exceeding_the_declared_book_cap_is_refused():
    data = _corpus(limits={"max_books": 1})
    data["books"].append(json.loads(json.dumps(data["books"][0])))
    data["books"][1]["id"] = "second"
    assert "exceeds the declared cap" in " ".join(_problems(data))


# --------------------------------------------------------------------------- #
# Headings are labels, not claims (the RULE 0B-D rule, carried over)
# --------------------------------------------------------------------------- #

def test_a_heading_carrying_a_figure_is_refused():
    data = _corpus()
    data["books"][0]["chapters"][0]["sections"] = [
        {"before_verse": 1, "heading": "The 3 disciplines"}]
    reason = " ".join(_problems(data))
    assert "carries a figure" in reason, reason


def test_a_heading_before_a_missing_verse_is_refused():
    data = _corpus()
    data["books"][0]["chapters"][0]["sections"] = [
        {"before_verse": 4, "heading": "Away from anywhere"}]
    assert "does not exist" in " ".join(_problems(data))


def test_out_of_order_sections_are_refused():
    verses = [_verse(1), _verse(2)]
    data = _corpus(verses=verses)
    data["books"][0]["chapters"][0]["sections"] = [
        {"before_verse": 2, "heading": "Second"}, {"before_verse": 1, "heading": "First"}]
    assert "out of order" in " ".join(_problems(data))


# --------------------------------------------------------------------------- #
# Statutes: a law with nothing executing it is a proverb here too
# --------------------------------------------------------------------------- #

def test_a_statute_naming_an_unknown_rule_is_refused():
    verse = _verse(statute={"rule": "RULE 0ZZZ", "check": "python tools/preflight_all.py"})
    assert "is not in MANDATES.md" in " ".join(_problems(_corpus(verses=[verse])))


def test_a_rule_themed_canon_only_mentions_is_refused(tmp_path):
    """`RULE 0B-D` inside a `Related:` pointer satisfied the old regex."""
    (tmp_path / "MANDATES.md").write_text(
        "## RULE 0B-E: NO MISREPRESENTATION\n\nA claim must not exceed its "
        "evidence.\n\n**Related:** RULE 0B-D.\n", encoding="utf-8")
    blocks, text = theme_tool._canon_rules(tmp_path)
    assert "0B-D" not in blocks
    problem = theme_tool.canon_rules.rule_problem("RULE 0B-D", blocks, text)
    assert problem and "has no section" in problem


def test_a_statute_whose_check_names_no_path_is_refused():
    verse = _verse(statute={"rule": "RULE 0B-D", "check": "python tools/gone.py --check"})
    assert "names no path" in " ".join(_problems(_corpus(verses=[verse])))


def test_a_statute_with_a_real_rule_and_check_passes():
    verse = _verse(statute={"rule": "RULE 0B-D",
                            "check": "python tools/preflight_all.py --self-test"})
    assert _problems(_corpus(verses=[verse])) == []


# --------------------------------------------------------------------------- #
# Notes and glossary
# --------------------------------------------------------------------------- #

def test_an_annotation_of_an_unknown_kind_is_refused():
    verse = _verse(annotations=[{"kind": "vibes", "note": "A note."}])
    assert "is not one of" in " ".join(_problems(_corpus(verses=[verse])))


def test_an_annotation_source_that_does_not_exist_is_refused():
    verse = _verse(annotations=[{"kind": "editorial", "note": "A note.",
                                "source": "docs/no_such_file.md"}])
    assert "does not exist in this repository" in " ".join(_problems(_corpus(verses=[verse])))


def test_a_glossary_see_pointing_nowhere_is_refused():
    data = _corpus(glossary=[{"term": "cthia", "gloss": "A gloss.", "see": "awakening 1:9"}])
    assert "not a verse of this theme" in " ".join(_problems(data))


def test_a_glossary_term_defined_twice_is_refused():
    data = _corpus(glossary=[{"term": "cthia", "gloss": "One."},
                             {"term": "Cthia", "gloss": "Two."}])
    assert "defined twice" in " ".join(_problems(data))


def test_an_introduction_without_sources_is_refused():
    data = _corpus()
    data["books"][0]["introduction"] = {"paragraphs": ["A paragraph."], "sources": []}
    assert "must name its sources" in " ".join(_problems(data))


def test_a_wrong_schema_is_refused():
    assert "schema must be" in " ".join(_problems(_corpus(schema="azazael/bible@1")))


def test_an_email_address_is_refused():
    assert "email" in " ".join(_problems(_corpus(verses=[_verse(text="write to a@b.com")])))


# --------------------------------------------------------------------------- #
# Rendering and the CLI
# --------------------------------------------------------------------------- #

def test_rendering_is_deterministic_and_carries_the_citation():
    data = _corpus()
    first = theme_tool.render_theme(data, "test")
    assert first == theme_tool.render_theme(data, "test")
    assert "corpus genesis 1:1" in first, first


def test_the_shipped_themes_validate():
    names = theme_tool.theme_names()
    assert names, "no themes found in %s" % THEMES
    for name in names:
        _path, data = theme_tool.load_theme(name)
        problems = theme_tool.validate(data, BASE)["problems"]
        assert problems == [], "%s: %s" % (name, problems)


def test_the_shipped_themes_are_rendered():
    """A stale .md is a second source, and it is the one a human reads."""
    for name in theme_tool.theme_names():
        _path, data = theme_tool.load_theme(name)
        assert theme_tool.drift_message(name, data) is None, name


def test_the_cli_exits_zero_on_the_shipped_themes():
    assert theme_tool.main(["--all", "--check"]) == 0


def test_the_cli_exits_one_on_a_theme_with_a_defect(tmp_path, monkeypatch):
    """The exit code is the control: FAIL printed with rc 0 is a false green."""
    bad = _corpus(verses=[_verse(cites=[])])
    (tmp_path / "broken.json").write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(theme_tool, "THEMES", tmp_path)
    assert theme_tool.main(["--all"]) == 1


def test_the_cli_reports_an_unreadable_base_corpus_as_unusable(tmp_path):
    with pytest.raises(theme_tool.ThemeError):
        theme_tool.load_base(tmp_path / "absent.json")
