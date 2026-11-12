# coding: utf-8
"""Tests for `tools/index_drift_check.py` -- the stale-index guard.

Why this file exists
--------------------
`tools/preflight_all.py --self-test` reported that this guard is a BLOCKING
check in `.githooks/pre-commit` whose declared control artifact was not in the
tree, so its fail-ability was undemonstrated. This file is that control: the
gate now has something that proves it can fail, rather than an assertion that
it can.

Two layers, on purpose
----------------------
* the DECISION (which paths drift, which override applies) is driven through
  the `_names` seam, so every branch is reachable without touching a repo;
* one REAL end-to-end run against a throwaway git repository, because a guard
  proven only against a fake `git` proves nothing about the real one.

The defect this guards against is not hypothetical: measured 2026-10-11, a
commit landed the text from *before* its own correction, because the file was
edited after `git add` and never re-staged. HEAD then recorded the opposite of
what the author believed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import index_drift_check as guard  # noqa: E402

STAGED = ("diff", "--cached", "--name-only")
UNSTAGED = ("diff", "--name-only")


def _drive(monkeypatch, staged, unstaged):
    """Answer the guard's two git questions with fixed path lists.

    `None` means "git failed", which is a distinct branch from "empty": an
    unrunnable guard must say so rather than report a clean tree.
    """
    answers = {STAGED: staged, UNSTAGED: unstaged}

    def _names(args):
        return answers.get(tuple(args), [])

    monkeypatch.setattr(guard, "_names", _names)


# ── the decision ─────────────────────────────────────────────────────────


def test_a_staged_path_edited_again_is_drift(monkeypatch, capsys):
    _drive(monkeypatch, ["mod.py"], ["mod.py"])
    assert guard.main() == 1
    out = capsys.readouterr().out
    assert "STALE INDEX" in out
    assert "mod.py" in out


def test_staged_and_unstaged_disjoint_sets_are_clean(monkeypatch):
    _drive(monkeypatch, ["a.py", "b.py"], ["c.py"])
    assert guard.main() == 0


def test_nothing_staged_returns_clean_before_asking_the_second_question(
    monkeypatch, capsys
):
    _drive(monkeypatch, [], ["edited.py"])
    assert guard.main() == 0
    assert "Nothing staged" in capsys.readouterr().out


def test_only_the_overlap_drifts(monkeypatch, capsys):
    _drive(monkeypatch, ["drifted.py", "clean.py"], ["drifted.py", "elsewhere.py"])
    assert guard.main() == 1
    out = capsys.readouterr().out
    assert "drifted.py" in out
    assert "clean.py" not in out


def test_the_report_follows_gits_ordering_not_set_ordering(monkeypatch, capsys):
    """Iterating the staged list, not a set, is the reason this test can exist."""
    _drive(monkeypatch, ["z_last.py", "a_first.py"], ["a_first.py", "z_last.py"])
    assert guard.main() == 1
    out = capsys.readouterr().out
    assert out.index("z_last.py") < out.index("a_first.py")


# ── the unrunnable and the overridden ────────────────────────────────────


def test_a_failed_git_read_does_not_crash_the_guard(monkeypatch):
    """`_names` is stubbed here, so nothing prints -- and that is the point.

    The loud report belongs to `_git`/`_names` (asserted below against the real
    parser). This test pins the other half: when git cannot answer, `main`
    returns a verdict instead of raising.
    """
    _drive(monkeypatch, None, ["x.py"])
    assert guard.main() == 0


def test_a_failed_unstaged_read_also_returns_a_verdict(monkeypatch):
    _drive(monkeypatch, ["x.py"], None)
    assert guard.main() == 0


def test_allow_stale_index_downgrades_to_a_loud_warning(monkeypatch, capsys):
    _drive(monkeypatch, ["mod.py"], ["mod.py"])
    monkeypatch.setenv("ALLOW_STALE_INDEX", "1")
    assert guard.main() == 0
    assert "ALLOW_STALE_INDEX=1" in capsys.readouterr().out


def test_the_override_is_the_exact_string_one(monkeypatch):
    """`ALLOW_STALE_INDEX=yes` is not the declared form -- same convention as
    `ALLOW_BULK_COMMIT`, and a looser match would be a silent bypass."""
    _drive(monkeypatch, ["mod.py"], ["mod.py"])
    monkeypatch.setenv("ALLOW_STALE_INDEX", "yes")
    assert guard.main() == 1


# ── the real end-to-end control ──────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, "git %s failed: %s" % (" ".join(args), proc.stderr)
    return proc.stdout


def test_real_repo_detects_drift_and_restaging_clears_it(tmp_path, monkeypatch):
    """The actual mechanism, against a real index.

    Hooks are disabled on the throwaway commit so this test cannot be affected
    by -- or accidentally invoke -- this repository's own pre-commit gate.
    """
    _git(tmp_path, "init", "-q")
    target = tmp_path / "mod.py"
    target.write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "mod.py")
    _git(tmp_path, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "core.hooksPath=", "commit", "-qm", "seed")

    # Stage an edit, then edit again: the index now holds the FIRST edit.
    target.write_text("value = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "mod.py")
    target.write_text("value = 3\n", encoding="utf-8")

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.delenv("ALLOW_STALE_INDEX", raising=False)
    assert guard.main() == 1, "a staged-then-edited path must be drift"

    # Re-staging is the documented fix, and must clear it.
    _git(tmp_path, "add", "mod.py")
    assert guard.main() == 0, "re-staging must clear the drift"


def test_real_repo_with_nothing_staged_is_clean(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert guard.main() == 0


@pytest.mark.parametrize("raw", ["mod.py\n\n   \n", "  mod.py  \n", "\n\n"])
def test_names_drops_blank_lines_and_strips_whitespace(monkeypatch, raw):
    """Driven through `_git`, so the REAL `_names` is under test.

    A blank line becoming a path entry would make the guard report drift on a
    clean tree -- a false red, which is why this asserts the parser rather than
    feeding it an already-parsed list.
    """
    monkeypatch.setattr(guard, "_git", lambda *a: (0, raw))
    parsed = guard._names(list(STAGED))
    assert parsed is not None
    assert "" not in parsed
    assert all(p == p.strip() for p in parsed)
    assert parsed == (["mod.py"] if "mod.py" in raw else [])


def test_names_returns_none_and_is_loud_when_git_fails(monkeypatch, capsys):
    monkeypatch.setattr(guard, "_git", lambda *a: (128, ""))
    assert guard._names(list(STAGED)) is None
    assert "failed" in capsys.readouterr().out
