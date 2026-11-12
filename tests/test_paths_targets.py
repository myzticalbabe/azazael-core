# coding: utf-8
"""
Unit tests for CORE/paths/targets.py target-list builders:
  get_desktop_targets, get_local_targets, get_trim_targets, get_error_targets

Covers the canonical-name derivation, the shared _cache memoization + copy
semantics, and the trim/error filtering behavior. cwd-independent and safe
to run under Python 3.15t (stdlib + the paths package only).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from pathlib import Path

from CORE.paths import (
    DESKTOP_TARGET_NAMES,
    LOCAL_TARGET_SUBDIRS,
    get_desktop_targets,
    get_local_targets,
    get_trim_targets,
    get_error_targets,
    get_desktop_dir,
    get_src_dir,
)
from CORE.paths.state import _cache, clear_cache


class TestGetDesktopTargets(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def test_returns_one_entry_per_canonical_name(self):
        t = get_desktop_targets()
        self.assertEqual(len(t), len(DESKTOP_TARGET_NAMES))
        for name in DESKTOP_TARGET_NAMES:
            self.assertTrue(any(t.endswith(os.path.normpath(name)) for t in t))

    def test_all_entries_rooted_at_desktop_dir(self):
        desk = str(get_desktop_dir()).replace("\\", "/")
        for t in get_desktop_targets():
            self.assertTrue(t.replace("\\", "/").startswith(desk))

    def test_correct_basename_mapping(self):
        got = {os.path.basename(t.rstrip("/\\")) for t in get_desktop_targets()}
        self.assertEqual(got, set(DESKTOP_TARGET_NAMES))

    def test_memoized_and_returns_copy(self):
        a = get_desktop_targets()
        b = get_desktop_targets()
        self.assertEqual(a, b)
        self.assertIsNot(a, b)      # fresh list object each call
        self.assertIn("desktop_targets", _cache)
        # mutating the returned list must not corrupt the cache
        a.clear()
        self.assertEqual(len(get_desktop_targets()), len(DESKTOP_TARGET_NAMES))


class TestGetLocalTargets(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def test_returns_one_entry_per_subdir(self):
        t = get_local_targets()
        self.assertEqual(len(t), len(LOCAL_TARGET_SUBDIRS))

    def test_first_entry_is_src_dir_itself(self):
        t = get_local_targets()
        self.assertEqual(os.path.normpath(t[0]), os.path.normpath(str(get_src_dir())))

    def test_subdir_entries_are_src_relative(self):
        src = os.path.normpath(str(get_src_dir()))
        for s, t in zip(LOCAL_TARGET_SUBDIRS, get_local_targets()):
            if s:
                expected = os.path.normpath(os.path.join(src, s))
                self.assertEqual(os.path.normpath(t), expected)

    def test_memoized_and_returns_copy(self):
        a = get_local_targets()
        self.assertIsNot(a, get_local_targets())
        self.assertIn("local_targets", _cache)
        self.assertEqual(a, get_local_targets())


class TestGetTrimTargets(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def test_includes_local_src_root(self):
        t = get_trim_targets()
        self.assertIn(os.path.normpath(str(get_src_dir())), [os.path.normpath(x) for x in t])

    def test_only_local_root_included(self):
        """trim only appends local_targets[:1] (just the src root)."""
        local_members = {os.path.normpath(x) for x in get_local_targets()}
        trim_members = {os.path.normpath(x) for x in get_trim_targets()}
        # all trim members must come from desktop(u) + local[:1]
        allowed = {os.path.normpath(x) for x in get_desktop_targets()}
        allowed.add(os.path.normpath(str(get_src_dir())))
        self.assertTrue(trim_members <= allowed)

    def test_no_duplicate_src_root(self):
        t = [os.path.normpath(x) for x in get_trim_targets()]
        self.assertEqual(len(t), len(set(t)))

    def test_desktop_members_filtered_to_existing(self):
        trim = get_trim_targets()
        for x in trim:
            self.assertTrue(Path(x).exists())


class TestGetErrorTargets(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def test_includes_all_local_targets(self):
        local = {os.path.normpath(x) for x in get_local_targets()}
        err = {os.path.normpath(x) for x in get_error_targets()}
        self.assertTrue(local <= err)

    def test_superset_of_trim(self):
        err = {os.path.normpath(x) for x in get_error_targets()}
        trim = {os.path.normpath(x) for x in get_trim_targets()}
        # error adds the non-root local subdirs on top of trim's set
        self.assertTrue(trim <= err)

    def test_desktop_members_filtered_to_existing(self):
        for x in get_error_targets():
            if os.path.normpath(x) not in [
                os.path.normpath(p) for p in get_local_targets()
            ]:
                self.assertTrue(Path(x).exists())

    def test_distinct_from_trim(self):
        err = set(os.path.normpath(x) for x in get_error_targets())
        trim = set(os.path.normpath(x) for x in get_trim_targets())
        self.assertGreater(len(err), len(trim))


class TestCacheIsolation(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def test_clear_cache_invalidates_all(self):
        get_desktop_targets()
        get_local_targets()
        self.assertIn("desktop_targets", _cache)
        self.assertIn("local_targets", _cache)
        clear_cache()
        self.assertEqual(_cache, {})

    def test_calls_recompute_after_clear(self):
        a = get_desktop_targets()
        clear_cache()
        b = get_desktop_targets()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()