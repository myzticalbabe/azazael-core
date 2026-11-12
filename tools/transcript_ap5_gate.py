"""Transcript gate: enforce prose rules over persisted assistant messages.

WHY THIS EXISTS
---------------
The opencode plugin API exposes hooks for tool execution, system-prompt
transform, and inbound messages -- but none receives assistant OUTPUT. So a
rule governing what the agent SAYS (AP-5, SYSTEM_INSTRUCTIONS.md:2046) can be
injected as advice but can never be gated pre-send. Injection != enforcement.

Assistant prose is persisted in the session store, so it CAN be gated
downstream. This is the same shape as claim_guard.py at commit time: scan a
durable artifact after the fact.

    python tools/transcript_ap5_gate.py --limit 500
    python tools/transcript_ap5_gate.py --session ses_abc --json
    python tools/transcript_ap5_gate.py --source all
    python tools/transcript_ap5_gate.py --source manicode --json
    python tools/transcript_ap5_gate.py --list-sources

Exit codes:
    0  scanned at least one store, no permission-seeking language found
    1  findings present
    2  nothing scannable: every requested store was absent, unreadable, or
       held no assistant prose

STORE COVERAGE AND WHAT IS ACTUALLY VERIFIED
--------------------------------------------
Two stores are modelled because the project is driven from two clients, and a
gate that reads one of them reports a clean bill for output it never saw.

``opencode`` (default)
    SQLite at ``%USERPROFILE%\\.local\\share\\opencode\\opencode.db``, read
    through ``part`` JOIN ``message`` with the role in ``message.data``.
    Observed directly: this is the store the gate has always read.

``manicode`` (freebuff's client)
    ``%USERPROFILE%\\.config\\manicode\\projects\\<project>\\chats\\
    <session>\\chat-messages.json``. OBSERVED 2026-10-19 by reading real
    session files under ``projects/azazael/chats/``:

    * ``chat-messages.json`` is a JSON **array** of message objects with keys
      ``id``, ``variant``, ``content``, ``blocks``, ``timestamp``.
    * ``variant`` is ``"ai"`` for assistant turns (``"user"`` for the operator,
      ``"ai"`` messages also carry ``variant: "ai"`` with an empty top-level
      ``content``).
    * assistant prose lives in ``blocks[]`` entries with ``type == "text"``
      and ``textType == "text"``; ``textType == "reasoning"`` blocks are the
      model's scratchpad and are deliberately NOT scanned.
    * ``log.jsonl`` in the same directory is an operational log
      (``level``/``timestamp``/``msg``/``data``) and carries NO assistant
      prose -- it is not a transcript store. Reading it would produce a
      confident zero.

    INFERRED, not observed: that the schema is stable across older sessions
    and across projects. Only the ``azazael`` project and recent sessions were
    read, and every session file is parsed defensively because of that gap.

SOURCE REGISTRY
---------------
Sources are data now: ``SOURCE_REGISTRY`` holds one ``SourceSpec`` row per
candidate transcript source (name, parser key, locator, enabled). A parser
key selects the scanner in ``_SCANNERS``, so adding a source is adding a
row plus one function -- no gating logic changes. Rows whose store is NOT
verified ship as ``enabled=False`` with ``locator=None`` and a reason in
``why``: ``--list-sources`` lists them, ``--source all`` (or naming them
explicitly) reports them as deferred -- a missing client never silently
reads as pass coverage -- but nothing is ever scanned at a guessed path.

The default scan is still the opencode store only: pre-commit invokes this
gate bare, and that default (registry slot #1) must not silently widen.
``--source all`` is the wide pass across every ENABLED row.

THE DISTINCTION THIS GATE EXISTS TO PROTECT
-------------------------------------------
An absent store, a store with no assistant prose, and a scanned-and-clean
store are three different results. Collapsing them into "0 findings" is how a
coverage gap reads as a pass -- the gate reported clean for freebuff output it
had never opened. Every result is therefore reported with its own status, and
exit 2 means "nothing was actually scanned", never "clean".

The store is opened read-only (mode=ro). This gate NEVER writes.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from permission_seeking_guard import scan_all as scan_text  # noqa: E402

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

DEFAULT_DB = os.path.expandvars(
    r"%USERPROFILE%\.local\share\opencode\opencode.db"
)

DEFAULT_MANICODE_ROOT = os.path.expandvars(
    r"%USERPROFILE%\.config\manicode\projects"
)

#: A session file above this size is reported, not parsed. The observed files
#: run to ~11 MB; a 1.2 GB sibling exists in the sibling `log.jsonl` tree, so
#: an unbounded read is a real hazard rather than a hypothetical one.
DEFAULT_MAX_STORE_BYTES = 64 * 1024 * 1024

#: Where acknowledged emissions live. The gate has no history of its own, so a
#: single sentence emitted once by any client failed the pre-commit path forever
#: -- and the only ways to pass were to edit a transcript or delete the finding,
#: both dishonest. An acknowledgement is the same shape as every other ratchet
#: in this tree: what is already known is RECORDED and still printed, what is
#: new fails. It is per-emission (source|session|message|part|rule|line|match),
#: never per-rule, so acknowledging one sentence cannot whitelist the rule.
ACK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "transcript_ap5_ack.json",
)


def load_acks(path: str | None = None) -> dict:
    """key -> ack record. A missing or unreadable ledger is EMPTY, never a pass.

    Reporting this as "0 acknowledged" and an unreadable file as "0
    acknowledged" are the same number but not the same fact, so the caller is
    told which one it got.

    ``path=None`` resolves to ACK_PATH at CALL time. A `path: str = ACK_PATH`
    default binds at import, so a test (or any caller) that redirects ACK_PATH
    would still write the repository ledger -- measured 2026-11-02, and it is
    the kind of quiet "the setting did nothing" that a test needs to catch.
    """
    path = path or ACK_PATH
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    rows = data.get("acknowledged") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    return {r["key"]: r for r in rows if isinstance(r, dict) and r.get("key")}


def save_acks(acks: dict, path: str | None = None) -> str:
    """Write the ledger sorted by key, so a re-run is a byte-identical file."""
    path = path or ACK_PATH
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    body = {
        "why": "Emissions that already happened and cannot be un-said. New "
               "ones still fail the gate.",
        "acknowledged": [acks[k] for k in sorted(acks)],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=1)
        handle.write("\n")
    return path

#: Per-store statuses. Kept as plain strings so they survive JSON round-trips
#: and read the same in the human output.
SCANNED = "scanned"
NO_ASSISTANT_PROSE = "no_assistant_prose"
ABSENT = "absent"
UNREADABLE = "unreadable"
OVERSIZE = "oversize"


@dataclass
class SourceSpec:
    """One row of the transcript-source registry.

    Adding a transcript source means ADDING A ROW to ``SOURCE_REGISTRY``
    (plus one parser keyed in ``_SCANNERS``), never editing gate logic:

    name     identifier used by ``--source`` and printed with every result
    parser   key into ``_SCANNERS``; None marks a documented placeholder
    locator  default path/root; None marks a store that is NOT verified
    enabled  False rows are listed and deferred, never scanned
    why      why the row is in the registry (required for placeholders)
    cli_attr argparse attribute that may override ``locator`` at run time;
             its default equals the locator, so an unset flag scans the
             registry path exactly
    """

    name: str
    parser: str | None
    locator: str | None
    enabled: bool
    why: str = ""
    cli_attr: str = ""


SOURCE_REGISTRY: list[SourceSpec] = [
    # Slot #1 -- the store the gate has always read. Its scan behavior is
    # preserved verbatim: same SQLite URI, same LIMIT window, same stats.
    SourceSpec(
        name="opencode",
        parser="opencode",
        locator=DEFAULT_DB,
        enabled=True,
        why="observed directly: opencode's own session store (SQLite, "
            "table part JOIN message)",
        cli_attr="db",
    ),
    # Slot #2 -- freebuff (manicode client) chats. Verified IN-REPO, not
    # invented: tools/verify_engram_mcp.py and tools/test_supervisor_load.py
    # both derive sessions from the same projects/chats tree, and the file
    # schema is transcribed from real session files in
    # tests/test_ap5_gate_stores.py.
    SourceSpec(
        name="manicode",
        parser="manicode",
        locator=DEFAULT_MANICODE_ROOT,
        enabled=True,
        why="freebuff chats: projects/<proj>/chats/<session>/"
            "chat-messages.json",
        cli_attr="manicode_root",
    ),
    # Placeholders -- candidates with NO verifiable locator or schema in
    # this repo (state/ and docs/ name no third transcript store as of
    # 2026-10-19). Wiring one means locating the real store and writing a
    # parser; a fabricated locator would report a confident zero, which is
    # exactly the failure this gate exists to catch.
    SourceSpec(
        name="kilo",
        parser=None,
        locator=None,
        enabled=False,
        why="kilo client is referenced in-repo but no transcript path or "
            "log schema is verifiable here; enable only after locating a "
            "real store",
    ),
    SourceSpec(
        name="manicode-log",
        parser=None,
        locator=None,
        enabled=False,
        why="log.jsonl beside manicode chats is an operational log "
            "(level/timestamp/msg/data) with NO assistant prose; kept as an "
            "explicit NON-source so nobody wires it expecting coverage",
    ),
]


@dataclass
class Finding:
    session_id: str
    message_id: str
    part_id: str
    rule: str
    line: int
    match: str
    excerpt: str
    source: str = "opencode"


def finding_key(f: "Finding") -> str:
    """Identity of one emission. Stable, because the emission is frozen.

    A finding is a property of a transcript that already exists, so its key can
    include the line and the matched text: an EDIT to that part changes the key
    and the old acknowledgement stops covering it, which is the property an
    acknowledgement needs. A key that survived editing would silence the
    rewritten sentence too.
    """
    return "|".join((f.source, f.session_id, f.message_id, f.part_id,
                     f.rule, str(f.line), f.match))


@dataclass
class StoreResult:
    """One store's outcome. Status separates 'nothing scanned' from 'clean'."""

    source: str
    path: str
    status: str
    note: str = ""
    scanned_parts: int = 0
    skipped_reasoning: int = 0
    sessions: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    #: Plan announcements (`let me X`). Reported, never counted: they name the
    #: assistant's own in-progress action and offer the operator no choice, so
    #: they are not AP-5 violations. Kept as a field so the class cannot be
    #: dropped without the count changing.
    announcements: list = field(default_factory=list)


