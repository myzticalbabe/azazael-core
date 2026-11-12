#!/usr/bin/env python3
# coding: utf-8
"""Encoding audit — find calls that move text through the OS codec.

The failure this detects, measured
----------------------------------
On Windows a `subprocess` call that captures output in TEXT mode without
`encoding=` lets the platform codec (cp1252) decode it. Non-ASCII output then
raises `UnicodeDecodeError` **inside subprocess's reader thread**, where it does
NOT propagate to the caller: the call appears to succeed and yields EMPTY
stdout. Measured 2026-10-13 during ratchet attribution work — a file with two
findings was scored as having zero, and a wrong per-file attribution was
reported from that phantom reading before the empty stdout was traced back to
the decode failure.

Why this parses instead of grepping
-----------------------------------
A same-line grep is not a valid detector, and this was measured too: grepping
`tools/` and `scripts/` for text-capture call sites reported 155 candidate
lines, but `encoding=` is frequently on a CONTINUATION line and is invisible to
a line-oriented match, while a `capture_output=True` call that never enables
text mode decodes nothing and cannot fail this way. Both errors point the same
direction — the question is about a call's KEYWORDS, so the source has to be
parsed. This tool walks the AST and inspects each call's kwargs.

What counts as decoding
-----------------------
`text=True` and `universal_newlines=True` decode. `encoding=` and `errors=`
only have an effect in text mode, so passing either IS text mode:
`subprocess.run(cmd, errors="replace")` decodes with the OS codec and that
handler, which is exactly the silent-truncation shape. A call that captures
bytes (`capture_output=True` with no text mode) is NOT reported — it decodes
nothing.

The console-print rule (`console_print`, opt-in via `--print-side`)
--------------------------------------------------------------------
The decode half above is about getting text INTO the process. It can still be
lost on the way OUT: a reporting tool that `print()`s captured subprocess
output — or a non-ASCII literal — without any `reconfigure()` of
sys.stdout/sys.stderr and without an encoding/sanitising transform on the
payload encodes that text with the CONSOLE codec at print time. On this host
that codec is cp1252, so under it the payload raises UnicodeEncodeError inside
the report path (measured: session_start.py, where the raise landed inside an
`except Exception` and was relabelled as "store unreachable" — engram #706) or
renders mojibake (measured: verify_hashes.py status glyphs). This rule flags
that shape and is configurable through
`--disable console_print` / `--console-utf8`; the disabled-path control that
would fail loudly if the detector were stubbed is
tests/test_encoding_write_side.py.

Known gaps, named rather than assumed
-------------------------------------
`subprocess.getoutput` / `getstatusoutput` always return `str` and take no
`encoding=`, so they cannot be fixed, only replaced by `subprocess.run(...)`.
Neither appears in `tools/` or `scripts/`, and `from subprocess import run`
appears only in archived vendored trees, so neither form is modelled here. If
one is introduced, this tool will not see it — re-check with
`grep -rn "getoutput\\|from subprocess import" tools/ scripts/` rather than
assume coverage. `import subprocess as _sp` IS modelled: it is the form in use
in `scripts/spine_cycle_apply.py`, and a bare `_sp.run(...)` is invisible to a
check that only looks for the literal name `subprocess`.

Console-print rule gaps: the payload-flow tracking is per-scope (module body,
each function) and one transform deep — a capture returned by a helper
function and printed in its caller is NOT modelled (that is the deliberate
shape of the clean fixture in the tests, which instead routes through
`reconfigure` and a sanitizer). `sys.stdout.buffer.write`, `os.write`, and a
user-bound `PIPE = subprocess.PIPE` alias are not modelled. Any call to a
method named `reconfigure` anywhere in the module exempts it whole —
deliberate leniency, because the convention it mirrors records a degraded
reconfigure and still sanitizes. An unrecognized sanitizer name is not
exempted unless its own body carries the `.encode()`/`.decode()` shape or the
name matches `sanit|scrub|ascii|emit`. A print classified as payload-flow is
not also tested for a lossy literal in the same argument. And the rule is OFF
unless `--print-side` is passed: a default run, including `--check`, decides
nothing about it.

Usage
-----
    python tools/encoding_audit.py                 # report, exit 0
    python tools/encoding_audit.py --check         # gate: exit 1 on any finding
    python tools/encoding_audit.py --dir tools --dir scripts
    python tools/encoding_audit.py --json
    python tools/encoding_audit.py --print-side    # + console-write blind spots
    python tools/encoding_audit.py --console-utf8  # UTF-8 console scenario
    python tools/encoding_audit.py --disable console_print   # disable the rule
"""
from __future__ import annotations

