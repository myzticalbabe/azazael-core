"""Guard: no module may fall back to `Path.home() / "azazael"` for its project root.

MEASURED 2026-10-11 (action registry followups, item [1]). Twenty-two code sites
across ~20 modules defaulted their project root to `Path.home() / "azazael"`. On this
host that names a DIFFERENT, stale checkout from the tree the modules ship in
(`C:/Azazael/azazael`), so with `AZAZAEL_ROOT` unset they put the wrong tree on
`sys.path` and read and wrote the wrong tree's output and data directories.

Every site is repaired. This test is the part that was MISSING: nothing stopped the
next module from reintroducing the same fallback -- which is how it reached 22 sites
in the first place. It is AST-based on purpose, so a COMMENT that quotes the old value
for the record (most repaired modules carry one) is not a violation while real code is.

The needle is checked in both quote styles, because the two `src/AOA` test modules
used single quotes and a double-quote-only sweep silently missed them.

SCOPE WIDENED 2026-10-11: the guard originally scanned `src/` only, and that is exactly
where it was blind -- `scripts/sync_g_drive.py:40` kept a live
`Path.home() / "azazael"` fallback while this file passed green, and that script's whole
job is syncing the repo to G:, so with `AZAZAEL_ROOT` unset it read the wrong tree. A
guard that covers one directory of the tree is not the class closed. Scan roots are now
`src/` + `scripts/`, and then `packages/` as well: MEASURED 2026-10-11, the needle regex
over the whole `packages/` tree returned exactly ONE match --
`packages/azazael-core/src/azazael_core/worms/clusters/METACLUSTER.py:839`, the
`azazael-core` FORK of this same module, whose `export_state` wrote the state file into
the stale checkout. It is repaired and the tree is scanned, so the next one cannot
arrive unnoticed.

Scanning `packages/` does NOT decide registry row D-3 (is that mirror a monorepo import
or an archive): this guard asserts ONE property -- no stale home-relative project root --
and that property holds under either disposition. D-3 keeps its own decision.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Scan roots. `src/` was the original scope; `scripts/` is the GAP that let a live
#: fallback survive at `scripts/sync_g_drive.py:40` while this guard passed. MEASURED
#: 2026-10-11: the class is `src/` + `scripts/` (26 `Path.home()` uses live under
#: scripts/, of which exactly two divide by "azazael" -- one fixed, one excepted).
#:
#: `packages/` was OUT OF SCOPE until the tree was actually measured. It holds the
#: `azazael-core` FORK of this same module, and that fork carried the identical fallback.
#: MEASURED 2026-10-11 with the needle regex over the whole tree: exactly ONE match, at
#: `packages/azazael-core/src/azazael_core/worms/clusters/METACLUSTER.py:839`. It is
#: repaired, so the tree is in scope -- scanned trees are a claim about coverage, and a
#: tree that ships a copy of the code is part of the class.
SCAN_ROOTS = (REPO / "src", REPO / "scripts", REPO / "packages")

#: Directory names holding COPIES or dependencies, never this repo's source. Mirrors the
#: SKIP_DIRS convention already used by `src/ORCHESTRATION/BIBL/bibl_map.py`.
#:
#: WHY THIS IS NEEDED NOW THAT `packages/` IS SCANNED: `rglob` does not respect
#: `.gitignore`, so a `build/lib/` or vendored copy of this module would fail the guard
#: for a site that no longer exists in source -- the guard would be red about a
#: pre-fix artefact. This is a SCOPE statement, never a hiding place: a fallback in real
#: source must never be added here, and `test_every_scan_root_is_actually_searched`
#: counts with the same filter, so a root reduced to copies alone still FAILS.
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", "site-packages",
    ".venv", "venv", ".venv-3.14t", "build", "dist", ".eggs",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})

#: Reviewed exceptions: `<repo-relative posix path>:<line>` -> reason.
#:
#: A NAMED single entry, never a directory skip, so every other file under the scan
#: roots stays covered. An entry here is a reviewable act with a stated reason, and
#: `test_reviewed_exceptions_are_still_real` FAILS when an entry outlives the site it
#: excused -- stale permission is as dangerous as a missing check, because it silently
#: re-authorises the site if the fallback comes back.
_REVIEWED_EXCEPTIONS = {
    "scripts/sna_scale_azazael.py:19": (
        "ROOT = Path.home() / 'azazael' / 'graphify-out' -- reads the "
        "graphify-out/graph.nd*.js shards of an externally-created checkout. "
        "Repointing it changes WHICH shards it reads, which is the author's call, "
        "not a guard's."
    ),
}

#: The stale value, in the shapes the tree actually used: `"azazael"` and `'azazael'`.
_STALE_NAMES = {"azazael"}


def _is_path_home_call(node: ast.AST) -> bool:
    """True only if this node IS the `Path.home()` call itself.

    Deliberately NOT "mentions `Path.home()` somewhere in its subtree". That looser
    needle flagged `Path.home() / ".cache" / "azazael" / "uv"` -- a legitimate cache
    directory -- because the `/ "azazael"` division has `Path.home()` somewhere in its
    left subtree. MEASURED 2026-10-11: it produced exactly two such false positives
    (`src/UVZ.py:60`, `src/META/UVZ.py:60`), which is how a guard gets disabled. The
    defect shape is `Path.home()` divided DIRECTLY by the name, so the sibling operand
    must BE the call, not merely contain it.
    """
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "home"
    )


def stale_root_lines(tree: ast.AST) -> list[int]:
    """Line numbers of `Path.home() / "azazael"` divisions that are in CODE.

    Matches the innermost division of a chained form, so
    `Path.home() / "azazael" / "src"` is caught by its inner BinOp regardless of
    what is appended after it.
    """
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            for a, b in ((node.left, node.right), (node.right, node.left)):
                if (isinstance(a, ast.Constant) and a.value in _STALE_NAMES
                        and _is_path_home_call(b)):
                    hits.append(node.lineno)
    return sorted(set(hits))


def _iter_py(root: Path):
    """Every source `.py` under `root`, in a stable order, minus copies and litter.

    Shared by `_scan` and by the per-root coverage test on purpose: if the coverage test
    counted with a plain `rglob` while `_scan` applied `_SKIP_DIRS`, a root reduced to
    build copies alone would pass coverage while the guard looked at nothing -- the
    exact failure shape this file exists to catch.
    """
    for path in sorted(root.rglob("*.py")):
        # Backup litter is not importable code; it is tracked separately (registry D-3).
        if ".pre_gate_backup" in path.name:
            continue
        # Compare only the parts INSIDE the scan root, so a parent directory that
        # happens to be named `build` cannot silently skip the whole tree.
        if _SKIP_DIRS.intersection(path.relative_to(root).parts[:-1]):
            continue
        yield path


def _scan() -> dict[str, list[int]]:
    """Map `<repo-relative posix path>` -> stale-root line numbers, over SCAN_ROOTS.

    Shared by the guard and by the exception-freshness test, so the two can never
    disagree about what the tree contains.
    """
    found: dict[str, list[int]] = {}
    for root in SCAN_ROOTS:
        # A root that is not there would let this guard pass while looking at
        # nothing -- the same failure shape it exists to catch.
        assert root.is_dir(), "scan root %s does not exist" % root
        for path in _iter_py(root):
            rel = path.relative_to(REPO).as_posix()
            found[rel] = []
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                # A file that cannot be parsed cannot contribute an importable fallback.
                continue
            found[rel] = stale_root_lines(tree)
    return found


def _scan_offenders() -> list[str]:
    """`<path>:<line>` for every stale root, in a stable order."""
    out: list[str] = []
    for rel, lines in sorted(_scan().items()):
        out.extend("%s:%d" % (rel, lineno) for lineno in lines)
    return out


def test_no_module_falls_back_to_a_home_relative_azazael_root() -> None:
    """The whole point of this file: keep the count at zero, across every scan root."""
    scanned = len(_scan())
    assert scanned > 100, (
        "scan found only %d files across %s -- the guard is not looking where it "
        "thinks it is" % (scanned, SCAN_ROOTS)
    )
    unexpected = [o for o in _scan_offenders() if o not in _REVIEWED_EXCEPTIONS]
    assert not unexpected, (
        "stale home-relative project-root fallback reintroduced at: %s\n"
        "Use `CORE.paths.get_project_root_safe()` (honours AZAZAEL_ROOT, never "
        "raises), or in a bootstrap module that cannot import CORE yet, "
        "`Path(__file__).resolve().parents[N]`.\n"
        "If the site legitimately names a DIFFERENT tree, add ONE named entry to "
        "_REVIEWED_EXCEPTIONS with its reason -- never widen the guard."
        % ", ".join(unexpected)
    )


def test_every_scan_root_is_actually_searched() -> None:
    """A root listed in SCAN_ROOTS that yields nothing is a silent gap.

    This is the specific defect that let `scripts/` sit uncovered: the guard looked
    like it covered the tree while it scanned one directory.
    """
    per_root = {root.name: sum(1 for _ in _iter_py(root)) for root in SCAN_ROOTS}
    assert per_root, "SCAN_ROOTS is empty"
    empty = sorted(name for name, count in per_root.items() if count == 0)
    assert not empty, "scan root(s) contributed zero .py files: %s" % empty


def test_reviewed_exceptions_are_still_real() -> None:
    """An exception that matches no site is stale permission, not a kindness."""
    stale = sorted(set(_REVIEWED_EXCEPTIONS) - set(_scan_offenders()))
    assert not stale, (
        "reviewed exception(s) no longer match any site: %s -- the exception "
        "outlived the code it excused; delete the entry" % ", ".join(stale)
    )


def test_the_needle_check_actually_catches_the_needle() -> None:
    """A negative control: the guard must FAIL on the very shape it forbids.

    Without this, a refactor that made `stale_root_lines` return [] for every input
    would leave the guard passing while catching nothing -- a gate that can never
    fail, which is the defect class this sweep exists to remove.
    """
    caught = stale_root_lines(ast.parse('ROOT = str(Path.home() / "azazael")\n'))
    assert caught == [1]

    caught_chained = stale_root_lines(
        ast.parse('sys.path.insert(0, str(Path.home() / "azazael" / "src"))\n'))
    assert caught_chained == [1]

    caught_single = stale_root_lines(
        ast.parse("sys.path.insert(0, str(Path.home() / 'azazael' / 'src'))\n"))
    assert caught_single == [1]

    # And it must NOT fire on the legitimate neighbours, or it will be disabled.
    assert stale_root_lines(ast.parse('D = str(Path.home() / "Desktop")\n')) == []
    assert stale_root_lines(ast.parse('P = str(Path.home() / ".azazael")\n')) == []
    assert stale_root_lines(ast.parse('B = Path(os.environ.get("X", str(Path.home() / "azazael_backup")))\n')) == []

    # The false positives that forced the needle to be tightened from "mentions
    # Path.home()" to "IS Path.home()": a cache dir under home is not a root.
    assert stale_root_lines(
        ast.parse('C = Path.home() / ".cache" / "azazael" / "uv"\n')) == []
    assert stale_root_lines(
        ast.parse('C = str(Path.home() / ".cache" / "azazael" / "uv")\n')) == []


def test_canonical_resolver_exists_and_points_at_this_tree() -> None:
    from CORE.paths import get_project_root_safe

    root = get_project_root_safe()
    assert isinstance(root, Path)
    assert (root / "src" / "CORE").is_dir(), (
        "get_project_root_safe() resolved to %s, which is not a tree containing "
        "src/CORE" % root
    )


def test_canonical_resolver_honours_the_env_override(tmp_path, monkeypatch) -> None:
    """AZAZAEL_ROOT must win -- the repair must not have dropped the override."""
    from CORE.paths import clear_cache, get_project_root_safe

    fake = tmp_path / "fakeroot"
    (fake / "src" / "CORE").mkdir(parents=True)
    monkeypatch.setenv("AZAZAEL_ROOT", str(fake))
    clear_cache()
    try:
        assert get_project_root_safe() == fake
    finally:
        clear_cache()


def test_canonical_resolver_never_raises_on_a_sentinel_failure(monkeypatch) -> None:
    """The fail-open contract: a monitor must not die for a bad root.

    A path that is neither a tree nor an existing directory forces the
    location-derived branch, which is the one that used to be `~/azazael`.
    """
    from CORE.paths import clear_cache, get_project_root_safe

    monkeypatch.delenv("AZAZAEL_ROOT", raising=False)
    clear_cache()
    try:
        root = get_project_root_safe()
    finally:
        clear_cache()
    assert (root / "src" / "CORE").is_dir(), (
        "the fallback branch must return the tree it ships in, not a home-relative "
        "guess; got %s" % root
    )
    assert os.path.basename(str(root)) == "azazael" or str(root).endswith("azazael"), (
        "fallback root %s does not look like this repo" % root
    )
