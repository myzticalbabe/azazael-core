# coding: utf-8
"""Tests for `tools/canon_drift_check.py` — the skill corpus/load-path match check.

Why these tests exist
---------------------
`skills/` is the declared source corpus (RULE 0R) and
`.agents/skills/<n>/SKILL.md` is the only path `skill()` loads. `sync_skills.py`
propagates one to the other, but nothing checked that it happened, and the
measured drift was 44 of 1,226 shared skills -- 26 with the load-path copy
missing its YAML frontmatter, so `skill()` could not load them at all.

The check's failure mode is the usual one: reporting OK while the load path is
broken. So the tests pin the three shapes of drift it must catch, and the
reconciliation rule it must apply without discarding text.

Every test builds a throwaway corpus. The check's whole purpose is to read the
real `skills/` tree, and a test that ran it there would depend on that tree's
current contents.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import canon_drift_check as cdc  # noqa: E402


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="")


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    src = tmp_path / "skills"
    load = tmp_path / ".agents" / "skills"
    src.mkdir(parents=True)
    load.mkdir(parents=True)
    monkeypatch.setattr(cdc, "SOURCE_DIR", src)
    monkeypatch.setattr(cdc, "LOAD_DIR", load)
    return src, load


def _pair(src: Path, load: Path, name: str, source: str, loaded: str) -> None:
    _write(src / (name + ".md"), source)
    _write(load / name / "SKILL.md", loaded)


FM = "---\nname: %s\ndescription: d\n---\n\n"
BODY = "# %s\n\nBody text here.\n"


class TestClassify:
    def test_in_sync_reports_no_drift(self, corpus):
        src, load = corpus
        _pair(src, load, "a", FM % "a" + BODY % "a", FM % "a" + BODY % "a")
        rep = cdc.classify()
        assert rep["identical"] == 1
        assert not rep["frontmatter_only"]
        assert not rep["substantive"]
        assert cdc.cmd_check() == 0

    def test_frontmatter_only_drift_detected(self, corpus):
        """Load path lost its YAML header -- skill() cannot load it."""
        src, load = corpus
        _pair(src, load, "a", FM % "a" + BODY % "a", BODY % "a")
        rep = cdc.classify()
        assert rep["frontmatter_only"] == ["a"]
        assert not rep["substantive"]
        assert cdc.cmd_check() == 1

    def test_substantive_drift_detected(self, corpus):
        src, load = corpus
        _pair(src, load, "a", FM % "a" + BODY % "a",
              FM % "a" + "# a\n\nDifferent body.\n")
        rep = cdc.classify()
        assert rep["substantive"] == ["a"]
        assert cdc.cmd_check() == 1

    def test_missing_from_load_detected(self, corpus):
        src, load = corpus
        _write(src / "a.md", FM % "a" + BODY % "a")
        rep = cdc.classify()
        assert rep["missing_in_load"] == ["a"]
        assert cdc.cmd_check() == 1

    def test_stale_in_load_detected(self, corpus):
        src, load = corpus
        _write(load / "a" / "SKILL.md", FM % "a" + BODY % "a")
        rep = cdc.classify()
        assert rep["stale_in_load"] == ["a"]
        assert cdc.cmd_check() == 1

    def test_crlf_is_not_drift(self, corpus):
        """Stored blobs are LF; a CRLF working copy must not read as drift."""
        src, load = corpus
        _write(src / "a.md", (FM % "a" + BODY % "a").replace("\n", "\r\n"))
        _write(load / "a" / "SKILL.md", FM % "a" + BODY % "a")
        assert cdc.classify()["identical"] == 1


class TestReconcileRule:
    def test_frontmatter_restored_from_source(self, corpus):
        src, load = corpus
        _pair(src, load, "a", FM % "a" + BODY % "a", BODY % "a")
        cdc.cmd_fix()
        assert cdc.classify()["identical"] == 1

    def test_richer_source_wins(self, corpus):
        src, load = corpus
        _pair(src, load, "a", FM % "a" + "# a\n\nLong source body text.\n",
              FM % "a" + "# a\n\nShort.\n")
        cdc.cmd_fix()
        assert "Long source body" in (load / "a" / "SKILL.md").read_text(encoding="utf-8")

    def test_richer_load_wins_without_discarding_source_text(self, corpus):
        """When the load path is richer, its extra text must survive -- the
        measured case for azazael-pre-action-gate (+2,344 B)."""
        src, load = corpus
        _pair(src, load, "a", FM % "a" + "# a\n\nShort source.\n",
              FM % "a" + "# a\n\nMuch longer load-path body text here.\n")
        cdc.cmd_fix()
        assert "Much longer load-path body" in (src / "a.md").read_text(encoding="utf-8")

    def test_missing_in_load_is_created(self, corpus):
        src, load = corpus
        _write(src / "a.md", FM % "a" + BODY % "a")
        cdc.cmd_fix()
        assert (load / "a" / "SKILL.md").exists()
        assert cdc.classify()["identical"] == 1

    def test_stale_is_promoted_to_source_not_deleted(self, corpus):
        """A load-path skill with no source would be DELETED by the next
        sync_skills run; promotion keeps the text and the skill."""
        src, load = corpus
        _write(load / "a" / "SKILL.md", FM % "a" + BODY % "a")
        cdc.cmd_fix()
        assert (src / "a.md").exists()
        assert (load / "a" / "SKILL.md").exists()
        assert cdc.classify()["identical"] == 1

    def test_fix_then_check_is_green(self, corpus):
        src, load = corpus
        _pair(src, load, "a", FM % "a" + BODY % "a", BODY % "a")
        _pair(src, load, "b", FM % "b" + "# b\n\nLong.\n", FM % "b" + "# b\n\nx\n")
        _write(load / "c" / "SKILL.md", FM % "c" + BODY % "c")
        cdc.cmd_fix()
        assert cdc.cmd_check() == 0


class TestRealCorpus:
    def test_committed_corpus_and_load_path_agree(self):
        """The live tree must be clean: the drift was closed deliberately and
        the hook gates on it, so a red here means the tree regressed."""
        assert cdc.cmd_check() == 0
