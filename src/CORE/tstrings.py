# coding: utf-8
"""
tstrings.py — Python 3.14 t-string (template string) utilities for AZAZAEL.

T-strings (PEP 750) produce Template objects that separate string structure
from interpolated values. This module provides processors to convert Templates
into formatted strings for different contexts:

- `fmt(template)` — simple string formatting (like f-string)
- `safe_log(template)` — structured logging with key=value pairs
- `safe_record(template)` — PAMS-safe recording with sanitized values
- `safe_sql(template)` — SQL-safe formatting with parameterized values
- `safe_html(template)` — HTML-escaped formatting
- `structured(template)` — returns dict with template + values for indexing

Usage:
    from CORE.tstrings import fmt, safe_log, safe_record

    name = "Agent-1"
    status = "running"

    # Simple formatting (equivalent to f-string)
    msg = fmt('Agent {name} is {status}')

    # Structured logging — produces "Agent-1 is running [name=Agent-1, status=running]"
    log_msg = safe_log('Agent {name} is {status}')

    # PAMS recording — sanitizes values for storage
    data = safe_record('Worm {worm_name} executed in {duration:.1f}ms')

    # Structured output — preserves template + values for indexing
    structured = structured('Agent {name} is {status}')
    # -> {"template": "Agent {name} is {status}", "values": {"name": "Agent-1", "status": "running"}}

CANONICAL SOURCE — import from here project-wide:
    from CORE.tstrings import fmt, safe_log, safe_record, structured
"""

import html
import re
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Template Processing ──────────────────────────────────────────────────────


def fmt(template) -> str:
    """Convert a t-string Template to a plain string (like f-string behavior).

    Iterates through template.strings and template.interpolations,
    joining them into a single formatted string.

    Args:
        template: A string.templatelib.Template object (from t-string syntax).

    Returns:
        Formatted string equivalent to the f-string version.

    Example:
        name = "world"
        assert fmt('Hello {name}') == f'Hello {name}'
    """
    parts: list[str] = []
    interpolations = template.interpolations

    for i, literal in enumerate(template.strings):
        parts.append(literal)
        if i < len(interpolations):
            interp = interpolations[i]
            val = interp.value

            # Apply format spec if present
            fmt_spec = interp.format_spec
            if fmt_spec:
                parts.append(format(val, fmt_spec))
            else:
                parts.append(str(val))

    return "".join(parts)


def safe_log(template) -> str:
    """Format a t-string for structured logging with key=value context.

    Produces a message string followed by extracted variable names and values,
    making logs searchable and indexable.

    Args:
        template: A Template object from t-string syntax.

    Returns:
        Formatted string like "Agent Agent-1 is running [name=Agent-1, status=running]"

    Example:
        agent = "Agent-1"
        status = "running"
        safe_log('{agent} is {status}')
        # -> "Agent-1 is running [agent=Agent-1, status=running]"
    """
    # Build the formatted message
    message = fmt(template)

    # Extract variable context from interpolations
    context_parts = []
    for interp in template.interpolations:
        expr = interp.expression
        val = interp.value
        # Only add context for variable references (skip literals and calls)
        if expr and _is_variable_ref(expr):
            context_parts.append(f"{expr}={_sanitize_for_log(val)}")

    if context_parts:
        return f"{message} [{', '.join(context_parts)}]"
    return message


def safe_record(template) -> dict[str, Any]:
    """Format a t-string for PAMS memory recording.

    Returns a dict with the formatted message and extracted variable context,
    suitable for passing to PamsRecorder.record_event() data parameter.

    Args:
        template: A Template object from t-string syntax.

    Returns:
        dict with 'message' and 'context' keys.

    Example:
        worm = "PortAuditor"
        duration = 42.5
        data = safe_record('Worm {worm} executed in {duration:.1f}ms')
        # -> {"message": "Worm PortAuditor executed in 42.5ms", "context": {"worm": "PortAuditor", "duration": 42.5}}
    """
    message = fmt(template)
    context = {}

    for interp in template.interpolations:
        expr = interp.expression
        if expr and _is_variable_ref(expr):
            context[expr] = _sanitize_for_storage(interp.value)

    return {"message": message, "context": context}


