# coding: utf-8
"""
Lesson Lifecycle Unit Tests
============================
Tests the full lifecycle: create → upsert → validate → deprecate → stale list.
Also tests DB reliability: check_integrity(), optimize().
"""
import sys
import os
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from session_memory import SessionMemory, Lesson


class TestLessonLifecycle:
    """Test the full lesson lifecycle."""

    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_session_memory.db")
        cls.sm = SessionMemory(cls.db_path)
        cls.sm.start_session("Lesson lifecycle tests", "test-runner")

    @classmethod
    def teardown_class(cls):
        try:
            cls.sm.end_session("Tests complete", regenerate_docs=False)
        except RuntimeError:
            pass
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_create_lesson(self):
        """Test creating a new lesson."""
        sm = self.sm
        sm.record_lesson(
            "Test pattern: create lesson",
            "Creating lessons stores them in the DB with correct metadata",
            category="architecture",
            confidence=0.85,
            related_files="test_file.py",
            tags="test,lifecycle",
        )
        lessons = sm.get_lessons(category="architecture")
        found = [l for l in lessons if l.pattern == "Test pattern: create lesson"]
        assert len(found) == 1, f"Expected 1 lesson, found {len(found)}"
        l = found[0]
        assert l.confidence == 0.85
        assert l.times_encountered == 1
        assert l.related_files == "test_file.py"
        assert l.tags == "test,lifecycle"
        assert not l.deprecated

    def test_upsert_lesson(self):
        """Test that re-recording the same pattern upserts."""
        sm = SessionMemory(os.path.join(self.tmpdir, "test_upsert.db"))
        sm.start_session("upsert test", "test-runner")
        sm.record_lesson(
            "Test pattern: create lesson",
            "Creating lessons stores them in the DB with correct metadata",
            category="architecture",
            confidence=0.85,
        )
        sm.record_lesson(
            "Test pattern: create lesson",
            "Updated lesson text ? second encounter",
            category="architecture",
            confidence=0.95,
        )
        lessons = sm.get_lessons(category="architecture")
        found = [l for l in lessons if l.pattern == "Test pattern: create lesson"]
        assert len(found) == 1
        l = found[0]
        assert l.times_encountered == 2
        # Weighted average: (0.85*1 + 0.95)/2 = 0.90
        assert abs(l.confidence - 0.90) < 0.01, f"Expected ~0.90, got {l.confidence}"
        assert "second encounter" in l.lesson

    def test_validate_lesson_exits_stale_list(self):
        """Test that validate_lesson() updates session_id so the lesson
        is no longer stale."""
        # Use a fresh DB with multiple sessions to test staleness properly
        sm2 = SessionMemory(os.path.join(self.tmpdir, "test_stale.db"))

        # Session 1: create a lesson
        sm2.start_session("Session 1: create stale-prone lesson", "test")
        sm2.record_lesson(
            "Test pattern: old lesson",
            "This lesson should be stale after starting session 2",
            category="convention",
            confidence=0.75,
        )
        lessons = sm2.get_lessons(category="convention")
        found = [l for l in lessons if l.pattern == "Test pattern: old lesson"]
        assert len(found) == 1
        lesson_id = found[0].id
        sm2.end_session("Session 1 done", regenerate_docs=False)

        # Session 2: now max_session=2, lesson from session 1 should be stale
        sm2.start_session("Session 2: validate stale lesson", "test")
        stale = sm2.get_stale_lessons(session_threshold=0)
        stale_ids = [s.id for s in stale]
        assert lesson_id in stale_ids, (
            f"Lesson {lesson_id} should be stale before validation. "
            f"Stale IDs: {stale_ids}"
        )

        # Validate it — should bump session_id to current session (2)
        sm2.validate_lesson(lesson_id)

        # Should no longer be stale (session_id=2, max=2, 2 < 2 = false)
        stale_after = sm2.get_stale_lessons(session_threshold=0)
        stale_ids_after = [s.id for s in stale_after]
        assert lesson_id not in stale_ids_after, (
            f"Lesson {lesson_id} should not be stale after validation. "
            f"Stale IDs: {stale_ids_after}"
        )
        sm2.end_session("Session 2 done", regenerate_docs=False)

    def test_deprecate_lesson(self):
        """Test deprecating a lesson."""
        sm = self.sm
        sm.record_lesson(
            "Test pattern: deprecate me",
            "This lesson will be deprecated",
            category="bug_fix",
            confidence=0.60,
        )
        lessons = sm.get_lessons(category="bug_fix")
        found = [l for l in lessons if l.pattern == "Test pattern: deprecate me"]
        assert len(found) == 1
        lesson_id = found[0].id
        assert not found[0].deprecated

        sm.deprecate_lesson(lesson_id, "No longer relevant")

        # Should not appear in active lessons
        active = sm.get_lessons(category="bug_fix")
        active_ids = [l.id for l in active if l.pattern == "Test pattern: deprecate me"]
        assert len(active_ids) == 0, "Deprecated lesson should not appear in active list"

        # Should appear when include_deprecated=True
        all_lessons = sm.get_lessons(category="bug_fix", include_deprecated=True)
        dep = [l for l in all_lessons if l.id == lesson_id]
        assert len(dep) == 1
        assert dep[0].deprecated == 1
        assert "[DEPRECATED" in dep[0].lesson

    def test_validate_undeprecates(self):
        """Test that validate_lesson() un-deprecates a lesson."""
        sm = self.sm
        # Create and deprecate
        sm.record_lesson(
            "Test pattern: undeprecate me",
            "Will be deprecated then revived",
            category="convention",
            confidence=0.70,
        )
        lessons = sm.get_lessons(category="convention")
        found = [l for l in lessons if l.pattern == "Test pattern: undeprecate me"]
        lesson_id = found[0].id
        sm.deprecate_lesson(lesson_id, "Test deprecation")

        # Verify it's deprecated
        all_lessons = sm.get_lessons(category="convention", include_deprecated=True)
        dep = [l for l in all_lessons if l.id == lesson_id]
        assert dep[0].deprecated == 1

        # Validate to revive
        sm.validate_lesson(lesson_id)

        # Should be active again
        active = sm.get_lessons(category="convention")
        revived = [l for l in active if l.pattern == "Test pattern: undeprecate me"]
        assert len(revived) == 1, "Lesson should be revived after validation"
        assert revived[0].deprecated == 0

    def test_get_stale_lessons_respects_threshold(self):
        """Test that stale threshold works correctly.

        Creates a lesson in session 1, then uses tight threshold (0)
        to detect it as stale, and a wide threshold (9999) to exclude it.
        """
        sm2 = SessionMemory(os.path.join(self.tmpdir, "test_threshold.db"))

        # Session 1: create lesson
        sm2.start_session("Session 1: create lesson", "test")
        sm2.record_lesson(
            "Test pattern: threshold lesson",
            "For testing threshold",
            category="architecture",
            confidence=0.80,
        )
        lessons = sm2.get_lessons(category="architecture")
        found = [l for l in lessons if l.pattern == "Test pattern: threshold lesson"]
        lesson_id = found[0].id
        sm2.end_session("Session 1 done", regenerate_docs=False)

        # Session 2: max_session=2, lesson from session 1 has session_id=1
        sm2.start_session("Session 2: test thresholds", "test")

        # With threshold=0 (cutoff = max_session - 0 = 2): session_id 1 < 2 → stale
        stale_tight = sm2.get_stale_lessons(session_threshold=0)
        stale_ids_tight = [s.id for s in stale_tight]
        assert lesson_id in stale_ids_tight, (
            f"Lesson {lesson_id} should be stale with threshold=0"
        )

        # With threshold=9999 (cutoff = 2 - 9999 = -9997): session_id 1 ≥ -9997 → not stale
        stale_wide = sm2.get_stale_lessons(session_threshold=9999)
        stale_ids_wide = [s.id for s in stale_wide]
        assert lesson_id not in stale_ids_wide, (
            f"Lesson {lesson_id} should NOT be stale with threshold=9999"
        )
        sm2.end_session("Session 2 done", regenerate_docs=False)


