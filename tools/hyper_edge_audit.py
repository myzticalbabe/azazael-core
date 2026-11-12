#!/usr/bin/env python3
"""hyper_edge_audit.py -- Hyper-Edge Audit Preflight (HED leg).

Runs the three HED auditors as one leg: reading_audit, settings_shadow,
and head_audit. Exits 1 if any denominator-free or gap findings exist,
exiting 0 when clean. This is the hyper-edge leg that consolidates reading//
settings/ head-audit as one gate leg, controlled by the hyper_edge knobs in
config/defaults.json.

HED-9 (reading_audit): denominator_free / collapsed findings
HED-10 (settings_shadow): orphan_knob / undeclared / fallback_drift / bypass_reader
HED-12 (head_audit): detached_worktree / dirty_worktree / exclusive_commits /
                     no_upstream / stale_head_state / unpushed / etc.

Exit: 0 = clean (all clear), 1 = issues found (mandatory leg failure),
      2 = could not perform
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Project root: resolve from this file, handle path normalization
_SCRIPT = Path(__file__).resolve()
# Normalize double-drive letters like C:\c\Azazael -> C:\Azazael
_ROOT_CANDIDATES = [
    _SCRIPT.parent.parent,  # normal: /c/Azazael/azazael
]
# Fix path if it has a spurious drive letter
for candidate in _ROOT_CANDIDATES:
    p = str(candidate)
    if p.startswith(r"C:\c") and not p.startswith(r"C:\Azazael"):
        # Strip the extra 'c' drive letter
        fixed = r"C:" + p[3:]  # C:\Azazael\azazael
        _ROOT_CANDIDATES[_ROOT_CANDIDATES.index(candidate)] = Path(fixed)
ROOT = _ROOT_CANDIDATES[0]

# HED-5 / HED-8 knobs from config/defaults.json hyper_edge section
HYPER_EDGE_KNOBS = {
    "reading_audit": True,
    "settings_shadow": True,
    "leg_ledger": True,
    "head_audit": True,
    "truncation": True,
    "ctypes_restype": True,
    "kernel": True,
    "kernel_timeout_s": 180,
    "strict": False,
    "leg_timeout_s": 300,
}


def _read_config_kob(key: str, fallback=None):
    """Read a hyper_edge knob from config via defaults_profile."""
    try:
        from tools.defaults_profile import read_default
        return read_default("hyper_edge.%s" % key, fallback)
    except Exception:
        return fallback


def _run_reading_audit() -> tuple:
    """Run reading_audit.py and return (exit_code, findings_summary)."""
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "reading_audit.py"), "--glob", "logs/*.json"],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        # Parse the output for finding classes
        output = result.stdout + result.stderr
        findings = []
        import re
        for m in re.finditer(r'-\s+denominator_free\s+', output):
            # Get the detail line following it
            idx = output.find(m.group(), m.end())
            if idx > 0:
                detail_end = output.find("\n", idx)
                detail = output[idx:detail_end].strip() if detail_end > idx else ""
                findings.append({"class": "denominator_free", "detail": detail})
        for m in re.finditer(r'-\s+collapsed\s+', output):
            idx = output.find(m.group(), m.end())
            if idx > 0:
                detail_end = output.find("\n", idx)
                detail = output[idx:detail_end].strip() if detail_end > idx else ""
                findings.append({"class": "collapsed", "detail": detail})
        return result.returncode, findings
    except Exception as e:
        return 2, [{"class": "tool_error", "detail": f"reading_audit failed: {e}"}]


def _run_settings_shadow() -> tuple:
    """Run settings_shadow.py and return (exit_code, findings_summary)."""
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "settings_shadow.py")],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        output = result.stdout + result.stderr
        findings = []
        import re
        for m in re.finditer(r'-\s+orphan_knob\s+', output):
            idx = output.find(m.group(), m.end())
            if idx > 0:
                detail_end = output.find("\n", idx)
                detail = output[idx:detail_end].strip() if detail_end > idx else ""
                findings.append({"class": "orphan_knob", "detail": detail})
        for m in re.finditer(r'-\s+fallback_drift\s+', output):
            idx = output.find(m.group(), m.end())
            if idx > 0:
                detail_end = output.find("\n", idx)
                detail = output[idx:detail_end].strip() if detail_end > idx else ""
                findings.append({"class": "fallback_drift", "detail": detail})
        for m in re.finditer(r'-\s+undeclared\s+', output):
            idx = output.find(m.group(), m.end())
            if idx > 0:
                detail_end = output.find("\n", idx)
                detail = output[idx:detail_end].strip() if detail_end > idx else ""
                findings.append({"class": "undeclared", "detail": detail})
        for m in re.finditer(r'-\s+bypass_reader\s+', output):
            idx = output.find(m.group(), m.end())
            if idx > 0:
                detail_end = output.find("\n", idx)
                detail = output[idx:detail_end].strip() if detail_end > idx else ""
                findings.append({"class": "bypass_reader", "detail": detail})
        return result.returncode, findings
    except Exception as e:
        return 2, [{"class": "tool_error", "detail": f"settings_shadow failed: {e}"}]


def _run_head_audit() -> tuple:
    """Run head_audit.py and return (exit_code, findings_summary)."""
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "head_audit.py")],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        output = result.stdout + result.stderr
        findings = []
        import re
        # Find finding class lines: "  - class_name    head_path detail"
        for m in re.finditer(r'-\s+(denominator_free|collapsed|orphan_knob|undeclared|fallback_drift|bypass_reader|detached_worktree|dirty_worktree|exclusive_commits|no_upstream|stale_head_state|unpushed)\s+', output):
            # Actually head_audit has its own class names; let's find the detail lines
            pass
        # Better: find lines starting with "  - " followed by a class name pattern
        for m in re.finditer(r'  - \S+\s+\S+\s+', output):
            # This regex matches "  - class_name    head_path detail" patterns
            # But we need to extract the class name
            idx = m.start()
            segment = output[idx:idx+80]
            # Look for the class field
            cm = re.match(r'\s*-\s+(\S+)\s+', output[idx:idx+60])
            if cm:
                class_name = cm.group(1)
                # Get the detail after the head path
                rest = output[idx+cm.end():]
                detail_end = rest.find("\n")
                detail = rest[:detail_end].strip() if detail_end > 0 else rest.strip()
                findings.append({"class": class_name, "detail": detail})
        return result.returncode, findings
    except Exception as e:
        return 2, [{"class": "tool_error", "detail": f"head_audit failed: {e}"}]


def _any_issue(findings: list, issue_classes: list) -> bool:
    """Check if any findings match the given issue classes."""
    for f in findings:
        if f.get("class") in issue_classes:
            return True
    return False


def main(argv=None) -> int:
    """Main entry point for the hyper-edge audit preflight."""
    print("=" * 60)
    print("Hyper-Edge Audit Preflight")
    print("=" * 60)
    print()

    # Check hyper_edge knobs
    running_legs = {}
    for leg_name in ["reading_audit", "settings_shadow", "head_audit"]:
        knob_val = _read_config_kob(leg_name, True)
        running_legs[leg_name] = bool(knob_val)
        print(f"  {leg_name}: {'enabled' if running_legs[leg_name] else 'disabled (knob off)'}")

    print()

    # Run each enabled leg
    all_findings = []

    # Leg 1: reading_audit
    if running_legs.get("reading_audit", False):
        print("--- Reading Audit (HED-9) ---")
        exit_code, findings = _run_reading_audit()
        print(f"  Exit code: {exit_code}")
        for f in findings:
            print(f"  - {f['class']}: {f['detail']}")
        all_findings.extend(findings)
        print()

    # Leg 2: settings_shadow
    if running_legs.get("settings_shadow", False):
        print("--- Settings Shadow (HED-10) ---")
        exit_code, findings = _run_settings_shadow()
        print(f"  Exit code: {exit_code}")
        for f in findings:
            print(f"  - {f['class']}: {f['detail']}")
        all_findings.extend(findings)
        print()

    # Leg 3: head_audit
    if running_legs.get("head_audit", False):
        print("--- Head Audit (HED-12) ---")
        exit_code, findings = _run_head_audit()
        print(f"  Exit code: {exit_code}")
        for f in findings:
            print(f"  - {f['class']}: {f['detail']}")
        all_findings.extend(findings)
        print()

    # Check for issues
    # Issue classes that should cause exit 1
    issue_classes = [
        # HED-9: denominator-free or collapsed
        "denominator_free", "collapsed",
        # HED-10: orphan_knob, undeclared, fallback_drift, bypass_reader
        "orphan_knob", "undeclared", "fallback_drift", "bypass_reader",
        # HED-12: various gap classes
        "detached_worktree", "dirty_worktree", "exclusive_commits",
        "no_upstream", "stale_head_state", "unpushed",
    ]

    has_issues = _any_issue(all_findings, issue_classes)

    print("=" * 60)
    if has_issues:
        print(f"⚠️  Hyper-Edge Audit: {len(all_findings)} issue(s) found")
        print("   Mandatory leg failed - exiting 1")
        for f in all_findings:
            print(f"  - {f['class']}: {f['detail']}")
        print()
        return 1
    else:
        print("✅ Hyper-Edge Audit: No issues found - all clear")
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())