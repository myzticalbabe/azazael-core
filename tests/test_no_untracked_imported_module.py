"""Guard: no TRACKED python module may import an UNTRACKED one.

MEASURED 2026-10-11 (wave 4 verification). Commit `b0d3f2b4` shipped three tracked
files -- `tools/evolution_progress.py`, `tools/fitness_arm_liveness.py` and
`tools/render_ouroboros.py` -- that import `tools/evolution_log.py` at module level,
while `evolution_log.py` was never tracked. A fresh clone therefore raised
`ImportError` on all three consumers: the consumer was in git and the supplier
existed on one disk only.

`evolution_log.py` is tracked now (commit `95c5371d`), so the live tree passes. This
test is the part that was MISSING -- nothing stopped the next commit from shipping a
consumer whose supplier is untracked, which is precisely how it happened the first
time. The same shape sits one level up in the gate itself: `.githooks` is gitignored,
so the pre-commit chain runs only in this machine's working tree.

Absolute imports are collected from EVERY scope, not just module level: a consumer
that imports the supplier inside a function still cannot run without it. Dotted
`from a.b import c` records `a`, because `a` is the module that must exist. Relative
imports are ignored -- they cannot name a top-level `tools/` file.

The needle is checked in both directions. A detector that reported every module, or
one that reported none, would leave this guard green while catching nothing, and a
gate that can never fail is the defect class this file exists to prevent.
"""
from __future__ import annotations

import ast
import subprocess
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: git must never hang a test run; the suite documents files that hung past 90s.
_GIT_TIMEOUT = 60


def _git_lines(*args: str) -> tuple[list[str] | None, str]:
    """Run git in the repo root. Returns `(lines, why)`, with `lines` None on failure.

    An explicit `(None, reason)` rather than a bare `None` so the caller can SKIP
    with the real cause. Returning a default that looks like data, or swallowing the
    error, would make this helper the same defect it is checking for.
    """
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)
    if done.returncode != 0:
        tail = (done.stderr or "").strip().splitlines()
        return None, "git exit %d%s" % (
            done.returncode, (": " + tail[-1]) if tail else "",
        )
    return [line for line in (done.stdout or "").splitlines() if line.strip()], "ok"


def _untracked_tool_modules(untracked_paths: Iterable[str]) -> set[str]:
    """Module names of untracked `.py` files at the TOP level of `tools/`.

    Top level only: `tools/nested/deep.py` is not reachable as `deep` by the
    consumers this guard protects, because they put `tools/` itself on `sys.path`
    (that is exactly what `evolution_progress.py` does). Backslashes are normalised
    so a Windows-form path from any source is still recognised.
    """
    names: set[str] = set()
    for raw in untracked_paths:
        parts = PurePosixPath(str(raw).replace("\\", "/")).parts
        if len(parts) == 2 and parts[0] == "tools" and parts[1].endswith(".py"):
            names.add(parts[1][: -len(".py")])
    return names


def _imported_module_names(paths: Iterable[Path]) -> dict[str, set[str]]:
    """Map imported module name -> the files that import it.

    Every scope is walked, so a lazy function-level import is still a dependency.
    An unparseable file contributes nothing: it cannot import anything that runs.
    """
    found: dict[str, set[str]] = {}
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            # Not importable code, so it cannot be a consumer of the supplier.
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue  # a relative import cannot name a top-level module
                if node.module:
                    names = [node.module.split(".")[0]]
            for name in names:
                found.setdefault(name, set()).add(str(path))
    return found


def _violations(
    untracked_modules: Iterable[str],
    importers: dict[str, set[str]],
) -> list[tuple[str, str]]:
    """`(importer_file, untracked_module)` pairs, sorted -- the shape of the defect."""
    out: list[tuple[str, str]] = []
    for module in sorted(set(untracked_modules)):
        for importer in sorted(importers.get(module, ())):
            out.append((importer, module))
    return out


