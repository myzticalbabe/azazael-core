# coding: utf-8
"""
Tests for sync_g_drive.py — --force flag logic and chunked sync.

Uses mocks to avoid requiring G: drive or robocopy. Tests focus on:
    - --force flag clears/does not clear checkpoint as expected
    - clear_checkpoint / load_checkpoint / save_checkpoint edge cases
    - sync_large_directory_chunked resume and file-at-root handling
    - is_chunk_complete behavior with various checkpoint states
    - format_size formatting edge cases
"""

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
# The script imports from pathlib, json, etc. — all stdlib, no G: drive needed


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dir():
    """Create a temporary directory for test isolation."""
    d = tempfile.mkdtemp(prefix="test_g_sync_")
    # Hermeticity: main() writes a ".last_sync" timestamp under
    # G_SOURCE_OF_TRUTH and clears the checkpoint afterwards. Redirect it here,
    # or a unit test mutates the real G: mirror and fails with Errno 22 when
    # that file cannot be overwritten -- a failure about the host, not the code.
    with patch("sync_g_drive.G_SOURCE_OF_TRUTH", Path(d)):
        yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def mock_checkpoint_file(tmp_dir):
    """Patch CHECKPOINT_FILE to point to a temp path."""
    cp = tmp_dir / "checkpoint.json"
    with patch("sync_g_drive.CHECKPOINT_FILE", cp):
        yield cp


# ── Checkpoint Tests ───────────────────────────────────────────────


