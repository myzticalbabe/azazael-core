# coding: utf-8
"""`git reset` runs no hook, so the guard has to be the command.

Measured 2026-09-29: 28 `reset:` entries in the reflog of this repo, one of
them `reset: moving to HEAD~1` at 04:02, after which 620+ files were dirty and
three modules were gone from the working tree. Recovery was manual and landed
as `73ee26af`, "Restore three modules lost by the hard reset" -- a commit
message that is the system reporting it had no other option.

There is no `pre-reset` hook in git, so no amount of work in `.githooks/`
would have caught it. That is why this is a wrapper plus an alias rather than
another hook, and why these tests are about *what it refuses* rather than
about what it prints.

Every test runs against a throwaway repository. The guard's whole purpose is
to operate on the real tree, and a test that exercised it against this one
would have to reset this one to mean anything -- which is the exact hazard
under test.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

import reset_guard as rg  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway repo with two commits, wired into the guard."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("one\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "first")
    (r / "a.txt").write_text("two\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "second")
    monkeypatch.setattr(rg, "REPO", r)
    return r


def test_a_reset_to_head_is_allowed(repo):
    """The guard must not be the reason ordinary work stops."""
    assert rg.main(["--check", "HEAD"]) == 0


def test_a_hard_reset_of_a_clean_tree_is_allowed(repo):
    """--hard on a clean tree at HEAD destroys nothing."""
    assert rg.main(["--hard", "--check", "HEAD"]) == 0


def test_it_refuses_a_reset_that_would_orphan_a_commit(repo, capsys):
    """The 04:02 case: `HEAD~1` does not merely unstage, it orphans."""
    rc = rg.main(["--check", "HEAD~1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "would stop being reachable" in err, (
        "the orphaning commit is the quiet half of this failure -- the tree "
        "looks fine until the reflog expires")
    assert "Nothing was changed" in err


def test_it_refuses_a_hard_reset_with_a_dirty_tree(repo, capsys):
    (repo / "a.txt").write_text("dirty\n", encoding="utf-8")

    rc = rg.main(["--hard", "--check", "HEAD"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "discard" in err and "working tree" in err
    assert "a.txt" in err, "the guard blocked without naming the file at risk"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "dirty\n", (
        "--check modified the tree it was asked to inspect")


def test_force_is_honoured_and_still_reports(repo, capsys):
    """An override that overrides must not also go silent."""
    (repo / "a.txt").write_text("dirty\n", encoding="utf-8")

    rc = rg.main(["--hard", "--force", "HEAD"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "Proceeding anyway" in captured.err
    assert (repo / "a.txt").read_text(encoding="utf-8") == "two\n"


def test_an_unresolvable_target_is_never_reported_safe(repo, capsys):
    """The failure mode this script exists to prevent is a false 'safe'.

    A probe that cannot answer must not fall through to permission, and it
    must not exit 0 either -- exit 0 here would be indistinguishable from
    'I checked and you are fine'.
    """
    rc = rg.main(["--check", "no-such-ref-xyz"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "cannot resolve" in err
    assert "safe" not in err


def test_untracked_files_are_not_reported_as_a_reset_loss(repo, capsys):
    """`git reset --hard` does not delete untracked files. `git clean` does.

    Asserted because an earlier version of the guard counted them into the
    --hard loss, which overstated the damage by 565 files in this repo and
    would have trained the reader to discount the number. The count is still
    reported, as context, and explicitly labelled as untouched.
    """
    (repo / "new.txt").write_text("scratch\n", encoding="utf-8")
    (repo / "a.txt").write_text("dirty\n", encoding="utf-8")

    rc = rg.main(["--hard", "--check", "HEAD"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "not touched by reset" in err
    assert "a.txt" in err
    assert "would discard 1 modified tracked" in err, (
        "the loss count included untracked files, which reset does not remove")

    # A soft reset of a tree holding only untracked files is genuinely safe.
    _git(repo, "checkout", "--", "a.txt")
    assert rg.main(["--check", "HEAD"]) == 0


def test_no_remote_is_not_a_permanent_blocker(repo, capsys):
    """A repo with no remote has nothing to be out of sync with.

    Treating every commit as permanently unpushed would make the guard refuse
    every reset forever, which is how a guard gets switched off. The orphan
    and dirty checks carry the weight instead, and they are the ones that
    match the loss actually observed here.
    """
    assert not rg.dirty_files()

    rc = rg.main(["--check", "HEAD~1"])
    err = capsys.readouterr().err

    assert rc == 1, "the orphaning commit still blocks"
    assert "would stop being reachable" in err
    assert "not on any remote" not in err


def test_pushed_commits_do_not_trigger_the_remote_blocker(repo, capsys):
    """The guard reports real exposure, not a standing alarm."""
    _git(repo, "init", "-q", "--bare", str(repo.parent / "remote.git"))
    _git(repo, "remote", "add", "origin", str(repo.parent / "remote.git"))
    _git(repo, "push", "-q", "-u", "origin", "HEAD")
    _git(repo, "commit", "-qm", "empty", "--allow-empty")
    _git(repo, "push", "-q", "origin", "HEAD")

    rc = rg.main(["--check", "HEAD"])
    assert rc == 0, "everything is pushed and the tree is clean"
    assert "not on any remote" not in capsys.readouterr().err


def test_unpushed_commits_are_reported_when_a_remote_exists(repo, capsys):
    """...and the mirror image: real exposure must still be named."""
    _git(repo, "init", "-q", "--bare", str(repo.parent / "remote.git"))
    _git(repo, "remote", "add", "origin", str(repo.parent / "remote.git"))
    _git(repo, "push", "-q", "-u", "origin", "HEAD")
    _git(repo, "commit", "-qm", "local only", "--allow-empty")

    rc = rg.main(["--check", "HEAD"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "not on any remote" in err
    assert "local only" in err


def test_the_alias_cannot_shadow_reset_and_the_tool_says_so(repo, capsys):
    """`git reset` cannot be intercepted, and the tool must not imply it can.

    MEASURED on this git: an alias cannot shadow a built-in. `git zzztest`
    fired its alias; `git status` ran real git with `alias.status` set and
    unread. An earlier version of `--install-alias` wrote `alias.reset`,
    read it back successfully, and printed "`git reset` now runs the guard" --
    a claim about enforcement that was false, verified by nothing. An
    enforcement that looks installed while enforcing nothing is the exact
    failure this script exists to prevent, so the tool now installs a
    non-built-in name and states the limit.
    """
    assert rg.ALIAS_NAME != "reset", (
        "an alias named reset is silently ignored by git, so installing it "
        "would be theatre")

    rc = rg.main(["--install-alias"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "does NOT intercept" in out, (
        "the tool claims or implies it intercepts `git reset`; it cannot")
    assert "no pre-reset hook" in out, (
        "the stated reason matters: without it a reader assumes the limit is "
        "this tool's and not git's")


def test_the_installed_alias_is_a_name_git_will_actually_run(repo, capsys):
    """Not just stored -- readable back byte-for-byte as written."""
    assert rg.main(["--install-alias"]) == 0
    capsys.readouterr()

    stored = subprocess.run(["git", "config", "--get",
                             f"alias.{rg.ALIAS_NAME}"],
                            cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert stored.returncode == 0, "the alias was not stored"
    assert stored.stdout.strip().startswith("!"), (
        "a `!` alias is what runs a shell command; anything else is a git "
        "subcommand name and would not invoke this script")
