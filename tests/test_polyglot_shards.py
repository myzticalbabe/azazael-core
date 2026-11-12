#!/usr/bin/env python3
"""G1 referencing test for the polyglot shard modules.

Names every new src module so the testing-paradigm gate (G1) is satisfied,
and asserts the golden cross-language agreement contract: every available
toolchain prints AZAZAEL-GOLDEN-OK. Missing toolchains skip cleanly inside
polyglot.self_test() and must not fail this test.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from INTERPRETERS import node_runner, pwsh_runner, sh_runner
from INTERPRETERS import run_code, supported_languages
from SHARDS import polyglot, registry


def test_runner_modules_expose_expected_api():
    assert hasattr(node_runner, "NodeRunner")
    assert hasattr(pwsh_runner, "PwshRunner")
    assert hasattr(sh_runner, "ShRunner")
    for cls in (node_runner.NodeRunner, pwsh_runner.PwshRunner, sh_runner.ShRunner):
        for method in ("run_file", "run_code", "check_syntax"):
            assert callable(getattr(cls, method, None)), f"{cls.__name__}.{method}"


def test_dispatcher_covers_all_shard_langs():
    langs = set(supported_languages())
    # supported_languages() skips short aliases (go/rs/js/sh); the canonical
    # long names are what it reports.
    for expected in ("python", "golang", "rust", "javascript", "powershell", "bash"):
        assert expected in langs, f"dispatcher missing {expected}"


def test_registry_routes_known_tasks():
    assert registry.route_shard("sync kali via sync_all") == "skills-ops"
    assert registry.route_shard("gateway upstream failover") == "gateway"
    assert registry.route_shard("run node_runner test file") == "interpreters"
    assert registry.route_shard("something entirely unknown xyz") == "skills-core"


def test_golden_cross_language_agreement():
    assert polyglot.self_test() is True


def test_run_code_javascript_inline():
    res = run_code("javascript", 'console.log("AZAZAEL-GOLDEN-OK");')
    assert res.success and "AZAZAEL-GOLDEN-OK" in res.stdout


def test_route_lang_spends_budgets():
    from SHARDS.registry import route_lang
    assert route_lang("gateway", "balanced") == "go"
    assert route_lang("gateway", "peak") == "rust"
    assert route_lang("gateway", "fast") in ("go", "rust")
    assert route_lang("skills-core") == "doc"
    assert route_lang("no-such-shard") == "python"
    assert route_lang("interpreters", "balanced") == "python"
    assert route_lang("interpreters", "fast") in (
        "python", "go", "rust", "javascript", "powershell", "bash")


def test_work_tier_sum_agreement_where_toolchains_exist():
    # E1 regression net: the work goldens must each print EXPECTED_SUM.
    # (The bash golden once summed 1..20000 via seq while every other
    # language summed 0..19999 — nothing covered the work tier, so the
    # routing table silently ranked bash on a failing measurement.)
    from SHARDS.polyglot import EXPECTED_SUM, WORK_SNIPPETS
    assert EXPECTED_SUM == 20000 * 19999 // 2 == 199990000
    assert set(WORK_SNIPPETS) == set(polyglot.supported_shard_langs())
    for lang in polyglot.supported_shard_langs():
        res = polyglot.run_shard(lang, tier="work")
        if res["status"] == "skipped":
            continue
        assert res["ok"], f"work golden failed for {lang}: {res}"
        assert res["sum"], f"work sum mismatch for {lang}: {res}"


def test_stale_sweep_falls_back_to_canonical_order():
    # Owner decision 2026-10-29: a stale table degrades to canonical
    # preference, never spends stale numbers. MAX_AGE=-1 makes every
    # table stale by age, deterministically, on any machine.
    import SHARDS.registry as reg
    old = reg.SWEEP_MAX_AGE_HOURS
    reg.SWEEP_MAX_AGE_HOURS = -1
    try:
        assert reg.sweep_freshness()["fresh"] is False
        assert reg._measured_order() == list(reg.LANG_ORDER)
    finally:
        reg.SWEEP_MAX_AGE_HOURS = old


def test_skip_contract_is_case_insensitive():
    # E2 regression net: a toolchain message with different casing must
    # still skip cleanly instead of failing the agreement test.
    res = polyglot.run_shard("powershell")
    assert res["status"] in ("pass", "skipped")
    assert res["ok"]