import argparse
import ast
import codecs
import json
import logging
import sys
from pathlib import Path

_LOG = logging.getLogger("encoding_audit")

ROOT = Path(__file__).resolve().parents[1]

#: Scan roots when `--dir` is not given, repo-relative.
#: `src/` and `tests/` are in the default deliberately (#999): a default that
#: skipped them certified two helper trees while 1,100+ production and test
#: modules went unexamined -- a clean `--check` that names the wrong tree is
#: worse than no check, because it is read as evidence.
DEFAULT_DIRS = ("src", "tools", "scripts", "tests", "packages")

#: subprocess entry points that accept the same text-mode kwargs.
ENTRY_POINTS = ("run", "Popen", "check_output", "check_call", "call")

#: Kwargs that turn text mode on by themselves; `encoding`/`errors` are inert
#: outside text mode, so their presence implies it (handled in `_decoding`).
TEXT_KWARGS = ("text", "universal_newlines")

#: Mode characters that make an `open()` a WRITE (encode) of text. `b` is
#: excluded below -- a binary write encodes nothing and cannot hit this defect.
WRITE_MODE_CHARS = ("w", "a", "x", "+")


# ---------- WRITE-side counterpart (the gap this tool shipped without) ---------
# The READ-side scan below catches a subprocess capture that DECODES with the OS
# codec. The mirror defect was left uncovered (engram #706/#707): `open(p, "w")`
# or `Path(p).write_text(...)` with no `encoding=` ENCODES with the OS codec
# (cp1252 on this host), so a non-ASCII string is written as mojibake or raises
# mid-write. A detector whose whole purpose is "we do not lose text" cannot stop
# at half the boundary.


# Receivers whose `open()` produces a BINARY archive handle. `tarfile.open(p,
# "w:gz")` matched the name rule below and was reported as a text write; passing
# `encoding=` to it raises TypeError, so a hit there is a detector defect, not a
# site to fix (5 sites in src/TOOLS/archive_system.py, 1 in TARBABY).
ARCHIVE_OPEN_RECEIVERS = frozenset(
    {"tarfile", "zipfile", "gzip", "bz2", "lzma", "zlib", "shutil"})
# Compression suffixes make the handle binary even when no "b" is written.
ARCHIVE_MODE_MARKERS = (":gz", ":bz2", ":xz", ":lzma", "|")


def _receiver_root(func: ast.AST) -> str:
    """Root identifier of a call receiver: `a.b.open` -> 'a', `open` -> ''."""
    value = getattr(func, "value", None)
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else ""


def _mode_writes(mode: str) -> bool:
    """True when an `open()` mode writes text (not a read, not binary)."""
    if any(marker in mode for marker in ARCHIVE_MODE_MARKERS):
        return False
    return any(c in mode for c in WRITE_MODE_CHARS) and "b" not in mode