@dataclass
class Report:
    db: str
    scanned_parts: int = 0
    scanned_sessions: set = field(default_factory=set)
    findings: list = field(default_factory=list)
    announcements: list = field(default_factory=list)
    schema_ok: bool = True
    note: str = ""
    source: str = "opencode"
    stores: list = field(default_factory=list)


def _role_of(message_data: Any) -> str:
    """Extract role from a message.data JSON blob (no role column exists)."""
    if isinstance(message_data, str):
        try:
            message_data = json.loads(message_data)
        except (ValueError, TypeError) as exc:
            # Unparseable role blob -> treat as not-assistant, which is the
            # conservative direction (we skip rather than scan someone else's
            # prose). Record it anyway: a role we cannot read is a schema
            # change, and silently dropping rows would shrink the scan without
            # saying so.
            note_failure("transcript_ap5_gate:_role_of", exc)
            return ""
    if isinstance(message_data, dict):
        role = message_data.get("role")
        if isinstance(role, str):
            return role
    return ""


def _text_of(part_data: Any) -> str:
    """Extract assistant prose from a part.data JSON blob.

    Falls back to the raw string when the shape is unknown: an unparseable
    blob is still text a human would have read, so skipping it would be a
    silent hole rather than a reported limitation.
    """
    if isinstance(part_data, str):
        try:
            part_data = json.loads(part_data)
        except (ValueError, TypeError) as exc:
            # Record, then fall back. The raw blob is still text a human would
            # have read, so returning it beats dropping it -- but an unparseable
            # blob is a schema change and must be visible, not swallowed.
            note_failure("transcript_ap5_gate:_text_of", exc)
            return part_data
    if isinstance(part_data, dict):
        if part_data.get("type") not in (None, "text"):
            return ""
        text = part_data.get("text")
        return text if isinstance(text, str) else ""
    return ""