class TestCheckpoint:
    """Test load/save/clear/check checkpoint operations."""

    def test_load_checkpoint_missing(self, mock_checkpoint_file):
        """Loading a nonexistent checkpoint should return empty dict."""
        from sync_g_drive import load_checkpoint

        cp = load_checkpoint()
        assert cp == {}

    def test_save_and_load_checkpoint(self, mock_checkpoint_file):
        """Saving then loading should return identical data."""
        from sync_g_drive import save_checkpoint, load_checkpoint

        data = {"src/dir1": {"completed_at": "2026-06-11T12:00:00", "rc": 1}}
        save_checkpoint(data)

        loaded = load_checkpoint()
        assert loaded == data

    def test_save_checkpoint_creates_file(self, mock_checkpoint_file):
        """save_checkpoint should create the checkpoint file."""
        from sync_g_drive import save_checkpoint

        assert not mock_checkpoint_file.exists()
        save_checkpoint({"key": "val"})
        assert mock_checkpoint_file.exists()
        content = json.loads(mock_checkpoint_file.read_text(encoding="utf-8"))
        assert content == {"key": "val"}

    def test_save_checkpoint_with_nonexistent_parent(self, tmp_dir):
        """save_checkpoint should create parent directories."""
        nested = tmp_dir / "deep" / "nested" / "cp.json"
        with patch("sync_g_drive.CHECKPOINT_FILE", nested):
            from sync_g_drive import save_checkpoint

            save_checkpoint({"key": "val"})
            assert nested.exists()

    def test_load_checkpoint_corrupted(self, mock_checkpoint_file):
        """Loading a corrupted checkpoint should return empty dict."""
        mock_checkpoint_file.write_text("{invalid json", encoding="utf-8")
        from sync_g_drive import load_checkpoint

        cp = load_checkpoint()
        assert cp == {}

    def test_load_checkpoint_os_error(self, mock_checkpoint_file):
        """Loading with read error should return empty dict."""
        from sync_g_drive import load_checkpoint

        # Patch read_text to raise OSError
        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            cp = load_checkpoint()
            assert cp == {}

    def test_save_checkpoint_os_error(self, mock_checkpoint_file):
        """Save with write error should not raise."""
        from sync_g_drive import save_checkpoint

        with patch.object(Path, "write_text", side_effect=OSError("Disk full")):
            # Should not raise
            save_checkpoint({"key": "val"})

    def test_clear_checkpoint_existing(self, mock_checkpoint_file):
        """clear_checkpoint should delete an existing checkpoint file."""
        from sync_g_drive import save_checkpoint, clear_checkpoint

        save_checkpoint({"key": "val"})
        assert mock_checkpoint_file.exists()

        clear_checkpoint()
        assert not mock_checkpoint_file.exists()

    def test_clear_checkpoint_nonexistent(self, mock_checkpoint_file):
        """clear_checkpoint should not raise if no checkpoint exists."""
        from sync_g_drive import clear_checkpoint

        assert not mock_checkpoint_file.exists()
        # Should not raise
        clear_checkpoint()

    def test_mark_chunk_complete_new(self, mock_checkpoint_file):
        """mark_chunk_complete should add a new entry to the checkpoint."""
        from sync_g_drive import mark_chunk_complete

        mark_chunk_complete("src/dir1", {"rc": 0})
        cp = json.loads(mock_checkpoint_file.read_text(encoding="utf-8"))
        assert "src/dir1" in cp
        assert cp["src/dir1"]["rc"] == 0
        assert "completed_at" in cp["src/dir1"]

    def test_mark_chunk_complete_appends(self, mock_checkpoint_file):
        """mark_chunk_complete should preserve existing entries."""
        from sync_g_drive import mark_chunk_complete

        mark_chunk_complete("src/dir1", {"rc": 0})
        mark_chunk_complete("src/dir2", {"rc": 1})
        cp = json.loads(mock_checkpoint_file.read_text(encoding="utf-8"))
        assert "src/dir1" in cp
        assert "src/dir2" in cp

    def test_is_chunk_complete_true(self, mock_checkpoint_file):
        """is_chunk_complete should return True for completed chunks."""
        from sync_g_drive import mark_chunk_complete, is_chunk_complete

        mark_chunk_complete("src/dir1", {"rc": 0})
        assert is_chunk_complete("src/dir1") is True

    def test_is_chunk_complete_false(self, mock_checkpoint_file):
        """is_chunk_complete should return False for uncompleted chunks."""
        from sync_g_drive import is_chunk_complete

        assert is_chunk_complete("src/nonexistent") is False

    def test_is_chunk_complete_dry_run(self, mock_checkpoint_file):
        """is_chunk_complete with dry_run=True should skip checkpoint check."""
        from sync_g_drive import mark_chunk_complete, is_chunk_complete

        mark_chunk_complete("src/dir1", {"rc": 0})
        # is_chunk_complete doesn't take a dry_run param — the caller checks
        # `if not dry_run and is_chunk_complete(chunk_key):` in sync_large_directory_chunked
        assert is_chunk_complete("src/dir1") is True

    # ── Non-dict JSON Edge Cases (Edge Case 4) ────────────────────────

    def test_load_checkpoint_list_returns_empty(self, mock_checkpoint_file):
        """load_checkpoint should return {} for JSON array (non-dict)."""
        mock_checkpoint_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        from sync_g_drive import load_checkpoint

        cp = load_checkpoint()
        assert cp == {}

    def test_load_checkpoint_string_returns_empty(self, mock_checkpoint_file):
        """load_checkpoint should return {} for JSON string (non-dict)."""
        mock_checkpoint_file.write_text(json.dumps("just a string"), encoding="utf-8")
        from sync_g_drive import load_checkpoint

        cp = load_checkpoint()
        assert cp == {}

    def test_load_checkpoint_number_returns_empty(self, mock_checkpoint_file):
        """load_checkpoint should return {} for JSON number (non-dict)."""
        mock_checkpoint_file.write_text(json.dumps(42), encoding="utf-8")
        from sync_g_drive import load_checkpoint

        cp = load_checkpoint()
        assert cp == {}

    def test_load_checkpoint_null_returns_empty(self, mock_checkpoint_file):
        """load_checkpoint should return {} for JSON null (non-dict)."""
        mock_checkpoint_file.write_text(json.dumps(None), encoding="utf-8")
        from sync_g_drive import load_checkpoint

        cp = load_checkpoint()
        assert cp == {}

    def test_load_checkpoint_boolean_returns_empty(self, mock_checkpoint_file):
        """load_checkpoint should return {} for JSON bool (non-dict)."""
        mock_checkpoint_file.write_text("true", encoding="utf-8")
        from sync_g_drive import load_checkpoint

        cp = load_checkpoint()
        assert cp == {}

    # ── Corrupted checkpoint edge cases for save/mark ──────────────────

    def test_mark_chunk_complete_with_list_checkpoint(self, mock_checkpoint_file):
        """mark_chunk_complete should handle when checkpoint contains a list (non-dict)."""
        # Write a list to the checkpoint (simulating corruption from a previous bug)
        mock_checkpoint_file.write_text(json.dumps(["bad", "data"]), encoding="utf-8")

        from sync_g_drive import mark_chunk_complete, is_chunk_complete

        # mark_chunk_complete calls load_checkpoint() which returns {}
        # for non-dict JSON, then saves a dict with the new entry
        mark_chunk_complete("src/dir1", {"rc": 0})

        # After mark, the file should contain the proper dict, not the old list
        cp = json.loads(mock_checkpoint_file.read_text(encoding="utf-8"))
        assert isinstance(cp, dict)
        assert "src/dir1" in cp
        assert cp["src/dir1"]["rc"] == 0
        assert "completed_at" in cp["src/dir1"]
        # is_chunk_complete should now work correctly
        assert is_chunk_complete("src/dir1") is True

    def test_mark_chunk_complete_with_string_checkpoint(self, mock_checkpoint_file):
        """mark_chunk_complete should handle when checkpoint contains a string."""
        mock_checkpoint_file.write_text(json.dumps("corrupted"), encoding="utf-8")

        from sync_g_drive import mark_chunk_complete, is_chunk_complete

        mark_chunk_complete("src/dir1", {"rc": 0})
        cp = json.loads(mock_checkpoint_file.read_text(encoding="utf-8"))
        assert isinstance(cp, dict)
        assert "src/dir1" in cp
        assert is_chunk_complete("src/dir1") is True

    def test_save_checkpoint_overwrites_list(self, mock_checkpoint_file):
        """save_checkpoint should save a proper dict even if file previously held a list."""
        mock_checkpoint_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        from sync_g_drive import save_checkpoint

        save_checkpoint({"proper": "dict"})
        cp = json.loads(mock_checkpoint_file.read_text(encoding="utf-8"))
        assert cp == {"proper": "dict"}

    def test_is_chunk_complete_with_list_checkpoint(self, mock_checkpoint_file):
        """is_chunk_complete should return False when checkpoint contains a list."""
        mock_checkpoint_file.write_text(json.dumps(["src/dir1"]), encoding="utf-8")

        from sync_g_drive import is_chunk_complete

        # load_checkpoint returns {} for non-dict, so no key is found
        assert is_chunk_complete("src/dir1") is False


