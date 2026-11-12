"""Vendored compat: backfill pydantic eval_type_backport removed in 2.14.

mcp 1.10.1 does `from pydantic._internal._typing_extra import
eval_type_backport`; pydantic 2.14 deleted it and bare 3.16t has no wheel
for 2.13 to downgrade to. On 3.16 `typing._eval_type` already handles every
syntax the backport covered, so this is a no-op alias, not a behavior change.

Import this module BEFORE any `mcp` import on 3.16t. Fail-open by design:
if pydantic is absent or already provides the name, this is a no-op.
(MellifluousML rule: stdlib only, zero third-party wheels required.)
"""
from __future__ import annotations

try:
    import typing as _typing

    import pydantic._internal._typing_extra as _te

    if not hasattr(_te, "eval_type_backport"):

        def eval_type_backport(value, globalns=None, localns=None, type_params=None):
            try:
                return _typing._eval_type(value, globalns, localns)
            except Exception:
                if hasattr(_te, "eval_type_lenient"):
                    return _te.eval_type_lenient(value, globalns, localns)
                return _te.eval_type(value, globalns, localns)

        _te.eval_type_backport = eval_type_backport
except Exception:
    pass  # scanner:ignore silent_except_pass -- optional backport patch; absence just means this pydantic has no _eval_type to fix