def _excerpt(around: str, width: int = 90) -> str:
    flat = " ".join(around.split())
    return flat if len(flat) <= width else flat[: width - 1] + "\u2026"


def _findings_for(
    text: str,
    session_id: str,
    message_id: str,
    part_id: str,
    source: str,
) -> tuple:
    """Scan one prose blob; return (violations, announcements) as Findings.

    The two classes come back separately and the exit code below reads only
    the first: `let me X` is a plan announcement, not a request for permission
    (permission_seeking_guard's docstring records the measurement), so counting
    it as a violation is exactly the false positive that made this gate's own
    transcript look non-compliant.
    """
    violations, announcements = scan_text(text)
    lines = text.splitlines()

    def _build(items: list, rule_prefix: str = "") -> list:
        built = []
        for f in items:
            lo = max(0, f.line - 1)
            context = lines[lo] if lo < len(lines) else ""
            built.append(
                Finding(
                    session_id=session_id or "?",
                    message_id=message_id or "?",
                    part_id=part_id or "?",
                    rule=rule_prefix + f.name,
                    line=f.line,
                    match=f.match,
                    excerpt=_excerpt(context),
                    source=source,
                )
            )
        return built

    return _build(violations), _build(announcements, "announcement:")


def scan_store(
    db: str,
    session: str | None = None,
    limit: int = 500,
) -> Report:
    report = Report(db=db)
    uri = "file:%s?mode=ro" % db.replace("\\", "/")
    try:
        con = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as exc:
        report.schema_ok = False
        report.note = "cannot open store: %s" % exc
        return report

    try:
        q = (
            "SELECT p.session_id, p.message_id, p.id, p.data, m.data "
            "FROM part p LEFT JOIN message m ON m.id = p.message_id "
        )
        args: list[Any] = []
        if session:
            q += "WHERE p.session_id = ? "
            args.append(session)
        q += "ORDER BY p.time_created DESC LIMIT ?"
        args.append(limit)

        try:
            rows = con.execute(q, args).fetchall()
        except sqlite3.Error as exc:
            report.schema_ok = False
            report.note = "query failed: %s" % exc
            return report

        for sess_id, msg_id, part_id, part_data, msg_data in rows:
            if _role_of(msg_data) != "assistant":
                continue
            text = _text_of(part_data)
            if not text:
                continue
            report.scanned_parts += 1
            report.scanned_sessions.add(sess_id or "?")
            violations, announcements = _findings_for(
                text, sess_id, msg_id, part_id, "opencode"
            )
            report.findings.extend(violations)
            report.announcements.extend(announcements)
        return report
    finally:
        con.close()


