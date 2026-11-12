"""policy.py — the data-flow rules that decide whether a tool call may happen.

Why this file exists
--------------------
A label nobody checks is a comment. This is the check: given a tool's declared
authority and the labels on its arguments, decide whether the call is allowed,
and when it is not, say **which rule** refused and **which argument** carried
**which origin** — so the refusal is reproducible instead of "blocked by policy".

The four rules, each named so a test can cite it:

P1 `control-integrity` — the *choice* of an acting tool, and the *destination* of
   a call, may not be derived from untrusted data. This is the CaMeL paper's
   central restriction: an injected page must not be able to steer control flow.
   A tainted `if` that selects `git_push` is refused even when every argument is
   literally trusted, because the branch that ran was chosen by the page.
P2 `tainted-argument` — an acting tool (`NET_OUT`, `EXEC`, `FS_WRITE`,
   `MSG_SEND`) may not receive tainted argument values. A tool may opt a *data*
   field back in via `tainted_args_allowed`, which is the honest way to say
   "this argument is quoted material, not a control signal" — e.g. the body of a
   search query. Nothing that names a destination can opt in.
P3 `tainted-destination` — for **every** tool, read-only or not, an argument whose
   name marks it as a destination (url, path, recipient, command...) must be
   untainted. A read-only fetch of an attacker-chosen URL is already an
   exfiltration channel and a staging step, so gating only the acting tools
   would miss the first half of the attack.
P4 `exec-purity` — an `EXEC` tool is refused if *any* argument is tainted. Shell
   composition is not a data flow worth distinguishing; there is no safe tainted
   argv.

What this module does NOT do: it does not decide what is untrusted (that is
`capabilities.py` + `guard.TaintLedger`) and it does not execute anything (that
is `interpreter.py` / the real dispatcher). Keeping the three apart is what lets
each be tested against a known-bad input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .capabilities import (
    Label,
    TRUST,
    ToolCapability,
    is_sensitive,
    label_of,
)


#: Argument names that name a destination, a target or an effect. Deliberately
#: generous: a false positive costs one explicit `destination_args` override or a
#: rename, while a false negative is an exfiltration path. Extend by passing
#: `destination_args=` on a `ToolSpec`, not by deleting from here.
DESTINATION_ARGS: frozenset[str] = frozenset({
    "url", "uri", "href", "link", "endpoint", "host", "hostname", "port",
    "path", "file", "filename", "filepath", "dir", "directory", "dest",
    "command", "cmd", "shell", "argv", "script", "executable",
    "recipient", "to", "cc", "bcc", "from", "sender", "reply_to",
    "account", "user", "subject",
    "repo", "remote", "branch", "ref", "tag", "commit",
    "table", "collection", "index", "bucket", "key", "topic", "channel",
})

_RULE_P1 = "control-integrity"
_RULE_P2 = "tainted-argument"
_RULE_P3 = "tainted-destination"
_RULE_P4 = "exec-purity"


class PolicyViolation(Exception):
    """Raised when a call is refused. Carries the verdict, not just a sentence."""

    def __init__(self, verdict: "Verdict"):
        self.verdict = verdict
        super().__init__(verdict.reason)


@dataclass(frozen=True)
class ToolSpec:
    """A tool's declared authority.

    `capabilities` is the whole point: a tool with none is a pure function and can
    receive anything; a tool with `NET_OUT`/`EXEC`/`FS_WRITE`/`MSG_SEND` is where
    taint turns into damage. Unknown capability strings count as acting authority
    (see `capabilities.is_sensitive`), so an unmodelled tool fails closed.
    """

    name: str
    capabilities: frozenset[str] = ToolCapability.NONE
    #: Arguments allowed to carry tainted data on an acting tool. Never a
    #: destination name — `Policy.register` rejects that combination.
    tainted_args_allowed: frozenset[str] = frozenset()
    #: Extra destination names for this tool, on top of `DESTINATION_ARGS`.
    destination_args: frozenset[str] = frozenset()
    #: Free text for the verdict line: who owns the tool, which lane wired it.
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ToolSpec needs a non-empty name")
        for capability in self.capabilities:
            if not isinstance(capability, str) or not capability:
                raise ValueError(
                    "%s: capability must be a non-empty string, got %r"
                    % (self.name, capability))
        overlap = self.tainted_args_allowed & (self.destination_args | DESTINATION_ARGS)
        if overlap:
            raise ValueError(
                "%s: %s cannot both name a destination and accept tainted data — "
                "a tainted destination is the exfiltration channel P3 exists to close"
                % (self.name, ",".join(sorted(overlap))))

    @property
    def sensitive(self) -> bool:
        return is_sensitive(self.capabilities)

    @property
    def destinations(self) -> frozenset[str]:
        return self.destination_args | DESTINATION_ARGS

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capabilities": sorted(self.capabilities),
            "tainted_args_allowed": sorted(self.tainted_args_allowed),
            "destination_args": sorted(self.destination_args),
            "note": self.note,
        }


@dataclass(frozen=True)
class Verdict:
    """The decision, with enough evidence to reproduce it."""

    allowed: bool
    tool: str
    rule: str = ""
    reason: str = ""
    tainted_args: tuple[str, ...] = ()
    selection: str = "trusted"
    origins: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.allowed

    def line(self) -> str:
        state = "ALLOW" if self.allowed else "REFUSE"
        head = "%s %s" % (state, self.tool)
        if self.allowed:
            return head
        return "%s [%s] %s" % (head, self.rule, self.reason)


@dataclass
class Policy:
    """A registry of `ToolSpec`s plus the rule evaluation.

    Tools that were never registered are **not** treated as harmless: an unknown
    tool is evaluated as if it held acting authority, because "I forgot to
    register it" must not be the way to get an unguarded call.
    """

    specs: dict[str, ToolSpec] = field(default_factory=dict)
    allow_unknown: bool = False
    history: list[Verdict] = field(default_factory=list)
    keep_history: int = 200

    # -- registry -----------------------------------------------------------
    def register(self, spec: ToolSpec) -> "Policy":
        """Register one tool and return the policy, so calls can chain.

        Returns `self`, not the spec: `Policy().register(spec)` reading as a spec
        is how a policy became a ToolSpec in the first interpreter test.
        """
        if spec.name in self.specs:
            raise ValueError("tool %r is already registered" % (spec.name,))
        self.specs[spec.name] = spec
        return self

    def register_all(self, specs: Iterable[ToolSpec]) -> "Policy":
        for spec in specs:
            self.register(spec)
        return self

    def spec_for(self, name: str) -> ToolSpec:
        spec = self.specs.get(name)
        if spec is not None:
            return spec
        if self.allow_unknown:
            return ToolSpec(name=name)
        return ToolSpec(
            name=name,
            capabilities=frozenset({"unregistered"}),
            note="unregistered tool: evaluated as acting authority (fail closed)",
        )

    # -- evaluation ---------------------------------------------------------
    def evaluate(self, tool: str, args: Mapping[str, Any] | None = None, *,
                 selection: Label = TRUST) -> Verdict:
        """Decide one call. Never raises; `check` is the raising wrapper."""
        spec = self.spec_for(tool)
        args = args or {}
        tainted = {name: label_of(value) for name, value in args.items()}
        tainted_args = tuple(sorted(n for n, lab in tainted.items() if lab.tainted))
        origins = tuple(sorted({
            origin
            for label in list(tainted.values()) + [selection]
            for origin in label.origins
        }))
        selection_line = selection.describe()

        def refuse(rule: str, reason: str) -> Verdict:
            # The origins go in the reason, not just in a field: a refusal line
            # read on its own in a log has to say WHICH source tainted the call,
            # or reproducing it means re-running the session.
            if origins:
                reason = "%s (origins: %s)" % (reason, ", ".join(origins[:3]))
            verdict = Verdict(False, tool, rule, reason, tainted_args,
                              selection_line, origins)
            self._remember(verdict)
            return verdict

        def allow() -> Verdict:
            verdict = Verdict(True, tool, "", "", tainted_args, selection_line,
                              origins)
            self._remember(verdict)
            return verdict

        # P1 — a tainted value must not choose which acting tool runs.
        if selection.tainted and spec.sensitive:
            return refuse(
                _RULE_P1,
                "the choice of %s was derived from %s: an injected source must "
                "not select an acting tool" % (tool, selection.describe()))

        # P3 — destinations are untainted for every tool, read-only included.
        for name in sorted(set(args) & spec.destinations):
            label = tainted[name]
            if label.tainted:
                return refuse(
                    _RULE_P3,
                    "argument %r names a destination and carries %s; a source "
                    "must not pick where the call goes" % (name, label.describe()))

        # P4 — shell arguments are all-or-nothing.
        if ToolCapability.EXEC in spec.capabilities and tainted_args:
            return refuse(
                _RULE_P4,
                "exec tool received tainted %s; there is no safe tainted argv"
                % ", ".join(repr(n) for n in tainted_args))

        # P2 — acting tools do not take tainted data.
        if spec.sensitive:
            offenders = tuple(
                n for n in tainted_args if n not in spec.tainted_args_allowed)
            if offenders:
                return refuse(
                    _RULE_P2,
                    "acting tool (%s) received tainted %s; declare the field in "
                    "tainted_args_allowed only if it is quoted data and not a "
                    "control signal"
                    % (",".join(sorted(spec.capabilities)),
                       ", ".join(repr(n) for n in offenders)))

        return allow()

    def check(self, tool: str, args: Mapping[str, Any] | None = None, *,
              selection: Label = TRUST) -> Verdict:
        """`evaluate`, but a refusal raises `PolicyViolation`."""
        verdict = self.evaluate(tool, args, selection=selection)
        if not verdict.allowed:
            raise PolicyViolation(verdict)
        return verdict

    # -- audit --------------------------------------------------------------
    def _remember(self, verdict: Verdict) -> None:
        self.history.append(verdict)
        if len(self.history) > self.keep_history:
            del self.history[: len(self.history) - self.keep_history]

    def refusals(self) -> list[Verdict]:
        return [v for v in self.history if not v.allowed]

    def stats(self) -> dict[str, Any]:
        total = len(self.history)
        refused = len(self.refusals())
        by_rule: dict[str, int] = {}
        for verdict in self.refusals():
            by_rule[verdict.rule] = by_rule.get(verdict.rule, 0) + 1
        return {
            "evaluated": total,
            "allowed": total - refused,
            "refused": refused,
            "by_rule": by_rule,
            "tools": len(self.specs),
        }


def default_policy() -> Policy:
    """The policy the shipped tools are registered against.

    Deliberately small: these are the surfaces this repository actually has, and
    each carries the capability that makes it dangerous. Anything not listed is
    evaluated as acting authority, so adding a tool is a decision, not a default.
    """
    policy = Policy()
    policy.register_all([
        ToolSpec("web_search.fetch",
                 capabilities=frozenset({ToolCapability.NET_READ}),
                 destination_args=frozenset({"query"}),
                 note="read-only fetch; a tainted URL is still refused by P3"),
        ToolSpec("web_search.search",
                 capabilities=frozenset({ToolCapability.NET_READ}),
                 tainted_args_allowed=frozenset({"query"}),
                 destination_args=frozenset({"provider"}),
                 note="searching FOR untrusted text is legitimate: the query is data"),
        ToolSpec("memory.recall",
                 capabilities=frozenset(),
                 tainted_args_allowed=frozenset({"query"}),
                 note="pure read of local memory"),
        ToolSpec("memory.record",
                 capabilities=frozenset({ToolCapability.FS_WRITE}),
                 tainted_args_allowed=frozenset({"content"}),
                 note="recording a quote is the point; the field is data by "
                      "declaration, and P3 still protects any destination"),
        ToolSpec("message.board_post",
                 capabilities=frozenset({ToolCapability.MSG_SEND}),
                 destination_args=frozenset({"lane"}),
                 note="publishes to other lanes: an exfiltration channel"),
        ToolSpec("shell.run",
                 capabilities=frozenset({ToolCapability.EXEC}),
                 note="P4: no tainted argument of any kind"),
        ToolSpec("http.post",
                 capabilities=frozenset({ToolCapability.NET_OUT}),
                 destination_args=frozenset({"body"}),
                 note="outbound body is a destination here: this tool only sends"),
    ])
    return policy


__all__ = [
    "DESTINATION_ARGS", "Policy", "PolicyViolation", "ToolSpec", "Verdict",
    "default_policy",
]
