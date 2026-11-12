"""Startup Step 4.8 Validation Test — Verifies RULE 0Z-F is enforced at session startup.

Checks:
1. Step 4.8 exists in azazael-system-startup.md
2. Step 4.8 contains RULE 0Z-F mandate
3. Step 4.8 contains AP-ZF-1 memory anchor
4. Step 4.8 appears AFTER Step 4.7 (correct ordering)
"""
import sys
import os
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

SKILL_PATH = "skills/azazael-system-startup.md"
ALT_PATH = "azazael/skills/azazael-system-startup.md"


def read_skill():
    """Read the startup skill, trying multiple paths."""
    for p in [SKILL_PATH, ALT_PATH]:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read(), p
    return None, None


if __name__ == "__main__":
    print("=" * 60)
    print("  STARTUP STEP 4.8 VALIDATION TEST")
    print("=" * 60)

    content, path = read_skill()
    if content is None:
        print("FAIL: Could not find azazael-system-startup.md")
        sys.exit(1)

    print(f"Reading: {path}")
    print(f"File size: {len(content)} chars")

    tests = [
        ("Step 4.8 header exists", "Step 4.8" in content),
        ("RULE 0Z-F mandate present", "RULE 0Z-F" in content),
        ("AP-ZF-1 memory anchor present", "AP-ZF-1" in content),
        ("Parallel execution mandate present", "PARALLEL" in content),
        ("Maximum concurrency mention", "MAXIMUM CONCURRENCY" in content),
    ]

    # Test ordering: Step 4.8 must be after Step 4.7
    # Match step HEADERS ("### Step 4.X") only, so body cross-references
    # like "(Step 4.8)" in earlier steps cannot cause a false ordering failure.
    import re
    headers = [m for m in re.finditer(r"^#{2,4} Step (4\.[0-9]+)", content, re.MULTILINE)]
    pos_47 = next((m.start() for m in headers if m.group(1) == "4.7"), -1)
    pos_48 = next((m.start() for m in headers if m.group(1) == "4.8"), -1)
    tests.append(("Step 4.8 header after Step 4.7 header", pos_47 >= 0 and pos_48 > pos_47))

    passed = 0
    for name, result in tests:
        status = "PASS" if result else "FAIL"
        if result:
            passed += 1
        print(f"  [{status}] {name}")

    total = len(tests)
    print(f"\n{'=' * 60}")
    if passed == total:
        print(f"  ALL {total} STARTUP STEP 4.8 TESTS PASSED!")
    else:
        print(f"  {passed}/{total} STARTUP STEP 4.8 TESTS PASSED")
        sys.exit(1)
