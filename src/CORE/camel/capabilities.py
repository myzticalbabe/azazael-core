"""capabilities.py — taint labels and tool authority for the CaMeL data-flow model.

Why this file exists
--------------------
On 2026-10-30 a fetched page is still just a string when it reaches an agent, and
`spec-fetched-data-not-instruction` in `evals/cases/zz-behaviour-specs.json`
recorded the consequence: instructions inside tool output get *followed* because
nothing in the runtime distinguishes "text I was asked to read" from "text I am
commanded by". CaMeL (Google DeepMind, 2025) answers this with two mechanisms
that this package implements:

1. a **privileged** component that holds the tools and never sees untrusted text;
2. a **label** on every value saying where it came from, so a policy can refuse
   the moment untrusted data reaches a capability that acts on the world.

This module is only the label lattice — the vocabulary. It deliberately has no
I/O, no policy and no interpreter, so the semantics of "what is tainted" can be
tested on its own (`tests/test_camel_capabilities.py`) without a page, a tool or
a model in the loop.

Two things are being tracked separately and must not be confused:

* **origin labels** (this module): *where a value came from*. `frozenset()` is
  trusted; a non-empty set names the untrusted sources (a URL, a file, a tool
  return) that contributed to it. Labels only ever grow — joining is the
  lattice's ∪, so a derived value can never lose its taint.
* **tool capabilities** (also here): *what a tool may do to the world*. A tool
  holding `NET_OUT`, `EXEC`, `FS_WRITE` or `MSG_SEND` can transmit data, run code
  or write files, so it is the point at which taint becomes dangerous. `policy.py`
  compares the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Tool authority
# ---------------------------------------------------------------------------
class ToolCapability:
    """Names for what a tool can do. Strings, not an Enum: a tool spec may carry
    capabilities this module has never heard of, and an unknown capability must
    read as *extra* authority (fail closed), which a closed Enum cannot express.
    """

    NONE = frozenset()
    NET_READ = "net.read"
    NET_OUT = "net.out"
    EXEC = "exec"
    FS_WRITE = "fs.write"
    MSG_SEND = "msg.send"
    LLM = "llm"


#: Capabilities that can move data out of the machine, change it, or run code.
#: Taint reaching one of these is the failure CaMeL exists to prevent.
SENSITIVE: frozenset[str] = frozenset({
    ToolCapability.NET_OUT,
    ToolCapability.EXEC,
    ToolCapability.FS_WRITE,
    ToolCapability.MSG_SEND,
})


def is_sensitive(capabilities: Iterable[str]) -> bool:
    """True when a capability set includes any acting capability.

    Unknown strings count as sensitive on purpose: a tool declared with a
    capability this module does not model is treated as *more* dangerous, not
    less. Fail closed, and the mistake shows up as a blocked call rather than an
    exfiltration.
    """
    caps = frozenset(capabilities)
    if caps & SENSITIVE:
        return True
    return bool(caps - {ToolCapability.NET_READ, ToolCapability.LLM})


# ---------------------------------------------------------------------------
# Origin labels
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Label:
    """Where a value came from.

    `frozenset()` means trusted — produced by code, config or the user's own
    instruction. A non-empty `origins` set means at least one part of the value
    arrived from a source the agent does not command (a fetched page, a file
    written by someone else, a tool's free-text return).
    """

    origins: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.origins, frozenset):
            object.__setattr__(self, "origins", frozenset(self.origins))
        for origin in self.origins:
            if not isinstance(origin, str) or not origin:
                raise ValueError(
                    "an origin label must be a non-empty string, got %r" % (origin,))

    @property
    def tainted(self) -> bool:
        return bool(self.origins)

    @property
    def trusted(self) -> bool:
        return not self.origins

    def join(self, other: "Label") -> "Label":
        """Least upper bound: the union of origins. Taint never washes off."""
        if not isinstance(other, Label):
            raise TypeError("cannot join a Label with %r" % (type(other).__name__,))
        if not other.origins:
            return self
        if not self.origins:
            return other
        if other.origins <= self.origins:
            return self
        return Label(self.origins | other.origins)

    def __or__(self, other: "Label") -> "Label":
        return self.join(other)

    def __str__(self) -> str:
        if not self.origins:
            return "trusted"
        return "untrusted(%s)" % ",".join(sorted(self.origins))

    def describe(self, limit: int = 3) -> str:
        """Short form for a verdict line; never a wall of URLs."""
        if not self.origins:
            return "trusted"
        ordered = sorted(self.origins)
        head = ",".join(ordered[:limit])
        if len(ordered) > limit:
            head += ",+%d" % (len(ordered) - limit)
        return "untrusted(%s)" % head


TRUST = Label()


def origin(source: str) -> Label:
    """One untrusted source, e.g. a final URL or `tool:barta_fetch`."""
    return Label(frozenset({source}))


# ---------------------------------------------------------------------------
# Labeled values
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Labeled:
    """A value plus the origins that contributed to it.

    Arithmetic and container operations are provided as *derivations* rather
    than overloading `__add__` and friends: an overload that silently dropped a
    label would be the one bug this whole package exists to prevent, and an
    explicit `derive` is greppable.
    """

    value: Any
    label: Label = TRUST

    # -- constructors -------------------------------------------------------
    @classmethod
    def trust(cls, value: Any) -> "Labeled":
        return cls(value, TRUST)

    @classmethod
    def untrusted(cls, value: Any, source: str) -> "Labeled":
        return cls(value, origin(source))

    # -- derivation ---------------------------------------------------------
    def derive(self, value: Any, label: Label | None = None) -> "Labeled":
        """A new value computed from this one: same taint unless widened."""
        return Labeled(value, self.label if label is None else self.label | label)

    def join(self, other: "Labeled") -> "Labeled":
        """Combine two labeled values, keeping both origins."""
        return Labeled((self.value, other.value), self.label | other.label)

    @property
    def tainted(self) -> bool:
        return self.label.tainted

    def __repr__(self) -> str:
        shown = repr(self.value)
        if len(shown) > 60:
            shown = shown[:57] + "...'"
        return "Labeled(%s, %s)" % (shown, self.label.describe())


def label_of(obj: Any) -> Label:
    """The label of any value, containers included.

    A list of `Labeled` is as tainted as its most-tainted element; a dict is the
    join over keys and values. This is what stops `[page_text]` from being
    treated as a clean list of one clean string.
    """
    if isinstance(obj, Labeled):
        return obj.label
    if isinstance(obj, Mapping):
        acc = TRUST
        for key, value in obj.items():
            acc = acc | label_of(key) | label_of(value)
        return acc
    if isinstance(obj, (list, tuple, set, frozenset)):
        acc = TRUST
        for item in obj:
            acc = acc | label_of(item)
        return acc
    return TRUST


def unwrap(obj: Any) -> Any:
    """Strip labels from a value tree, for handing to a tool that needs plain data.

    Callers must have already checked the policy: unwrapping is the act of
    crossing the boundary, and the policy verdict is the only thing that makes it
    safe. `guard.check_call` is the wrapper that enforces this ordering.
    """
    if isinstance(obj, Labeled):
        return unwrap(obj.value)
    if isinstance(obj, Mapping):
        return {unwrap(k): unwrap(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [unwrap(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(unwrap(item) for item in obj)
    if isinstance(obj, (set, frozenset)):
        return {unwrap(item) for item in obj}
    return obj


def join_labels(*labels: Label) -> Label:
    acc = TRUST
    for item in labels:
        acc = acc | item
    return acc


def join_all(values: Iterable[Any]) -> Label:
    acc = TRUST
    for item in values:
        acc = acc | label_of(item)
    return acc


def seal(values: Sequence[Any], label: Label) -> list[Labeled]:
    """Return `values` as `Labeled`s carrying at least `label`.

    Used by the quarantine reader: a field parsed out of untrusted text is
    untrusted even though the parse produced a clean `int`, because the *choice*
    of which integer came from the attacker's page.
    """
    out: list[Labeled] = []
    for item in values:
        if isinstance(item, Labeled):
            out.append(Labeled(item.value, item.label | label))
        else:
            out.append(Labeled(item, label))
    return out


__all__ = [
    "Label", "Labeled", "TRUST", "ToolCapability", "SENSITIVE",
    "is_sensitive", "origin", "label_of", "unwrap", "join_labels", "join_all",
    "seal",
]
