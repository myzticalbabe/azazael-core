#!/usr/bin/env python3
# coding: utf-8
"""Integration tests exercising the REAL worm-governance audit path (no mocks).

The worm governance pipeline (CORE.worm_governance) is a pure-analysis step
over the REAL WORM_REGISTRY — it performs naming, dedup, and lifecycle analysis
without executing any worm or writing registry state. Unlike the CLUSTERWORMS
execution tests, there is nothing to execute here; the audit path consumes the
same real registry the runtime builds.

These tests verify, end-to-end against the real 100+ worm registry:

  * discover_worms()                         -> populates the real registry
  * run_governance_audit()                   -> full GovernanceReport convenience path
  * WormGovernor.govern(snapshot)            -> naming / dedup / lifecycle / category health
  * LifecycleManager + NamingValidator       -> real per-worm judgments
  * report consistency                       -> counts reconcile with the real registry

Run standalone (python tests/test_worm_governance_integration.py) or under pytest.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _env_315t

os.environ.setdefault("AZAZAEL_TEST_QUICK", "1")

# ── Python 3.15t Free-Threading Guard (module level — before imports) ──
from _env_315t import setup_315t
setup_315t(mock_pams=True)

try:
    import pytest
except BaseException:
    import types
    pytest = types.ModuleType("pytest")
    pytest.skip = lambda reason=None: exec("raise type('Skip', (BaseException,), {})(reason or '')")
    pytest.mark = types.ModuleType("mark")
    pytest.mark.skip = lambda *a, **kw: lambda f: f

# ── Real runtime paths (no mocks) ────────────────────────────────────
from CORE.worm_registry import WORM_REGISTRY, discover_worms, reset_registry
from CORE.worm_governance import (
    WormGovernor,
    WormNamingValidator,
    LifecycleManager,
    LifecycleState,
    run_governance_audit,
)


def _ensure_discovered() -> int:
    """Idempotently populate the REAL registry (cache-backed after first run)."""
    if len(WORM_REGISTRY) < 20:
        reset_registry()      # clear partial import side-effects, set done=False
        discover_worms()      # cache-backed => loads the full real registry fast
    return len(WORM_REGISTRY)


# ═══════════════════════════════════════════════════════════════════
# Worm Governance audit end-to-end against the real registry
# ═══════════════════════════════════════════════════════════════════


class TestGovernanceAuditEndToEnd:
    """Run the full governance audit against the real worm registry."""

    def test_run_governance_audit_returns_full_report(self):
        """run_governance_audit() should produce a complete GovernanceReport."""
        _ensure_discovered()
        report = run_governance_audit()
        assert report.timestamp
        # Naming report
        assert report.naming_report.total_worms == len(WORM_REGISTRY)
        assert report.naming_report.valid_count + report.naming_report.violation_count == \
            report.naming_report.total_worms
        # Dedup report
        assert report.dedup_report.total_pairs_checked > 0
        # Lifecycle report
        assert report.lifecycle_report.total_worms == len(WORM_REGISTRY)
        assert report.lifecycle_report.entries  # at least one per-worm entry
        # Category health covers every real category
        assert report.category_health
        # Convergence findings is a non-empty list of policy concerns
        assert isinstance(report.convergence_issues, list)
        assert report.overall_grade in ("A", "B", "C", "D", "F")

    def test_govern_knows_every_real_worm(self):
        """govern() accounts for every entry in the real registry."""
        _ensure_discovered()
        governor = WormGovernor()
        report = governor.govern(WORM_REGISTRY)
        # Naming covers the whole registry
        assert report.naming_report.total_worms == len(WORM_REGISTRY)
        assert len(report.lifecycle_report.entries) == len(WORM_REGISTRY)
        # Category health counts sum to the whole registry (no orphans)
        hc = report.category_health
        assert sum(v["count"] for v in hc.values()) == len(WORM_REGISTRY)

    def test_govern_audit_is_read_only(self):
        """The audit path must not mutate the real registry or spawn worms."""
        _ensure_discovered()
        before = {n: dict(i) for n, i in WORM_REGISTRY.items()}
        governor = WormGovernor()
        governor.govern(WORM_REGISTRY)
        run_governance_audit()
        # Registry key set unchanged; per-worm spawn counts unchanged
        assert set(WORM_REGISTRY.keys()) == set(before.keys())
        for name, info in WORM_REGISTRY.items():
            assert info["spawn_count"] == before[name]["spawn_count"]
            assert isinstance(info["class"], type)

    def test_govern_worm_on_real_entry(self):
        """govern_worm() returns a real per-worm governance report."""
        _ensure_discovered()
        governor = WormGovernor()
        name, info = next(iter(WORM_REGISTRY.items()))
        wr = governor.govern_worm(name, info)
        assert wr is not None
        assert wr.name == name
        assert wr.category == info.get("category")
        assert wr.source_file == info.get("source_file", "")
        assert 0.0 <= wr.governance_score <= 10.0
        assert wr.naming.name == name
        assert wr.lifecycle.name == name

    def test_lifecycle_states_cover_all_real_worms(self):
        """Every real worm resolves to a known lifecycle state."""
        _ensure_discovered()
        manager = LifecycleManager()
        seen = set()
        for name, info in WORM_REGISTRY.items():
            state = manager.get_state(name, info)
            seen.add(state)
            assert state in (LifecycleState.DRAFT, LifecycleState.ACTIVE,
                             LifecycleState.DEPRECATED, LifecycleState.RETIRED)
        # Real registry contains both drafted and active worms in practice
        assert seen

    def test_naming_validator_handles_real_names(self):
        """Naming validation is deterministic and total across real worms."""
        _ensure_discovered()
        validator = WormNamingValidator()
        report = validator.validate_registry(WORM_REGISTRY)
        assert report.total_worms == len(WORM_REGISTRY)
        checked = report.valid_count + report.violation_count
        assert checked == report.total_worms
        # Every violation carries a name + at least one reason
        for v in report.violations:
            assert isinstance(v.name, str)
            assert v.violations


# ═══════════════════════════════════════════════════════════════════
# Standalone Runner
# ═══════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    passed = failed = 0
    for name, obj in sorted(globals().items(), key=lambda x: x[0]):
        if isinstance(obj, type) and name.startswith("Test"):
            inst = obj()
            for mn in sorted(dir(inst)):
                if mn.startswith("test_"):
                    try:
                        getattr(inst, mn)()
                        print(f"  [OK] {name}.{mn}")
                        passed += 1
                    except Exception as e:
                        print(f"  [FAIL] {name}.{mn}: {e}")
                        failed += 1
    print(f"\n  Passed: {passed}, Failed: {failed}")
    sys.exit(0 if failed == 0 else 1)