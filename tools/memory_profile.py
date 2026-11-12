"""Memory Profiling — Tracks memory usage of each azazael module."""

import sys
import os
sys.path.insert(0, os.path.expanduser("~/Desktop/azazael-project-e"))

MODULES = [
    "ADLP_FusionCore",
    "FusionHealer",
    "HYDRA",
    "SystemManager",
    "SystemManagerGUI",
    "SelfImprovingGUI",
    "AssistantLogProtocol",
    "ADLP_MLOrchestrator",
    "ADLP_SystemTools",
    "PythonMapper_Quick",
]

print("=" * 65)
print("  AZAZAEL MODULE MEMORY PROFILING")
print("=" * 65)

# Get baseline
import tracemalloc
tracemalloc.start()
baseline = tracemalloc.get_traced_memory()

results = []
for mod_name in MODULES:
    # Clear any previous imports
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    # Reset trace
    tracemalloc.reset_peak()

    start_snapshot = tracemalloc.take_snapshot()
    try:
        __import__(mod_name)
        current, peak = tracemalloc.get_traced_memory()
        end_snapshot = tracemalloc.take_snapshot()
        stats = end_snapshot.compare_to(start_snapshot, 'lineno')
        top_stats = stats[:3]
        results.append((mod_name, current, peak, top_stats, "OK"))
    except Exception as e:
        results.append((mod_name, 0, 0, [], f"FAILED: {e}"))
        continue

    # Display
    current_kb = current / 1024
    peak_kb = peak / 1024
    print(f"\n  {mod_name:<30s} {current_kb:>8.1f} KB current  {peak_kb:>8.1f} KB peak")
    for stat in top_stats[:2]:
        print(f"    {stat}")

tracemalloc.stop()

print("\n" + "=" * 65)
print("  SUMMARY")
print("=" * 65)
for name, cur, peak, _, status in results:
    curr_kb = cur / 1024
    peak_kb = peak / 1024
    badge = "✅" if status == "OK" else "❌"
    print(f"  {badge} {name:<30s} {curr_kb:>8.1f} KB")
print(f"\n  Total: {sum(r[1] for r in results if r[4]=='OK')/1024:.1f} KB")
print()
