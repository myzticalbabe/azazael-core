# coding: utf-8
"""


__all__ = [
    "Interpolation",
    "SafeStatement",
    "Token",
    "TokenKind",
    "VERSION",
    "sql",
]

safe_sql.py — Safe SQL construction with parameterized queries.

Since PEP 750 (t-strings) is still a draft proposal and not available in
Python 3.14t, this module provides the same conceptual API using regular
function syntax. A future migration path is marked for when t-strings land.

Usage:
    from CORE.safe_sql import sql, Interpolation

    # Build safe parameterized queries
    stmt = sql("SELECT * FROM memories WHERE source = {src}", src="SystemZ")
    # stmt.sql  -> "SELECT * FROM memories WHERE source = %s"
    # stmt.params -> ("SystemZ",)

    # IN clauses (list values auto-expand)
    stmt = sql("SELECT * FROM memories WHERE event_type IN {types}",
               types=["error", "warning"])
    # stmt.sql  -> "SELECT * FROM memories WHERE event_type IN (%s, %s)"
    # stmt.params -> ("error", "warning")

    # Identifier (column/table name) interpolation — use :ident format
    stmt = sql("SELECT {col:ident}, {col2:ident} FROM {tbl:ident}",
               col="source", col2="event_type", tbl="memories")
    # stmt.sql  -> "SELECT "source", "event_type" FROM "memories""
    # (identifiers are double-quoted for safety)

    # Introspection (PEP 750 Interpolation pattern)
    it = Interpolation(value="SystemZ", expr="src", fmt_spec="", conv="")

Future Migration (when PEP 750 lands in Python):
    Replace:
        stmt = sql("SELECT * FROM {tbl:ident} WHERE source = {src}",
                    tbl="memories", src="SystemZ")
    With:
        def my_handler(t):
            return process_t_string(t)
        stmt = my_handler(f"SELECT * FROM {tbl:ident} WHERE source = {src}")
"""

import re
from dataclasses import dataclass
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

VERSION = "1.0.0"


# -- Interpolation (PEP 750-style introspection object) --------------

@dataclass
class Interpolation:
    """A single interpolation in a template string (PEP 750 pattern).

    Attributes:
        value: The evaluated expression value.
        expr: The raw expression text (e.g., ``"user.name"``).
        fmt_spec: The format specification after ``:`` (e.g., ``"ident"``).
        conv: The conversion flag after ``!`` (e.g., ``"r"``, ``"s"``).
    """
    value: Any
    expr: str = ""
    fmt_spec: str = ""
    conv: str = ""


# -- Token types -----------------------------------------------------

class TokenKind:
    LITERAL = "literal"
    VALUE = "value"       # {name}   -> parameterized %s
    IDENT = "ident"       # {name:ident} -> quoted identifier
    RAW = "raw"           # {name:raw}  -> direct injection (use sparingly)


@dataclass
class Token:
    kind: str
    text: str = ""
    value: Any = None
    expr: str = ""


# -- SafeStatement ---------------------------------------------------

