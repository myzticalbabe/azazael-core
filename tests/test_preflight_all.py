# coding: utf-8
"""Tests for `tools/preflight_all.py` -- the gate registry, runner and self-test.

The framework's whole claim is that a check which cannot fail is not a check, so
these tests are written the same way: every behaviour is driven to the state it
is supposed to REJECT, not just to the state it is supposed to accept.

Two layers again:

* the registry's relationship to its producer (`.githooks/pre-commit`) is tested
  against the REAL hook, because the failure mode being prevented is drift
  between a table and the gate that actually runs;
* the runner's behaviour is tested against a synthetic gate written into
  `tmp_path`, so "reuse a cached PASS" and "re-run when the gate changed" are
  observable rather than inferred.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import preflight_all as pf  # noqa: E402

#: The hook's inventory, transcribed from `.githooks/pre-commit` on 2026-11-01.
#: This list is meant to be UPDATED when the hook changes -- it is a transcription
#: check, and the failure it exists to catch is the hook gaining a gate that the
#: registry never learns about. It went stale silently for six gates (bible_check,
#: platform_claims, evidence_gate, agent_orders_sync, unresolved_ratchet, and
#: failure_evidence_gate when it landed), so it is now the list the hook actually
#: runs rather than the list it ran when the file was written.
HOOK_SCRIPTS = {
    "Project_ELIZA/tools/shard_intake.py",   # --check; tier 5c. Registered 2026-11-03,
                                             # and the first gate in the set that lives
                                             # outside tools/ or scripts/ -- the derivation
                                             # used to truncate it to tools/shard_intake.py.
    "scripts/commit_gate.py",
    "tools/agent_orders_sync.py",
    "tools/anti_pattern_gate.py",
    "tools/at_risk_register.py",
    "tools/bible_check.py",
    "tools/bible_theme.py",   # --all --check; the themed corpora had no gate at all
                              # until 2026-11-02, so this list could not have
                              # named it -- transcribed with the stanza.
    "tools/canon_drift_check.py",
    "tools/disk_floor_gate.py",   # fast tier 0; a full disk fails as a locked
                                  # database rather than as "disk full", so it
                                  # runs before anything that writes.
    "tools/evidence_gate.py",
    "tools/failure_evidence_gate.py",
    "tools/index_drift_check.py",
    "tools/mesh_function_preflight.py",
    "tools/metthew_families.py",
    "tools/paradigm_gate.py",
    "tools/platform_claims.py",
    "tools/pmb_session_observe.py",
    "tools/preflight_all.py",
    "tools/prompt_check.py",      # fast tier 10b; the instruction files are not
                                  # staged paths, so the commit is the only
                                  # moment "the copies just read are the copies
                                  # being shipped" can be asked.
    "tools/staged_scanner_gate.py",
    "tools/span_receipt_guard.py",   # --selftest; landed in the hook 2026-11-01,
                                     # transcribed here 2026-11-02 (this list went
                                     # one gate stale, which is the failure the
                                     # parity check exists to catch).
    "tools/supervisor_mcp.py",
    "tools/test_paradigm_gate.py",
    "tools/transcript_ap5_gate.py",
    "tools/unresolved_ratchet.py",
    "tools/wire_evidence_audit.py",
}


# ── the registry against its producer ────────────────────────────────────


class TestHookParity:

    def test_the_real_hook_is_transcribed_exactly(self):
        text = pf.read_hook()
        assert text is not None, "the hook must be readable; it is tracked"
        assert set(pf.hook_gate_scripts(text)) == HOOK_SCRIPTS

    def test_a_script_named_only_in_prose_is_not_counted_as_a_gate(self):
        """The hook's header mentions `tools/install_git_hooks.py` in a comment."""
        text = pf.read_hook()
        assert text is not None
        assert "install_git_hooks" in text, "premise of this test"
        assert "tools/install_git_hooks.py" not in pf.hook_gate_scripts(text)

    def test_registry_and_hook_agree(self):
        ok, message = pf.hook_parity(pf.GATES, pf.read_hook())
        assert ok, message
        # The count is asserted against the registry's own inside_hook rows rather
        # than a number typed here: a hardcoded "12 gates" outlived the registry it
        # described and turned a correct message into a failure.
        inside_hook = sum(1 for gate in pf.GATES if gate.inside_hook)
        assert "%d gates" % inside_hook in message, message

    def test_an_unreadable_hook_is_missing_evidence_not_a_pass(self):
        ok, message = pf.hook_parity(pf.GATES, None)
        assert not ok
        assert "MISSING_EVIDENCE" in message

    def test_a_registered_but_unrun_gate_is_reported_as_drift(self):
        rogue = pf.Gate(name="rogue", argv=("tools/not_in_hook.py",), interpreter="python",
                        env=(), inside_hook=True, blocking=True, slow=False, reads="x",
                        control_test=None, control=None, uncontrolled_because="synthetic")
        ok, message = pf.hook_parity((*pf.GATES, rogue), pf.read_hook())
        assert not ok
        assert "tools/not_in_hook.py" in message

    def test_a_gate_the_hook_runs_but_the_registry_omits_is_reported_as_drift(self):
        hook = '#!/bin/sh\npython "$SCRIPT_DIR/tools/unregistered.py"\n'
        ok, message = pf.hook_parity(pf.GATES, hook)
        assert not ok
        assert "tools/unregistered.py" in message


