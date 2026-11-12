# coding: utf-8
"""Tests for `tools/install_git_hooks.py` — the hook installer.

Why these tests exist
---------------------
The defect this closes is not "the installer is buggy" but "the hook was never
installed". `.githooks/pre-commit` had existed since 2026-09-28 and
`git config --get core.hooksPath` returned nothing, so git read `.git/hooks/`,
which holds only samples. The installer's job is to make the tracked hook the
one that runs, and its failure mode is the same as the gate's: reporting
INSTALLED while the hook still does not run.

So the tests pin: a fresh repo reports NOT INSTALLED; install makes it
INSTALLED; the setting points at the tracked directory, not a copy; and a
missing tracked hook is a failure, not a silent skip.

Every test runs against a throwaway repository. The installer's whole purpose is
to change the config of the real tree, and a test that exercised it against this
one would mutate this one -- which is the hazard under test.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import install_git_hooks as igh  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(repo), check=True,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout


#: A stand-in for the generator, honouring the one contract the installer relies
#: on: `<python> bible_ship.py --install-hooks <root> --hooks <name>` writes
#: `<root>/.githooks/<name>`. The installer is what is under test here, not the
#: generator, so the stub implements the CONTRACT and nothing else.
_GENERATOR = '''import sys
from pathlib import Path
args = sys.argv[1:]
root = Path(args[args.index("--install-hooks") + 1])
name = args[args.index("--hooks") + 1]
path = root / ".githooks" / name
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
print("  %-14s planted" % name)
'''


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway git repo with a tracked-style .githooks/pre-commit + generator."""
    r = tmp_path / "repo"
    (r / ".githooks").mkdir(parents=True)
    (r / ".githooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n",
                                                encoding="utf-8")
    (r / "tools").mkdir()
    (r / "tools" / "bible_ship.py").write_text(_GENERATOR, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(r), check=True,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    monkeypatch.setattr(igh, "ROOT", r)
    return r


class TestInstall:
    def test_fresh_repo_reports_not_installed(self, repo, capsys):
        assert igh.check() == 1
        assert "NOT INSTALLED" in capsys.readouterr().out

    def test_install_sets_hookspath_and_reports_installed(self, repo, capsys):
        assert igh.install() == 0
        assert _git(repo, "config", "--get", "core.hooksPath").strip() == ".githooks"
        capsys.readouterr()
        assert igh.check() == 0
        assert "INSTALLED" in capsys.readouterr().out

    def test_install_is_idempotent(self, repo):
        assert igh.install() == 0
        assert igh.install() == 0
        assert _git(repo, "config", "--get", "core.hooksPath").strip() == ".githooks"

    def test_install_points_at_tracked_dir_not_a_copy(self, repo):
        """The setting must name .githooks/, so the tracked file is what runs."""
        igh.install()
        assert igh.current_hookspath() == ".githooks"
        # No copy was made into .git/hooks/
        copied = repo / ".git" / "hooks" / "pre-commit"
        assert not copied.exists()

    def test_missing_tracked_hook_fails(self, repo, capsys):
        (repo / ".githooks" / "pre-commit").unlink()
        assert igh.install() == 1
        assert "missing" in capsys.readouterr().err.lower()

    def test_install_plants_the_generated_push_hook(self, repo, capsys):
        """A clone has no pre-push: `.githooks/*` is gitignored. Measured
        2026-11-01, the installer reported INSTALLED while the push hook -- and
        so both push-time gates -- did not exist. Installing must plant it."""
        assert not (repo / ".githooks" / "pre-push").exists()
        assert igh.install() == 0
        assert (repo / ".githooks" / "pre-push").is_file()
        capsys.readouterr()
        assert igh.check() == 0

    def test_install_fails_without_the_generator(self, repo, capsys):
        """No generator means the push gate cannot be wired -- say so, fail."""
        (repo / "tools" / "bible_ship.py").unlink()
        assert igh.install() == 1
        err = capsys.readouterr().err
        assert "bible_ship" in err
        assert not (repo / ".githooks" / "pre-push").exists()

    def test_check_names_the_remedy_for_a_missing_generated_hook(self, repo, capsys):
        igh.check()
        out = capsys.readouterr().out
        assert "NOT INSTALLED" in out
        assert "--hooks pre-push" in out, out


class TestUninstall:
    def test_uninstall_unsets_hookspath(self, repo):
        igh.install()
        assert igh.uninstall() == 0
        assert igh.current_hookspath() == ""

    def test_uninstall_when_unset_is_not_an_error(self, repo):
        assert igh.uninstall() == 0


class TestExecutableBit:
    """`_make_executable` is best-effort: POSIX has an execute bit, Windows
    does not (os.chmod there only toggles the read-only flag), and git for
    Windows ignores the bit anyway when core.filemode is false. The honest
    contract is therefore "set it where it exists, never raise", and the test
    asserts exactly that rather than a bit Windows cannot store."""

    def test_make_executable_is_safe_and_keeps_file_writable(self, repo):
        import os
        import stat
        p = repo / ".githooks" / "pre-commit"
        assert igh._make_executable(p) is True
        mode = os.stat(p).st_mode
        assert mode & stat.S_IWRITE, "must not have made the hook read-only"
        if os.name == "posix":
            assert mode & stat.S_IXUSR


class TestRealRepoHooksDir:
    def test_required_hooks_are_tracked(self):
        """The real repo must ship the hook the installer activates."""
        tracked = subprocess.run(
            ["git", "ls-files", ".githooks/pre-commit"], cwd=str(ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        assert tracked == ".githooks/pre-commit", (
            "the pre-commit hook is not tracked; a fresh clone would have none")
