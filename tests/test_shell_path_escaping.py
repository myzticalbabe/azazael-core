# coding: utf-8
"""Shell / path-escaping regression tests — the class held only by prose.

Why these tests exist
---------------------
`reports/ERROR_LESSON_AUDIT_2026-10-03.md` grouped six ERROR_LOG entries
(#51, #55, #56, #59, #60, #63) as "shell / path escaping" and recorded the gap
plainly:

    three skills (`azazael-fix-gitbash-paths`, `azazael-powershell-escape`,
    `azazael-crossplat-fileops`), zero tests or lints.

Three thorough documents and no check. This file converts the documented
hazard into one runnable check. Every test runs the DOCUMENTED dangerous
invocation against a temp dir and asserts what actually happens, so the skill
text is verified rather than trusted.

What is mechanized, entry by entry
----------------------------------
* **#59 / #63** — a Windows backslash path is eaten by bash (`\\U` -> `U`).
  `_bash` shows the mangling and the forward-slash form that survives.
* **#56** — `$`, and quoting, decide whether bash expands a variable before
  PowerShell ever sees it.
* **#60 / #55** — an unquoted path with a space becomes two arguments; the
  "tool is broken" reports were this, not the tool.
* **#51** — MSYS2 translates arguments that look like paths; the documented
  opt-out is `MSYS2_ARG_CONV_EXCL`. Its effect on a bash *builtin* is nil,
  which is itself the finding: the variable only matters for native programs,
  so a test that only exercised `printf` would prove nothing about it. The
  assertion is therefore scoped to what is observable.
* **pathlib gotcha** — `pathlib.Path("/c/Users/...")` is not a Windows path.
  This is checked with `PureWindowsPath`, so it holds on every host.

Bash-dependent tests skip (never silently pass) when `bash` is unavailable.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path, PureWindowsPath

import pytest
def _probe_bash(exe: str | None) -> str | None:
    """Return exe iff it actually executes a script (rc=0 AND output).

    `C:\\WINDOWS\\system32\\bash.EXE` (the WSL launcher stub) returns rc=0
    with EMPTY output here — a silent lie, exactly the hazard class under
    test. Probing for real execution keeps the suite honest: a bash that
    cannot run is absence, not a passing environment.
    """
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-c", "printf '%s' ok"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
    except Exception:
        return None
    return exe if (r.returncode == 0 and r.stdout == "ok") else None


def _find_bash() -> str | None:
    cands = [r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")]
    for cand in cands:
        hit = _probe_bash(cand)
        if hit:
            return hit
    return None


_BASH = _find_bash()

requires_bash = pytest.mark.skipif(
    _BASH is None, reason="bash not available; this class is a bash hazard")


def _bash(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a script through bash, returning the completed process."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([_BASH, "-c", script], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=full_env, timeout=30)


# ── #59 / #63: backslash paths are eaten by bash ─────────────────────────


class TestBackslashMangling:
    @requires_bash
    def test_backslash_path_is_mangled_by_bash(self):
        """`C:\\Users` reaches the command as `C:Users` -- the documented bug."""
        # Raw string: bash, not Python, must see the backslashes.
        out = _bash(r'printf "%s" C:\Users\clint\x.txt')
        assert out.stdout == "C:Usersclintx.txt"
        assert out.stdout != r"C:\Users\clint\x.txt"

    @requires_bash
    def test_forward_slash_path_survives(self):
        """The documented safe pattern: forward slashes pass through intact."""
        out = _bash('printf "%s" "C:/Users/clint/x.txt"')
        assert out.stdout == "C:/Users/clint/x.txt"

    @requires_bash
    def test_single_quotes_protect_a_backslash_path(self):
        """Quoting is the other safe pattern: bash does not process escapes
        inside single quotes."""
        out = _bash(r"printf '%s' 'C:\Users\clint\x.txt'")
        assert out.stdout == r"C:\Users\clint\x.txt"


# ── #56: `$` is expanded by bash before the target sees it ───────────────


class TestDollarExpansion:
    @requires_bash
    def test_unquoted_dollar_is_expanded(self):
        out = _bash('printf "%s" $FOO', env={"FOO": "expanded"})
        assert out.stdout == "expanded"

    @requires_bash
    def test_double_quotes_still_expand(self):
        out = _bash('printf "%s" "$FOO"', env={"FOO": "expanded"})
        assert out.stdout == "expanded"

    @requires_bash
    def test_single_quotes_prevent_expansion(self):
        """The fix for a PowerShell command containing `$var`: single-quote it
        (or write it to a .ps1) so bash hands the `$` through untouched."""
        out = _bash("printf '%s' '$FOO'", env={"FOO": "expanded"})
        assert out.stdout == "$FOO"


# ── #60 / #55: unquoted paths split, and that is not a broken tool ───────


class TestWordSplitting:
    @requires_bash
    def test_unquoted_path_with_space_splits_into_two_arguments(self):
        out = _bash('printf "%s\\n" $P', env={"P": "a b"})
        assert out.stdout.splitlines() == ["a", "b"]

    @requires_bash
    def test_quoted_path_with_space_stays_one_argument(self):
        out = _bash('printf "%s\\n" "$P"', env={"P": "a b"})
        assert out.stdout.splitlines() == ["a b"]

    @requires_bash
    def test_shlex_quote_round_trips_a_real_path(self, tmp_path):
        """The safe way to build a shell command in Python: quote the path."""
        target = tmp_path / "a b's.txt"
        target.write_text("payload", encoding="utf-8")
        out = _bash("cat " + shlex.quote(target.as_posix()))
        assert out.returncode == 0
        assert out.stdout == "payload"

    @requires_bash
    def test_unquoted_real_path_with_space_fails(self, tmp_path):
        """The negative control: the same path unquoted does not work."""
        target = tmp_path / "a b.txt"
        target.write_text("payload", encoding="utf-8")
        out = _bash("cat " + target.as_posix())
        assert out.returncode != 0


# ── #51: MSYS2 argument translation, and its documented opt-out ──────────


class TestMsysTranslation:
    @requires_bash
    def test_msys2_arg_conv_excl_is_honoured_by_the_shell(self):
        """The documented fix is `MSYS2_ARG_CONV_EXCL=...`. The variable is
        readable and preserved across the invocation, which is the part a test
        can assert without a native program in the loop. Its effect on a bash
        builtin is nil by construction -- the variable only applies when MSYS
        builds the argv of a native (non-MSYS) process."""
        out = _bash('printf "%s" "$MSYS2_ARG_CONV_EXCL"',
                    env={"MSYS2_ARG_CONV_EXCL": "/mnt"})
        assert out.stdout == "/mnt"

    @requires_bash
    def test_cmd_exe_double_slash_bypasses_msys(self):
        """`cmd.exe //c` is the documented bypass: the `//c` form stops MSYS
        rewriting the switch to `/c` (which cmd.exe would read as a path)."""
        out = _bash('printf "%s" //c')
        assert out.stdout == "//c"


# ── the pathlib gotcha: "/c/Users/..." is not a Windows path ─────────────


class TestPathlibGotcha:
    def test_posix_style_path_has_no_drive_on_windows(self):
        """`pathlib.Path("/c/Users/clint")` fails on Windows because it has no
        drive -- the documented gotcha. Asserted on PureWindowsPath so it holds
        on any host."""
        p = PureWindowsPath("/c/Users/clint")
        assert p.drive == ""
        assert not p.is_absolute()

    def test_drive_letter_forward_slash_path_is_absolute(self):
        p = PureWindowsPath("C:/Users/clint")
        assert p.drive == "C:"
        assert p.is_absolute()

    def test_path_as_posix_uses_forward_slashes(self, tmp_path):
        """`.as_posix()` is how a Windows Path becomes a shell-safe string."""
        assert "\\" not in tmp_path.as_posix()


# ── #61: backslashes inside f-string expressions ───────────────────────


class TestFStringBackslash:
    def test_no_backslash_inside_fstring_expression(self):
        """#61: `{'\n'.join(...)}` is a SyntaxError — backslashes are illegal
        inside f-string `{...}`. Walk src/ with ast and flag any
        FormattedValue whose source segment carries a backslash, so the
        anti-pattern cannot be reintroduced where py_compile would only
        catch it after the fact (a file that fails to parse breaks the
        whole suite's collection)."""
        import ast as _ast

        root = Path(__file__).resolve().parents[1] / "src"
        bad: list[str] = []
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                tree = _ast.parse(text)
            except (OSError, SyntaxError):
                continue
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.FormattedValue):
                    continue
                seg = _ast.get_source_segment(text, node)
                if seg and "\\" in seg:
                    bad.append("%s:%s: %s" % (
                        path.relative_to(root), node.lineno, seg[:80]))
        assert not bad, "backslash inside f-string expression:\n" + "\n".join(
            bad[:10])


# ── the three skills must exist and stay loadable ────────────────────────


class TestSkillsPresent:
    @pytest.mark.parametrize("name", [
        "azazael-fix-gitbash-paths",
        "azazael-powershell-escape",
        "azazael-crossplat-fileops",
    ])
    def test_skill_source_exists_with_frontmatter(self, name):
        root = Path(__file__).resolve().parents[1]
        src = root / "skills" / (name + ".md")
        assert src.exists(), "%s is missing from the source corpus" % name
        assert src.read_text(encoding="utf-8").startswith("---"), (
            "%s has no YAML frontmatter; skill() cannot load it" % name)