# ─── manicode (freebuff client) ──────────────────────────────────────────


def _message_list(doc: Any) -> list:
    """Return the message array from a parsed chat-messages.json document."""
    if isinstance(doc, list):
        return [m for m in doc if isinstance(m, dict)]
    if isinstance(doc, dict):
        for key in ("messages", "chatMessages"):
            value = doc.get(key)
            if isinstance(value, list):
                return [m for m in value if isinstance(m, dict)]
        return [doc]
    return []


def _prose_blocks(message: dict) -> tuple:
    """Split an assistant message into (prose_blocks, reasoning_count).

    Transcribed from observed session files: assistant prose is
    ``blocks[i] = {"type": "text", "content": ..., "textType": "text"}``;
    ``textType: "reasoning"`` is scratchpad and is counted, not scanned.
    """
    prose: list = []
    reasoning = 0
    blocks = message.get("blocks")
    if isinstance(blocks, list) and blocks:
        for idx, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text_type = block.get("textType")
            if text_type == "reasoning":
                reasoning += 1
                continue
            if text_type not in (None, "text"):
                continue
            text = block.get("content")
            if isinstance(text, str) and text.strip():
                prose.append((str(idx), text))
        return prose, reasoning
    # No blocks at all: fall back to the top-level content, which is where a
    # user turn keeps its text. Observed assistant turns carry an empty
    # top-level content, so this never double-counts a block-bearing message.
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        prose.append(("0", content))
    return prose, reasoning


