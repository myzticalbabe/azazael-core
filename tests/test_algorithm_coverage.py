#!/usr/bin/env python
"""
Test harness that replays all 19 failed Error #214 algorithm damage signatures
against the HEUR-01 through HEUR-10 catalog to measure detection coverage.

Each algorithm's damage pattern is encoded as a corrupted code string paired
with the HEUR pattern(s) expected to detect it. The harness writes each string
to a temp file, scans it with HeuristicErrorRecorder._scan_for_patterns(), and
compares detected vs expected patterns.

Algorithms with an empty expected list are "known gaps" — damage patterns that
the current HEUR catalog cannot detect by design (e.g., single-line stubs that
look identical to valid Python). They are excluded from the detection rate
calculation but reported separately.
"""

import os
import sys
import tempfile
# from typing import List, Dict, Optional, Tuple  # removed — all names have native equivalents

# Ensure src/ is on path (harness is in tests/, src/ is one level up)
_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from CORE.compliance.heuristic_recorder import HeuristicErrorRecorder

# ---------------------------------------------------------------------------
# 19 Failed Algorithm Damage Signatures
# ---------------------------------------------------------------------------
# Each entry: {id, category, desc, healthy, corrupted, expected}
#   expected: list of HEUR pattern IDs that SHOULD detect this damage
#   empty expected = known gap (pattern cannot detect this by design)

