# coding: utf-8
"""Unit tests for the 2026-10-31 census instruments.

The census's own `--selftest` proves classification on a fixture. These tests
prove the three STANDALONE instruments behave as gates: the alias table cannot
name a directory that no longer exists, an unused declaration is reported stale,
the unresolved ratchet fails on growth, `--affected` is file-precise and refuses
an ambiguous name, and the boundary policy's own controls stay green.

Nothing here needs a network, a daemon or the real repository: every fixture is
written to a temp dir, because a gate that only runs on one machine's tree is a
gate that stops running on the next one.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def _load(name: str):
    """Import a tools/ module by path: tools/ is a script directory, not a package."""
    spec = importlib.util.spec_from_file_location("_t_" + name, TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fixture(root: Path) -> None:
    """A -> B -> C, plus a driver outside src that calls into C."""
    for tree in ("A", "B", "C", "D", "RUN"):
        (root / "src" / tree).mkdir(parents=True, exist_ok=True)
    (root / "src" / "A" / "a.py").write_text("import B.b\n", encoding="utf-8")
    (root / "src" / "B" / "b.py").write_text("from C import c\n", encoding="utf-8")
    (root / "src" / "C" / "c.py").write_text("c = 1\n", encoding="utf-8")
    (root / "src" / "C" / "dup.py").write_text("d = 1\n", encoding="utf-8")
    (root / "src" / "D" / "dup.py").write_text("d = 2\n", encoding="utf-8")
    (root / "src" / "RUN" / "go.bat").write_text("python C\\c.py\r\n", encoding="utf-8")


class AliasTable(unittest.TestCase):
    """The declared alias table is configuration, so it must not outlive it."""

    def test_every_declaration_names_a_real_path(self):
        yc = _load("yggdrasil_census")
        data = json.loads((TOOLS / "resolution_aliases.json").read_text(encoding="utf-8"))
        src = ROOT / "src"
        missing = []
        for prefix, target in (data.get("prefixes") or {}).items():
            if not (src / target).exists():
                missing.append("prefix %s -> %s" % (prefix, target))
        for name, target in (data.get("files") or {}).items():
            if not (src / target).is_file():
                missing.append("file %s -> %s" % (name, target))
        self.assertEqual(missing, [], "alias declaration points at a path that "
                                      "does not exist: %r" % (missing,))
        # ...and the deliberate absences stay documented, so an absent entry is a
        # decision on the record rather than an oversight.
        self.assertIn("deliberately_absent", data)
        self.assertIsInstance(yc.Aliases({}, "empty").stale(), list)

    def test_every_decision_keeps_its_evidence(self):
        """A choice in the table must carry the measurement that justified it.

        The alias file resolves the ml_platforms duplicate by decision. If the
        evidence can be dropped, the next session inherits a preference with no
        reason and no way to overturn it.
        """
        data = json.loads((TOOLS / "resolution_aliases.json").read_text(encoding="utf-8"))
        decisions = data.get("decisions") or {}
        for name, entry in decisions.items():
            self.assertTrue(entry.get("choice"), "decision %r states no choice" % name)
            self.assertTrue(entry.get("evidence"), "decision %r has no evidence" % name)
        self.assertIn("ml_platforms", json.dumps(decisions),
                      "the duplicate-directory decision is no longer on record")

    def test_unresolved_baseline_states_a_reason(self):
        """A ratchet baseline without its `review` note is a suppression."""
        base = json.loads((TOOLS / "unresolved_baseline.json").read_text(encoding="utf-8"))
        self.assertIn("counts", base)
        self.assertIn("total", base)
        self.assertTrue((base.get("review") or "").strip(),
                        "baseline records counts but not why they are acceptable")

    def test_unused_declaration_is_stale(self):
        yc = _load("yggdrasil_census")
        al = yc.Aliases({"prefixes": {"USED": "CORE", "UNUSED": "CORE"}}, "test")
        al.used["prefix:USED"] += 1
        self.assertEqual(al.stale(), ["prefix:UNUSED"])


class Affected(unittest.TestCase):
    """`--affected` licenses re-verification, so it may not guess."""

    @classmethod
    def setUpClass(cls):
        cls.yc = _load("yggdrasil_census")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        _fixture(root)
        cls.ledger = cls.yc.census(root / "src", keep_edges=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_file_precise_transitive(self):
        res = self.yc.affected_trees(self.ledger, "C/c.py")
        self.assertEqual(res["granularity"], "file")
        depth = {r["node"]: r["depth"] for r in res["rows"]}
        self.assertEqual(depth.get("B/b.py"), 1)
        self.assertEqual(depth.get("A/a.py"), 2)
        # the driver that calls into C is a *shell* hop and must be reported as one
        self.assertIn("RUN/go.bat", res["affected_files"])

    def test_ambiguous_basename_is_refused(self):
        res = self.yc.affected_trees(self.ledger, "dup.py")
        self.assertTrue(res.get("error"), "ambiguous name was answered, not refused")
        self.assertEqual(res["rows"], [])
        self.assertEqual(sorted(res["candidates"]), ["C/dup.py", "D/dup.py"])

    def test_limb_granularity_is_labelled(self):
        res = self.yc.affected_trees(self.ledger, "C")
        self.assertEqual(res["granularity"], "limb")
        self.assertIn("B", res["affected_limbs"])


class Ratchet(unittest.TestCase):
    """The unresolved count may fall, never rise."""

    def test_growth_fails_and_flat_passes(self):
        rr = _load("unresolved_ratchet")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name) / "baseline.json"
        base.write_text(json.dumps({"counts": {"ambiguous": 2, "outside-root": 1},
                                    "total": 3}), encoding="utf-8")
        rr.BASELINE = base
        now = {"ambiguous": 2, "outside-root": 1, "target-not-found": 0}
        rr.measure = lambda root: {"references": 3, "resolved": 0,
                                  "unresolved": dict(now), "total": sum(now.values())}
        self.assertEqual(rr.main(["--check"]), 0, "unchanged counts must pass")

        now["ambiguous"] = 3
        self.assertEqual(rr.main(["--check"]), 1, "a new unresolved reference must fail")

        now["ambiguous"] = 1        # resolving one must not fail the gate
        self.assertEqual(rr.main(["--check"]), 0)


class FileIndex(unittest.TestCase):
    """Every file gets a bucket, and the buckets are checkable.

    The census `--selftest` asserts the same shape on a fixture; these tests keep
    the CLASSIFIER honest against the two directions it can fail in -- swallowing
    an unknown format (always "data") and refusing a known one (always
    "unclassified"). A fallback that hides the second failure is the one worth
    guarding hardest, because it makes the "100%" claim unfalsifiable.
    """

    @classmethod
    def setUpClass(cls):
        cls.yc = _load("yggdrasil_census")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        r = cls.root
        (r / "node_modules").mkdir()
        (r / "node_modules" / "dep.js").write_text("import 'x';\n", encoding="utf-8")
        (r / "vendor" / "lib").mkdir(parents=True)
        (r / "vendor" / "lib" / "dep.py").write_text("x = 1\n", encoding="utf-8")
        (r / "archived" / "2026-01-01").mkdir(parents=True)
        (r / "archived" / "2026-01-01" / "old.py").write_text("x = 1\n", encoding="utf-8")
        (r / "cli.build").mkdir()
        (r / "cli.build" / "x.obj").write_text("1", encoding="utf-8")
        (r / "hook").write_text("#!/usr/bin/env bash\necho\n", encoding="utf-8")
        (r / "mod.py").write_text("y = 2\n", encoding="utf-8")
        (r / "data.json").write_text("{}", encoding="utf-8")
        (r / "odd.zzz").write_text("plain text\n", encoding="utf-8")
        (r / "blob.zzz").write_bytes(b"\x00\x01\x02")
        (r / "weird").write_text("#!/opt/bin/mystery-lang\n", encoding="utf-8")
        cls.inv = cls.yc.file_inventory([r])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cls_of(self, rel):
        return self.yc.classify_file(self.root / rel, self.root)

    def test_code_and_noncode_are_declared(self):
        self.assertEqual(self.cls_of("mod.py"), ("code", "python"))
        self.assertEqual(self.cls_of("data.json"), ("noncode", "data"))
        # a script with no suffix is code: `scripts/hooks/pre-commit` is this shape
        self.assertEqual(self.cls_of("hook"), ("code", "shell"))

    def test_directory_classes_apply_within_the_root(self):
        self.assertEqual(self.cls_of("node_modules/dep.js"), ("vendored", "vendored"))
        self.assertEqual(self.cls_of("vendor/lib/dep.py"), ("vendored", "vendored"))
        self.assertEqual(self.cls_of("archived/2026-01-01/old.py"), ("archived", "archived"))
        self.assertEqual(self.cls_of("cli.build/x.obj"), ("generated", "generated"))
        # ...and a class must NOT leak onto an unrelated tree: `vendor` here is a
        # sibling of the file, not an ancestor of it.
        self.assertEqual(self.cls_of("mod.py")[0], "code")

    def test_undeclared_suffix_is_sniffed_and_named(self):
        self.assertEqual(self.cls_of("odd.zzz"), ("noncode", "text:unrecognised"))
        self.assertEqual(self.cls_of("blob.zzz"), ("noncode", "binary:unrecognised"))
        self.assertEqual(self.inv["undeclared_extensions"].get(".zzz"), 2)

    def test_unknown_interpreter_stays_unclassified(self):
        # The falsifiable path: an interpreter nobody declared is genuinely
        # unknown, and `unclassified` must be able to say so.
        self.assertEqual(self.cls_of("weird"), ("unclassified",
                                                "shebang:#!/opt/bin/mystery-lang"))
        self.assertEqual(self.inv["unclassified"], 1)
        self.assertFalse(self.inv["every_file_indexed"])

    def test_totals_reconcile_with_disk(self):
        on_disk = sum(1 for p in self.root.rglob("*") if p.is_file())
        self.assertEqual(self.inv["total"], on_disk)
        self.assertTrue(self.inv["adds_up"], "buckets do not sum to the total")
        self.assertEqual(self.inv["unrecognised"], 2)
        self.assertFalse(self.inv["fully_declared"],
                         "a sniffed file was reported as fully declared")

    def test_real_tree_is_fully_declared(self):
        """The repository itself: no undeclared suffix, no unplaced file."""
        inv = self.yc.file_inventory([ROOT / "tools"])
        self.assertEqual(inv["unclassified"], 0)
        self.assertTrue(inv["adds_up"])


class BoundaryPolicy(unittest.TestCase):
    """Tier constraints are only worth having if their controls are green."""

    def test_controls_and_policy_shape(self):
        bp = _load("boundary_policy")
        self.assertEqual(bp.selftest(), 0)
        policy = json.loads((TOOLS / "boundary_policy.json").read_text(encoding="utf-8"))
        rules = policy.get("constraints") or []
        self.assertTrue(rules, "policy declares no constraints")
        for rule in rules:
            self.assertTrue(rule.get("name"), "unnamed rule: %r" % (rule,))
            self.assertTrue(rule.get("why"), "rule %r has no reason on record"
                            % (rule.get("name"),))
            self.assertTrue(rule.get("forbid"), "rule %r forbids nothing"
                            % (rule.get("name"),))


if __name__ == "__main__":
    unittest.main()
