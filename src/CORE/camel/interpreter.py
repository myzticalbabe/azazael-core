"""interpreter.py — the privileged program runs here, with capabilities tracked.

Why this file exists
--------------------
The last piece of CaMeL, and the one that makes the other three enforceable. A
privileged model that "knows not to follow instructions in a web page" is a
preference, and preferences are what an injection attacks. What it cannot argue
with is an interpreter that refuses to branch on a tainted value.

The privileged model therefore emits a **restricted program** rather than a
sequence of calls. This file executes that program and enforces two invariants
the moment they can be checked:

C1 `control-integrity` — any tainted value used as a condition (`if`, `while`,
   `ifexp`, comprehension filter) raises `TaintedControlFlow`. An injected page
   cannot pick a branch, so it cannot pick a tool or a destination.
C2 `data-flow` — every tool call is evaluated by `policy.Policy` with the labels
   on its arguments *and* the label on the value that selected the tool. Taint
   reaching an acting tool raises `PolicyViolation` before the tool runs.

Everything else is ordinary evaluation where labels only ever grow (see
`capabilities`). That is the whole design: labels cannot be lost, so the only way
to move data across a boundary is a call the policy allows.

Deliberate limits, because "restricted Python" that quietly supports everything is
not restricted:

* no imports, `lambda`, `class`, `def`, `with`, `try`, `yield`, `global`, walrus;
  `UnsupportedSyntax` names the node so the refusal is actionable;
* assignment targets are plain names (no `a.b = ...` / `a[0] = ...`), which keeps
  "where did this value come from" a single lookup;
* a step budget and per-loop iteration cap, so a generated program cannot hang the
  caller. A hang is a denial of service with extra steps.

The interpreter is not a sandbox for *hostile code* — the program is written by
our privileged side, not by the attacker. It is a sandbox for *hostile data*.
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .capabilities import (
    Label,
    Labeled,
    TRUST,
    label_of,
    origin,
    unwrap,
)
from .policy import Policy, PolicyViolation, ToolSpec

MAX_STEPS = 2000
MAX_LOOP_ITERATIONS = 200
MAX_TRACE = 80


class CamelError(Exception):
    """Base for everything the interpreter refuses."""


class TaintedControlFlow(CamelError):
    """C1: a tainted value tried to choose a branch, loop or comprehension arm."""

    def __init__(self, node: str, lineno: int, label: Label):
        self.node = node
        self.lineno = lineno
        self.label = label
        super().__init__(
            "tainted %s at line %d: a value carrying %s may not decide control "
            "flow (an injected source must not choose the branch)"
            % (node, lineno, label.describe()))


class UnsupportedSyntax(CamelError):
    """The program used a construct the restricted language does not allow."""


class BudgetExceeded(CamelError):
    """The program ran past its step or iteration budget."""


class NameError_(CamelError):
    """A name the program used was never provided. Named to avoid shadowing."""


@dataclass(frozen=True)
class Tool:
    """A tool bound into the program's namespace."""

    spec: ToolSpec
    fn: Callable[..., Any]

    def __repr__(self) -> str:
        return "Tool(%s, caps=%s)" % (self.spec.name,
                                      ",".join(sorted(self.spec.capabilities)) or "-")


@dataclass
class Result:
    """What the program produced, and what it did on the way."""

    output: Any = None
    trace: list[str] = field(default_factory=list)
    steps: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> Label:
        return label_of(self.output)

    @property
    def tainted(self) -> bool:
        return self.label.tainted

    def summary(self) -> str:
        return "%d steps, %d call(s), output %s" % (
            self.steps, len(self.calls),
            ("tainted " + self.label.describe()) if self.tainted else "trusted")


class _Module:
    """Attribute-only container so `web_search.fetch(...)` reads naturally."""

    def __init__(self, **members: Any):
        self.__dict__.update(members)

    def __getattr__(self, item: str) -> Any:
        raise AttributeError(
            "no tool %r on this module (available: %s)"
            % (item, ",".join(sorted(k for k in self.__dict__ if not k.startswith("_")))))


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_UNARY_OPS = {ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos}

