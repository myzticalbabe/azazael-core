# coding: utf-8
"""The four data-flow rules, each with a passing control beside its refusal.

Why this file exists
--------------------
A policy that refuses everything looks identical to a policy that works, from the
refusal log. Every rule here is therefore tested **twice**: once with tainted
input (must refuse, naming the rule and the argument) and once with the same call
in trusted form (must allow). If the second half ever fails, the gate has become
a wall and the first half stops being evidence of anything.

Rule → test map:
  P1 control-integrity      tainted selection of an acting tool
  P2 tainted-argument       tainted value into NET_OUT/EXEC/FS_WRITE/MSG_SEND
  P3 tainted-destination    any tool whose url/path/recipient came from a source
  P4 exec-purity            no tainted argv, ever
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.camel.capabilities import Labeled, TRUST, ToolCapability, origin  # noqa: E402
from CORE.camel.policy import (  # noqa: E402
    DESTINATION_ARGS,
    Policy,
    PolicyViolation,
    ToolSpec,
    default_policy,
)

PAGE = "https://evil.test/page"


def _tainted(value="from the page"):
    return Labeled.untrusted(value, PAGE)


class TestRule2TaintedArgument:
    def test_acting_tool_refuses_tainted_data(self):
        policy = default_policy()
        verdict = policy.evaluate("message.board_post", {"content": _tainted()})
        assert not verdict.allowed
        assert verdict.rule == "tainted-argument"
        assert verdict.tainted_args == ("content",)
        assert PAGE in verdict.reason

    def test_same_call_with_clean_data_is_allowed(self):
        """The control: rule P2 is not refusing the tool, it is refusing taint."""
        policy = default_policy()
        verdict = policy.evaluate("message.board_post",
                                  {"content": Labeled.trust("our own words")})
        assert verdict.allowed, verdict.reason

    def test_declared_data_field_may_carry_taint(self):
        """Recording a quote is the point; the field is declared as data."""
        policy = default_policy()
        verdict = policy.evaluate("memory.record",
                                  {"content": _tainted("a quoted sentence")})
        assert verdict.allowed, verdict.reason

    def test_a_reading_tool_accepts_tainted_input(self):
        policy = default_policy()
        verdict = policy.evaluate("memory.recall", {"query": _tainted()})
        assert verdict.allowed, verdict.reason


class TestRule3TaintedDestination:
    def test_even_a_read_only_fetch_refuses_a_tainted_url(self):
        """The first half of the attack: make the agent fetch the attacker's URL."""
        policy = default_policy()
        verdict = policy.evaluate("web_search.fetch", {"url": _tainted(PAGE)})
        assert not verdict.allowed
        assert verdict.rule == "tainted-destination"
        assert "url" in verdict.reason

    def test_clean_url_is_allowed(self):
        policy = default_policy()
        verdict = policy.evaluate("web_search.fetch",
                                  {"url": Labeled.trust("https://example.test/")})
        assert verdict.allowed, verdict.reason

    def test_destination_beats_the_data_exemption(self):
        """`memory.record` may quote, but it may not be told where to write."""
        policy = default_policy()
        verdict = policy.evaluate(
            "memory.record",
            {"content": _tainted("quote"), "path": _tainted("/tmp/pwned")})
        assert not verdict.allowed
        assert verdict.rule == "tainted-destination"

    def test_a_spec_cannot_opt_a_destination_into_taint(self):
        with pytest.raises(ValueError, match="destination"):
            ToolSpec("bad.tool",
                     capabilities=frozenset({ToolCapability.NET_OUT}),
                     tainted_args_allowed=frozenset({"url"}))

    def test_destination_names_are_matched_by_name(self):
        for name in ("url", "recipient", "command", "path", "repo", "topic"):
            assert name in DESTINATION_ARGS


class TestRule1ControlIntegrity:
    def test_tainted_selection_of_an_acting_tool_is_refused(self):
        policy = default_policy()
        verdict = policy.evaluate("shell.run", {"command": Labeled.trust("ls")},
                                  selection=origin(PAGE))
        assert not verdict.allowed
        assert verdict.rule == "control-integrity"

    def test_tainted_selection_of_a_reading_tool_is_allowed(self):
        """The control: P1 guards authority, not curiosity."""
        policy = default_policy()
        verdict = policy.evaluate("memory.recall",
                                  {"query": Labeled.trust("lawsuits")},
                                  selection=origin(PAGE))
        assert verdict.allowed, verdict.reason

    def test_clean_selection_of_an_acting_tool_is_allowed(self):
        policy = default_policy()
        verdict = policy.evaluate("shell.run", {"command": Labeled.trust("ls")})
        assert verdict.allowed, verdict.reason


class TestRule4ExecPurity:
    def test_any_tainted_argv_is_refused(self):
        policy = default_policy()
        verdict = policy.evaluate("shell.run",
                                  {"command": Labeled.trust("echo"),
                                   "arg": _tainted("; rm -rf /")})
        assert not verdict.allowed
        assert verdict.rule == "exec-purity"

    def test_clean_argv_is_allowed(self):
        policy = default_policy()
        verdict = policy.evaluate("shell.run",
                                  {"command": Labeled.trust("echo"),
                                   "arg": Labeled.trust("hello")})
        assert verdict.allowed, verdict.reason


class TestUnknownTools:
    def test_an_unregistered_tool_is_acting_authority(self):
        policy = Policy()
        verdict = policy.evaluate("something.new", {"payload": _tainted()})
        assert not verdict.allowed
        assert verdict.rule == "tainted-argument"
        assert "unregistered" in verdict.reason

    def test_an_unregistered_tool_with_clean_arguments_runs(self):
        """Fail closed must mean 'on taint', not 'on anything unseen'."""
        policy = Policy()
        assert policy.evaluate("something.new",
                               {"payload": Labeled.trust("x")}).allowed

    def test_allow_unknown_is_opt_in(self):
        policy = Policy(allow_unknown=True)
        policy.register(ToolSpec("x"))  # nothing: allow_unknown still applies
        assert policy.evaluate("unregistered.pure",
                               {"v": Labeled.trust(1)}).allowed


class TestCheckAndAudit:
    def test_check_raises_with_the_verdict_attached(self):
        policy = default_policy()
        with pytest.raises(PolicyViolation) as info:
            policy.check("message.board_post", {"content": _tainted()})
        assert info.value.verdict.rule == "tainted-argument"
        assert "REFUSE" in info.value.verdict.line()

    def test_history_records_allowed_and_refused(self):
        policy = default_policy()
        policy.evaluate("memory.recall", {"query": Labeled.trust("q")})
        policy.evaluate("message.board_post", {"content": _tainted()})
        stats = policy.stats()
        assert stats["evaluated"] == 2
        assert stats["refused"] == 1
        assert stats["by_rule"] == {"tainted-argument": 1}
        assert len(policy.refusals()) == 1

    def test_duplicate_registration_is_an_error_not_a_silent_replace(self):
        policy = Policy()
        policy.register(ToolSpec("t"))
        with pytest.raises(ValueError):
            policy.register(ToolSpec("t"))

    def test_verdict_carries_origins_for_reproduction(self):
        policy = default_policy()
        verdict = policy.evaluate("message.board_post", {"content": _tainted()})
        assert verdict.origins == (PAGE,)
        assert verdict.selection == "trusted"