# ── --force flag logic tests ───────────────────────────────────────

class TestForceFlag:
    """Test the --force flag interaction with clear_checkpoint."""

    @staticmethod
    def _mock_sync_dir(rel_dir, dry_run=False):
        """Match sync_directory's real return shape."""
        return {"dir": rel_dir, "status": "success", "rc": 1}

    @staticmethod
    def _mock_chunked_empty(rel_dir, dry_run=False):
        """Return empty chunk results."""
        return []

    @staticmethod
    def _mock_files_with_failure(dry_run=False, use_checkpoint=True):
        """Return a file result with failure so end-of-sync clear_checkpoint doesn't fire.

        sync_g_drive.main() calls clear_checkpoint() at end-of-sync only
        when failed == 0, where `failed` counts failures from sync_files()
        (NOT from the chunked sync). By including a failure here, we
        isolate the --force explicit clear_checkpoint() call from the
        end-of-sync one.
        """
        return [{"file": "_mock", "status": "failed", "reason": "Mock failure"}]

    def test_force_with_sync_clears_checkpoint(self, tmp_dir):
        """--force --sync should call clear_checkpoint from the force block."""
        from sync_g_drive import main

        # Patch Path.home() to prevent memory DB sync from accessing real G: drive
        mock_home = Path(tmp_dir) / "mock_home"

        with patch("sync_g_drive.clear_checkpoint") as mock_clear:
            with patch("sync_g_drive.sync_directory", side_effect=self._mock_sync_dir):
                with patch("sync_g_drive.sync_large_directory_chunked", side_effect=self._mock_chunked_empty):
                    with patch("sync_g_drive.sync_files", side_effect=self._mock_files_with_failure):
                        with patch("sys.argv", ["sync_g_drive.py", "--force", "--sync"]):
                            with patch("builtins.print"):
                                # Patch Path.home to prevent G: drive access in memory DB sync block
                                with patch.object(Path, "home", return_value=mock_home):
                                    main()

        # Called once from --force block; end-of-sync blocked by files failure
        mock_clear.assert_called_once()

    def test_force_without_sync_does_not_clear(self, tmp_dir):
        """--force without --sync should NOT call clear_checkpoint (no sync code runs)."""
        from sync_g_drive import main

        with patch("sync_g_drive.clear_checkpoint") as mock_clear:
            with patch("sys.argv", ["sync_g_drive.py", "--force"]):
                with patch("builtins.print"):
                    main()

        mock_clear.assert_not_called()

    def test_sync_without_force_does_not_clear(self, tmp_dir):
        """--sync without --force should NOT call clear_checkpoint (no --force, files fail)."""
        from sync_g_drive import main
        mock_home = Path(tmp_dir) / "mock_home"

        with patch("sync_g_drive.clear_checkpoint") as mock_clear:
            with patch("sync_g_drive.sync_directory", side_effect=self._mock_sync_dir):
                with patch("sync_g_drive.sync_large_directory_chunked", side_effect=self._mock_chunked_empty):
                    with patch("sync_g_drive.sync_files", side_effect=self._mock_files_with_failure):
                        with patch("sys.argv", ["sync_g_drive.py", "--sync"]):
                            with patch("builtins.print"):
                                with patch.object(Path, "home", return_value=mock_home):
                                    main()

        # No --force block fires, end-of-sync blocked by files failure
        mock_clear.assert_not_called()

    def test_force_dry_run_clears_checkpoint(self, tmp_dir):
        """--force --sync --dry-run: force + dry-run both call clear_checkpoint."""
        from sync_g_drive import main
        mock_home = Path(tmp_dir) / "mock_home"

        with patch("sync_g_drive.clear_checkpoint") as mock_clear:
            with patch("sync_g_drive.sync_directory", side_effect=self._mock_sync_dir):
                with patch("sync_g_drive.sync_large_directory_chunked", side_effect=self._mock_chunked_empty):
                    with patch("sync_g_drive.sync_files", side_effect=self._mock_files_with_failure):
                        with patch("sys.argv", ["sync_g_drive.py", "--force", "--sync", "--dry-run"]):
                            with patch("builtins.print"):
                                with patch.object(Path, "home", return_value=mock_home):
                                    main()

        # Called by both --force block AND dry-run block
        assert mock_clear.call_count == 2, (
            f"Expected 2 calls (--force + --dry-run), got {mock_clear.call_count}"
        )

    def test_force_flag_order_independent(self, tmp_dir):
        """--sync --force (reversed order) should still call clear_checkpoint."""
        from sync_g_drive import main
        mock_home = Path(tmp_dir) / "mock_home"

        with patch("sync_g_drive.clear_checkpoint") as mock_clear:
            with patch("sync_g_drive.sync_directory", side_effect=self._mock_sync_dir):
                with patch("sync_g_drive.sync_large_directory_chunked", side_effect=self._mock_chunked_empty):
                    with patch("sync_g_drive.sync_files", side_effect=self._mock_files_with_failure):
                        with patch("sys.argv", ["sync_g_drive.py", "--sync", "--force"]):
                            with patch("builtins.print"):
                                with patch.object(Path, "home", return_value=mock_home):
                                    main()

        # Called once from --force block; end-of-sync blocked by files failure
        mock_clear.assert_called_once()


# ── sync_large_directory_chunked tests ─────────────────────────────