class SafeStatement:
    """A parameterized SQL statement built by :func:`sql`.

    Attributes:
        sql: The SQL string with ``%s`` placeholders.
        params: tuple of values for the placeholders.
        identifiers: tuple of identifier names (for tracking).
        interpolations: list of :class:`Interpolation` objects (PEP 750 style).
    """

    def __init__(self):
        self.sql: str = ""
        self.params: tuple[Any, ...] = ()
        self.identifiers: tuple[str, ...] = ()
        self.interpolations: list[Interpolation] = []
        self._parts: list[str] = []
        self._param_list: list[Any] = []
        self._ident_list: list[str] = []

    def __repr__(self) -> str:
        return f"<SafeStatement sql={self.sql!r} params={self.params!r}>"

    def __str__(self) -> str:
        return self.sql % tuple(
            f"'{_escape_literal(p)}'" if isinstance(p, str) else str(p)
            for p in self.params
        )

    def _finalize(self):
        self.sql = "".join(self._parts).strip()
        self.params = tuple(self._param_list)
        self.identifiers = tuple(self._ident_list)

    def build(self, template: str, **kwargs) -> "SafeStatement":
        """Parse a template string and build the safe statement."""
        tokens = _tokenize(template)
        self._param_list = []
        self._ident_list = []
        self._parts = []
        self.interpolations = []

        for token in tokens:
            if token.kind == TokenKind.LITERAL:
                self._parts.append(token.text)

            elif token.kind == TokenKind.VALUE:
                if token.text not in kwargs:
                    raise ValueError(
                        f"Missing parameter: {{{token.text}}}.\n"
                        f"Available kwargs: {list(kwargs)}"
                    )
                val = kwargs[token.text]
                it = Interpolation(value=val, expr=token.text)
                self.interpolations.append(it)

                if isinstance(val, (list, tuple)):
                    # IN clause — one placeholder per element
                    if not val:
                        # Empty list — use a never-match condition
                        self._parts.append("(1=0)")
                    else:
                        placeholders = "(%s)" % ", ".join(["%s"] * len(val))
                        self._parts.append(placeholders)
                        self._param_list.extend(val)
                else:
                    self._parts.append("%s")
                    self._param_list.append(val)

            elif token.kind == TokenKind.IDENT:
                if token.text not in kwargs:
                    raise ValueError(
                        f"Missing identifier: {{{token.text}:ident}}.\n"
                        f"Available kwargs: {list(kwargs)}"
                    )
                val = kwargs[token.text]
                it = Interpolation(value=val, expr=token.text, fmt_spec="ident")
                self.interpolations.append(it)

                if isinstance(val, str):
                    self._parts.append(_quote_ident(val))
                    self._ident_list.append(val)
                elif isinstance(val, (list, tuple)):
                    parts = ", ".join(_quote_ident(v) for v in val)
                    self._parts.append(parts)
                    self._ident_list.extend(val)
                else:
                    raise TypeError(
                        f"Identifier must be str or list/tuple, got {type(val).__name__}"
                    )

            elif token.kind == TokenKind.RAW:
                if token.text not in kwargs:
                    raise ValueError(
                        f"Missing raw parameter: {{{token.text}:raw}}.\n"
                        f"Available kwargs: {list(kwargs)}"
                    )
                val = kwargs[token.text]
                if not isinstance(val, str):
                    raise TypeError(
                        f"Raw parameter must be str, got {type(val).__name__}"
                    )
                it = Interpolation(value=val, expr=token.text, fmt_spec="raw")
                self.interpolations.append(it)
                self._parts.append(val)

        self._finalize()
        return self


# -- Public API ------------------------------------------------------

def sql(template: str, **kwargs) -> SafeStatement:
    """Build a safe parameterized SQL statement.

    Use ``{name}`` for values (becomes ``%s`` with separated params),
    ``{name:ident}`` for identifiers (column/table names, auto-quoted),
    and ``{name:raw}`` for rare direct injection cases.

    Args:
        template: SQL template with ``{placeholder}`` markers.
        **kwargs: Values for each placeholder.

    Returns:
        A :class:`SafeStatement` with ``.sql`` and ``.params`` attributes.

    Example::
        stmt = sql("SELECT * FROM {table:ident} WHERE source = {src}",
                   table="memories", src="SystemZ")
        cur.execute(stmt.sql, stmt.params)
    """
    return SafeStatement().build(template, **kwargs)


# -- Internal: Tokenizer ---------------------------------------------

_PLACEHOLDER_RE = re.compile(
    r"\{(?P<name>[a-zA-Z_][a-zA-Z0-9_.]*?)(?:\:(?P<fmt>ident|raw))?\}"
)


def _tokenize(template: str) -> list[Token]:
    """Tokenize a template string into literal and placeholder tokens."""
    tokens: list[Token] = []
    last_end = 0

    for match in _PLACEHOLDER_RE.finditer(template):
        start, end = match.start(), match.end()

        # Literal text before this placeholder
        if start > last_end:
            tokens.append(Token(kind=TokenKind.LITERAL, text=template[last_end:start]))

        name = match.group("name")
        fmt = match.group("fmt")

        if fmt == "ident":
            tokens.append(Token(kind=TokenKind.IDENT, text=name))
        elif fmt == "raw":
            tokens.append(Token(kind=TokenKind.RAW, text=name))
        else:
            tokens.append(Token(kind=TokenKind.VALUE, text=name))

        last_end = end

    # Remaining literal text
    if last_end < len(template):
        tokens.append(Token(kind=TokenKind.LITERAL, text=template[last_end:]))

    return tokens


# -- Internal: Helpers -----------------------------------------------


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier (column/table name) safely."""
    if not name:
        raise ValueError("Empty identifier")
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
        raise ValueError(
            f"Invalid identifier: {name!r}. "
            f"Identifiers must match [a-zA-Z_][a-zA-Z0-9_]*"
        )
    return f'"{name}"'


def _escape_literal(val: str) -> str:
    """Escape a string literal for safe display (not for SQL execution).

    This is only for __str__ representation, not for actual SQL building.
    """
    return val.replace("'", "''")