def safe_sql(template) -> tuple:
    """Format a t-string for safe SQL parameterized queries.

    Returns a tuple of (query_template, params) where the query template
    has ? placeholders for values and inline text for identifiers.

    IMPORTANT: SQL only allows ? placeholders for VALUES, not for identifiers
    (table names, column names). This function uses _is_identifier() to guess
    which interpolations are identifiers (single word, no dots/calls) and
    inlines them, while parameterizing everything else.

    If you need full control, use fmt() for trusted queries or manually
    build parameterized queries.

    Args:
        template: A Template object from t-string syntax.

    Returns:
        tuple of (query_string_with_placeholders, parameter_list).

    Example:
        table = "users"
        user_id = 42
        query, params = safe_sql('SELECT * FROM {table} WHERE id = {user_id}')
        # -> ("SELECT * FROM users WHERE id = ?", [42])
        #   table is inlined (identifier), user_id is parameterized (value)
    """
    parts: list[str] = []
    params: list[Any] = []

    for i, literal in enumerate(template.strings):
        parts.append(literal)
        if i < len(template.interpolations):
            interp = template.interpolations[i]
            expr = interp.expression

            # Simple identifiers (table/column names) go inline — SQL requires it
            # Everything else gets parameterized for injection safety
            if expr and _is_identifier(expr) and isinstance(interp.value, str):
                parts.append(str(interp.value))
            else:
                parts.append("?")
                params.append(interp.value)

    return "".join(parts), params


def safe_html(template) -> str:
    """Format a t-string with HTML-escaped interpolated values.

    All interpolated values are HTML-escaped to prevent XSS.
    Literal string parts are included as-is (assumed to be trusted template).

    Args:
        template: A Template object from t-string syntax.

    Returns:
        HTML-safe formatted string.

    Example:
        user_input = '<script>alert("xss")</script>'
        safe_html('Hello {user_input}')
        # -> 'Hello &lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'
    """
    parts: list[str] = []

    for i, literal in enumerate(template.strings):
        parts.append(literal)
        if i < len(template.interpolations):
            interp = template.interpolations[i]
            val = interp.value
            escaped = html.escape(str(val))
            parts.append(escaped)

    return "".join(parts)


def structured(template) -> dict[str, Any]:
    """Extract structured data from a t-string for indexing and storage.

    Returns the raw template string and a dict of variable values,
    enabling downstream systems to index by variable name.

    Args:
        template: A Template object from t-string syntax.

    Returns:
        dict with 'template' (raw template string) and 'values' (variable dict).

    Example:
        agent = "Agent-1"
        status = "running"
        result = structured('{agent} is {status}')
        # -> {"template": "{agent} is {status}", "values": {"agent": "Agent-1", "status": "running"}}
    """
    # Reconstruct the raw template string from strings and interpolation expressions
    template_parts: list[str] = []
    for i, literal in enumerate(template.strings):
        template_parts.append(literal)
        if i < len(template.interpolations):
            interp = template.interpolations[i]
            expr = interp.expression or ""
            fmt_spec = interp.format_spec
            conversion = interp.conversion

            # Reconstruct {expr!conversion:format_spec}
            interp_str = expr
            if conversion:
                interp_str += f"!{conversion}"
            if fmt_spec:
                interp_str += f":{fmt_spec}"
            template_parts.append(f"{{{interp_str}}}")

    raw_template = "".join(template_parts)

    # Extract values keyed by expression
    values = {}
    for interp in template.interpolations:
        expr = interp.expression
        if expr:
            values[expr] = interp.value

    return {"template": raw_template, "values": values}


# ── Internal Helpers ─────────────────────────────────────────────────────────


def _is_variable_ref(expr: str) -> bool:
    """Check if an expression looks like a simple variable reference.

    Returns True for 'name', 'agent.status', 'obj.attr'
    Returns False for '42', '"hello"', 'func()', 'a + b'
    """
    if not expr:
        return False
    # Simple variable or dotted attribute access
    return bool(re.match(r'^[a-zA-Z_][a-zA-Z0-9_.]*$', expr))


def _is_identifier(expr: str) -> bool:
    """Check if an expression is a simple identifier (for SQL column/table names)."""
    if not expr:
        return False
    return bool(re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', expr))


def _sanitize_for_log(val: Any) -> str:
    """Sanitize a value for inclusion in log context strings."""
    s = str(val)
    # Truncate very long values
    if len(s) > 200:
        s = s[:197] + "..."
    # Escape newlines and brackets that could break log parsing
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("[", "\\[").replace("]", "\\]")
    return s


def _sanitize_for_storage(val: Any) -> Any:
    """Sanitize a value for PAMS storage. Preserves types when possible."""
    if isinstance(val, (str, int, float, bool, type(None))):
        return val
    if isinstance(val, (list, tuple)):
        return [_sanitize_for_storage(v) for v in val]
    if isinstance(val, dict):
        return {k: _sanitize_for_storage(v) for k, v in val.items()}
    return str(val)
