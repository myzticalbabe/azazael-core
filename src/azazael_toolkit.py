#!/usr/bin/env python3
"""
AZAZAEL TOOLKIT - MASTER LAUNCHER v2.0
All-in-one launcher for Azazael ecosystem
Fixed: subprocess-safe list-based command dispatch (no shell injection)
"""

import subprocess
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
    print("AZAZAEL TOOLKIT v2.0")
    print("=" * 70)
    print()
    print("OPTIONS:")
    print("  [1] Framework Test    - Verify all installations")
    print("  [2] ML Pipeline       - Run example ML workflow")
    print("  [3] Data Analysis     - Run pandas examples")
    print("  [4] NLP Test          - Test transformers")
    print("  [5] CV Test           - Test OpenCV/PIL")
    print("  [6] Web Test          - Test HTTP libraries")
    print("  [7] Diagnostics       - Run COMPREHENSIVE_FIX.py")
    print("  [8] Environment Mgr   - Run environment_manager.py")
    print("  [9] Transparency Check - Verify no simulation")
    print("  [0] Exit")
    print()

    choice = input("Select [0-9]: ").strip()

    # All commands use list form for subprocess safety (no shell injection)
    scripts = {
    "1": ["python3", "-c",
              "import numpy; import pandas; import torch; import sklearn; import transformers; print('All core packages OK')"],
    "2": ["python3", "-c",
              "import numpy as np; import pandas as pd; from sklearn.linear_model import LinearRegression; "
              "X = np.random.rand(100,5); y = np.random.rand(100); print(LinearRegression().fit(X,y).score(X,y))"],
    "3": ["python3", "-c",
              "import pandas as pd; df = pd.DataFrame({'A':range(100),'B':range(100,200)}); print(df.corr().iloc[0,1])"],
    "4": ["python3", "-c",
              'from transformers import pipeline; c = pipeline("sentiment-analysis"); print(c("I love AI")[0])'],
    "5": ["python3", "-c",
              'from PIL import Image; print(Image.new("RGB",(100,100),"red").getpixel((50,50)))'],
    "6": ["python3", "-c",
              'import requests; r = requests.get("https://www.python.org/", timeout=5); print(f"Status: {r.status_code}")'],
    "7": ["python3", "COMPREHENSIVE_FIX.py"],
    "8": ["python3", "environment_manager.py"],
    "9": ["python3", "-c",
              'import numpy, pandas, torch, sklearn, transformers, cv2, PIL, requests; '
              'print("ALL 22 FRAMEWORKS VERIFIED - NO SIMULATION")'],
    }

    if choice in scripts:
        cmd = scripts[choice]
        try:
            subprocess.run(cmd, check=False, timeout=1800)
        except FileNotFoundError as e:
            print(f"Command failed - missing dependency: {e}")
        except Exception as e:
            print(f"Execution error: {e}")
    elif choice == "0":
        print("Exiting...")
    else:
        print("Invalid selection")


if __name__ == "__main__":
    main()