def _session_files(root: str, session: str | None) -> list:
    """Newest-first list of (session_id, chat-messages.json) under root."""
    pattern = os.path.join(root, "*", "chats", "*", "chat-messages.json")
    paths = glob.glob(pattern)
    pairs = []
    for path in paths:
        session_id = os.path.basename(os.path.dirname(path))
        if session and session_id != session:
            continue
        pairs.append((session_id, path))
    # Session directory names are ISO timestamps, so lexical order is
    # chronological. Newest first matches the opencode leg's ORDER BY DESC.
    pairs.sort(key=lambda pair: pair[0], reverse=True)
    return pairs


def scan_manicode_store(
    root: str = DEFAULT_MANICODE_ROOT,
    session: str | None = None,
    limit: int = 500,
    max_bytes: int = DEFAULT_MAX_STORE_BYTES,
) -> StoreResult:
    """Scan freebuff session transcripts for AP-5 findings.

    Never raises. Every failure mode becomes a status, because an exception
    here would be caught by the fail-open hook and read as "clean".
    """
    result = StoreResult(source="manicode", path=root, status=ABSENT)
    if not os.path.isdir(root):
        result.note = "no manicode projects root at %s" % root
        return result

    pairs = _session_files(root, session)
    if not pairs:
        result.note = (
            "no session transcripts under %s (filter session=%r)"
            % (root, session)
        )
        return result

    scanned_any_file = False
    unreadable = []
    oversize = []
    for session_id, path in pairs:
        if result.scanned_parts >= limit:
            break
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            note_failure("transcript_ap5_gate:manicode:size", exc)
            unreadable.append("%s (%s)" % (session_id, exc))
            continue
        if size > max_bytes:
            oversize.append("%s (%d bytes)" % (session_id, size))
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                doc = json.load(handle)
        except (OSError, ValueError) as exc:
            # A session we cannot parse must not silently shrink the scan.
            note_failure("transcript_ap5_gate:manicode:parse", exc)
            unreadable.append("%s (%s)" % (session_id, exc))
            continue

        scanned_any_file = True
        if session_id not in result.sessions:
            result.sessions.append(session_id)
        for message in _message_list(doc):
            if message.get("variant") != "ai":
                continue
            prose, reasoning = _prose_blocks(message)
            result.skipped_reasoning += reasoning
            message_id = str(message.get("id") or "?")
            for part_id, text in prose:
                if result.scanned_parts >= limit:
                    break
                result.scanned_parts += 1
                violations, announcements = _findings_for(
                    text, session_id, message_id, part_id, "manicode"
                )
                result.findings.extend(violations)
                result.announcements.extend(announcements)

    if result.scanned_parts:
        result.status = SCANNED
        notes = []
        if unreadable:
            notes.append("unreadable: %s" % ", ".join(unreadable[:5]))
        if oversize:
            notes.append("oversize: %s" % ", ".join(oversize[:5]))
        result.note = "; ".join(notes)
    elif oversize or unreadable:
        # Which failure to name matters: "too large to parse" and "cannot be
        # read" are different remedies. Mixed cases report UNREADABLE and say
        # both, because a single label would hide one of them.
        result.status = UNREADABLE if unreadable else OVERSIZE
        result.note = "no prose scanned; unreadable: %s oversize: %s" % (
            ", ".join(unreadable[:5]) or "-",
            ", ".join(oversize[:5]) or "-",
        )
    elif scanned_any_file:
        result.status = NO_ASSISTANT_PROSE
        result.note = (
            "%d session file(s) parsed, no assistant prose blocks"
            % len(result.sessions)
        )
    else:
        result.status = NO_ASSISTANT_PROSE
        result.note = "no session file could be read"
    return result


