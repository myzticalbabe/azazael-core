"""Tests for `tools/orphan_triage.py` — the class each orphan row lands in.

Why this file exists: `DEAD` is a WORKLIST, and on 2026-10-15 it was wrong in
three measured ways that a full two-interpreter sweep did not catch --

  1. `_collect_tests()` scanned only the repo-root `tests/`, so the 7 `ML.*`
     modules imported by `src/tests/test_ml_*.py` were reported DEAD with the
     evidence "nothing imports it, nothing names it". False on its face: those
     test files name them in their own import statements.
  2. Generated output (`graphify-out/`) was searched for "dynamic references",
     so a cache that quotes every module path classed whole families DYNAMIC.
  3. `src/SHORTCUTS/` (27 `.bat` files beside one `__init__.py`) was called dead
     capability rather than a directory of programs.

Every expected value below is transcribed from a real `classify()` run on this
tree. A hand-written fixture would only re-assert the assumptions the fix was
built on, which is how the `ML.nsga` false DEAD survived a suite run in the
first place.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Imported by path rather than by putting `tools/` on sys.path: this file must
# not change module resolution for every other test in the same chunk.
_spec = importlib.util.spec_from_file_location(
    "orphan_triage", ROOT / "tools" / "orphan_triage.py")
assert _spec is not None and _spec.loader is not None
triage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(triage)

#: Classified ONCE -- each `classify()` call rescans the tree.
_TARGETS = (
    "ML.nsga",
    "ML.estimators",
    "ML.nsga_tuner",
    "SHORTCUTS",
    "CODEX",
    "PSYCHE.affect",
    "no_such_module_xyz",
)
ROWS = {mod: (cls, ev) for cls, mod, ev in triage.classify(list(_TARGETS))}


def test_pytest_testpaths_is_read_from_the_ini_not_assumed():
    """The collection set is what separates TEST-ONLY from TEST-INERT."""
    assert triage._pytest_testpaths() == ("tests",)


def test_module_imported_by_an_uncollected_test_is_not_dead():
    """The regression: 7 live modules read as DEAD because src/tests was unseen."""
    cls, ev = ROWS["ML.nsga"]
    assert cls == "TEST-INERT", ROWS["ML.nsga"]
    assert "src/tests/test_ml_nsga.py" in ev, ev
    assert "NOT collected" in ev, ev


def test_every_ml_module_with_a_src_tests_file_is_out_of_the_dead_list():
    """All three rows named in the fix, not just the one that was spot-checked.

    The intent is that none of them reads as DEAD. Two are TEST-INERT (only
    `src/tests/`, which pytest does not collect) and one moved on: measured
    2026-11-02, `ML.estimators` is imported by `tests/test_intake_estimator_arm.py`
    -- a COLLECTED test -- so TEST-ONLY is now the true class and asserting
    TEST-INERT here would demand a wrong answer. Both classes are live.
    """
    for mod in ("ML.nsga", "ML.nsga_tuner"):
        assert ROWS[mod][0] == "TEST-INERT", (mod, ROWS[mod])
    cls, ev = ROWS["ML.estimators"]
    assert cls == "TEST-ONLY", ROWS["ML.estimators"]
    assert "tests/test_intake_estimator_arm.py" in ev, ev


def test_collected_and_uncollected_importers_do_not_share_a_class():
    """`tests/` runs and `src/tests/` does not, so one class for both would
    claim coverage that does not exist."""
    assert ROWS["PSYCHE.affect"][0] == "TEST-ONLY", ROWS["PSYCHE.affect"]
    assert ROWS["ML.nsga"][0] == "TEST-INERT", ROWS["ML.nsga"]


def test_script_directory_is_not_dead_capability():
    cls, ev = ROWS["SHORTCUTS"]
    assert cls == "SCRIPT-DIR", ROWS["SHORTCUTS"]
    assert ".bat" in ev, ev


def test_a_library_with_a_stray_script_is_not_excused_as_a_script_dir():
    """The dominance rule. `CODEX` is 18 `.py` against 5 `.bat`, so it keeps its
    real class (measured: DYNAMIC, off a string literal in
    `packages/azazael-core/src/azazael_core/paths.py:137`). Without the rule the
    new class would hide live modules instead of surfacing them, trading three
    false diagnoses for one new one."""
    assert ROWS["CODEX"][0] != "SCRIPT-DIR", ROWS["CODEX"]


def test_naming_a_module_is_not_importing_it():
    """Both directions of the matcher, because the failure was one-directional.

    Measured 2026-10-15: before `_import_forms`, this file's own mention of
    `ML.nsga` in a string flipped it from TEST-INERT to TEST-ONLY -- an
    instrument whose answer about a module moves when an unrelated test is
    added is not measuring imports.
    """
    # A SYNTHETIC module name on purpose. Naming a real module here would put
    # this file into that module's evidence base -- which is the defect under
    # test, and writing the fixture with "ML.nsga" reproduced it exactly.
    mention = [("tests/fake.py", 'OTHER = "SYNTH.mod"\n', True)]
    assert triage._test_import_of("SYNTH.mod", mention) is None
    for text in ("from SYNTH.mod import thing\n",
                 "import SYNTH.mod\n",
                 "from SYNTH import mod\n",
                 'm = importlib.import_module("SYNTH.mod")\n'):
        hit = triage._test_import_of("SYNTH.mod", [("tests/fake.py", text, True)])
        assert hit == ("tests/fake.py", True), (text, hit)
    # `SYNTH.mod_extra` must not satisfy an edge to `SYNTH.mod`
    near = [("tests/fake.py", "import SYNTH.mod_extra\n", True)]
    assert triage._test_import_of("SYNTH.mod", near) is None


def test_absent_module_is_reported_missing_not_dead():
    assert ROWS["no_such_module_xyz"][0] == "MISSING"


def test_generated_output_cannot_mint_a_dynamic_reference():
    """`graphify-out/` is a graphify run's index, rewritten on every run, and it
    quotes module paths. Counting it made a class that was not even stable
    between two scans of one tree."""
    assert "graphify-out" in triage.SKIP_PARTS
    sites = triage._collect_string_literals()
    generated = [lit for lit, where in sites.items()
                 if any(rel.startswith("graphify-out/") for rel, _ in where)]
    assert generated == [], "generated index still feeding DYNAMIC: %r" % generated[:5]
