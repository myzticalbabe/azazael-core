"""Main-suite controls for the REAPER (`Project_FLAMING_SWORD/tools/sentinel.py`).

The reaper already carries its own selftest with a positive control (a correct action
must stay allowed) and ten mutations (each known-bad frame must be BLOCKED for the
named S-check) -- satisfying the negative-control rule. These tests make that control
run reachable from the main suite by loading the LIVE production file by path, and add
only what the selftest cannot express:

* the CLI exit contract is exactly {ok: 0, blocked: 1, cannot-run >= 2} and exit 1 is
  never reported as an OK verdict (a reaper that praises what it reaped is worse than
  none);
* the S-checks fire on a bad root, not a good one -- the reaper must be able to be
  pointed at any tree, not just its own.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "Project_FLAMING_SWORD" / "tools" / "sentinel.py"


@pytest.fixture(scope="module")
def reaper(load_tool_by_path):
    return load_tool_by_path(TOOL, "flaming_sword_reaper")


# --------------------------------------------------------------------------- #
# The reaper's own controls, run against the live production file
# --------------------------------------------------------------------------- #

def test_selftest_passes_on_the_live_file(reaper):
    failures, lines = reaper.selftest()
    assert failures == 0, "\n".join(lines)


def test_healthy_action_is_allowed(reaper):
    verdict = reaper.check(
        intent="refuse a shard whose golden run does not reproduce",
        target="tools/sword.py",
        must_not_break=["tests/test_defender.py"],
        defends=["the golden run's state_sha256 token"],
        refusal="BLOCK when the acceptance command exits non-zero",
        root=TOOL.parent.parent,
    )
    assert verdict["verdict"] == "SENTINEL OK"
    assert not verdict["problems"]


def test_checks_resolve_against_a_stated_root(reaper, tmp_path: pathlib.Path):
    # A healthy frame relative to an empty tree MUST block: everything it names is
    # a ghost there. Answers "can the reaper be pointed at any tree" and is the
    # positive control for `root=` not silently defaulting to here.
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tools" / "sword.py").write_bytes(b"# kept\n")
    verdict = reaper.check(
        intent="add an S8 check to the extras",
        target="new:tools/extras.py",
        must_not_break=["tests/test_extras.py"],
        defends=["the S8 note in CHANGELOG.md"],
        refusal="BLOCK when S8 answers a topic instead of an act",
        root=tmp_path,
    )
    # target is honestly `new:` (no file), but the break list names a ghost.
    assert any(p.startswith("S3 break-resolves") for p in verdict["problems"])
    (tmp_path / "tests" / "test_extras.py").write_bytes(b"def test_s8(): pass\n")
    ok = reaper.check(
        intent="add an S8 check to the extras",
        target="new:tools/extras.py",
        must_not_break=["tests/test_extras.py"],
        defends=["the S8 note in CHANGELOG.md"],
        refusal="BLOCK when S8 answers a topic instead of an act",
        root=tmp_path,
    )
    assert ok["verdict"] == "SENTINEL OK"


def test_topic_intent_and_ghost_target_are_both_reaped(reaper):
    bad = reaper.check(
        intent="the validation situation",
        target="tools/ghost.py",
        must_not_break=["tests/test_ghost.py"],
        defends=[],
        refusal="",
        root=TOOL.parent.parent,
    )
    prefixes = [p.split(":", 1)[0] for p in bad["problems"]]
    for expected in ("S1 intent-scoped", "S2 target-resolves", "S3 break-resolves",
                     "S4 defence-named", "S5 landing-stated"):
        assert any(p == expected for p in prefixes), prefixes
    assert bad["verdict"] == "SENTINEL BLOCKED"


# --------------------------------------------------------------------------- #
# CLI exit contract
# --------------------------------------------------------------------------- #

def ok_root(base: pathlib.Path) -> pathlib.Path:
    """A minimal tree the exit-0 frame can honestly resolve against."""
    root = base / "okroot"
    (root / "tools").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "tools" / "cmd_ok.py").write_bytes(b"\n")
    (root / "tests" / "keep.md").write_bytes(b"# kept\n")
    return root


def _run(argv: list[str], cwd: pathlib.Path):
    proc = subprocess.run([sys.executable, str(TOOL), *argv],
                          capture_output=True, text=True, cwd=str(cwd), timeout=60)
    return proc


def test_cli_exit_0_on_an_allowed_action(tmp_path: pathlib.Path):
    # The fixture tree is built for the frame, so the check cannot pass by
    # accident: --root is isolated, the exit-0 contract holds only honestly.
    root = ok_root(tmp_path)
    proc = _run(["--intent", "verify the cli exit contract",
                 "--target", "tools/cmd_ok.py",
                 "--break", "tests/keep.md",
                 "--defends", "the exit contract in this module's docstring",
                 "--refusal", "BLOCK when the exit code disagrees with the verdict",
                 "--root", str(root)], cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SENTINEL OK" in proc.stdout, proc.stdout


# --------------------------------------------------------------------------- #
# Blocked frame: the subprocess contract, once
# --------------------------------------------------------------------------- #
# The blocked-frame CLI assertions run ONCE, in subprocess text mode, and their
# findings carry to --json mode in the sibling test below. Both earlier shapes
# (in-process main() calling, json-only blocked run) were deleted here: an
# in-process main() and a subprocess still exercise the same return contract,
# and the never-reads-as-OK property is stated against real printed bytes.
def _bad_frame_argv(tmp_path: pathlib.Path):
    return ["--intent", "the situation with the exits",  # S1: topic, not act
            "--target", "tools/ghost.py",               # S2: ghost
            "--break", "prose about the tests",          # S3: no path
            "--refusal", "",                             # S5: nothing
            "--root", str(tmp_path)]


def test_cli_blocked_frame_exits_1_and_never_reads_as_ok(
        tmp_path: pathlib.Path):
    # Exit 0 IFF the verdict is OK: a non-zero exit whose printed prose (or
    # JSON) still reads 'SENTINEL OK' is the one way a reaper lies to a shell
    # pipeline.
    proc = _run(_bad_frame_argv(tmp_path), cwd=tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "SENTINEL BLOCKED" in proc.stdout, proc.stdout
    assert "SENTINEL OK" not in proc.stdout, "exit 1 must never read as OK"


def test_cli_blocked_frame_names_at_least_one_deadly_claim(
        tmp_path: pathlib.Path):
    argv = _bad_frame_argv(tmp_path) + ["--json"]
    proc = _run(argv, cwd=tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "SENTINEL BLOCKED"
    assert payload["problems"], "a BLOCKED verdict must name at least one claim"
    prefixes = [p.split(":", 1)[0] for p in payload["problems"]]
    assert "S1 intent-scoped" in prefixes and "S2 target-resolves" in prefixes


def test_cli_blocked_frame_exits_1_names_what_was_reaped_with_the_deadly_claims(
        tmp_path: pathlib.Path):
    # --json mode: not just exit-1, but pointing at exactly which claim died.
    proc = _run(_bad_frame_argv(tmp_path) + ["--json"], cwd=tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "SENTINEL BLOCKED"
    assert payload["problems"], "a BLOCKED verdict must name at least one claim"
    prefixes = [p.split(":", 1)[0] for p in payload["problems"]]
    assert "S1 intent-scoped" in prefixes and "S2 target-resolves" in prefixes


def test_cli_selftest_exit_0_and_its_json_reports_zero_failures(
        tmp_path: pathlib.Path):
    proc = _run(["--selftest", "--json"], cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    # The positive control must be present BEFORE the mutations: a healthy fixture
    # runs and stays allowed -- otherwise '0 failures' could mean 'ran nothing'.
    assert payload["failures"] == 0
    assert any(l.startswith("PASS healthy control") for l in payload["lines"]), payload["lines"][:2]


# --------------------------------------------------------------------------- #
# Batch mode: --frame-file, one process for many frames
# --------------------------------------------------------------------------- #

def _frame(**overrides):
    frame = {"intent": "verify the cli exit contract",
             "target": "tools/cmd_ok.py",
             "must_not_break": ["tests/keep.md"],
             "defends": ["the exit contract in this module's docstring"],
             "refusal": "BLOCK when the exit code disagrees with the verdict"}
    frame.update(overrides)
    return frame


def test_batch_frame_file_checks_healthy_frames_and_exits_0(tmp_path: pathlib.Path):
    root = ok_root(tmp_path)
    frames = tmp_path / "frames.json"
    frames.write_text(json.dumps({"root": str(root),
                                  "frames": [_frame(), _frame(target="tools/cmd_ok.py",
                                                              defends=["two"])]}),
                      encoding="utf-8")
    proc = _run(["--frame-file", str(frames)], cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("SENTINEL OK") == 2, proc.stdout


def test_batch_names_its_blocked_frame_and_what_was_never_checked(
        tmp_path: pathlib.Path):
    root = ok_root(tmp_path)
    frames = tmp_path / "frames.json"
    frames.write_text(json.dumps({"root": str(root),
                                  "frames": [_frame(), _frame(intent="the situation"),
                                             _frame(target="tools/never_reached.py")]}),
                      encoding="utf-8")
    proc = _run(["--frame-file", str(frames)], cwd=tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    # The blocked frame is named by index, and the frames after it are not silent:
    # the batch says they were never checked.
    assert "frame #1" in proc.stdout, proc.stdout
    assert "SENTINEL BLOCKED" in proc.stdout, proc.stdout
    assert "never checked" in proc.stdout, proc.stdout


def test_batch_json_mode_reports_structured(tmp_path: pathlib.Path):
    root = ok_root(tmp_path)
    frames = tmp_path / "frames.json"
    frames.write_text(json.dumps({"root": str(root),
                                  "frames": [_frame(intent="the situation")]}),
                      encoding="utf-8")
    proc = _run(["--frame-file", str(frames), "--json"], cwd=tmp_path)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "batch"
    assert payload["blocked_at"] == 0
    assert payload["checked"][0]["verdict"] == "SENTINEL BLOCKED"
    assert any(p.startswith("S1 intent-scoped")
               for p in payload["checked"][0]["problems"])


def test_batch_cannot_run_arms_always_exit_2_and_never_read_as_ok(
        tmp_path: pathlib.Path):
    """The exit contract's >=2 arm: an unreadable/unshaped batch is an absence,
    never a pass. Three arms cover the three can't-run shapes: file missing,
    file not JSON, a frame missing a required field."""
    root = ok_root(tmp_path)

    # 1: the file does not exist at all
    proc = _run(["--frame-file", str(tmp_path / "absent.json")], cwd=tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "SENTINEL OK" not in proc.stdout + proc.stderr
    assert "CANNOT RUN" in proc.stderr, proc.stderr

    # 2: the file exists but is not JSON
    not_json = tmp_path / "not.json"
    not_json.write_text("this is prose, not json", encoding="utf-8")
    proc = _run(["--frame-file", str(not_json), "--json"], cwd=tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "SENTINEL OK" not in proc.stdout
    assert "CANNOT RUN" in proc.stderr

    # 3: a frame missing a required field -- the whole batch stops
    bad_frame = _frame()
    del bad_frame["refusal"]
    bad = tmp_path / "missing_field.json"
    bad.write_text(json.dumps({"root": str(root), "frames": [bad_frame]}),
                   encoding="utf-8")
    proc = _run(["--frame-file", str(bad), "--json"], cwd=tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "batch"
    assert "refusal" in payload["error"]
    assert "SENTINEL OK" not in proc.stdout + proc.stderr

    # 4: an empty frame list is cannot-run too -- checking nothing is not checking
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"root": str(root), "frames": []}), encoding="utf-8")
    proc = _run(["--frame-file", str(empty)], cwd=tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