class TestRegistryIntegrity:

    @pytest.mark.parametrize("gate", pf.GATES, ids=lambda g: g.name)
    def test_every_registered_script_exists_on_disk(self, gate):
        assert (pf.REPO / gate.argv[0]).is_file(), gate.argv[0]

    @pytest.mark.parametrize("gate", pf.GATES, ids=lambda g: g.name)
    def test_an_inside_hook_gate_is_attributed_to_the_hook(self, gate):
        if gate.inside_hook:
            assert (pf.REPO / gate.argv[0]).is_file()

    def test_hook_non_blocking_gates_are_exactly_the_advisory_ones(self):
        """The hook's own contract: only advisory/side-effect gates ignore their exit code."""
        non_blocking = sorted(g.name for g in pf.GATES if g.inside_hook and not g.blocking)
        assert non_blocking == ["pmb-outcome-observer", "testing-paradigm-advisory", "transcript-ap5"]

    def test_names_are_unique(self):
        names = [g.name for g in pf.GATES]
        assert len(names) == len(set(names))

    def test_every_gate_states_what_it_reads(self):
        for gate in pf.GATES:
            assert gate.reads.strip(), gate.name

    def test_a_gate_with_no_control_gives_a_reason(self):
        for gate in pf.GATES:
            if not gate.control and not gate.control_test:
                assert gate.uncontrolled_because, gate.name


# ── interpreters ─────────────────────────────────────────────────────────


class TestInterpreters:

    def test_the_pmb_venv_interpreter_honours_the_override(self, tmp_path, monkeypatch):
        fake = tmp_path / "py.exe"
        fake.write_text("", encoding="utf-8")
        monkeypatch.setenv("PMB_VENV_PYTHON", str(fake))
        assert pf.resolve_interpreter("pmb-venv") == str(fake)

    def test_an_unknown_interpreter_name_does_not_resolve(self, monkeypatch):
        assert pf.resolve_interpreter("definitely-not-an-interpreter") is None

    def test_probing_reports_the_real_version_not_just_a_resolved_name(self):
        path = pf.resolve_interpreter("python")
        assert path is not None
        ok, info = pf.probe_interpreter(path)
        assert ok
        assert "v" in info

    def test_probing_a_non_executable_fails_rather_than_passing(self, tmp_path):
        junk = tmp_path / "junk"
        junk.write_text("not an interpreter", encoding="utf-8")
        ok, info = pf.probe_interpreter(str(junk))
        assert not ok
        assert info


