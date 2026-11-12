"""Fuzz test: DirectMemory with random event types, data, and severities."""
import sys, os, random, string, json, sqlite3, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from CORE.direct_memory import DirectMemory
from pathlib import Path

_orig_db = None

def setup_module():
    global _orig_db
    import CORE.direct_memory as dm_mod
    _orig_db = str(dm_mod.DB_PATH)
    dm_mod.DB_PATH = Path(tempfile.mktemp(suffix='_test_fuzz.db'))

def teardown_module():
    import CORE.direct_memory as dm_mod
    if Path(str(dm_mod.DB_PATH)).exists():
        Path(str(dm_mod.DB_PATH)).unlink()
    # Restore UNCONDITIONALLY. The old `if _orig_db:` guard made the restore a
    # no-op whenever the saved value was falsy, which is how the same shape in
    # test_session_mappings leaked a deleted temp path into the whole process
    # (see tests/test_memory_db_module_teardown.py).
    dm_mod.DB_PATH = _orig_db

def random_string(max_len=20):
    return ''.join(random.choices(string.ascii_letters, k=random.randint(1, max_len)))

class TestFuzz:
    def test_random_event_types(self):
        dm = DirectMemory()
        severities = ["info", "warning", "error", "critical"]
        for _ in range(50):
            etype = random_string(30)
            sev = random.choice(severities)
            tags = [random_string(10) for _ in range(random.randint(0, 3))]
            try:
                dm.record(etype, {"fuzz": True, "value": random.randint(0, 1000)},
                         severity=sev, tags=tags)
            except ValueError:
                pass  # Some random types may fail validation
        count = dm.count()
        assert count >= 2  # At minimum session_start + anything
        dm.end({"ok": True, "events": count})

    def test_extreme_data_sizes(self):
        dm = DirectMemory()
        large_str = "x" * 10000
        dm.file_read([f"/fuzz/{large_str[:50]}.py"])
        dm.file_edit("/fuzz/large.py", large_str[:500], lines_changed=99999)
        dm.end({"ok": True})
        assert dm.count() >= 3
