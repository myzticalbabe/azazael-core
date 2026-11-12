# coding: utf-8
"""Tests for sync_skills.py — Skills Propagation Automation (RULE 0R).

Tests the current SkillsSync class-based API:
    - sync_all()        full sync (dry-run, created, updated, skipped, deleted)
    - sync_one()        single skill sync (success, error, dry-run)
    - get_gap_report()  missing/stale skill detection
"""

import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import SCRIPTS.sync_skills as _sync_mod
sync_all = _sync_mod.sync_all
sync_one = _sync_mod.sync_one
get_gap_report = _sync_mod.get_gap_report


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_sync_singleton():
    """Reset the SkillsSync singleton before each test to prevent state leakage."""
    _sync_mod._sync_instance = None
    yield


@pytest.fixture
def fake_project():
    """Create a temporary project structure with azazael/skills/ and .agents/skills/.

    Returns (project_root, skills_src, skills_dest) so tests can set up
    their own skill files and mock _resolve_paths to point here.
    """
    root = Path(tempfile.mkdtemp(prefix="test_sync_skills_"))
    skills_src = root / "azazael" / "skills"
    skills_dest = root / ".agents" / "skills"
    skills_src.mkdir(parents=True, exist_ok=True)
    skills_dest.mkdir(parents=True, exist_ok=True)
    yield root, skills_src, skills_dest
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def pop_skills_dir(fake_project):
    """Create a few fake skill .md files in azazael/skills/.

    Returns (project_root, skills_src, skills_dest) with two skills created.
    """
    root, skills_src, skills_dest = fake_project
    (skills_src / "azazael-test-one.md").write_text("# Test Skill One\n\nSimple test skill.", encoding="utf-8")
    (skills_src / "azazael-test-two.md").write_text("# Test Skill Two\n\nAnother test skill.", encoding="utf-8")
    return root, skills_src, skills_dest


# ── Helpers ─────────────────────────────────────────────────────────


def _patch_paths(skills_src, skills_dest):
    """Patch _resolve_paths in SkillsSync to return controlled paths."""
    return patch("SCRIPTS.sync_skills._resolve_paths", return_value=(skills_src, skills_dest))


def _run_sync(skills_src, skills_dest, **kwargs):
    """Run sync_all with patched paths and return stats."""
    with _patch_paths(skills_src, skills_dest):
        return sync_all(**kwargs)


def _run_one(skills_src, skills_dest, skill_name, **kwargs):
    """Run sync_one with patched paths and return result."""
    with _patch_paths(skills_src, skills_dest):
        return sync_one(skill_name, **kwargs)


def _get_gap(skills_src, skills_dest):
    """Run get_gap_report with patched paths and return report."""
    with _patch_paths(skills_src, skills_dest):
        return get_gap_report()


# ── sync_all Tests ──────────────────────────────────────────────────


