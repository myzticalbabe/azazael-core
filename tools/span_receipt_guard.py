"""span_receipt_guard.py — a routed delegation must carry a span row.

WHY THIS EXISTS. AOA's router writes one span per routed call
(``core/delegation_tracer.emit_delegation``), and that write is deliberately
best-effort: telemetry must not turn a working route into a failed one, so
``emit_delegation`` returns ``None`` -- silently -- when tracing is off, when the
writer cannot be imported, or when the SQLite write fails. The result dict only
grows a ``trace`` key when the write reported success.

So "this route was traced" is an assertion with exactly three failure modes that
produce no evidence and no exception:

  1. ``AZAZAEL_AOA_TRACING`` is off, so nothing was even attempted;
  2. ``CORE.cd3_exporter`` was not importable, so ``record_span`` is None;
  3. ``record_span`` returned False -- a write that failed and was buried by
     ``note_failure``.

This guard turns the assertion into a check. It does not trust the router's own
report that a span was written; it opens the span table read-only and looks for
**this** row, by trace_id AND span_id.

WHAT A RECEIPT IS HERE. The row, read back by identity:

    {"trace_id": "<32 hex>",
     "span_id": "<16 hex>",
     "found": true}

``found`` is the load-bearing field; ``name``, ``status_code``, ``database`` and
``rows_in_trace`` are carried so a reader can see *what* was found without a
second query. Extra keys are allowed and ignored.

WHAT IT CATCHES
  1. A route that returned no trace reference at all (any of the three causes
     above), with the environment read to say which one it probably is.
  2. A trace address with no span id. "Something was written under this trace" is
     not evidence about *this arm route* -- a sibling span would satisfy it.
  3. A span id that no row carries: the write was reported and did not persist,
     or the row was replaced by a later write under a colliding id.
  4. A span id that exists only in a *different* trace: the id is matched against
     the trace it claims, not against the table as a whole.
  5. A receipt whose ids are not non-empty strings, or whose ``found`` is not a
     bool (``"true"`` and ``1`` are not a reading).

THE LIMIT, STATED PLAINLY. This proves a row is present and attributable. It does
not prove the row describes the call that was made -- a writer could record the
wrong arm_id and still pass (the attributes are the writer's claim, not the
guard's). It also cannot run after the span was purged, so a correct refusal can
mean "gone", not "never written"; the reason names which by reporting whether the
database file and the table exist at all. RULE 0B-D: this is presence, not truth.

READ-ONLY, BY CONSTRUCTION. The database is opened ``mode=ro``. A check that
created the file it was asked to inspect would report "no rows" for a database it
had just made -- the empty-file-reads-as-clean failure the board canon records.

Usage:
    python tools/span_receipt_guard.py --result route_result.json
    python tools/span_receipt_guard.py --trace <id> --span <id>
    python tools/span_receipt_guard.py --result route_result.json --db other.db
    python tools/span_receipt_guard.py --selftest

With ``--result`` the file is the dict ``route_task`` returned, written to disk
with ``json.dump``; the guard reads its ``trace`` key. ``--db`` defaults to the
live telemetry store, resolved by ``cd3_exporter`` so the guard looks where the
writer writes.

Exit: 0 the route has its span row, 1 it does not (or the selftest failed).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: Resolved lazily: importing cd3_exporter reads the paths file and may reload it,
#: which a caller that only wants to validate an in-memory receipt should not pay.
_DEFAULT_DB: str | None = None

_TRACING_ENV = "AZAZAEL_AOA_TRACING"
_OFF = {"0", "off", "false", "no", "disabled"}


def default_db() -> str:
    """The span database the writer targets, resolved through its own module."""
    global _DEFAULT_DB
    if _DEFAULT_DB is None:
        from CORE.cd3_exporter import TELEMETRY_DB_PATH  # noqa: WPS433
        _DEFAULT_DB = str(TELEMETRY_DB_PATH)
    return _DEFAULT_DB


def tracing_enabled() -> bool:
    """Mirror of ``delegation_tracer.tracing_enabled``, read from the environment.

    Duplicated rather than imported so the guard can explain a refusal in an
    environment where the tracer itself will not import -- that is one of the
    three causes it exists to name.
    """
    return str(os.environ.get(_TRACING_ENV, "on")).strip().lower() not in _OFF


def _open_ro(db_path: str) -> sqlite3.Connection:
    """Read-only handle. Never creates the file it was asked to inspect.

    The absolute form is ``file:///C:/...``; ``file:C:/...`` is parsed as a
    relative path on this platform, so the drive letter must sit after the third
    slash or the guard looks in the wrong place and reports a false absence.
    """
    p = Path(db_path)
    if p.is_absolute():
        uri = "file:///%s?mode=ro" % p.as_posix().lstrip("/")
    else:
        uri = "file:%s?mode=ro" % p.as_posix()
    return sqlite3.connect(uri, uri=True, timeout=5)


def lookup(trace_id: str, span_id: str, db_path: str | None = None) -> dict:
    """Find the row this (trace, span) pair names. Returns a receipt.

    ``found`` is True only when a row carries BOTH ids. An exception is never
    raised: a missing database, a missing table and a closed handle all mean *no
    row*, and each is reported as the reason it means that.
    """
    path = db_path or default_db()
    receipt: dict = {
        "trace_id": trace_id or "",
        "span_id": span_id or "",
        "found": False,
        "database": path,
    }
    if not Path(path).exists():
        receipt["reason"] = "the span database does not exist at %s" % path
        return receipt
    try:
        conn = _open_ro(path)
    except sqlite3.Error as exc:
        receipt["reason"] = "the span database cannot be opened (%s)" % exc
        return receipt
    try:
        row = conn.execute(
            "SELECT name, status_code FROM otel_spans "
            " WHERE trace_id = ? AND span_id = ?",
            (receipt["trace_id"], receipt["span_id"]),
        ).fetchone()
        if row is None:
            receipt["reason"] = "no row in otel_spans carries this trace_id and span_id"
            # A count is the difference between "the route was never traced" and
            # "the trace is there and this span is not", which are different bugs.
            try:
                (rows,) = conn.execute(
                    "SELECT COUNT(*) FROM otel_spans WHERE trace_id = ?",
                    (receipt["trace_id"],),
                ).fetchone()
                receipt["rows_in_trace"] = int(rows)
            except sqlite3.Error as exc:
                # The count is context, not the verdict -- the refusal stands
                # without it -- but a reader must be able to tell "zero rows"
                # from "the count could not be read".
                receipt["rows_in_trace"] = None
                receipt["rows_error"] = str(exc)
            return receipt
        receipt["found"] = True
        receipt["name"] = row[0]
        receipt["status_code"] = row[1]
    except sqlite3.Error as exc:
        receipt["reason"] = (
            "otel_spans cannot be read (%s); the table may not exist yet, which "
            "means no span was ever written to this file" % exc)
        return receipt
    finally:
        conn.close()
    return receipt


def receipt_for(result: dict, db_path: str | None = None) -> dict | None:
    """Build the receipt for a ``route_task`` result.

    None when the result carries no trace reference -- which is itself the defect
    this guard reports, kept distinct from a receipt that says ``found: false``
    so the two failure modes stay tellable apart in the output.
    """
    trace = result.get("trace") if isinstance(result, dict) else None
    if not isinstance(trace, dict):
        return None
    return lookup(trace.get("trace_id") or "", trace.get("span_id") or "",
                  db_path=db_path)


def validate(receipt: object) -> list[tuple[str, str]]:
    """Return (field, reason) for every problem; empty means the route is traced.

    Field names are returned rather than a boolean so a refusal can say which
    part of the receipt is wrong instead of "invalid".
    """
    if receipt is None:
        reason = ("the route returned no trace reference; emit_delegation "
                  "returned None")
        if not tracing_enabled():
            reason += (" -- %s is off in this environment, so no span was "
                       "attempted" % _TRACING_ENV)
        else:
            reason += (" -- tracing is on, so either the writer was not "
                       "importable or the write failed and was buried")
        return [("trace", reason)]
    if not isinstance(receipt, dict):
        return [("receipt", "is a %s, not an object" % type(receipt).__name__)]

    problems: list[tuple[str, str]] = []
    for field in ("trace_id", "span_id"):
        if field not in receipt:
            problems.append((field, "missing from the receipt"))
            continue
        value = receipt[field]
        if not isinstance(value, str):
            problems.append(
                (field, "is a %s, must be a string" % type(value).__name__))
        elif not value.strip():
            problems.append(
                (field, "is empty; an empty id cannot name a row -- a trace with "
                        "no span id is not evidence about this arm route"))

    if "found" not in receipt:
        problems.append(("found", "missing from the receipt"))
    elif not isinstance(receipt["found"], bool):
        problems.append(
            ("found", "is a %s (%r), must be a bool"
                      % (type(receipt["found"]).__name__, receipt["found"])))
    elif receipt["found"] is False and not problems:
        detail = receipt.get("reason") or "no row carries this trace_id and span_id"
        rows = receipt.get("rows_in_trace")
        if rows is not None:
            detail += " (%d row(s) exist under this trace_id)" % rows
        problems.append(
            ("found", "no span row for this route: %s [%s]"
                      % (detail, receipt.get("database", "?"))))

    return problems


def _report(problems: list[tuple[str, str]]) -> int:
    if not problems:
        return 0
    print("REFUSED: no span row for this arm route (%d problem(s))" % len(problems))
    for field, reason in problems:
        print("  - %s: %s" % (field, reason))
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Require a routed delegation to have produced a span row")
    ap.add_argument("--result", metavar="FILE",
                    help="JSON file holding a route_task() result dict")
    ap.add_argument("--trace", metavar="ID", help="trace_id to check directly")
    ap.add_argument("--span", metavar="ID", help="span_id to check directly")
    ap.add_argument("--db", metavar="FILE",
                    help="span database (default: the writer's telemetry store)")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the guard refuses an untraced route and accepts "
                         "a traced one")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.result:
        try:
            raw = Path(args.result).read_text(encoding="utf-8")
        except OSError as exc:
            print("REFUSED: result file cannot be read (%s)" % exc.strerror)
            return 1
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            print("REFUSED: result file is not JSON (%s at line %d)"
                  % (exc.msg, exc.lineno))
            return 1
        receipt = receipt_for(result, db_path=args.db)
        if receipt is None:
            return _report(validate(None))
        problems = validate(receipt)
        if problems:
            return _report(problems)
        print("ACCEPTED: span %s present in trace %s (%s, status_code=%s)"
              % (receipt["span_id"], receipt["trace_id"],
                 receipt.get("name", "?"), receipt.get("status_code", "?")))
        return 0

    if not args.trace or not args.span:
        print("REFUSED: need --result FILE, or both --trace and --span")
        return 1
    problems = validate(lookup(args.trace, args.span, db_path=args.db))
    if problems:
        return _report(problems)
    print("ACCEPTED: span %s present in trace %s" % (args.span, args.trace))
    return 0


# ── selftest: the negative controls first, then a real write ────────────────
def _write_span(db_path: str, trace_id: str, span_id: str) -> bool:
    from CORE.cd3_exporter import record_span
    return record_span(
        trace_id=trace_id, span_id=span_id,
        name="delegation selftest -> arm1",
        start_time=1, end_time=2, status_code=1,
        attributes={"agent.delegation.arm_id": "arm1"},
        db_path=db_path,
    )


def selftest() -> int:
    fails = 0
    total = 0
    print("=" * 72)
    print("SPAN RECEIPT SELF-TEST -- an untraced route must be REFUSED")
    print("=" * 72)

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "telemetry_store.db")
        trace_id, span_id = uuid.uuid4().hex, uuid.uuid4().hex[:16]
        other_trace = uuid.uuid4().hex

        # (label, receipt, the field the refusal MUST name)
        cases: list[tuple[str, object, str]] = [
            ("no trace reference at all", None, "trace"),
            ("receipt is a string", "span", "receipt"),
            ("found is the string 'true'",
             {"trace_id": trace_id, "span_id": span_id, "found": "true"}, "found"),
            ("trace id empty",
             {"trace_id": "", "span_id": span_id, "found": True}, "trace_id"),
            ("span id empty (a trace alone is not evidence)",
             {"trace_id": trace_id, "span_id": "", "found": True}, "span_id"),
        ]

        print("-" * 72)
        print("shape refusals")
        print("-" * 72)
        total += len(cases)
        for label, receipt, field in cases:
            problems = validate(receipt)
            named = {f for f, _ in problems}
            ok = bool(problems) and field in named
            print("  [%s] refuse: %s" % ("PASS" if ok else "FAIL", label))
            if not ok:
                fails += 1
                print("         expected field %r, got %s" % (field, sorted(named)))

        print("-" * 72)
        print("read-back refusals -- a row that does not exist is not a receipt")
        print("-" * 72)
        total += 4
        # Nothing has been written yet, so this id is guaranteed absent.
        problems = validate(lookup(trace_id, span_id, db_path=db))
        ok = bool(problems) and {f for f, _ in problems} == {"found"}
        print("  [%s] refuse: span never written" % ("PASS" if ok else "FAIL"))
        if not ok:
            fails += 1
            print("         got %s" % (problems,))

        assert _write_span(db, trace_id, span_id), "selftest could not write a span"

        problems = validate(lookup(other_trace, span_id, db_path=db))
        ok = bool(problems)
        print("  [%s] refuse: span exists only under a different trace_id"
              % ("PASS" if ok else "FAIL"))
        if not ok:
            fails += 1

        print("-" * 72)
        print("and MUST ACCEPT a real row -- otherwise it is a guard that only says no")
        print("-" * 72)
        result = {"status": "ok",
                  "trace": {"trace_id": trace_id, "span_id": span_id}}
        receipt = receipt_for(result, db_path=db)
        problems = validate(receipt)
        ok = not problems
        print("  [%s] accept: a routed call whose span row is present"
              % ("PASS" if ok else "FAIL"))
        if not ok:
            fails += 1
            for field, reason in problems:
                print("         false refusal: %s: %s" % (field, reason))
        if ok and receipt is not None:
            assert receipt["name"] == "delegation selftest -> arm1", receipt
            assert receipt["status_code"] == 1, receipt

        problems = validate(receipt_for({"status": "ok"}, db_path=db))
        ok = bool(problems)
        print("  [%s] refuse: the same call with no trace key on the result"
              % ("PASS" if ok else "FAIL"))
        if not ok:
            fails += 1

    print("\n  %d cases, %d failure(s)" % (total, fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