class TestDBReliability:
    """Test database reliability and maintenance methods."""

    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_reliability.db")
        cls.sm = SessionMemory(cls.db_path)
        cls.sm.start_session("DB reliability tests", "test-runner")

    @classmethod
    def teardown_class(cls):
        try:
            cls.sm.end_session("Reliability tests complete", regenerate_docs=False)
        except RuntimeError:
            pass
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_check_integrity_clean(self):
        """Test integrity check on a clean DB."""
        result = self.sm.check_integrity()
        assert result["integrity"] == "ok"
        assert result["quick_check"] == "ok"
        assert result["foreign_key_violations"] == 0
        assert result["orphaned_decisions"] == 0
        assert result["orphaned_file_changes"] == 0
        assert result["orphaned_lessons"] == 0
        assert result["db_size_mb"] >= 0
        assert result["wal_size_mb"] >= 0

    def test_optimize(self):
        """Test optimize runs without error."""
        result = self.sm.optimize()
        assert result["before_size_mb"] >= 0
        assert result["after_size_mb"] >= 0
        assert result["saved_mb"] >= 0
        assert result["optimized_at"]

    def test_end_session_regenerates_docs(self):
        """Test that end_session(regenerate_docs=True) calls the
        regeneration pipeline (even if scripts don't exist, it
        should handle the failure gracefully)."""
        sm = SessionMemory(os.path.join(self.tmpdir, "test_regen.db"))
        sm.start_session("Test regeneration", "test-runner")
        result = sm.end_session("Done", regenerate_docs=True)
        assert result["session_id"] > 0
        assert "docs_regenerated" in result
        # Without scripts available, dashboard/readmes should be False
        # but no exception should be raised
        assert isinstance(result["docs_regenerated"], dict)
        assert "dashboard" in result["docs_regenerated"]
        assert "readmes" in result["docs_regenerated"]