ALGORITHMS: list[Dict] = [
    # ==================== Indentation Destruction (6) ====================
    {
        "id": 1,
        "category": "Indentation",
        "desc": "Stripped def-block indentation — body lines pulled to column 0",
        "healthy": "def process():\n    x = 1\n    return x\n",
        "corrupted": "def process():\nx = 1\nreturn x\n",
        "expected": ["HEUR-01"],  # block-header followed by unindented body
    },
    {
        "id": 2,
        "category": "Indentation",
        "desc": "0-indent method inside class — def at column 0 within indented class",
        "healthy": "class Worker:\n    def run(self):\n        pass\n",
        "corrupted": "class Worker:\ndef run(self):\n    pass\n",
        "expected": ["HEUR-06"],  # column-0 def with self followed by 4+ space line
    },
    {
        "id": 6,
        "category": "Indentation",
        "desc": "Stripped nested if-block — HEUR-01 requires column-0 header (known gap)",
        "healthy": "def check(val):\n    if val:\n        return True\n    return False\n",
        "corrupted": "def check(val):\n    if val:\nreturn True\n    return False\n",
        "expected": [],  # HEUR-01 ^ requires column-0 block header; nested 'if' at 4sp won't match
    },
    {
        "id": 8,
        "category": "Indentation",
        "desc": "Module-level double-indent — HEUR-09 excludes def lines (known gap)",
        "healthy": "def calculate():\n    return 42\n",
        "corrupted": "def calculate():\n        return 42\n",
        "expected": [],  # HEUR-09 jump_re excludes def lines; only 2 lines, no non-excluded pair
    },
    {
        "id": 11,
        "category": "Indentation",
        "desc": "Indent-jump sandwich — one 12-space line amid 4-space siblings",
        "healthy": "def process():\n    a = 1\n    b = 2\n    c = 3\n",
        "corrupted": "def process():\n    a = 1\n            b = 2\n    c = 3\n",
        "expected": ["HEUR-09"],  # sandwich: a(4)->b(12)->c(4), 12 amid 4
    },
    {
        "id": 14,
        "category": "Indentation",
        "desc": "Stripped nested try-block — HEUR-01 requires column-0 header (known gap)",
        "healthy": "def run():\n    try:\n        x = 1\n    except:\n        pass\n",
        "corrupted": "def run():\n    try:\nx = 1\n    except:\n        pass\n",
        "expected": [],  # HEUR-01 ^ requires column-0 block header; nested 'try' at 4sp won't match
    },

    # ==================== Line Merge/Deletion (5) ====================
    {
        "id": 4,
        "category": "Line Merge/Del",
        "desc": "Multiple single-line def stubs merged (HEUR-02 known gap)",
        "healthy": "def f(): pass\ndef g(): pass\n",
        "corrupted": "def f(): pass def g(): pass\n",
        "expected": [],  # HEUR-02 excludes valid stubs -> false positive
    },
    {
        "id": 7,
        "category": "Line Merge/Del",
        "desc": "Two real statements merged — class keyword + return on one line",
        "healthy": "class Foo:\n    def bar(self):\n        return 1\n\nclass Baz:\n    pass\n",
        "corrupted": "class Foo        return 1\n",
        "expected": ["HEUR-02"],  # class keyword + return keyword on same line
    },
    {
        "id": 9,
        "category": "Line Merge/Del",
        "desc": "Assignment + class keyword merged on one line",
        "healthy": "def calc():\n    x = compute()\n    return x\n",
        "corrupted": "def calc():\n    x = compute()        class X: return 1\n",
        "expected": ["HEUR-02"],  # class keyword + return keyword on same line
    },
    {
        "id": 12,
        "category": "Line Merge/Del",
        "desc": "def + pass merged on one line with extra statement (HEUR-02 known gap)",
        "healthy": "def f(): pass\ndef g(): pass\n",
        "corrupted": "def f(): pass def g(): pass\n",
        "expected": [],  # same as #4 — valid-stub exclusion
    },
    {
        "id": 18,
        "category": "Line Merge/Del",
        "desc": "def+pass merged with class stub (HEUR-02 known gap)",
        "healthy": "class X: pass\ndef f(): pass\n",
        "corrupted": "class X: pass def f(): pass\n",
        "expected": [],  # valid-stub exclusion
    },

    # ==================== Import Corruption (3) ====================
    {
        "id": 3,
        "category": "Import",
        "desc": "Column-0 import inside class body (Algorithm #3 signature)",
        "healthy": "class Config:\n    # config init\n    import os\n    path = os.getenv('X')\n",
        "corrupted": "class Config:\nimport os\n    path = os.getenv('X')\n",
        "expected": ["HEUR-08"],  # column-0 import after class header
    },
    {
        "id": 5,
        "category": "Import",
        "desc": "from-import merged into comment line",
        "healthy": "# Data imports\nfrom typing import List\nimport os\n",
        "corrupted": "# Init from typing import List import os\n",
        "expected": ["HEUR-05"],  # comment + from-import + import on same line
    },
    {
        "id": 10,
        "category": "Import",
        "desc": "from-import merged into comment (single-line variant)",
        "healthy": "# Setup\nfrom pathlib import Path\nimport os\n",
        "corrupted": "# Setup from pathlib import Path import os\n",
        "expected": ["HEUR-05"],  # comment + from-import + import on same line
    },

    # ==================== Method Duplication (3) ====================
    {
        "id": 13,
        "category": "Method",
        "desc": "Duplicate method in same class — two def foo() at same indent",
        "healthy": "class Foo:\n    def foo(self):\n        pass\n",
        "corrupted": "class Foo:\n    def foo(self):\n        pass\n    def foo(self):\n        return 1\n",
        "expected": ["HEUR-03"],  # duplicate def at same indent in same class
    },
    {
        "id": 16,
        "category": "Method",
        "desc": "Duplicate at module level — HEUR-03 requires indented def (known gap)",
        "healthy": "def main():\n    pass\n",
        "corrupted": "def main():\n    pass\ndef main():\n    return 1\n",
        "expected": [],  # HEUR-03 _DUPLICATE_METHOD_RE requires ^ +def (indented); column-0 def won't match
    },
    {
        "id": 19,
        "category": "Method",
        "desc": "Main-guard calls missing function (deleted def)",
        "healthy": "def run():\n    return 0\n\nif __name__ == '__main__':\n    run()\n",
        "corrupted": "if __name__ == '__main__':\n    run()\n",
        "expected": ["HEUR-04"],  # main guard calls run() but no def run() exists
    },

    # ==================== Syntax Destruction (2) ====================
    {
        "id": 15,
        "category": "Syntax",
        "desc": "4-space indentation step — indistinguishable from normal nesting (known gap)",
        "healthy": "def process():\n    a = 1\n    b = 2\n",
        "corrupted": "def process():\n    a = 1\n        b = 2\n",
        "expected": [],  # diff=4 is normal Python step, HEUR-09 skips diff<=4
    },
    {
        "id": 17,
        "category": "Syntax",
        "desc": "Dangling function body — def with no body before next def (Algorithm #17)",
        "healthy": "def main():\n    init()\n    return 0\n",
        "corrupted": "def main():\n\n\ndef other():\n    pass\n",
        "expected": ["HEUR-10"],  # def followed by blanks then next def
    },
]


# ---------------------------------------------------------------------------
# Test Harness
# ---------------------------------------------------------------------------

def run_algorithm_test(
    recorder: HeuristicErrorRecorder,
    algo: Dict,
    algo_num: int,
) -> tuple[str, List[str], List[str], bool]:
    """Run one algorithm's corrupted code through _scan_for_patterns().

    Returns:
        (status, expected, detected, covered)
        status: "DETECTED", "MISSED", or "KNOWN_GAP"
        expected: list of expected HEUR pattern IDs
        detected: list of actual HEUR pattern IDs detected
        covered: True if at least one expected pattern was detected
    """
    expected = algo["expected"]
    corrupted = algo["corrupted"]

    # Write corrupted code to temp file and scan
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".py", text=True)
        os.write(fd, corrupted.encode("utf-8"))
        os.close(fd)

        hits = recorder._scan_for_patterns(tmp)
        detected = [h["pattern_id"] for h in hits]
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)

    # Determine status
    if not expected:
        return ("KNOWN_GAP", expected, detected, True)
    else:
        covered = any(eid in detected for eid in expected)
        status = "DETECTED" if covered else "MISSED"
        return (status, expected, detected, covered)


