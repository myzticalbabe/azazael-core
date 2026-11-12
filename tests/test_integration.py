"""Integration test: verify DM-AJ cross-reference pipeline end-to-end."""
import sys, os, json, sqlite3, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from CORE.direct_memory import DirectMemory
from pathlib import Path

_orig_db = None

def setup_module():
    global _orig_db
    import CORE.direct_memory as dm_mod
    _orig_db = str(dm_mod.DB_PATH)
    dm_mod.DB_PATH = Path(tempfile.mktemp(suffix='_test_int.db'))

def teardown_module():
    import CORE.direct_memory as dm_mod
    if Path(str(dm_mod.DB_PATH)).exists():
        Path(str(dm_mod.DB_PATH)).unlink()
    # Restore UNCONDITIONALLY. The old `if _orig_db:` guard made the restore a
    # no-op whenever the saved value was falsy, which is how the same shape in
    # test_session_mappings leaked a deleted temp path into the whole process
    # (see tests/test_memory_db_module_teardown.py).
    dm_mod.DB_PATH = _orig_db

class TestIntegrationPipeline:
    def test_cross_reference_lifecycle(self):
        dm = DirectMemory()
        dm.file_read(["/test/a.py"])
        dm.skill_loaded("int-test")
        sid = dm.session_id

        # Link a mock audit session
        result = dm.link_audit_session(".audit/int_session.jsonl",
                                       audit_session_id="int-uuid-123",
                                       mapping_type="manual",
                                       confidence=1.0)
        assert result is True

        # Query the cross-reference
        rows = DirectMemory.query_cross_reference(dm_session_id=sid)
        assert len(rows) >= 1
        r = rows[0]
        assert "audit_filepath" in r or "confidence" in r

        # Verify idempotent link
        result2 = dm.link_audit_session(".audit/int_session.jsonl",
                                        audit_session_id="int-uuid-123")
        assert result2 is False

        # Summary should include this mapping
        summary = DirectMemory.get_mapping_summary()
        assert summary["total_mappings"] >= 1

        dm.end({"ok": True})

    def test_backfill_roundtrip(self):
        dm = DirectMemory()
        dm.file_read(["/test/b.py"])
        dm.end({"ok": True})
        result = DirectMemory.cross_reference_ledgers()
        assert isinstance(result, dict)
        assert "new_links" in result
        assert "total_mappings" in result