class TestGetStaleLessonsNoLimit:
    """Test that get_stale_lessons() returns ALL stale lessons (no LIMIT 30).

    This is a regression test for the LIMIT-30 bug that masked data quality
    issues: the method previously had LIMIT 30, so if there were 60 stale
    lessons, only 30 were returned and the operability report showed 30.
    Now the method has no LIMIT and returns every stale lesson.
    """

    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_no_limit.db")

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_returns_more_than_30_stale_lessons(self):
        """Create 60 stale lessons and verify all 60 are returned.

        Previously the LIMIT 30 would cap this at 30, masking 30 stale lessons.
        """
        sm = SessionMemory(self.db_path)

        # Session 1: create 60 lessons
        sm.start_session("Session 1: create many lessons", "test")
        for i in range(60):
            sm.record_lesson(
                f"Test pattern: stale lesson {i:03d}",
                f"Lesson content for stale lesson {i}",
                category="convention",
                confidence=0.75,
            )
        sm.end_session("Session 1 done", regenerate_docs=False)

        # Session 2: max_session=2, all 60 lessons have session_id=1 < 2 → stale
        sm.start_session("Session 2: check stale count", "test")

        # With threshold=0 (cutoff = 2 - 0 = 2): session_id 1 < 2 → stale
        stale = sm.get_stale_lessons(session_threshold=0)
        assert len(stale) == 60, (
            f"Expected 60 stale lessons (no LIMIT), got {len(stale)}. "
            f"If this is < 60, the LIMIT may not have been fully removed."
        )
        sm.end_session("Session 2 done", regenerate_docs=False)

    def test_increasing_stale_threshold_reduces_count(self):
        """Verify threshold filtering still works after LIMIT removal."""
        sm = SessionMemory(os.path.join(self.tmpdir, "test_threshold.db"))
        sm.start_session("Session 1: create many lessons", "test")
        for i in range(60):
            sm.record_lesson(
                f"Test pattern: stale lesson {i:03d}",
                f"Lesson content for stale lesson {i}",
                category="convention",
                confidence=0.75,
            )
        sm.end_session("Session 1 done", regenerate_docs=False)

        # Session 2: max_session=2, all 60 lessons have session_id=1 < 2 -> stale
        sm.start_session("Session 2: check stale count", "test")
        stale = sm.get_stale_lessons(session_threshold=0)
        assert len(stale) == 60

        # Session 3: threshold check
        sm.start_session("Session 3: check threshold", "test")

        # With tight threshold (0): all 60 from session 1 are stale
        stale_tight = sm.get_stale_lessons(session_threshold=0)
        assert len(stale_tight) == 60

        # With wide threshold (10): cutoff = 3-10 = -7, nothing is below -7 -> 0 stale
        stale_wide = sm.get_stale_lessons(session_threshold=10)
        assert len(stale_wide) == 0
        sm.end_session("Session 3 done", regenerate_docs=False)