class TestSyncLargeDirectoryChunked:
    """Test chunked sync logic with mocked robocopy."""

    def test_chunked_skips_dot_dirs(self, tmp_dir):
        """Chunked sync should skip . and __pycache__ entries."""
        src = tmp_dir / "src"
        src.mkdir()
        (src / ".hidden").mkdir()
        (src / "__pycache__").mkdir()
        (src / "visible").mkdir()
        (src / "visible" / "f.txt").write_text("hello", encoding="utf-8")

        from sync_g_drive import sync_large_directory_chunked

        with patch("sync_g_drive.SOURCE_ROOT", tmp_dir):
            with patch("sync_g_drive.G_SOURCE_OF_TRUTH", tmp_dir / "g_mirror"):
                with patch("sync_g_drive.robocopy", return_value=(1, "Files copied")) as mock_robo:
                    with patch("sync_g_drive.mark_chunk_complete"):
                        with patch("sync_g_drive.is_chunk_complete", return_value=False):
                            with patch("builtins.print"):
                                results = sync_large_directory_chunked("src", dry_run=False)

        # Should have synced only 'visible', not .hidden or __pycache__
        synced = [r for r in results if r["status"] == "success"]
        assert len(synced) == 1
        assert "visible" in synced[0]["dir"]

    def test_chunked_handles_files_at_root(self, tmp_dir):
        """Chunked sync should handle files (not just dirs) at root level.

        This is the regression test for the mkdir-on-file bug.
        """
        src = tmp_dir / "src2"
        src.mkdir()
        (src / "readme.txt").write_text("root file", encoding="utf-8")  # File at root
        sub = src / "subdir"
        sub.mkdir()
        (sub / "data.txt").write_text("nested", encoding="utf-8")

        from sync_g_drive import sync_large_directory_chunked

        with patch("sync_g_drive.SOURCE_ROOT", tmp_dir):
            with patch("sync_g_drive.G_SOURCE_OF_TRUTH", tmp_dir / "g_mirror2"):
                with patch("sync_g_drive.robocopy", return_value=(1, "Files copied")) as mock_robo:
                    with patch("sync_g_drive.mark_chunk_complete"):
                        with patch("sync_g_drive.is_chunk_complete", return_value=False):
                            with patch("builtins.print"):
                                results = sync_large_directory_chunked("src2", dry_run=False)

        # Both the file and the directory should sync successfully
        synced = [r for r in results if r["status"] == "success"]
        assert len(synced) == 2, f"Expected 2 synced, got {len(synced)}: {results}"
        dirs_found = [r["dir"] for r in synced]
        assert any("readme.txt" in d for d in dirs_found)
        assert any("subdir" in d for d in dirs_found)
        # robocopy should only be called for the directory chunk, not the file
        assert mock_robo.call_count == 1

    def test_chunked_resume_skips_completed(self, tmp_dir):
        """Chunked sync should skip chunks marked complete in checkpoint."""
        src = tmp_dir / "src3"
        src.mkdir()
        (src / "done_dir").mkdir()
        (src / "done_dir" / "f.txt").write_text("done", encoding="utf-8")
        (src / "pending_dir").mkdir()
        (src / "pending_dir" / "g.txt").write_text("pending", encoding="utf-8")

        from sync_g_drive import sync_large_directory_chunked

        # Mark done_dir as completed
        with patch("sync_g_drive.SOURCE_ROOT", tmp_dir):
            with patch("sync_g_drive.G_SOURCE_OF_TRUTH", tmp_dir / "g_mirror3"):
                with patch("sync_g_drive.is_chunk_complete", side_effect=lambda k: "done_dir" in k):
                    with patch("sync_g_drive.robocopy", return_value=(1, "Files copied")) as mock_robo:
                        with patch("sync_g_drive.mark_chunk_complete"):
                            with patch("builtins.print"):
                                results = sync_large_directory_chunked("src3", dry_run=False)

        # Should have 2 results: 1 resumed, 1 synced
        resumed = [r for r in results if r["status"] == "resumed"]
        synced = [r for r in results if r["status"] == "success"]
        assert len(resumed) == 1, f"Expected 1 resumed, got {len(resumed)}: {results}"
        assert len(synced) == 1, f"Expected 1 synced, got {len(synced)}: {results}"
        assert "done_dir" in resumed[0]["dir"]
        assert "pending_dir" in synced[0]["dir"]
        # robocopy should only be called for pending_dir
        assert mock_robo.call_count == 1

    def test_chunked_dry_run_does_not_check_resume(self, tmp_dir):
        """Dry run should skip resume checkpoint check."""
        src = tmp_dir / "src4"
        src.mkdir()
        (src / "adir").mkdir()
        (src / "adir" / "f.txt").write_text("a", encoding="utf-8")

        from sync_g_drive import sync_large_directory_chunked

        with patch("sync_g_drive.SOURCE_ROOT", tmp_dir):
            with patch("sync_g_drive.G_SOURCE_OF_TRUTH", tmp_dir / "g_mirror4"):
                with patch("sync_g_drive.robocopy", return_value=(1, "[DRY RUN] Would copy")) as mock_robo:
                    with patch("sync_g_drive.is_chunk_complete") as mock_is_complete:
                        with patch("builtins.print"):
                            results = sync_large_directory_chunked("src4", dry_run=True)

        # In dry run, is_chunk_complete should NOT be checked
        # (the guard is: if not dry_run and is_chunk_complete(chunk_key):)
        mock_is_complete.assert_not_called()  # Should never be called
        assert len(results) == 1


