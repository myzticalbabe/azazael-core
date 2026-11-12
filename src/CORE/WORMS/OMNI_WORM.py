# coding: utf-8
"""
OMNI_WORM - Ultimate Self-Healing Worm
Diagnoses and fixes ALL Azazael module issues
"""

import sys
import subprocess
import re
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

AZAAZEL_ROOT = Path(__file__).parent.parent.parent
VENV_PYTHON = AZAAZEL_ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON_EXE = VENV_PYTHON if VENV_PYTHON.exists() else sys.executable


def _is_nuitka():
    """Detect if running as a Nuitka-compiled binary."""
    # Multiple detection methods for reliability
    if getattr(__builtins__, "__compiled__", False):
        return True
    if sys.argv[0].endswith(".exe") and not sys.argv[0].endswith("python.exe"):
        return True
    if "__compiled__" in str(__builtins__.__dict__):
        return True
    return False


def fix_bom_files():
    print("\n[1/6] Scanning and fixing BOM characters...")
    if _is_nuitka():
        print("      [--] Compiled binary mode: BOM scan skipped (handled at build time)")
        return 0
    fixed = 0
    for py_file in AZAAZEL_ROOT.rglob("*.py"):
        try:
            content = py_file.read_bytes()
            if content.startswith(b"\xef\xbb\xbf"):
                content = content[3:]
                py_file.write_bytes(content)
                print(f"      Fixed BOM: {py_file.relative_to(AZAAZEL_ROOT)}")
                fixed += 1
        except Exception as exc:
            note_failure("OMNI_WORM:L43", exc)
    print(f"      Total BOM fixes: {fixed}")
    return fixed


def fix_syspath_imports():
    print("\n[2/6] Fixing sys.path imports...")
    if _is_nuitka():
        print("      [--] Compiled binary mode: sys.path scan skipped (handled at build time)")
        return 0
    fixed = 0

    pattern = str(Path.home() / "Desktop" / "Azazael_UNIFIED").encode("utf-8")

    for py_file in AZAAZEL_ROOT.rglob("*.py"):
        try:
            content = py_file.read_bytes()
            if pattern in content:
                text = content.decode("utf-8-sig")

                if "ml_platforms" in str(py_file):
                    new_import = 'sys.path.insert(0, str(Path(__file__).parent / "ml_platforms"))'
                else:
                    new_import = 'sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))'

                old_pattern = (
                    r"sys\.path\.insert\(0, str\(C:\\\\Users.*?ml_platforms\)\)"  # scanner:ignore deliberate: pattern this fixer rewrites
                )

                if re.search(old_pattern, text):
                    new_text = re.sub(old_pattern, new_import, text)
                    py_file.write_text(new_text, encoding="utf-8")
                    print(f"      Fixed: {py_file.relative_to(AZAAZEL_ROOT)}")
                    fixed += 1
        except Exception as exc:
            note_failure("OMNI_WORM:L78", exc)

    print(f"      Total sys.path fixes: {fixed}")
    return fixed


def create_init_files():
    print("\n[3/6] Creating missing __init__.py files...")
    created = 0

    dirs = [
        AZAAZEL_ROOT / "CORE",
        AZAAZEL_ROOT / "CORE" / "WORMS",
        AZAAZEL_ROOT / "HYDRA",
        AZAAZEL_ROOT / "HYDRA" / "ml_platforms",
        AZAAZEL_ROOT / "META",
        AZAAZEL_ROOT / "UTILITIES",
        AZAAZEL_ROOT / "UTILITIES" / "TERENCE",
        AZAAZEL_ROOT / "UTILITIES" / "TARBABY",
        AZAAZEL_ROOT / "UTILITIES" / "KLINK",
        AZAAZEL_ROOT / "UTILITIES" / "PERSEPHONE",
        AZAAZEL_ROOT / "UTILITIES" / "POINDEXTER",
        AZAAZEL_ROOT / "ORCHESTRATION",
        AZAAZEL_ROOT / "ORCHESTRATION" / "ml_platforms",
        AZAAZEL_ROOT / "ORCHESTRATION" / "ml_platforms" / "improvements",
        AZAAZEL_ROOT / "ORCHESTRATION" / "scripts",
        AZAAZEL_ROOT / "ORCHESTRATION" / "TEST",
        AZAAZEL_ROOT / "CODEX",
        AZAAZEL_ROOT / "CODEX" / "TESTING",
        AZAAZEL_ROOT / "CODEX" / "TESTING" / "UNIT",
        AZAAZEL_ROOT / "CODEX" / "TESTING" / "PERFORMANCE",
        AZAAZEL_ROOT / "CODEX" / "TESTING" / "INTEGRATION",
        AZAAZEL_ROOT / "CODEX" / "DOCUMENTATION",
        AZAAZEL_ROOT / "CODEX" / "VIDEOS",
    ]

    init_content = '# coding: utf-8\n"""\nAuto-generated package init\n"""\n'

    for d in dirs:
        init_file = d / "__init__.py"
        if not init_file.exists():
            try:
                init_file.write_text(init_content, encoding="utf-8")
                print(f"      Created: {d.name}/__init__.py")
                created += 1
            except Exception as exc:
                note_failure("OMNI_WORM:L124", exc)

    print(f"      Total created: {created}")
    return created