def test_no_tracked_module_imports_an_untracked_one() -> None:
    """The whole point of this file: a supplier outside git is still inside an import."""
    tracked, why_tracked = _git_lines("ls-files")
    untracked, why_untracked = _git_lines(
        "ls-files", "--others", "--exclude-standard",
    )
    if tracked is None or untracked is None:
        pytest.skip(
            "git cannot answer in %s (%s / %s); this guard needs a work tree"
            % (ROOT, why_tracked, why_untracked)
        )

    untracked_modules = _untracked_tool_modules(untracked)
    tracked_py = [ROOT / name for name in tracked if name.endswith(".py")]
    existing = [path for path in tracked_py if path.is_file()]
    importers = _imported_module_names(existing)

    assert len(existing) > 100, (
        "scan found only %d tracked .py files under %s -- the guard is not looking "
        "where it thinks it is" % (len(existing), ROOT)
    )

    bad = _violations(untracked_modules, importers)
    assert not bad, (
        "tracked module(s) import an UNTRACKED module -- a fresh clone raises "
        "ImportError on the consumer:\n  %s\n"
        "Fix by landing the supplier (`git add -f <file>`; state/ and .githooks are "
        "gitignored, tools/ is not) or by dropping the import."
        % "\n  ".join("%s imports %r" % (importer, module) for importer, module in bad)
    )


def test_untracked_tool_module_names_are_extracted() -> None:
    """Only top-level `tools/*.py` counts, because only that is importable as a bare name."""
    got = _untracked_tool_modules([
        "tools/evolution_log.py",
        "tools\\windows_form.py",
        "tools/nested/deep.py",
        "tools/style_samples/KLEIN4B_Fairy.json",
        "tools/README.md",
        "src/CORE/thing.py",
        "tests/test_thing.py",
    ])
    assert got == {"evolution_log", "windows_form"}, got


def test_import_collection_reads_real_source(tmp_path) -> None:
    """The AST half must actually see imports, or the live check is vacuous.

    Includes a FUNCTION-level import and a dotted `from a.b import c`, because both
    shapes appear in the real consumers and a module-level-only, first-component-blind
    collector would miss them.
    """
    source = tmp_path / "consumer.py"
    source.write_text(
        "import os\n"
        "import evolution_log\n"
        "from evolution_log import RE_BEST\n"
        "from CORE.paths import get_project_root_safe\n"
        "from . import sibling\n"
        "def build():\n"
        "    import inner_mod\n"
        "    return inner_mod, get_project_root_safe, RE_BEST, os, sibling\n",
        encoding="utf-8",
    )
    got = _imported_module_names([source])
    assert "evolution_log" in got, "a plain import was not collected"
    assert "os" in got
    assert "CORE" in got, "a dotted ImportFrom must record its first component"
    assert "inner_mod" in got, "a function-level import is still a dependency"
    assert "sibling" not in got, "a relative import cannot name a tools/ module"


def test_the_needle_check_actually_catches_the_needle() -> None:
    """Negative controls: the detector must fire on the defect and stay quiet otherwise.

    The first pair is the exact historical shape; the last two are the inverse
    controls, so a detector that reported everything cannot pass. MEASURED
    2026-10-11: the live tree has no such pair, which is why the guard needs a
    synthetic one to prove it can fail at all.
    """
    historical = {"evolution_log": {"tools/evolution_progress.py"}}
    assert _violations({"evolution_log"}, historical) == [
        ("tools/evolution_progress.py", "evolution_log"),
    ]

    # Several consumers of one supplier, and two suppliers at once: all reported.
    many = {
        "evolution_log": {
            "tools/evolution_progress.py",
            "tools/fitness_arm_liveness.py",
            "tools/render_ouroboros.py",
        },
        "other_untracked": {"tools/whatever.py"},
    }
    got = _violations({"evolution_log", "other_untracked"}, many)
    assert len(got) == 4, got
    assert ("tools/render_ouroboros.py", "evolution_log") in got
    assert ("tools/whatever.py", "other_untracked") in got

    # INVERSE: an untracked module nobody imports is not a violation.
    assert _violations({"never_imported"}, historical) == []
    # INVERSE: nothing untracked means nothing to report, however many importers exist.
    assert _violations(set(), many) == []
    # INVERSE: a tracked module is never reported, even when imported everywhere.
    assert _violations({"evolution_log"}, {"os": {"tools/a.py", "tools/b.py"}}) == []
