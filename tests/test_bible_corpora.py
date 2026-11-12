#!/usr/bin/env python3
# coding: utf-8
"""Gate: the sibling emotional corpora, and the two lies that are theirs.

`tests/test_bible_check.py` tests the checker. This file tests the *second and
third corpora* against it, because a validator that has only ever been pointed
at one document has not been shown to accept a second one -- and in fact could
not, until `edition_path()` and the corpus-aware drift check existed.

The two negative controls are the failures this subject invites:

* a figure quoted from a study, in prose, with no path behind it. The whole
  reason `SOURCES.md` exists is that this is the natural way to write about
  research and the one way the corpus forbids;
* a citation that points into the *other* corpus. The two are read separately,
  so a cross-corpus reference is a broken reference even when the target verse
  is real and the sentence is true.

A control that has never fired is unknown rather than working. Both are
exercised here, on a copy, so the shipped corpora stay clean.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import bible_check as B

EMOTIONAL = ROOT / "docs" / "bible" / "emotional" / "emotional.json"
FEMALE = ROOT / "docs" / "bible" / "emotional" / "female.json"
CORPORA = (EMOTIONAL, FEMALE)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", CORPORA, ids=lambda p: p.stem)
def test_corpus_is_clean(path: Path) -> None:
    """Each sibling corpus validates, and its own generated edition is current.

    Deliberately not `B.check()`: that also re-derives the genealogy from `src/`,
    which belongs to every lane at once. A corpus gate that goes red because a
    different lane edited an unrelated module teaches people to ignore it.
    """
    assert path.exists(), "the corpus is missing: %s" % path
    data = _load(path)
    B.validate(data, ROOT)
    edition = B.edition_path(path)
    assert edition.exists(), "regenerate it: %s" % edition
    assert edition.read_text(encoding="utf-8") == B.render_edition(data, B._label(path))


@pytest.mark.parametrize("path", CORPORA, ids=lambda p: p.stem)
def test_a_figure_with_no_command_is_refused(path: Path) -> None:
    """The digit-free rule, exercised: the corpus's characteristic lie."""
    data = _load(path)
    data["books"][0]["chapters"][0]["verses"][0]["text"] = (
        "A study found that 30 percent of the sample reported it.")
    with pytest.raises(B.CorpusError, match="a number needs `source`"):
        B.validate(data, ROOT)


def test_a_citation_into_the_other_corpus_is_refused() -> None:
    """Cross-corpus references break, even when the target verse exists there."""
    data = _load(FEMALE)
    data["books"][0]["chapters"][0]["verses"][0]["cites"] = ["gate 1:1"]
    with pytest.raises(B.CorpusError, match="unknown book"):
        B.validate(data, ROOT)


def test_edition_path_belongs_to_its_corpus() -> None:
    """One generated file per corpus; the canon keeps its historical name."""
    assert B.edition_path(B.CORPUS).name == "BOOK_OF_THE_TREE.md"
    assert B.edition_path(EMOTIONAL).name == "EMOTIONAL_BIBLE.md"
    assert B.edition_path(FEMALE).name == "FEMALE_BIBLE.md"
