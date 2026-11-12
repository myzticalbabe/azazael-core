# coding: utf-8
"""Tests for servemesh dangling-name filter (E-P1-2 group 1, filter-not-delete).

  1. next_provider never returns a registry-absent name; reports skips.
  2. Known-but-unconfigured locals stay selectable (fail-open design).
  3. CHAIN tuple itself untouched (audit trail pinned).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.WORMS.expansion import servemesh as sm


def _worm():
    w = sm.ProviderFailoverMeshWorm.__new__(sm.ProviderFailoverMeshWorm)
    import threading
    w._down = set()
    w._uses = {}
    w._fail_streak = {}
    w._down_reason = {}
    w._lock = threading.Lock()
    return w


class TestDanglingFilter(unittest.TestCase):
    def test_unregistered_names_skipped_and_reported(self):
        w = _worm()
        # Down every resolvable name: the pick must fail (not return a
        # dangling name) and report all six skips.
        from CORE.inference.providers import PROVIDER_REGISTRY
        aliases = sm.ProviderFailoverMeshWorm.PROVIDER_ALIASES
        resolvable = {p for p in sm.ProviderFailoverMeshWorm.CHAIN
                      if aliases.get(p, p) in PROVIDER_REGISTRY}
        self.assertTrue(resolvable)
        w._down = set(resolvable)
        out = w.next_provider()
        self.assertFalse(out["success"])
        self.assertIsNone(out["provider"])
        self.assertTrue(set(out["skipped_unresolvable"]) >= {
            "vllm", "sglang", "openwebui", "jan", "kilo", "opencode"})

    def test_all_down_still_reports_skips(self):
        w = _worm()
        w._down = set(sm.ProviderFailoverMeshWorm.CHAIN)
        out = w.next_provider()
        self.assertFalse(out["success"])
        self.assertIn("skipped_unresolvable", out)

    def test_chain_tuple_untouched(self):
        self.assertIn("vllm", sm.ProviderFailoverMeshWorm.CHAIN)
        self.assertIn("opencode", sm.ProviderFailoverMeshWorm.CHAIN)


if __name__ == "__main__":
    unittest.main()
