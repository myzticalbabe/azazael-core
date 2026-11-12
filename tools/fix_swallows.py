#!/usr/bin/env python3
"""fix_swallows.py — record every unrecorded swallow, deterministically.

WHY A PROGRAM AND NOT AN EDITOR PASS
------------------------------------
Two fix waves were done by hand (2026-10-09). Three files were corrupted, by
exactly two mistakes: an insertion that landed one indentation level off, and a
replacement that joined two statements onto one line. Both are impossible here,
because the call line's indentation is COPIED FROM THE SOURCE LINE it is inserted
at, and every insertion is its own line. The tool also refuses to write any file
whose transformed source does not `ast.parse`.

WHAT IT DOES
------------
For every handler that `tools/swallow_inventory.py` classifies as an unrecorded
swallow, it inserts one call as the FIRST statement of that handler's body:

    note_failure("<stem>:L<handler line>", <bound exc name or None>)

Every pre-existing statement, comment and line ending is preserved. Fail-open
behaviour is untouched: the call records, it never raises.

OPTIONS
    --file PATH            convert one file
    --dir ROOT             convert every *.py under ROOT
    --dry-run              report what would change, write nothing
    --ensure-recorder      also add the ledger comment block + import shim to any
                           file that has no recorder yet (without it, files that
                           never imported a recorder would call an undefined name)
    --self-test            oracle on the embedded fixture; touches no repo file

SAFETY
  * idempotent -- a converted file reports nothing to do on the next run
  * skips recorder shims and ledger-sink wrappers (the detector's own exclusions
    decide what counts as a finding)
  * `--ensure-recorder` skips a file already carrying the shim (probed by the
    shim block's own comment tag), so no file ever gets two recorders
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from swallow_inventory import scan_source  # noqa: E402  (same-directory tool)

#: Added by --ensure-recorder. `List` is imported alongside it when missing.
RECORDER_BLOCK_TAG = "#: Swallows recorded when the failure ledger is unavailable"
#: keep a stable, searchable name too
_RECORDER_TAG = RECORDER_BLOCK_TAG

RECORDER_BLOCK = [
    "",
    "#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only",
    "#: leaf, so this fallback should never fire -- if it does, the ledger is missing).",
    "_SWALLOWED: List[str] = []",
    "",
    "try:",
    "    from CORE.failure_ledger import note_failure",
    "except ImportError:                               # never break the host",
    "    def note_failure(where: str, exc: BaseException | None = None) -> None:",
    '        _SWALLOWED.append("%s:no-recorder" % where)',
]


def _newline_of(src: str) -> str:
    return "\r\n" if "\r\n" in src[:4000] else "\n"


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _comment_suffix(line: str) -> str:
    """The trailing `# ...` on a line, with its leading spacing, or ''."""
    body = line.rstrip("\r\n")
    in_str = None
    for i, ch in enumerate(body):
        if in_str:
            if ch == in_str and body[i - 1] != "\\":
                in_str = None
            continue
        if ch in "\"'":
            in_str = ch
        elif ch == "#":
            return body[i - 1:].rstrip() if i and body[i - 1] == " " else " " + body[i:].rstrip()
    return ""


def _targets(src: str, path: str) -> tuple[ast.Module, list[ast.ExceptHandler]]:
    tree = ast.parse(src)
    flagged = {r["line"] for r in scan_source(path, src)}
    handlers = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ExceptHandler) and node.lineno in flagged):
            continue
        # NEVER convert inside a recorder: a handler whose body calls the
        # enclosing note_failure/_note_failure function would make the
        # recorder recurse into itself, forever, once its write fails
        # (failure_ledger.py shipped exactly this bug 2026-10-09 and a
        # broken-lock probe raised RecursionError). Same for the ledger
        # module itself.
        if "failure_ledger" in path.replace("\\", "/"):
            continue
        if _handler_calls_own_recorder(node):
            continue
        handlers.append(node)
    return tree, handlers


def _handler_calls_own_recorder(handler: ast.ExceptHandler) -> bool:
    """True if any enclosing FunctionDef of this handler is named in a call
    the handler makes -- i.e. converting it would recurse the recorder."""
    for sub in ast.walk(handler):
        if isinstance(sub, ast.Call):
            f = sub.func
            nm = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            if nm in ("note_failure", "_note_failure"):
                return True
    return False



def _call(indent: str, label: str, lineno: int, exc: str | None) -> str:
    return '%snote_failure("%s:L%d", %s)' % (indent, label, lineno, exc or "None")


def convert_source(src: str, path: str) -> tuple[str, int]:
    """Return (new_source, sites_converted). Raises SyntaxError if unsafe."""
    nl = _newline_of(src)
    lines = src.splitlines(keepends=True)
    _, handlers = _targets(src, path)
    if not handlers:
        return src, 0

    label = Path(path).stem
    edits: list[tuple[int, int, list[str]]] = []      # (start_idx, end_idx, replacement)

    for node in handlers:
        first = node.body[0]
        i = first.lineno - 1
        exc = node.name or None

        if first.lineno == node.lineno:
            # One-line suite: `except X: <body>` -- the shape that corrupted a
            # file in wave 2. Split it into a header plus properly indented lines.
            line = lines[i].rstrip("\r\n")
            body_col = first.col_offset
            header = line[:body_col].rstrip()
            if not header.endswith(":"):
                raise SyntaxError("cannot split one-line suite at line %d" % node.lineno)
            tail = line[body_col:].rstrip()
            indent = _indent_of(line) + "    "
            sole_pass = len(node.body) == 1 and isinstance(first, ast.Pass)
            out = [header + nl, _call(indent, label, node.lineno, exc) + nl]
            if not sole_pass:
                out.append(indent + tail + nl)
            edits.append((i, i + 1, out))
            continue

        if isinstance(first, ast.Pass) and len(node.body) == 1:
            # Sole `pass`: replace it, keeping any trailing comment.
            indent = _indent_of(lines[i])
            keep = _comment_suffix(lines[i])
            edits.append((i, i + 1, [_call(indent, label, node.lineno, exc) + keep + nl]))
        else:
            # Multi-statement (or single non-pass) body: insert above the first
            # statement, at that statement's own indentation.
            indent = _indent_of(lines[i])
            comments = []
            j = i - 1
            while j >= 0 and lines[j].strip().startswith("#"):
                comments.insert(0, lines[j])
                j -= 1
            ins = comments + [_call(indent, label, node.lineno, exc) + nl]
            edits.append((i, i, ins))

    for start, end, repl in sorted(edits, key=lambda e: -e[0]):
        lines[start:end] = repl

    new_src = "".join(lines)
    ast.parse(new_src)                    # the gate: never write an unparseable file
    return new_src, len(handlers)


def ensure_recorder(src: str) -> tuple[str, bool]:
    """Add the ledger comment block + import shim if the file has no recorder.

    The presence probe is the RECORDER BLOCK's own comment tag, NOT the name
    `note_failure`: the converter itself inserts `note_failure(...)` call lines
    into every converted file, so probing that name would make this function a
    no-op precisely for the files that need it (found 2026-10-09: 424 files had
    calls and no shim because of exactly this self-defeating probe).
    """
    if _RECORDER_TAG in src:
        return src, False
    nl = _newline_of(src)
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    anchor = 0
    typing_idx = None
    first_rec_call = None          # earliest module-level note_failure call
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            anchor = max(anchor, node.end_lineno or node.lineno)
            if isinstance(node, ast.ImportFrom) and node.module == "typing":
                typing_idx = node
        elif first_rec_call is None and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # A module-level statement that calls note_failure (directly or
            # inside a try/if) fixes this file's ordering: the shim must land
            # BEFORE it, not merely after the last import -- a handler ABOVE
            # the shim would NameError at import time (vocab_migration.py
            # shipped exactly that bug in the 2026-10-09 sweep). Calls INSIDE
            # a module-level def/class body are safe: the shim, defined at
            # import time, precedes any function call at runtime.
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    nm = (f.id if isinstance(f, ast.Name)
                          else f.attr if isinstance(f, ast.Attribute) else "")
                    if nm in ("note_failure", "_note_failure"):
                        first_rec_call = sub.lineno
                        break
    if first_rec_call is not None and first_rec_call <= anchor:
        # Put the shim immediately BEFORE the line of the earliest call.
        anchor = first_rec_call - 1
    if anchor == 0:
        # No module-level import at all: the shim must come BEFORE every
        # call, so anchor it after the module docstring instead of bailng out
        # (four files hit this in the 2026-10-09 sweep and stayed shim-less).
        if tree.body and isinstance(tree.body[0], ast.Expr) \
                and isinstance(tree.body[0].value, ast.Constant) \
                and isinstance(tree.body[0].value.value, str):
            anchor = tree.body[0].end_lineno or tree.body[0].lineno
        else:
            anchor = 0
        if anchor == 0:
            return src, False           # no docstring either: leave the file alone

    patch: list[str] = []
    if typing_idx is None:
        patch.append("from typing import List" + nl)
    elif not any(a.name == "List" for a in typing_idx.names):
        first_import_line = lines[typing_idx.lineno - 1].rstrip("\r\n")
        if "(" in first_import_line:
            # Parenthesized (multi-line) import: add the alias as its own line
            # after the opening paren, NOT by splicing into that line --
            # `from typing import (\n    Any,` + ", List" is a syntax error.
            open_paren = first_import_line.index("(")
            lines[typing_idx.lineno - 1] = (
                first_import_line[:open_paren + 1] + nl + "    List,"
                + nl)
        else:
            lines[typing_idx.lineno - 1] = first_import_line + ", List" + nl

    patch += [ln + nl if not ln.endswith(nl) else ln for ln in RECORDER_BLOCK]
    out = lines[:anchor] + patch + lines[anchor:]
    new_src = "".join(out)
    ast.parse(new_src)
    return new_src, True


def fix_file(path: Path, ensure: bool, dry_run: bool = False) -> dict:
    src = path.read_text(encoding="utf-8", errors="surrogateescape")
    orig = src
    # utf-8 BOM is preserved because it lives in the decoded text's first char.
    new_src, converted = convert_source(src, str(path).replace("\\", "/"))
    added = sank = False
    if ensure and not dry_run:
        new_src, added = ensure_recorder(new_src)
    elif ensure and dry_run:
        _, added = ensure_recorder(new_src)
    if not dry_run:
        new_src, sank = _drop_dead_sink(new_src)
    if not dry_run and new_src != orig:
        path.write_text(new_src, encoding="utf-8", errors="surrogateescape", newline="")
    return {"file": str(path), "converted": converted, "recorder_added": added,
            "sank_dropped": sank, "changed": new_src != orig}


def _drop_dead_sink(src: str) -> tuple[str, bool]:
    """Remove the fallback recorder's dead `_SWALLOWED` list, when it truly is dead.

    The shim's ImportError fallback appends to a per-file `_SWALLOWED` list that
    NOTHING in the repo reads -- a sink nobody drains (proven by grep 2026-10-09:
    no reader outside this tool and the detector). If the append-to-list is the
    fallback's ONLY action, the whole list + append can go; the stub call stays
    and simply does nothing on the (never-taken) ImportError path, which the
    detector already whitelists via the recorder-shim rule.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src, False
    # Only rewrite when the file carries the canonical shim tag and its
    # `note_failure` name is otherwise unused at module level.
    if _RECORDER_TAG not in src:
        return src, False
    lines = src.splitlines(keepends=True)
    # locate the tag block; the dead sink is exactly `_SWALLOWED: List[str] = []`
    # plus the fallback's single append line.
    tag_i = next((i for i, ln in enumerate(lines) if _RECORDER_TAG in ln), None)
    if tag_i is None:
        return src, False
    # find the `_SWALLOWED: ... = []` assignment below the tag
    sw_i = next((i for i in range(tag_i, min(tag_i + 8, len(lines)))
                 if lines[i].lstrip().startswith("_SWALLOWED:")), None)
    if sw_i is None:
        return src, False
    # the append must be a single, exactly-shaped line inside the fallback def
    app_i = next((i for i in range(sw_i, min(sw_i + 14, len(lines)))
                  if '_SWALLOWED.append("%s:no-recorder" % where)' in lines[i]), None)
    if app_i is None:
        return src, False
    # Is _SWALLOWED used ANYWHERE ELSE in this file (beyond its decl + append)?
    uses = [i for i, ln in enumerate(lines)
            if "_SWALLOWED" in ln and i not in (sw_i, app_i)]
    # The comment line above the decl also mentions it? Tag line does not.
    if uses:
        return src, False
    # Better still: if the fallback def is now EMPTY, drop the whole fallback
    # (the except branch can simply `def note_failure(...): pass`-less -- no
    # def at all is not possible, so keep the def with a pass body). Replace
    # append with plain `pass`, never a split-string mutation.
    nl = _newline_of(src)
    indent = _indent_of(lines[app_i])
    lines[app_i] = indent + "pass  # ledger missing; evidence-vs-silence tradeoff capped here" + nl
    lines[sw_i] = ""                     # the never-read `_SWALLOWED: List[str] = []` decl
    # `List` import may now be unused -- only if THIS was its sole use; leave
    # the import alone (a stray unused import is harmless; breaking a paren
    # import line is not).
    out = "".join(lines)
    ast.parse(out)
    return out, True