def _opencode_result(db: str, report: Report) -> StoreResult:
    """Convert the SQLite scan into the uniform per-store shape."""
    result = StoreResult(source="opencode", path=db, status=ABSENT)
    if not report.schema_ok:
        result.status = UNREADABLE
        result.note = report.note
        result.findings = list(report.findings)
        return result
    result.scanned_parts = report.scanned_parts
    result.sessions = sorted(report.scanned_sessions)
    result.findings = list(report.findings)
    result.announcements = list(report.announcements)
    if report.scanned_parts:
        result.status = SCANNED
    else:
        result.status = NO_ASSISTANT_PROSE
        result.note = "store opened, no assistant parts in the scanned window"
    return result


# ─── source registry runners ─────────────────────────────────────────────


def _scan_opencode_source(
    spec: SourceSpec,
    session: str | None,
    limit: int,
    max_bytes: int,  # uniform scanner signature; unused on this leg
) -> StoreResult:
    """Scanner for registry slot 'opencode'.

    max_bytes is part of the uniform scanner signature only: this leg caps
    work with --limit rows (a read-only SQLite open is cheap), exactly as
    the gate has always done.
    """
    db = spec.locator
    if db is None:
        return StoreResult(
            source=spec.name, path="", status=ABSENT,
            note="registry slot has no locator",
        )
    if not os.path.exists(db):
        return StoreResult(
            source=spec.name, path=db, status=ABSENT,
            note="no session store at %s" % db,
        )
    return _opencode_result(db, scan_store(db, session, limit))


def _scan_manicode_source(
    spec: SourceSpec,
    session: str | None,
    limit: int,
    max_bytes: int,
) -> StoreResult:
    """Scanner for registry slot 'manicode' (freebuff chats)."""
    if spec.locator is None:
        return StoreResult(
            source=spec.name, path="", status=ABSENT,
            note="registry slot has no locator",
        )
    return scan_manicode_store(
        root=spec.locator,
        session=session,
        limit=limit,
        max_bytes=max_bytes,
    )


#: parser-key -> scanner. The registry rows name their scanners by this
#: key, so the table is the whole wiring between data and behavior.
_SCANNERS = {
    "opencode": _scan_opencode_source,
    "manicode": _scan_manicode_source,
}


def _slot_note(spec: SourceSpec) -> str:
    """Why a registry slot will not run; empty string when it will."""
    if not spec.enabled:
        return "disabled slot -- %s" % (spec.why or "not enabled in registry")
    if spec.parser not in _SCANNERS:
        return "enabled but scanner key %r is not registered" % spec.parser
    return ""


def run_source(
    spec: SourceSpec,
    session: str | None = None,
    limit: int = 500,
    max_bytes: int = DEFAULT_MAX_STORE_BYTES,
) -> StoreResult | None:
    """Run one registry slot's scanner; None when the slot must not run.

    Scanners convert their own failure modes into statuses, so one bad
    store degrades only ITS row (nothing crashes the scan, and nothing
    fail-opens into a false clean on another source). Anything that
    escapes a scanner anyway is caught here and becomes UNREADABLE for
    that one source.
    """
    if _slot_note(spec):
        return None
    scanner = _SCANNERS.get(spec.parser)
    if scanner is None:  # defensive; _slot_note already filters this
        return None
    try:
        return scanner(spec, session, limit, max_bytes)
    except Exception as exc:  # noqa: BLE001 -- a status, never a crash
        note_failure("transcript_ap5_gate:run_source:%s" % spec.name, exc)
        return StoreResult(
            source=spec.name,
            path=spec.locator or "",
            status=UNREADABLE,
            note="scanner raised: %s" % (exc,),
        )


def _print_registry() -> None:
    """Show every registry row -- slots are discoverable, never hidden."""
    print("transcript gate -- source registry (%d slot(s))"
          % len(SOURCE_REGISTRY))
    for spec in SOURCE_REGISTRY:
        state = "enabled" if spec.enabled else "disabled"
        scanner = spec.parser if spec.parser in _SCANNERS else "none"
        print("  %-13s %-8s scanner=%-8s locator=%s"
              % (spec.name, state, scanner, spec.locator or "-"))
        if spec.why:
            print("              why: %s" % spec.why)


