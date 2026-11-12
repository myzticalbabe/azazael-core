# coding: utf-8
"""
HEALING_WORM - Self-Healing Module Activation
Fixes import paths and activates all Azazael modules
"""

import sys
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


def _is_nuitka():
    """Detect if running as a Nuitka-compiled binary."""
    return "__compiled__" in dir(__builtins__)


def _find_python():
    """Find a real Python interpreter on the system."""
    if not _is_nuitka():
        return sys.executable
    # In Nuitka context, try to find a real Python.
    # Production is the free-threaded 3.15t venv. The 3.14t venv was retired
    # and archived 2026-10-19; the 3.14m venv is the only sanctioned 3.14
    # runtime, for a package that genuinely has no 3.15 build.
    candidates = [
        Path("C:/Azazael/.venvs/py315t/Scripts/python.exe"),
        Path("C:/Azazael/.venvs/py315/Scripts/python.exe"),
        Path("C:/Azazael/.venvs/py314m/Scripts/python.exe"),
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    # Fallback: try 'python' on PATH
    return "python"


def run_healing():
    print("=" * 60)
    print("HEALING WORM - Self-Healing Module Activation")
    print("=" * 60)
    print()

    fixes = []

    print("[1/4] Creating __init__.py files for all packages...")

    init_files = [
        AZAAZEL_ROOT / "CORE" / "__init__.py",
        AZAAZEL_ROOT / "CORE" / "WORMS" / "__init__.py",
        AZAAZEL_ROOT / "HYDRA" / "__init__.py",
        AZAAZEL_ROOT / "HYDRA" / "ml_platforms" / "__init__.py",
        AZAAZEL_ROOT / "META" / "__init__.py",
        AZAAZEL_ROOT / "UTILITIES" / "__init__.py",
        AZAAZEL_ROOT / "UTILITIES" / "TERENCE" / "__init__.py",
        AZAAZEL_ROOT / "UTILITIES" / "TARBABY" / "__init__.py",
        AZAAZEL_ROOT / "UTILITIES" / "KLINK" / "__init__.py",
        AZAAZEL_ROOT / "UTILITIES" / "PERSEPHONE" / "__init__.py",
        AZAAZEL_ROOT / "UTILITIES" / "POINDEXTER" / "__init__.py",
        AZAAZEL_ROOT / "ORCHESTRATION" / "__init__.py",
        AZAAZEL_ROOT / "ORCHESTRATION" / "ml_platforms" / "__init__.py",
        AZAAZEL_ROOT
        / "ORCHESTRATION"
        / "ml_platforms"
        / "improvements"
        / "__init__.py",
        AZAAZEL_ROOT / "ORCHESTRATION" / "scripts" / "__init__.py",
        AZAAZEL_ROOT / "ORCHESTRATION" / "TEST" / "__init__.py",
        AZAAZEL_ROOT / "CODEX" / "__init__.py",
        AZAAZEL_ROOT / "CODEX" / "TESTING" / "__init__.py",
        AZAAZEL_ROOT / "CODEX" / "TESTING" / "UNIT" / "__init__.py",
        AZAAZEL_ROOT / "CODEX" / "TESTING" / "PERFORMANCE" / "__init__.py",
        AZAAZEL_ROOT / "CODEX" / "TESTING" / "INTEGRATION" / "__init__.py",
        AZAAZEL_ROOT / "CODEX" / "DOCUMENTATION" / "__init__.py",
        AZAAZEL_ROOT / "CODEX" / "VIDEOS" / "__init__.py",
    ]

    for init_file in init_files:
        if not init_file.exists():
            init_file.parent.mkdir(parents=True, exist_ok=True)
            init_file.write_text("# coding: utf-8\n", encoding="utf-8")
            fixes.append(f"Created: {init_file.relative_to(AZAAZEL_ROOT)}")

    print(f"      Created: {len(fixes)} __init__.py files")

    print("\n[2/4] Fixing module imports...")

    modules_to_fix = {
        "HYDRA/hydra_launcher.py": [
            (
                r"sys\.path\.insert\(0, str\(C:\\\\Users.*?ml_platforms\)\)",  # scanner:ignore deliberate: pattern this fixer rewrites
                'sys.path.insert(0, str(Path(__file__).parent / "ml_platforms"))',
            ),
        ],
        "HYDRA/hydra_protocols.py": [
            (
                r"sys\.path\.insert\(0, str\(C:\\\\Users.*?ml_platforms\)\)",  # scanner:ignore deliberate: pattern this fixer rewrites
                'sys.path.insert(0, str(Path(__file__).parent / "ml_platforms"))',
            ),
        ],
        "META/HYDRA_CONTAINER.py": [
            (
                r"sys\.path\.insert\(0, str\(C:\\\\Users.*?ml_platforms\)\)",  # scanner:ignore deliberate: pattern this fixer rewrites
                'sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))',
            ),
        ],
        "META/HYDRAMETA.py": [
            (
                r"sys\.path\.insert\(0, str\(C:\\\\Users.*?ml_platforms\)\)",  # scanner:ignore deliberate: pattern this fixer rewrites
                'sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))',
            ),
        ],
        "CORE/WORMS/WORMZ.py": [
            (
                r"sys\.path\.insert\(0, str\(C:\\\\Users.*?ml_platforms\)\)",  # scanner:ignore deliberate: pattern this fixer rewrites
                'sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))',
            ),
        ],
        "CORE/WORMS/SYSTEMZ.py": [
            (
                r"sys\.path\.insert\(0, str\(C:\\\\Users.*?ml_platforms\)\)",  # scanner:ignore deliberate: pattern this fixer rewrites
                'sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))',
            ),
        ],
        "CORE/WORMS/CLUSTERWORMS.py": [
            (
                r"sys\.path\.insert\(0, str\(C:\\\\Users.*?ml_platforms\)\)",  # scanner:ignore deliberate: pattern this fixer rewrites
                'sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))',
            ),
        ],
    }

    for rel_path, replacements in modules_to_fix.items():
        file_path = AZAAZEL_ROOT / rel_path
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8-sig")
                original = content
                for old_pat, new_pat in replacements:
                    content = re.sub(old_pat, new_pat, content)
                if content != original:
                    file_path.write_text(content, encoding="utf-8")
                    fixes.append(f"Fixed imports: {rel_path}")
            except Exception as e:
                note_failure("HEALING_WORM:L142", e)
                fixes.append(f"Error fixing {rel_path}: {e}")

    print(f"      Fixed {len([f for f in fixes if 'Fixed' in f])} modules")

    print("\n[3/4] Testing module imports...")

    # Add module paths for direct import testing
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

    print("      Module status:")
    for mod_name, label in module_tests:
        try:
            __import__(mod_name)
            print(f"        [OK] {label}")
        except Exception as e:
            print(f"        [FAIL] {label}: {e}")

    print("\n[4/4] Verifying ML frameworks...")

    ml_frameworks = {
        "numpy": "NumPy",
        "torch": "PyTorch",
        "langchain": "LangChain",
        "gymnasium": "Gym",
    }

    for mod_name, display_name in ml_frameworks.items():
        try:
            mod = __import__(mod_name)
            version = getattr(mod, "__version__", "installed")
            print(f"        [OK] {display_name}: {version}")
        except ImportError:
            print(f"        [--] {display_name}: not installed")
        except Exception as e:
            print(f"        [!] {display_name}: {e}")

    # Also check heavy ML frameworks (may crash on 3.14t)
    heavy_frameworks = ["tensorflow", "sklearn", "xgboost", "transformers"]
    for mod_name in heavy_frameworks:
        try:
            mod = __import__(mod_name)
            version = getattr(mod, "__version__", "installed")
            print(f"        [OK] {mod_name}: {version}")
        except ImportError:
            print(f"        [--] {mod_name}: not installed")
        except Exception as e:
            print(f"        [!] {mod_name}: skipped ({e})")

    print("\n" + "=" * 60)
    print("HEALING COMPLETE")
    print("=" * 60)
    print(f"\nTotal fixes: {len(fixes)}")
    for fix in fixes[:15]:
        print(f"  - {fix}")

    return fixes


if __name__ == "__main__":
    run_healing()
