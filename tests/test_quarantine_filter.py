#!/usr/bin/env python3
"""Quarantine filter safety pins (#988 audit).

filter_quarantined is a pure function: the audit producer appends
row.get("id") and the filter compares the same key, so same-source ids
always align in type. These tests pin the properties the audit relies on:
exact-id drops, unlisted rows kept, id-less rows kept (fail-open toward
visibility — documented here so a future strict-mode change is explicit),
and empty reports passing everything through.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_drops_listed_ids_only():
    from CORE.local_memory import LocalMemoryStore
    rows = [{"id": 1}, {"id": 2}, {"id": 3}]
    kept = LocalMemoryStore.filter_quarantined(
        rows, {"quarantined_ids": [2], "reasons": ["2:no-provenance"]})
    assert [r["id"] for r in kept] == [1, 3]


def test_empty_report_passes_all():
    from CORE.local_memory import LocalMemoryStore
    rows = [{"id": 1}, {"id": 2}]
    assert LocalMemoryStore.filter_quarantined(rows, {}) == rows
    assert LocalMemoryStore.filter_quarantined(
        rows, {"quarantined_ids": [], "reasons": []}) == rows


def test_id_less_rows_are_kept_fail_open():
    from CORE.local_memory import LocalMemoryStore
    rows = [{"noid": True}, {"id": 5}]
    kept = LocalMemoryStore.filter_quarantined(
        rows, {"quarantined_ids": [5], "reasons": []})
    assert kept == [{"noid": True}]
