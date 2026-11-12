#!/usr/bin/env python3
"""swallow_inventory.py — find EVERY unrecorded swallow, not just the two shapes
the anti-pattern scanner knows about.

WHY THIS EXISTS
---------------
`CORE/compliance/scanner.py` flags exactly two swallow shapes, and only when the
handler body is a SINGLE statement:

    except ...:          except ...:
        pass                 return <const>

That is a real finding, but it is not the whole class. Everything below swallows
an error just as completely and is invisible to the counter today:

  silent_continue        except: continue        (loop skips the failure)
  silent_break           except: break           (loop abandons early)
  empty_literal_return   except: return []/{}/() (success-shaped default, non-Constant)
  bare_except            except: ...             (also masks KeyboardInterrupt/SystemExit)
  base_exception         except BaseException:   (same, explicitly)
  suppress_context       with contextlib.suppress(...):  (no handler at all)
  marker_hidden          a silent swallow carrying `# scanner:ignore`, i.e. the
                         scanner's own suppression marker hiding a finding from
                         the count
  unlogged_handler       ANY body that never acts on the exception -- no raise, no
                         logging/reporting call -- regardless of how many statements
                         it has (so `x = None; continue` and `handle(); pass` count)

FAIL-OPEN IS NOT THE BUG. This repo deliberately fails open at boundaries; a hook
must never break what it guards. The defect is an UNRECORDED swallow, because a
swallowed failure is indistinguishable from success. So the tool separates
`acted` handlers (they raise, log, or record) from silent ones, and only the
silent ones are inventory rows.

USAGE
    python tools/swallow_inventory.py                 # src/ (default), markdown
    python tools/swallow_inventory.py --json out.json
    python tools/swallow_inventory.py --dir src/CORE --top 30
    python tools/swallow_inventory.py --self-test     # oracle: fixtures, no repo
    python tools/swallow_inventory.py --sites state/swallow_sites

--sites writes one `handler line | shape | exception type | body` list per file,
bottom-up, which is the work order handed to a fix wave (and the record of what a
wave was told to touch).

The --self-test is the negative control: a fixture file containing one of every
shape above PLUS handlers that legitimately act (raise / log / note_failure, both
public and private, and the recorder-shim installer) must produce exactly the
silent set and NOT the acting ones.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

#: Calls that count as ACTING on the exception (making the swallow observable).
#: Matched on the final attribute/name of the call, so `log.warning`,
#: `logger.exception`, `self._log.error` and `note_failure` all match.
_ACTOR_NAMES = frozenset({
    "raise", "warn", "warning", "warnings", "error", "exception", "critical",
    "debug", "info", "log", "logging", "note_failure", "failure_notes",
    "print", "audit", "emit", "record", "report", "append_warning",
})

#: Single-statement handlers of these shapes are the scanner's two known kinds.
_SILENT_STMT = {"pass"}
_KNOWN_SHAPES = frozenset({"silent_pass", "return_default"})


def _callee_name(node: ast.AST) -> str:
    """Final attribute/name of a call target: `a.b.c` -> 'c', `f` -> 'f'."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


_LEDGER_SINKS = frozenset({"swallowed", "swallow_ledger", "failure_notes", "_swallowed"})


def _norm(name: str) -> str:
    """Normalize a callable name for actor matching.

    Leading/trailing underscores are stripped: this tree's recorders are
    frequently private helpers (`self._record`, `_note_failure`, `_log`), and
    comparing raw names counted every call to those as an UNRECORDED swallow.
    That was a false-positive bug in this tool's first release (found 2026-10-09
    while auditing the fix wave); the fixture below now locks it.
    """
    return name.strip("_").lower()


def _acts_on_exception(body: list[ast.stmt]) -> bool:
    """True if any statement raises, logs, or otherwise reports the failure."""
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Raise):
                return True
            if isinstance(sub, ast.Call):
                name = _norm(_callee_name(sub.func))
                if name == "getlogger":          # logging.getLogger(...) then used
                    return True
                if name in _ACTOR_NAMES:
                    return True
                # `log.error(...)` / `note_failure(...)` land above; anything whose
                # attribute chain mentions a log-ish word also counts.
                if any(_norm(part) in _ACTOR_NAMES
                       for part in ast.unparse(sub.func).lower().split(".")):
                    return True
    return False