class TestSyncAll:
    def test_missing_source_dir(self, fake_project):
        """Missing azazael/skills/ directory should return zero stats with no errors."""
        root, skills_src, skills_dest = fake_project
        # Remove the skills src dir
        shutil.rmtree(skills_src)
        stats = _run_sync(skills_src, skills_dest)
        assert stats["errors"] == 0
        assert stats["created"] == 0
        assert stats["updated"] == 0
        assert stats["skipped"] == 0

    def test_empty_skills_dir(self, fake_project):
        """Empty skills directory should return zero stats with no errors."""
        root, skills_src, skills_dest = fake_project
        stats = _run_sync(skills_src, skills_dest)
        assert stats["created"] == 0
        assert stats["updated"] == 0
        assert stats["skipped"] == 0
        assert stats["errors"] == 0

    def test_basic_sync_creates_files(self, pop_skills_dir):
        """Basic sync should create SKILL.md files in .agents/skills/<name>/."""
        root, skills_src, skills_dest = pop_skills_dir
        stats = _run_sync(skills_src, skills_dest)
        assert stats["errors"] == 0
        assert stats["created"] >= 2  # At least test-one and test-two
        # Verify files exist
        one = skills_dest / "azazael-test-one" / "SKILL.md"
        two = skills_dest / "azazael-test-two" / "SKILL.md"
        assert one.exists()
        assert two.exists()
        assert one.read_text(encoding="utf-8") == "# Test Skill One\n\nSimple test skill."

    def test_sync_updates_changed_file(self, pop_skills_dir):
        """Sync should update SKILL.md when source changes."""
        root, skills_src, skills_dest = pop_skills_dir
        # First sync
        _run_sync(skills_src, skills_dest)

        # Modify source
        src = skills_src / "azazael-test-one.md"
        src.write_text("# Updated Skill One\n\nChanged content.", encoding="utf-8")

        # Second sync
        stats = _run_sync(skills_src, skills_dest)
        assert stats["updated"] >= 1
        assert stats["errors"] == 0

        # Verify target was updated
        target = skills_dest / "azazael-test-one" / "SKILL.md"
        assert target.read_text(encoding="utf-8") == "# Updated Skill One\n\nChanged content."

    def test_sync_unchanged_file(self, pop_skills_dir):
        """Sync should report skipped when source matches target."""
        root, skills_src, skills_dest = pop_skills_dir
        # First sync creates files
        _run_sync(skills_src, skills_dest)
        # Second sync should find nothing changed
        stats = _run_sync(skills_src, skills_dest)
        assert stats["updated"] == 0
        assert stats["skipped"] >= 2
        assert stats["errors"] == 0

    def test_dry_run_does_not_create_files(self, pop_skills_dir):
        """Dry-run should not create any files on disk."""
        root, skills_src, skills_dest = pop_skills_dir
        stats = _run_sync(skills_src, skills_dest, dry_run=True)
        assert stats["created"] >= 2  # Reports as created but doesn't write
        assert stats["errors"] == 0
        # Verify NO files were created
        one = skills_dest / "azazael-test-one" / "SKILL.md"
        assert not one.exists()

    def test_dry_run_with_existing_target(self, pop_skills_dir):
        """Dry-run with existing target should not modify it."""
        root, skills_src, skills_dest = pop_skills_dir
        # First real sync
        _run_sync(skills_src, skills_dest)
        target = skills_dest / "azazael-test-one" / "SKILL.md"
        original_content = target.read_text(encoding="utf-8")

        # Modify source
        src = skills_src / "azazael-test-one.md"
        src.write_text("# Different content", encoding="utf-8")

        # Dry-run sync should NOT overwrite
        stats = _run_sync(skills_src, skills_dest, dry_run=True)
        assert stats["updated"] >= 1
        assert target.read_text(encoding="utf-8") == original_content  # Unchanged

    def test_new_directory_counted(self, pop_skills_dir):
        """First sync should count new directories as created."""
        root, skills_src, skills_dest = pop_skills_dir
        stats = _run_sync(skills_src, skills_dest)
        assert stats["created"] >= 2
        # Verify dirs exist
        one_dir = skills_dest / "azazael-test-one"
        assert one_dir.is_dir()

    def test_handles_special_chars_in_skill_name(self, fake_project):
        """Skill names with special characters should sync correctly."""
        root, skills_src, skills_dest = fake_project
        (skills_src / "azazael-fix-data-recording.md").write_text("# Data Fix", encoding="utf-8")
        (skills_src / "azazael_314t_master.md").write_text("# 3.14t Master", encoding="utf-8")

        stats = _run_sync(skills_src, skills_dest)
        assert stats["errors"] == 0
        assert stats["created"] == 2

        d1 = skills_dest / "azazael-fix-data-recording" / "SKILL.md"
        d2 = skills_dest / "azazael_314t_master" / "SKILL.md"
        assert d1.exists()
        assert d2.exists()

    def test_cleans_up_stale_skills(self, pop_skills_dir):
        """sync_all should delete stale skills in dest that have no source."""
        root, skills_src, skills_dest = pop_skills_dir
        # First sync creates the two skills
        _run_sync(skills_src, skills_dest)
        assert (skills_dest / "azazael-test-one" / "SKILL.md").exists()

        # Remove source file for test-one
        (skills_src / "azazael-test-one.md").unlink()

        # Second sync should delete the stale one
        stats = _run_sync(skills_src, skills_dest)
        assert stats["deleted"] == 1
        assert not (skills_dest / "azazael-test-one").exists()
        assert (skills_dest / "azazael-test-two" / "SKILL.md").exists()

    # ── write/read failure handling ────────────────────────────────

    def test_write_failure_on_create(self, pop_skills_dir):
        """When write_bytes fails on a new file, error should be reported."""
        root, skills_src, skills_dest = pop_skills_dir
        with patch.object(Path, "write_text", side_effect=OSError("No space left on device")):
            with _patch_paths(skills_src, skills_dest):
                stats = sync_all()

        assert stats["errors"] >= 1, f"Expected >=1 error, got {stats}"
        assert stats["created"] == 0, f"Expected 0 created, got {stats}"
        # No target files were created since write failed
        one = skills_dest / "azazael-test-one" / "SKILL.md"
        two = skills_dest / "azazael-test-two" / "SKILL.md"
        assert not one.exists()
        assert not two.exists()

    def test_write_failure_on_update(self, pop_skills_dir):
        """When write fails on update, error reported and original content preserved."""
        root, skills_src, skills_dest = pop_skills_dir
        # First sync creates files
        _run_sync(skills_src, skills_dest)

        # Modify source so update is triggered
        src = skills_src / "azazael-test-one.md"
        src.write_text("# Updated Skill One\n\nChanged content.", encoding="utf-8")

        target = skills_dest / "azazael-test-one" / "SKILL.md"
        original_content = target.read_text(encoding="utf-8")

        with patch.object(Path, "write_text", side_effect=OSError("No space left on device")):
            with _patch_paths(skills_src, skills_dest):
                stats = sync_all()

        assert stats["errors"] >= 1, f"Expected >=1 error, got {stats}"
        assert stats["updated"] == 0, f"Expected 0 updated, got {stats}"
        # Original content preserved unchanged
        assert target.read_text(encoding="utf-8") == original_content

    def test_read_failure_on_create(self, pop_skills_dir):
        """When read fails on source, error should be reported."""
        root, skills_src, skills_dest = pop_skills_dir
        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            with _patch_paths(skills_src, skills_dest):
                stats = sync_all()

        assert stats["errors"] >= 1, f"Expected >=1 error, got {stats}"
        assert stats["created"] == 0, f"Expected 0 created, got {stats}"
        # No target files created since read failed before write
        one = skills_dest / "azazael-test-one" / "SKILL.md"
        assert not one.exists()


