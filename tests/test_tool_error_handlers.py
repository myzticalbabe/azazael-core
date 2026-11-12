# coding: utf-8
"""Tests for two error-handler branches in `tools/` that had no coverage.

Both branches exist because a FAILURE was previously indistinguishable from a
healthy ABSENCE:

  1. `engram_supervise.check_hook()` used to swallow a parse error and then
     report `nudge: none due` -- so a supervisor whose output could not be read
     announced itself as a supervisor with nothing to say. The fix makes those
     two states different strings, and the negative controls below are the
     point of this file: if they ever collapse again, these tests fail.
  2. `audit_chain_repair.apply_repair()` used to swallow a failed cleanup of its
     own temp file. A partial repair left on disk is a real artifact, so it is
     now named. The test injects both the write failure and the unlink failure,
     and checks the tool's safety invariant under them: the original file's
     BYTES must be unchanged.

Fixtures are built with the tools' OWN code (`compute_hash` / `serialise`), not a
reimplementation, so a change to the hash recipe moves the fixture with it.
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    """Load a `tools/` module by path (it is not importable as a package here)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


es = _load("engram_supervise")
acr = _load("audit_chain_repair")


# --------------------------------------------------------- engram_supervise

def _hook_output(text):
    """Run `check_hook` with the engram call stubbed, so no subprocess runs."""
    with mock.patch.object(es, "run", lambda *a, **k: text):
        return es.check_hook()


def test_unreadable_hook_output_is_not_reported_as_none_due():
    """A parse failure must be a LOUD state, not an empty one."""
    got = _hook_output("not json at all")
    assert got and got[0].startswith("nudge: UNREADABLE"), got
    assert "JSONDecodeError" in got[0], "the cause must be named, not just the state"


def test_valid_output_with_no_message_is_none_due():
    assert _hook_output('{"systemMessage": ""}') == ["nudge: none due"]


def test_the_two_states_do_not_collapse():
    """The negative control for the pair above.

    This is the regression that matters: the pre-fix code returned the SAME
    answer for both inputs, which is what let a broken supervisor look healthy.
    """
    unreadable = _hook_output("{{{ not json")
    empty = _hook_output('{"systemMessage": ""}')
    assert unreadable != empty
    assert unreadable[0] != empty[0]


def test_a_real_nudge_reports_only_its_first_line():
    got = _hook_output('{"systemMessage": "do this first\\nsecond line"}')
    assert got == ["nudge: do this first"], got


# ------------------------------------------------------- audit_chain_repair

def _torn_fixture(path):
    """A chain with a torn fragment and a genesis-linked record after it.

    Built with the tool's own `compute_hash` and `serialise` so it cannot drift
    away from the recipe it is testing. Written as bytes so the file is LF-only
    and the byte comparison below is exact.
    """
    genesis = "0" * 64
    first = {"type": "probe", "n": 1, "ts": "2026-01-01T00:00:00", "prev_hash": genesis}
    first["hash"] = acr.compute_hash(first, genesis)
    # Chains from genesis even though it is NOT the first record: the symptom.
    second = {"type": "probe", "n": 2, "ts": "2026-01-01T00:00:01", "prev_hash": genesis}
    second["hash"] = acr.compute_hash(second, genesis)
    lines = [acr.serialise(first), '{"type":"torn","partial', acr.serialise(second)]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return path


def _boom_unlink(self, *a, **k):
    raise OSError("simulated unlink failure")


def test_a_failed_cleanup_names_the_leftover_temp_file(tmp_path):
    """Write fails AND the cleanup fails: the artifact must be named."""
    path = _torn_fixture(tmp_path / "fixture_chain.jsonl")
    before = path.read_bytes()
    buf = io.StringIO()

    with mock.patch.object(os, "replace",
                           side_effect=OSError("simulated write failure")), \
         mock.patch.object(pathlib.Path, "unlink", _boom_unlink):
        with contextlib.redirect_stdout(buf):
            rc = acr.apply_repair(path)

    out = buf.getvalue()
    temp = path.with_name(path.name + ".repair_tmp")
    assert rc == acr.EXIT_CANNOT, out
    assert "could not remove the partial temp file" in out, out
    assert str(temp) in out, "the message must name the file it left behind"
    assert temp.exists(), "the test claims a leftover file; it must really exist"
    assert path.read_bytes() == before, "a refused repair must not touch the original"


def test_a_backup_is_written_and_size_verified_before_any_mutation(tmp_path):
    """The safety precondition: refuse to repair what was not first backed up."""
    path = _torn_fixture(tmp_path / "fixture_chain.jsonl")
    original_size = path.stat().st_size
    buf = io.StringIO()

    with mock.patch.object(os, "replace",
                           side_effect=OSError("simulated write failure")), \
         mock.patch.object(pathlib.Path, "unlink", _boom_unlink):
        with contextlib.redirect_stdout(buf):
            acr.apply_repair(path)

    backups = list(tmp_path.glob("fixture_chain.jsonl.pre_repair_*"))
    assert len(backups) == 1, buf.getvalue()
    assert backups[0].stat().st_size == original_size
    assert "size-verified" in buf.getvalue()


def test_a_healthy_chain_is_left_alone(tmp_path):
    """Negative control for the fixture: with nothing torn, the tool must refuse.

    If this ever starts repairing, the two tests above are proving the failure
    branch of a tool that has stopped distinguishing broken from healthy.
    """
    genesis = "0" * 64
    rec = {"type": "probe", "n": 1, "ts": "2026-01-01T00:00:00", "prev_hash": genesis}
    rec["hash"] = acr.compute_hash(rec, genesis)
    # Two records, properly linked: a genuinely valid chain, so this control
    # cannot pass merely because a one-record chain is special-cased.
    nxt = {"type": "probe", "n": 2, "ts": "2026-01-01T00:00:01", "prev_hash": rec["hash"]}
    nxt["hash"] = acr.compute_hash(nxt, rec["hash"])
    path = tmp_path / "healthy.jsonl"
    path.write_bytes((acr.serialise(rec) + "\n" + acr.serialise(nxt) + "\n").encode("utf-8"))
    before = path.read_bytes()
    buf = io.StringIO()

    with contextlib.redirect_stdout(buf):
        rc = acr.apply_repair(path)

    assert rc == acr.EXIT_OK, buf.getvalue()
    assert "no change made" in buf.getvalue(), buf.getvalue()
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.pre_repair_*"))
