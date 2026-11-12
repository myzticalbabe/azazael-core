"""
Unit tests for DirectMemory session_mappings table and AJ-compatible methods.

Tests:
- link_audit_session() creates a mapping row
- list_mappings() returns created mappings
- query_cross_reference() joins across DM+AJ
- All 6 new AJ-compatible methods produce correct event types
- get_mapping_summary() returns valid stats
"""

import sys, os, json, sqlite3, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE.direct_memory import DirectMemory
from pathlib import Path


def setup_module():
    """Use a temp DB for tests to avoid polluting the real database."""
    global _orig_db
    # DB_PATH is a MODULE-level variable that DirectMemory reads at
    # construction time -- it is not a class attribute, so
    # `DirectMemory.__dict__.get('DB_PATH', '')` is always '' and the old
    # teardown's `if _orig_db:` never restored anything. That leaked a
    # deleted temp path into every later test that uses the shared store
    # (they wrote to the temp DB via DirectMemory and read the real one via
    # get_memory_store), so the leak is fixed at the source: capture and
    # restore the module global unconditionally.
    import CORE.direct_memory as dm_mod
    _orig_db = dm_mod.DB_PATH
    dm_mod.DB_PATH = Path(tempfile.mktemp(suffix='_test.db'))


def teardown_module():
    import CORE.direct_memory as dm_mod
    if Path(str(dm_mod.DB_PATH)).exists():
        Path(str(dm_mod.DB_PATH)).unlink()
    dm_mod.DB_PATH = _orig_db


class TestSessionMappings:
    def test_link_audit_session(self):
        dm = DirectMemory()
        result = dm.link_audit_session(".audit/test_session.jsonl",
                                       audit_session_id="abc123",
                                       mapping_type="manual",
                                       confidence=1.0,
                                       notes="test")
        assert result is True, "First link should return True"
        # Idempotent: second link should return False
        result2 = dm.link_audit_session(".audit/test_session.jsonl",
                                        audit_session_id="abc123")
        assert result2 is False, "Duplicate link should return False"
        dm.end({"test": True})

    def test_list_mappings(self):
        dm = DirectMemory()
        dm.link_audit_session(".audit/session_a.jsonl", audit_session_id="aaa")
        dm.link_audit_session(".audit/session_b.jsonl", audit_session_id="bbb")
        mappings = dm.list_mappings()
        assert len(mappings) >= 2
        assert any(m.get("audit_filepath", "").endswith("session_a.jsonl")
                   for m in mappings)
        dm.end({"test": True})

    def test_query_cross_reference(self):
        dm = DirectMemory()
        dm.link_audit_session(".audit/xref.jsonl", audit_session_id="xref123")
        sid = dm.session_id
        results = DirectMemory.query_cross_reference(dm_session_id=sid)
        assert len(results) >= 1
        r = results[0]
        # query_cross_reference JOINs memories with session_mappings;
        # the column names come from SELECT aliases, not table columns.
        assert "audit_filepath" in r, f"Expected audit_filepath in keys: {list(r.keys())}"
        # dm_session_id comes from the JOIN: "s.dm_session_id" is aliased as... it's from session_mappings, mapped to m.session_id
        # The query uses "m.session_id" in the WHERE clause but selects "s.dm_session_id" via the JOIN
        # Actually looking at the query: it selects m.* plus s.audit_filepath, s.audit_session_id, s.confidence
        # So dm_session_id wouldn't be directly selected. Check what keys we do get:
        assert "confidence" in r, f"Expected confidence in keys: {list(r.keys())}"
        dm.end({"test": True})

    def test_get_mapping_summary(self):
        summary = DirectMemory.get_mapping_summary()
        assert "total_mappings" in summary
        assert "by_type" in summary
        assert "unique_dm_sessions" in summary
        assert "unique_aj_sessions" in summary


class TestAJCompatibleMethods:
    def _test_method(self, method_name, call_method):
        dm = DirectMemory()
        mid = call_method(dm)
        assert isinstance(mid, int) and mid > 0
        events = dm.list_events()
        types = [e["event_type"] for e in events]
        # Map expected event types
        expected = {
            "inference_spawn": "inference_spawn",
            "gate_passed": "gate_passed",
            "sentinel_run": "sentinel_run",
            "scan_anti_pattern": "scan_anti_pattern",
            "sync_skills": "sync_skills",
            "expect_action": "expect_action",
        }
        assert expected[method_name] in types, \
            f"{method_name} should record type {expected[method_name]}"
        dm.end({"test": True})

    def test_inference_spawn(self):
        self._test_method("inference_spawn",
                          lambda dm: dm.inference_spawn("test-agent", model="llama3.2"))

    def test_gate_passed(self):
        self._test_method("gate_passed",
                          lambda dm: dm.gate_passed(5, 0))

    def test_sentinel_run(self):
        self._test_method("sentinel_run",
                          lambda dm: dm.sentinel_run("PASS"))

    def test_scan_anti_pattern(self):
        self._test_method("scan_anti_pattern",
                          lambda dm: dm.scan_anti_pattern("test.py", "AST", 100, 0))

    def test_sync_skills(self):
        self._test_method("sync_skills",
                          lambda dm: dm.sync_skills(10, 2, 0))

    def test_expect_action(self):
        self._test_method("expect_action",
                          lambda dm: dm.expect_action("test_run", "expected test"))


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