# ── the evidence contract ────────────────────────────────────────────────


class TestFingerprint:

    def _gate(self):
        return pf.Gate(name="x", argv=("tools/index_drift_check.py",), interpreter="python",
                       env=(), inside_hook=False, blocking=True, slow=False, reads="x",
                       control_test=None, control=None, uncontrolled_because="synthetic")

    def test_the_same_inputs_fingerprint_the_same(self):
        gate = self._gate()
        assert pf.gate_fingerprint(gate, "py", "state") == pf.gate_fingerprint(gate, "py", "state")

    def test_a_different_repo_state_changes_the_fingerprint(self):
        """A cached PASS recorded against another tree must not be reused."""
        gate = self._gate()
        assert pf.gate_fingerprint(gate, "py", "state-a") != pf.gate_fingerprint(gate, "py", "state-b")

    def test_a_missing_repo_state_is_its_own_input(self):
        gate = self._gate()
        assert pf.gate_fingerprint(gate, "py", None) != pf.gate_fingerprint(gate, "py", "state")

    def test_a_different_interpreter_changes_the_fingerprint(self):
        gate = self._gate()
        assert pf.gate_fingerprint(gate, "py", "s") != pf.gate_fingerprint(gate, "py3", "s")

    def test_the_real_repo_state_resolves_inside_a_repo(self):
        assert pf.repo_state_hash() is not None

    def test_head_is_part_of_the_state_hash(self, monkeypatch):
        """Two CLEAN worktrees differ only by HEAD - the hash must notice.

        Before 2026-10-31 it did not: the two diffs were the whole input, so a
        run on a clean worktree hashed two empty diffs, and so did the same
        worktree one commit later. A cached PASS recorded at the earlier commit
        was therefore reused for a tree no gate had judged. This pins the fix
        without moving the real HEAD: only the rev-parse call is faked, every
        other git call still runs against the real repo.
        """
        import subprocess as _sp
        real_run = _sp.run

        def _with_head(sha):
            def fake(argv, *a, **kw):
                if list(argv)[:3] == ["git", "rev-parse", "HEAD"]:
                    return _sp.CompletedProcess(argv, 0, stdout=sha, stderr=b"")
                return real_run(argv, *a, **kw)
            return fake

        monkeypatch.setattr(pf.subprocess, "run", _with_head(b"deadbeef\n"))
        first = pf.repo_state_hash()
        monkeypatch.setattr(pf.subprocess, "run", _with_head(b"cafebabe\n"))
        second = pf.repo_state_hash()
        assert first and second and first != second

    def test_repo_state_is_none_outside_a_repo(self, tmp_path, monkeypatch):
        """`GIT_CEILING_DIRECTORIES` stops git walking up out of `tmp_path`.

        Without it, this test asserts a property of wherever pytest's tmp dir
        happens to live rather than a property of the code: an enclosing repo is
        exactly why the first version of this test failed.
        """
        monkeypatch.setattr(pf, "REPO", tmp_path)
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
        assert pf.repo_state_hash() is None


# ── the runner, against a synthetic gate ─────────────────────────────────


