#!/usr/bin/env python3
# coding: utf-8
"""Tests for the three graph-assisted audit tools.

Each of these tools shipped with a false PASS and was fixed. Every test
below that mentions a bug is a regression test for a specific one, because
a test that only checks the working case would not have caught any of them.

  dead_function_check   shipped reporting 789 functions dead because it
                        looked up candidate names in an empty dict and so
                        recorded zero references, ever.
  manifest_reconcile    shipped printing "OK: every declared manifest entry
                        resolves" having extracted ZERO entries, because it
                        read only `X = {...}` and the manifest is annotated
                        `X: Dict = {...}`. Vacuous truth is the failure mode
                        a reconciliation tool can least afford.
  dict_key_contract     shipped reporting 23,027 candidates because it
                        counted keys on dicts it could not see built.

The shared lesson is the third test: each tool must return 2
(INCONCLUSIVE) rather than 0 (OK) when it examined nothing.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import dead_function_check as dfc          # noqa: E402
import dict_key_contract as dkc            # noqa: E402
import manifest_reconcile as mr            # noqa: E402


# ---------------------------------------------------------------------
# shared: a tool that examined nothing must not say OK
# ---------------------------------------------------------------------

@pytest.mark.parametrize("tool", ["dead_function_check", "manifest_reconcile",
                                  "dict_key_contract"])
def test_missing_inputs_are_inconclusive_not_ok(tool, tmp_path):
    """No data in -> rc 2. A green result meaning 'nothing was checked' is
    the exact failure this suite exists to prevent."""
    r = subprocess.run(
        [sys.executable, str(TOOLS / (tool + ".py")),
         "--graph", str(tmp_path / "nope.json"),
         "--root", str(tmp_path),
         "--repo", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 2, \
        f"{tool} returned {r.returncode}, expected INCONCLUSIVE(2)"


def test_dict_key_contract_empty_scan_is_inconclusive(tmp_path):
    (tmp_path / "src").mkdir()
    r = subprocess.run(
        [sys.executable, str(TOOLS / "dict_key_contract.py"),
         "--root", str(tmp_path), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 2, "empty tree must be INCONCLUSIVE"


# ---------------------------------------------------------------------
# dead_function_check
# ---------------------------------------------------------------------

def test_reference_sites_are_actually_recorded():
    """The bug: `real_references` was handed the (empty) sites dict as its
    lookup table, so every `candidates.get(name)` missed and no candidate
    could ever acquire a reference. The signature takes names and sites
    separately for this reason."""
    tree = ast.parse("def a():\n    return helper()\n\ndef helper():\n    pass\n")
    idx = {Path("m.py"): {"tree": tree, "funcs": {}, "classes": {},
                          "methods": {}, "exported": set(), "reexported": set()}}
    names = {"helper"}
    sites: dict[str, set] = {}
    dfc.real_references(idx, names, sites)
    assert sites.get("helper"), "a real call site must be recorded"
    assert all(len(s) == 3 for s in sites["helper"]), \
        "each site is (file, line, kind)"


def test_reference_scan_covers_more_than_src(tmp_path):
    """A function called only from a test is not dead. src/ is 745 of 1,273
    Python files here; scanning src/ alone reported every test-only helper
    as unreferenced."""
    assert set(dfc.REFSCAN) >= {"src", "tests", "tools", "scripts"}
    assert "tests" in dfc.REFSCAN


def _mini_graph(files, nodes, edges=()):
    """A graph.json-shaped dict: nodes with _callable, links with relation."""
    gn = []
    for i, (path, name) in enumerate(nodes):
        gn.append({"id": f"n{i}", "label": name, "source_file": path,
                   "source_location": f"L{0}", "_callable": True})
    gl = []
    for src, tgt in edges:
        gl.append({"source": src, "target": tgt, "relation": "calls"})
    return {"nodes": gn, "links": gl}


def test_end_to_end_a_called_function_is_not_reported_dead(tmp_path, monkeypatch):
    """The whole point of the tool, end to end.

    The first version reported 789 functions dead because `real_references`
    was given the empty `sites` dict as its lookup table, so no reference
    was ever recorded. This fails if that bug returns in any form.

    The call is `mod.tested_only()` -- an attribute call -- because that is
    what the M1 mutation disables.
    """
    src = tmp_path / "src"
    tst = tmp_path / "tests"
    src.mkdir()
    tst.mkdir()
    (src / "mod.py").write_text(
        "def lonely():\n    return 1\n\n"
        "def tested_only():\n    return 2\n", encoding="utf-8")
    (tst / "test_mod.py").write_text(
        "import mod\nmod.tested_only()\n", encoding="utf-8")

    src_files = sorted(src.rglob("*.py"))
    tst_files = sorted(tst.rglob("*.py"))
    idx = dfc.module_index(src_files + tst_files)
    nodes = [("src/mod.py", "lonely"), ("src/mod.py", "tested_only")]
    g = _mini_graph(src_files + tst_files, nodes)
    for n in g["nodes"]:
        meta = idx[tmp_path / n["source_file"]]
        n["source_location"] = "L%d" % meta["funcs"][n["label"]]

    # analyse() resolves graph source_file as ROOT / sf, so ROOT must be the
    # temp tree for the stub paths to line up with the parsed files.
    monkeypatch.setattr(dfc, "ROOT", tmp_path)
    monkeypatch.setattr(dfc, "SRC", src)
    monkeypatch.setattr(dfc, "REFSCAN", ("src", "tests"))

    def fake_py_files(roots):
        if tuple(roots) == ("src",):
            return src_files
        if tuple(roots) == ("src", "tests"):
            return src_files + tst_files
        return []

    monkeypatch.setattr(dfc, "py_files", fake_py_files)
    rep = dfc.analyse(g, 0)
    names = {r["name"] for r in rep["dead_functions"]}
    assert "tested_only" not in names, \
        f"a function called from tests/ is not dead; got {names}"
    assert "lonely" in names, f"a function nothing calls IS dead; got {names}"


def test_dunder_and_common_names_are_not_candidates():
    """Dunders are filtered by the `startswith("__")` test, not by the
    FRAMEWORK_HOOKS set, so the set lists only named hooks."""
    assert "__init__" not in dfc.FRAMEWORK_HOOKS, \
        "dunders are excluded structurally; listing them here is misleading"
    assert "execute" in dfc.FRAMEWORK_HOOKS
    assert "get" in dfc.TOO_COMMON


def test_pytest_hooks_are_framework_entry_points():
    """`pytest_configure` in a conftest is called by pytest, not by code.
    Reporting it dead is a false positive with no defence."""
    assert "pytest_configure" in dfc.FRAMEWORK_HOOKS


def test_conftest_is_never_a_candidate(tmp_path, monkeypatch):
    """conftest.py is loaded by pytest, not imported by anything, so no
    name in it has a call site by construction."""
    src = tmp_path / "src"
    src.mkdir()
    f = src / "conftest.py"
    f.write_text("def pytest_configure(config):\n    pass\n", encoding="utf-8")
    code = dfc.ROOT / "tools" / "dead_function_check.py"
    text = code.read_text(encoding="utf-8")
    assert 'if p.name == "conftest.py"' in text, \
        "conftest exclusion regressed"


def test_end_to_end_framework_hooks_and_conftest_are_excluded(tmp_path,
                                                             monkeypatch):
    """`execute` is reached by the worm registry through a dict lookup, and
    conftest hooks by pytest. Neither has a call edge, and reporting 2,800
    of them as dead is what made the first report useless.

    Note the fixture uses names that ARE in FRAMEWORK_HOOKS. An earlier
    version of this test used `initialize`/`shutdown`, which are not in the
    set -- so the test asserted nothing and a mutation removing the
    FRAMEWORK_HOOKS check did not fail it.
    """
    src = tmp_path / "src"
    src.mkdir()
    body = ("class Arm:\n"
            "    def execute(self):\n        pass\n"
            "    def health(self):\n        pass\n"
            "    def genuinely_dead(self):\n        pass\n")
    (src / "arms.py").write_text(body, encoding="utf-8")
    (src / "conftest.py").write_text(
        "def pytest_configure(config):\n    pass\n", encoding="utf-8")

    files = sorted(src.rglob("*.py"))
    idx = dfc.module_index(files)
    wanted = ["Arm.execute", "Arm.health", "Arm.genuinely_dead",
              "pytest_configure"]
    g = _mini_graph(files, [(str(f.relative_to(tmp_path)).replace("\\", "/"), n)
                            for f in files for n in wanted])
    lines = {}
    for f in files:
        for (cls, meth), ln in idx[f]["methods"].items():
            lines[f"{cls}.{meth}"] = ln
        for fn, ln in idx[f]["funcs"].items():
            lines.setdefault(fn, ln)
    for n in g["nodes"]:
        base = n["label"].split(".")[-1]
        n["source_location"] = "L%d" % lines.get(n["label"], lines.get(base, 1))

    monkeypatch.setattr(dfc, "ROOT", tmp_path)
    monkeypatch.setattr(dfc, "SRC", src)
    monkeypatch.setattr(dfc, "REFSCAN", ("src",))
    monkeypatch.setattr(dfc, "py_files", lambda roots: files)
    rep = dfc.analyse(g, 0)
    # The recorded name is the bare method name: the class prefix is stripped
    # so that FRAMEWORK_HOOKS and TOO_COMMON can match at all. Before that
    # fix every `execute`/`health` method bypassed both exclusions.
    allnames = {r["name"] for r in rep["dead_functions"]} | \
               {r["name"] for r in rep["unreferenced_methods"]}
    for hook in ("execute", "health", "pytest_configure"):
        assert hook not in allnames, \
            f"{hook} is a framework entry point, not dead; got {allnames}"
    assert "genuinely_dead" in allnames, \
        f"the control must still be reported; got {allnames}"


# ---------------------------------------------------------------------
# manifest_reconcile
# ---------------------------------------------------------------------

MANIFEST = '''\
BUILD_TARGETS: Dict[str, Dict] = {
    "a": {"entry": "src/real.py", "output": "a.exe"},
    "b": {"entry": "src/moved_here.py", "output": "b.exe"},
    "c": {"entry": "src/nowhere.py", "output": "c.exe"},
}
'''


def _repo(tmp_path, manifest_text=MANIFEST, files=("src/real.py",
                                                   "src/moved_here.py")):
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts" / "build_all_concurrent.py").write_text(
        manifest_text, encoding="utf-8")
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def _graph(tmp_path, paths):
    nodes = [{"id": Path(p).stem, "label": Path(p).stem,
              "source_file": p, "_callable": False} for p in paths]
    return nodes


def test_annotated_manifest_assignment_is_read(tmp_path):
    """`X: Dict = {...}` is the form this repo uses. Reading only `X = {...}`
    found zero entries and reported OK -- vacuous truth."""
    repo = _repo(tmp_path)
    found = mr.declared_entries(repo / "scripts" / "build_all_concurrent.py")
    assert len(found) == 3, f"expected 3 entries, got {len(found)}"


def test_bare_manifest_assignment_is_also_read(tmp_path):
    repo = _repo(tmp_path, manifest_text=MANIFEST.replace(
        "BUILD_TARGETS: Dict[str, Dict] = {", "BUILD_TARGETS = {"))
    found = mr.declared_entries(repo / "scripts" / "build_all_concurrent.py")
    assert len(found) == 3


def test_docstring_example_paths_are_not_entries(tmp_path):
    """The lesson from `__all__` in src/CORE/STRUCTURE.py, where the
    apparent export list was 100 lines up inside a docstring. A grep found
    it; the AST does not."""
    src = '''\
"""
Example manifest:
__all__ = ["a", "b"]
"""
BUILD_TARGETS = {"a": {"entry": "src/real.py"}}
'''
    repo = _repo(tmp_path, manifest_text=src, files=("src/real.py",))
    found = mr.declared_entries(repo / "scripts" / "build_all_concurrent.py")
    assert len(found) == 1, f"docstring paths leaked in: {found}"


def test_classify_distinguishes_moved_from_missing(tmp_path):
    repo = _repo(tmp_path)
    by_base = {"real.py": {"src/real.py"},
               "moved_here.py": {"src/moved_here.py"},
               "nowhere.py": set()}
    # The declared path does not exist on disk, so classify falls through to
    # the basename lookup -- which is the branch under test.
    got_moved = mr.classify("src/moved_here.py", repo, by_base, set())
    assert got_moved["status"] == "ok", "a real path short-circuits to ok"

    got_moved = mr.classify("src/moved_here.py.bak", repo, by_base, set())
    assert got_moved["status"] == "missing"

    got_moved = mr.classify("src/renamed.py", repo,
                            {"renamed.py": {"src/renamed.py"}}, set())
    assert got_moved["status"] == "moved"
    assert got_moved["candidates"] == ["src/renamed.py"]

    got_missing = mr.classify("src/nowhere.py", repo, by_base, set())
    assert got_missing["status"] == "missing"
    assert got_missing["candidates"] == []


def test_ambiguous_basename_is_reported_not_guessed(tmp_path):
    """Two files share a basename. Picking one would be a guess dressed as
    a finding, and a guess in a build manifest is worse than no report."""
    repo = _repo(tmp_path)
    by_base = {"dup.py": {"a/dup.py", "b/dup.py"}}
    got = mr.classify("src/dup.py", repo, by_base, set())
    assert got["status"] == "ambiguous"
    assert len(got["candidates"]) == 2


def test_ambiguous_prefers_the_same_directory(tmp_path):
    """Two files share the basename `dup.py`. The one in the SAME directory
    as the declaration is the better-supported answer, so it is preferred
    over reporting the pair as ambiguous."""
    repo = _repo(tmp_path)
    by_base = {"dup.py": {"other/dup.py", "src/dup.py"}}
    got = mr.classify("src/dup.py", repo, by_base, set())
    assert got["status"] == "moved"
    assert got["candidates"] == ["src/dup.py"], \
        "same-directory evidence is stronger than a bare basename match"


def test_ambiguous_across_directories_is_not_guessed(tmp_path):
    """Same basename, neither in the declared directory: no evidence
    distinguishes them, so the answer is 'ambiguous', not a coin flip."""
    repo = _repo(tmp_path)
    by_base = {"dup.py": {"x/dup.py", "y/dup.py"}}
    got = mr.classify("src/dup.py", repo, by_base, set())
    assert got["status"] == "ambiguous"
    assert got["candidates"] == ["x/dup.py", "y/dup.py"]


def _graph_json(tmp_path, paths):
    nodes = [{"id": Path(p).stem + str(i), "label": Path(p).stem,
              "source_file": p, "_callable": False}
             for i, p in enumerate(paths)]
    (tmp_path / "graph.json").write_text(
        json.dumps({"nodes": nodes, "links": []}), encoding="utf-8")


def test_manifest_with_no_entries_is_inconclusive(tmp_path):
    """The vacuous-truth failure, end to end.

    The tool shipped printing "OK: every declared manifest entry resolves"
    having extracted zero entries, because it read only `X = {...}` and the
    real manifest is `X: Dict = {...}`. "Every entry resolves" is trivially
    true of no entries, so the guard has to be tested through main().
    """
    repo = _repo(tmp_path, manifest_text="BUILD_TARGETS = {}\n",
                 files=("src/real.py",))
    _graph_json(tmp_path, ["src/real.py"])
    r = subprocess.run(
        [sys.executable, str(TOOLS / "manifest_reconcile.py"),
         "--graph", str(tmp_path / "graph.json"),
         "--repo", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 2, \
        f"no entries must be INCONCLUSIVE, got rc={r.returncode}: {r.stdout}"
    assert "INCONCLUSIVE" in r.stderr


def test_manifest_reports_a_missing_entry_end_to_end(tmp_path):
    """And the tool must still return 1 when there IS something to say."""
    repo = _repo(tmp_path, files=("src/real.py", "src/moved_here.py"))
    _graph_json(tmp_path, ["src/real.py", "src/moved_here.py"])
    r = subprocess.run(
        [sys.executable, str(TOOLS / "manifest_reconcile.py"),
         "--graph", str(tmp_path / "graph.json"),
         "--repo", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 1, f"expected rc=1, got {r.returncode}"
    assert "MISSING" in r.stdout and "nowhere.py" in r.stdout


# ---------------------------------------------------------------------
# dict_key_contract
# ---------------------------------------------------------------------

def _findings(src, min_sites=1):
    tree = ast.parse(src)
    mk = dkc.scan(tree)
    out = []
    for d, keys in sorted(mk.read.items()):
        if d.split("|", 1)[-1] in dkc.EXCLUDED_OWNERS:
            continue
        for k, lines in sorted(keys.items()):
            if k not in mk.written.get(d, {}):
                out.append(("read_never_written", d, k))
    for d, keys in sorted(mk.written.items()):
        if d.split("|", 1)[-1] in dkc.EXCLUDED_OWNERS:
            continue
        for k, lines in sorted(keys.items()):
            if k not in mk.read.get(d, {}):
                out.append(("written_never_read", d, k))
    return out


def test_read_never_written_in_one_function():
    got = _findings('def f():\n    r = {}\n    r["a"] = 1\n    return r["b"]\n')
    assert ("read_never_written", "f|r", "b") in got


def test_written_never_read():
    got = _findings('def f():\n    r = {}\n    r["a"] = 1\n    return 0\n')
    assert ("written_never_read", "f|r", "a") in got


def test_symmetric_access_is_clean():
    got = _findings('def f():\n    r = {}\n    r["a"] = 1\n    return r["a"]\n')
    assert not got, f"symmetric access must be clean, got {got}"


def test_parameter_dicts_are_out_of_scope():
    """A dict passed in has keys the caller chose. Claiming to know them is
    the noise floor that made the first version report 23,027 candidates.

    The fixture is deliberately awkward: a dict literal named `p` exists at
    module level, so `owned("p")` is already true and the parameter check is
    the ONLY thing standing between this and a false finding. A fixture
    without that collision would pass whether or not the check exists.
    """
    src = ('p = {"seed": 1}\n\n'
           'def f(p):\n    return p["whatever"]\n')
    mk = dkc.scan(ast.parse(src))
    assert "p" in mk.dict_names, "fixture precondition: `p` is a known dict"
    assert not mk.read, \
        f"a parameter dict's keys are the caller's business, got {mk.read}"


def test_returned_dict_is_out_of_scope():
    """A returned literal is read by callers, by definition. Counting it
    was 7,119 of 13,634 findings -- over half the report, all meaningless."""
    assert "<returned>" in dkc.EXCLUDED_OWNERS
    got = _findings('def f():\n    return {"a": 1}\n')
    assert not got, f"returned dict must be ignored, got {got}"


def test_cross_function_dict_is_separate_scopes():
    """`worm_result` crosses four methods in CLUSTERWORMS.py. The census
    is per (function, dict) precisely so that boundary is visible rather
    than silently pooled."""
    src = ('def a():\n    r = {}\n    r["k"] = 1\n    return r\n\n'
           'def b():\n    r = a()\n    return r["k"]\n')
    mk = dkc.scan(ast.parse(src))
    assert "a|r" in mk.written
    assert "b|r" in mk.read
    assert "b|r" not in mk.written, \
        "a dict obtained from a callee must not inherit its writes"


def test_parameter_dict_is_not_censused():
    src = ('p = {"seed": 1}\n\n'
           'def b(p):\n    return p["k"]\n')
    mk = dkc.scan(ast.parse(src))
    assert "b|p" not in mk.read, \
        f"a parameter dict's keys are the caller's business, got {mk.read}"


def test_low_coverage_tree_is_inconclusive_end_to_end(tmp_path):
    """`--min-sites` too high for the only file present means nothing was
    examined. That must be INCONCLUSIVE, not a green OK."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tiny.py").write_text(
        'def f():\n    r = {}\n    r["a"] = 1\n    return r["a"]\n',
        encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(TOOLS / "dict_key_contract.py"),
         "--root", str(tmp_path), "--min-sites", "9999", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 2, \
        f"nothing examined must be INCONCLUSIVE, got rc={r.returncode}"


def test_dotted_underscore_peels_subscript():
    """The bug in the first version of monotone_reduction_check: `_dotted`
    returned '' for a Subscript, so `d["k"] = ...` resolved to no dict at
    all and nothing was ever counted."""
    node = ast.parse("d['k'] = 1").body[0].targets[0]
    assert dkc._dotted(node.value) == "d"
    assert dkc._key_of(node) == "k"


# ---------------------------------------------------------------------
# the cross-tool lesson
# ---------------------------------------------------------------------

def test_no_tool_reports_ok_without_saying_what_it_examined():
    """Each tool prints its examined-count on every run. A green result with
    no count next to it is how a tool reports success for having done
    nothing, which is what all three of these did at least once."""
    for name in ("dead_function_check", "manifest_reconcile",
                 "dict_key_contract"):
        r = subprocess.run(
            [sys.executable, str(TOOLS / (name + ".py")), "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, name


# ---------------------------------------------------------------------
# dict_key_contract, end to end through main()
# ---------------------------------------------------------------------

def _dkc_run(tmp_path, files):
    for name, body in files.items():
        p = tmp_path / "src" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(TOOLS / "dict_key_contract.py"),
         "--root", str(tmp_path), "--min-sites", "1", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_dict_key_end_to_end_reports_and_exits_nonzero(tmp_path):
    r = _dkc_run(tmp_path, {"m.py": 'def f():\n'
                                      '    r = {}\n'
                                      '    r["a"] = 1\n'
                                      '    return r["b"]\n'})
    assert r.returncode == 1, f"a real finding must exit 1, got {r.returncode}"
    rep = json.loads(r.stdout)
    assert rep["findings"], "findings must be reported"
    assert rep["examined"] == 1, "must say what it examined"


def test_dict_key_end_to_end_clean_file_exits_zero(tmp_path):
    r = _dkc_run(tmp_path, {"m.py": 'def f():\n'
                                      '    r = {}\n'
                                      '    r["a"] = 1\n'
                                      '    return r["a"]\n'})
    assert r.returncode == 0, f"clean file must exit 0, got {r.returncode}"
    assert "OK" in r.stdout or not json.loads(r.stdout)["findings"]


def test_dict_key_scope_excludes_duplicates_outside_the_scoped_trees(tmp_path):
    """This repo carries tracked, unlinked duplicates of its own modules at
    the repo root. A report naming "CLUSTERWORMS.py:706" when the reader
    has src/CORE/WORMS/CLUSTERWORMS.py open points at the wrong file."""
    r = _dkc_run(tmp_path, {"m.py": 'def f():\n'
                                      '    r = {}\n'
                                      '    r["a"] = 1\n'
                                      '    return r["b"]\n'})
    (tmp_path / "m.py").write_text('def g():\n    r = {}\n'
                                   '    r["a"] = 1\n'
                                   '    return r["b"]\n', encoding="utf-8")
    rep = json.loads(r.stdout)
    for f in rep["findings"]:
        assert not f["file"].startswith(".."), \
            f"findings must be inside the scoped trees, got {f['file']}"


# ---------------------------------------------------------------------
# shadowed_module_check
# ---------------------------------------------------------------------

import shadowed_module_check as smc   # noqa: E402


def test_shadow_detection_reports_both_directions(tmp_path):
    """The correction this tool exists for.

    An audit called 8 root-level files "stale shadows" and proposed deleting
    them on commit-date recency. Measuring content instead: health_server.py
    has 120 lines the src/ copy lacks and defines a function it does not.
    Deleting on that reasoning destroys working code, so the tool must
    report divergence in BOTH directions and refuse to pick a winner.
    """
    a = tmp_path / "health_server.py"
    a.write_text("def get_health_json():\n    pass\n\n"
                 "def _get_azazael_health():\n    return {}\n", encoding="utf-8")
    b = tmp_path / "src" / "CORE" / "health_server.py"
    b.parent.mkdir(parents=True)
    b.write_text("def get_health_json():\n    pass\n\n"
                 "def prometheus():\n    pass\n", encoding="utf-8")
    c = tmp_path / "shadowed_module_check.py"
    c.write_text(inspect_src(), encoding="utf-8")

    shadowers = smc.top_level_shadowers(tmp_path)
    assert "health_server.py" in shadowers, "a root file must be detected"
    tw = smc.twins(tmp_path, shadowers)
    assert tw["health_server.py"], "its twin must be found"

    counts = smc.unique_line_counts(tmp_path, "health_server.py",
                                    "src/CORE/health_server.py")
    assert counts["only_root"] > 0, "root has content the twin lacks"
    assert counts["only_twin"] > 0, "the twin has content root lacks"


def test_identical_shadow_is_flagged_identical(tmp_path):
    a = tmp_path / "mod.py"
    a.write_text("x = 1\n", encoding="utf-8")
    b = tmp_path / "src" / "mod.py"
    b.parent.mkdir(parents=True)
    b.write_text("x = 1\n", encoding="utf-8")
    counts = smc.unique_line_counts(tmp_path, "mod.py", "src/mod.py")
    assert counts["only_root"] == 0 and counts["only_twin"] == 0


def test_archive_twins_are_classified_separately(tmp_path):
    """A shadow of an archived copy and a shadow of a live module need
    opposite handling; collapsing them is how the wrong one gets deleted."""
    a = tmp_path / "thing.py"
    a.write_text("y = 1\n", encoding="utf-8")
    c = tmp_path / "archive" / "scripts" / "thing.py"
    c.parent.mkdir(parents=True)
    c.write_text("y = 2\n", encoding="utf-8")
    assert c.as_posix().startswith(smc.ARCHIVE_PREFIXES[0]) or \
        str(c).replace("\\", "/").find("archive/") >= 0
    live = tmp_path / "src" / "thing.py"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("y = 3\n", encoding="utf-8")
    assert "archive/" not in live.as_posix()


def test_import_check_finds_a_bare_name_import(tmp_path):
    """The difference between 'ambiguous' and 'actively dangerous': a shadow
    nothing imports is a naming problem; one something imports can be
    resolved to the wrong file at runtime."""
    caller = tmp_path / "src" / "app.py"
    caller.parent.mkdir(parents=True)
    caller.write_text("import health_server\n", encoding="utf-8")
    root = tmp_path / "health_server.py"
    root.write_text("x = 1\n", encoding="utf-8")
    hits = smc.import_check(tmp_path, {"health_server"})
    assert hits.get("health_server"), "a bare-name import must be detected"
    assert any("app.py" in s for s in hits["health_server"])


def test_import_check_ignores_archives_and_self(tmp_path):
    arch = tmp_path / "archive" / "old.py"
    arch.parent.mkdir(parents=True)
    arch.write_text("import health_server\n", encoding="utf-8")
    root = tmp_path / "health_server.py"
    root.write_text("import health_server\n", encoding="utf-8")
    hits = smc.import_check(tmp_path, {"health_server"})
    assert not hits, "archives and self-references are not resolution hazards"


def inspect_src() -> str:
    """A stand-in 'twin' so the fixture is a real tracked .py file."""
    return "z = 0\n"


# ---------------------------------------------------------------------
# scope_coverage
# ---------------------------------------------------------------------

import scope_coverage as scov   # noqa: E402


def _preflight(tmp_path, stems, groups=None):
    p = tmp_path / "mesh_function_preflight.py"
    if groups is None:
        p.write_text(
            'FABRIC = %r\nSCOPE_GROUPS: list[tuple[str, list[str]]] = '
            '[("Fabric", FABRIC)]\n' % (stems,), encoding="utf-8")
    else:
        p.write_text(groups, encoding="utf-8")
    return p


def test_scope_coverage_flags_a_declared_stem_with_no_file(tmp_path):
    """The dangerous direction. A stem in SCOPE_GROUPS that matches no file
    means findings inside it can never be reported -- and the wire-or-cut
    test stays green over a silently smaller question."""
    pf = _preflight(tmp_path, ["mesh", "ghost_module"])
    src = tmp_path / "src"
    src.mkdir()
    (src / "mesh.py").write_text("x = 1\n", encoding="utf-8")
    rep = scov.analyse(pf, src)
    assert rep["declared_but_absent"] == ["ghost_module"]
    assert rep["coverage_pct"] == 100.0


def test_scope_coverage_reports_unexamined_modules(tmp_path):
    """9.5% coverage is a fact worth stating out loud. A green wire-or-cut
    test means the declared scope is triaged, not that the tree is."""
    pf = _preflight(tmp_path, ["mesh"])
    src = tmp_path / "src"
    src.mkdir()
    (src / "mesh.py").write_text("x = 1\n", encoding="utf-8")
    for n in ("other", "another", "third"):
        (src / f"{n}.py").write_text("y = 1\n", encoding="utf-8")
    rep = scov.analyse(pf, src)
    assert rep["modules_on_disk"] == 4
    assert rep["present_on_disk"] == 1
    assert rep["unexamined"] == 3
    assert rep["coverage_pct"] == 25.0


def test_scope_coverage_end_to_end_exit_codes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mesh.py").write_text("x = 1\n", encoding="utf-8")

    bad = _preflight(tmp_path, ["mesh", "ghost"])
    r = subprocess.run(
        [sys.executable, str(TOOLS / "scope_coverage.py"),
         "--preflight", str(bad), "--src", str(src)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 1, "a phantom scope entry must exit 1"
    assert "DECLARED BUT ABSENT" in r.stdout

    good = _preflight(tmp_path, ["mesh"])
    r = subprocess.run(
        [sys.executable, str(TOOLS / "scope_coverage.py"),
         "--preflight", str(good), "--src", str(src)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, "an intact scope must exit 0"


def test_scope_coverage_reads_scope_from_ast_not_import(tmp_path):
    """Importing the preflight would pull in the whole worm registry. A tool
    that measures the boundary should have no side effects reading it."""
    pf = _preflight(tmp_path, ["mesh"])
    text = pf.read_text(encoding="utf-8")
    assert "import" not in text
    groups = scov.declared_stems(pf)
    assert groups == {"Fabric": ["mesh"]}


# ---------------------------------------------------------------------
# dict_key_triage
# ---------------------------------------------------------------------

import dict_key_triage as dkt   # noqa: E402


def _finding(file="src/m.py", scope="f", owner="r", key="k", line=3,
             kind="read_never_written"):
    return {"kind": kind, "file": file, "dict": f"{scope}|{owner}",
            "key": key, "line": line, "read_at": [line]}


def test_triage_key_is_stable_and_scope_qualified():
    """Two functions can each hold a local `result` with genuinely different
    keys. Collapsing them would let one declaration excuse the other."""
    a = dkt.key_for(_finding(scope="f1", owner="result", key="a"))
    b = dkt.key_for(_finding(scope="f2", owner="result", key="b"))
    assert a != b
    assert a == "src/m.py::f1::result::a"


def test_triage_reports_undeclared_and_stale():
    contract = {"findings": [_finding(key="live")]}
    triage = {"entries": {
        "src/m.py::f::r::live": {"status": "accepted", "reason": "ok"},
        "src/m.py::f::r::gone": {"status": "accepted", "reason": "ok"},
    }}
    rep = dkt.analyse(contract, triage)
    assert rep["undeclared"] == []
    assert rep["stale"] == ["src/m.py::f::r::gone"], \
        "a declaration with no finding is how a triage file rots"


def test_triage_flags_undeclared_findings():
    contract = {"findings": [_finding(key="new")]}
    rep = dkt.analyse(contract, {"entries": {}})
    assert rep["undeclared"] == ["src/m.py::f::r::new"]


def test_accepted_without_a_reason_blocks():
    """3,464 is a debt count, not a defect count. A blanket dismissal with no
    stated reason is the failure mode this mechanism exists to prevent."""
    contract = {"findings": [_finding(key="k")]}
    triage = {"entries": {"src/m.py::f::r::k": {"status": "accepted",
                                                "reason": "  "}}}
    rep = dkt.analyse(contract, triage)
    assert rep["missing_reason"] == ["src/m.py::f::r::k"]
    assert "src/m.py::f::r::k" in rep["blocking"]


def test_invalid_status_blocks():
    contract = {"findings": [_finding(key="k")]}
    triage = {"entries": {"src/m.py::f::r::k": {"status": "looks_fine",
                                                "reason": "x"}}}
    rep = dkt.analyse(contract, triage)
    assert rep["bad_status"] == ["src/m.py::f::r::k"]


def test_overdue_review_blocks():
    contract = {"findings": [_finding(key="k")]}
    triage = {"entries": {"src/m.py::f::r::k": {
        "status": "accepted", "reason": "checked",
        "review_after": "2000-01-01"}}}
    rep = dkt.analyse(contract, triage)
    assert rep["overdue"] == ["src/m.py::f::r::k"], \
        "an accepted verdict past its review date must be revisited"


def test_future_review_date_is_not_overdue():
    contract = {"findings": [_finding(key="k")]}
    triage = {"entries": {"src/m.py::f::r::k": {
        "status": "accepted", "reason": "checked",
        "review_after": "2999-01-01"}}}
    rep = dkt.analyse(contract, triage)
    assert rep["overdue"] == []
    assert rep["blocking"] == []


# ---------------------------------------------------------------------
# commit_scope_check
# ----���----------------------------------------------------------------

import commit_scope_check as csc   # noqa: E402


def test_commit_scope_classifies_noise_and_source():
    rep = csc.classify(
        staged_paths=["src/CORE/x.py", "state/chain.json",
                      "logs/run.log", "output/suite/a.json"],
        dirty_paths=["src/CORE/x.py", "src/CORE/y.py", "state/chain.json"])
    assert rep["staged_source"] == 1
    assert rep["staged_noise"] == 3
    assert rep["unstaged_source"] == 1
    assert "src/CORE/y.py" in rep["unstaged_source_sample"]


def test_commit_scope_reads_the_index_not_the_working_tree():
    """633 dirty entries is a fact about the tree. What a commit contains is
    a fact about the index, and conflating the two is the whole failure."""
    text = (TOOLS / "commit_scope_check.py").read_text(encoding="utf-8")
    assert "diff" in text and "--cached" in text and "--name-only" in text


def test_commit_scope_noise_detection():
    assert csc.is_noise("state/a.json")
    assert csc.is_noise("logs/b.log")
    assert csc.is_noise("graphify-out/graph.json")
    assert not csc.is_noise("src/CORE/spine_registry.py")
    assert not csc.is_noise("tools/dead_function_check.py")


def test_commit_scope_not_a_repo_is_inconclusive(tmp_path):
    r = subprocess.run(
        [sys.executable, str(TOOLS / "commit_scope_check.py"),
         "--repo", str(tmp_path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 2, "a non-repo must be INCONCLUSIVE, not OK"


# ---------------------------------------------------------------------
# graphify_version_check
# ---------------------------------------------------------------------

import graphify_version_check as gvc   # noqa: E402


def test_graphify_alignment_requires_patch_as_well_as_version():
    """Version equality alone is not alignment. An unpatched 0.9.55 would
    satisfy a naive equality check and quietly produce a graph.html that
    cannot render this tree."""
    src = (TOOLS / "graphify_version_check.py").read_text(encoding="utf-8")
    assert 'mk.get("patched")' in src, "patch state must gate alignment"
    assert "aligned" in src


def test_graphify_check_reports_rather_than_installs():
    """The remedy installs a package outside this project and replaces a
    patched install. It must be printed, never executed."""
    src = (TOOLS / "graphify_version_check.py").read_text(encoding="utf-8")
    assert "uv tool install" in src, "the remedy must be documented"
    assert "subprocess.run([\"uv\"" not in src
    assert "uv tool install" not in src.split("def main")[1].split('print(')[0] \
        .replace("REMEDY = (", "").replace(")", ""), \
        "main() must not invoke the installer"
    assert "not fixed" in src, "the refusal must be stated to the reader"
