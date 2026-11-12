# coding: utf-8
"""Interpreter controls: tainted data cannot branch, and every call is policed.

Why this file exists
--------------------
An instruction in a web page is only dangerous at the moment it changes what the
agent *does*. The interpreter is where that moment is observable, so the tests
that matter are:

* a tainted value used as a condition raises, whether it reached the condition
  directly, through an f-string, through `len()`, or through an if-expression —
  the four ways a "clever" implementation leaks one bit of control;
* a tool call with a tainted argument is refused **before the tool body runs**
  (the spy asserts zero calls, because a refusal that still executes is a log
  entry, not a control);
* the same programs, fed trusted input, run and return the expected value. That
  second half is not decoration: an interpreter that raises on everything would
  satisfy the first half perfectly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.camel.capabilities import (  # noqa: E402
    Labeled,
    ToolCapability,
    origin,
)
from CORE.camel.interpreter import (  # noqa: E402
    BudgetExceeded,
    CamelError,
    Interpreter,
    NameError_,
    TaintedControlFlow,
    UnsupportedSyntax,
    run,
)
from CORE.camel.policy import Policy, PolicyViolation, ToolSpec, default_policy  # noqa: E402

PAGE = "https://evil.test/page"


class Spy:
    """A tool body that records its calls, so 'refused' can be proven."""

    def __init__(self, returns=None):
        self.calls: list[dict] = []
        self._returns = returns

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self._returns if self._returns is not None else "ok"


def _interpreter(*specs):
    interpreter = Interpreter(policy=default_policy())
    spies = {}
    for spec in specs:
        spies[spec.name] = Spy()
        interpreter.register_tool(spec, spies[spec.name])
    return interpreter, spies


class TestCleanProgramsRun:
    def test_arithmetic(self):
        result = Interpreter().execute("total = 1 + 2\ntotal")
        assert result.output.value == 3
        assert not result.tainted

    def test_a_loop_over_a_trusted_list(self):
        result = Interpreter().execute(
            "acc = 0\nfor n in [1, 2, 3]:\n    acc = acc + n\nacc")
        assert result.output.value == 6

    def test_a_string_program(self):
        result = Interpreter().execute('greeting = "hello"\ngreeting + "!"')
        assert result.output.value == "hello!"

    def test_condition_on_a_trusted_value(self):
        result = Interpreter().execute(
            "size = len([1, 2])\nif size > 1:\n    verdict = \"big\"\nelse:\n    verdict = \"small\"\nverdict")
        assert result.output.value == "big"

    def test_a_read_only_tool_call_is_allowed_and_runs_once(self):
        spec = ToolSpec("memory.recall", tainted_args_allowed=frozenset({"query"}))
        interpreter, spies = _interpreter(spec)
        result = interpreter.execute('rows = memory_recall(query="suits")\nrows')
        assert spies["memory.recall"].calls == [{"query": "suits"}]
        assert result.output.value == "ok"
        assert len(result.calls) == 1
        assert result.calls[0]["allowed"] is True


class TestControlFlowIntegrity:
    @pytest.mark.parametrize("program", [
        'if page == "go":\n    n = 1\nn',
        'if "delete" in page:\n    n = 1\nn',
        'n = 1 if page else 2\nn',
        'rows = [x for x in [1, 2] if page]\nrows',
        'n = 0\nwhile page and n < 3:\n    n = n + 1\nn',
    ])
    def test_a_tainted_condition_raises(self, program):
        interpreter = Interpreter()
        with pytest.raises(TaintedControlFlow) as info:
            interpreter.execute(program, {"page": Labeled.untrusted("go", PAGE)})
        assert info.value.label.origins == frozenset({PAGE})
        assert "may not decide control flow" in str(info.value)

    def test_taint_reaches_the_condition_through_an_fstring(self):
        with pytest.raises(TaintedControlFlow):
            Interpreter().execute('label = f"{page}!"\nif label:\n    n = 1\nn',
                                  {"page": Labeled.untrusted("x", PAGE)})

    def test_taint_reaches_the_condition_through_len(self):
        """One bit of the page is still the page."""
        with pytest.raises(TaintedControlFlow):
            Interpreter().execute('if len(page) > 3:\n    n = 1\nn',
                                  {"page": Labeled.untrusted("long text", PAGE)})

    def test_the_same_programs_run_with_trusted_input(self):
        """The control: refusal is about taint, not about the shape of the code."""
        result = Interpreter().execute('if page == "go":\n    n = 1\nelse:\n    n = 0\nn',
                                       {"page": Labeled.trust("go")})
        assert result.output.value == 1
        result = Interpreter().execute('label = f"{page}!"\nif label:\n    n = 7\nn',
                                       {"page": Labeled.trust("x")})
        assert result.output.value == 7

    def test_a_condition_is_evaluated_once(self):
        """`if fetch():` must not call the tool twice and branch once."""
        spec = ToolSpec("memory.recall")
        interpreter, spies = _interpreter(spec)
        interpreter.execute('if memory_recall(query="q"):\n    n = 1\nn')
        assert len(spies["memory.recall"].calls) == 1


class TestPolicyCheckedCalls:
    def test_a_tainted_argument_never_reaches_the_tool_body(self):
        spec = ToolSpec("message.board_post",
                        capabilities=frozenset({ToolCapability.MSG_SEND}))
        interpreter, spies = _interpreter(spec)
        with pytest.raises(PolicyViolation) as info:
            interpreter.execute('message_board_post(content=page)',
                                {"page": Labeled.untrusted("secret", PAGE)})
        assert info.value.verdict.rule == "tainted-argument"
        assert spies["message.board_post"].calls == [], (
            "the tool ran despite the refusal: a refusal that still executes is "
            "a log line, not a control")

    def test_a_clean_call_to_the_same_tool_runs(self):
        spec = ToolSpec("message.board_post",
                        capabilities=frozenset({ToolCapability.MSG_SEND}))
        interpreter, spies = _interpreter(spec)
        interpreter.execute('message_board_post(content="our words")')
        assert spies["message.board_post"].calls == [{"content": "our words"}]

    def test_a_tainted_selection_of_an_acting_tool_is_refused(self):
        spec = ToolSpec("shell.run", capabilities=frozenset({ToolCapability.EXEC}))
        interpreter, spies = _interpreter(spec)
        tool = interpreter.tools["shell.run"]
        with pytest.raises(PolicyViolation) as info:
            interpreter.execute('shell_run(command="ls")',
                                {"shell_run": Labeled(tool, origin(PAGE))})
        assert info.value.verdict.rule == "control-integrity"
        assert spies["shell.run"].calls == []

    def test_a_tainted_selection_of_a_read_only_tool_runs(self):
        spec = ToolSpec("memory.recall", tainted_args_allowed=frozenset({"query"}))
        interpreter, spies = _interpreter(spec)
        tool = interpreter.tools["memory.recall"]
        interpreter.execute('memory_recall(query="q")',
                            {"memory_recall": Labeled(tool, origin(PAGE))})
        assert spies["memory.recall"].calls == [{"query": "q"}]

    def test_an_acting_tool_returns_tainted_data(self):
        spec = ToolSpec("shell.run", capabilities=frozenset({ToolCapability.EXEC}))
        interpreter, _ = _interpreter(spec)
        result = interpreter.execute('shell_run(command="ls")')
        assert result.tainted
        assert "tool:shell.run" in result.label.origins

    def test_an_undeclared_variable_cannot_be_used(self):
        with pytest.raises(NameError_):
            Interpreter().execute("stolen")

    def test_dotted_module_access_reaches_registered_tools(self):
        spec = ToolSpec("web_search.fetch")
        interpreter, spies = _interpreter(spec)
        interpreter.execute('web_search.fetch(url="https://example.test/")')
        assert spies["web_search.fetch"].calls == [{"url": "https://example.test/"}]


class TestBudgetAndLanguage:
    def test_an_endless_loop_is_stopped(self):
        with pytest.raises(BudgetExceeded):
            Interpreter(max_loop_iterations=5).execute("n = 0\nwhile n > -1:\n    n = n + 1\nn")

    def test_a_step_budget_is_enforced(self):
        with pytest.raises(BudgetExceeded):
            Interpreter(max_steps=5).execute("a = 1\nb = 2\nc = 3\nd = 4\ne = 5\nf = 6\ng = 7")

    @pytest.mark.parametrize("program,needle", [
        ("import os", "import"),
        ("f = lambda x: x", "lambda"),
        ("def f():\n    pass", "def"),
        ("class C:\n    pass", "class"),
    ])
    def test_definition_and_import_nodes_are_refused(self, program, needle):
        with pytest.raises(UnsupportedSyntax) as info:
            Interpreter().execute(program)
        assert needle in str(info.value)

    def test_private_attributes_are_unreachable(self):
        with pytest.raises(UnsupportedSyntax):
            Interpreter().execute('x = "abc"\nx.__class__')

    def test_eval_is_refused(self):
        with pytest.raises(UnsupportedSyntax):
            Interpreter().execute('eval("1+1")')

    def test_syntax_errors_are_reported_as_camel_errors(self):
        with pytest.raises(CamelError, match="does not parse"):
            Interpreter().execute("if :")

    def test_empty_program_is_refused(self):
        with pytest.raises(CamelError):
            Interpreter().execute("   ")


class TestTrace:
    def test_the_trace_names_the_call_and_the_selection(self):
        spec = ToolSpec("memory.recall", tainted_args_allowed=frozenset({"query"}))
        interpreter, _ = _interpreter(spec)
        result = interpreter.execute('memory_recall(query="q")')
        assert any("CALL memory.recall" in line for line in result.trace)
        assert "1 call(s)" in result.summary()

    def test_a_refused_call_is_in_the_trace_before_it_raises(self):
        spec = ToolSpec("message.board_post",
                        capabilities=frozenset({ToolCapability.MSG_SEND}))
        interpreter, _ = _interpreter(spec)
        with pytest.raises(PolicyViolation):
            interpreter.execute('message_board_post(content=page)',
                                {"page": Labeled.untrusted("x", PAGE)})
        assert any("REFUSE message.board_post" in line for line in interpreter.trace)


def test_run_helper_wires_specs_and_tools():
    spy = Spy("done")
    result = run('memory_recall(query="q")',
                 tools={"memory.recall": (
                     ToolSpec("memory.recall",
                              tainted_args_allowed=frozenset({"query"})), spy)},
                 policy=Policy().register(
                     ToolSpec("memory.recall",
                              tainted_args_allowed=frozenset({"query"}))))
    assert result.output.value == "done"
    assert spy.calls == [{"query": "q"}]