def _merge(results: list) -> Report:
    merged = Report(db=", ".join(r.path for r in results))
    merged.source = ",".join(r.source for r in results)
    merged.stores = results
    for result in results:
        merged.scanned_parts += result.scanned_parts
        merged.scanned_sessions.update(result.sessions)
        merged.findings.extend(result.findings)
        merged.announcements.extend(result.announcements)
        if result.status == UNREADABLE:
            merged.schema_ok = False
            merged.note = (merged.note + "; " if merged.note else "") + (
                "%s: %s" % (result.source, result.note)
            )
    return merged


_STATUS_TEXT = {
    SCANNED: "scanned %d assistant part(s) across %d session(s)",
    NO_ASSISTANT_PROSE: "NO ASSISTANT PROSE IN STORE -- nothing was scanned",
    ABSENT: "STORE ABSENT -- nothing was scanned",
    UNREADABLE: "STORE UNREADABLE -- nothing was scanned",
    OVERSIZE: "STORE TOO LARGE -- nothing was scanned",
}


def _print_store_line(result: StoreResult) -> None:
    template = _STATUS_TEXT.get(result.status, "%s" % result.status)
    if result.status == SCANNED:
        detail = template % (result.scanned_parts, len(result.sessions))
    else:
        detail = template
    print("  %-9s %s" % (result.source + ":", detail))
    print("            %s" % result.path)
    if result.note:
        print("            note: %s" % result.note)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB, help="opencode session store path")
    ap.add_argument("--session", help="restrict to one session id")
    ap.add_argument("--limit", type=int, default=500, help="parts to scan")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--source",
        choices=tuple(s.name for s in SOURCE_REGISTRY) + ("all",),
        default="opencode",
        help="which transcript source(s) to scan (default: opencode, "
             "registry slot #1; 'all' = every enabled source; disabled "
             "slots are selectable and defer themselves explicitly)",
    )
    ap.add_argument(
        "--manicode-root",
        default=DEFAULT_MANICODE_ROOT,
        help="manicode projects root (default: %%USERPROFILE%%/.config/manicode/projects)",
    )
    ap.add_argument(
        "--max-store-bytes",
        type=int,
        default=DEFAULT_MAX_STORE_BYTES,
        help="skip a session transcript larger than this (reported, not silent)",
    )
    ap.add_argument(
        "--list-sources",
        action="store_true",
        help="print the source registry (name / state / scanner / locator) "
             "and exit",
    )
    ap.add_argument(
        "--ack",
        metavar="WHY",
        help="record the CURRENT findings in the acknowledgement ledger and "
             "exit 0. Acknowledged findings keep printing on every run; only "
             "NEW ones fail. Use it for prose that was already emitted and "
             "cannot be un-said -- not to quiet a rule.",
    )
    args = ap.parse_args(argv)

    if args.list_sources:
        _print_registry()
        return 0

    results: list = []
    skipped: list[tuple[str, str]] = []
    for spec in SOURCE_REGISTRY:
        if args.source != "all" and spec.name != args.source:
            continue
        if not spec.enabled or spec.parser not in _SCANNERS:
            skipped.append((spec.name, _slot_note(spec)))
            continue
        # One CLI flag may override a slot's locator (--db for opencode,
        # --manicode-root for manicode); its argparse default equals the
        # registry locator, so an unset flag scans the registered path.
        # No truthiness guard: an explicitly passed value (even "") must
        # win, matching the pre-registry main behavior for --db.
        if spec.cli_attr:
            spec = replace(spec, locator=getattr(args, spec.cli_attr))
        result = run_source(spec, args.session, args.limit,
                            args.max_store_bytes)
        if result is not None:
            results.append(result)

    report = _merge(results)
    scannable = [r for r in results if r.status == SCANNED]

    acks = load_acks()
    acked = [f for f in report.findings if finding_key(f) in acks]
    fresh = [f for f in report.findings if finding_key(f) not in acks]

    if args.ack:
        now = datetime.now(timezone.utc).isoformat()
        for f in report.findings:
            key = finding_key(f)
            acks[key] = {
                "key": key,
                "source": f.source,
                "session": f.session_id,
                "message": f.message_id,
                "part": f.part_id,
                "rule": f.rule,
                "line": f.line,
                "match": f.match,
                "excerpt": f.excerpt,
                "why": args.ack,
                "acked_at": now,
            }
        path = save_acks(acks)
        print("acknowledged %d new emission(s); ledger: %s"
              % (len(report.findings), path))
        for f in report.findings:
            print("  [%s] %s %s msg=%s line=%d %r"
                  % (f.rule, f.source, f.session_id, f.message_id, f.line,
                     f.match))
        return 0

    if args.json:
        print(
            json.dumps(
                {
                    "source": report.source,
                    "db": report.db,
                    "skipped_slots": [
                        {"name": name, "reason": reason}
                        for name, reason in skipped
                    ],
                    "scanned_parts": report.scanned_parts,
                    "scanned_sessions": sorted(report.scanned_sessions),
                    "findings": [asdict(f) for f in report.findings],
                    "new_findings": [asdict(f) for f in fresh],
                    "acknowledged_count": len(acked),
                    # Reported, never counted: the exit code below reads
                    # `findings` only, so an announcement cannot fail a run.
                    "announcements": [asdict(f) for f in report.announcements],
                    "announcement_count": len(report.announcements),
                    "stores": [
                        {
                            "source": r.source,
                            "path": r.path,
                            "status": r.status,
                            "note": r.note,
                            "scanned_parts": r.scanned_parts,
                            "skipped_reasoning": r.skipped_reasoning,
                            "sessions": r.sessions,
                            "findings": len(r.findings),
                            "announcements": len(r.announcements),
                            "scanned": r.status == SCANNED,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
        if fresh:
            return 1
        return 0 if scannable else 2

    print("transcript gate -- assistant prose (post-hoc)")
    for result in results:
        _print_store_line(result)
    for name, reason in skipped:
        print("  deferred  %s: %s" % (name, reason))
    if report.announcements:
        print("  announcements: %d (plan announcements -- NOT violations; "
              "they offer the operator no decision)"
              % len(report.announcements))
        shown = report.announcements[:5]
        for f in shown:
            print("      [%s] %s line=%d %r"
                  % (f.rule, f.source, f.line, f.match))
        if len(report.announcements) > len(shown):
            print("      ... and %d more (total %d; full list in --output JSON)"
                  % (len(report.announcements) - len(shown),
                     len(report.announcements)))

    if not scannable:
        print("")
        print("  AP-5: NOT SCANNED -- no store yielded assistant prose.")
        print("        This is not a clean result. Nothing was audited.")
        return 2

    if acked:
        print("  acknowledged: %d prior emission(s) (recorded in %s; "
              "reported on every run, not gating)"
              % (len(acked), ACK_PATH))

    if not report.findings:
        print("")
        print("  AP-5: clean (%d part(s) scanned across %d store(s))"
              % (report.scanned_parts, len(scannable)))
        return 0

    if not fresh:
        print("")
        print("  AP-5: no NEW finding (%d already-acknowledged emission(s) "
              "remain on record)" % len(acked))
        return 0

    print("")
    print("  AP-5: %d new finding(s), %d acknowledged"
          % (len(fresh), len(acked)))
    for f in fresh:
        # Full ids: 12-char truncations are not lookup keys (2026-10-22
        # lane-B #9 — a truncated id reproduces to "not found").
        print("  [%s] %s %s msg=%s line=%d %r" % (
            f.rule, f.source, f.session_id, f.message_id, f.line,
            f.match))
        if f.excerpt:
            print("      \u2026 %s" % f.excerpt)
    print(
        "\nMandated actions are NOT requests (SYSTEM_INSTRUCTIONS.md:2046).\n"
        "This gate runs AFTER emission: it cannot prevent a violation, only\n"
        "make it visible. Prevention is not available -- no assistant-output\n"
        "hook exists in the plugin API."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
