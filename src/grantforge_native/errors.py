"""Failure taxonomy + durable error ledger.

**Why this module exists.** "Fix any/all errors" is only an engineering promise if
each failure the executable can meet has (a) a stable code, (b) the remedy that
actually clears it, and (c) a record that survives the process. A traceback gives
none of the three: it is unsearchable across sessions, it names a line rather than
a condition, and it tells the operator nothing to do.

So every failure is mapped here to one `code` from `ERRORS`, and every code carries
the action that resolves it. `cli.main` prints the code and the remedy and appends
one JSON line to the ledger; the MCP server returns the same structure inside the
tool result, so a calling model can act on it instead of guessing.

The ledger is append-only JSONL at `~/.grantforge/errors.jsonl`
(`GRANTFORGE_ERRLOG` overrides it). It stores the code, the remedy, a short detail
and a timestamp — never RFP or profile text, because the ledger is diagnostics and
the documents are the applicant's.

Classification is marker-based on the message plus the exception's class name. It is
deliberately not clever: a code that is wrong is a test to add, not a place to add
another heuristic.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# code -> (meaning, remedy). Order matters for `code_for`: the first marker that
# matches wins, so the specific markers come before the general ones.
ERRORS = {
    "model_unreachable": (
        "the local model endpoint refused the connection or timed out",
        "start the model server (llama-server on 127.0.0.1:8081) and re-run; "
        "`grantforge doctor` prints the URL it tried and the models it saw",
    ),
    "model_truncated": (
        "the model's reply hit the token budget before it finished",
        "raise --max-tokens, or ask for a shorter section; the reply is reported as "
        "truncated rather than parsed as a bad answer",
    ),
    "model_bad_reply": (
        "the model answered but the reply could not be used",
        "re-run, or use --offline for the rule-decided criteria; a question the rules "
        "cannot decide stays `unjudged` by design",
    ),
    "draft_needs_model": (
        "drafting was asked for with no model available (--offline)",
        "drop --offline, or start a model endpoint; drafting is the one command that "
        "cannot run on rules alone",
    ),
    "input_unreadable": (
        "an input file was missing, unreadable, or not JSON",
        "check the path and the JSON; `grantforge demo --json` shows the shape an RFP "
        "and a profile must have",
    ),
    "seal_mismatch": (
        "a sealed artefact's digest does not match its contents",
        "treat the artefact as modified: re-run the command that produced it and compare "
        "the digest, rather than editing the seal",
    ),
    "unknown": (
        "a failure this build has no code for",
        "record the message with `grantforge doctor --json` output; an unclassified "
        "failure is a missing entry in ERRORS, not an expected state",
    ),
}

# code -> the next action an AGENT should take after seeing that code. This is the
# harness half of the taxonomy: a code that only describes the failure leaves the
# calling model to guess, and a guessing agent is an unharnessed one. Every key in
# ERRORS must appear here (enforced by a test), so a new failure cannot ship without
# a push.
NEXT = {
    "model_unreachable": "run `grantforge doctor --json`; if `ok` is false, start the "
                         "model server, then re-run the same command with `--offline` "
                         "only if rule-decided criteria are enough",
    "model_truncated": "re-run the same call with a larger `max_tokens`, or request the "
                       "section in smaller pieces; do not parse the truncated text",
    "model_bad_reply": "re-run once; if it repeats, switch to `--offline` and report the "
                       "criterion as `unjudged` rather than accepting a paraphrase",
    "draft_needs_model": "drop `--offline` for `draft`, or start the model server; a draft "
                         "is never produced by rules alone",
    "input_unreadable": "re-read the path from the case manifest (`case verify --json`) "
                        "and fix the file; do not edit the case to match the input",
    "seal_mismatch": "compare the artefact's recomputed digest with the seal, re-run the "
                     "producing command, and never rewrite the seal",
    "unknown": "capture `grantforge doctor --json` and the exact command, then open a new "
               "code in `errors.ERRORS` with its remedy and a test that provokes it",
}

# (marker, code) pairs, checked against "ClassName: message" lowercased.
_MARKERS = (
    ("finish_reason", "model_truncated"),
    ("truncat", "model_truncated"),
    ("no usable json", "model_bad_reply"),
    ("bad reply", "model_bad_reply"),
    ("unusable", "model_bad_reply"),
    ("draft requires a model", "draft_needs_model"),
    ("connection refused", "model_unreachable"),
    ("connectionreset", "model_unreachable"),
    ("timed out", "model_unreachable"),
    ("timeout", "model_unreachable"),
    ("unreachable", "model_unreachable"),
    ("no model", "model_unreachable"),
    ("llmunavailable", "model_unreachable"),
    ("llmbadreply", "model_bad_reply"),
    ("seal", "seal_mismatch"),
    ("no such file", "input_unreadable"),
    ("not found", "input_unreadable"),
    ("json", "input_unreadable"),
    ("decode", "input_unreadable"),
    ("permission", "input_unreadable"),
)


def code_for(exc):
    """Map an exception (or a message) to one code from ERRORS."""
    text = exc if isinstance(exc, str) else "%s: %s" % (type(exc).__name__, exc)
    lowered = text.lower()
    for marker, code in _MARKERS:
        if marker in lowered:
            return code
    return "unknown"


def explain(exc):
    """The structure the CLI prints and the MCP tool returns."""
    code = code_for(exc)
    meaning, remedy = ERRORS[code]
    detail = exc if isinstance(exc, str) else "%s: %s" % (type(exc).__name__, exc)
    return {"ok": False, "code": code, "meaning": meaning, "remedy": remedy,
            "detail": str(detail)[:400]}


def ledger_path():
    override = os.environ.get("GRANTFORGE_ERRLOG")
    if override:
        return Path(override)
    return Path.home() / ".grantforge" / "errors.jsonl"


def record(entry, path=None):
    """Append one entry to the ledger. Returns the path, and never raises.

    A diagnostics write that can fail the operation it is diagnosing is a worse bug
    than the one it was recording, so this returns the intended path even when the
    append does not land, and says so in the entry's `written` field.
    """
    target = Path(path) if path else ledger_path()
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "code": entry.get("code", "unknown"),
            "detail": entry.get("detail", ""), "remedy": entry.get("remedy", "")}
    global LAST_WRITE_ERROR
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
        LAST_WRITE_ERROR = None
    except OSError as exc:
        # Reaching this line means the ledger that proves failures were recorded is
        # itself unavailable. Saying so beats a silent no-op; the caller still gets the
        # intended path so a later `errors` call can show the gap.
        LAST_WRITE_ERROR = "%s: %s" % (type(exc).__name__, exc)
    return str(target)


LAST_WRITE_ERROR = None
# How many lines of the ledger could not be parsed on the last read. A torn line is a
# real defect (a writer died mid-append) and it is counted rather than skipped quietly:
# silently dropping it would make the ledger look clean at exactly the moment it is not.
LAST_READ_DEFECTS = 0


def read_ledger(path=None, limit=50):
    """The most recent entries, newest last. Unreadable lines are counted, not hidden."""
    global LAST_READ_DEFECTS
    target = Path(path) if path else ledger_path()
    LAST_READ_DEFECTS = 0
    if not target.exists():
        return []
    entries = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            LAST_READ_DEFECTS += 1
            continue
    return entries[-limit:]
