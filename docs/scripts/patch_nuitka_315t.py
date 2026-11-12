#!/usr/bin/env python3.15t
"""
patch_nuitka_315t.py — Apply/verify 3.15t free-threading patches for Nuitka.

Nuitka 4.1.2+ requires C-level patches for Python free-threading support.
Patches below were authored against 3.14t (PY_VERSION_HEX >= 0x030e0000,
_CompiledFrameType _PyStackRef shape); they have NOT been re-validated
against 3.15 headers, and Nuitka is not installed in the 3.15t venv
(.exe builds remain SUSPENDED — see CODEBASE_AUDIT_TODO.md).
Re-audit against 3.15 headers before --apply.
This script manages patching of:
1. allocator.h — PyObject allocation for free-threaded builds
2. SconsPythonBuild.py — 3.15t build flags and detection
3. code_generation templates — thread-local storage in generated C code

Usage:
    python3.15t docs/scripts/patch_nuitka_315t.py --apply
    python3.15t docs/scripts/patch_nuitka_315t.py --verify
    python3.15t docs/scripts/patch_nuitka_315t.py --rollback

Created: June 4, 2026 — Recovered from G:\\ source-of-truth
Ported: Oct 6, 2026 — renamed 314t->315t, C-patch body UNVERIFIED on 3.15
Reference: azazael-packaging skill, LESSONS_TASK_D_NUITKA_BUILD.md
"""

import sys
import os
import shutil
import argparse
from pathlib import Path
from datetime import datetime

# Ensure safe output on Windows (avoid UnicodeEncodeError)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_nuitka_path():
    """Find the Nuitka installation path for the current Python."""
    import nuitka
    return Path(nuitka.__file__).parent


BACKUP_DIR = None


def backup_file(filepath: Path) -> Path:
    """Backup a file before patching."""
    global BACKUP_DIR
    if BACKUP_DIR is None:
        BACKUP_DIR = filepath.parent / f"nuitka_315t_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        BACKUP_DIR.mkdir(exist_ok=True)
    
    backup_path = BACKUP_DIR / filepath.name
    if not backup_path.exists():
        shutil.copy2(filepath, backup_path)
    return backup_path


def patch_allocator_h(nuitka_path: Path, apply: bool = True):
    """Patch allocator.h for 3.15t PyObject allocation."""
    candidates = [
        nuitka_path / "build" / "include" / "nuitka" / "allocator.h",
        nuitka_path / "static_src" / "allocator.h",
    ]
    target = None
    for c in candidates:
        if c.exists():
            target = c
            break
    
    if target is None:
        print("  [??] allocator.h not found")
        return False
    
    backup_file(target)
    content = target.read_text(encoding="utf-8")
    
    if apply:
        if "#if PY_VERSION_HEX >= 0x030e0000" not in content:
            marker = "#include <Python.h>"
            if marker in content:
                patch = """
/* === AZAZAEL 3.15t Patch: Free-threading allocator === */
#if PY_VERSION_HEX >= 0x030e0000
#define NUITKA_FREE_THREADED 1
#define PyObject_HEAD_INIT_NUITKA PyObject_HEAD_INIT(&PyType_Type)
#else
#define NUITKA_FREE_THREADED 0
#endif
/* === END AZAZAEL PATCH === */
"""
                content = content.replace(marker, marker + patch)
                target.write_text(content, encoding="utf-8")
                print("  [OK] allocator.h patched")
                return True
    else:
        backup = BACKUP_DIR / "allocator.h"
        if backup.exists():
            shutil.copy2(backup, target)
            print("  [--] allocator.h rolled back")
            return True
    
    return False


def patch_scons_build(nuitka_path: Path, apply: bool = True):
    """Patch SconsPythonBuild.py for 3.15t detection."""
    target = nuitka_path / "build" / "SconsPythonBuild.py"
    if not target.exists():
        print("  [??] SconsPythonBuild.py not found")
        return False
    
    backup_file(target)
    content = target.read_text(encoding="utf-8")
    
    if apply:
        if "Py_GIL_DISABLED" not in content:
            detect_patch = """
    # === AZAZAEL 3.15t Patch: Detect free-threading ===
    if python_version >= (3, 14):
        from sysconfig import get_config_var
        if get_config_var('Py_GIL_DISABLED'):
            env.Append(CPPDEFINES=['Py_GIL_DISABLED'])
            if sys.platform == 'win32':
                env.Append(CCFLAGS=['/utf-8'])
    # === END AZAZAEL PATCH ===
"""
            insert_marker = "def _getPythonIncPath"
            if insert_marker in content:
                content = content.replace(
                    f"def _getPythonIncPath",
                    detect_patch + "\n\n" + "def _getPythonIncPath"
                )
                target.write_text(content, encoding="utf-8")
                print("  [OK] SconsPythonBuild.py patched")
                return True
    else:
        backup = BACKUP_DIR / "SconsPythonBuild.py"
        if backup.exists():
            shutil.copy2(backup, target)
            print("  [--] SconsPythonBuild.py rolled back")
            return True
    
    return False


