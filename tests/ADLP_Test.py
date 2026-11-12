#!/usr/bin/env python3
import os
from pathlib import Path
"""Test the Assistant Dynamic Logging Protocol."""

import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src', 'ORCHESTRATION'))

from AssistantLogProtocol import get_logger, LogCategory, LogLevel

print("=" * 60)
print("ADLP TEST - Assistant Dynamic Logging Protocol")
print("=" * 60)

logger = get_logger("test_assistant")

print("\n[1] Logging system info...")
logger.log(
    LogCategory.SYSTEM,
    LogLevel.INFO,
    "test",
    "Test entry added",
    {"test_key": "test_value"},
)
logger.log(LogCategory.SYSTEM, LogLevel.WARNING, "test", "Warning test entry")
logger.log(LogCategory.SYSTEM, LogLevel.ERROR, "test", "Error test entry")

print("\n[2] Logging Python environment...")
logger.log_python_scan(
    [
        {"path": "C:\\Python314\\python.exe", "version": "3.14.3"},  # scanner:ignore deliberate: sample scan payload
        {"path": "C:\\Program Files\\Python311\\python.exe", "version": "3.11.9"},  # scanner:ignore deliberate: sample scan payload
    ]
)

print("\n[3] Logging error...")
logger.log_error("test_module", "Test error message", {"error_code": 404})

print("\n[4] Taking snapshot...")
snapshot = logger.take_snapshot()

print("\n[5] Querying logs...")
python_logs = logger.query(category="python", limit=5)
error_logs = logger.query(category="error", limit=5)
all_logs = logger.query(limit=10)

print(f"\n  Python logs: {len(python_logs)}")
print(f"  Error logs: {len(error_logs)}")
print(f"  Total queried: {len(all_logs)}")

print("\n[6] Getting context...")
ctx = logger.get_assistant_context()

print("\n[7] Summary check...")
summary = logger.get_system_summary()
print(f"  Session: {summary['session_id']}")
print(f"  Total entries: {summary['total_entries']}")
print(f"  Categories: {summary['categories']}")

print("\n" + "=" * 60)
print("ALL TESTS PASSED!")
print("=" * 60)