#: Builtins the language keeps. Each propagates labels from its arguments, so
#: `len(page_text)` is tainted: its VALUE is one bit of the page.
_SAFE_BUILTINS: dict[str, Callable[..., Any]] = {
    "len": len,
    "str": lambda v: "" if v is None else str(v),
    "int": int,
    "float": float,
    "abs": abs,
    "min": min,
    "max": max,
    "sorted": lambda v: list(v),
    "list": list,
    "sum": sum,
    "round": round,
    "repr": repr,
    "bool": bool,
}
#: Builtins whose result is a property of the INPUTS, so it carries their taint.
_TAINT_PRESERVING = frozenset({"len", "str", "repr", "sorted", "list",
                               "min", "max", "sum", "int", "float", "round",
                               "abs", "bool"})

_FORBIDDEN_NODES = {
    ast.Import: "import", ast.ImportFrom: "import", ast.Lambda: "lambda",
    ast.FunctionDef: "def", ast.AsyncFunctionDef: "async def",
    ast.ClassDef: "class", ast.With: "with", ast.AsyncWith: "async with",
    ast.Try: "try", ast.Raise: "raise", ast.Yield: "yield",
    ast.Global: "global", ast.Nonlocal: "nonlocal", ast.Await: "await",
    ast.NamedExpr: "walrus := ", ast.Delete: "del", ast.Assert: "assert",
    ast.Match: "match", ast.AsyncFor: "async for",
}


