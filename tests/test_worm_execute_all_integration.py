#!/usr/bin/env python3
# coding: utf-8
"""End-to-end integration tests that drive REAL worms through ClusterWORMZ.execute_all().

Unlike test_worm_registry_integration.py (which resolves worms via the runtime
resolution path only), these tests exercise the full orchestration surface:

  * ClusterWORMZ() construction              -> real cluster with real shared state
  * add_worm(name, module)                   -> WormConfig wiring
  * execute_all()                            -> WormExecutor -> _resolve_worm_dynamic
                                                -> _execute_worm_class -> real method call
  * per-worm result dicts                     -> success/result/duration_ms/error

Only deterministic, read-only worms are executed (profiling, telemetry, security
audit, config lint, orchestration summary) — no state mutation, no external side
effects. SecretRotator is deliberately excluded (it rotates a real secret).

Run standalone (python tests/test_worm_execute_all_integration.py) or under pytest.
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
from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ


# Deterministic, read-only worms verified (via probe) to return success=True
# through the real execute_all() path.
SAFE_EXECUTE_WORMS = [
    "CodeProfiler",        # profiling / read-only instrumented summary
    "SpanTracer",          # telemetry / emits a local trace span
    "MetricAggregator",    # telemetry / empty snapshot (no writes)
    "EnvValidator",        # config / scans env files (empty-files degree of freedom)
    "PortAuditor",         # security / port audit
    "ConfigLinter",        # config / lint of empty config dir
    "ClusterWORMZ",         # orchestration / cluster status report (D1/U3:
                           # ClusterManager removed; ClusterWORMZ
                           # subsumed category grouping)
]


def _ensure_discovered() -> int:
    """Idempotently populate the REAL registry (cache-backed after first run)."""
    if len(WORM_REGISTRY) < 20:
        reset_registry()      # clear partial import side-effects, set done=False
        discover_worms()      # cache-backed => loads the full real registry fast
    return len(WORM_REGISTRY)


def _build_and_execute(names):
    """Build a real ClusterWORMZ from the given worm names and run execute_all()."""
    return ClusterWORMZ.build_from_registry(list(names)).execute_all()


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ.execute_all() end-to-end against the real registry
# ═══════════════════════════════════════════════════════════════════


class TestExecuteAllEndToEnd:
    """Drive real worms through the full ClusterWORMZ.execute_all() path."""

    def test_execute_all_runs_real_safe_worms(self):
        """execute_all() should return one result dict per worm, all successful."""
        _ensure_discovered()
        results = _build_and_execute(SAFE_EXECUTE_WORMS)
        assert len(results) == len(SAFE_EXECUTE_WORMS)
        by_name = {r.get("name"): r for r in results}
        for name in SAFE_EXECUTE_WORMS:
            r = by_name.get(name)
            assert r is not None, f"no result for {name}"
            assert r["success"] is True, f"{name} failed: {r.get('error')}"
            assert "result" in r, name
            assert "duration_ms" in r, name

    def test_results_carry_registered_class_output(self):
        """A resolved result should carry real method output, not a stub."""
        _ensure_discovered()
        results = _build_and_execute(["CodeProfiler"])
        assert results and results[0]["success"]
        res = results[0]["result"]
        assert isinstance(res, dict)
        # Real CodeProfiler output describes instrumented code, not a stub marker
        assert "worm" in res or "success" in res, f"no shape markers: {sorted(res)[:8]}"

    def test_execute_all_results_are_isolated_per_worm(self):
        """Each worm yields its own result entry keyed by name."""
        _ensure_discovered()
        results = _build_and_execute(["SpanTracer", "PortAuditor", "ConfigLinter"])
        names = {r.get("name") for r in results}
        assert {"SpanTracer", "PortAuditor", "ConfigLinter"} <= names

    def test_execute_all_through_single_worm_cluster(self):
        """A hand-built single-worm cluster still resolves and executes."""
        _ensure_discovered()
        cluster = ClusterWORMZ("e2e_single_unused_type")  # not in CLUSTER_REGISTRY -> no preload
        cluster.set_parallel(False)
        cluster.add_worm("ClusterWORMZ", "ClusterWORMZ")
        out = cluster.execute_all()
        assert len(out) == 1
        r = out[0]
        assert r["name"] == "ClusterWORMZ"
        assert r["success"] is True
        res = r["result"]
        # A class-level resolution arrives as an ENVELOPE that names the method
        # and carries its output one level down; the report is inside `result`,
        # and asserting at the top level reads the envelope instead of the report.
        assert isinstance(res, dict)
        # The mixin's ``get_summary()``
        # to, so the report can sit one level down or arrive directly. The
        # fields are environment-dependent, so pin the CONTRACT -- a real,
        # non-empty dict report -- rather than a field name that may move.
        payload = res.get("result") if "result" in res else res
        assert isinstance(payload, dict)
        assert payload, "the worm returned an empty report"

    def test_execute_all_parallel_and_sequential_agree(self):
        """Sequential and parallel execution both resolve the same real worms."""
        _ensure_discovered()
        worms = ["CodeProfiler", "SpanTracer", "PortAuditor"]

        cluster_seq = ClusterWORMZ.build_from_registry(worms)
        cluster_seq.set_parallel(False)
        seq = cluster_seq.execute_all()

        cluster_par = ClusterWORMZ.build_from_registry(worms)
        cluster_par.set_parallel(True)
        par = cluster_par.execute_all()

        seq_names = {r.get("name"): r.get("success") for r in seq}
        par_names = {r.get("name"): r.get("success") for r in par}
        for w in worms:
            assert seq_names.get(w) is True, f"sequential {w} failed"
            assert par_names.get(w) is True, f"parallel {w} failed"


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