# coding: utf-8
"""


__all__ = [
    "AZAAZEL_ROOT",
    "AzazaelNode",
    "VENV_PYTHON",
    "main",
]

NODE - Network Organized Development Engine
Azazael Ecosystem Activation Node
Fixes deficiencies and activates all ML frameworks
"""

import json
import re
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Any, List

from CORE.paths import get_src_dir

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

AZAAZEL_ROOT = get_src_dir()
VENV_PYTHON = AZAAZEL_ROOT / ".venv" / "Scripts" / "python.exe"


class AzazaelNode:
    def __init__(self):
        self.deficiencies_fixed = []
        self.ml_activated = {}
        self.status = {}

    def scan_deficiencies(self) -> dict[str, list[str]]:
        issues = {
            "bom_files": [],
            "syntax_errors": [],
            "import_errors": [],
            "missing_imports": [],
        }

        for py_file in AZAAZEL_ROOT.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8-sig")
                if content.startswith("\ufeff"):
                    issues["bom_files"].append(str(py_file))
                if "sys.path.insert" in content and "C:\\Users" in content:  # scanner:ignore deliberate: detector for this anti-pattern
                    issues["syntax_errors"].append(str(py_file))
            except Exception as exc:
                note_failure("NODE:L51", exc)
                issues["syntax_errors"].append(str(py_file))

        return issues

    def fix_bom(self, file_path: str) -> bool:
        try:
            path = Path(file_path)
            content = path.read_text(encoding="utf-8-sig")
            content = content.replace("\ufeff", "", 1)
            path.write_text(content, encoding="utf-8")
            self.deficiencies_fixed.append(f"BOM removed: {file_path}")
            return True
        except Exception as exc:
            note_failure("NODE:L64", exc)
            return False

    def fix_bad_syspath(self, file_path: str) -> bool:
        try:
            path = Path(file_path)
            content = path.read_text(encoding="utf-8")

            if "HYDRA\\ml_platforms" in file_path:
                new_pattern = (
                    'sys.path.insert(0, str(Path(__file__).parent / "ml_platforms"))'
                )
            elif "ml_platforms" in file_path:
                new_pattern = 'sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))'
            else:
                new_pattern = 'sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))'

            old_pattern = r"sys\.path\.insert\(0, str\(C:\\\\Users.*?ml_platforms\)\)"  # scanner:ignore deliberate: pattern this fixer rewrites

            if re.search(old_pattern, content):
                new_content = re.sub(old_pattern, new_pattern, content)
                if new_content != content:
                    path.write_text(new_content, encoding="utf-8")
                    self.deficiencies_fixed.append(f"sys.path fixed: {file_path}")
                    return True
            return False
        except Exception as exc:
            note_failure("NODE:L90", exc)
            return False

    def activate_ml(self) -> dict[str, Any]:
        results = {}

        frameworks = {
            "tensorflow": "import tensorflow as tf; print(tf.__version__)",
            "torch": "import torch; print(torch.__version__)",
            "sklearn": "import sklearn; print(sklearn.__version__)",
            "numpy": "import numpy; print(numpy.__version__)",
            "pandas": "import pandas; print(pandas.__version__)",
            "keras": "import keras; print(keras.__version__)",
            "scipy": "import scipy; print(scipy.__version__)",
        }

        for name, code in frameworks.items():
            try:
                result = subprocess.run(
                    [str(VENV_PYTHON), "-c", code],
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=30,
                )
                version = result.stdout.strip() or result.stderr.strip()
                results[name] = ("OK", version)
                self.ml_activated[name] = version
            except Exception as e:
                note_failure("NODE:L117", e)
                results[name] = ("FAILED", str(e))
                self.ml_activated[name] = "ERROR"

        return results

    def run(self) -> dict[str, Any]:
        return {
            "timestamp": datetime.now().isoformat(),
            "ml_frameworks": self.activate_ml(),
            "deficiencies_found": self.scan_deficiencies(),
        }


def main():
    print("=" * 60)
    print("AZAAZEL NODE - Network Organized Development Engine")
    print("=" * 60)
    print()

    node = AzazaelNode()

    print("[1/4] Scanning deficiencies...")
    issues = node.scan_deficiencies()
    for cat, files in issues.items():
        if files:
            print(f"      {cat}: {len(files)} found")

            print("\n[2/4] Activating ML frameworks...")
            ml_status = node.activate_ml()
            for fw, (status, version) in ml_status.items():
                icon = "[OK]" if status == "OK" else "[FAIL]"
                print(f"      {icon} {fw}: {version}")

                print("\n[3/4] Applying fixes...")
                fixes_applied = 0
                for file_path in issues.get("bom_files", []):
                    if node.fix_bom(file_path):
                        fixes_applied += 1
                        for file_path in issues.get("syntax_errors", []):
                            if isinstance(file_path, str) and file_path.endswith(".py"):  # type: ignore[reportUnnecessaryIsInstance]
                                if node.fix_bad_syspath(file_path):
                                        fixes_applied += 1

                                print(f"      Fixed: {fixes_applied} issues")

                                print("\n[4/4] Saving status...")
                                status = node.run()

                                status_path = AZAAZEL_ROOT / "CODEX" / "DOCUMENTATION" / "NODE_STATUS.json"
                                status_path.parent.mkdir(parents=True, exist_ok=True)
                                with open(status_path, "w", encoding="utf-8") as f:
                                    json.dump(status, f, indent=2, default=str)
                                    print(f"      Status: {status_path}")

                                    print()
                                    print("=" * 60)
                                    print("NODE COMPLETE")
                                    print("=" * 60)

                                    return status


if __name__ == "__main__":
    main()
