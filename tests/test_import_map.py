"""Tests for the AST import graph (2026-09-12) and the backlog triage.

The regression these guard: coverage was decided by "does the substring
`providers` appear anywhere in a test file?", which is true of a docstring and
blind to a module reached through a subprocess. The import graph must resolve
real imports, resolve aliases, and see a file named by a path literal.
"""
import json
import os
import sys
import unittest

_scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import import_map  # noqa: E402
import triage_test_backlog as triage  # noqa: E402


class TestImportGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = import_map.get_map()

    def test_parses_every_python_file(self):
        self.assertGreater(len(self.m.files), 500)
        self.assertEqual(self.m.parse_errors, {},
                         "every project .py must parse: %r"
                         % list(self.m.parse_errors)[:5])

    def test_resolves_a_full_dotted_src_path(self):
        rel = "src/CORE/WORMS/expansion/evolution.py"
        self.assertIn(rel, self.m.files)
        self.assertIn(rel, self.m.resolve("CORE.WORMS.expansion.evolution"))

    def test_resolves_a_basename_alias(self):
        """Suites put their directory on sys.path, so `import budget_policy`."""
        self.assertIn("scripts/budget_policy.py",
                      self.m.resolve("budget_policy"))

    def test_unknown_names_resolve_to_nothing(self):
        self.assertEqual(self.m.resolve("no_such_module_xyz"), set())

    def test_records_real_importers(self):
        module = "src/CORE/WORMS/expansion/proteus_hooks.py"
        importers = self.m.importers(module)
        self.assertTrue(any(f.startswith("tests/") for f in importers),
                        "the module's own suite should import it: %r" % importers)

    def test_sees_a_subprocess_style_reference(self):
        """test_scanner runs scanner.py as a child process, never importing it."""
        module = "src/CORE/compliance/scanner.py"
        refs = self.m.referenced_by(module)
        self.assertIn("tests/test_scanner.py", refs)
        self.assertIn(module, self.m.coverage()["path_referenced"])
        # and the resolver really does map a path literal to a project file
        self.assertEqual(
            self.m.resolve_path_literal("src/CORE/compliance/scanner.py",
                                        "tests/test_scanner.py"), module)

    def test_a_bare_mention_is_not_a_reference(self):
        """Regression: a string literal is a MENTION, not a load.

        This very file names src/tmp_verify.py in an assertion. Counting that
        as a reference made a dead module look live - the same false-positive
        class as the substring testmap.
        """
        self.assertEqual(self.m.referenced_by("src/tmp_verify.py"), set())

    def test_coverage_is_split_by_evidence(self):
        cov = self.m.coverage()
        self.assertGreater(cov["covered"], 0)
        self.assertEqual(cov["src_modules"],
                         cov["covered"] + len(cov["uncovered"]))
        self.assertGreater(len(cov["via_import"]), 50)

    def test_duplicate_basenames_are_not_collapsed(self):
        """The old testmap kept a SET of basenames, so same-named modules
        merged. The import graph is keyed by path."""
        src = self.m.src_modules()
        bases = {}
        for rel in src:
            bases.setdefault(os.path.basename(rel), []).append(rel)
        dupes = {b: v for b, v in bases.items() if len(v) > 1}
        self.assertTrue(dupes, "this repo has same-named modules")
        path = sorted(dupes)[0]
        self.assertEqual(len(set(dupes[path])), len(dupes[path]))

    def test_is_live_distinguishes_an_orphan(self):
        live, why = self.m.is_live("src/CORE/WORMS/expansion/evolution.py")
        self.assertTrue(live)
        self.assertNotEqual(why, "no importer, no reference, not runnable")
        # a temp scratch file has no importer, no run-reference, no guard
        orphan, _ = self.m.is_live("src/tmp_verify.py")
        self.assertFalse(orphan)

    def test_is_live_matches_its_own_criteria(self):
        """is_live must be exactly "has an importer, a run-reference, or is a
        runnable entry point" - no surprises in either direction."""
        for rel in self.m.src_modules():
            live, _ = self.m.is_live(rel)
            evidence = bool(self.m.importers(rel)) or bool(self.m.referenced_by(rel))
            if not evidence:
                self.assertFalse(live, "%s has no evidence but is_live" % rel)


class TestTriage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # state/test_backlog.json is produced by scripts/repo_audit.py; without
        # it there is nothing to triage. Skip honestly rather than error on a
        # missing external artifact.
        if not os.path.exists(triage.BACKLOG):
            raise unittest.SkipTest(
                "no %s - run scripts/repo_audit.py first" % triage.BACKLOG
            )
        cls.result = triage.triage()

    def test_zero_modules_left_unknown(self):
        self.assertEqual(self.result["unknown_count"], 0)
        verdicts = {r["verdict"] for r in self.result["rows"]}
        self.assertTrue(verdicts <= {"test", "delete", "vendor"},
                        "unexpected verdicts: %r" % verdicts)

    def test_counts_are_exhaustive(self):
        counts = self.result["counts"]
        self.assertEqual(sum(counts.values()), self.result["total"])

    def test_every_row_carries_evidence(self):
        for r in self.result["rows"]:
            self.assertTrue(r.get("evidence"), "no evidence for %r" % r)
            self.assertTrue(r.get("path"))
            self.assertNotIn("unknown", r["verdict"])

    def test_delete_candidates_are_not_imported(self):
        """A module something imports must never be called dead."""
        m = import_map.get_map()
        for rel in self.result["delete_candidates"]:
            self.assertFalse(m.importers(rel),
                             "%s has importers but was marked delete" % rel)
            self.assertFalse(m.referenced_by(rel))
            self.assertNotIn(rel, m.has_main)

    def test_delete_candidates_are_flagged_for_review(self):
        for r in self.result["rows"]:
            if r["verdict"] == "delete":
                self.assertTrue(r.get("requires_review"),
                                "deletion must stay review-gated")

    def test_generated_scaffolding_is_not_called_vendored(self):
        kinds = {r.get("kind") for r in self.result["rows"]
                 if r["verdict"] == "vendor"}
        self.assertTrue(kinds <= {"vendored", "generated"})
        self.assertIn("generated", self.result["vendor_kind"])

    def test_vendor_detection_reads_the_header(self):
        kind, why = triage._is_vendored("src/ORCHESTRATION/scripts/check_links.py")
        self.assertEqual(kind, "generated")
        self.assertIn("scaffolding", why)

    def test_vendor_detection_accepts_a_vendor_path(self):
        kind, why = triage._is_vendored("src/third_party/lib/thing.py")
        self.assertEqual(kind, "vendored")
        self.assertIn("third_party", why)


if __name__ == "__main__":
    unittest.main()
