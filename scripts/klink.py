#!/usr/bin/env python3
"""
KLINK - Azazael Ecosystem Unified Command Launcher (shim)
Delegates to canonical implementation at src/UTILITIES/KLINK/klink.py
"""
import os
import sys

# Ensure the canonical KLINK is on the path
_klink_dir = os.path.join(
    os.environ.get("AZAZAEL_ROOT",
    os.environ.get("Azazael_ROOT",
    os.path.expanduser("~/azazael"))),
    "src", "UTILITIES", "KLINK"
)
if _klink_dir not in sys.path:
    sys.path.insert(0, _klink_dir)

# Import and run from canonical source
import klink
if __name__ == "__main__":
    klink.main()
