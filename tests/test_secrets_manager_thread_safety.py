"""Test: Secrets Manager Thread Safety (standalone, no pytest needed).

Validates that SecretsManager handles concurrent read/write from
multiple threads without data corruption under Python 3.14t free-threading.
"""
import os
import sys
import threading
import time

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
    print("Test: Secrets Manager Thread Safety")
    print("=" * 60)

    from CORE.secrets_manager import SecretsManager

    # Deterministic concurrency probe, not a secret-presence check: seed a
    # scratch value BEFORE the first SecretsManager() call freezes the cache
    # (WSL/CI have no GROQ_API_KEY; without this every call returns None and
    # the race test fails deterministically). setdefault never overrides real keys.
    os.environ.setdefault("GROQ_API_KEY", "test-concurrency-probe-key")
    # secrets_manager.py builds a module-level `_secrets = SecretsManager()`
    # AT IMPORT, freezing the cache before this line runs. reload() re-reads
    # the live environment into the shared singleton.
    SecretsManager().reload()

    # ── Test 1: Singleton returns same instance ────────────────────────
    sm1 = SecretsManager()
    sm2 = SecretsManager()
    check(sm1 is sm2, "Singleton returns same instance")

    # ── Test 2: Concurrent singleton creation (race condition test) ────
    instances = []
    lock = threading.Lock()
    errors = []

    def create_instance():
        try:
            sm = SecretsManager()
            with lock:
                instances.append(id(sm))
        except Exception as e:
            with lock:
                errors.append(str(e))

    threads = [threading.Thread(target=create_instance) for _ in range(20)]
    [t.start() for t in threads]
    [t.join(timeout=5) for t in threads]

    unique = len(set(instances))
    check(unique == 1, f"Concurrent creation: {unique} unique instances (expected 1)")
    check(len(errors) == 0, f"No errors during concurrent creation: {errors if errors else 'No errors'}")

    # ── Test 3: Concurrent get() calls ─────────────────────────────────
    get_results = []
    def get_keys():
        sm = SecretsManager()
        for _ in range(50):
            key = sm.get("GROQ_API_KEY")
            with lock:
                get_results.append(key is not None)

    threads = [threading.Thread(target=get_keys) for _ in range(10)]
    [t.start() for t in threads]
    [t.join(timeout=5) for t in threads]

    all_non_none = all(get_results)
    check(all_non_none, f"Concurrent get(): all {len(get_results)} calls returned non-None")

    # ── Test 4: Concurrent audit() calls ────────────────────────────────
    audit_results = []
    def run_audit():
        sm = SecretsManager()
        result = sm.audit()
        with lock:
            audit_results.append(len(result))

    threads = [threading.Thread(target=run_audit) for _ in range(10)]
    [t.start() for t in threads]
    [t.join(timeout=5) for t in threads]

    all_same = len(set(audit_results)) == 1
    check(all_same, f"Concurrent audit(): all returned {audit_results[0]} results (consistent)")

    # ── Test 5: Key masking security ───────────────────────────────────
    sm = SecretsManager()
    masked = sm._mask_key("nvapi-vM1234567890abcdefCfmQ")
    check("1234" not in masked, f"Key masked: {masked}")
    check(masked.endswith("CfmQ"), f"Masked key preserves last 4 chars: {masked}")
    check("..." in masked, "Masked key contains '...' separator")

    # ── Test 6: Audit never leaks raw keys ─────────────────────────────
    audit = sm.audit()
    for entry in audit:
        preview = entry.get("key_preview", "")
        if preview:
            # Preview should be masked or "***" - never the raw key
            is_masked = ("..." in preview) or (preview == "***")
            check(is_masked, f"Audit key masked for {entry['provider']}: {preview}")

    # ── Summary ────────────────────────────────────────────────────────
    print()
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"FAILURES: {FAIL}")
        sys.exit(1)


# ── pytest entry point (2026-09-21) ──────────────────────────────────
# The thread-safety checks above never ran under a test runner: they sit under
# `if __name__ == "__main__"`, so pytest collected nothing and returned rc=5.
# Free-threading races are exactly the class of defect that only shows up when the
# checks actually execute, so leaving them uncollected was the worst case.
def test_script_passes(run_standalone_script):
    proc = run_standalone_script(__file__)
    assert proc.returncode == 0, (
        f"{__file__} exited {proc.returncode}\n"
        + proc.stdout[-3000:] + "\n" + proc.stderr[-2000:]
    )