def _synthetic(tmp_path, monkeypatch, *, exit_code=0, blocking=True, interpreter="python"):
    """A real (tiny) gate script in a throwaway repo root.

    The gate reads its own exit code from a sibling `.code` file, so a test can
    change the gate's VERDICT without touching the script's bytes -- which is
    what makes "was it reused or re-run?" observable.
    """
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    script = tmp_path / "tools" / "fake_gate.py"
    script.write_text(
        "import pathlib, sys\n"
        "code = pathlib.Path(__file__).with_suffix('.code')\n"
        "sys.exit(int(code.read_text(encoding='utf-8').strip()))\n",
        encoding="utf-8",
    )
    (tmp_path / "tools" / "fake_gate.code").write_text(str(exit_code), encoding="utf-8")
    hook = tmp_path / ".githooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text('#!/bin/sh\npython "$SCRIPT_DIR/tools/fake_gate.py"\n', encoding="utf-8")
    monkeypatch.setattr(pf, "REPO", tmp_path)
    monkeypatch.setattr(pf, "HOOK", hook)
    monkeypatch.setattr(pf, "EVIDENCE", tmp_path / "logs" / "evidence.json")
    monkeypatch.setattr(pf, "repo_state_hash", lambda: "STATE")
    gate = pf.Gate(name="fake", argv=("tools/fake_gate.py",), interpreter=interpreter,
                   env=(), inside_hook=True, blocking=blocking, slow=False, reads="synthetic",
                   control_test=None, control=None, uncontrolled_because="synthetic")
    return gate, script


class TestCheck:

    def test_a_passing_gate_exits_zero_and_records_evidence(self, tmp_path, monkeypatch):
        gate, _ = _synthetic(tmp_path, monkeypatch)
        assert pf.main(["--check"], gates=(gate,)) == 0
        evidence = pf.load_evidence()
        assert evidence["fake"]["exit_code"] == 0
        assert evidence["fake"]["fingerprint"]

    def test_a_failing_gate_exits_one_and_records_the_nonzero_exit(self, tmp_path, monkeypatch):
        gate, _ = _synthetic(tmp_path, monkeypatch, exit_code=1)
        assert pf.main(["--check"], gates=(gate,)) == 1
        assert pf.load_evidence()["fake"]["exit_code"] == 1

    def test_a_matching_cached_pass_is_reused_without_re_running(self, tmp_path, monkeypatch, capsys):
        """The gate's verdict is FLIPPED to fail; a reused PASS still exits 0.

        That is the only way to observe reuse rather than infer it -- and it is
        also the reason the next two tests exist.
        """
        gate, script = _synthetic(tmp_path, monkeypatch, exit_code=0)
        assert pf.main(["--check"], gates=(gate,)) == 0
        (script.with_suffix(".code")).write_text("1", encoding="utf-8")
        assert pf.main(["--check"], gates=(gate,)) == 0
        assert "CACHED" in capsys.readouterr().out

    def test_force_re_runs_and_finds_the_flipped_verdict(self, tmp_path, monkeypatch):
        gate, script = _synthetic(tmp_path, monkeypatch, exit_code=0)
        assert pf.main(["--check"], gates=(gate,)) == 0
        (script.with_suffix(".code")).write_text("1", encoding="utf-8")
        assert pf.main(["--check", "--force"], gates=(gate,)) == 1

    def test_editing_the_gate_invalidates_its_cached_pass(self, tmp_path, monkeypatch):
        gate, script = _synthetic(tmp_path, monkeypatch, exit_code=0)
        assert pf.main(["--check"], gates=(gate,)) == 0
        (script.with_suffix(".code")).write_text("1", encoding="utf-8")
        # A gate edited after its last green run must not inherit that green.
        script.write_text(script.read_text(encoding="utf-8") + "# a real change\n",
                          encoding="utf-8")
        assert pf.main(["--check"], gates=(gate,)) == 1

    def test_an_absent_repo_state_prevents_reuse(self, tmp_path, monkeypatch, capsys):
        gate, script = _synthetic(tmp_path, monkeypatch, exit_code=0)
        monkeypatch.setattr(pf, "repo_state_hash", lambda: None)
        assert pf.main(["--check"], gates=(gate,)) == 0
        (script.with_suffix(".code")).write_text("1", encoding="utf-8")
        # No tree hash means no evidence that the PASS still describes the tree.
        assert pf.main(["--check"], gates=(gate,)) == 1
        assert "CACHED" not in capsys.readouterr().out

    def test_an_unrunnable_blocking_gate_fails(self, tmp_path, monkeypatch, capsys):
        gate, _ = _synthetic(tmp_path, monkeypatch, interpreter="definitely-not-an-interpreter")
        assert pf.main(["--check"], gates=(gate,)) == 1
        assert "UNRUNNABLE" in capsys.readouterr().out

    def test_an_unrunnable_non_blocking_gate_does_not_fail(self, tmp_path, monkeypatch):
        gate, _ = _synthetic(tmp_path, monkeypatch, blocking=False,
                             interpreter="definitely-not-an-interpreter")
        assert pf.main(["--check"], gates=(gate,)) == 0

    def test_a_declared_unrunnable_gate_is_honoured(self, tmp_path, monkeypatch, capsys):
        gate, _ = _synthetic(tmp_path, monkeypatch, interpreter="definitely-not-an-interpreter")
        assert pf.main(["--check", "--allow-unrunnable", "fake"], gates=(gate,)) == 0
        assert "DECLARED UNRUNNABLE" in capsys.readouterr().out

    def test_registry_drift_stops_the_run(self, tmp_path, monkeypatch):
        gate, _ = _synthetic(tmp_path, monkeypatch)
        (tmp_path / ".githooks" / "pre-commit").write_text(
            "#!/bin/sh\npython \"$SCRIPT_DIR/tools/somewhere_else.py\"\n", encoding="utf-8")
        assert pf.main(["--check"], gates=(gate,)) == 1


