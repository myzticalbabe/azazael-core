# coding: utf-8
"""
TWITLER - System Information & Notification System
Add to Complete Systems Analysis

NOTE: The path in the TWITLER data dictionary is a historical snapshot
from the pre-reorganization era. The TWITLER project may no longer exist
at that location.
"""


TWITLER = {
    "description": "System Information & Notification System",
    "type": "Monitoring & Notifications",
    "path": "C:/Users/clint/Desktop/TWITLER/",
    "files": [
        "TWITLER.bat",
        "TWITLER_Admin.ps1",
        "install_deps.bat",
        "monitor_system.bat",
        "quick_health.bat",
        "quick_status.bat",
        "RUN_THIS_AS_ADMIN.bat",
        "run_wormz_ml.bat",
        "scripts/",
    ],
    "features": [
        "System information gathering",
        "Health monitoring",
        "Quick status checks",
        "WORMZ_ML execution",
        "Admin operations",
        "Notification system",
        "Dependency installation",
    ],
    "commands": {
        "quick_status.bat": "Quick system status check",
        "quick_health.bat": "Health monitoring",
        "monitor_system.bat": "Continuous monitoring",
        "install_deps.bat": "Install dependencies",
        "run_wormz_ml.bat": "Run ML worms",
        "RUN_THIS_AS_ADMIN.bat": "Admin operations",
        "TWITLER.bat": "Main launcher",
        "TWITLER_Admin.ps1": "PowerShell admin interface",
    },
}


def print_twitler_info():
    print("=" * 60)
    print("TWITLER - System Information & Notification")
    print("=" * 60)
    print(f"\nPath: {TWITLER['path']}")
    print(f"Type: {TWITLER['type']}")
    print(f"\nFiles ({len(TWITLER['files'])}):")
    for f in TWITLER["files"]:
        print(f"  - {f}")
    print("\nFeatures:")
    for f in TWITLER["features"]:
        print(f"  - {f}")
    print("\nCommands:")
    for cmd, desc in TWITLER["commands"].items():
        print(f"  {cmd}: {desc}")


if __name__ == "__main__":
    print_twitler_info()
