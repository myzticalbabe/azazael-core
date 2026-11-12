# coding: utf-8
"""Unit tests for evolution helpers + Terence verify/rollback.

Covers B6 gaps on evolution_cycle.py (deterministic helpers only — the
full lifecycle stays proven via live logs) and locks in the Terence
proficiency ladder (good/bad/stale fixes).
"""
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import evolution_cycle as evc


class TestGenomeSeed:
    def test_deterministic(self):
        g = {"learning_rate": 0.3, "epsilon": 0.1, "discount": 0.9,
             "epsilon_decay": 0.05}
        assert evc.genome_seed(g, "a") == evc.genome_seed(g, "a")

    def test_salts_differ(self):
        g = {"learning_rate": 0.3, "epsilon": 0.1, "discount": 0.9,
             "epsilon_decay": 0.05}
        seeds = {evc.genome_seed(g, s) for s in evc._SALTS}
        assert len(seeds) == len(evc._SALTS) == 3

    def test_genomes_differ(self):
        g1 = {"learning_rate": 0.3, "epsilon": 0.1, "discount": 0.9,
              "epsilon_decay": 0.05}
        g2 = dict(g1, learning_rate=0.31)
        assert evc.genome_seed(g1) != evc.genome_seed(g2)

    def test_key_order_stable(self):
        g1 = {"epsilon": 0.1, "learning_rate": 0.3, "discount": 0.9,
              "epsilon_decay": 0.05}
        g2 = {"learning_rate": 0.3, "discount": 0.9, "epsilon": 0.1,
              "epsilon_decay": 0.05}
        assert evc.genome_seed(g1) == evc.genome_seed(g2)


