# coding: utf-8
"""DualLedgerValidator cost and correctness controls (RULE 0Y-4).

Why this file exists
--------------------
On 2026-10-30 two test files hung and no suite run had ever completed. The
faulthandler frame named the cause exactly:

    governance.py:791 _read_memory_events  <- cross_validate
      <- check_rule_0y <- check_all <- test_check_all_returns_compliance_result

`_read_memory_events` issued `SELECT * FROM memories ... fetchall()` over a
5.4 GB / 4,028,967-row store to answer "which sessions do both ledgers share?".
Measured cost of one call: minutes and gigabytes of RAM, on every gate
assertion. After the fix the same call is 0.09 s.

These tests are the negative controls for that repair. The first one fails if
anybody reintroduces a whole-store read on the comparison path; the last two
prove the comparison can still fire when the two ledgers do share a session id,
so a "green" result means "nothing to report", not "oracle disabled".
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.compliance import governance  # noqa: E402


def _make_store(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """rows: (session_id, event_type, source)."""
    con = sqlite3.connect(str(path), timeout=5)
    con.execute(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT, event_type TEXT, source TEXT, data TEXT)"
    )
    con.executemany(
        "INSERT INTO memories (session_id, event_type, source, data) VALUES (?,?,?,?)",
        [(sid, et, src, "{}") for sid, et, src in rows],
    )
    con.commit()
    con.close()


def _write_audit(audit_dir: Path, events: list[dict], name: str = "session_A.jsonl") -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    with (audit_dir / name).open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def assert_statements_bounded(statements: list[str]) -> None:
    """Fail on any whole-store read among `statements`.

    Factored out so the checker itself can be tested against a known-bad
    statement -- a control that cannot fail is not a control (RULE 0B-C).
    """
    memory_statements = [s for s in statements if "memories" in s.lower()]
    assert memory_statements, "vacuity: no statement touched the store"
    for stmt in memory_statements:
        lowered = stmt.lower()
        assert "from memories" in lowered
        assert "where" in lowered, f"unbounded read of the whole store: {stmt}"
        assert "select *" not in lowered, f"whole-row select on the comparison path: {stmt}"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Run inside tmp so `src/.audit` from the repo cannot leak into the read."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_no_whole_store_read(isolated, monkeypatch):
    """The comparison path must never select every row of `memories`.

    Traces every statement the validator executes. A bare `SELECT * FROM
    memories` (or any statement over `memories` with no WHERE clause) fails
    here, which is precisely the shape that hung the suite.
    """
    store = isolated / "memory.db"
    _make_store(store, [("sess-A", "file_edit", "live")] * 3 + [("sess-B", "test_run", "live")])
    _write_audit(isolated / ".audit", [{"type": "file_edit", "session_file": "sess-A"}])

    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        con.set_trace_callback(statements.append)
        return con

    monkeypatch.setattr(governance.sqlite3, "connect", traced_connect)

    validator = governance.DualLedgerValidator(
        audit_dir=str(isolated / ".audit"), memory_db_path=str(store)
    )
    result = validator.cross_validate()

    assert_statements_bounded(statements)
    assert result["comparable"] is True, result


def test_the_boundedness_checker_rejects_a_whole_store_read():
    """Negative control for the checker above: it must reject the old shape."""
    with pytest.raises(AssertionError, match="unbounded read of the whole store"):
        assert_statements_bounded(["SELECT * FROM memories"])
    with pytest.raises(AssertionError, match="whole-row select"):
        assert_statements_bounded(["SELECT * FROM memories WHERE session_id = ?"])
    # and the shape the fix actually issues must pass
    assert_statements_bounded(
        ["SELECT session_id, event_type, COUNT(*) FROM memories "
         "WHERE source != 'audit_backfill' AND session_id IN (?,?) GROUP BY session_id, event_type"]
    )


def test_large_store_returns_within_budget(isolated):
    """50k rows must not cost more than the documented budget."""
    store = isolated / "big.db"
    rows = [(f"sess-{i % 500}", "file_edit", "live") for i in range(50_000)]
    _make_store(store, rows)
    _write_audit(isolated / ".audit", [{"type": "file_edit", "session_file": "nobody"}])

    validator = governance.DualLedgerValidator(
        audit_dir=str(isolated / ".audit"), memory_db_path=str(store)
    )
    started = time.monotonic()
    result = validator.cross_validate()
    elapsed = time.monotonic() - started

    assert elapsed < governance.DualLedgerValidator.MEMORY_BUDGET_S + 1.0, (
        f"cross_validate took {elapsed:.2f}s on a 50k-row store"
    )
    # No shared session id -> nothing was compared, and it says so.
    assert result["comparable"] is False
    assert result["consistent"] is True
    assert result["total_memory_events"] is None
    assert "Nothing was compared" in result["note"]


def test_detects_discrepancy_when_sessions_overlap(isolated):
    """The oracle must be able to fire: shared session, mismatched counts."""
    store = isolated / "memory.db"
    # audit records 1 file_edit, memory records 3 -> a real discrepancy
    _make_store(store, [("sess-A", "file_edit", "live")] * 3)
    _write_audit(isolated / ".audit", [{"type": "file_edit", "session_file": "sess-A"}])

    validator = governance.DualLedgerValidator(
        audit_dir=str(isolated / ".audit"), memory_db_path=str(store)
    )
    result = validator.cross_validate()

    assert result["comparable"] is True
    assert result["matching_sessions"] == 1
    assert result["consistent"] is False
    assert any(d["event_type"] == "file_edit" for d in result["discrepancies"])
    edit = next(d for d in result["discrepancies"] if d["event_type"] == "file_edit")
    assert edit["audit_count"] == 1 and edit["memory_count"] == 3


def test_consistent_when_counts_match(isolated):
    """Positive control: identical counts across a shared session is a pass."""
    store = isolated / "memory.db"
    _make_store(store, [("sess-A", "file_edit", "live"), ("sess-A", "test_run", "live")])
    _write_audit(
        isolated / ".audit",
        [{"type": "file_edit", "session_file": "sess-A"},
         {"type": "test_run", "session_file": "sess-A"}],
    )

    validator = governance.DualLedgerValidator(
        audit_dir=str(isolated / ".audit"), memory_db_path=str(store)
    )
    result = validator.cross_validate()

    assert result["comparable"] is True
    assert result["consistent"] is True
    assert result["discrepancies"] == []
    assert result["total_memory_events"] == 2
