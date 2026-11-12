"""RULE 0Z-F Consistency Verification Test

Count-based approach: verifies the correct NUMBER of RULE 0Z-F check-line
occurrences across each canonical document, ensuring all PRE-ACTION GATE
assertion blocks are covered regardless of content position shifts.

Expected counts:
  SYSTEM_INSTRUCTIONS.md: >=3 (top banner + RULE 0S section + sentinel checklist)
  SOP.md:                >=3 (top banner + RULE 0S section + sentinel checklist)
  MANDATES.md:           >=1 (PRE-ACTION GATE assertion block)

Supports --ci flag for JUnit XML output (used by CI pipeline).
"""
import sys
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


CHECK_LINE = "RULE 0Z-F (Parallel Execution)"
SECTION_MARKER = "PARALLEL AGENT EXECUTION MANDATE"
SOP_MARKER = "PARALLEL AGENT EXECUTION"

CHECKS = [
    ("SYSTEM_INSTRUCTIONS.md", [
        ("RULE 0Z-F section", lambda c: SECTION_MARKER in c),
        ("PRE-ACTION GATE + sentinel count >= 3", lambda c: c.count(CHECK_LINE) >= 3),
    ]),
    ("SOP.md", [
        ("RULE 0Z-F section", lambda c: SOP_MARKER in c),
        ("PRE-ACTION GATE + sentinel count >= 3", lambda c: c.count(CHECK_LINE) >= 3),
    ]),
    ("MANDATES.md", [
        ("RULE 0Z-F section", lambda c: SOP_MARKER in c),
        ("PRE-ACTION GATE check line present", lambda c: c.count(CHECK_LINE) >= 1),
    ]),
]


def write_junit_xml(results, output_path: str):
    """Write JUnit XML report from test results."""
    ts = ET.Element("testsuite")
    ts.set("name", "test_rule_0zf_consistency")
    ts.set("tests", str(len(results)))
    ts.set("failures", str(sum(1 for _, passed in results if not passed)))
    ts.set("timestamp", datetime.now(timezone.utc).isoformat())

    for test_name, passed in results:
        tc = ET.SubElement(ts, "testcase")
        tc.set("name", test_name)
        tc.set("classname", "RULE_0Z_F")
        if not passed:
            failure = ET.SubElement(tc, "failure")
            failure.set("message", f"{test_name} FAILED")
            failure.text = f"Check failed for {test_name}"

    tree = ET.ElementTree(ts)
    tree.write(output_path, xml_declaration=True, encoding="utf-8")
    print(f"  JUnit XML written to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="RULE 0Z-F Consistency Test")
    parser.add_argument("--ci", action="store_true", help="Generate JUnit XML output")
    args = parser.parse_args()

    all_pass = True
    total = sum(len(c[1]) for c in CHECKS)
    fail_count = 0
    results = []

    for fname, checks in CHECKS:
        try:
            with open(fname, "r", encoding="utf-8") as f:
                c = f.read()
        except FileNotFoundError:
            try:
                with open("azazael/" + fname, "r", encoding="utf-8") as f:
                    c = f.read()
            except FileNotFoundError:
                print()
                print("=== " + fname + " === NOT FOUND (skip)")
                continue

        print()
        print("=== " + fname + " ===")
        actual_count = c.count(CHECK_LINE)
        print(f"  {CHECK_LINE} occurrences: {actual_count}")

        for check_name, check_fn in checks:
            result = check_fn(c)
            status = "PASS" if result else "FAIL"
            if not result:
                all_pass = False
                fail_count += 1
            print("  [" + status + "] " + check_name)
            results.append((f"{fname}:{check_name}", result))

    print()
    print("Overall: " + ("ALL PASS" if all_pass else "SOME FAILED"))

    if args.ci:
        # Write JUnit XML to same directory as this script (matches CI artifact path)
        output_dir = Path(__file__).resolve().parent
        output_path = str(output_dir / "test_rule_0zf_consistency.xml")
        write_junit_xml(results, output_path)

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
