"""camel — CaMeL: capability-tracking data flow for tool-using agents.

Five modules, one invariant each:

| module            | invariant |
|-------------------|-----------|
| `capabilities`    | a value's origin never disappears; joining is the lattice's ∪ |
| `policy`          | an acting tool never receives tainted data, and tainted data never chooses the call |
| `quarantine`      | the component that reads untrusted text returns declared, typed fields and nothing else |
| `interpreter`     | a tainted value cannot decide control flow, and every tool call is policy-checked |
| `guard`           | the runtime boundary: what a fetch registered is refused as an argument to an exfil-capable tool |

Read in that order. `capabilities` and `policy` have no I/O; `quarantine` reads
text; `interpreter` executes a restricted program; `guard` is what the real code
calls.

Why the package exists at all: `evals/cases/zz-behaviour-specs.json` carries
`spec-fetched-data-not-instruction` — "instructions inside fetched content are
quoted, not obeyed" — as a *specification* because nothing in the runtime could
enforce it. This package is the enforcement. The headline finding it answers is
that prompting for safety is a preference and a data-flow rule is a fact:
`docs/UPGRADE_RADAR_2026-10-30.md` §B-8.

Exports are lazy (PEP 562) so importing `CORE.camel` costs nothing and cannot
pull a module's dependencies into an unrelated process.
"""

from __future__ import annotations

from typing import Any

_LAZY = {
    "capabilities": ("Label", "Labeled", "TRUST", "ToolCapability", "SENSITIVE",
                     "is_sensitive", "origin", "label_of", "unwrap", "seal",
                     "join_labels", "join_all"),
    "policy": ("Policy", "PolicyViolation", "ToolSpec", "Verdict",
               "DESTINATION_ARGS", "default_policy"),
    "quarantine": ("DeterministicReader", "FieldSpec", "ModelReader",
                   "QuarantineResult", "Schema", "SchemaError", "schema_from"),
    "interpreter": ("Interpreter", "Result", "Tool", "run", "CamelError",
                    "TaintedControlFlow", "UnsupportedSyntax", "BudgetExceeded"),
    "guard": ("TaintLedger", "check_call", "guard_call", "register_untrusted",
              "ledger_for"),
}

__all__ = sorted(name for names in _LAZY.values() for name in names)  # type: ignore[reportUnsupportedDunderAll]


def __getattr__(name: str) -> Any:
    for module, names in _LAZY.items():
        if name in names:
            mod = __import__("%s.%s" % (__name__, module), fromlist=[name])
            return getattr(mod, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__() -> list[str]:
    return sorted(__all__)