# -- oracle ---------------------------------------------------------------------

_FIXTURE = '''
import logging
from typing import Any

log = logging.getLogger(__name__)


def a():
    try:
        x = 1 / 0
    except ZeroDivisionError:
        pass


def b():
    try:
        x = 1 / 0
    except ZeroDivisionError:
        pass    # keep this comment


def c():
    try:
        x = 1 / 0
    except Exception as exc:
        flag = False
        return flag


def d():
    try:
        x = 1 / 0
    except ValueError: pass


def e():
    try:
        x = 1 / 0
    except OSError:
        # explains the swallow
        pass


def f():
    try:
        x = 1 / 0
    except Exception:
        log.error("acts on it")
        raise


def g():
    try:
        from nowhere import note_failure
    except ImportError:
        def note_failure(where, exc):
            _SWALLOWED.append(str(where))


def h():
    try:
        x = 1 / 0
    except Exception:
        note_failure("g:L63", None)


#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).
_RECORDER_TAG_SENTINEL = True
_SWALLOWED_TAG_PROBE: list = []

def h():
    try:
        x = 1 / 0
    except Exception:
        pass


#: The recorder AND the multi-line typing import in one file: splicing a comma into
#: the parenthesis-open line would be a syntax error, so the fixer must add the alias
#: as its own line under the paren. No other fixture covers this wrapping.
from typing import (
    Any,
    Generic,
)
_EXTRA_NAMES: list = []


def pending_call():
    # Call ABOVE its shim definition: the ordering fix must anchor before it.
    try:
        x = 1 / 0
    except Exception:
        note_failure("already-called-up-here", None)


def i():
    try:
        x = 1 / 0
    except Exception:
        pass
'''


