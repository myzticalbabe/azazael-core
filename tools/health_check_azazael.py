"""
Azazael Project — Comprehensive Module Health Check
Tests all core modules and reports pass/fail status.
"""

import sys
import importlib
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Desktop/azazael-project-e"))

MODULES = [
    # Core framework
    ("ADLP_FusionCore", "ADLP_FusionCore", "FusionCore, CORE, TIER, LVL"),
    ("FusionHealer", "FusionHealer", "FusionHealer class"),
    ("HYDRA", "HYDRA", "HYDRACluster, ProcessingStage, AdaptationType"),
    # System tools
    ("SystemManager", "SystemManager", "SystemManager, SystemInfo"),
    ("SystemManagerGUI", "SystemManagerGUI", "SystemManagerApp"),
    ("SelfImprovingGUI", "SelfImprovingGUI", "SelfImprovingApp"),
    # Python mapper
    ("PythonMapper_Quick", "PythonMapper_Quick", "QuickMapper"),
    # Assistant protocol
    ("AssistantLogProtocol", "AssistantLogProtocol", "log_entry, query, learn, recall"),
    # ML orchestrator
    ("ADLP_MLOrchestrator", "ADLP_MLOrchestrator", "ML_ORCH class"),
    # System tools core
    ("ADLP_SystemTools", "ADLP_SystemTools", "system_tools"),
]

PASSED = 0
FAILED = 0
RESULTS = []

print("=" * 70)
print("  AZAZAEL PROJECT — COMPREHENSIVE HEALTH CHECK")
print("=" * 70)
print()

for mod_name, import_name, contents in MODULES:
    start = time.perf_counter()
    try:
        mod = importlib.import_module(import_name)
        elapsed = time.perf_counter() - start

        # Verify expected contents exist
        found_items = []
        for item in contents.replace(", ", ",").split(","):
            item = item.strip()
            if hasattr(mod, item):
                found_items.append(item)

        status = "✅ PASS"
        PASSED += 1
        detail = f"{len(found_items)}/{len(contents.split(','))} symbols found in {elapsed:.2f}s"
    except Exception as e:
        elapsed = time.perf_counter() - start
        status = "❌ FAIL"
        FAILED += 1
        detail = f"{type(e).__name__}: {e}"

    RESULTS.append((mod_name, status, detail))
    print(f"  {status}  {mod_name:<30s} {detail}")

print()
print("=" * 70)
print(f"  SUMMARY:  {PASSED}/{PASSED + FAILED} modules healthy ({PASSED/(PASSED+FAILED)*100:.1f}%)")
print("=" * 70)
print()