@dataclass
class Interpreter:
    """Executes a restricted program with `Policy` enforcement."""

    policy: Policy = field(default_factory=Policy)
    max_steps: int = MAX_STEPS
    max_loop_iterations: int = MAX_LOOP_ITERATIONS
    tools: dict[str, Tool] = field(default_factory=dict)
    trace: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0

    # -- setup --------------------------------------------------------------
    def register_tool(self, spec: ToolSpec, fn: Callable[..., Any]) -> Tool:
        if not callable(fn):
            raise TypeError("tool %r needs a callable" % (spec.name,))
        tool = Tool(spec, fn)
        self.tools[spec.name] = tool
        if spec.name not in self.policy.specs:
            self.policy.register(spec)
        return tool

    def register_specs(self, specs: Iterable[ToolSpec],
                       fn: Callable[[str], Callable[..., Any]]) -> None:
        for spec in specs:
            self.register_tool(spec, fn(spec.name))

    def _namespace(self, inputs: Mapping[str, Any]) -> dict[str, Labeled]:
        ns: dict[str, Labeled] = {}
        modules: dict[str, _Module] = {}
        for name, tool in self.tools.items():
            module, _, leaf = name.partition(".")
            bound = Labeled(tool, TRUST)
            ns[name.replace(".", "_")] = bound
            if leaf:
                # The module holds the RAW Tool: attribute access wraps it in a
                # Labeled, and wrapping twice produced "'Labeled' is not
                # callable" the first time this ran.
                modules.setdefault(module, _Module())
                setattr(modules[module], leaf, tool)
                ns[leaf] = bound
        for module_name, module in modules.items():
            ns[module_name] = Labeled(module, TRUST)
        for name, fn in _SAFE_BUILTINS.items():
            ns[name] = Labeled(fn, TRUST)
        for name, value in inputs.items():
            if not name.isidentifier():
                raise CamelError("input name %r is not an identifier" % (name,))
            ns[name] = value if isinstance(value, Labeled) else Labeled(value, TRUST)
        return ns

    # -- execution ----------------------------------------------------------
    def execute(self, source: str, inputs: Mapping[str, Any] | None = None) -> Result:
        if not isinstance(source, str) or not source.strip():
            raise CamelError("program source must be a non-empty string")
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as exc:
            raise CamelError("program does not parse: %s (line %s)"
                             % (exc.msg, exc.lineno)) from exc
        self._reject_forbidden(tree)

        self.trace = []
        self.calls = []
        self.steps = 0
        ns = self._namespace(inputs or {})
        loop_budget = {"n": self.max_loop_iterations}

        try:
            output = self._block(tree.body, ns, loop_budget)
        except PolicyViolation as exc:
            self._note("%s" % exc.verdict.line())
            raise
        return Result(output=output, trace=list(self.trace),
                      steps=self.steps, calls=list(self.calls))

    def _reject_forbidden(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            name = _FORBIDDEN_NODES.get(type(node))
            if name:
                raise UnsupportedSyntax(
                    "%r is not in the restricted language (line %s); the program "
                    "may branch, loop, call tools and compute — not define, "
                    "import or handle exceptions"
                    % (name.strip(), getattr(node, "lineno", "?")))
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "eval":
                raise UnsupportedSyntax("eval() is not available (line %s)"
                                        % node.lineno)

    # -- statements ---------------------------------------------------------
    def _block(self, body: list[ast.stmt], ns: dict[str, Labeled],
               budget: dict[str, int]) -> Any:
        output: Any = None
        for stmt in body:
            self._step(stmt)
            result = self._stmt(stmt, ns, budget)
            if result is not _NO_VALUE:
                output = result
        return output

    def _stmt(self, node: ast.stmt, ns: dict[str, Labeled],
              budget: dict[str, int]) -> Any:
        if isinstance(node, ast.Expr):
            value = self._expr(node.value, ns)
            return value
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                raise UnsupportedSyntax(
                    "assignment target must be a plain name (line %d)" % node.lineno)
            value = self._expr(node.value, ns)
            ns[node.targets[0].id] = value
            self._note("line %d: %s = %s" % (node.lineno, node.targets[0].id,
                                             self._describe(value)))
            return _NO_VALUE
        if isinstance(node, ast.AugAssign):
            if not isinstance(node.target, ast.Name):
                raise UnsupportedSyntax(
                    "augmented assignment target must be a plain name (line %d)"
                    % node.lineno)
            current = self._name(node.target.id, ns)
            value = self._expr(node.value, ns)
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise UnsupportedSyntax("operator %s not supported (line %d)"
                                        % (type(node.op).__name__, node.lineno))
            combined = Labeled(op(current.value, value.value),
                               current.label | value.label)
            ns[node.target.id] = combined
            return _NO_VALUE
        if isinstance(node, ast.If):
            _, truth = self._decide(node.test, ns, "if")
            branch = node.body if truth else node.orelse
            return self._block(branch, ns, budget)
        if isinstance(node, ast.For):
            return self._for(node, ns, budget)
        if isinstance(node, ast.While):
            return self._while(node, ns, budget)
        if isinstance(node, ast.Pass):
            return _NO_VALUE
        if isinstance(node, ast.Break):
            return _BREAK
        if isinstance(node, ast.Continue):
            return _CONTINUE
        raise UnsupportedSyntax("statement %s is not supported (line %s)"
                                % (type(node).__name__,
                                   getattr(node, "lineno", "?")))

    def _for(self, node: ast.For, ns: dict[str, Labeled],
             budget: dict[str, int]) -> Any:
        if not isinstance(node.target, ast.Name):
            raise UnsupportedSyntax(
                "for-loop target must be a plain name (line %d)" % node.lineno)
        iterable = self._expr(node.iter, ns)
        if not isinstance(iterable.value, (list, tuple, set, str, dict, range)):
            raise CamelError("for loop needs a sequence at line %d, got %s"
                             % (node.lineno, type(iterable.value).__name__))
        items = list(iterable.value)
        if len(items) > self.max_loop_iterations:
            raise BudgetExceeded(
                "for loop at line %d iterates %d items (cap %d)"
                % (node.lineno, len(items), self.max_loop_iterations))
        output: Any = _NO_VALUE
        for item in items:
            self._step(node)
            ns[node.target.id] = Labeled(item, iterable.label)
            result = self._block(node.body, ns, budget)
            if result is _BREAK:
                break
            if result is _CONTINUE:
                continue
            if result is not _NO_VALUE:
                output = result
        else:
            for stmt in node.orelse:
                self._stmt(stmt, ns, budget)
        return output

    def _while(self, node: ast.While, ns: dict[str, Labeled],
               budget: dict[str, int]) -> Any:
        output: Any = _NO_VALUE
        count = 0
        broke = False
        while True:
            self._step(node)
            _, truth = self._decide(node.test, ns, "while")
            if not truth:
                break
            count += 1
            if count > self.max_loop_iterations:
                raise BudgetExceeded(
                    "while loop at line %d ran %d iterations (cap %d)"
                    % (node.lineno, count, self.max_loop_iterations))
            result = self._block(node.body, ns, budget)
            if result is _BREAK:
                broke = True
                break
            if result is not _CONTINUE and result is not _NO_VALUE:
                output = result
        if not broke:
            for stmt in node.orelse:
                self._stmt(stmt, ns, budget)
        return output

    # -- control-flow integrity (C1) ----------------------------------------
    def _decide(self, node: ast.expr, ns: dict[str, Labeled],
                kind: str) -> tuple[Labeled, bool]:
        """Evaluate a condition ONCE, refuse if tainted, return (value, truth).

        Once matters: a condition may contain a tool call (`if fetch(u):`), and
        evaluating it twice would run the call twice while the trace showed one
        branch. The check therefore inspects the same value the branch uses.
        """
        value = self._expr(node, ns)
        if value.label.tainted:
            raise TaintedControlFlow(kind, getattr(node, "lineno", 0), value.label)
        return value, bool(unwrap(value))

    # -- expressions --------------------------------------------------------
    def _expr(self, node: ast.expr, ns: dict[str, Labeled]) -> Labeled:
        if isinstance(node, ast.Constant):
            return Labeled(node.value, TRUST)
        if isinstance(node, ast.Name):
            return self._name(node.id, ns)
        if isinstance(node, ast.Attribute):
            base = self._expr(node.value, ns)
            return self._attribute(base, node.attr, node.lineno)
        if isinstance(node, ast.Subscript):
            base = self._expr(node.value, ns)
            index = self._expr(node.slice, ns) if not isinstance(node.slice, ast.Slice) \
                else Labeled(slice(None), TRUST)
            try:
                value = base.value[index.value]
            except (KeyError, IndexError, TypeError) as exc:
                raise CamelError("subscript at line %d failed: %s"
                                 % (node.lineno, exc)) from exc
            return Labeled(value, base.label | index.label)
        if isinstance(node, ast.BinOp):
            return self._binop(node, ns)
        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op is None:
                raise UnsupportedSyntax("unary %s at line %d"
                                        % (type(node.op).__name__, node.lineno))
            operand = self._expr(node.operand, ns)
            return Labeled(op(operand.value), operand.label)
        if isinstance(node, ast.BoolOp):
            values = [self._expr(v, ns) for v in node.values]
            label = TRUST
            for item in values:
                label = label | item.label
            if isinstance(node.op, ast.And):
                result = all(bool(unwrap(v)) for v in values)
            else:
                result = any(bool(unwrap(v)) for v in values)
            return Labeled(result, label)
        if isinstance(node, ast.Compare):
            left = self._expr(node.left, ns)
            label = left.label
            result: Any = True
            for op_node, comparator in zip(node.ops, node.comparators):
                right = self._expr(comparator, ns)
                label = label | right.label
                op = _CMP_OPS.get(type(op_node))
                if op is None:
                    raise UnsupportedSyntax("comparison %s at line %d"
                                            % (type(op_node).__name__, node.lineno))
                try:
                    result = result and op(left.value, right.value)
                except TypeError as exc:
                    raise CamelError("comparison at line %d failed: %s"
                                     % (node.lineno, exc)) from exc
                left = right
            return Labeled(bool(result), label)
        if isinstance(node, ast.IfExp):
            _, truth = self._decide(node.test, ns, "ifexp")
            branch = node.body if truth else node.orelse
            return self._expr(branch, ns)
        if isinstance(node, ast.List):
            return self._collect(node.elts, ns, list)
        if isinstance(node, ast.Tuple):
            return self._collect(node.elts, ns, tuple)
        if isinstance(node, ast.Set):
            return self._collect(node.elts, ns, lambda v: list(v))
        if isinstance(node, ast.Dict):
            label = TRUST
            out: dict[Any, Any] = {}
            for key, value in zip(node.keys, node.values):
                if key is None:
                    raise UnsupportedSyntax("dict unpacking at line %d" % node.lineno)
                k = self._expr(key, ns)
                v = self._expr(value, ns)
                label = label | k.label | v.label
                out[unwrap(k)] = unwrap(v)
            return Labeled(out, label)
        if isinstance(node, ast.JoinedStr):
            label = TRUST
            parts: list[str] = []
            for piece in node.values:
                if isinstance(piece, ast.FormattedValue):
                    if piece.format_spec is not None:
                        raise UnsupportedSyntax(
                            "format spec at line %d" % piece.lineno)
                    item = self._expr(piece.value, ns)
                    label = label | item.label
                    parts.append(str(item.value))
                else:
                    item = self._expr(piece, ns)
                    label = label | item.label
                    parts.append(str(item.value))
            return Labeled("".join(parts), label)
        if isinstance(node, ast.ListComp):
            return self._comprehension(node, ns)
        if isinstance(node, ast.Call):
            return self._call(node, ns)
        raise UnsupportedSyntax("expression %s is not supported (line %s)"
                                % (type(node).__name__, getattr(node, "lineno", "?")))

    def _binop(self, node: ast.BinOp, ns: dict[str, Labeled]) -> Labeled:
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise UnsupportedSyntax("operator %s at line %d"
                                    % (type(node.op).__name__, node.lineno))
        left = self._expr(node.left, ns)
        right = self._expr(node.right, ns)
        try:
            value = op(left.value, right.value)
        except Exception as exc:  # noqa: BLE001 — arithmetic on attacker data
            raise CamelError("operation at line %d failed: %s: %s"
                             % (node.lineno, type(exc).__name__, exc)) from exc
        return Labeled(value, left.label | right.label)

    def _collect(self, elts: list[ast.expr], ns: dict[str, Labeled],
                 factory: Callable[[list[Any]], Any]) -> Labeled:
        label = TRUST
        values: list[Any] = []
        for element in elts:
            item = self._expr(element, ns)
            label = label | item.label
            values.append(unwrap(item))
        return Labeled(factory(values), label)

    def _comprehension(self, node: ast.ListComp, ns: dict[str, Labeled]) -> Labeled:
        if len(node.generators) != 1:
            raise UnsupportedSyntax(
                "comprehension at line %d: exactly one generator is supported"
                % node.lineno)
        gen = node.generators[0]
        if not isinstance(gen.target, ast.Name):
            raise UnsupportedSyntax("comprehension target must be a name (line %d)"
                                    % node.lineno)
        source = self._expr(gen.iter, ns)
        label = TRUST
        out: list[Any] = []
        for item in list(source.value)[: self.max_loop_iterations]:
            self._step(node)
            local = dict(ns)
            local[gen.target.id] = Labeled(item, source.label)
            for condition in gen.ifs:
                _, truth = self._decide(condition, local, "comprehension filter")
                if not truth:
                    break
            else:
                value = self._expr(node.elt, local)
                label = label | value.label
                out.append(unwrap(value))
        return Labeled(out, label | TRUST)

    def _attribute(self, base: Labeled, attr: str, lineno: int) -> Labeled:
        if attr.startswith("_"):
            raise UnsupportedSyntax(
                "attribute %r at line %d: private attributes are not reachable"
                % (attr, lineno))
        try:
            value = getattr(base.value, attr)
        except AttributeError as exc:
            raise CamelError("attribute %r at line %d: %s"
                             % (attr, lineno, exc)) from exc
        return Labeled(value, base.label)

    def _name(self, name: str, ns: dict[str, Labeled]) -> Labeled:
        try:
            return ns[name]
        except KeyError:
            raise NameError_(
                "name %r is not defined; the program may only use its inputs and "
                "the registered tools (%s)"
                % (name, ", ".join(sorted(ns)))) from None

    # -- calls (C2) ---------------------------------------------------------
    def _call(self, node: ast.Call, ns: dict[str, Labeled]) -> Labeled:
        callee = self._expr(node.func, ns)
        target = callee.value
        args: dict[str, Labeled] = {}
        for position, argument in enumerate(node.args):
            if isinstance(argument, ast.Starred):
                raise UnsupportedSyntax("*args at line %d" % node.lineno)
            args["_pos%d" % position] = self._expr(argument, ns)
        for keyword in node.keywords:
            if keyword.arg is None:
                raise UnsupportedSyntax("**kwargs at line %d" % node.lineno)
            args[keyword.arg] = self._expr(keyword.value, ns)

        if isinstance(target, Tool):
            return self._tool_call(target, args, callee.label, node.lineno)
        if callable(target):
            name = getattr(target, "__name__", "") or "<callable>"
            label = TRUST
            for item in args.values():
                label = label | item.label
            try:
                value = target(*[unwrap(v) for v in args.values()])
            except Exception as exc:  # noqa: BLE001 — builtin on attacker data
                raise CamelError("call %s at line %d failed: %s: %s"
                                 % (name, node.lineno, type(exc).__name__, exc)) from exc
            if name in _TAINT_PRESERVING:
                return Labeled(value, label)
            return Labeled(value, label)
        raise CamelError("line %d: %r is not callable"
                         % (node.lineno, type(target).__name__))

    def _tool_call(self, tool: Tool, args: dict[str, Labeled], selection: Label,
                   lineno: int) -> Labeled:
        verdict = self.policy.evaluate(tool.spec.name, args, selection=selection)
        self.calls.append({
            "tool": tool.spec.name,
            "line": lineno,
            "allowed": verdict.allowed,
            "rule": verdict.rule,
            "capabilities": sorted(tool.spec.capabilities),
        })
        if not verdict.allowed:
            self._note("line %d: %s" % (lineno, verdict.line()))
            raise PolicyViolation(verdict)
        self._note("line %d: CALL %s (selection %s)"
                   % (lineno, tool.spec.name, selection.describe()))
        try:
            value = tool.fn(**{k: unwrap(v) for k, v in args.items()})
        except Exception as exc:  # noqa: BLE001 — tool boundary reports
            raise CamelError("tool %s at line %d raised %s: %s"
                             % (tool.spec.name, lineno, type(exc).__name__,
                                str(exc)[:200])) from exc
        # A tool's return is untrusted unless the call was pure: an acting tool
        # returns data we did not author.
        label = selection
        if tool.spec.sensitive:
            label = label | origin("tool:" + tool.spec.name)
        if isinstance(value, Labeled):
            return Labeled(value.value, value.label | label)
        return Labeled(value, label)

    # -- bookkeeping --------------------------------------------------------
    def _step(self, node: ast.AST) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise BudgetExceeded(
                "program exceeded %d steps (line %s); a generated program that "
                "does not terminate is a denial of service"
                % (self.max_steps, getattr(node, "lineno", "?")))

    def _note(self, line: str) -> None:
        if len(self.trace) < MAX_TRACE:
            self.trace.append(line)

    @staticmethod
    def _describe(value: Labeled) -> str:
        shown = repr(value.value)
        if len(shown) > 48:
            shown = shown[:45] + "..."
        return "%s [%s]" % (shown, value.label.describe())


_NO_VALUE = object()
_BREAK = object()
_CONTINUE = object()


def run(source: str, *, tools: Mapping[str, tuple[ToolSpec, Callable[..., Any]]],
        inputs: Mapping[str, Any] | None = None,
        policy: Policy | None = None,
        max_steps: int = MAX_STEPS) -> Result:
    """Convenience entry point: build an interpreter, register tools, run."""
    interpreter = Interpreter(policy=policy or Policy(), max_steps=max_steps)
    for name, (spec, fn) in tools.items():
        if name != spec.name:
            raise CamelError("tool key %r does not match spec name %r"
                             % (name, spec.name))
        interpreter.register_tool(spec, fn)
    return interpreter.execute(source, inputs)


__all__ = [
    "BudgetExceeded", "CamelError", "Interpreter", "NameError_", "Result",
    "TaintedControlFlow", "Tool", "UnsupportedSyntax", "run",
]
