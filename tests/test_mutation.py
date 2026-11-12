"""Mutation testing: verify existing tests catch seeded code bugs."""
import sys, os, json, sqlite3, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from CORE.direct_memory import DirectMemory
from pathlib import Path

_orig_db = None

def setup_module():
    global _orig_db
    import CORE.direct_memory as dm_mod
    _orig_db = str(dm_mod.DB_PATH)
    dm_mod.DB_PATH = Path(tempfile.mktemp(suffix='_test_mut.db'))

def teardown_module():
    import CORE.direct_memory as dm_mod
    if Path(str(dm_mod.DB_PATH)).exists():
        Path(str(dm_mod.DB_PATH)).unlink()
    # Restore UNCONDITIONALLY. The old `if _orig_db:` guard made the restore a
    # no-op whenever the saved value was falsy, which is how the same shape in
    # test_session_mappings leaked a deleted temp path into the whole process
    # (see tests/test_memory_db_module_teardown.py).
    dm_mod.DB_PATH = _orig_db

class TestMutationDetection:
    def test_link_audit_session_idempotent(self):
        dm = DirectMemory()
        r1 = dm.link_audit_session(".audit/mut.jsonl", audit_session_id="mut1")
        assert r1 is True, "First link should succeed"
        r2 = dm.link_audit_session(".audit/mut.jsonl", audit_session_id="mut1")
        assert r2 is False, "Duplicate link should be idempotent (returns False)"
        dm.end({"ok": True})

    def test_empty_audit_filepath_rejected(self):
        dm = DirectMemory()
        result = dm.link_audit_session("", audit_session_id="test")
        assert result is False, "Empty filepath should return False"
        dm.end({"ok": True})

    def test_mapping_summary_structure(self):
        dm = DirectMemory()
        dm.link_audit_session(".audit/s.jsonl", audit_session_id="s1")
        summary = DirectMemory.get_mapping_summary()
        assert "total_mappings" in summary
        assert "unique_dm_sessions" in summary
        assert "unique_aj_sessions" in summary
        dm.end({"ok": True})