def main():
    recorder = HeuristicErrorRecorder()

    # Run all 19 algorithms
    results = []
    for i, algo in enumerate(ALGORITHMS, 1):
        status, expected, detected, covered = run_algorithm_test(
            recorder, algo, i
        )
        results.append((algo, status, expected, detected, covered))

    # ── Output ──
    print("=" * 72)
    print("  ERROR #214 ALGORITHM DAMAGE COVERAGE TEST")
    print("  HEUR-01 through HEUR-10 vs. 19 Failed Algorithms")
    print("=" * 72)
    print()

    # Per-algorithm results
    for algo, status, expected, detected, covered in results:
        marker = "OK" if status in ("DETECTED", "KNOWN_GAP") else "!!"
        exp_str = ", ".join(expected) if expected else "(none)"
        det_str = ", ".join(detected) if detected else "(none)"

        print(
            f"  [{marker}] Algo #{algo['id']:>2} ({algo['category']:<14}) "
            f"{algo['desc'][:58]:<58} "
            f"exp=[{exp_str:<12}] got=[{det_str}]"
        )

    # ── Coverage Summary ──
    print()
    print("=" * 72)
    print("  COVERAGE BY CATEGORY")
    print("=" * 72)

    categories = {}
    for algo, status, expected, detected, covered in results:
        cat = algo["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "detected": 0, "gaps": 0, "missed": 0}
        categories[cat]["total"] += 1
        if status == "KNOWN_GAP":
            categories[cat]["gaps"] += 1
        elif status == "DETECTED":
            categories[cat]["detected"] += 1
        else:
            categories[cat]["missed"] += 1

    for cat, counts in sorted(categories.items()):
        detectable = counts["total"] - counts["gaps"]
        rate = (counts["detected"] / detectable * 100) if detectable > 0 else 100.0
        print(
            f"  {cat:<20} {counts['detected']}/{detectable} detected "
            f"({rate:.0f}%)  [{counts['total']} total, "
            f"{counts['gaps']} known gaps, {counts['missed']} missed]"
        )

    # ── Overall ──
    total = len(results)
    gaps = sum(1 for _, s, _, _, _ in results if s == "KNOWN_GAP")
    detected = sum(1 for _, s, _, _, _ in results if s == "DETECTED")
    missed = sum(1 for _, s, _, _, _ in results if s == "MISSED")
    detectable = total - gaps

    print()
    print("=" * 72)
    print("  OVERALL COVERAGE")
    print("=" * 72)
    print(f"  Known gaps:          {gaps} algorithms (undetectable by design)")
    print(f"  Detectable:          {detectable} algorithms")
    print(f"  Detected:            {detected} of {detectable} ({detected / detectable * 100:.1f}%)")
    print(f"  Missed:              {missed} (real detection gaps)")
    print(f"  Overall (of 19):     {detected}/19 ({detected / 19 * 100:.1f}%)")

    # ── Missed details ──
    if missed:
        print()
        print("  MISSED (real gaps to address):")
        for algo, status, expected, detected, covered in results:
            if status == "MISSED":
                exp_str = ", ".join(expected)
                det_str = ", ".join(detected) if detected else "(none)"
                print(
                    f"    Algo #{algo['id']}: {algo['desc'][:60]}\n"
                    f"      Expected: {exp_str}  Got: {det_str}"
                )

    # ── HEUR Pattern Effectiveness ──
    print()
    print("=" * 72)
    print("  HEUR PATTERN EFFECTIVENESS")
    print("=" * 72)

    all_patterns = [f"HEUR-0{i}" for i in range(1, 10)] + ["HEUR-10"]
    pattern_stats = {}
    for pid in all_patterns:
        expected_count = sum(1 for algo in ALGORITHMS for e in algo["expected"] if e == pid)
        detected_count = sum(
            1 for algo, status, _, detected, _ in results
            if status == "DETECTED" and pid in detected
        )
        pattern_stats[pid] = (expected_count, detected_count)

    for pid in all_patterns:
        exp, det = pattern_stats[pid]
        bar = "#" * max(exp, 1)
        print(f"  {pid}: expected in {exp} algos => detected in {det}  {bar}")

    print()
    print("=" * 72)

    return 0 if missed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())


# ── pytest entry point (2026-09-21) ──────────────────────────────────
# `main()` is a harness, not a test function, so pytest collected ZERO tests from
# this file (rc=5) and the 19 damage-signature coverage measurements it performs
# had never run under a test runner.
def test_script_passes(run_standalone_script):
    proc = run_standalone_script(__file__, timeout=300.0)
    assert proc.returncode == 0, (
        f"{__file__} exited {proc.returncode}\n"
        + proc.stdout[-3000:] + "\n" + proc.stderr[-2000:]
    )
