"""
SystemManager vs FusionHealer — Scan Comparison
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Desktop/azazael-project-e"))

print("=" * 65)
print("  SystemManager vs FusionHealer — Scan Comparison")
print("=" * 65)

# ── FusionHealer scan ──
print("\n[1/2] FusionHealer scan...", flush=True)
from FusionHealer import FusionHealer
healer = FusionHealer()
healer.scan_system()

fh_healed = len(healer.healed)
fh_errors = len(healer.errors)
fh_warnings = len(healer.warnings)
fh_report_path = healer.az_root / "system_report.json"

print(f"  Healed:  {fh_healed}")
print(f"  Errors:  {fh_errors}")
print(f"  Warnings:{fh_warnings}")

# ── SystemManager scan ──
print("\n[2/2] SystemManager report...", flush=True)
sm_report_path = Path.home() / ".appdata" / "SystemManager" / "system_report.json"
if sm_report_path.exists():
    report = json.loads(sm_report_path.read_text(encoding="utf-8"))
    sm_crawlers = len(report.get("crawlers", {}))
    sm_total_items = sum(report.get("crawlers", {}).values())
    print(f"  Crawlers:     {sm_crawlers}")
    print(f"  Total items:  {sm_total_items}")
else:
    print(f"  No report found at {sm_report_path}")

# ── Comparison ──
print("\n" + "=" * 65)
print("  COMPARISON")
print("=" * 65)
print(f"  {'Metric':<30} {'FusionHealer':<18} {'SystemManager':<18}")
print(f"  {'-'*30} {'-'*18} {'-'*18}")
print(f"  {'Items found':<30} {fh_healed:<18} {sm_total_items if sm_report_path.exists() else 'N/A':<18}")
print(f"  {'Errors':<30} {fh_errors:<18} {'N/A':<18}")
print(f"  {'Warnings':<30} {fh_warnings:<18} {'N/A':<18}")
print(f"  {'Output file':<30} {'system_report.json':<18} {'system_report.json':<18}")
print(f"\n  FusionHealer focuses on: module health, import validation, Python paths")
print(f"  SystemManager focuses on: system resources, processes, disk, network")
print(f"\n  → They complement each other — run both for full coverage.")
print()
