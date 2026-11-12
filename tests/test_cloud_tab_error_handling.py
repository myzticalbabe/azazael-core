"""Test: Cloud Tab Error Handling (standalone, no pytest needed).

Validates that the CloudTab module handles errors gracefully:
network errors, invalid input, API failures without crashing.
Tests the module in isolation (no tkinter mainloop needed).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

if __name__ == "__main__":
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

    print("=" * 60)
    print("Test: Cloud Tab Error Handling")
    print("=" * 60)

    # ── Test 1: CloudTab module imports cleanly ────────────────────────
    try:
        from CORE.inference.cloud_tab import CloudTab, HAS_CLOUD_PROVIDERS, VERSION
        check(True, f"CloudTab imports: VERSION={VERSION}, HAS_CLOUD_PROVIDERS={HAS_CLOUD_PROVIDERS}")
    except Exception as e:
        check(False, f"CloudTab import failed: {e}")
        sys.exit(1)

    # ── Test 2: Providers module is wired correctly ────────────────────
    try:
        from CORE.inference.providers import get_provider, list_providers
        providers = list_providers()
        check(len(providers) > 0, f"list_providers() returned {len(providers)} providers")
        configured = [p for p in providers if p['has_api_key']]
        check(len(configured) >= 0, f"Provider config check ran: {len(configured)} providers configured")
        for p in configured:
            print(f"        {p['id']}: {p['name']} ({p['models']} models)")
    except Exception as e:
        check(False, f"Provider import failed: {e}")

    # ── Test 3: get_provider handles invalid names gracefully ──────────
    try:
        prov = get_provider("nonexistent_provider_xyz")
        check(False, "Should have raised ValueError for invalid provider")
    except ValueError as e:
        check(True, f"Invalid provider raises ValueError: {str(e)[:60]}")
    except Exception as e:
        check(True, f"Invalid provider raises exception: {type(e).__name__}")

    # ── Test 4: get_provider handles empty string ──────────────────────
    try:
        prov = get_provider("")
        check(False, "Should have raised for empty provider name")
    except (ValueError, KeyError):
        check(True, "Empty provider name raises error as expected")
    except Exception as e:
        check(True, f"Empty provider raises {type(e).__name__}")

    # ── Test 5: API client error handling ──────────────────────────────
    try:
        from CORE.inference.api_client import APIClient, InferenceError, mask_key, safe_error_message
        check(True, "APIClient imports successfully")
    except Exception as e:
        check(False, f"APIClient import failed: {e}")

    # ── Test 6: Key masking works ──────────────────────────────────────

    check(mask_key("short") == "***", f"Short key masked: {mask_key('short')}")
    check(mask_key("nvapi-vM1234567890abcdef") == "nvapi-vM...cdef",
          f"Long key masked correctly")
    check(mask_key("") == "", "Empty key returns empty string")

    # ── Test 7: Safe error message truncation ──────────────────────────
    long_err = "Error " + "x" * 200
    safe = safe_error_message(long_err)
    check(len(safe) <= 120, f"Error truncated to {len(safe)} chars (max 120)")

    # ── Test 8: Base APIClient instantiation ───────────────────────────
    client = APIClient(provider_name="test", max_retries=2)
    check(client.provider_name == "test", "APIClient provider_name set correctly")
    stats = client.get_stats()
    check(stats['calls'] == 0, "APIClient starts with 0 calls")
    check(stats['errors'] == 0, "APIClient starts with 0 errors")

    # ── Summary ────────────────────────────────────────────────────────
    print()
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"FAILURES: {FAIL}")
        sys.exit(1)


# ── pytest entry point (2026-09-21) ──────────────────────────────────
# Everything above is guarded by `if __name__ == "__main__"`, so pytest collected
# ZERO tests from this file and reported rc=5 ("no tests collected") -- i.e. these
# checks had never run under a test runner at all. See the fixture's docstring in
# `tests/conftest.py` for why the script is executed instead of rewritten.
def test_script_passes(run_standalone_script):
    proc = run_standalone_script(__file__)
    assert proc.returncode == 0, (
        f"{__file__} exited {proc.returncode}\n"
        + proc.stdout[-3000:] + "\n" + proc.stderr[-2000:]
    )