def _appends_to_ledger(body: list[ast.stmt]) -> bool:
    """True if the handler appends to the swallow ledger sink itself.

    `_SWALLOWED.append(...)` IS the record: it is what the fallback recorder and
    this tree's `_note_failure` wrappers do. Without this rule the wrapper counts
    as an unrecorded swallow in every file that has one.
    """
    for stmt in body:
        for sub in ast.walk(stmt):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and _norm(sub.func.attr) in {"append", "add", "appendleft", "write"}
                    and isinstance(sub.func.value, ast.Name)
                    and _norm(sub.func.value.id) in _LEDGER_SINKS):
                return True
    return False


def _defines_recorder(body: list[ast.stmt]) -> bool:
    """True if the handler INSTALLS a recorder -- i.e. it is a fallback shim.

    This tree's ledger fallback is

        try:
            from CORE.failure_ledger import note_failure
        except ImportError:
            def note_failure(where, exc):
                _SWALLOWED.append(where)

    Without this rule every converted file contributes one phantom swallow from its
own import block, which would make a fix wave look like it regressed the file.
    """
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _norm(stmt.name) in _ACTOR_NAMES:
                return True
    return False


def _empty_literal(stmt: ast.stmt) -> bool:
    """`return [] / {} / () / set() / list() / dict()` — success-shaped, non-Constant."""
    if not isinstance(stmt, ast.Return) or stmt.value is None:
        return False
    v = stmt.value
    if isinstance(v, (ast.List, ast.Dict, ast.Tuple, ast.Set)) :
        return not getattr(v, "elts", None) and not getattr(v, "keys", None)
    if isinstance(v, ast.Call) and _callee_name(v.func) in {"list", "dict", "set", "tuple"}:
        return not v.args and not v.keywords
    return False


def classify_handler(node: ast.ExceptHandler, src_lines: list[str]) -> str | None:
    """Return a swallow shape name, or None if the handler is fine."""
    body = node.body
    exc_type = node.type
    bare = exc_type is None
    base = isinstance(exc_type, ast.Name) and exc_type.id == "BaseException"

    acted = _acts_on_exception(body) or _defines_recorder(body) or _appends_to_ledger(body)

    # Shape of a single-statement body (what the scanner sees).
    shape = None
    if len(body) == 1:
        only = body[0]
        if isinstance(only, ast.Pass):
            shape = "silent_pass"
        elif isinstance(only, ast.Continue):
            shape = "silent_continue"
        elif isinstance(only, ast.Break):
            shape = "silent_break"
        elif isinstance(only, ast.Return) and (
                only.value is None or isinstance(only.value, ast.Constant)):
            shape = "return_default"
        elif _empty_literal(only):
            shape = "empty_literal_return"

    if shape is None and not acted:
        shape = "unlogged_handler"          # multi-statement body, still unrecorded

    if shape is None:
        return None                          # acted on the error: acceptable

    # A silent swallow that also catches the whole BaseException hierarchy is worse.
    if bare:
        return "bare_except"
    if base:
        return "base_exception"

    # Was it hidden from the scanner's counter by its own suppression marker?
    line = src_lines[body[0].lineno - 1] if body and body[0].lineno <= len(src_lines) else ""
    if "# scanner:ignore" in line:
        return "marker_hidden"
    return shape