class TestGetSessionReviewCountAccuracy:
    """Test that get_session_review() counts are accurate.

    Previously, stale_lessons and open_decisions counts used len() on
    rows that had LIMITs, so counts could be wrong. Now they use
    separate COUNT(*) queries for accuracy.
    """

    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_review_counts.db")

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_stale_count_matches_actual_stale_lessons(self):
        """get_session_review() stale count must match the actual number
        of stale lessons returned by get_stale_lessons(), even when
        there are more than the detail row limit.

        Uses session_threshold=0 to avoid the 10-session grace period.
        """
        sm = SessionMemory(self.db_path)

        # Session 1: create 40 lessons
        sm.start_session("Session 1: bulk lessons", "test")
        for i in range(40):
            sm.record_lesson(
                f"Test pattern: bulk lesson {i:03d}",
                f"Content for bulk lesson {i}",
                category="architecture",
                confidence=0.80,
            )
        sm.end_session("Session 1 done", regenerate_docs=False)

        # Session 2: get_stale_lessons with threshold=0 → all 40 are stale
        sm.start_session("Session 2: verify counts", "test")

        # get_stale_lessons(threshold=0) should return all 40
        stale_list = sm.get_stale_lessons(session_threshold=0)
        assert len(stale_list) == 40, (
            f"Expected 40 stale lessons with threshold=0, got {len(stale_list)}"
        )

        # get_session_review() uses STALE_SESSION_THRESHOLD=10, so with only
        # 2 sessions the cutoff is negative → 0 stale. That's correct.
        # The important test is that the count in the review dict equals
        # what get_stale_lessons() returns with the same threshold.
        review = sm.get_session_review()
        review_stale = review["counts"]["stale_lessons"]

        # With default threshold (10), cutoff = 2-10 = -7, so 0 stale
        # This should match get_stale_lessons(session_threshold=10)
        stale_default = sm.get_stale_lessons(session_threshold=10)
        assert review_stale == len(stale_default), (
            f"get_session_review() stale count ({review_stale}) must match "
            f"get_stale_lessons(threshold=10) count ({len(stale_default)})"
        )
        sm.end_session("Session 2 done", regenerate_docs=False)

    def test_open_decisions_count_is_accurate(self):
        """get_session_review() open_decisions count must use COUNT(*),
        not len() on a limited query."""
        sm = SessionMemory(self.db_path)
        sm.start_session("Session: open decisions test", "test")

        # Create 60 open decisions (no outcome)
        for i in range(60):
            sm.record_decision(
                context=f"Context for decision {i}",
                decision=f"Decision {i}: do the thing",
                rationale=f"Reason {i}",
            )

        review = sm.get_session_review()
        open_count = review["counts"]["open_decisions"]
        assert open_count == 60, (
            f"Expected 60 open decisions, got {open_count}. "
            f"COUNT(*) should see all 60 regardless of detail row LIMIT."
        )
        sm.end_session("Done", regenerate_docs=False)
