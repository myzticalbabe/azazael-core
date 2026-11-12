"""Test: Provider Failover (standalone, no pytest needed).

Validates that the system can switch from a failed provider to a
working backup, measuring failover time and response quality.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

PASS = 0
FAIL = 0

def check(condition, msg):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {msg}")
    else:
        FAIL += 1
        print(f"  FAIL: {msg}")
    return condition

if __name__ == "__main__":
    print("=" * 60)
    print("Test: Provider Failover")
    print("=" * 60)

    from CORE.inference.providers import get_provider, list_providers, get_best_provider

    # -- Test 1: get_best_provider returns a working provider ---------------
    best = get_best_provider()
    check(best is not None, "get_best_provider() returns a provider")
    check(best.has_valid_key(), f"Best provider has valid key: {best.provider_name}")
    print(f"        Best: {best.provider_name} -> {best.config.model}")

    # -- Test 2: All configured providers are reachable ---------------------
    working = 0
    failing = 0
    for p in list_providers():
        if p['has_api_key'] and p['id'] not in ('xai',):
            try:
                prov = get_provider(p['id'])
                result = prov.chat("Say OK.", max_tokens=5)
                if 'error' in result:
                    failing += 1
                    print(f"        {p['id']}: FAIL - {result['error'][:60]}")
                else:
                    working += 1
                    print(f"        {p['id']}: OK ({result['elapsed_ms']}ms)")
            except Exception as e:
                failing += 1
                print(f"        {p['id']}: CRASH - {str(e)[:60]}")

    check(working >= 1, f"At least 1 provider working: {working}/{working + failing}")

    # -- Test 3: Failover between providers (simulate) ----------------------
    print()
    print("  Failover test: calling 3 different providers sequentially...")
    providers = ['groq', 'openrouter', 'nvidia_nim']
    responses = []
    start = time.time()

    for name in providers:
        try:
            prov = get_provider(name)
            if prov.has_valid_key():
                result = prov.chat("Say exactly 'hello'.", max_tokens=10)
                responses.append((name, result))
                if 'error' not in result:
                    break  # Success -- stop failover
        except Exception as e:
            responses.append((name, {"error": str(e)}))

    elapsed = int((time.time() - start) * 1000)
    check(len(responses) > 0, f"Failover: {len(responses)} providers tried in {elapsed}ms")
    for name, result in responses:
        status = "OK" if 'error' not in result else "FAIL"
        content = result.get('content', result.get('error', '?'))[:40]
        print(f"        {name}: {status} - {content}")

    # -- Test 4: Provider stats are tracked ---------------------------------
    for name in ['groq', 'nvidia_nim']:
        try:
            prov = get_provider(name)
            stats = prov.get_stats()
            check('total_requests' in stats, f"{name} stats has total_requests")
            check('total_tokens' in stats, f"{name} stats has total_tokens")
        except Exception:
            pass

    # -- Summary ------------------------------------------------------------
    print()
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"FAILURES: {FAIL}")
        sys.exit(1)


# ── pytest entry point (2026-09-21) ──────────────────────────────────
# Collected nothing before this (rc=5, "no tests collected"), so these checks had
# never run under a test runner. Unlike the other standalone scripts in this
# directory, this one makes LIVE inference calls (`prov.chat(...)`), so it is
# gated: a suite that needs provider credentials and network is not something CI
# should run by default, and a default that silently hits the network is worse
# than one that visibly skips. Set AZAZAEL_LIVE_NETWORK=1 to run it.
import pytest as _pytest  # noqa: E402  (appended after the script body by design)

pytestmark = _pytest.mark.skipif(
    os.environ.get("AZAZAEL_LIVE_NETWORK") != "1",
    reason="live inference providers; set AZAZAEL_LIVE_NETWORK=1 to run",
)


def test_script_passes(run_standalone_script):
    proc = run_standalone_script(__file__)
    assert proc.returncode == 0, (
        f"{__file__} exited {proc.returncode}\n"
        + proc.stdout[-3000:] + "\n" + proc.stderr[-2000:]
    )