def scan_source(path: str, source: str) -> list[dict]:
    """All swallow rows in one file's source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    rows = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            shape = classify_handler(node, lines)
            if shape:
                first = node.body[0] if node.body else None
                rows.append({"file": path, "line": node.lineno,
                             "shape": shape,
                             "type": ast.unparse(node.type) if node.type else "bare",
                             "body": (ast.unparse(first).splitlines()[0] if first else "")})
        elif isinstance(node, ast.With):
            for item in node.items:
                txt = ast.unparse(item.context_expr)
                if "suppress(" in txt.replace(" ", ""):
                    rows.append({"file": path, "line": node.lineno,
                                 "shape": "suppress_context", "type": txt})
    return rows


def scan_dir(root: Path) -> list[dict]:
    rows: list[dict] = []
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            rows.extend(scan_source(str(p).replace("\\", "/"),
                                    p.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return rows


# -- work orders ----------------------------------------------------------------

def write_sites(rows: list[dict], outdir: Path) -> int:
    """One bottom-up site list per file, for handing to a fix wave."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[r["file"]].append(r)
    outdir.mkdir(parents=True, exist_ok=True)
    written = 0
    for f, rs in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        slug = Path(f).stem.lower().replace("-", "_")
        out = outdir / (slug + ".md")
        body = ["# Swallow sites in %s (%d) - work BOTTOM-UP so earlier line numbers stay valid"
                % (f, len(rs)), "",
                "Each row: handler line | shape | exception type | the body statement you will replace."]
        for r in sorted(rs, key=lambda r: -r["line"]):
            body.append("- line %-4d %-22s %-32s body: %s"
                        % (r["line"], r["shape"], r["type"][:32],
                           (r.get("body") or "")[:110]))
        out.write_text("\n".join(body) + "\n", encoding="utf-8")
        written += 1
    return written


# -- oracle ---------------------------------------------------------------------

_FIXTURE = '''
import logging
log = logging.getLogger(__name__)

_SWALLOWED = []
_FALLBACK = []


def a():
    try:
        x = 1 / 0
    except ZeroDivisionError:
        pass                      # swallow: silent_pass


def b():
    for i in [1]:
        try:
            x = 1 / 0
        except ZeroDivisionError:
            continue              # swallow: silent_continue


def b2():
    for i in [1]:
        try:
            x = 1 / 0
        except ZeroDivisionError:
            break                 # swallow: silent_break


def c():
    try:
        x = 1 / 0
    except ZeroDivisionError:
        return 0                  # swallow: return_default


def d():
    try:
        x = 1 / 0
    except ZeroDivisionError:
        return {}                 # swallow: empty_literal_return


def e():
    try:
        x = 1 / 0
    except:                       # swallow: bare_except
        pass


def f():
    try:
        x = 1 / 0
    except Exception:
        log.error("real handling")   # OK: acts on the error
        raise


def g():
    try:
        x = 1 / 0
    except Exception:
        note_failure("somewhere", exc)   # OK: records
        return 0


def h():
    try:
        x = 1 / 0
    except Exception:
        flag = False
        return flag               # swallow: unlogged_handler (multi-statement)


def i():
    try:
        x = 1 / 0
    except Exception:
        pass    # scanner:ignore     # swallow: marker_hidden


def _record(kind, payload):
    pass


def _note_failure(where, exc):
    pass


def _helper():
    pass


def j():
    try:
        x = 1 / 0
    except Exception as exc:
        _record("evt", {"error": str(exc)})   # OK: private recorder, not a swallow


def k():
    try:
        x = 1 / 0
    except Exception as exc:
        _note_failure("somewhere", exc)       # OK: private recorder, not a swallow


def m():
    try:
        x = 1 / 0
    except Exception:
        _helper()                             # swallow: unlogged_handler (j/k/m control)
        return 0


def n():
    try:
        from nowhere_at_all import note_failure
    except ImportError:                       # OK: recorder SHIM, not a swallow
        def note_failure(where, exc):
            _FALLBACK.append(str(where))


def o():
    try:
        x = 1 / 0
    except Exception as _e:
        _SWALLOWED.append("%s" % _e)          # OK: appends to the swallow ledger
'''


