# coding: utf-8
"""Rootdir test configuration — applies to EVERY test directory in the repo.

WHY THIS EXISTS (audit 2026-09-21, second occurrence of the same class)
-----------------------------------------------------------------------
`tests/conftest.py` redirects `AZAZAEL_MEMORY_DIR`, `AZAZAEL_GENERATED_DIR`
and `AZAZAEL_ENTRENCHMENT_PATH` so a test cannot write into the live stores
it is testing. But a conftest only applies to its OWN directory subtree, and
this repository has a SECOND test directory:

    pytest.ini:  testpaths = tests

`pytest.ini` confines the normal run to `tests/`, so `src/tests/` is reached
only by IDE test discovery or a direct `pytest src/tests/...` — exactly the
runs nobody watches. `src/tests/` has `__init__.py` and 14 test modules and
had NO conftest, so every redirect written for `tests/` was absent there.

Measured consequence (this file's justification, not a hypothesis):
`src/tests/test_worm_registry.py` defines fixture worm classes named
`FleetOrch`, `NetDiag`, `DivWorm`, `SysMonitor`, `ClockSync`, `BinAnalysis`,
`BatchWorm2/4`, `ShortCooldown`, `MonWorm`, `DiagWorm`, `OnlyWorm` and calls
`SpawnDiversityEngine.record_spawn(...)`, which writes a REAL
`spawn_selected` row via `get_memory_store()`. The live choice tap held:

    608 spawn_selected rows total
      408 written by those fixtures (12 names, NONE of them registered worms)
      200 written by the only real component ever chosen (MetaCluster)

So two thirds of the production "which worm did we choose" record was unit
test fixtures. Anything reading that tap to score a component - including
`CORE.WORMS.expansion.spawn_credit.chosen_but_never_run()` and the
morphogenesis opportunity trigger built on it - would score twelve worms
that do not exist and conclude the fleet is 12 organs short. The previous
scrub of the OUTCOME tap fixed the symptom on one tap; this is the same hole
on the CHOICE tap, and the hole is "a test directory outside tests/".

The fix is placed at the rootdir, not added to `src/tests/`, because the
defect is not "src/tests is special" - it is "a test directory anywhere is
unprotected". A third test directory added later is covered by construction
rather than by remembering.

The env vars are read at IMPORT time by the modules that own them
(`CORE.local_memory` computes `DB_PATH` at import), so they must be set at
collection, before any test module imports. That is why this is module-level
code and not a fixture.

`tests/conftest.py` is unchanged and still works: its redirects are guarded
by `if not os.environ.get(...)`, so with this file setting the vars first
they become deliberate no-ops and its `pytest_unconfigure` correctly skips
removing directories it did not create (it tracks its own override handles).
"""

from __future__ import annotations

import os
import pathlib
import tempfile

# ── Containment pen: generated specialist source ────────────────────
_GENERATED_DIR_OVERRIDE = None
if not os.environ.get("AZAZAEL_GENERATED_DIR"):
    _GENERATED_DIR_OVERRIDE = tempfile.mkdtemp(prefix="azazael_generated_")
    os.environ["AZAZAEL_GENERATED_DIR"] = _GENERATED_DIR_OVERRIDE

# ── Entrenchment snapshot (GrayQueen census baseline) ───────────────
_ENTRENCHMENT_OVERRIDE = None
if not os.environ.get("AZAZAEL_ENTRENCHMENT_PATH"):
    _ENTRENCHMENT_OVERRIDE = pathlib.Path(
        tempfile.mkdtemp(prefix="azazael_entrenchment_")) / "entrenchment.json"
    os.environ["AZAZAEL_ENTRENCHMENT_PATH"] = str(_ENTRENCHMENT_OVERRIDE)

# ── Memory store: the outcome AND choice telemetry taps ─────────────
_MEMORY_DIR_OVERRIDE = None
if not os.environ.get("AZAZAEL_MEMORY_DIR"):
    _MEMORY_DIR_OVERRIDE = tempfile.mkdtemp(prefix="azazael_memory_")
    os.environ["AZAZAEL_MEMORY_DIR"] = _MEMORY_DIR_OVERRIDE