# ── --force-test flag tests ───────────────────────────────────────

class TestForceTestFlag:
    """Test the --force-test mode (clear checkpoint + local-only status, no G: drive)."""

    def test_force_test_clears_checkpoint(self, tmp_dir):
        """--force-test should call clear_checkpoint."""
        from sync_g_drive import main

        with patch("sync_g_drive.clear_checkpoint") as mock_clear:
            with patch("sync_g_drive.get_file_count", return_value=42):
                with patch("sync_g_drive.get_dir_size", return_value=1000):
                    with patch("sys.argv", ["sync_g_drive.py", "--force-test"]):
                        with patch("builtins.print"):
                            main()

        mock_clear.assert_called_once()

    def test_force_test_does_not_call_sync(self, tmp_dir):
        """--force-test should NOT call sync_directory, sync_large_directory_chunked, or sync_files."""
        from sync_g_drive import main

        with patch("sync_g_drive.clear_checkpoint"):
            with patch("sync_g_drive.get_file_count", return_value=42):
                with patch("sync_g_drive.get_dir_size", return_value=1000):
                    with patch("sync_g_drive.sync_directory") as mock_sync_dir:
                        with patch("sync_g_drive.sync_large_directory_chunked") as mock_sync_chunk:
                            with patch("sync_g_drive.sync_files") as mock_sync_files:
                                with patch("sys.argv", ["sync_g_drive.py", "--force-test"]):
                                    with patch("builtins.print"):
                                        main()

        mock_sync_dir.assert_not_called()
        mock_sync_chunk.assert_not_called()
        mock_sync_files.assert_not_called()

    def test_force_test_shows_local_status(self, tmp_dir):
        """--force-test should show local file count and size without G: drive access."""
        from sync_g_drive import main

        printed = []

        def capture_print(*args, **kwargs):
            printed.extend(str(a) for a in args)

        with patch("sync_g_drive.clear_checkpoint"):
            with patch("sync_g_drive.get_file_count", return_value=42):
                with patch("sync_g_drive.get_dir_size", return_value=5000):
                    with patch("sys.argv", ["sync_g_drive.py", "--force-test"]):
                        with patch("builtins.print", side_effect=capture_print):
                            main()

        output = " ".join(printed)
        assert "42" in output, f"Expected local file count in output: {output}"
        assert "NOT ACCESSED" in output, f"Expected 'NOT ACCESSED' for G: drive: {output}"
        assert "test mode" in output.lower(), f"Expected 'test mode' indicator: {output}"

    def test_force_test_without_sync_returns_early(self):
        """--force-test should return after showing status (no sync block runs)."""
        from sync_g_drive import main

        with patch("sync_g_drive.clear_checkpoint"):
            with patch("sync_g_drive.get_file_count", return_value=0):
                with patch("sync_g_drive.get_dir_size", return_value=0):
                    with patch("sys.argv", ["sync_g_drive.py", "--force-test"]):
                        with patch("builtins.print"):
                            # Should not raise or attempt G: drive access
                            main()

    def test_force_test_standalone_no_sync_required(self):
        """--force-test should work standalone (no --sync needed)."""
        from sync_g_drive import main

        with patch("sync_g_drive.clear_checkpoint"):
            with patch("sync_g_drive.get_file_count", return_value=0):
                with patch("sync_g_drive.get_dir_size", return_value=0):
                    with patch("sys.argv", ["sync_g_drive.py", "--force-test"]):
                        with patch("builtins.print"):
                            # Should not trigger help (which requires --sync/--status/--verify)
                            main()

    def test_force_test_prints_header(self, tmp_dir):
        """--force-test should print a header indicating test mode."""
        from sync_g_drive import main

        printed = []

        def capture_print(*args, **kwargs):
            printed.extend(str(a) for a in args)

        with patch("sync_g_drive.clear_checkpoint"):
            with patch("sync_g_drive.get_file_count", return_value=0):
                with patch("sync_g_drive.get_dir_size", return_value=0):
                    with patch("sys.argv", ["sync_g_drive.py", "--force-test"]):
                        with patch("builtins.print", side_effect=capture_print):
                            main()

        output = " ".join(printed)
        assert "force-test" in output.lower(), f"Expected 'force-test' in header: {output}"
        assert "Clearing checkpoint" in output, f"Expected 'Clearing checkpoint' message: {output}"


# ── --status / --verify tests ─────────────────────────────────────


