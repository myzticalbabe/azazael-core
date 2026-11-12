#!/usr/bin/env python3
import os
from pathlib import Path
"""
ML Test for Self-Improving GUI
Tests ML clustering and prediction functions.
"""

import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src', 'ORCHESTRATION'))

from ADLP_MLOrchestrator import ML_ORCH, activate_ml, predict, analyze, cluster, train

print("=" * 70)
print("  ML TEST FOR SELF-IMPROVING GUI")
print("=" * 70)
print()

print("Activating ML Orchestrator...")
result = activate_ml()
print(f"  Status: {result['status']}")
print()

print("--- CLUSTERING TEST ---")
widgets = [
    {"id": "nav1", "type": "button", "clicks": 45, "time": 120, "errors": 0},
    {"id": "nav2", "type": "button", "clicks": 38, "time": 95, "errors": 1},
    {"id": "disp1", "type": "canvas", "clicks": 120, "time": 300, "errors": 2},
    {"id": "disp2", "type": "text", "clicks": 200, "time": 450, "errors": 3},
    {"id": "ctrl1", "type": "button", "clicks": 67, "time": 180, "errors": 0},
    {"id": "ctrl2", "type": "button", "clicks": 54, "time": 140, "errors": 1},
    {"id": "info1", "type": "label", "clicks": 15, "time": 30, "errors": 0},
    {"id": "info2", "type": "text", "clicks": 89, "time": 220, "errors": 2},
]

print(f"Clustering {len(widgets)} widgets...")
cluster_result = cluster(widgets, 4)
print(f"  Clusters formed: {cluster_result['n_clusters']}")
for cid, items in cluster_result["clusters"].items():
    print(f"    Cluster {cid}: {len(items)} items")

print()
print("--- PREDICTION TEST ---")
predictions = [
    ("system_health", {"cpu_usage": 45, "memory_usage": 62, "disk_usage": 60}),
    ("system_health", {"cpu_usage": 85, "memory_usage": 90, "disk_usage": 75}),
    ("anomaly", {"values": [10, 12, 11, 13, 50]}),
    ("trend", {"values": [100, 105, 102, 108, 110]}),
]

for model, data in predictions:
    pred = predict(model, data)
    print(f"  {model}: {pred}")

print()
print("--- ANALYSIS TEST ---")
analysis_data = {
    "cpu_usage": 45,
    "memory_usage": 62,
    "disk_usage": 60,
    "network_activity": 75,
    "processes_running": 234,
}
analysis = analyze(analysis_data)
print(f"  Summary: {analysis['summary']}")
print(f"  Recommendations: {len(analysis['recommendations'])}")
for rec in analysis["recommendations"]:
    print(f"    - {rec}")

print()
print("--- TRAINING TEST ---")
train_data = [
    {"type": "widget", "clicks": 45, "time": 120},
    {"type": "widget", "clicks": 67, "time": 180},
    {"type": "widget", "clicks": 89, "time": 220},
]
train_result = train("widget_model", train_data)
print(f"  Model: {train_result['model']}")
print(f"  Trained: {train_result['trained']}")
print(f"  Samples: {train_result['samples']}")

print()
print("--- STATUS CHECK ---")
status = ML_ORCH.get_status()
for k, v in status.items():
    print(f"  {k}: {v}")

print()
print("=" * 70)
print("  ML TESTS COMPLETE")
print("=" * 70)