# ── Heart pen: no autonomous beats under test ─────────────────────────
# `warm_caches_now()` starts the spine-heart daemon, whose first beat runs a
# FULL worm cycle immediately on its own thread. Under a suite that also runs
# torch construction on worker threads, the daemon's concurrent torch entry
# AVs inside torch C code on 3.15 (measured via faulthandler: spine-heart
# thread in parameter.__new__). setdefault so an explicit export wins.
if not os.environ.get("AZAZAEL_NO_HEART"):
    os.environ["AZAZAEL_NO_HEART"] = "1"


def pytest_unconfigure(config):
    """Remove the session's redirected pen, snapshot and store."""
    import shutil

    if _GENERATED_DIR_OVERRIDE:
        shutil.rmtree(_GENERATED_DIR_OVERRIDE, ignore_errors=True)
    if _ENTRENCHMENT_OVERRIDE:
        shutil.rmtree(str(_ENTRENCHMENT_OVERRIDE.parent), ignore_errors=True)
    if _MEMORY_DIR_OVERRIDE:
        shutil.rmtree(_MEMORY_DIR_OVERRIDE, ignore_errors=True)


# ── PMB outcome observer: the hardwired test trigger ────────────────
# PMB's Earned-Memory loop (`pmb.health.earned_memory.lesson_impact`) and its
# adherence pass (`followcheck`) both read the `agent_actions` table, which is
# written ONLY by the PostToolUse `track-action` hook PMB installs for
# claude-code. This host (Codebuff) is absent from PMB's capability registry,
# so no hook fires and the loop is structurally dead here -- it reported
# `n_outcome_turns: 0` forever. A test run is an outcome-bearing turn that is
# GUARANTEED to happen, so we record it at the one point every run reaches.
#
# This must NOT make a test run fail, so it is entirely best-effort. The
# observer needs the `pmb` package, which lives in the `.pmb-venv` interpreter,
# not in the free-threaded 3.15t interpreter running these tests -- hence the
# subprocess. Disable with AZAZAEL_PMB_OBSERVE=0.
_PMB_VENV_PYTHON_CANDIDATES = (
    os.environ.get("PMB_VENV_PYTHON"),
    str(pathlib.Path.home() / ".pmb-venv" / "Scripts" / "python.exe"),
    str(pathlib.Path.home() / ".pmb-venv" / "bin" / "python"),
)


def _pmb_observe_test_outcome(exitstatus) -> None:
    """Record this run's pass/fail as a PMB agent_action. Best-effort."""
    if os.environ.get("AZAZAEL_PMB_OBSERVE") == "0":
        return
    interp = next((p for p in _PMB_VENV_PYTHON_CANDIDATES
                   if p and os.path.exists(p)), None)
    if not interp:
        return  # no PMB venv on this machine -- nothing to observe into
    observer = pathlib.Path(__file__).resolve().parent / "tools" / "pmb_session_observe.py"
    if not observer.exists():
        return
    try:
        import subprocess
        import time as _time
        # A distinct session id per RUN: `lesson_impact` groups actions into
        # turns by session_id + time gap, so without this two runs minutes apart
        # collapse into one turn and the outcome count under-reports. Each pytest
        # invocation IS a separate session, so this is the honest id.
        subprocess.run(
            [interp, str(observer), "--pytest-exit", str(int(exitstatus)),
             "--session-id", "pytest-%d" % int(_time.time()),
             "--quiet"],
            timeout=20, capture_output=True,
        )
    except Exception:
        pass  # scanner:ignore -- a memory side-effect must never fail the suite


def pytest_sessionfinish(session, exitstatus):
    _pmb_observe_test_outcome(exitstatus)


# ── PMB live-suggestion hook (2026-10-06) ─────────────────────────────
# The missing half of "live error resolution": on test failure, query PMB
# recall with the failure text BEFORE the human (or agent) diagnoses by hand.
# Proven: recall(error_string) returned the exact recorded fix first try.
# Best-effort, capped, never fails the suite. Disable with
# AZAZAEL_PMB_SUGGEST=0. Uses the .pmb-venv interpreter like the observer.
_FAILED_FOR_SUGGEST: list = []