class TestGetStatus:
    """Test the get_status helper function with mocked file counts."""

    def test_get_status_g_drive_missing(self, tmp_dir):
        """get_status should report g_available=False when G: drive is inaccessible."""
        from sync_g_drive import get_status

        # Mock G_SOURCE_OF_TRUTH to a nonexistent path so exists() returns False
        missing_g = tmp_dir / "nonexistent_g"

        with patch("sync_g_drive.get_file_count", side_effect=lambda path, pattern="*":
            42 if "*.py" in pattern else 100):
            with patch("sync_g_drive.get_dir_size", return_value=50000):
                with patch("sync_g_drive.G_SOURCE_OF_TRUTH", missing_g):
                    status = get_status()

        assert status["g_available"] is False
        assert status["source_py_files"] == 42
        assert status["source_total_files"] == 100
        assert status["source_size"] == 50000
        # G: stats should all be 0 when missing
        assert status["g_py_files"] == 0
        assert status["g_total_files"] == 0
        assert status["g_size"] == 0
        # Drift = source - G: = 100 - 0 = 100
        assert status["drift_files"] == 100
        assert status["drift_size"] == 50000

    def test_get_status_g_drive_available(self, tmp_dir):
        """get_status should include G: stats when drive is available."""
        from sync_g_drive import get_status

        available_g = tmp_dir / "g_avail"
        available_g.mkdir()

        # When G: drive exists, get_file_count is called 4 times:
        # 1. get_file_count(SOURCE_ROOT / "src", "*.py") → 42
        # 2. get_file_count(G_SOURCE_OF_TRUTH / "src", "*.py") → 30
        # 3. get_file_count(SOURCE_ROOT, "*") → 100
        # 4. get_file_count(G_SOURCE_OF_TRUTH, "*") → 80
        # get_dir_size is called 2 times:
        # 5. get_dir_size(SOURCE_ROOT) → 50000
        # 6. get_dir_size(G_SOURCE_OF_TRUTH) → 40000
        # Use sequential return values — call order is deterministic:
        # 1. get_file_count(SOURCE_ROOT / "src", "*.py") → 42
        # 2. get_file_count(G_SOURCE_OF_TRUTH / "src", "*.py") → 30
        # 3. get_file_count(SOURCE_ROOT, "*") → 100
        # 4. get_file_count(G_SOURCE_OF_TRUTH, "*") → 80
        file_count_returns = [42, 30, 100, 80]
        file_count_calls = []

        def mock_file_count(path, pattern="*"):
            file_count_calls.append((str(path), pattern))
            return file_count_returns[len(file_count_calls) - 1]

        dir_size_returns = [50000, 40000]
        dir_size_calls = []

        def mock_dir_size(path):
            dir_size_calls.append(str(path))
            return dir_size_returns[len(dir_size_calls) - 1]

        with patch("sync_g_drive.get_file_count", side_effect=mock_file_count):
            with patch("sync_g_drive.get_dir_size", side_effect=mock_dir_size):
                with patch("sync_g_drive.G_SOURCE_OF_TRUTH", available_g):
                    status = get_status()

        assert status["g_available"] is True
        assert status["source_py_files"] == 42
        assert status["g_py_files"] == 30
        assert status["source_total_files"] == 100
        assert status["g_total_files"] == 80
        assert status["source_size"] == 50000
        assert status["g_size"] == 40000
        assert status["drift_files"] == 20  # 100 - 80
        assert status["drift_size"] == 10000  # 50000 - 40000
        # Verify get_file_count was called 4 times
        assert len(file_count_calls) == 4