def write_findings(tree: ast.AST, lines: "list[str]") -> "list[dict]":
    """Text writes (`open`/`write_text`) that omit an explicit `encoding=`."""
    out: "list[dict]" = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else "")
        if name not in ("open", "write_text"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        if "encoding" in kw:
            continue
        if name == "open":
            if _receiver_root(func) in ARCHIVE_OPEN_RECEIVERS:
                continue
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            mkw = kw.get("mode")
            if isinstance(mkw, ast.Constant):
                mode = mkw.value
            mode = mode if isinstance(mode, str) else "r"
            if not _mode_writes(mode):
                continue
            label = 'open(mode=%r)' % mode
        else:
            label = "Path.write_text()"
        out.append({"line": node.lineno, "call": label,
                    "source": (lines[node.lineno - 1].strip()[:120]
                               if node.lineno <= len(lines) else "")})
    return out


# ---------- Console-print rule (the WRITE of reported text to a terminal) ------
# RULE (console_print) — the detection heuristic this function realizes.
#
# A REPORTING tool is flagged when, with NO call to a method named
# `reconfigure` anywhere in the module (a `reconfigure` is a TextIOWrapper-only
# method, and the estate convention in tools/session_start.py calls it on a
# loop variable over (sys.stdout, sys.stderr), so the method NAME is matched,
# not the receiver), a `print(...)` or a `sys.stdout.write(...)` / 
# `sys.stderr.write(...)` (stderr hits the same console) writes text that
#
#   (a) flows from subprocess output: the argument names a value assigned
#       from a subprocess call that CAPTURES text (`capture_output=True`
#       literal, `stdout=subprocess.PIPE`, or `subprocess.check_output`) and
#       is text-typed (`text=True` / `encoding=` / `errors=` literally
#       present). A symbolic text kwarg (`text=flag`, `**opts`) counts as
#       captured and is never cleared. A bytes-typed capture counts as clean,
#       because `print(bytes)` prints a repr and encodes nothing; or
#   (b) is a literal whose value the ASSUMED console codec cannot encode,
#       tested by actually attempting `literal.encode(console)`.
#
# ...unless the printed argument bears an encoding/sanitising transform of its
# own: a `.encode()`/`.decode()` call inside the argument subtree, a call whose
# name matches PRINT_SANITIZE_HINTS (the `session_start._emit` convention), or
# the printed name last assigned from such a transform. A `# scanner:ignore`
# comment on the print statement's OPENING line suppresses that one site — the
# same per-line marker the rest of this repo uses as a disabled-rule idiom. The
# whole rule is additionally gateable by name (RULES_ENABLED / `--disable
# console_print`) and by console scenario (`--console-utf8`): under a UTF-8
# console the rule reports nothing, because a UTF-8 encoding cannot lose text.
#
# Measured precedents: tools/verify_hashes.py status glyphs printed to a
# cp1252 console; session_start.py U+2192/U+FFFD store payloads reaching a
# cp1252 console through bare `print()` inside an `except Exception`.

#: Name the rule registers under. `--disable console_print` targets this.
CONSOLE_PRINT_RULE = "console_print"

#: The console codec assumed when none is given — this host's Windows default.
#: `--console-utf8` swaps in the UTF-8 scenario, under which no print loses
#: text, so the rule has nothing to report.
CONSOLE_DEFAULT = "cp1252"

#: Method-name that marks a module's console streams as re-encoded. Deliberately
#: receiver-agnostic (see the RULE comment).
CONSOLE_RECONFIGURE_ATTR = "reconfigure"

#: Substrings one call-name or one printed-name's defining expression may match
#: to count as a sanitizer for cp1252. `emit` is in the list because the estate
#: convention names its console sanitizer `_emit` (tools/session_start.py).
PRINT_SANITIZE_HINTS = ("sanit", "scrub", "ascii", "emit")

#: The per-site suppression marker. Same string the repo canon treats as a
#: disabled-rule mechanism (`# scanner:ignore`); placed on the print
#: statement's opening line, it suppresses that site's finding.
PRINT_SUPPRESSION_MARKER = "scanner:ignore"


def _capture_decoding(value: ast.expr, aliases: "set[str]",
                      from_names: "set[str]") -> "tuple[str, bool]":
    """Classify one expression as a text-capturing subprocess call, or not.

    Returns (kind, splat-is-unknown) with kind one of
    no_capture | bytes | undetermined | text.
    """
    if not isinstance(value, ast.Call):
        return ("no_capture", False)
    stdout_kw = None
    for kw in value.keywords:
        if kw.arg is None:
            return ("undetermined", False)  # **opts could set anything
        if kw.arg == "stdout":
            stdout_kw = kw.value
    entry = _entry_point(value, aliases, from_names)
    is_check_output = entry == "subprocess.check_output"
    if is_check_output and _pipe_value_or_none(stdout_kw, aliases):
        pass                                  # check_output captures by contract
    elif not _capture_output_on(value):
        return ("no_capture", False)
    info = _decoding(value)
    if info["confirmed"]:
        return ("text", False)
    if info["unknown"]:
        return ("undetermined", True)
    return ("bytes", False)


def _capture_output_on(node: ast.Call) -> bool:
    """True when the call turns capture_output on with a literal True."""
    for kw in node.keywords:
        if (kw.arg == "capture_output" and isinstance(kw.value, ast.Constant)
                and bool(kw.value.value)):
            return True
    return False


def _pipe_value_or_none(value: "ast.expr | None", aliases: "set[str]") -> bool:
    """True when a value is `<subprocess-alias>.PIPE`."""
    return (isinstance(value, ast.Attribute) and value.attr == "PIPE"
            and isinstance(value.value, ast.Name)
            and value.value.id in aliases)


def _assign_targets(node: ast.Assign) -> "list[str]":
    """The plain names an assignment binds (Tuple targets unpacked)."""
    out: "list[str]" = []
    for target in node.targets:
        if isinstance(target, ast.Name):
            out.append(target.id)
        elif isinstance(target, ast.Tuple):
            out.extend(elt.id for elt in target.elts
                       if isinstance(elt, ast.Name))
    return out


def _reconfigured(tree: ast.AST) -> bool:
    """True when any `reconfigure` call exists in the module."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == CONSOLE_RECONFIGURE_ATTR):
            return True
    return False


def _walk_scope(stmts: "list[ast.stmt]") -> "ast.Module":
    """A walkable module wrapper around one scope's statement list."""
    return ast.Module(body=stmts, type_ignores=[])


def _print_sites(stmts: "list[ast.stmt]") -> "list[tuple[ast.Call, str]]":
    """The print/stdout-write/stderr-write call sites in one scope, in order."""
    out: "list[tuple[ast.Call, str]]" = []
    for node in ast.walk(_walk_scope(stmts)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "print":
            out.append((node, "print"))
        elif (isinstance(func, ast.Attribute) and func.attr == "write"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr in ("stdout", "stderr")
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "sys"):
            out.append((node, "sys.%s" % func.value.attr))
    return out


def _capture_records(stmts: "list[ast.stmt]", aliases: "set[str]",
                     from_names: "set[str]") -> "dict[str, dict]":
    """Per-scope records of names that hold captured subprocess text.

    One line-ordered pass over the scope's assignments (ast.walk is breadth-
    first, so Assign nodes are explicitly sorted by (lineno, col_offset) —
    the rule needs "capture before print", which is a line question). A direct
    capture assignment binds its targets with the capture's kind; a chain
    assignment whose value mentions an already-recorded name inherits that
    kind and re-tests its own value for a sanitizing transform. Bytes captures
    are deliberately NOT recorded: a print of bytes writes a repr and encodes
    nothing.
    """
    records: "dict[str, dict]" = {}
    assigns = [n for n in ast.walk(_walk_scope(stmts))
               if isinstance(n, ast.Assign)]
    assigns.sort(key=lambda n: (n.lineno, n.col_offset))
    for node in assigns:
        targets = _assign_targets(node)
        if not targets:
            continue
        sanitized = _transformed(node.value)
        kind = _capture_kind(node.value, aliases, from_names, records)
        if kind in ("text", "undetermined"):
            for name in targets:
                records[name] = {"lineno": node.lineno, "type": kind,
                                 "sanitized": sanitized}
    return records


def _capture_kind(value: ast.expr, aliases: "set[str]",
                  from_names: "set[str]",
                  records: "dict[str, dict]") -> "str | None":
    """The payload kind an assignment's value carries, or None for nothing.

    Order matters: a direct capture call in the value wins over a chain
    through an already-recorded name (recapture beats inheritance); a chain
    only counts through names recorded EARLIER in the same scope, so it cites
    the record because the payload began there.
    """
    for sub in ast.walk(value) if isinstance(value, ast.expr) else ():
        if isinstance(sub, ast.Call):
            kind = _capture_decoding(sub, aliases, from_names)
            if kind in ("text", "undetermined"):
                return kind
    for sub in ast.walk(value):
        if isinstance(sub, ast.Name) and sub.id in records:
            return records[sub.id]["type"]
    return None


def _transformed(value: ast.expr) -> bool:
    """True when the value's own subtree carries an encoding/sanitizing shape."""
    for sub in ast.walk(value):
        if not isinstance(sub, ast.Call):
            continue
        name = _call_name(sub)
        if name in ("encode", "decode"):
            return True
        if any(hint in name for hint in PRINT_SANITIZE_HINTS):
            return True
    return False


def _call_name(node: ast.Call) -> str:
    """The called function's bare identifier, or '' when it has none."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _loses_literal(value: ast.expr, console: str) -> bool:
    """True when a literal in the subtree has a char the console codec drops.

    Each candidate code point is encoded one at a time: a non-ASCII char that
    cp1252 (or whatever console codec) cannot represent is exactly the glyph
    that makes the print either raise UnicodeEncodeError or degrade under an
    errors= handler.
    """
    for sub in ast.walk(value):
        if not (isinstance(sub, ast.Constant) and isinstance(sub.value, str)):
            continue
        for char in sub.value:
            if ord(char) <= 0x7F:
                continue
            try:
                char.encode(console)
            except (UnicodeEncodeError, LookupError, ValueError) as exc:
                # The encode failure IS the signal here, not an error being
                # swallowed into a default: record which glyph lost which
                # console codec, then report the loss. Two statements in the
                # body, so no except_return_default finding to suppress.
                _LOG.debug("literal char %r loses console codec %s: %s",
                           char, console, exc)
                return True
    return False


def print_findings(tree: ast.AST, lines: "list[str]",
                   console: str = CONSOLE_DEFAULT) -> "list[dict]":
    """Console writes that can lose text; see the RULE comment above.

    Scanned per scope (the module body, and each function): a print of a
    captured-text payload, or of a literal the assumed console codec cannot
    encode. A module carrying any `reconfigure` call is exempt whole. Rows
    carry kind ('subflow' | 'literal') and the same line/call/source shape as
    the other rule families.
    """
    try:
        if codecs.lookup(console).name == "utf-8":
            return []  # a UTF-8 encode cannot lose text; scenario is clean
    except (LookupError, ValueError):
        return []  # an unknown codec cannot be reasoned about — no guesses
    aliases, from_names = _subprocess_names(tree)
    if _reconfigured(tree):
        return []
    scopes: "list[tuple[str, list[ast.stmt]]]" = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(("function", node.body))
    scopes.append(("module", [stmt for stmt in tree.body
                              if not isinstance(stmt, (ast.FunctionDef,
                                                       ast.AsyncFunctionDef,
                                                       ast.ClassDef))]))
    out: "list[dict]" = []
    for _scope_name, stmts in scopes:
        records = _capture_records(stmts, aliases, from_names)
        for node, label in _print_sites(stmts):
            row = _finding_row(node, label, records, aliases, from_names,
                               console, lines)
            if row is not None:
                out.append(row)
    return out


def _finding_row(node: ast.Call, label: str, records: "dict[str, dict]",
                 aliases: "set[str]", from_names: "set[str]", console: str,
                 lines: "list[str]") -> "dict | None":
    """Classify one print/console-write site, or None when it must stay silent."""
    if (node.lineno <= len(lines)
            and PRINT_SUPPRESSION_MARKER in lines[node.lineno - 1]):
        return None
    args = list(node.args) + [kw.value for kw in node.keywords]
    flow = False
    handled = False
    for arg in args:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Call):
                name = _call_name(sub)
                if name in ("encode", "decode"):
                    handled = True
                elif any(hint in name for hint in PRINT_SANITIZE_HINTS):
                    handled = True
                else:
                    kind = _capture_decoding(sub, aliases, from_names)
                    if kind in ("text", "undetermined"):
                        flow = True
            elif isinstance(sub, ast.Name) and sub.id in records:
                record = records[sub.id]
                if record["lineno"] < node.lineno:
                    if record["sanitized"]:
                        handled = True
                    else:
                        flow = True
    if handled:
        return None
    kind = "subflow" if flow else None
    if kind is None and _loses_literal(ast.Tuple(elts=args, ctx=ast.Load()),
                                       console):
        kind = "literal"
    if kind is None:
        return None
    return {"line": node.lineno, "call": label, "kind": kind,
            "source": (lines[node.lineno - 1].strip()[:120]
                       if node.lineno <= len(lines) else "")}


def _iter_py_files(dirs: "list[str]") -> "list[Path]":
    """Every `.py` file under each `--dir` (a file argument is taken as-is)."""
    out: "list[Path]" = []
    for entry in dirs:
        base = Path(entry)
        base = base if base.is_absolute() else (ROOT / entry)
        if base.is_file():
            if base.suffix == ".py":
                out.append(base)
            continue
        if not base.is_dir():
            continue
        out.extend(sorted(p for p in base.rglob("*.py")
                          if "__pycache__" not in p.parts))
    return out


def _subprocess_names(tree: ast.AST) -> "tuple[set[str], set[str]]":
    """Return (names bound to the module, entry points imported from it).

    Both import forms are collected because a detector whose whole purpose is
    not missing a site cannot afford to understand only one spelling of it.
    """
    aliases: "set[str]" = set()
    from_names: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    aliases.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                for alias in node.names:
                    if alias.name in ENTRY_POINTS:
                        from_names.add(alias.asname or alias.name)
    return aliases, from_names


def _entry_point(node: ast.Call, aliases: "set[str]",
                 from_names: "set[str]") -> "str | None":
    """The subprocess function this call invokes, or None if it is not one."""
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in aliases and func.attr in ENTRY_POINTS:
            return "subprocess.%s" % func.attr
        return None
    if isinstance(func, ast.Name) and func.id in from_names:
        return "subprocess.%s" % func.id
    return None


def _decoding(node: ast.Call) -> dict:
    """Classify one call's decoding kwargs.

    Three states, because conflating the last two is how a detector starts
    reporting things it has not checked:

      confirmed  — text mode is definitely on (literal `text=True`, or an
                   `encoding`/`errors` kwarg, whose presence implies text mode).
      undetermined — `**kwargs` could supply the text flags, or a text kwarg
                   holds a non-literal value, so a constant cannot settle it.
      clean      — bytes were captured, or `encoding=` is present.
    """
    kwargs: "dict[str, ast.expr]" = {}
    splat = False
    for kw in node.keywords:
        if kw.arg is None:
            splat = True
        else:
            kwargs[kw.arg] = kw.value

    def _literal_true(name: str) -> bool:
        value = kwargs.get(name)
        return isinstance(value, ast.Constant) and bool(value.value)

    symbolic = [name for name in TEXT_KWARGS
                if name in kwargs and not isinstance(kwargs[name], ast.Constant)]
    confirmed = (_literal_true("text") or _literal_true("universal_newlines")
                 or "encoding" in kwargs or "errors" in kwargs)
    return {"confirmed": confirmed, "has_encoding": "encoding" in kwargs,
            "splat": splat, "symbolic": symbolic,
            "unknown": bool(symbolic) or splat}


def scan_file(path: Path) -> dict:
    """Return the audit result for one file.

    A file that will not parse is REPORTED as skipped with its error rather than
    silently contributing zero sites -- an unreadable file and a clean file must
    not look the same in the output, which is the whole lesson of this tool.
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"path": path, "scanned": False, "findings": [], "undetermined": [],
                "writes": [], "skipped": "line %s: %s" % (exc.lineno, exc.msg)}

    aliases, from_names = _subprocess_names(tree)
    lines = source.splitlines()
    findings: "list[dict]" = []
    undetermined: "list[dict]" = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        entry = _entry_point(node, aliases, from_names)
        if entry is None:
            continue
        info = _decoding(node)
        row = {"line": node.lineno, "call": entry,
               "source": (lines[node.lineno - 1].strip()[:120]
                          if node.lineno <= len(lines) else "")}
        if info["confirmed"] and not info["has_encoding"]:
            row["note"] = ("**kwargs may supply encoding" if info["splat"] else "")
            findings.append(row)
        elif info["unknown"]:
            row["note"] = ("**kwargs present" if info["splat"]
                           else "%s is not a literal" % ", ".join(info["symbolic"]))
            undetermined.append(row)
    return {"path": path, "scanned": True, "findings": findings,
            "undetermined": undetermined, "writes": write_findings(tree, lines),
            "skipped": None}


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", action="append", default=None, metavar="PATH",
                    help="file or directory to scan, repeatable "
                         "(default: %s)" % " ".join(DEFAULT_DIRS))
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when any text-decoding call lacks encoding=")
    ap.add_argument("--json", action="store_true",
                    help="emit the same result as JSON")
    ap.add_argument("--write-side", action="store_true",
                    help="also report text WRITES (open/write_text) that omit "
                         "encoding=; included in --check")
    args = ap.parse_args()

    dirs = args.dir or list(DEFAULT_DIRS)
    files = _iter_py_files(dirs)

    by_file: "dict[str, list[dict]]" = {}
    undetermined: "dict[str, list[dict]]" = {}
    writes_by_file: "dict[str, list[dict]]" = {}
    skipped: "list[str]" = []
    for path in files:
        result = scan_file(path)
        rel = _relative(path)
        if result["skipped"]:
            skipped.append("%s: %s" % (rel, result["skipped"]))
            continue
        if result["findings"]:
            by_file[rel] = result["findings"]
        if result["undetermined"]:
            undetermined[rel] = result["undetermined"]
        if result["writes"]:
            writes_by_file[rel] = result["writes"]

    total = sum(len(rows) for rows in by_file.values())
    write_total = sum(len(rows) for rows in writes_by_file.values())

    if args.json:
        # The write side is part of the same report, not a printing side-effect:
        # a JSON consumer (state/encoding_slice_manifest.json is built from this)
        # cannot see a site that only exists in the human-readable block, and an
        # exit code that ignores write_total certifies a tree it did not check.
        payload = {"scanned": len(files), "dirs": dirs, "total": total,
                   "findings": by_file, "undetermined": undetermined,
                   "write_total": write_total, "writes": writes_by_file,
                   "skipped": skipped}
        print(json.dumps(payload, indent=2))
        failed = bool(total) or bool(args.write_side and write_total)
        return 1 if (args.check and failed) else 0

    print("encoding audit -- %s" % ", ".join(dirs))
    print("  scanned:  %d file(s)" % len(files))
    print("  missing:  %d text-decoding call(s) without encoding= in %d file(s)"
          % (total, len(by_file)))

    if by_file:
        print()
        print("  MISSING encoding= -- these decode with the OS codec, so non-ASCII")
        print("  output raises in the reader thread and leaves stdout EMPTY:")
        for rel, rows in sorted(by_file.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            print("    %5d  %s" % (len(rows), rel))
            for row in rows:
                print("          line %-6s %s%s"
                      % (row["line"], row["call"],
                         ("  (%s)" % row["note"]) if row["note"] else ""))

    if undetermined:
        print()
        print("  UNDETERMINED -- NOT counted above; the kwargs are not statically")
        print("  knowable, so these were neither cleared nor reported as findings:")
        for rel, rows in sorted(undetermined.items()):
            for row in rows:
                print("    %s:%s  %s  (%s)"
                      % (rel, row["line"], row["call"], row["note"]))

    if args.write_side and writes_by_file:
        print()
        print("  MISSING encoding= on WRITES -- these ENCODE with the OS codec, so")
        print("  non-ASCII text is written as mojibake (or raises mid-write):")
        for rel, rows in sorted(writes_by_file.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            print("    %5d  %s" % (len(rows), rel))
            for row in rows:
                print("          line %-6s %s" % (row["line"], row["call"]))

    if skipped:
        print()
        print("  SKIPPED -- could not be parsed, so nothing was checked in them:")
        for line in skipped:
            print("    %s" % line)

    if args.check:
        failed = bool(total) or (args.write_side and write_total)
        if failed:
            print()
            if total:
                print("FAIL: %d site(s) in %d file(s) decode without an explicit "
                      "encoding. Add encoding=\"utf-8\", errors=\"replace\"."
                      % (total, len(by_file)))
            if args.write_side and write_total:
                print("FAIL: %d text-write site(s) in %d file(s) omit encoding=."
                      % (write_total, len(writes_by_file)))
            return 1
        print()
        print("OK: every text-decoding subprocess call names its encoding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