def pytest_runtest_logreport(report):
    if report.failed and report.when in ("setup", "call"):
        try:
            msg = ""
            if hasattr(report, "longrepr"):
                msg = str(report.longrepr)[:400]
            _FAILED_FOR_SUGGEST.append((report.nodeid, msg))
        except Exception:
            pass  # scanner:ignore -- hook contract: logreport must never raise; telemetry append is best-effort


def _pmb_suggest_for(error_text):
    """Ask PMB recall for matching fixes. Returns text or ''."""
    if os.environ.get("AZAZAEL_PMB_SUGGEST") == "0":
        return ""
    interp = next((p for p in _PMB_VENV_PYTHON_CANDIDATES
                   if p and os.path.exists(p)), None)
    if not interp:
        return ""
    client = pathlib.Path(__file__).resolve().parent / "tools" / "pmb_client.py"
    if not client.exists():
        return ""
    try:
        import subprocess
        # Bytes + replace: client output may carry non-UTF8 console bytes;
        # text=True would die in the reader thread instead (measured).
        r = subprocess.run(
            [interp, str(client), "recall", error_text[:200], "--top-k", "2"],
            timeout=25, capture_output=True,
        )
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        return out[:900] if out else ""
    except Exception:
        return ""  # scanner:ignore -- designed degrade: PMB unreachable means no suggestion (docstring: "Returns text or ''")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not _FAILED_FOR_SUGGEST:
        return
    if os.environ.get("AZAZAEL_PMB_SUGGEST") == "0":
        return
    terminalreporter.write_sep("=", "PMB LIVE SUGGESTIONS (recorded fixes matching these failures)")
    for nodeid, msg in _FAILED_FOR_SUGGEST[:3]:
        terminalreporter.write_line(f"-- {nodeid}")
        if not msg.strip():
            terminalreporter.write_line("   (no error text captured)")
            continue
        sugg = _pmb_suggest_for(msg)
        terminalreporter.write_line("   " + (sugg.replace("\n", "\n   ")
                                     if sugg else "(PMB unreachable or no match)"))
    remaining = len(_FAILED_FOR_SUGGEST) - 3
    if remaining > 0:
        terminalreporter.write_line(f"... +{remaining} more failures (see above)")


# NOTE (2026-10-06): a torch session warmup lived here and was removed the
# same day it was added -- measured to turn a passing solo run into an
# access violation on 3.15. Lesson kept, code gone: never pre-touch native
# runtimes at session scope to fix worker-scope races.


# ── Shadow-split visibility guard (2026-10-04) ──────────────────────────
# The editable install resolves CORE.* to the hub tree (C:\Azazael) while
# top-level script modules resolve via sys.path to THIS worktree. Tests that
# import both sides in one process (observers, spine registry, operator
# scoring) then compare constants from two different TREES and fail on a
# split that is environmental, not behavioral. This guard makes the split
# VISIBLE at session start (a warning, never a failure): path-asserting
# tests must relativize (see test_algorithm_observers._treerel), and suite
# runs that must be single-tree should run from the hub tree.
def _tree_root(start: str) -> str:
    p = pathlib.Path(start).resolve()
    if p.is_file():
        p = p.parent
    for cand in [p, *p.parents]:
        if (cand / "src").is_dir() and (cand / "scripts").is_dir():
            return os.path.normcase(str(cand))
    return os.path.normcase(str(p))


def pytest_configure(config):
    try:
        import importlib.util as _ilu
        import warnings as _w
        spec = _ilu.find_spec("CORE")
        origin = (getattr(spec, "origin", "") or "")
        if not origin:
            return
        core_tree = _tree_root(os.path.dirname(os.path.abspath(origin)))
        test_tree = _tree_root(str(pathlib.Path(__file__).resolve().parent))
        if core_tree != test_tree:
            _w.warn(
                "SHADOW-SPLIT: CORE resolves under %s but tests run from %s. "
                "Tests importing both CORE.* and top-level script modules "
                "compare constants from two different TREES. Path-asserting "
                "tests must relativize (test_algorithm_observers._treerel "
                "pattern); for single-tree runs, run pytest from the tree "
                "the editable install points at." % (core_tree, test_tree),
                RuntimeWarning, stacklevel=2)
    except Exception:
        pass  # scanner:ignore -- the shadow-split guard is advisory ("a warning, never a failure" per its own header); configure() must not raise