class TestVerifySync:
    """Test the verify_sync helper function with real temp directories."""

    MARKER_FILES = [
        "src/CORE/direct_memory.py",
        "src/CORE/local_memory.py",
        "src/CORE/paths.py",
        "SYSTEM_INSTRUCTIONS.md",
        "DIRECT_MEMORY_INSTRUCTIONS.md",
        "CODEBASE_AUDIT_TODO.md",
    ]

    def _create_source_files(self, root: Path, content: str = "# source"):
        """Create all marker source files under root."""
        for rel_path in self.MARKER_FILES:
            file = root / rel_path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content, encoding="utf-8")

    def _create_g_files(self, root: Path, rel_paths, content: str = "# g"):
        """Create specific marker files on G: drive."""
        for rel_path in rel_paths:
            file = root / rel_path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content, encoding="utf-8")

    def test_verify_sync_all_missing(self, tmp_dir):
        """verify_sync should report MISSING for all files when G: drive is empty."""
        from sync_g_drive import verify_sync

        src_root = tmp_dir / "src"
        g_root = tmp_dir / "g_empty"
        src_root.mkdir(parents=True, exist_ok=True)
        g_root.mkdir(parents=True, exist_ok=True)

        self._create_source_files(src_root)

        with patch("sync_g_drive.SOURCE_ROOT", src_root):
            with patch("sync_g_drive.G_SOURCE_OF_TRUTH", g_root):
                results = verify_sync()

        for rel_path in self.MARKER_FILES:
            assert results[rel_path] == "MISSING", \
                f"Expected {rel_path} to be MISSING, got {results[rel_path]}"
        assert len(results) == 6

    def test_verify_sync_all_ok(self, tmp_dir):
        """verify_sync should report OK when all files are synced (same mtime)."""
        from sync_g_drive import verify_sync

        src_root = tmp_dir / "src_ok"
        g_root = tmp_dir / "g_ok"
        src_root.mkdir(parents=True, exist_ok=True)
        g_root.mkdir(parents=True, exist_ok=True)

        # Create same content on both sides then normalize mtimes so source <= G:
        content = "# identical content\n"
        self._create_source_files(src_root, content)
        self._create_g_files(g_root, self.MARKER_FILES, content)
        # Set G: file mtimes to be slightly newer than source using os.utime
        import os
        now = 2000000000.0
        for rel_path in self.MARKER_FILES:
            os.utime(g_root / rel_path, (now, now))

        with patch("sync_g_drive.SOURCE_ROOT", src_root):
            with patch("sync_g_drive.G_SOURCE_OF_TRUTH", g_root):
                results = verify_sync()

        for rel_path in self.MARKER_FILES:
            assert results[rel_path] == "OK", \
                f"Expected {rel_path} to be OK, got {results[rel_path]}"

    def test_verify_sync_mixed_results(self, tmp_dir):
        """verify_sync should return mixed OK/MISSING/STALE results."""
        from sync_g_drive import verify_sync

        src_root = tmp_dir / "src_mixed"
        g_root = tmp_dir / "g_mixed"
        src_root.mkdir(parents=True, exist_ok=True)
        g_root.mkdir(parents=True, exist_ok=True)

        # Source files that exist on both sides: direct_memory, local_memory, paths, SYSTEM_INSTRUCTIONS
        # G: files that match source: direct_memory, SYSTEM_INSTRUCTIONS (OK)
        # G: file that is older than source: local_memory (STALE)
        # G: files missing: DIR_MEM, CODEBASE_AUDIT (MISSING)

        # Step 1: Create all source files
        self._create_source_files(src_root, "# source content")

        import os

        # Step 2: Create G: files for some, set explicit mtimes for deterministic results
        # G: files have later mtime → OK (src.mtime <= dst.mtime)
        ok_paths = ["src/CORE/direct_memory.py", "SYSTEM_INSTRUCTIONS.md"]
        self._create_g_files(g_root, ok_paths, "# G content")
        old_ts = 1000000000.0
        for rel_path in ok_paths:
            os.utime(g_root / rel_path, (old_ts, old_ts))
        # Source files all have earlier mtime → OK (src.mtime <= dst.mtime)
        for rel_path in ok_paths:
            os.utime(src_root / rel_path, (old_ts - 100, old_ts - 100))

        # Step 3: For STALE, create G: file with old timestamp, then modify source with newer
        stale_path = "src/CORE/local_memory.py"
        stale_g = g_root / stale_path
        stale_g.parent.mkdir(parents=True, exist_ok=True)
        stale_g.write_text("# old G content", encoding="utf-8")
        os.utime(stale_g, (old_ts, old_ts))  # G: has old timestamp
        # Source has newer timestamp → STALE (src.mtime > dst.mtime)
        new_ts = 2000000000.0
        (src_root / stale_path).write_text("# NEWER source content", encoding="utf-8")
        os.utime(src_root / stale_path, (new_ts, new_ts))

        with patch("sync_g_drive.SOURCE_ROOT", src_root):
            with patch("sync_g_drive.G_SOURCE_OF_TRUTH", g_root):
                results = verify_sync()

        assert results["src/CORE/direct_memory.py"] == "OK", results
        assert results["SYSTEM_INSTRUCTIONS.md"] == "OK", results
        assert results["src/CORE/local_memory.py"] == "STALE", results
        assert results["src/CORE/paths.py"] == "MISSING", results
        assert results["DIRECT_MEMORY_INSTRUCTIONS.md"] == "MISSING", results
        assert results["CODEBASE_AUDIT_TODO.md"] == "MISSING", results

    def test_verify_sync_source_missing(self, tmp_dir):
        """verify_sync should report SOURCE_MISSING when source file is gone."""
        from sync_g_drive import verify_sync

        src_root = tmp_dir / "src_sm"
        g_root = tmp_dir / "g_sm"
        src_root.mkdir(parents=True, exist_ok=True)
        g_root.mkdir(parents=True, exist_ok=True)

        # Create only G: files for some paths but NOT corresponding source files
        g_file = g_root / "src/CORE/direct_memory.py"
        g_file.parent.mkdir(parents=True, exist_ok=True)
        g_file.write_text("# G content only", encoding="utf-8")
        # Don't create the source file at src_root / "src/CORE/direct_memory.py"

        with patch("sync_g_drive.SOURCE_ROOT", src_root):
            with patch("sync_g_drive.G_SOURCE_OF_TRUTH", g_root):
                results = verify_sync()

        # All files that lack source should be MISSING (source missing + G: missing)
        # But the one G: file that exists while source is missing → SOURCE_MISSING
        assert results["src/CORE/direct_memory.py"] == "SOURCE_MISSING", results