# ── the self-test ────────────────────────────────────────────────────────


class TestSelfTest:

    def test_the_real_registry_self_tests_green(self):
        assert pf.cmd_self_test(pf.GATES) == 0

    def test_the_real_self_test_names_the_typescript_interpreter_divergence(self, capsys):
        """The measured fact: check 1 runs on a different interpreter than 1b-5."""
        pf.cmd_self_test(pf.GATES)
        out = capsys.readouterr().out
        assert "interpreters actually execute" in out

    def test_a_control_that_does_not_fail_is_a_hard_failure(self, monkeypatch, capsys):
        monkeypatch.setitem(pf.CONTROLS, "anti-pattern-ratchet",
                            lambda interpreter: (False, "control did NOT fail"))
        assert pf.cmd_self_test(pf.GATES) == 1
        assert "control did NOT fail" in capsys.readouterr().out

    def test_a_missing_control_artifact_is_a_hard_failure(self, monkeypatch, capsys):
        bogus = pf.Gate(name="bogus", argv=("tools/preflight_all.py",), interpreter="python",
                        env=(), inside_hook=False, blocking=True, slow=True, reads="x",
                        control_test="tests/never_written.py", control=None,
                        uncontrolled_because=None)
        assert pf.cmd_self_test((*pf.GATES, bogus)) == 1
        assert "tests/never_written.py" in capsys.readouterr().out

    def test_an_unreadable_hook_fails_the_self_test(self, monkeypatch, capsys):
        monkeypatch.setattr(pf, "read_hook", lambda: None)
        assert pf.cmd_self_test(pf.GATES) == 1
        assert "MISSING_EVIDENCE" in capsys.readouterr().out

    def test_a_named_control_with_no_implementation_is_a_failure(self, monkeypatch, capsys):
        bogus = pf.Gate(name="bogus", argv=("tools/preflight_all.py",), interpreter="python",
                        env=(), inside_hook=False, blocking=True, slow=True, reads="x",
                        control_test=None, control="not-implemented", uncontrolled_because=None)
        assert pf.cmd_self_test((*pf.GATES, bogus)) == 1
        assert "not-implemented" in capsys.readouterr().out

    def test_gates_with_no_control_are_counted_not_hidden(self, capsys):
        pf.cmd_self_test(pf.GATES)
        out = capsys.readouterr().out
        assert "nothing declared" in out
        assert "is NOT a demonstrated one" in out


# ── the CLI surface ──────────────────────────────────────────────────────