class TestTerenceVerify:
    def _fix(self, path, line, original, fixed):
        sys.path.insert(
            0, os.path.join(os.path.dirname(__file__), '..', 'src',
                            'UTILITIES', 'TERENCE'))
        from TERENCE import TERENCE, Fix
        return TERENCE(), Fix(str(path), line, "bare-except", original, fixed,
                            "test fix")

    def test_good_fix_applies(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("try:\n    x = 1\nexcept:\n    pass\n", encoding="utf-8")
        t, fix = self._fix(p, 3, "except:", "except Exception:")
        ok, reason = t._apply_verified(fix)
        assert ok, reason
        assert "except Exception:" in p.read_text(encoding="utf-8")

    def test_breaking_fix_rolls_back(self, tmp_path):
        p = tmp_path / "a.py"
        before = "x = 1\n"
        p.write_text(before, encoding="utf-8")
        t, fix = self._fix(p, 1, "x = 1", "x = (1")
        ok, reason = t._apply_verified(fix)
        assert not ok
        assert p.read_text(encoding="utf-8") == before

    def test_stale_line_rejected(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("y = 2\n", encoding="utf-8")
        t, fix = self._fix(p, 1, "x = 1", "x = 2")
        ok, reason = t._apply_verified(fix)
        assert not ok and "drifted" in reason
        assert p.read_text(encoding="utf-8") == "y = 2\n"

    def test_non_utf8_skipped_intact(self, tmp_path):
        p = tmp_path / "a.py"
        raw = b"x = '\xff\xfe not utf8'\n"
        p.write_bytes(raw)
        t, fix = self._fix(p, 1, "x =", "x ==")
        ok, reason = t._apply_verified(fix)
        assert not ok and "UTF8" in reason
        assert p.read_bytes() == raw

    def test_locked_file_rejected(self, tmp_path):
        import sys
        if sys.platform != "win32":
            pytest.skip("msvcrt file locking is Windows-only")
        import msvcrt
        p = tmp_path / "a.py"
        p.write_text("x = 1\n", encoding="utf-8")
        lfh = open(str(p) + ".terence.lock", "w", encoding="utf-8")
        msvcrt.locking(lfh.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            t, fix = self._fix(p, 1, "x = 1", "x = 2")
            ok, reason = t._apply_verified(fix)
            assert not ok and "locked" in reason
            assert p.read_text(encoding="utf-8") == "x = 1\n"
        finally:
            lfh.close()
            try:
                import os as _os
                _os.remove(str(p) + ".terence.lock")
            except OSError:
                pass  # scanner:ignore -- best-effort lock cleanup in tmp_path; assertions already completed above


class TestTerenceSelfGuard:
    """Regression tests for the 2026-09-11 self-corruption incident.

    TERENCE's training pass scanned its own rule table. Because several rule
    DESCRIPTIONS embed the pattern they detect (e.g. "== None"), it rewrote
    its own metadata: the None rule's description became "Use 'is None'
    instead of 'is None'", the assertEquals pattern dropped its 's', and the
    list(d.keys()) rule was shortened. These tests fail if TERENCE can ever
    edit its own package again, and pin the repaired rule fields in place.
    """

    def _module(self):
        import importlib.util
        path = os.path.join(os.path.dirname(__file__), '..', 'src',
                            'UTILITIES', 'TERENCE', 'TERENCE.py')
        spec = importlib.util.spec_from_file_location('terence_selfguard',
                                                      path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_self_dir_is_own_package(self):
        import pathlib
        t = self._module().TERENCE()
        assert t.SELF_DIR.name == 'TERENCE'
        assert t._is_self(pathlib.Path(str(t.SELF_DIR), 'TERENCE.py'))
        assert t._is_self(pathlib.Path(str(t.SELF_DIR), 'terence_enhanced.py'))

    def test_sibling_package_is_not_self(self):
        t = self._module().TERENCE()
        assert not t._is_self(os.path.join('src', 'CORE', 'local_memory.py'))

    def test_scan_file_refuses_own_source(self):
        """The exact regression: scanning TERENCE.py must yield no fixes."""
        import pathlib
        t = self._module().TERENCE()
        assert t._scan_file(pathlib.Path(str(t.SELF_DIR), 'TERENCE.py')) == []

    def test_no_file_in_own_package_yields_fixes(self):
        """Every module in TERENCE's package must scan clean.

        Equivalent to the full-scan guarantee without sweeping the repo:
        the loop in scan() can only add fixes that _scan_file produced, and
        this asserts that function produces none for any sibling module.
        """
        import pathlib
        t = self._module().TERENCE()
        own = list(pathlib.Path(str(t.SELF_DIR)).rglob('*.py'))
        assert own, 'expected to find TERENCE source files'
        offenders = {str(p): t._scan_file(p) for p in own}
        assert all(v == [] for v in offenders.values()), offenders

    def test_apply_refuses_own_source(self):
        mod = self._module()
        t = mod.TERENCE()
        fix = mod.Fix(os.path.join(str(t.SELF_DIR), 'TERENCE.py'), 1,
                      'Use modern unittest methods', 'assertEquals',
                      'assertEqual', 'test')
        ok, reason = t._apply_verified(fix)
        assert not ok and 'own package' in reason

    def test_corrupted_rule_fields_are_repaired(self):
        t = self._module().TERENCE()
        by_desc = {r['desc']: r for r in t.FIXES}
        assert "Use 'is None' instead of '== None'" in by_desc
        assert "Use 'is not None' instead of '!= None'" in by_desc
        assert by_desc['Simplify list(d.keys())']['match'] == 'list(d.keys())'
        assert by_desc['Use modern unittest methods']['pattern'] == \
            r'assertEquals|assert_'
        # no rule description may claim to replace a thing with itself
        for desc in by_desc:
            head, _, tail = desc.partition('instead of ')
            assert not tail or head.replace('Use ', '') != tail, desc

    def test_substitution_and_unsafe_keys_exist(self):
        T = self._module().TERENCE
        descs = {r['desc'] for r in T().FIXES}
        assert set(T.RULE_SUBSTITUTIONS) <= descs
        assert set(T.UNSAFE_RULES) <= descs

    def test_none_rule_never_inverts_negation(self):
        import re
        T = self._module().TERENCE
        pattern, repl = T.RULE_SUBSTITUTIONS[
            "Use 'is None' instead of '== None'"]
        assert re.sub(pattern, repl, 'x == None') == 'x is None'
        # the negated form must be left untouched by this rule
        assert re.sub(pattern, repl, 'x != None') == 'x != None'
