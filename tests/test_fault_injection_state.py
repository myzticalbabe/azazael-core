# coding: utf-8
"""PHASE H — fault injection.

Engram #784, phase H. Every drill here damages a TEMP COPY, a temp database or a
monkeypatched object. Nothing in this file writes to live state, and the store
drills point at a fresh database under ``tmp_path``.

The rule under test is the tree's own: a fail-open path must OPEN. A path that
raises on damage is acceptable only where a caller is contracted to catch it, so
each drill asserts the *contract that actually exists* -- verified by running the
probe first, not by assuming the friendlier behaviour. Two probes run against the
real store found:

  * a corrupt database file raises ``sqlite3.DatabaseError`` from the
    CONSTRUCTOR and records no note;
  * a read-only database file raises ``sqlite3.OperationalError`` from
    ``record_event`` and records no note.

So ``LocalMemoryStore`` is a RAISING api, and the fail-open boundary is its
caller. That is what ``test_record_link_fails_open`` exercises: the technique
runtime must absorb the store's exception, answer False and record the reason.
Treating the store itself as fail-open would have been an assumption about code
nobody had run.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import merge_bake_lane  # noqa: E402
import CORE.technique_runtime as runtime_mod  # noqa: E402
from CORE.failure_ledger import failure_notes, reset_notes  # noqa: E402
from CORE.local_memory import LocalMemoryStore  # noqa: E402

VALID_ROW = {
    "source": "fault-probe",
    "item_id": "probe-1",
    "title": "A probe row that satisfies every field the gate requires",
    "body": "x" * 80,
    "kind": "lesson",
}


def _write_lane(tmp_path, rows, name="lane.jsonl"):
    lane = tmp_path / name
    lane.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return lane


def _normalised_titles(rows):
    return {merge_bake_lane.norm_title(row.get("title", "")) for row in rows}


# ── the merge gate: damage is rejected, never partially accepted ────────────

def test_a_lane_file_with_one_corrupt_line_contributes_zero_rows(tmp_path):
    """A partly-readable lane file must contribute NOTHING, not its good half.

    This is the fail-SAFE direction and it is deliberate: the loader parses the
    whole file before validating any row, so a truncated write cannot smuggle an
    arbitrary prefix of accepted rows into the bake corpus. The assertion that
    the valid row is absent is the load-bearing half -- checking only that
    BAD_JSON appears would pass on an implementation that kept the good rows.
    """
    lane = tmp_path / "corrupt.jsonl"
    lane.write_text(json.dumps(VALID_ROW) + "\n" + "{ this is not json\n", encoding="utf-8")

    merged, dropped, accepted, _base = merge_bake_lane.merge_lanes([lane])

    assert ("BAD_JSON") in {reason for _where, reason, _detail in dropped}
    assert accepted == 0
    assert merge_bake_lane.norm_title(VALID_ROW["title"]) not in _normalised_titles(merged)


def test_an_unreadable_lane_path_is_reported_rather_than_raising(tmp_path):
    """A missing lane file must be a reported drop, not an exception: the merge
    gate runs in a batch where one absent lane must not abort the others."""
    missing = tmp_path / "does-not-exist.jsonl"

    merged, dropped, accepted, _base = merge_bake_lane.merge_lanes([missing])

    assert "UNREADABLE" in {reason for _where, reason, _detail in dropped}
    assert accepted == 0
    assert isinstance(merged, list)


def test_every_row_level_violation_is_dropped_with_its_own_reason(tmp_path):
    """Each schema violation must be named, and none of them may reach the merge.

    An over-broad validator that answered "invalid" for all four would pass a
    count-only check, so the reasons are compared as a set.
    """
    duplicate = dict(VALID_ROW, item_id="probe-dup")
    rows = [
        VALID_ROW,
        {"source": "fault-probe", "item_id": "no-kind", "title": "A row missing the kind field",
         "body": "y" * 80},
        {"source": "fault-probe", "item_id": "bad-kind", "title": "A row with an unknown kind value",
         "body": "y" * 80, "kind": "not_a_real_kind"},
        {"source": "fault-probe", "item_id": "short", "title": "A row whose body is too short",
         "body": "tiny", "kind": "lesson"},
        # Same title as VALID_ROW up to case and whitespace, so the duplicate
        # check is exercised through the normaliser rather than on a byte match.
        dict(VALID_ROW, item_id="probe-2", title="  " + VALID_ROW["title"].upper() + " "),
        duplicate,
    ]
    lane = _write_lane(tmp_path, rows, name="violations.jsonl")

    merged, dropped, accepted, _base = merge_bake_lane.merge_lanes([lane])
    reasons = {reason for _where, reason, _detail in dropped}

    assert accepted == 1, "exactly one row here is admissible"
    assert {"BAD_SCHEMA", "BAD_KIND", "too-short", "dup"} <= reasons
    titles = _normalised_titles(merged)
    assert merge_bake_lane.norm_title(VALID_ROW["title"]) in titles
    assert merge_bake_lane.norm_title("A row missing the kind field") not in titles
    assert merge_bake_lane.norm_title("A row with an unknown kind value") not in titles
    assert merge_bake_lane.norm_title("A row whose body is too short") not in titles


# ── the fail-open boundary that really exists ──────────────────────────────

def test_record_link_fails_open_when_the_store_cannot_write(monkeypatch):
    """The technique runtime must absorb a broken store, not propagate it.

    ``record_link`` is documented "Fail-open: never raises; False only if the
    store is unwritable." A store whose write raises is exactly that condition,
    so the contract is: return False AND leave the reason in the ledger. Both
    halves are asserted, because returning False silently would be the same
    defect the ledger module exists to prevent.
    """
    reset_notes()

    class _BrokenStore:
        def record_event(self, *args, **kwargs):
            raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(runtime_mod, "get_memory_store", lambda: _BrokenStore())

    kept = runtime_mod.record_link("fault-probe-hook", "subject-1", ok=True, citation="c1")

    assert kept is False, "an unwritable store must yield False rather than raise"
    assert any(key.startswith("technique-runtime:record-link")
               for key in failure_notes()), "the absorbed failure must be recorded, not swallowed"


def test_a_readonly_store_file_raises_and_that_is_why_the_caller_guards(tmp_path):
    """Characterisation, not approval: the store's write path RAISES.

    Pinned because it defines where the fail-open boundary is. If this ever
    becomes a non-raising call, the change is deliberate and visible here -- and
    ``record_link``'s guard would then need re-reading rather than re-trusting.
    """
    store = LocalMemoryStore(db_path=str(tmp_path / "readonly.db"))
    store.record_event("fault-probe", "warmup", {"a": 1})   # create the file

    os.chmod(store.db_path, 0o444)
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.record_event("fault-probe", "readonly_write", {"a": 2})
    finally:
        os.chmod(store.db_path, 0o644)


def test_a_corrupt_database_file_is_rejected_at_construction_not_half_read(tmp_path):
    """A garbage file masquerading as a store must fail at construction.

    The failing-fast behaviour is the useful half: a store that opened on a
    corrupt file and returned empty reads would be indistinguishable from an
    empty memory, which is the "absence of evidence read as evidence" failure
    this tree's own audits keep finding.
    """
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"this is not a sqlite database\n" * 64)

    with pytest.raises(sqlite3.DatabaseError):
        LocalMemoryStore(db_path=str(db))


# -- the base set: drift must be loud, not silently untitled -----------------

def test_a_drifted_base_harvest_row_is_excluded_and_reported(tmp_path, monkeypatch):
    """A base-set file is not lane-validated, so a shape drift must be loud.

    The 2026-10-16 web9/web10 harvest files arrived as {angle, arxiv, note,
    technique, verdict}. Because ``base_rows()`` fed base files straight into
    bake_merged.jsonl, every ``title`` lookup on those 19 rows returned "" --
    the defect this guard exists for. The guard must exclude such a row AND
    name it in the report; dropping it quietly would reproduce the very
    silence being fixed, so both halves are asserted.
    """
    state = tmp_path / "state"
    state.mkdir()
    (state / "bake_corpus.jsonl").write_text(
        json.dumps(VALID_ROW) + "\n", encoding="utf-8")
    (state / "bake_harvest_drift.jsonl").write_text(
        json.dumps({"angle": "a", "arxiv": "1", "note": "n",
                    "technique": "t", "verdict": "CONCEPT"}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(merge_bake_lane, "ROOT", tmp_path)

    report = []
    rows = merge_bake_lane.base_rows(report)

    assert len(rows) == 1, "only the shape-correct base row may be admitted"
    assert [reason for _where, reason, _detail in report] == ["base-no-title"]
    assert [where for where, _reason, _detail in report] == [
        "bake_harvest_drift.jsonl:1"]
