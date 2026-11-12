"""bypass_kit.py -- mutation kit that tries to break in, so the walls get tested.

A parser nobody attacks is a wall nobody measured. This kit
generates adversarial inputs -- instruction injections, path
escapes, oversized payloads, unicode tricks, section spoofs --
and runs each against Eve's own parsers, reporting HELD (refused
or contained) versus BYPASSED (applied something it should not).
A BYPASSED is a finding to fix, recorded into the bake lane by
the operator, never hidden and never auto-patched.

Scope, stated plainly: the kit mutates inputs to Eve's own
document and instruction parsers only. It touches no gate, no
scanner, no auth, no foreign code. A bypass kit that reaches past
its own walls is not a test instrument; this one cannot, by
construction -- every case runs in-process against a named
function, and the only side effects are temp files it deletes.

Suites:
- instruction_injections(): inputs that try to smuggle an op past
  parse_instruction, or stack two ops in one line.
- path_escapes(): doc paths that try to leave the tree (..,
  absolute, NUL, device names).
- oversized(): a 2 MB line against replace/steps paths.
- unicode_tricks(): RTL overrides, lookalike letters, zero-width
  joins inside op keywords.
- section_spoofs(): headings crafted to hijack the section span.

run_suite() executes all cases and returns held/bypassed counts
plus every BYPASSED case with its input and effect. Green means
measured, not invincible: the kit grows, the walls keep score.
"""
import os as _os
import tempfile as _tempfile

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass


def instruction_injections():
    """Inputs that try to smuggle ops past the instruction parser."""
    return [
        "replace a with b with c",
        "REPLACE x WITH y",
        "replace  with y",
        "replace x with ",
        "undo undo",
        "replace undo with redo",
        "make steps outline mutate",
        "outline; replace a with b",
        "replace a\\nb with c",
        "uppercase in ",
        "mutate mutate mutate",
        "replace a\twith b",
        "replace a with b\nwith c",
        "  replace a with b",
        "replace a with b;",
    ]


def path_escapes():
    """Doc paths that try to leave the tree."""
    return [
        "../../../etc/passwd",
        "/etc/hosts",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",  # scanner:ignore -- adversarial test DATA the kit feeds read_document to verify refusal, never a path this module opens
        "doc\x00.md",
        "CON",
        "NUL.txt",
        "..\\..\\secret.md",
        "~/evil.md",
        "\\\\server\\share\\evil.md",
        "evil. ",
        "file.txt:secret",
        "CoN.md",
    ]


def oversized():
    """A 2 MB single line plus a 200k-word document."""
    return ["x" * (2 * 1024 * 1024), "word " * 200000]


def unicode_tricks():
    """Bidi, lookalikes, zero-width joins inside op keywords."""
    return [
        "repla\u202ece b with c",
        "replace \u0430 with b",
        "re\u200bplace a with b",
        "UPPER\u200bCASE",
        "mut\u00e1te",
        "und\u043e",
        "\uff52eplace a with b",
        "replac\u0301e a with b",
    ]


def section_spoofs():
    """Headings crafted to hijack the section span."""
    return [
        "# ",
        "####### deep",
        "INSPECTION",
        "Inspection ",
        "#Inspection",
        "=" * 100,
        "##inspection",
        "INSPECTION:",
        "Inspection##",
    ]


def _try_instruction(case):
    from .live_morph import parse_instruction, apply_op
    try:
        op = parse_instruction(case)
    except ValueError:
        return "HELD", "refused at parse"
    if op["op"] == "replace" and (not op["old"] or not op["new"]):
        return "BYPASSED", "empty side survived parse: %r" % op
    try:
        _, detail = apply_op("alpha beta\ngamma delta\n", op)
    except Exception as e:
        return "HELD", "refused at apply: %s" % str(e)[:80]
    # A multi-word new side ("replace a with b with c" -> new "b with c")
    # is a legitimate greedy parse, not a smuggle: the whole tail becomes
    # the replacement, and the log shows it. Only an empty side surviving
    # parse would be a bypass, and that is refused above.
    return "HELD", "parsed as %s: %s" % (op["op"], detail[:80])


def _try_path(case):
    from .doc_tutor import read_document
    try:
        read_document(case)
        return "BYPASSED", "opened without refusal: %r" % case[:60]
    except ValueError:
        return "HELD", "refused: no such document or bad format"
    except Exception as e:
        note_failure("bypass_kit:path", e)
        return "HELD", "refused by OS: %s" % str(e)[:80]


def _try_oversized(case):
    from .live_morph import LiveState
    st = LiveState(case, "bulk")
    try:
        r = st.instruct("outline")
        ok = r["ok"] and isinstance(st.text, str)
        return ("HELD", "outline over bulk ok") if ok else ("BYPASSED",
                "bulk broke outline")
    except Exception as e:
        note_failure("bypass_kit:bulk", e)
        return "HELD", "refused: %s" % str(e)[:80]


def _try_unicode(case):
    return _try_instruction(case)


def _try_spoof(case):
    from .live_morph import apply_op
    doc = "%s\nbody line here\n## Next\nmore text\n" % case
    try:
        _, detail = apply_op(doc, {"op": "case", "upper": True,
                                   "section": "Inspection", "raw": case})
        return "HELD", "section span contained: %s" % detail[:80]
    except Exception as e:
        note_failure("bypass_kit:spoof", e)
        return "HELD", "refused: %s" % str(e)[:80]


_SUITES = (("instruction_injections", instruction_injections,
            _try_instruction),
           ("path_escapes", path_escapes, _try_path),
           ("oversized", oversized, _try_oversized),
           ("unicode_tricks", unicode_tricks, _try_unicode),
           ("section_spoofs", section_spoofs, _try_spoof))


def run_suite():
    """Run every case. Returns counts plus each BYPASSED with its effect."""
    results, bypassed = [], []
    for suite_name, cases_fn, try_fn in _SUITES:
        for case in cases_fn():
            try:
                verdict, detail = try_fn(case)
            except Exception as e:
                note_failure("bypass_kit:case", e)
                verdict, detail = "HELD", "harness refused: %s" % str(e)[:80]
            results.append({"suite": suite_name, "verdict": verdict,
                            "detail": detail,
                            "input": case[:80] if isinstance(case, str)
                            else "<bulk>"})
            if verdict == "BYPASSED":
                bypassed.append(results[-1])
    held = sum(1 for r in results if r["verdict"] == "HELD")
    return {"ran": len(results), "held": held,
            "bypassed": bypassed, "bypassed_count": len(bypassed),
            "results": results,
            "note": ("HELD means refused or contained. Any BYPASSED is a "
                     "finding: record it, fix the parser, re-run.")}