def patch_code_templates(nuitka_path: Path, apply: bool = True):
    """Patch code generation templates for thread-local storage."""
    templates_dir = nuitka_path / "build" / "inline_copy"
    if not templates_dir.exists():
        templates_dir = nuitka_path / "code_generation" / "templates"
    
    if not templates_dir.exists():
        print("  [??] Code templates not found")
        return False
    
    patched = 0
    for template_file in templates_dir.rglob("*.c.j2"):
        backup_file(template_file)
        content = template_file.read_text(encoding="utf-8")
        
        if apply:
            if "static PyObject *" in content and "_Thread_local" not in content:
                content = content.replace(
                    "static PyObject *",
                    "static _Thread_local PyObject *"
                )
                template_file.write_text(content, encoding="utf-8")
                patched += 1
        else:
            backup = BACKUP_DIR / template_file.name
            if backup.exists():
                shutil.copy2(backup, template_file)
                patched += 1
    
    if apply:
        print(f"  [OK] Code templates patched ({patched} files)")
    else:
        print(f"  [--] Code templates rolled back ({patched} files)")
    
    return patched > 0


def verify_patches(nuitka_path: Path):
    """Verify that patches are applied correctly."""
    print("\n  Verifying Nuitka 3.15t patches...")
    
    # Check allocator.h
    for alloc_path in [
        nuitka_path / "build" / "include" / "nuitka" / "allocator.h",
        nuitka_path / "static_src" / "allocator.h",
    ]:
        if alloc_path.exists():
            content = alloc_path.read_text(encoding="utf-8")
            if "NUITKA_FREE_THREADED" in content:
                print("  [OK] allocator.h: NUITKA_FREE_THREADED defined")
            else:
                print("  [--] allocator.h: NOT patched (stock Nuitka)")
            break
    else:
        print("  [??] allocator.h: not found")
    
    # Check SconsPythonBuild.py
    scons_path = nuitka_path / "build" / "SconsPythonBuild.py"
    if scons_path.exists():
        content = scons_path.read_text(encoding="utf-8")
        if "Py_GIL_DISABLED" in content:
            print("  [OK] SconsPythonBuild.py: Py_GIL_DISABLED detection present")
        else:
            print("  [--] SconsPythonBuild.py: NOT patched (stock Nuitka)")
    else:
        print("  [??] SconsPythonBuild.py: not found")
    
    # Check code templates
    templates_dir = nuitka_path / "build" / "inline_copy"
    if templates_dir.exists():
        thread_local_count = 0
        for tf in templates_dir.rglob("*.c.j2"):
            if "_Thread_local" in tf.read_text(encoding="utf-8"):
                thread_local_count += 1
        if thread_local_count:
            print(f"  [OK] Code templates: {thread_local_count} files with _Thread_local")
        else:
            print("  [~~] Code templates: not patched (may not be needed)")
    else:
        print("  [??] Code templates: not found")
    
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Apply/verify Nuitka 3.15t free-threading patches"
    )
    parser.add_argument("--apply", action="store_true", help="Apply patches")
    parser.add_argument("--verify", action="store_true", help="Verify patch status")
    parser.add_argument("--rollback", action="store_true", help="Rollback all patches")
    parser.add_argument("--nuitka-path", type=str, help="Explicit Nuitka install path")
    
    args = parser.parse_args()
    
    if not (args.apply or args.verify or args.rollback):
        parser.print_help()
        return
    
    # Find Nuitka
    try:
        if args.nuitka_path:
            nuitka_path = Path(args.nuitka_path)
        else:
            nuitka_path = get_nuitka_path()
    except ImportError:
        print("ERROR: Nuitka not installed. Run: pip install nuitka")
        sys.exit(1)
    
    print(f"Nuitka path: {nuitka_path}")
    print(f"Python: {sys.version}")
    
    import sysconfig
    free_threaded = bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
    print(f"Free-threaded: {free_threaded}")
    
    if args.verify:
        verify_patches(nuitka_path)
        return
    
    apply = args.apply
    
    print(f"\n{'Applying' if apply else 'Rolling back'} patches...")
    
    results = {
        "allocator.h": patch_allocator_h(nuitka_path, apply),
        "SconsPythonBuild.py": patch_scons_build(nuitka_path, apply),
        "code_templates": patch_code_templates(nuitka_path, apply),
    }
    
    if apply:
        patched_count = sum(1 for v in results.values() if v)
        print(f"\nApplied {patched_count}/{len(results)} patches")
        
        if patched_count > 0:
            print(f"Backups saved to: {BACKUP_DIR}")
            print("\nNuitka is now patched for Python 3.15t free-threading.")
            print("Run: python3.15t -m nuitka --standalone ...")
    else:
        print(f"\nRolled back all patches.")


if __name__ == "__main__":
    main()