class TestMainStatusAndVerify:
    """Test main() CLI entry point with --status and --verify flags."""

    def test_main_status_g_drive_missing(self):
        """--status should print G: as unavailable when drive is missing."""
        from sync_g_drive import main

        printed = []

        def capture(*args):
            printed.extend(str(a) for a in args)

        with patch("sync_g_drive.get_status", return_value={
            "g_available": False,
            "source_py_files": 42,
            "g_py_files": 0,
            "source_total_files": 100,
            "g_total_files": 0,
            "source_size": 50000,
            "g_size": 0,
            "drift_files": 100,
            "drift_size": 50000,
        }):
            with patch("sys.argv", ["sync_g_drive.py", "--status"]):
                with patch("builtins.print", side_effect=capture):
                    main()

        output = " ".join(printed)
        assert "G: available: False" in output, f"Missing G: available flag: {output}"
        assert "Source .py:   42" in output, f"Missing source count: {output}"
        assert "Drift:        100 files" in output, f"Missing drift: {output}"
        # G: total line should NOT appear when unavailable
        assert "G: total" not in output, f"G: total should not appear when unavailable: {output}"

    def test_main_status_g_drive_available(self):
        """--status should print G: stats when drive is available."""
        from sync_g_drive import main

        printed = []

        def capture(*args):
            printed.extend(str(a) for a in args)

        with patch("sync_g_drive.get_status", return_value={
            "g_available": True,
            "source_py_files": 42,
            "g_py_files": 30,
            "source_total_files": 100,
            "g_total_files": 80,
            "source_size": 50000,
            "g_size": 40000,
            "drift_files": 20,
            "drift_size": 10000,
        }):
            with patch("sys.argv", ["sync_g_drive.py", "--status"]):
                with patch("builtins.print", side_effect=capture):
                    main()

        output = " ".join(printed)
        assert "G: available: True" in output, f"Missing G: available: {output}"
        assert "G: total:     80 files" in output, f"Missing G: total: {output}"
        assert "Drift:        20 files" in output, f"Missing drift: {output}"

    def test_main_verify_issues_found(self):
        """--verify should print ISSUES FOUND with [!!] for missing files."""
        from sync_g_drive import main

        printed = []

        def capture(*args):
            printed.extend(str(a) for a in args)

        with patch("sync_g_drive.verify_sync", return_value={
            "src/CORE/direct_memory.py": "MISSING",
            "src/CORE/local_memory.py": "MISSING",
            "src/CORE/paths.py": "MISSING",
            "SYSTEM_INSTRUCTIONS.md": "MISSING",
            "DIRECT_MEMORY_INSTRUCTIONS.md": "MISSING",
            "CODEBASE_AUDIT_TODO.md": "MISSING",
        }):
            with patch("sys.argv", ["sync_g_drive.py", "--verify"]):
                with patch("builtins.print", side_effect=capture):
                    main()

        output = " ".join(printed)
        assert "ISSUES FOUND" in output, f"Expected ISSUES FOUND: {output}"
        assert "!!" in output, f"Expected [!!] markers: {output}"
        assert "direct_memory.py" in output, f"Expected file path: {output}"

    def test_main_verify_all_ok(self):
        """--verify should print ALL OK when all files are synced."""
        from sync_g_drive import main

        printed = []

        def capture(*args):
            printed.extend(str(a) for a in args)

        with patch("sync_g_drive.verify_sync", return_value={
            "src/CORE/direct_memory.py": "OK",
            "src/CORE/local_memory.py": "OK",
            "src/CORE/paths.py": "OK",
            "SYSTEM_INSTRUCTIONS.md": "OK",
            "DIRECT_MEMORY_INSTRUCTIONS.md": "OK",
            "CODEBASE_AUDIT_TODO.md": "OK",
        }):
            with patch("sys.argv", ["sync_g_drive.py", "--verify"]):
                with patch("builtins.print", side_effect=capture):
                    main()

        output = " ".join(printed)
        assert "ALL OK" in output, f"Expected ALL OK: {output}"
        assert "OK" in output, f"Expected [OK] markers: {output}"
        assert "!!" not in output, f"No [!!] expected when all OK: {output}"

    def test_main_verify_mixed_results(self):
        """--verify should show [!!], [OK], and [??] icons for mixed results."""
        from sync_g_drive import main

        printed = []

        def capture(*args):
            printed.extend(str(a) for a in args)

        with patch("sync_g_drive.verify_sync", return_value={
            "src/CORE/direct_memory.py": "OK",
            "src/CORE/local_memory.py": "MISSING",
            "src/CORE/paths.py": "STALE",
            "SYSTEM_INSTRUCTIONS.md": "OK",
            "DIRECT_MEMORY_INSTRUCTIONS.md": "MISSING",
            "CODEBASE_AUDIT_TODO.md": "SOURCE_MISSING",
        }):
            with patch("sys.argv", ["sync_g_drive.py", "--verify"]):
                with patch("builtins.print", side_effect=capture):
                    main()

        output = " ".join(printed)
        assert "ISSUES FOUND" in output, f"Expected ISSUES FOUND: {output}"
        # [OK] for direct_memory.py
        # [!!] for local_memory.py (MISSING)
        # [??] for paths.py (STALE)  — icon is "??" for anything not OK or MISSING
        assert "[OK]" in output, f"Expected [OK]: {output}"
        assert "[!!]" in output, f"Expected [!!]: {output}"
        assert "[??]" in output, f"Expected [??] for STALE: {output}"


# ── format_size tests ──────────────────────────────────────────────


class TestFormatSize:
    """Test the format_size helper function."""

    def test_format_size_bytes(self):
        """0-1023 bytes should show as B."""
        from sync_g_drive import format_size
        assert format_size(0) == "0.0 B"
        assert format_size(100) == "100.0 B"
        assert format_size(1023) == "1023.0 B"

    def test_format_size_kb(self):
        """1024-1048575 bytes should show as KB."""
        from sync_g_drive import format_size
        assert format_size(1024) == "1.0 KB"
        assert format_size(2048) == "2.0 KB"
        assert format_size(153600) == "150.0 KB"

    def test_format_size_mb(self):
        """1048576+ bytes should show as MB."""
        from sync_g_drive import format_size
        assert format_size(1048576) == "1.0 MB"
        assert format_size(52428800) == "50.0 MB"

    def test_format_size_gb(self):
        """1073741824+ bytes should show as GB."""
        from sync_g_drive import format_size
        assert format_size(1073741824) == "1.0 GB"
        assert format_size(53687091200) == "50.0 GB"

    def test_format_size_tb(self):
        """1099511627776+ bytes should show as TB."""
        from sync_g_drive import format_size
        assert format_size(1099511627776) == "1.0 TB"
