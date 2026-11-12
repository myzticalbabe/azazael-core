#!/usr/bin/env python3
# coding: utf-8
"""
CAIN Obsolescence Accountability Runner
========================================

Runner script for CAIN Obsolescence Accountability CLI.
Run from project root: python tools/cain_obsolescence_runner.py [command]

This script sets up the path and runs the CLI module.
"""

import sys
import os
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Now run the CLI
from src.ARC.cain_obsolescence_accountability.cain_obsolescence_cli import main

if __name__ == "__main__":
    sys.exit(main())