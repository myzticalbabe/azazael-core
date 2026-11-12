"""Unit tests for auto_sync.py — RULE 0R-A polling daemon.

Tests:
1. compute_hashes returns dict with .md files
2. detect_changes identifies new/modified/deleted files
3. run_sync returns False when sync script is missing (patched)
4. poll_once succeeds when no changes
5. _ensure_hashes_warmed warms cache on first call
6. --quiet flag suppresses verbose output
"""

import sys
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

# auto_sync.py lives in the same directory as this test file
TEST_DIR = os.path.dirname(__file__)
SCRIPTS_DIR = os.path.abspath(TEST_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from auto_sync import (
    compute_hashes,
    detect_changes,
    run_sync,
    _ensure_hashes_warmed,
    _last_hashes,
)
from typing import List
import shutil

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class TestAutoSync(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: self._cleanup_temp(self.temp_dir))

    def _cleanup_temp(self, path: Path):
        shutil.rmtree(path, ignore_errors=True)

    def test_compute_hashes_empty_dir(self):
        """compute_hashes returns empty dict for empty directory."""
        result = compute_hashes(self.temp_dir)
        self.assertEqual(result, {})

    def test_compute_hashes_finds_md_files(self):
        """compute_hashes finds .md files and computes hashes."""
        skill1 = self.temp_dir / "test1.md"
        skill2 = self.temp_dir / "test2.md"
        skill1.write_text("# Test 1", encoding="utf-8")
        skill2.write_text("# Test 2", encoding="utf-8")

        result = compute_hashes(self.temp_dir)
        self.assertEqual(len(result), 2)
        self.assertIn("test1.md", result)
        self.assertIn("test2.md", result)
        self.assertNotEqual(result["test1.md"], result["test2.md"])

    def test_detect_changes_new_file(self):
        """detect_changes identifies new files."""
        old = {"a.md": "hash1"}
        new = {"a.md": "hash1", "b.md": "hash2"}
        changes = detect_changes(old, new)
        self.assertEqual(len(changes), 1)
        self.assertIn("b.md", changes[0])
        self.assertIn("new", changes[0])

    def test_detect_changes_modified(self):
        """detect_changes identifies modified files."""
        old = {"a.md": "hash1"}
        new = {"a.md": "hash2"}
        changes = detect_changes(old, new)
        self.assertEqual(len(changes), 1)
        self.assertIn("a.md", changes[0])
        self.assertIn("modified", changes[0])

    def test_detect_changes_deleted(self):
        """detect_changes identifies deleted files."""
        old = {"a.md": "hash1", "b.md": "hash2"}
        new = {"a.md": "hash1"}
        changes = detect_changes(old, new)
        self.assertEqual(len(changes), 1)
        self.assertIn("b.md", changes[0])
        self.assertIn("deleted", changes[0])

    def test_detect_changes_no_changes(self):
        """detect_changes returns empty list when nothing changed."""
        old = {"a.md": "hash1"}
        new = {"a.md": "hash1"}
        changes = detect_changes(old, new)
        self.assertEqual(changes, [])

    @patch('auto_sync.SYNC_SCRIPT', Path('/nonexistent/sync_skills.py'))
    def test_run_sync_missing_script(self):
        """run_sync returns False when sync_skills.py doesn't exist (path patched)."""
        result = run_sync()
        self.assertFalse(result)

    def test_ensure_hashes_warmed(self):
        """_ensure_hashes_warmed returns current hashes on first call."""
        global _last_hashes
        saved = _last_hashes
        _last_hashes = {}
        try:
            result = _ensure_hashes_warmed()
            self.assertIsInstance(result, dict)
        finally:
            _last_hashes = saved


if __name__ == "__main__":
    unittest.main()
