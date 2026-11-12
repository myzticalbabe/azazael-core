# coding: utf-8
"""SOTA rows EP125 and EP169 — two mechanisms, verified together.

* **EP169** (`redteam.py`) is data plus arithmetic and is exercised in-process.
* **EP125** (`sentinel_enforcer.py`) is exercised in a SUBPROCESS, and that is
  deliberate rather than incidental: importing `CORE.sentinel_enforcer` calls
  `sys.addaudithook()` and installs a deny-write hook for the WHOLE
  interpreter, so importing it in this process would deny every file write
  made by every later test in the session. The child process keeps that side
  effect where it belongs.

Repo convention: tests insert `src/` themselves (`tests/conftest.py` does not).
"""
import json
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# --------------------------------------------------------------------- EP169

def test_design_dimensions_are_data_with_unique_keys():
    from CORE.redteam import DESIGN_DIMENSIONS

    keys = [dim["key"] for dim in DESIGN_DIMENSIONS]
    assert len(keys) == len(set(keys)) >= 5
    for dim in DESIGN_DIMENSIONS:
        assert dim["question"], dim
        assert dim["failure"], dim


def test_unanswered_dimension_is_a_gap_not_a_pass():
    from CORE.redteam import DESIGN_DIMENSIONS, score_dimensions

    all_yes = {dim["key"]: True for dim in DESIGN_DIMENSIONS}
    assert score_dimensions(all_yes)["verdict"] == "CLEARED"

    partial = dict(all_yes)
    dropped = next(iter(partial))
    del partial[dropped]
    result = score_dimensions(partial)
    assert result["verdict"] == "INCOMPLETE"
    assert result["complete"] is False
    assert dropped in result["unanswered"]
    assert dropped not in result["passed"], (
        "an unanswered dimension must not be counted as mitigated — silently "
        "counting it is the failure this shape exists to prevent")


def test_exposed_dimension_outranks_incompleteness():
    from CORE.redteam import DESIGN_DIMENSIONS, score_dimensions

    answers = {dim["key"]: True for dim in DESIGN_DIMENSIONS}
    answers[DESIGN_DIMENSIONS[0]["key"]] = False
    result = score_dimensions(answers)
    assert result["exposed"] is True
    assert result["verdict"] == "EXPOSED"


def test_hack_critic_exposes_the_same_checklist():
    from CORE.redteam import DESIGN_DIMENSIONS, HackCritic

    result = HackCritic().score_dimensions(
        {dim["key"]: True for dim in DESIGN_DIMENSIONS})
    assert result["verdict"] == "CLEARED"


# --------------------------------------------------------------------- EP125

_CHILD = r"""
import json, pathlib, sys, tempfile
sys.path.insert(0, %(src)r)
from CORE import sentinel_enforcer as se

out = {}
tmp = pathlib.Path(tempfile.mkdtemp()) / "guarded.py"

with se.permit_here("test fixture"):
    tmp.write_text("x = 1\n", encoding="utf-8")
out["match_ok"] = se.post_validate(str(tmp), expected="x = 1\n")[0]
out["mismatch_denied"] = se.post_validate(
    str(tmp), expected="x = 2\n")[0] is False

with se.permit_here("test fixture"):
    tmp.write_text("def broken(:\n", encoding="utf-8")
out["unparseable_denied"] = se.post_validate(str(tmp))[0] is False

target = pathlib.Path(tempfile.mkdtemp()) / "landed.py"
ok, msg = se.write_guarded(str(target), "y = 2\n")
out["write_guarded_ok"] = ok
out["write_guarded_landed_matches"] = (
    target.read_text(encoding="utf-8") == "y = 2\n")
out["write_guarded_msg_nonempty"] = bool(msg)
out["post_validate_failures"] = se.get_status()["post_validate_failures"]

print("RESULT " + json.dumps(out))
"""


def _run_child():
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD % {"src": str(_SRC)}],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, "child produced no RESULT line\nstdout=%s\nstderr=%s" % (
        proc.stdout[-2000:], proc.stderr[-2000:])
    return proc, json.loads(lines[-1][len("RESULT "):])


def test_post_edit_guardrail_catches_what_the_pre_edit_gate_cannot():
    proc, result = _run_child()
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert result["match_ok"] is True
    assert result["mismatch_denied"] is True, (
        "content that landed differently from what was authorized must fail: "
        "this is the half the pre-edit gate cannot see")
    assert result["unparseable_denied"] is True


def test_write_guarded_never_reports_success_over_a_wrong_file():
    proc, result = _run_child()
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert result["write_guarded_msg_nonempty"] is True
    if result["write_guarded_ok"]:
        # The contract that matters: it may refuse, but it must never report
        # success while the file on disk is not what was authorized.
        assert result["write_guarded_landed_matches"] is True
    assert result["post_validate_failures"] >= 2, (
        "both violations above must be counted, not just returned")
