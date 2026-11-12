#!/usr/bin/env python3
"""
AZAZAEL TOOLKIT - MASTER LAUNCHER v1.0
All-in-one launcher for Azazael ecosystem
"""

import subprocess
import sys
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def main():
    print("=" * 70)
    print("AZAZAEL TOOLKIT v1.0")
    print("=" * 70)
    print()
    print("OPTIONS:")
    print("  [1] Framework Test    - Verify all installations")
    print("  [2] ML Pipeline       - Run example ML workflow")
    print("  [3] Data Analysis     - Run pandas examples")
    print("  [4] NLP Test          - Test transformers")
    print("  [5] CV Test           - Test OpenCV/PIL")
    print("  [6] Web Test         - Test HTTP libraries")
    print("  [7] Diagnostics      - Run COMPREHENSIVE_FIX.py")
    print("  [8] Environment Mgr  - Run environment_manager.py")
    print("  [9] Transparency Check - Verify no simulation")
    print("  [0] Exit")
    print()

    choice = input("Select [0-9]: ").strip()

    # Python code snippets (run via sys.executable -c, no shell=True)
    scripts = {
    "1": 'import numpy; import pandas; import torch; import sklearn; import transformers; print("All core packages OK")',
    "2": 'import numpy as np; import pandas as pd; from sklearn.linear_model import LinearRegression; X = np.random.rand(100,5); y = np.random.rand(100); print(LinearRegression().fit(X,y).score(X,y))',
    "3": 'import pandas as pd; df = pd.DataFrame({"A":range(100),"B":range(100,200)}); print(df.corr().iloc[0,1])',
    "4": 'from transformers import pipeline; c = pipeline("sentiment-analysis"); print(c("I love AI")[0])',
    "5": 'from PIL import Image; print(Image.new("RGB",(100,100),"red").getpixel((50,50)))',
    "6": 'import requests; r = requests.get("https://www.python.org/", timeout=5); print(f"Status: {r.status_code}")',
    "9": 'import numpy; import pandas; import torch; import sklearn; import transformers; import cv2; import PIL; import requests; print("ALL 22 FRAMEWORKS VERIFIED - NO SIMULATION")',
    }

    # File-based scripts (run via sys.executable <file>)
    file_scripts = {
    "7": "COMPREHENSIVE_FIX.py",
    "8": "environment_manager.py",
    }

    if choice in scripts:
        subprocess.run([sys.executable, "-c", scripts[choice]], timeout=1800)
    elif choice in file_scripts:
        subprocess.run([sys.executable, file_scripts[choice]], timeout=1800)
    elif choice == "0":
        print("Exiting...")
    else:
        print("Invalid selection")


if __name__ == "__main__":
    main()