# ── sync_one Tests ──────────────────────────────────────────────────


class TestSyncOne:
    def test_sync_one_creates_file(self, pop_skills_dir):
        """sync_one should create a single skill file."""
        root, skills_src, skills_dest = pop_skills_dir
        ok = _run_one(skills_src, skills_dest, "azazael-test-one")
        assert ok is True
        target = skills_dest / "azazael-test-one" / "SKILL.md"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "# Test Skill One\n\nSimple test skill."

    def test_sync_one_dry_run(self, pop_skills_dir):
        """sync_one dry-run should not create files."""
        root, skills_src, skills_dest = pop_skills_dir
        ok = _run_one(skills_src, skills_dest, "azazael-test-one", dry_run=True)
        assert ok is True
        target = skills_dest / "azazael-test-one" / "SKILL.md"
        assert not target.exists()

    def test_sync_one_missing_source(self, pop_skills_dir):
        """sync_one with missing source should return False."""
        root, skills_src, skills_dest = pop_skills_dir
        ok = _run_one(skills_src, skills_dest, "azazael-nonexistent")
        assert ok is False

    def test_sync_one_read_error(self, pop_skills_dir):
        """sync_one with read error should return False."""
        root, skills_src, skills_dest = pop_skills_dir
        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            ok = _run_one(skills_src, skills_dest, "azazael-test-one")
        assert ok is False
        target = skills_dest / "azazael-test-one" / "SKILL.md"
        assert not target.exists()

    def test_sync_one_write_error(self, pop_skills_dir):
        """sync_one with write error should return False."""
        root, skills_src, skills_dest = pop_skills_dir
        with patch.object(Path, "write_text", side_effect=OSError("No space left on device")):
            ok = _run_one(skills_src, skills_dest, "azazael-test-one")
        assert ok is False
        target = skills_dest / "azazael-test-one" / "SKILL.md"
        assert not target.exists()


# ── get_gap_report Tests ────────────────────────────────────────────


class TestGapReport:
    def test_full_match(self, pop_skills_dir):
        """get_gap_report should show no gaps when all skills are synced."""
        root, skills_src, skills_dest = pop_skills_dir
        # Sync first
        _run_sync(skills_src, skills_dest)
        # Check gap
        gap = _get_gap(skills_src, skills_dest)
        assert gap["total_source"] == 2
        assert gap["total_dest"] == 2
        assert gap["missing_count"] == 0
        assert gap["stale_count"] == 0
        assert gap["sync_pct"] == 100.0

    def test_missing_skills(self, pop_skills_dir):
        """get_gap_report should show skills in source but not dest."""
        root, skills_src, skills_dest = pop_skills_dir
        # Don't sync yet — all are missing
        gap = _get_gap(skills_src, skills_dest)
        assert gap["total_source"] == 2
        assert gap["total_dest"] == 0
        assert gap["missing_count"] == 2
        assert "azazael-test-one" in gap["missing_in_dest"]
        assert "azazael-test-two" in gap["missing_in_dest"]

    def test_stale_skills(self, pop_skills_dir):
        """get_gap_report should show skills in dest but not source."""
        root, skills_src, skills_dest = pop_skills_dir
        # Sync first, then remove a source
        _run_sync(skills_src, skills_dest)
        (skills_src / "azazael-test-one.md").unlink()

        gap = _get_gap(skills_src, skills_dest)
        assert gap["total_source"] == 1
        assert gap["total_dest"] == 2
        assert gap["stale_count"] == 1
        assert "azazael-test-one" in gap["stale_in_dest"]
        # 2 dest / 1 source = 200% (stale skills inflate percentage)
        assert gap["sync_pct"] == 200.0

    def test_empty_both(self, fake_project):
        """get_gap_report with no skills in either dir."""
        root, skills_src, skills_dest = fake_project
        gap = _get_gap(skills_src, skills_dest)
        assert gap["total_source"] == 0
        assert gap["total_dest"] == 0
        assert gap["missing_count"] == 0
        assert gap["stale_count"] == 0
        assert gap["sync_pct"] == 0.0