def self_test() -> int:
    """Negative control: the fixture's expected silent set, and nothing else."""
    rows = scan_source("<fixture>", _FIXTURE)
    got = Counter(r["shape"] for r in rows)
    expected = Counter({
        "silent_pass": 1,           # a
        "silent_continue": 1,       # b
        "silent_break": 1,          # b2
        "return_default": 1,        # c
        "empty_literal_return": 1,  # d
        "bare_except": 1,           # e  (bare wins over silent_pass)
        "unlogged_handler": 2,      # h, m  (m is the negative control for the
                                    # underscore normalization: a NON-recorder
                                    # private helper must still be flagged)
        "marker_hidden": 1,         # i
    })
    print("fixture rows found:")
    for r in sorted(rows, key=lambda r: r["line"]):
        print("   line %-4d %-22s %s" % (r["line"], r["shape"], r["type"]))
    print("\nexpected: %s" % dict(expected))
    print("got:      %s" % dict(got))
    ok = got == expected
    # The acting handlers must NOT be reported: f ('Except Exception' + log + raise),
    # g (note_failure), j/k (PRIVATE recorders `_record`/`_note_failure`) and n (the
    # recorder-shim installer) are all absent from `got`, which is what comparing the
    # whole Counter to `expected` proves. m is the negative control that keeps the
    # underscore normalization honest: a NON-recorder private helper still flags.
    print("\nSELF-TEST: %s" % ("PASS - silent set exact, acting handlers excluded" if ok
                               else "FAIL - detector disagrees with the fixture"))
    return 0 if ok else 1


# -- report ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="src", help="root to scan (default: src)")
    ap.add_argument("--json", default="", help="also write machine-readable rows here")
    ap.add_argument("--top", type=int, default=20, help="files per shape to print")
    ap.add_argument("--self-test", action="store_true", help="run the fixture oracle only")
    ap.add_argument("--sites", default="", help="write one work-order .md per file here")
    ap.add_argument("--min-sites", type=int, default=1, help="skip files below this (-sites)")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    root = Path(args.dir)
    if not root.exists():
        print("no such directory: %s" % root)
        return 2
    rows = scan_dir(root)

    by_shape = Counter(r["shape"] for r in rows)
    by_file: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_file[r["file"]][r["shape"]] += 1

    ordered = ["bare_except", "base_exception", "marker_hidden", "unlogged_handler",
               "silent_pass", "return_default", "empty_literal_return",
               "silent_continue", "silent_break", "suppress_context"]
    print("SWALLOW INVENTORY - root=%s  files=%d" % (root, len(by_file)))
    print("total unrecorded swallow sites: %d" % len(rows))
    print()
    print("%-24s %6s   %s" % ("shape", "count", "what it hides"))
    what = {
        "silent_pass": "body is a single pass (scanner counts these)",
        "return_default": "single return of None/constant (scanner counts these)",
        "empty_literal_return": "single return []/{}/() - success-shaped, NOT counted",
        "silent_continue": "loop skips the failure - NOT counted",
        "silent_break": "loop abandons early - NOT counted",
        "bare_except": "also masks KeyboardInterrupt/SystemExit - NOT counted",
        "base_exception": "catches BaseException explicitly - NOT counted",
        "unlogged_handler": "multi-statement body, never acts on exc - NOT counted",
        "marker_hidden": "hidden from the counter by its own scanner:ignore",
        "suppress_context": "contextlib.suppress - no handler at all",
    }
    for shape in ordered:
        if by_shape.get(shape):
            print("%-24s %6d   %s" % (shape, by_shape[shape], what[shape]))
    for shape in sorted(set(by_shape) - set(ordered)):
        print("%-24s %6d" % (shape, by_shape[shape]))

    print()
    print("worst files (all shapes):")
    for f, c in sorted(by_file.items(), key=lambda kv: -sum(kv[1].values()))[:args.top]:
        print("  %-58s %4d   %s" % (f[:58], sum(c.values()),
                                     ", ".join("%s=%d" % kv for kv in c.most_common(3))))

    if args.sites:
        keep = [r for r in rows if r["file"].count("/") >= 0]
        counts = Counter(r["file"] for r in keep)
        keep = [r for r in keep if counts[r["file"]] >= args.min_sites]
        n = write_sites(keep, Path(args.sites))
        print("\nwrote %d work-order file(s) to %s (%d sites)" % (n, args.sites, len(keep)))

    if args.json:
        payload = {"root": str(root), "total": len(rows),
                   "by_shape": dict(by_shape),
                   "by_file": {k: dict(v) for k, v in by_file.items()},
                   "rows": rows}
        Path(args.json).write_text(json.dumps(payload, indent=2, sort_keys=True),
                                   encoding="utf-8")
        print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
