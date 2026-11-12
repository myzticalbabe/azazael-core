# coding: utf-8
"""The no-LLM graph refresh must resolve a graphify install it can actually use.

Why this is a test and not a comment
------------------------------------
`scripts/graph_refresh_no_llm.py` hard-coded its interpreter to the pip install
in the system Python 3.14. That was the right instinct about the venv (the 3.14t
venv has no graphify, so `sys.executable -m graphify` reaches nothing) and the
wrong conclusion about the target. Measured 2026-09-22:

    ~/.local/bin/graphify.exe          0.9.50  exporters/html.py present, PATCHED
    Python314 site-packages (pip)      0.8.8   no exporters package at all
    ~/.claude/skills/graphify (skill)  0.9.55

The hard-coded value was the middle row -- the *only* install whose layout
cannot carry `scripts/graphify_node_limit_patch.py`, because that patch rewrites
`exporters/html.py` and 0.8.8 is a flat package with no `exporters/` at all. And
`graphify_huge_mode.py` is read FROM that file. So the repo's documented refresh
path ran an engine its own renderer does not target, and the huge-mode renderer
was unreachable from it.

What it catches:

* the resolver returning an install without `exporters/html.py` -- the defect
  itself, and the one that produces a wrong artifact rather than an error;
* a silent fallback when nothing qualifies: refusing is the point, because the
  alternative is writing a 688k-node page through the small-graph code path;
* a candidate walk that reports success while finding nothing;
* a patched install losing to an unpatched one (the patch is the reason to
  prefer one install over another; version alone is not).

The instruments here are stubbed rather than probed, so these are unit tests of
the resolution ORDER and the refusal, not of the host's disk. One test does read
the real host, to prove the walk is not vacuous where it runs.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "tools", _ROOT / "scripts", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

#: The implementation lives in one module now; both entry points call it. The
#: tests target the implementation, not a re-export, so patching the module
#: under test cannot silently patch a copy that nothing calls.
import graphify_interpreter as g  # noqa: E402
import graph_refresh_no_llm as refresh  # noqa: E402


def test_both_entry_points_share_one_implementation():
    """A re-export must BE the implementation, not a second copy of it.

    Error #208 was one hard-coded interpreter; the same class of bug arrived
    twice because two call sites each decided for themselves. This asserts the
    identity, so a future "just inline it here" lands as a failure.
    """
    assert refresh.resolve_graphify_python is g.resolve_graphify_python
    assert refresh.HUGEMODE_MARKER == g.HUGEMODE_MARKER
    assert refresh.REQUIRED_INSTALL_PARTS == g.REQUIRED_INSTALL_PARTS


def _graphify_exec_lists(src: str) -> list[int]:
    """Line numbers of `[...sys.executable..., "graphify"...]` literals.

    Read from the AST, not from the text. The first version of this check was a
    substring search and it failed on its OWN subject's comment, which quotes
    the bad pattern while explaining it -- the same prose-vs-code confusion the
    isolation-profile test in `test_aggregate_suite_gate.py` had to solve the
    same way.
    """
    bad: list[int] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            continue
        uses_sys_executable = any(
            isinstance(e, ast.Attribute) and e.attr == "executable"
            and isinstance(e.value, ast.Name) and e.value.id == "sys"
            for e in node.elts)
        names_graphify = any(
            isinstance(e, ast.Constant) and e.value == "graphify"
            for e in node.elts)
        if uses_sys_executable and names_graphify:
            bad.append(getattr(node, "lineno", 0))
    return bad


def test_graph_rebuild_resolves_instead_of_using_bare_sys_executable():
    """`tools/graph_rebuild.py` ran `sys.executable -m graphify` -- a venv with
    no graphify at all -- so its rounds and cluster pass could only ever fail.

    Both halves are asserted because either alone is satisfiable by the broken
    code: the probe must be gone AND the resolver must be the thing that runs.
    """
    import graph_rebuild

    src = (_ROOT / "tools" / "graph_rebuild.py").read_text(
        encoding="utf-8", errors="replace")
    assert _graphify_exec_lists(src) == [], (
        "bare sys.executable graphify invocation still present at %s"
        % _graphify_exec_lists(src))
    assert callable(graph_rebuild.graphify_cmd), "no graphify_cmd helper"

    # And the helper must refuse, not substitute an interpreter of its own.
    graph_rebuild._GRAPHIFY_PY_RESOLVED = True
    graph_rebuild.GRAPHIFY_PY = None
    try:
        graph_rebuild.graphify_cmd(("update", "."), report=lambda _m: None)
        raise AssertionError("graphify_cmd fell back instead of refusing")
    except RuntimeError as exc:
        assert "exporters/html.py" in str(exc), exc
    finally:
        graph_rebuild._GRAPHIFY_PY_RESOLVED = False

    # Positive control: a resolved interpreter yields a well-formed command.
    graph_rebuild._GRAPHIFY_PY_RESOLVED = True
    graph_rebuild.GRAPHIFY_PY = Path(sys.executable)
    try:
        cmd = graph_rebuild.graphify_cmd(("update", "."), report=lambda _m: None)
        assert cmd[1:] == ["-m", "graphify", "update", "."], cmd
    finally:
        graph_rebuild._GRAPHIFY_PY_RESOLVED = False


def _fake_install(tmp_path: Path, name: str, *, patched: bool) -> Path:
    """A graphify install directory shaped like a real 0.9.x one."""
    install = tmp_path / name / "graphify"
    (install / "exporters").mkdir(parents=True)
    body = "def to_html():\n    return 1\n"
    if patched:
        body = "%s = 5000\n%s" % (g.HUGEMODE_MARKER, body)
    (install / "exporters" / "html.py").write_text(body, encoding="utf-8")
    (install / "cli.py").write_text("# cli\n", encoding="utf-8")
    return install


def _fake_python(tmp_path: Path, name: str) -> Path:
    py = tmp_path / ("%s-python.exe" % name)
    py.write_text("", encoding="utf-8")
    return py


def _stub(monkeypatch, installs: dict[Path, tuple[Path, str]]) -> None:
    """Make the resolver see `installs` as the whole world."""
    monkeypatch.setattr(g, "graphify_python_candidates",
                        lambda: list(installs))
    monkeypatch.setattr(g, "_graphify_install_dir",
                        lambda py: installs.get(py, (None, "?"))[0])
    monkeypatch.setattr(g, "_graphify_version",
                        lambda py: installs.get(py, (None, "?"))[1])


def test_an_install_without_the_exporters_package_is_never_selected(tmp_path, monkeypatch):
    """The 0.8.8 shape: flat package, no `exporters/`. Must be skipped."""
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "cli.py").write_text("# flat layout only\n", encoding="utf-8")
    good = _fake_install(tmp_path, "good", patched=True)
    py_bad = _fake_python(tmp_path, "bad")
    py_good = _fake_python(tmp_path, "good")
    _stub(monkeypatch, {py_bad: (flat, "0.8.8"), py_good: (good, "0.9.50")})

    notes: list[str] = []
    chosen = g.resolve_graphify_python(report=notes.append)

    assert chosen == py_good, "the un-patchable install was selected"
    assert any("no exporters" in n for n in notes), notes


def test_it_refuses_instead_of_falling_back_when_nothing_qualifies(tmp_path, monkeypatch):
    """No capable install means None. A fallback would write the wrong artifact."""
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "cli.py").write_text("# flat\n", encoding="utf-8")
    py = _fake_python(tmp_path, "only")
    _stub(monkeypatch, {py: (flat, "0.8.8")})

    assert g.resolve_graphify_python(report=lambda _m: None) is None


def test_a_patched_install_beats_a_newer_unpatched_one(tmp_path, monkeypatch):
    """Preference is capability, not version: the patch is what huge mode needs."""
    patched = _fake_install(tmp_path, "patched", patched=True)
    unpatched = _fake_install(tmp_path, "unpatched", patched=False)
    py_patched = _fake_python(tmp_path, "patched")
    py_new = _fake_python(tmp_path, "new")
    # The unpatched one is NEWER, and must still lose.
    _stub(monkeypatch, {py_patched: (patched, "0.9.50"),
                        py_new: (unpatched, "0.9.65")})

    notes: list[str] = []
    chosen = g.resolve_graphify_python(report=notes.append)

    assert chosen == py_patched, (chosen, notes)
    assert any("patched" in n for n in notes), notes


def test_an_unpatched_only_world_warns_instead_of_proceeding_quietly(tmp_path, monkeypatch):
    """Capable-but-unpatched is usable and must say so, by name."""
    install = _fake_install(tmp_path, "unpatched", patched=False)
    py = _fake_python(tmp_path, "u")
    _stub(monkeypatch, {py: (install, "0.9.50")})

    notes: list[str] = []
    assert g.resolve_graphify_python(report=notes.append) == py
    joined = " ".join(notes)
    assert "UNPATCHED" in joined and g.HUGEMODE_MARKER in joined, notes


def test_the_version_skew_is_reported_with_the_remedy(tmp_path, monkeypatch):
    """A skill/package skew is named, with the command that ends it."""
    install = _fake_install(tmp_path, "good", patched=True)
    py = _fake_python(tmp_path, "good")
    _stub(monkeypatch, {py: (install, "0.9.50")})
    skill = tmp_path / ".graphify_version"
    skill.write_text("0.9.55\n", encoding="utf-8")
    monkeypatch.setattr(g, "SKILL_VERSION_FILE", skill)

    notes: list[str] = []
    g.resolve_graphify_python(report=notes.append)

    joined = " ".join(notes)
    assert "skew" in joined, notes
    assert "graphifyy==0.9.55" in joined, notes
    assert "graphify_node_limit_patch.py" in joined, notes


def test_the_skill_and_the_package_can_agree_and_say_nothing(tmp_path, monkeypatch):
    """The negative control: agreement must not produce a warning."""
    install = _fake_install(tmp_path, "good", patched=True)
    py = _fake_python(tmp_path, "good")
    _stub(monkeypatch, {py: (install, "0.9.55")})
    skill = tmp_path / ".graphify_version"
    skill.write_text("0.9.55\n", encoding="utf-8")
    monkeypatch.setattr(g, "SKILL_VERSION_FILE", skill)

    notes: list[str] = []
    g.resolve_graphify_python(report=notes.append)

    assert not any("skew" in n or "WARNING" in n for n in notes), notes


def test_the_candidate_walk_is_not_vacuous_on_this_host():
    """The stubs above prove the order; this proves the walk has something to walk.

    A resolver whose candidate list is empty returns None for everything and
    reads exactly like "no capable install on this box" -- the same silent
    instrument this session has already been bitten by twice. So the real list
    is measured, and if a capable install exists here it must be found.
    """
    cands = g.graphify_python_candidates()
    assert cands, "no candidates; the resolution below would be vacuous"
    assert any(p.exists() for p in cands), cands
    chosen = g.resolve_graphify_python(report=lambda _m: None)
    if chosen is None:
        pytest.skip("no capable graphify install on this host")
    install = g._graphify_install_dir(chosen)
    assert install is not None
    for part in g.REQUIRED_INSTALL_PARTS:
        assert (install / part).exists(), (chosen, part)


# ── the in-flight claim is a claim, and only the claimant may release it ──
#
# Found 2026-09-29 by `graphify diagnose multigraph` reporting 451,315 nodes
# for a graph whose report said 47,273. The hourly spine refresh had been
# running since 10:00:55 and wrote graph.json at 10:27 -- the full unscoped
# corpus, 638 MB -- over a scoped rebuild that had finished at 10:25:51 and
# reported success. `clear_marker()` ran in a `finally` and unlinked
# unconditionally, so the finishing pass deleted the other pass's claim, and
# the clobber was invisible until something compared two numbers.


def test_clear_marker_does_not_release_a_claim_it_does_not_own(monkeypatch, tmp_path):
    """The regression: a finishing pass deleted the live pass's claim.

    `_pid_alive` is stubbed rather than probed: the point is that a claim held
    by a LIVE other process survives, and a bare foreign pid is not evidence
    of that -- an unused pid is a corpse, and `clear_marker` is supposed to
    clean corpses up.
    """
    monkeypatch.setattr(refresh, "MARKER", tmp_path / "graph_refresh.running")
    monkeypatch.setattr(refresh, "_pid_alive", lambda pid: True)
    refresh.MARKER.write_text("100.0 %d" % (os.getpid() + 1_000_000),
                              encoding="utf-8")

    refresh.clear_marker()

    assert refresh.MARKER.exists(), \
        "clear_marker released a claim held by another process -- the second " \
        "pass then raced the first and overwrote its artifact"


def test_clear_marker_releases_its_own_claim(monkeypatch, tmp_path):
    """Ownership is not a reason to leak the slot forever."""
    monkeypatch.setattr(refresh, "MARKER", tmp_path / "graph_refresh.running")
    refresh.write_marker()
    assert refresh.read_marker() is not None
    refresh.clear_marker()
    assert refresh.read_marker() is None


def test_a_marker_left_by_a_dead_process_is_not_a_claim(monkeypatch, tmp_path):
    """A corpse must not wedge the refresh permanently after a crash."""
    monkeypatch.setattr(refresh, "MARKER", tmp_path / "graph_refresh.running")
    monkeypatch.setattr(refresh, "_pid_alive", lambda pid: False)
    refresh.MARKER.write_text("100.0 424242", encoding="utf-8")

    assert refresh.in_flight() is None, \
        "a dead pid was treated as an in-flight refresh, so the artifact " \
        "could never be rebuilt again"
    # ...and the corpse is still clearable rather than blocking the start guard.
    refresh.clear_marker()
    assert not refresh.MARKER.exists()


def test_a_live_other_process_is_a_claim_and_blocks_a_second_pass(
        monkeypatch, tmp_path, capsys):
    """The guard that uses both halves: refuse, rather than race."""
    monkeypatch.setattr(refresh, "MARKER", tmp_path / "graph_refresh.running")
    monkeypatch.setattr(refresh, "_pid_alive", lambda pid: True)
    # Belt and braces, and not decoration: with the guard removed, `main([])`
    # falls through and LAUNCHES A REAL 3-MINUTE REBUILD of graphify-out/.
    # A test that can rewrite a 46 MB artifact is a test that can silently
    # take the tree down, and the first bite-check of this fix did exactly
    # that (178s, rc=0, clobbering the artifact mid-verification).
    monkeypatch.setattr(refresh, "resolve_graphify_python", lambda **kw: None)
    refresh.MARKER.write_text("100.0 424242", encoding="utf-8")

    rc = refresh.main([])

    assert rc == 3, "a second pass was allowed to start over the same artifact"
    out = capsys.readouterr().out
    assert "already in flight" in out and "424242" in out, \
        "the refusal did not say who holds the claim or that it would race"


def test_an_absent_marker_does_not_block(monkeypatch, tmp_path):
    """The guard must not be the reason a refresh cannot start."""
    monkeypatch.setattr(refresh, "MARKER", tmp_path / "graph_refresh.running")
    assert refresh.in_flight() is None
    # main() would go on to resolve an interpreter, so only the guard is
    # exercised here; a claim-free marker must return None, not a refusal.
    assert not refresh.MARKER.exists()
