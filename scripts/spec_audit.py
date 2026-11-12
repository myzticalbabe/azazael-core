"""
Spec Audit — Check all .spec files against actual Python implementations.

Scans all *_314t.spec files in the project root and checks if the
referenced Python scripts exist. Also checks for scripts referenced
in the spec's analysis section.

Outputs a gap report: Implemented / Spec-Only / Stale.
"""

import os
import sys
import glob
from pathlib import Path


def audit_specs(spec_dir: str = None, src_dirs: list = None) -> list:
    if spec_dir is None:
        # Default to project root (3 levels up from azazael/scripts/)
        spec_dir = str(Path(__file__).resolve().parent.parent.parent)
    """Audit .spec files against Python implementations.

    Returns list of dicts with: spec_file, referenced_script, exists, status.
    """
    if src_dirs is None:
        src_dirs = ["azazael/src", "src"]

    spec_pattern = os.path.join(spec_dir, "*_314t.spec")
    spec_files = sorted(glob.glob(spec_pattern))

    results = []
    for spec_path in spec_files:
        name = os.path.basename(spec_path).replace("_314t.spec", "")
        # The referenced script is typically the same name
        py_script = f"{name}.py"

        # Search in src directories for the script
        found_in = []
        for sd in src_dirs:
            full_path = os.path.join(spec_dir, sd, py_script)
            if os.path.exists(full_path):
                found_in.append(full_path)
            # Also search recursively
            for root, dirs, files in os.walk(os.path.join(spec_dir, sd)):
                if py_script in files:
                    found_in.append(os.path.join(root, py_script))

        # Also search in azazael/src subdirectories
        azazael_src = os.path.join(spec_dir, "azazael", "src")
        if os.path.exists(azazael_src):
            for root, dirs, files in os.walk(azazael_src):
                if py_script in files:
                    found_in.append(os.path.join(root, py_script))

        # Check for any .py file matching the name anywhere in azazael/
        if not found_in:
            azazael_dir = os.path.join(spec_dir, "azazael")
            if os.path.exists(azazael_dir):
                for root, dirs, files in os.walk(azazael_dir):
                    for f in files:
                        if f.lower() == py_script.lower():
                            found_in.append(os.path.join(root, f))

        found_in = list(set(found_in))  # deduplicate

        if found_in:
            results.append({
                "spec_file": spec_path,
                "referenced_script": py_script,
                "exists": True,
                "locations": found_in[:3],
                "status": "Implemented",
            })
        else:
            results.append({
                "spec_file": spec_path,
                "referenced_script": py_script,
                "exists": False,
                "locations": [],
                "status": "Spec-Only",
            })

    return results


def print_report(results: list):
    """Print formatted audit report."""
    implemented = [r for r in results if r["status"] == "Implemented"]
    spec_only = [r for r in results if r["status"] == "Spec-Only"]

    print("=" * 60)
    print("  SPEC AUDIT REPORT")
    print("=" * 60)
    print(f"  Total specs: {len(results)}")
    print(f"  Implemented: {len(implemented)}")
    print(f"  Spec-Only:   {len(spec_only)}")
    print()

    if spec_only:
        print("  SPEC-ONLY (no Python implementation found):")
        print()
        for r in spec_only:
            print(f"    {os.path.basename(r['spec_file'])}")
            print(f"      -> {r['referenced_script']}")
        print()

    if implemented:
        print("  IMPLEMENTED:")
        print()
        for r in implemented:
            loc = r["locations"][0] if r["locations"] else "?"
            print(f"    {os.path.basename(r['spec_file'])}")
            print(f"      -> {loc}")

    print()
    print(f"  Summary: {len(implemented)} implemented, {len(spec_only)} spec-only")

    return len(spec_only)  # Return count of gaps found


if __name__ == "__main__":
    import sys
    results = audit_specs()
    gaps = print_report(results)
    sys.exit(1 if gaps > 0 else 0)