class TestCLI:

    def test_list_reports_every_gate(self, capsys):
        assert pf.cmd_list(pf.GATES, as_json=False) == 0
        out = capsys.readouterr().out
        for gate in pf.GATES:
            assert gate.name in out

    def test_list_as_json_is_parseable(self, capsys):
        assert pf.cmd_list(pf.GATES, as_json=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert {row["name"] for row in payload} == {g.name for g in pf.GATES}
        assert all("reads" in row for row in payload)

    def test_json_without_list_is_a_usage_error(self):
        assert pf.main(["--json"]) == 2

    def test_list_and_check_are_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            pf.main(["--list", "--check"])

    def test_list_json_carries_the_required_artifacts(self, capsys):
        assert pf.cmd_list(pf.GATES, as_json=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert all("requires" in row for row in payload)
        assert any(row["requires"] for row in payload)

    def test_self_test_only_flags_are_rejected_without_self_test(self):
        assert pf.main(["--check", "--run-controls"]) == 2
        assert pf.main(["--check", "--strict"]) == 2
        assert pf.main(["--list", "--allow-blind", "x"]) == 2


# ── BLIND: a gate whose required artifact is gone cannot judge ───────────


def _blind_harness(tmp_path, monkeypatch, requires, control_test=None, name="needy"):
    """A registry whose hook runs nothing, so parity passes with one gate."""
    hook = tmp_path / ".githooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(pf, "REPO", tmp_path)
    monkeypatch.setattr(pf, "HOOK", hook)
    monkeypatch.setattr(pf, "EVIDENCE", tmp_path / "logs" / "evidence.json")
    gate = pf.Gate(name=name, argv=("tools/preflight_all.py",), interpreter="python",
                   env=(), inside_hook=False, blocking=True, slow=True, reads="synthetic",
                   control_test=control_test, control=None,
                   uncontrolled_because="synthetic", requires=requires)
    return gate


class TestBlind:

    def test_an_absent_required_artifact_is_reported_blind(self, tmp_path, monkeypatch, capsys):
        gate = _blind_harness(tmp_path, monkeypatch, ("logs/never_recorded.json",))
        assert pf.cmd_self_test((gate,)) == 0
        out = capsys.readouterr().out
        assert "BLIND" in out
        assert "logs/never_recorded.json" in out

    def test_a_present_required_artifact_is_not_blind(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "logs" / "there.json").write_text("{}", encoding="utf-8")
        gate = _blind_harness(tmp_path, monkeypatch, ("logs/there.json",))
        assert pf.cmd_self_test((gate,)) == 0
        assert "BLIND" not in capsys.readouterr().out

    def test_strict_promotes_blind_to_a_failure_naming_the_path(self, tmp_path, monkeypatch, capsys):
        gate = _blind_harness(tmp_path, monkeypatch, ("logs/never_recorded.json",))
        assert pf.cmd_self_test((gate,), strict=True) == 1
        out = capsys.readouterr().out
        assert "BLIND" in out and "logs/never_recorded.json" in out

    def test_allow_blind_declares_it_but_never_hides_it(self, tmp_path, monkeypatch, capsys):
        gate = _blind_harness(tmp_path, monkeypatch, ("logs/never_recorded.json",))
        assert pf.cmd_self_test((gate,), strict=True, allow_blind=("needy",)) == 0
        out = capsys.readouterr().out
        assert "BLIND" in out, "declaring it must not delete the fact"
        assert "--allow-blind" in out

    def test_the_summary_reports_the_blind_count(self, capsys):
        pf.cmd_self_test(pf.GATES)
        assert "blind" in capsys.readouterr().out

    @pytest.mark.parametrize("gate", pf.GATES, ids=lambda g: g.name)
    def test_required_artifacts_are_repo_relative(self, gate):
        for rel in gate.requires:
            assert not Path(rel).is_absolute(), rel
            assert ":" not in rel, rel

    def test_the_gates_that_read_a_baseline_declare_one(self):
        by_name = {g.name: g for g in pf.GATES}
        for name in ("anti-pattern-ratchet", "mesh-ledger-freshness",
                     "two-interpreter-suite", "aggregate-suite-isolation", "type-check"):
            assert by_name[name].requires, name

    def test_gates_that_read_only_git_declare_no_file(self):
        """Their input is the index, which `repo_state_hash` covers instead."""
        by_name = {g.name: g for g in pf.GATES}
        for name in ("changeset-focus", "stale-index-guard", "staged-scanner"):
            assert by_name[name].requires == (), name


# ── --run-controls: execute the declared artifact ───────────────────────


def _control_artifact(tmp_path, body):
    d = tmp_path / "tests"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "test_ctl_fixture.py"
    with p.open("w", encoding="utf-8") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    return "tests/test_ctl_fixture.py"


class TestRunControls:

    def test_a_passing_control_artifact_is_demonstrated(self, tmp_path, monkeypatch, capsys):
        control = _control_artifact(tmp_path, "def test_ok():\n    assert True\n")
        gate = _blind_harness(tmp_path, monkeypatch, (), control_test=control)
        assert pf.cmd_self_test((gate,), run_controls=True) == 0
        assert "OK" in capsys.readouterr().out

    def test_a_failing_control_artifact_is_a_hard_failure(self, tmp_path, monkeypatch, capsys):
        """The only way the framework can notice a control that stopped working."""
        control = _control_artifact(tmp_path, "def test_bad():\n    assert False\n")
        gate = _blind_harness(tmp_path, monkeypatch, (), control_test=control)
        assert pf.cmd_self_test((gate,), run_controls=True) == 1
        out = capsys.readouterr().out
        assert "FAIL" in out and control in out

    def test_a_missing_interpreter_fails_the_control_run(self, tmp_path, monkeypatch, capsys):
        control = _control_artifact(tmp_path, "def test_ok():\n    assert True\n")
        gate = _blind_harness(tmp_path, monkeypatch, (), control_test=control)
        gate = gate._replace(interpreter="definitely-not-an-interpreter")
        assert pf.cmd_self_test((gate,), run_controls=True) == 1
        assert "interpreter" in capsys.readouterr().out

    def test_controls_are_not_run_unless_asked(self, tmp_path, monkeypatch, capsys):
        control = _control_artifact(tmp_path, "def test_bad():\n    assert False\n")
        gate = _blind_harness(tmp_path, monkeypatch, (), control_test=control)
        assert pf.cmd_self_test((gate,)) == 0
        assert "EXECUTED" not in capsys.readouterr().out

    def test_the_summary_does_not_call_a_run_control_undeclared(
        self, tmp_path, monkeypatch, capsys
    ):
        """After the run, the old 'NOT a demonstrated one' line is a stale claim."""
        control = _control_artifact(tmp_path, "def test_ok():\n    assert True\n")
        gate = _blind_harness(tmp_path, monkeypatch, (), control_test=control)
        pf.cmd_self_test((gate,), run_controls=True)
        out = capsys.readouterr().out
        assert "NOT a demonstrated one" not in out
        assert "demonstrated for THIS run" in out

    # The global per-test bound is 120 s (pytest.ini, and golden_arrow passes 120
    # on the command line). Measured 2026-11-02: 59.9 s alone, 119.8 s with six
    # test files running in parallel -- i.e. the harness killed this test at the
    # boundary and reported a failure/error for a suite that had not asserted
    # anything yet. The budget is stated here rather than raising the global bound,
    # so one genuinely wedged test cannot hold the other 500 files for 5 minutes.
    @pytest.mark.timeout(300)
    def test_every_real_control_artifact_executes_green(self):
        """The whole point of item 2: fail-ability DEMONSTRATED, not declared.

        Slower than the rest of this file (it runs eight suites), and worth it:
        this is the assertion that would go red the moment a gate's control
        stopped exercising the gate.
        """
        assert pf.cmd_self_test(pf.GATES, run_controls=True) == 0
