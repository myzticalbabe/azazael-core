# coding: utf-8
"""
Tests for safe_file_ops.py — verify-before-delete & atomic write utilities.

Functions tested:
    - verify_tree_integrity
    - safe_copytree
    - safe_rmtree
    - safe_move
    - atomic_write
    - safe_copytree_chunked
    - is_same_drive
"""

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from CORE.safe_file_ops import (
    verify_tree_integrity,
    safe_copytree,
    safe_rmtree,
    safe_move,
    atomic_write,
    safe_copytree_chunked,
    is_same_drive,
    _file_md5,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dir():
    """Create a temporary directory for test isolation. Cleans up after test."""
    d = tempfile.mkdtemp(prefix="test_safe_ops_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def src_dir(tmp_dir):
    """Create a source directory with test files."""
    src = tmp_dir / "source"
    src.mkdir()
    (src / "a.txt").write_text("hello", encoding="utf-8")
    (src / "b.txt").write_text("world", encoding="utf-8")
    sub = src / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("nested content", encoding="utf-8")
    return src


@pytest.fixture
def dst_dir(tmp_dir):
    """Create an empty destination directory."""
    d = tmp_dir / "dest"
    d.mkdir()
    return d


# ── _file_md5 Tests ────────────────────────────────────────────────


class TestFileMd5:
    def test_basic_hash(self, tmp_dir):
        """MD5 of known content should match expected hash."""
        f = tmp_dir / "test.txt"
        f.write_text("hello", encoding="utf-8")
        h = _file_md5(f)
        assert h == "5d41402abc4b2a76b9719d911017c592", f"Unexpected hash: {h}"

    def test_empty_file(self, tmp_dir):
        """Empty file should produce known MD5."""
        f = tmp_dir / "empty.txt"
        f.write_text("", encoding="utf-8")
        h = _file_md5(f)
        assert h == "d41d8cd98f00b204e9800998ecf8427e", f"Unexpected hash: {h}"

    def test_nonexistent_file(self, tmp_dir):
        """Nonexistent file should return empty string."""
        h = _file_md5(tmp_dir / "nope.txt")
        assert h == ""

    def test_binary_content(self, tmp_dir):
        """Binary content should still produce valid MD5."""
        f = tmp_dir / "bin.bin"
        f.write_bytes(bytes(range(256)))
        h = _file_md5(f)
        assert len(h) == 32, f"Expected 32-char hex, got {len(h)}"
        assert all(c in "0123456789abcdef" for c in h), f"Non-hex chars: {h}"


# ── verify_tree_integrity Tests ────────────────────────────────────


class TestVerifyTreeIntegrity:
    def test_identical_trees(self, src_dir, dst_dir):
        """Identical source and destination should report match=True."""
        shutil.copytree(str(src_dir), str(dst_dir / "source"))
        result = verify_tree_integrity(src_dir, dst_dir / "source")
        assert result["match"] is True
        assert result["src_count"] == result["dst_count"] == 3
        assert result["src_size"] == result["dst_size"]

    def test_missing_files(self, src_dir, dst_dir):
        """Destination with missing files should report match=False and list missing."""
        shutil.copytree(str(src_dir), str(dst_dir / "source"))
        # Delete one file from destination
        (dst_dir / "source" / "a.txt").unlink()
        result = verify_tree_integrity(src_dir, dst_dir / "source")
        assert result["match"] is False
        assert "a.txt" in result["missing"]

    def test_extra_files(self, src_dir, dst_dir):
        """Destination with extra files should report match=False and list extra."""
        shutil.copytree(str(src_dir), str(dst_dir / "source"))
        (dst_dir / "source" / "extra.txt").write_text("extra", encoding="utf-8")
        result = verify_tree_integrity(src_dir, dst_dir / "source")
        assert result["match"] is False
        assert "extra.txt" in result["extra"]

    def test_size_mismatch(self, src_dir, dst_dir):
        """Files with same name but different sizes should report size_mismatch."""
        shutil.copytree(str(src_dir), str(dst_dir / "source"))
        (dst_dir / "source" / "a.txt").write_text("different content", encoding="utf-8")
        result = verify_tree_integrity(src_dir, dst_dir / "source")
        assert result["match"] is False
        assert "a.txt" in result["size_mismatch"]

    def test_source_not_found(self, tmp_dir):
        """Missing source should report error."""
        result = verify_tree_integrity(tmp_dir / "nonexistent", tmp_dir / "dest")
        assert result["match"] is False
        assert "error" in result

    def test_destination_not_found(self, src_dir, tmp_dir):
        """Missing destination should report all source files as missing."""
        result = verify_tree_integrity(src_dir, tmp_dir / "nonexistent")
        assert result["match"] is False
        assert len(result["missing"]) == 3

    def test_empty_directories(self, tmp_dir):
        """Two empty directories should match."""
        a = tmp_dir / "a"
        b = tmp_dir / "b"
        a.mkdir()
        b.mkdir()
        result = verify_tree_integrity(a, b)
        assert result["match"] is True
        assert result["src_count"] == result["dst_count"] == 0

    def test_hash_sampling(self, src_dir, dst_dir):
        """Hash sampling should detect content differences (same-length content)."""
        shutil.copytree(str(src_dir), str(dst_dir / "source"))
        # Modify b.txt with same-length content so size check passes but hash differs
        (dst_dir / "source" / "b.txt").write_text("w0rld", encoding="utf-8")
        result = verify_tree_integrity(
            src_dir, dst_dir / "source",
            sample_hashes=True, hash_max_files=10,
        )
        assert result["match"] is False
        assert len(result["hash_mismatch"]) > 0


# ── safe_copytree Tests ───────────────────────────────────────────


class TestSafeCopytree:
    def test_basic_copy_with_verify(self, src_dir, dst_dir):
        """Safe copytree should copy and verify by default."""
        result = safe_copytree(src_dir, dst_dir / "copy")
        assert result["success"] is True
        verify = result["detail"]
        assert verify["match"] is True
        assert verify["src_count"] == verify["dst_count"] == 3

    def test_copy_no_verify(self, src_dir, dst_dir):
        """Safe copytree with verify=False should skip integrity check."""
        result = safe_copytree(src_dir, dst_dir / "copy", verify=False)
        assert result["success"] is True
        assert (dst_dir / "copy" / "a.txt").exists()
        assert (dst_dir / "copy" / "sub" / "c.txt").exists()

    def test_source_not_found(self, tmp_dir, dst_dir):
        """Missing source should fail gracefully."""
        result = safe_copytree(tmp_dir / "nonexistent", dst_dir / "copy")
        assert result["success"] is False
        assert "not found" in str(result["detail"]).lower()

    def test_copy_with_ignore(self, src_dir, dst_dir):
        """Ignore callable should exclude files. Disable verify since src has files not in dst."""
        result = safe_copytree(
            src_dir, dst_dir / "copy",
            verify=False,
            ignore=shutil.ignore_patterns("b.txt"),
        )
        assert result["success"] is True
        assert (dst_dir / "copy" / "a.txt").exists()
        assert not (dst_dir / "copy" / "b.txt").exists()
        # Verify the excluded file is truly missing (not a copy failure)
        assert (dst_dir / "copy" / "sub" / "c.txt").exists()

    def test_hash_sampling_during_copy(self, src_dir, dst_dir):
        """Copy with hash sampling should pass for identical trees."""
        result = safe_copytree(src_dir, dst_dir / "copy", sample_hashes=True)
        assert result["success"] is True
        detail = result["detail"]
        assert detail.get("sampled_count", 0) > 0


# ── safe_rmtree Tests ──────────────────────────────────────────────


class TestSafeRmtree:
    def test_basic_removal(self, src_dir):
        """safe_rmtree should remove an existing directory."""
        result = safe_rmtree(src_dir)
        assert result["success"] is True
        assert not src_dir.exists()

    def test_remove_nonexistent(self, tmp_dir):
        """Removing a nonexistent path should succeed trivially."""
        result = safe_rmtree(tmp_dir / "nope")
        assert result["success"] is True

    def test_dry_run(self, src_dir):
        """Dry run should not actually remove the directory."""
        assert src_dir.exists()
        result = safe_rmtree(src_dir, dry_run=True)
        assert result["success"] is True
        assert src_dir.exists()

    def test_verify_source_passes(self, src_dir, tmp_dir):
        """Removing with verify_source should pass when dest is complete."""
        dest = tmp_dir / "backup"
        shutil.copytree(str(src_dir), str(dest))
        result = safe_rmtree(src_dir, verify_source=dest)
        assert result["success"] is True
        assert not src_dir.exists()

    def test_verify_source_fails(self, src_dir, tmp_dir):
        """Removing with verify_source should fail when dest is incomplete."""
        dest = tmp_dir / "backup"
        dest.mkdir()
        # Copy only one file — incomplete
        shutil.copy2(str(src_dir / "a.txt"), str(dest / "a.txt"))
        result = safe_rmtree(src_dir, verify_source=dest)
        # Should refuse to delete because source has files not in dest
        assert result["success"] is False
        assert src_dir.exists()  # Source should NOT be deleted

    def test_verify_source_missing(self, src_dir, tmp_dir):
        """Removing with missing verify_source should fail."""
        result = safe_rmtree(src_dir, verify_source=tmp_dir / "nonexistent")
        assert result["success"] is False

    def test_remove_single_file(self, tmp_dir):
        """Removing a single file (not directory) should work."""
        f = tmp_dir / "file.txt"
        f.write_text("hello", encoding="utf-8")
        result = safe_rmtree(f)
        assert result["success"] is True
        assert not f.exists()

    def test_backup_before_delete(self, src_dir, tmp_dir):
        """Backup_dir option should create backup before deleting."""
        backup_dir = tmp_dir / "backups"
        result = safe_rmtree(src_dir, backup_dir=backup_dir)
        # Should have backed up and then deleted
        assert result["success"] is True
        assert not src_dir.exists()
        # Backup should exist
        backups = list(backup_dir.iterdir())
        assert len(backups) >= 1


# ── safe_move Tests ────────────────────────────────────────────────


class TestSafeMove:
    def test_file_move_with_verify(self, tmp_dir):
        """Moving a file with verify should copy then delete source."""
        src = tmp_dir / "src_move.txt"
        dst = tmp_dir / "dst_move.txt"
        src.write_text("move content", encoding="utf-8")
        result = safe_move(src, dst, verify=True)
        assert result["success"] is True
        assert not src.exists()
        assert dst.read_text(encoding="utf-8") == "move content"

    def test_file_move_no_verify(self, tmp_dir):
        """Moving a file without verify should still succeed."""
        src = tmp_dir / "src.txt"
        dst = tmp_dir / "dst.txt"
        src.write_text("content", encoding="utf-8")
        result = safe_move(src, dst, verify=False)
        assert result["success"] is True
        assert not src.exists()
        assert dst.read_text(encoding="utf-8") == "content"

    def test_directory_move(self, src_dir, tmp_dir):
        """Moving a directory should copy then delete."""
        dst = tmp_dir / "moved_dir"
        result = safe_move(src_dir, dst)
        assert result["success"] is True
        assert not src_dir.exists()
        assert (dst / "a.txt").exists()
        assert (dst / "sub" / "c.txt").exists()

    def test_move_nonexistent(self, tmp_dir):
        """Moving a nonexistent source should fail."""
        result = safe_move(tmp_dir / "nope.txt", tmp_dir / "dst.txt")
        assert result["success"] is False
        assert "not found" in str(result["detail"]).lower()


# ── atomic_write Tests ─────────────────────────────────────────────


class TestAtomicWrite:
    def test_write_string(self, tmp_dir):
        """Writing string content should create the file."""
        f = tmp_dir / "out.txt"
        result = atomic_write(f, "hello world")
        assert result["success"] is True
        assert f.read_text(encoding="utf-8") == "hello world"

    def test_write_bytes(self, tmp_dir):
        """Writing bytes content should create the file."""
        f = tmp_dir / "out.bin"
        result = atomic_write(f, b"\x00\x01\x02\xff")
        assert result["success"] is True
        assert f.read_bytes() == b"\x00\x01\x02\xff"

    def test_overwrite_existing(self, tmp_dir):
        """Overwriting an existing file should succeed."""
        f = tmp_dir / "out.txt"
        f.write_text("original", encoding="utf-8")
        result = atomic_write(f, "modified")
        assert result["success"] is True
        assert f.read_text(encoding="utf-8") == "modified"

    def test_make_backup(self, tmp_dir):
        """make_backup=True should create a .bak file."""
        f = tmp_dir / "out.txt"
        f.write_text("original", encoding="utf-8")
        result = atomic_write(f, "modified", make_backup=True)
        assert result["success"] is True
        assert f.read_text(encoding="utf-8") == "modified"
        # Check backup exists
        bak = f.with_suffix(".txt.bak")
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == "original"

    def test_write_nested_directory(self, tmp_dir):
        """Writing to a nested path should create intermediate dirs."""
        f = tmp_dir / "a" / "b" / "c" / "deep.txt"
        result = atomic_write(f, "nested")
        assert result["success"] is True
        assert f.read_text(encoding="utf-8") == "nested"

    def test_write_empty_string(self, tmp_dir):
        """Writing empty string should create empty file."""
        f = tmp_dir / "empty.txt"
        result = atomic_write(f, "")
        assert result["success"] is True
        assert f.read_text(encoding="utf-8") == ""


# ── safe_copytree_chunked Tests ────────────────────────────────────


class TestSafeCopytreeChunked:
    def test_basic_chunked_copy(self, src_dir, tmp_dir):
        """Chunked copy should copy all files."""
        dst = tmp_dir / "chunked_dst"
        result = safe_copytree_chunked(src_dir, dst)
        assert result["success"] is True
        assert result["copied"] == 3
        assert (dst / "a.txt").exists()
        assert (dst / "sub" / "c.txt").exists()

    def test_resume_from_checkpoint(self, src_dir, tmp_dir):
        """Chunked copy with existing checkpoint should skip completed files."""
        dst = tmp_dir / "chunked_dst"
        dst.mkdir(parents=True, exist_ok=True)

        # Create a partial copy: copy one file manually, create checkpoint
        src_file = src_dir / "a.txt"
        dst_file = dst / "a.txt"
        shutil.copy2(str(src_file), str(dst_file))
        cp = dst / ".copy_checkpoint"
        cp.write_text(json.dumps({src_file.name: src_file.stat().st_size}), encoding="utf-8")

        # Run chunked copy — should resume, copying only b.txt and sub/c.txt
        result = safe_copytree_chunked(src_dir, dst)
        assert result["success"] is True
        # Verify 3 total files, 2 newly copied, 1 resumed from checkpoint
        assert result["total"] == 3
        assert result["copied"] >= 2  # At minimum b.txt + sub/c.txt

    def test_chunked_copy_empty_dir(self, tmp_dir):
        """Chunked copy of empty directory should succeed."""
        src = tmp_dir / "empty_src"
        dst = tmp_dir / "empty_dst"
        src.mkdir()
        result = safe_copytree_chunked(src, dst)
        assert result["success"] is True
        assert result["total"] == 0

    def test_chunked_verify(self, src_dir, tmp_dir):
        """Chunked copy with verify=True should pass integrity."""
        dst = tmp_dir / "verified_dst"
        result = safe_copytree_chunked(src_dir, dst, verify=True)
        assert result["success"] is True
        assert "integrity" in result
        assert result["integrity"]["match"] is True


# ── is_same_drive Tests ────────────────────────────────────────────


class TestIsSameDrive:
    def test_same_directory(self, tmp_dir):
        """Two paths in same directory should be on same drive."""
        a = tmp_dir / "a"
        b = tmp_dir / "b"
        a.touch()
        b.touch()
        result = is_same_drive(a, b)
        # On Windows, paths in same temp dir have same drive letter
        # On Unix, same device means same mount
        assert result is True


# ── Edge Case Tests ────────────────────────────────────────────────


class TestEdgeCases:
    def test_verify_integrity_with_symlinks(self, src_dir, tmp_dir):
        """verify_tree_integrity should handle (or skip) symlinks gracefully."""
        # Create symlink (may fail on Windows without admin)
        try:
            link = src_dir / "link_to_a.txt"
            link.symlink_to(src_dir / "a.txt")
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

        dst = tmp_dir / "link_dst"
        shutil.copytree(str(src_dir), str(dst))
        result = verify_tree_integrity(src_dir, dst)
        # Symlinks might be counted differently; at minimum shouldn't crash
        assert "error" not in result

    def test_atomic_write_special_chars(self, tmp_dir):
        """Writing content with special chars should work."""
        f = tmp_dir / "special.txt"
        content = "line1\nline2\n\ttabbed\nunicode accent: eacute"
        result = atomic_write(f, content)
        assert result["success"] is True
        # Use binary read to avoid text-mode newline translation differences
        written = f.read_bytes()
        expected = content.encode("utf-8")
        assert written == expected

    def test_empty_directory_copy_and_remove(self, tmp_dir):
        """Copying and removing an empty directory should work."""
        src = tmp_dir / "empty"
        dst = tmp_dir / "empty_copy"
        src.mkdir()
        result = safe_copytree(src, dst, verify=True)
        assert result["success"] is True
        remove_result = safe_rmtree(dst)
        assert remove_result["success"] is True

    def test_deeply_nested_copy(self, tmp_dir):
        """Copying deeply nested directory should preserve structure."""
        src = tmp_dir / "deep"
        src.mkdir(parents=True)
        # Create nesting: deep/a/b/c/d/e/file.txt
        deep_file = src / "a" / "b" / "c" / "d" / "e" / "file.txt"
        deep_file.parent.mkdir(parents=True)
        deep_file.write_text("deeply nested content", encoding="utf-8")
        # Also create a file at root level
        (src / "root.txt").write_text("root", encoding="utf-8")

        dst = tmp_dir / "deep_copy"
        result = safe_copytree(src, dst)
        assert result["success"] is True
        assert (dst / "a" / "b" / "c" / "d" / "e" / "file.txt").exists()
        assert (dst / "a" / "b" / "c" / "d" / "e" / "file.txt").read_text(encoding="utf-8") == "deeply nested content"
        assert (dst / "root.txt").exists()


# ── Cross-Platform Path Handling Tests ─────────────────────────────


class TestCrossPlatformPathHandling:
    """
    Tests for cross-platform path handling in safe_file_ops.

    These tests verify that safe_file_ops functions handle paths correctly
    regardless of the path style used (POSIX forward-slash, Windows backslash,
    mixed separators, UNC paths, etc.). They run on the current platform but
    exercise the path manipulation logic that must be platform-agnostic.

    Key concerns:
    - Path() normalizes separators across platforms
    - relative_to() output may use OS-native separators
    - verify_tree_integrity uses str(relative_to()) for its file maps
    - is_same_drive() has platform-specific branches
    - Functions accepting Union[str, Path] must handle both
    """

    def test_posix_style_string_paths(self, tmp_dir):
        """POSIX-style forward-slash paths should work as input."""
        src = tmp_dir / "posix_src"
        src.mkdir()
        (src / "file.txt").write_text("posix test", encoding="utf-8")

        # Use forward-slash string path
        dst = str(tmp_dir).replace("\\", "/") + "/posix_dst"
        result = safe_copytree(str(src).replace("\\", "/"), dst)
        assert result["success"] is True
        dst_p = Path(dst)
        assert dst_p.exists()
        assert (dst_p / "file.txt").read_text(encoding="utf-8") == "posix test"

    def test_mixed_separator_paths(self, tmp_dir):
        """Mixed forward/backslash paths should normalize correctly."""
        # Create a path with mixed separators
        src = tmp_dir / "mixed_src"
        src.mkdir()
        (src / "data.txt").write_text("mixed", encoding="utf-8")

        # Use mixed separators: backslash after drive, forward slash in subpath
        mixed = str(tmp_dir) + "/mixed_dst"
        result = safe_copytree(str(src).replace("\\", "/"), mixed)
        assert result["success"] is True
        assert Path(tmp_dir / "mixed_dst" / "data.txt").read_text(encoding="utf-8") == "mixed"

    def test_relative_to_produces_portable_separators(self, tmp_dir):
        """relative_to() output should produce consistent relative paths.

        verify_tree_integrity uses str(f.relative_to(src_p)) to build its
        internal file map. On Windows this produces backslash-separated paths
        while on POSIX it produces forward-slash paths. The comparison logic
        must work correctly regardless.
        """
        src = tmp_dir / "rel_src"
        sub = src / "sub" / "nested"
        sub.mkdir(parents=True)
        (sub / "file.txt").write_text("deep", encoding="utf-8")

        # Mirror the exact logic from verify_tree_integrity
        file_map = {}
        for f in src.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(src))
                file_map[rel] = f.stat()

        # The relative path should be usable to re-locate the file
        for rel_key in file_map:
            reconstructed = src / rel_key
            assert reconstructed.exists(), f"Reconstructed path not found: {reconstructed}"
            assert reconstructed.read_text(encoding="utf-8") == "deep"

    def test_verify_integrity_with_posix_style_paths(self, tmp_dir):
        """verify_tree_integrity should work with forward-slash paths."""
        src = tmp_dir / "vi_src"
        src.mkdir()
        (src / "a.txt").write_text("alpha", encoding="utf-8")
        (src / "b.txt").write_text("beta", encoding="utf-8")

        dst = tmp_dir / "vi_dst"
        shutil.copytree(str(src), str(dst))

        # Call with forward-slash string paths
        src_fwd = str(src).replace("\\", "/")
        dst_fwd = str(dst).replace("\\", "/")
        result = verify_tree_integrity(src_fwd, dst_fwd)
        assert result["match"] is True
        assert result["src_count"] == result["dst_count"] == 2

    def test_verify_integrity_with_missing_file_posix(self, tmp_dir):
        """verify_tree_integrity missing-file detection with POSIX paths."""
        src = tmp_dir / "vm_src"
        src.mkdir()
        (src / "a.txt").write_text("alpha", encoding="utf-8")
        (src / "b.txt").write_text("beta", encoding="utf-8")

        dst = tmp_dir / "vm_dst"
        dst.mkdir()
        # Copy only one file
        shutil.copy2(str(src / "a.txt"), str(dst / "a.txt"))

        src_fwd = str(src).replace("\\", "/")
        dst_fwd = str(dst).replace("\\", "/")
        result = verify_tree_integrity(src_fwd, dst_fwd)
        assert result["match"] is False
        assert len(result["missing"]) == 1

    def test_is_same_drive_windows_unc_paths(self):
        """is_same_drive should handle Windows UNC paths."""
        if sys.platform != "win32":
            pytest.skip("Windows-specific: UNC path drive parsing")

        # UNC paths: \\server\share\path
        # Path('\\\\server\\share\\folder').drive returns '\\\\server\\share'
        unc = "\\\\server\\share\\folder\\file.txt"
        same = "\\\\server\\share\\other\\foo.txt"
        diff = "\\\\other\\share\\bar.txt"

        assert is_same_drive(unc, same), "Same UNC share should match"
        assert not is_same_drive(unc, diff), "Different UNC shares should not match"

    def test_is_same_drive_with_posix_style(self, tmp_dir):
        """is_same_drive should handle forward-slash paths."""
        a = tmp_dir / "a.txt"
        b = tmp_dir / "b.txt"
        a.touch()
        b.touch()

        fwd_a = str(a).replace("\\", "/")
        fwd_b = str(b).replace("\\", "/")
        assert is_same_drive(fwd_a, fwd_b) is True

    def test_is_same_drive_nonexistent_paths(self, tmp_dir):
        """is_same_drive should handle nonexistent paths gracefully."""
        # On Windows: Path.resolve() works even for nonexistent paths
        # On Unix: os.stat() raises OSError
        result = is_same_drive(
            str(tmp_dir / "nonexistent_1"),
            str(tmp_dir / "nonexistent_2"),
        )
        # Shouldn't crash; expect bool
        assert isinstance(result, bool)

    def test_atomic_write_with_posix_style_path(self, tmp_dir):
        """atomic_write should work with forward-slash paths."""
        posix_path = str(tmp_dir).replace("\\", "/") + "/posix_atomic.txt"
        result = atomic_write(posix_path, "forward slash content")
        assert result["success"] is True
        assert Path(posix_path).read_text(encoding="utf-8") == "forward slash content"

    def test_atomic_write_with_mixed_separators(self, tmp_dir):
        """atomic_write should handle mixed separator paths."""
        # Test with backslash in directory, forward slash in filename
        mixed = str(tmp_dir) + "/mixed" + "\\" + "out.txt"
        result = atomic_write(mixed, "mixed separator content")
        assert result["success"] is True
        assert Path(mixed).read_text(encoding="utf-8") == "mixed separator content"

    def test_safe_rmtree_with_posix_style(self, tmp_dir):
        """safe_rmtree should work with forward-slash paths."""
        src = tmp_dir / "rm_posix"
        src.mkdir()
        (src / "f.txt").write_text("delete me", encoding="utf-8")

        posix_src = str(src).replace("\\", "/")
        result = safe_rmtree(posix_src)
        assert result["success"] is True
        assert not src.exists()

    def test_safe_move_with_posix_style(self, tmp_dir):
        """safe_move should work with forward-slash paths."""
        src = tmp_dir / "mv_posix_src.txt"
        dst = tmp_dir / "mv_posix_dst.txt"
        src.write_text("move me", encoding="utf-8")

        src_fwd = str(src).replace("\\", "/")
        dst_fwd = str(dst).replace("\\", "/")
        result = safe_move(src_fwd, dst_fwd, verify=True)
        assert result["success"] is True
        assert not src.exists()
        assert dst.read_text(encoding="utf-8") == "move me"

    def test_safe_copytree_chunked_with_posix_style(self, src_dir, tmp_dir):
        """safe_copytree_chunked should work with forward-slash paths."""
        dst = tmp_dir / "chunk_posix"
        src_fwd = str(src_dir).replace("\\", "/")
        dst_fwd = str(dst).replace("\\", "/")
        result = safe_copytree_chunked(src_fwd, dst_fwd, verify=True)
        assert result["success"] is True
        assert result["total"] == 3
        assert (dst / "sub" / "c.txt").read_text(encoding="utf-8") == "nested content"

    def test_file_md5_with_posix_style_path(self, tmp_dir):
        """_file_md5 should work with forward-slash paths."""
        f = tmp_dir / "hash_posix.txt"
        f.write_text("hash me", encoding="utf-8")

        posix_path = str(f).replace("\\", "/")
        h = _file_md5(Path(posix_path))
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)

    def test_path_with_trailing_separator(self, tmp_dir):
        """Paths with trailing separators should normalize correctly."""
        src = tmp_dir / "trail_src"
        src.mkdir()
        (src / "f.txt").write_text("trailing", encoding="utf-8")

        dst = tmp_dir / "trail_dst"

        # Add trailing separator to source path
        trailing = str(src).replace("\\", "/") + "/"
        result = safe_copytree(trailing, str(dst))
        assert result["success"] is True
        assert (dst / "f.txt").read_text(encoding="utf-8") == "trailing"

    def test_path_with_dot_segments(self, tmp_dir):
        """Paths with ./ and ../ segments should resolve correctly."""
        src = tmp_dir / "dot_src"
        src.mkdir()
        (src / "f.txt").write_text("dots", encoding="utf-8")

        dst = tmp_dir / "dot_dst"

        # Use ./ prefix and ../ segments in path strings
        dot_path = str(tmp_dir) + "/./dot_extra/../dot_src"
        result = safe_copytree(dot_path, str(dst))
        assert result["success"] is True
        assert (dst / "f.txt").read_text(encoding="utf-8") == "dots"

    def test_case_sensitivity_detection(self, tmp_dir):
        """Detect whether current filesystem is case-sensitive.

        On Windows, the filesystem is case-insensitive so 'CaseTest.txt'
        and 'casetest.txt' point to the same file. On POSIX, they are
        two distinct files.
        """
        f = tmp_dir / "CaseTest.txt"
        f.write_text("case", encoding="utf-8")
        alt = tmp_dir / "casetest.txt"
        same_file = alt.exists()
        fs_case_sensitive = not same_file
        # Windows = case-insensitive (False), POSIX = case-sensitive (True)
        expected = (sys.platform != "win32")
        assert fs_case_sensitive == expected, (
            f"Expected case_sensitive={expected} on {sys.platform}, got {fs_case_sensitive}"
        )

    def test_verify_integrity_detects_case_mismatch(self, tmp_dir):
        """verify_tree_integrity should detect case-only file differences
        on case-sensitive filesystems and treat them as identical on
        case-insensitive filesystems."""
        src = tmp_dir / "case_src"
        src.mkdir()
        (src / "ReadMe.txt").write_text("case content", encoding="utf-8")

        dst = tmp_dir / "case_dst"
        shutil.copytree(str(src), str(dst))

        # Rename to different case
        (dst / "ReadMe.txt").rename(dst / "readme.txt")

        result = verify_tree_integrity(src, dst)

        if sys.platform == "win32":
            # On Windows, case-insensitive FS means 'readme.txt' and
            # 'ReadMe.txt' resolve to the same file; should not crash
            assert "error" not in result
        else:
            # On POSIX, different case = different file = extra or missing
            assert result["match"] is False
            assert (len(result["missing"]) + len(result["extra"])) == 1