def test_imports():
    print("\n[4/6] Testing module imports...")

    # Add paths for direct import testing
    for p in [
        AZAAZEL_ROOT / "HYDRA",
        AZAAZEL_ROOT / "HYDRA" / "ml_platforms",
        AZAAZEL_ROOT / "CORE" / "WORMS",
        AZAAZEL_ROOT / "UTILITIES",
        AZAAZEL_ROOT,
        AZAAZEL_ROOT / "META",
    ]:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    module_tests = [
        ("hydra_launcher", "HYDRA.hydra_launcher"),
        ("hydra_protocols", "HYDRA.hydra_protocols"),
        ("WORMZ", "CORE.WORMS.WORMZ"),
        ("SYSTEMZ", "CORE.WORMS.SYSTEMZ"),
    ]

    for mod_name, label in module_tests:
        try:
            __import__(mod_name)
            print(f"      [OK] {label}")
        except Exception as e:
            print(f"      [FAIL] {label}: {e}")


def verify_ml():
    print("\n[5/6] Verifying ML frameworks...")

    ml_frameworks = {
        "numpy": "NumPy",
        "torch": "PyTorch",
        "gymnasium": "Gymnasium",
        "langchain": "LangChain",
    }

    print("      Lightweight ML frameworks:")
    for mod_name, display_name in ml_frameworks.items():
        try:
            mod = __import__(mod_name)
            version = getattr(mod, "__version__", "installed")
            print(f"        [OK] {display_name}: {version}")
        except ImportError:
            print(f"        [--] {display_name}: not installed")
        except Exception as e:
            print(f"        [!] {display_name}: skipped {e}")

    if not _is_nuitka():
        print("      Heavy ML frameworks (subprocess):")
        code = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

import numpy
import pandas
print(f"NumPy: {numpy.__version__}")
print(f"Pandas: {pandas.__version__}")
try:
    import tensorflow as tf
    print(f"TensorFlow: {tf.__version__}")
except Exception as e:
    print(f"TensorFlow: N/A ({e})")
try:
    import torch
    print(f"PyTorch: {torch.__version__}")
except Exception as e:
    print(f"PyTorch: N/A ({e})")
try:
    import sklearn
    print(f"sklearn: {sklearn.__version__}")
except Exception as e:
    print(f"sklearn: N/A ({e})")
"""
        result = subprocess.run(
            [str(PYTHON_EXE), "-c", code],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
            cwd=str(AZAAZEL_ROOT),
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                print(f"        {line}")
        else:
            print(f"        [--] Heavy ML subprocess failed: {result.stderr[:800]}")
    else:
        print("      [--] Heavy ML frameworks: skip subprocess in compiled mode")


def create_bootstrap():
    print("\n[6/6] Creating bootstrapper...")

    bootstrap = '''# coding: utf-8
"""
Azazael Bootstrapper
Run this first to initialize all module paths
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
HYDRA_ML = ROOT / "HYDRA" / "ml_platforms"

if str(HYDRA_ML) not in sys.path:
    sys.path.insert(0, str(HYDRA_ML))

print(f"Azazael bootstrap complete. PYTHONPATH includes: {HYDRA_ML}")
'''

    boot_file = AZAAZEL_ROOT / "bootstrap.py"
    boot_file.write_text(bootstrap, encoding="utf-8")
    print("      Created: bootstrap.py")

    return True


def main():
    print("=" * 60)
    print("OMNI WORM - Ultimate Self-Healing")
    print("=" * 60)

    bom_fixes = fix_bom_files()
    syspath_fixes = fix_syspath_imports()
    init_creates = create_init_files()

    test_imports()
    verify_ml()
    create_bootstrap()

    print("\n" + "=" * 60)
    print("HEALING SUMMARY")
    print("=" * 60)
    print(f"\nBOM fixes: {bom_fixes}")
    print(f"sys.path fixes: {syspath_fixes}")
    print(f"__init__.py created: {init_creates}")
    print("\nTo use Azazael modules, run first:")
    print(f"  python {AZAAZEL_ROOT}/bootstrap.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