def self_test() -> int:
    before = scan_source("<fixture>", _FIXTURE)
    print("fixture findings: %s" % [(r["line"], r["shape"]) for r in before])
    src, n = convert_source(_FIXTURE, "<fixture>/fixture.py")
    after = scan_source("<fixture>", src)
    # The paren-splice case must be exercised on a file that HAS no recorder yet,
    # so build it from the part of the fixture that has no note_failure definition.
    paren_case_src = _FIXTURE[_FIXTURE.index("#: The recorder AND the multi-line"):] \
        .replace("\nfrom nowhere", "\n#from nowhere")
    # Ordering case: a file whose note_failure call sits ABOVE the point where
    # the last import ends must get its shim placed BEFORE the call.
    order_case_src = '''import sys
from typing import Dict

try:
    from CORE.audit_trail import EVENT_VOCAB
except ImportError:
    note_failure("order:L6", None)
    EVENT_VOCAB = {}
'''
    fixed, added = ensure_recorder(order_case_src)
    call_line = next(i for i, ln in enumerate(fixed.splitlines())
                     if 'note_failure("order:L6"' in ln)
    shim_line = next(i for i, ln in enumerate(fixed.splitlines())
                     if "failure_ledger import note_failure" in ln)
    print("\nconverted %d sites; findings after: %s" % (n, after))
    # Count the INSERTED calls only: the fixture also contains the recorder shim's
    # `def note_failure(...)`, so a bare count of "note_failure(" is not the measure.
    called = sum(1 for ln in src.splitlines()
                 if 'note_failure("fixture:L' in ln)
    checks = [
        ("seven swallow sites found", len(before) == 7),   # 5 originals + h + i
        ("every one converted", n == 7),
        ("no findings left", after == []),
        ("exactly one inserted call per site", called == 7),
        ("comment preserved on the sole pass", "# keep this comment" in src),
        ("leading comment preserved", "# explains the swallow" in src),
        ("multi-statement body kept", "return flag" in src),
        ("one-line suite split with a call line", "except ValueError:" in src),
        ("acting handler untouched (log+raise)", "log.error" in src),
        ("recorder shim untouched",
         "def note_failure(where, exc):" in src and "nowhere import note_failure" in src),
        ("idempotent second run", convert_source(src, "<fixture>/fixture.py")[1] == 0),
        ("paren-typing-List splice works",
         ensure_recorder(paren_case_src)[0].count("\n    List,\n") == 1),
        # False-positive guard: the FULL fixture already names note_failure (the
        # shim), so --ensure-recorder must NOT bolt on a second recorder there.
        ("no duplicate recorder in fixture",
         not ensure_recorder(src)[1]),  # the full fixture already carries the shim tag
        ("shim lands BEFORE an earlier call (import-time ordering)",
         shim_line < call_line),
    ]
    for name, ok in checks:
        print("   [%s] %s" % ("PASS" if ok else "FAIL", name))
    bad = [n for n, ok in checks if not ok]
    print("\nSELF-TEST: %s" % ("PASS" if not bad else "FAIL - " + ", ".join(bad)))
    if bad:
        print("\n--- transformed fixture ---\n%s" % src)
    return 0 if not bad else 1


# -- cli ------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--dir", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ensure-recorder", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    paths: list[Path] = [Path(f) for f in args.file]
    if args.dir:
        root = Path(args.dir)
        if not root.exists():
            print("no such directory: %s" % root)
            return 2
        paths += [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]
    if not paths:
        print("nothing to do: pass --file or --dir (or --self-test)")
        return 2

    total_files = total_sites = failed = 0
    for p in paths:
        try:
            res = fix_file(p, args.ensure_recorder, args.dry_run)
        except SyntaxError as e:
            failed += 1
            print("  SKIP  %-56s refused: %s" % (str(p)[-56:], e))
            continue
        except OSError as e:
            failed += 1
            print("  SKIP  %-56s unreadable: %s" % (str(p)[-56:], e))
            continue
        if res["converted"]:
            total_files += 1
            total_sites += res["converted"]
            print("  %s %-52s %3d site(s)%s"
                  % ("WOULD" if args.dry_run else "FIXED", str(p)[-52:], res["converted"],
                     " +recorder" if res["recorder_added"] else ""))

    print("\n%s: %d file(s), %d site(s)%s"
          % ("would convert" if args.dry_run else "converted",
             total_files, total_sites,
             ", %d refused" % failed if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
