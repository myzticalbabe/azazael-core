# coding: utf-8
"""Lint shell command text for heredocs that lose their payload.

Why this exists (evidence, not theory). On 2026-10-15 a 12-edit Python patch was
piped as `python - <<'PY'` with a ~170-line payload. Bash reported
`here-document at line 1 delimited by end-of-file (wanted 'PY')` and Python raised
`SyntaxError: unterminated triple-quoted string literal` -- the script never ran,
so NONE of the twelve edits applied. The working method was to write the patch as
a file with an editor and run the file.

THE PREDICTOR IS LENGTH, AND THAT IS MEASURED. Two commands from the same session,
both with triple-quoted strings in the payload:

  * ~170 lines -> mangled, payload never reached the command;
  * ~20 lines  -> applied correctly on the first try.

So triple quotes are NOT what breaks a heredoc. What they change is how the
failure LOOKS: with them the mangled text becomes a SyntaxError (loud, and only
noticed because the tool output was read), whereas without them a truncated
payload can look like a silently-successful no-op. This lint therefore keys on
length -- the observed predictor -- and reports the triple-quote combination as
context on a finding rather than as a rule of its own. Keying on triple quotes
would have flagged a command that demonstrably worked, and a lint that fires on a
working command is a lint that gets switched off.

WHAT IS FLAGGED (two shapes):

  * ``heredoc_too_long`` -- the payload exceeds ``MAX_PAYLOAD_LINES``. Above the
    ~20 lines that worked, below the ~170 that did not.
  * ``heredoc_unterminated`` -- the terminator line is missing. Bash reports this
    loudly, but catching it before the command is issued is the difference
    between a caught mistake and a lost edit.

WHAT IS NOT FLAGGED: a short heredoc, with or without triple quotes. That is the
normal safe form; this lint must not train anyone to stop using it.

CLI:
    python tools/heredoc_pipe_lint.py FILE...      # lint each file as command text
    python tools/heredoc_pipe_lint.py -            # lint stdin
    python tools/heredoc_pipe_lint.py --json FILE  # machine-readable findings

Exit 0 when clean, 1 when any finding, 2 on a usage error. Stdlib only, and it
reads nothing but the text it is given.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Payloads above this many lines are flagged. Sits above the ~20-line heredoc
#: that worked and below the ~170-line one that was lost.
MAX_PAYLOAD_LINES = 40

#: `<<'TAG'`, `<<"TAG"`, `<<\TAG` or bare `<<TAG`. The quoting decides whether the
#: shell expands the body, which does not change whether the payload survives.
_HEREDOC_START = re.compile(
    r"<<-?\s*(?P<quote>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)")

_TRIPLE_QUOTE = re.compile(r"(\"\"\"|''')")


def scan_command(text: str, *, source: str = "<text>") -> List[Dict[str, Any]]:
    """Find every unsafe heredoc in `text`. Pure: no I/O, no global state."""
    findings: List[Dict[str, Any]] = []
    lines = text.splitlines()

    for index, line in enumerate(lines):
        match = _HEREDOC_START.search(line)
        if match is None:
            continue
        tag = match.group("tag")

        # The terminator is a line whose ONLY content is the tag (leading
        # whitespace allowed, for `<<-`). Matching the tag as a substring would
        # stop at its first use inside the payload instead of at the end.
        terminator = None
        for probe in range(index + 1, len(lines)):
            if lines[probe].strip() == tag:
                terminator = probe
                break

        if terminator is None:
            findings.append({
                "source": source, "line": index + 1,
                "kind": "heredoc_unterminated", "tag": tag,
                "detail": ("heredoc <<%s opened at line %d is never terminated; the "
                           "shell will report 'delimited by end-of-file' and the "
                           "payload will not reach the command"
                           % (tag, index + 1)),
            })
            continue

        payload = lines[index + 1:terminator]
        if len(payload) <= MAX_PAYLOAD_LINES:
            continue

        # Context, not the rule: a long payload that ALSO carries triple quotes
        # is the combination that failed here, and it is worth saying why the
        # symptom was a SyntaxError rather than a quiet truncation.
        has_triple = any(_TRIPLE_QUOTE.search(p) for p in payload)
        detail = ("heredoc <<%s payload is %d lines (limit %d); a payload this "
                  "large must be reproduced byte for byte through the shell -- "
                  "write it to a file and run the file instead"
                  % (tag, len(payload), MAX_PAYLOAD_LINES))
        if has_triple:
            detail += (". It also contains a triple-quoted string, so a mangled "
                       "payload surfaces as a SyntaxError rather than as a silent "
                       "no-op -- do not read 'no error' as 'the edits applied'")
        findings.append({
            "source": source, "line": index + 1, "kind": "heredoc_too_long",
            "tag": tag, "payload_lines": len(payload),
            "contains_triple_quotes": has_triple, "detail": detail,
        })

    return findings


def lint_file(path: Path) -> List[Dict[str, Any]]:
    """Lint one file of command text, accepting either line ending."""
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return scan_command(text, source=str(path))


def _usage() -> int:
    sys.stderr.write(
        "usage: heredoc_pipe_lint.py [--json] FILE... | - (stdin)\n"
        "Lints shell command text for heredocs that lose their payload.\n")
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if not args:
        return _usage()

    findings: List[Dict[str, Any]] = []
    for arg in args:
        if arg == "-":
            findings.extend(scan_command(sys.stdin.read(), source="<stdin>"))
            continue
        path = Path(arg)
        if not path.is_file():
            sys.stderr.write("heredoc-pipe-lint: not a file: %s\n" % arg)
            return 2
        findings.extend(lint_file(path))

    if as_json:
        print(json.dumps({"findings": findings, "count": len(findings)},
                         indent=2, sort_keys=True))
    else:
        for f in findings:
            print("%s:%d: %s: %s" % (f["source"], f["line"], f["kind"], f["detail"]))
        if findings:
            print("heredoc-pipe-lint: %d finding(s) -- the payload may not survive "
                  "the shell. Write the payload to a file and run the file."
                  % len(findings))
        else:
            print("heredoc-pipe-lint: clean (no unsafe heredoc in %d input(s))"
                  % len(args))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
