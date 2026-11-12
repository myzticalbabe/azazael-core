#!/usr/bin/env python3.15t
"""
sync_all.py - Bidirectional sync across all Azazael environments.

Environments:
  1. Windows 11   : <this tree>                         (primary dev, derived)
  2. WSL2         : /root/azazael                  (Linux build target)
  3. Desktop      : C:/Azazael/desktop/AZAZAEL_Standalone (release)
  4. G: source    : G:/source-of-truth/azazael      (canonical archive)
  5. G: AZAZAEL   : G:/AZAZAEL/AZAZAEL_ROOT          (primary archive)
  6. Kali WSL2    : /root/azazael inside distro kali-linux (via wsl -d kali-linux;
                      E:/Kali holds only the distro ext4.vhdx, never a tree)

Usage:
    python3.15t sync_all.py --status          Show sync status
    python3.15t sync_all.py --sync-all        Sync all environments
    python3.15t sync_all.py --sync-to wsl2    Sync Windows to WSL2
    python3.15t sync_all.py --sync-to kali    Sync WSL2 to Kali
    python3.15t sync_all.py --verify          Verify integrity across all

Created: June 4, 2026 - Environment realignment session
"""

import os
import sys
import shutil
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Any

# Ensure UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════
# Environment Mapping
# ═══════════════════════════════════════════════════════════════════════

#: This file lives at <root>/scripts/sync_all.py, so the Windows hub root is
#: derived, never literal (U3/move-day: the tree moved C:/Users/clint/azazael
#: -> C:/Azazael/azazael; a literal would have pinned the old Ort).
_WINDOWS_ROOT = Path(__file__).resolve().parent.parent

ENVIRONMENTS: Dict[str, Dict] = {
    "windows": {
        "label": "Windows 11 (primary dev)",
        "root": _WINDOWS_ROOT,
        "src": _WINDOWS_ROOT / "src",
        "docs": _WINDOWS_ROOT / "docs",
        "tests": _WINDOWS_ROOT / "tests",
        "type": "windows",
    },
    "wsl2": {
        "label": "WSL2 Ubuntu-26.04",
        "root": Path("/root/azazael"),
        "src": Path("/root/azazael/src"),
        "docs": Path("/root/azazael/docs"),
        "tests": Path("/root/azazael/tests"),
        "type": "wsl",
        "wsl_distro": "Ubuntu-26.04",
    },
    "desktop": {
        "label": "Desktop AZAZAEL_Standalone",
        "root": Path("C:/Azazael/desktop/AZAZAEL_Standalone"),
        "src": None,
        "docs": Path("C:/Azazael/desktop/AZAZAEL_Standalone/docs"),
        "tests": None,
        "type": "release",
        "exe_only": True,
    },
    "g_source": {
        "label": "G: source-of-truth",
        "root": Path("G:/source-of-truth/azazael"),
        "src": Path("G:/source-of-truth/azazael/src"),
        "docs": Path("G:/source-of-truth/azazael/docs"),
        "tests": None,
        "type": "archive",
    },
    "g_root": {
        "label": "G: AZAZAEL_ROOT",
        "root": Path("G:/AZAZAEL/AZAZAEL_ROOT"),
        "src": Path("G:/AZAZAEL/AZAZAEL_ROOT/src"),
        "docs": Path("G:/AZAZAEL/AZAZAEL_ROOT/docs"),
        "tests": None,
        "type": "archive",
    },
    "kali": {
        "label": "Kali Linux (WSL2)",
        # The tree lives inside the distro. `E:/Kali/azazael` was never a real
        # path — E:\Kali holds only the distro's ext4.vhdx and an icon — so the
        # environment reported as missing while it was running with a full tree.
        "root": Path("/root/azazael"),
        "src": Path("/root/azazael/src"),
        "docs": Path("/root/azazael/docs"),
        "tests": Path("/root/azazael/tests"),
        "type": "wsl",
        "wsl_distro": "kali-linux",
        "wsl_root_user": True,
    },
}

SYNC_CODE_PATTERNS = [
    "src/CORE/WORMS/expansion/*.py",
    "src/CORE/WORMS/CLUSTERWORMS.py",
    "src/CORE/WORMS/METACLUSTER.py",
    "src/CORE/WORMS/NETWORK_DIAG.py",
    "src/CORE/WORMS/CODEX_TOOLS.py",
    "src/ORCHESTRATION/hydra_bridge.py",
    "tests/*.py",
]

SYNC_DOC_PATTERNS = [
    "docs/*.md",
    "docs/scripts/*.py",
    "LESSONS_*.md",
    "MEMORIES*.md",
    "MASTER_TASKS.md",
    "SYSTEM_INSTRUCTIONS.md",
    "HAT_TAXONOMY.md",
    # SOP.md added 2026-10-18: canon (RULE 0S) that was missing from the sync
    # patterns, which is exactly how G: source-of-truth drifted to a stale
    # copy carrying a duplicate 6-box checklist (16 open boxes vs 10 in every
    # other environment -- measured, see state/MISSING_TASKS_FORENSIC_2026-10-18.md).
    "SOP.md",
    # MANDATES.md added 2026-10-18: measured same gap (all 3 G:/G:\\ copies
    # drifted in content) -- together with SOP.md this closes the only two
    # canon (RULE 0K) docs that had no sync path. MD-only by design: the
    # module map source is Nuitka build output, not markdown.
    "MANDATES.md",
]

SYNC_SCRIPT_PATTERNS = [
    "scripts/*.py",
]

SYNC_SKILL_PATTERNS = [
    "skills/*.md",
]

def _detect_wsl() -> bool:
    """True only inside real WSL: non-Windows kernel carrying the Microsoft marker.

    The old check (Path("/root/azazael").exists()) misfired on Windows hosts
    that happen to have a C:\\root\\azazael directory, silently disabling the
    Windows->WSL2 leg. Platform gate first, marker second."""
    if sys.platform == "win32":
        return False
    marker = Path("/proc/version")
    if not marker.is_file():
        return False
    try:
        text = marker.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        text = ""
    return "microsoft" in text


# WSL environment detection
IS_WSL = _detect_wsl()


def _resolve_path(p: Path, env_type: str) -> Path:
    """Resolve a path for the current runtime. Converts C:/G:/ -> /mnt/ on WSL."""
    if IS_WSL and env_type != "wsl":
        return Path(wsl_path(p))
    return p


def _ensure_wsl_interop() -> bool:
    """Re-register the WSL interop binfmt handler if it went missing.

    Observed 2026-10-21: /proc/sys/fs/binfmt_misc/WSLInterop vanished
    mid-session with nothing taking action, and every wsl.exe exec then
    failed with ``OSError: Exec format error``. Re-registering is safe,
    idempotent, and session-scoped. Returns True when wsl.exe looks
    executable after the attempt.
    """
    import shutil
    if sys.platform == "win32":
        return shutil.which("wsl.exe") is not None
    handler = Path("/proc/sys/fs/binfmt_misc/WSLInterop")
    if handler.exists():
        return True
    try:
        if os.geteuid() == 0:
            Path("/proc/sys/fs/binfmt_misc/register").write_text(
                ":WSLInterop:M::MZ::/init:PF"
            )
    except OSError:
        # Re-register failed: report the handler's actual state (missing in
        # practice). A Call return is an explicit status check, not a
        # success-shaped constant, so the failure stays visible to callers.
        return handler.exists()
    return handler.exists()


def _exec_wsl_exe(argv: List[str], timeout: int) -> Tuple[int, str, str]:
    """Exec wsl.exe once. TimeoutExpired / OSError propagate to the caller."""
    result = subprocess.run(
        argv,
        capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
    )
    return result.returncode, result.stdout, result.stderr


def _run_wsl_with_heal(argv: List[str], timeout: int, unavailable: str) -> Tuple[int, str, str]:
    """Run wsl.exe, self-healing a dropped interop handler with one retry."""
    try:
        return _exec_wsl_exe(argv, timeout)
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except OSError:
        # FileNotFoundError (no wsl.exe) or Exec format error (WSLInterop
        # handler dropped mid-session). Re-register when possible, retry once.
        if _ensure_wsl_interop():
            try:
                return _exec_wsl_exe(argv, timeout)
            except (subprocess.TimeoutExpired, OSError):
                return -1, "", unavailable
        return -1, "", unavailable


def run_wsl(cmd: str, timeout: int = 60) -> Tuple[int, str, str]:
    """Run a command inside WSL2 (Ubuntu-26.04).
    
    Execs wsl.exe directly rather than through a shell. `shell=True` on Windows
    means cmd.exe, which does not treat single quotes as quoting and reads
    `2>/dev/null` as a redirection to a file named /dev/null, so every probe came
    back rc=255 "The system cannot find the path specified." and the
    cross-environment verdict was decided by a shell parse error rather than by
    the files. wsl.exe is an ordinary PE binary and execs fine without a shell.
    Self-heals a dropped WSLInterop handler with one retry (2026-10-21).
    """
    return _run_wsl_with_heal(
        ["wsl.exe", "-d", "Ubuntu-26.04", "-e", "bash", "-c", cmd],
        timeout, "WSL not available",
    )


def wsl_path(win_path: Path) -> str:
    """Convert Windows Path to WSL /mnt/c/... path."""
    s = str(win_path).replace("\\", "/")
    if s.startswith("C:"):
        return "/mnt/c" + s[2:]
    if s.startswith("G:"):
        return "/mnt/g" + s[2:]
    if s.startswith("E:"):
        return "/mnt/e" + s[2:]
    return s


# ═══════════════════════════════════════════════════════════════════════
# Sync Operations
# ═══════════════════════════════════════════════════════════════════════

def _copy_to_wsl(src_file: Path, dest_wsl: str) -> tuple:
    """Copy a file into WSL2 via the \\\\wsl$ UNC path (no shell quoting).

    Falls back to `cp` over wsl.exe when the UNC path is unavailable.
    Returns (ok, error_message).
    """
    unc = "\\\\wsl$\\Ubuntu-26.04" + dest_wsl.replace("/", "\\")
    try:
        Path(unc).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, unc)
        return True, ""
    except OSError as e:
        cmd = f"mkdir -p $(dirname {dest_wsl}) && cp {wsl_path(src_file)} {dest_wsl}"
        rc, _out, err = run_wsl(cmd)
        return (rc == 0), err.strip()


def sync_windows_to_wsl2(dry_run: bool = False) -> Dict[str, Any]:
    """Sync Windows 11 -> WSL2 (code + docs + tests + scripts).
    
    Source: Windows files accessed via /mnt/c/... from WSL2
    Destination: WSL2 native filesystem at /root/azazael/...
    """
    windows_root = ENVIRONMENTS["windows"]["root"]
    all_patterns = SYNC_CODE_PATTERNS + SYNC_DOC_PATTERNS + SYNC_SCRIPT_PATTERNS + SYNC_SKILL_PATTERNS
    synced = []
    errors = []

    for pattern in all_patterns:
        for src_file in sorted(windows_root.glob(pattern)):
            rel_path = src_file.relative_to(windows_root)
            # Source: Windows file via /mnt/c mount
            src_wsl = wsl_path(src_file)
            # Destination: WSL2 native filesystem
            dest_wsl = f"/root/azazael/{rel_path.as_posix()}"

            if dry_run:
                synced.append(str(rel_path))
                continue

            ok, err_msg = _copy_to_wsl(src_file, dest_wsl)

            if ok:
                synced.append(str(rel_path))
            else:
                errors.append(f"{rel_path}: {err_msg}")

    return {"synced": synced, "errors": errors, "count": len(synced),
            "target": "wsl2"}


def sync_wsl_to_windows(dry_run: bool = False) -> Dict[str, Any]:
    """Sync WSL2 -> Windows 11 (code + docs + skills + scripts).
    
    Source: WSL2 native filesystem at /root/azazael/...
    Destination: Windows files via /mnt/c/Azazael/azazael/...
    Runs from WSL2 directly — no wsl.exe needed.
    """
    if not IS_WSL:
        return {"synced": [], "errors": ["sync_wsl_to_windows runs inside WSL2 only — a WSL-style absolute path on Windows resolves under the current drive, refusing"], "count": 0, "target": "windows"}
    wsl_root = Path("/root/azazael")
    windows_root = Path("/mnt/c/Azazael/azazael")
    
    if not wsl_root.exists():
        return {"synced": [], "errors": ["WSL root /root/azazael not found"], "count": 0, "target": "windows"}
    if not windows_root.exists():
        return {"synced": [], "errors": ["Windows root /mnt/c/Azazael/azazael not found"], "count": 0, "target": "windows"}
    
    all_patterns = SYNC_CODE_PATTERNS + SYNC_DOC_PATTERNS + SYNC_SCRIPT_PATTERNS + SYNC_SKILL_PATTERNS
    synced = []
    errors = []
    
    for pattern in all_patterns:
        for src_file in sorted(wsl_root.glob(pattern)):
            rel_path = src_file.relative_to(wsl_root)
            dest = windows_root / rel_path
            
            if dry_run:
                synced.append(str(rel_path))
                continue
            
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src_file, dest)
                synced.append(str(rel_path))
            except OSError as e:
                errors.append(f"{rel_path}: {e}")
    
    return {"synced": synced, "errors": errors, "count": len(synced), "target": "windows"}


def _is_mounted_windows_drive(p: Path) -> bool:
    """True when `p` sits on a mounted Windows drive rather than on the distro's
    own root filesystem.

    `/mnt/g` existed only as an ordinary directory on the WSL root device —
    `/proc/mounts` had /mnt/c and /mnt/e but no /mnt/g. `sync_wsl_to_g` therefore
    copied its whole file set into the distro and reported success, while the
    real G: archives stayed months out of date and nothing failed. Comparing the
    device id is what distinguishes an actual drvfs mount from that trap.
    """
    try:
        return p.stat().st_dev != Path("/").stat().st_dev
    except OSError:
        return False


def sync_wsl_to_g(dry_run: bool = False) -> Dict[str, Any]:
    """Sync WSL2 -> G: archives (both source-of-truth and AZAZAEL_SOURCE_OF_TRUTH).
    
    Runs from WSL2 directly — copies to /mnt/g/ paths.
    """
    if not IS_WSL:
        return {"synced": [], "errors": ["sync_wsl_to_g runs inside WSL2 only — refusing (a WSL-style absolute path on Windows resolves under the current drive, never the source)"], "count": 0, "target": "g"}
    wsl_root = Path("/root/azazael")
    g_targets = [
        Path("/mnt/g/source-of-truth/azazael"),
        Path("/mnt/g/AZAZAEL_SOURCE_OF_TRUTH"),
    ]
    
    all_patterns = SYNC_CODE_PATTERNS + SYNC_DOC_PATTERNS + SYNC_SCRIPT_PATTERNS + SYNC_SKILL_PATTERNS
    total_synced = []
    total_errors = []
    
    for g_root in g_targets:
        if not g_root.exists():
            total_errors.append(f"G: target {g_root} not found")
            continue
        if not _is_mounted_windows_drive(g_root):
            total_errors.append(
                f"G: target {g_root} is not a mounted Windows drive (same device "
                f"as /) — refusing to write the archive into the distro's own "
                f"filesystem; mount it with: mount -t drvfs G: /mnt/g"
            )
            continue
        
        for pattern in all_patterns:
            for src_file in sorted(wsl_root.glob(pattern)):
                rel_path = src_file.relative_to(wsl_root)
                dest = g_root / rel_path
                
                if dry_run:
                    total_synced.append(f"{g_root.name}:{rel_path}")
                    continue
                
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src_file, dest)
                    total_synced.append(f"{g_root.name}:{rel_path}")
                except OSError as e:
                    total_errors.append(f"{g_root.name}:{rel_path}: {e}")
    
    return {"synced": total_synced, "errors": total_errors, "count": len(total_synced), "target": "g"}


def _glob_sync_set(root: Path) -> Tuple[list, list]:
    """Glob every sync pattern under `root`; return (rel_paths, errors)."""
    all_patterns = SYNC_CODE_PATTERNS + SYNC_DOC_PATTERNS + SYNC_SCRIPT_PATTERNS + SYNC_SKILL_PATTERNS
    rels: list = []
    errors: list = []
    for pattern in all_patterns:
        try:
            for src_file in sorted(root.glob(pattern)):
                try:
                    rels.append(src_file.relative_to(root).as_posix())
                except ValueError as e:
                    errors.append(f"{src_file}: {e}")
        except OSError as e:
            errors.append(f"glob {pattern}: {e}")
    # The list is embedded in a bash double-quoted loop: drop anything that
    # could break out of quoting rather than passing it through.
    safe = [r for r in rels if '"' not in r and "`" not in r and "$" not in r and "\n" not in r]
    for r in rels:
        if r not in safe:
            errors.append(f"skipped unsafe path for kali pull: {r!r}")
    return safe, errors


def _kali_pull_from_mntc(src_tree_wsl: str, rels: list, dry_run: bool) -> Dict[str, Any]:
    """Have Kali pull `rels` straight from its own /mnt/c view of the source tree.

    No E: bridge, no staging dir, no host-side residue: one wsl.exe call,
    Kali copies from /mnt/c/... (which it mounts natively) into /root/azazael.
    Returns the sync result dict; the caller supplies the already-globbed list
    so dry_run never shells out."""
    kali_root = "/root/azazael"
    errors: list = []
    if dry_run:
        return {"synced": list(rels), "errors": errors, "count": len(rels), "target": "kali"}
    import tempfile
    fd, list_path = tempfile.mkstemp(prefix="kali_sync_", suffix=".lst")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(rels) + "\n")
        list_wsl = wsl_path(Path(list_path))
        # Single Kali-side loop: copy, report per-file FAILs, print OK count.
        # Quoted heredoc-free one-liner; 2>/dev/null keeps quiet, FAIL lines carry the signal.
        kali_cmd = (
            f"ok=0; bad=0; "
            f"while IFS= read -r f; do "
            f"[ -z \"$f\" ] && continue; "
            f"mkdir -p \"{kali_root}/$(dirname \"$f\")\" 2>/dev/null; "
            f"if cp \"{src_tree_wsl}/$f\" \"{kali_root}/$f\" 2>/dev/null; then ok=$((ok+1)); "
            f"else echo \"FAIL:$f\"; bad=$((bad+1)); fi; "
            f"done < \"{list_wsl}\"; "
            f"echo \"PULL_DONE ok=$ok bad=$bad\""
        )
        rc, stdout, stderr = run_wsl_kali(kali_cmd, timeout=300)
    finally:
        try:
            os.unlink(list_path)
        except OSError as e:
            print(f"warning: kali sync list not removed: {list_path}: {e}", file=sys.stderr)
    if rc != 0:
        errors.append(f"kali pull failed rc={rc}: {stderr.strip()[:300]}")
        return {"synced": [], "errors": errors, "count": 0, "target": "kali"}
    fails = [ln[5:] for ln in stdout.splitlines() if ln.startswith("FAIL:")]
    errors.extend(f"kali:{f}: copy failed" for f in fails)
    done = [ln for ln in stdout.splitlines() if ln.startswith("PULL_DONE")]
    ok_n = 0
    if done:
        try:
            ok_n = int(done[-1].split("ok=")[1].split()[0])
        except (IndexError, ValueError):
            ok_n = 0
    synced = [r for r in rels if r not in set(fails)]
    if ok_n != len(synced):
        errors.append(f"kali pull count mismatch: loop ok={ok_n} vs files={len(synced)}")
    return {"synced": synced, "errors": errors, "count": len(synced), "target": "kali"}


def sync_wsl_to_kali(dry_run: bool = False) -> Dict[str, Any]:
    """Sync the pattern set into Kali's /root/azazael — from either runtime.

    Windows runtime (the normal one): glob the Windows tree, hand Kali the
    relative list, Kali pulls straight from its own /mnt/c mount. No E:
    bridge — the bridge only exists inside real WSL, and resolving /mnt/e on
    Windows wrote staging residue into C:\\mnt\\e while Kali saw nothing
    (2026-10-29: "5 files synced" + missing bridge, source was the stale
    C:\\root fragment).

    WSL-Ubuntu runtime: same Kali-side pull, but the source tree is first
    staged to the E: bridge (real mount there) because Kali cannot see
    Ubuntu's /root. The bridge mount is verified with
    _is_mounted_windows_drive before a byte is written.
    """
    all_count_note = None
    if IS_WSL:
        wsl_root = Path("/root/azazael")
        if not wsl_root.exists():
            return {"synced": [], "errors": ["WSL root /root/azazael not found"], "count": 0, "target": "kali"}
        e_mnt = Path("/mnt/e")
        if not _is_mounted_windows_drive(e_mnt):
            return {"synced": [], "errors": ["/mnt/e is not a mounted Windows drive here — refusing to stage the Kali bridge into this distro's own filesystem"], "count": 0, "target": "kali"}
        e_bridge = Path("/mnt/e/kali_sync_temp")
        rels, errors = _glob_sync_set(wsl_root)
        if dry_run:
            return {"synced": rels, "errors": errors, "count": len(rels), "target": "kali"}
        staged = []
        for rel in rels:
            src_file = wsl_root / rel
            bridge_dest = e_bridge / rel
            bridge_dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src_file, bridge_dest)
                staged.append(rel)
            except OSError as e:
                errors.append(f"bridge:{rel}: {e}")
        rc, _out, stderr = run_wsl_kali(
            "mkdir -p /root/azazael && cp -r /mnt/e/kali_sync_temp/* /root/azazael/"
        )
        if rc != 0:
            errors.append(f"kali batch copy failed: {stderr.strip()}")
            errors.append("Files staged at /mnt/e/kali_sync_temp/ — run manually in Kali:")
            errors.append("  cp -r /mnt/e/kali_sync_temp/* /root/azazael/")
            return {"synced": staged, "errors": errors, "count": len(staged), "target": "kali"}
        try:
            shutil.rmtree(e_bridge, ignore_errors=True)
        except OSError:
            pass
        return {"synced": staged, "errors": errors, "count": len(staged), "target": "kali"}
    # Windows runtime: Kali pulls from its /mnt/c view of the Windows tree.
    windows_root = ENVIRONMENTS["windows"]["root"]
    if not windows_root.exists():
        return {"synced": [], "errors": [f"Windows root {windows_root} not found"], "count": 0, "target": "kali"}
    rels, errors = _glob_sync_set(windows_root)
    if dry_run:
        return {"synced": rels, "errors": errors, "count": len(rels), "target": "kali"}
    src_tree_wsl = wsl_path(windows_root)
    r = _kali_pull_from_mntc(src_tree_wsl, rels, dry_run=False)
    r["errors"] = errors + r["errors"]
    return r


def run_wsl_kali(cmd: str, timeout: int = 60) -> Tuple[int, str, str]:
    """Run a command inside Kali WSL2 distro.
    
    Execs wsl.exe directly rather than through a shell, for the same reason as
    `run_wsl`: cmd.exe mangles the quoting and the `2>/dev/null` redirect, so a
    shell-routed probe reports rc=255 while looking like a filesystem problem.
    """
    return _run_wsl_with_heal(
        ["wsl.exe", "-d", "kali-linux", "-u", "root", "-e", "bash", "-c", cmd],
        timeout, "WSL/kali-linux not available",
    )


def sync_docs_to_target(windows_root: Path, target_root: Path,
                        dry_run: bool = False) -> Dict[str, Any]:
    """Sync documentation to any target environment."""
    synced = []
    for pattern in SYNC_DOC_PATTERNS:
        for src_file in sorted(windows_root.glob(pattern)):
            rel_path = src_file.relative_to(windows_root)
            dest = target_root / rel_path
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src_file, dest)
                except OSError as e:
                    synced.append(f"ERROR: {rel_path}: {e}")
                    continue
            synced.append(str(rel_path))
    return {"synced": synced, "count": len(synced),
            "target": str(target_root)}


# ═══════════════════════════════════════════════════════════════════════
# Status & Verification
# ═══════════════════════════════════════════════════════════════════════


def get_env_status(env_name: str, env_info: Dict) -> Dict[str, Any]:
    """Get status of an environment — works from both Windows and WSL."""
    env_type = env_info["type"]
    
    # A WSL2 distro keeps its tree inside the distro, so a host-side stat() of the
    # root always fails and the generic branch below would report a running
    # distro as unavailable purely because of where the script was launched.
    # Probe through wsl.exe instead, from Windows as well as from WSL.
    if env_type == "wsl" and not IS_WSL:
        runner = run_wsl_kali if env_info.get("wsl_distro") == "kali-linux" else run_wsl
        tree = str(env_info["root"]).replace("\\", "/")
        status: dict[str, Any] = {
            "name": env_name,
            "label": env_info["label"],
            "type": env_type,
        }
        rc, _, _ = runner(f"test -d {tree}")
        status["available"] = (rc == 0)
        if not status["available"]:
            status["warning"] = f"{tree} not accessible via wsl.exe"
            return status
        # Count via wsl.exe
        rc_py, out_py, _ = runner(f"find {tree}/src -name '*.py' 2>/dev/null | wc -l")
        status["py_files"] = int(out_py.strip()) if rc_py == 0 and out_py.strip().isdigit() else 0
        rc_docs, out_docs, _ = runner(f"ls {tree}/docs/*.md 2>/dev/null | wc -l")
        status["docs"] = int(out_docs.strip()) if rc_docs == 0 and out_docs.strip().isdigit() else 0
        return status
    
    root = _resolve_path(env_info["root"], env_type)

    status = {
        "name": env_name,
        "label": env_info["label"],
        "type": env_type,
        "available": root.exists(),
    }
    if not status["available"]:
        status["warning"] = "Environment root not accessible"
        return status

    # Count .py files
    src = env_info.get("src")
    if src:
        src_path = _resolve_path(src, env_type)
        if src_path.exists():
            status["py_files"] = len(list(src_path.rglob("*.py")))
        else:
            status["py_files"] = 0
    else:
        status["py_files"] = 0

    # Count .md docs
    docs = env_info.get("docs")
    if docs:
        docs_path = _resolve_path(docs, env_type)
        if docs_path.exists():
            status["docs"] = len(list(docs_path.glob("*.md")))
        else:
            status["docs"] = 0
    else:
        status["docs"] = 0

    if env_info.get("exe_only"):
        status["exe_files"] = len(list(root.glob("*.exe")))
    return status


def verify_integrity() -> Dict[str, bool]:
    """Verify file integrity across environments — works from both Windows and WSL."""
    results: dict[str, bool] = {}

    # Resolve environment roots for current runtime
    win_type = ENVIRONMENTS["windows"]["type"]
    g_source_type = ENVIRONMENTS["g_source"]["type"]
    win_root = _resolve_path(ENVIRONMENTS["windows"]["root"], win_type)
    g_source = _resolve_path(ENVIRONMENTS["g_source"]["root"], g_source_type)
    wsl_root = Path("/root/azazael") if IS_WSL else None

    # Helper: get filenames from a WSL distro. Ubuntu-26.04 and Kali both keep
    # their tree at /root/azazael, so the *distro* — not the path — is what must
    # select the runner. Keying on the path alone made the two distros
    # indistinguishable and let Kali diverge unseen (D-2).
    def wsl_files(subpath: str, ext: str = "*.py", distro: str = "ubuntu") -> set[str]:
        if IS_WSL and wsl_root and distro == "ubuntu":
            p = wsl_root / subpath
            return set(f.name for f in p.glob(ext)) if p.exists() else set()
        runner = run_wsl_kali if distro == "kali" else run_wsl
        rc, stdout, _ = runner(f"ls /root/azazael/{subpath}/{ext} 2>/dev/null")
        if rc != 0 or not stdout.strip():
            return set()
        return set(Path(f).name for f in stdout.strip().split("\n") if f)

    # 1. Code: expansion/ parity, Windows vs each distro. Ubuntu and Kali are
    #    probed separately — a shared "/root/azazael" must not merge verdicts.
    win_exp_path = win_root / "src/CORE/WORMS/expansion"
    win_exp = set(f.name for f in win_exp_path.glob("*.py")) if win_exp_path.exists() else set()
    results["expansion"] = win_exp == wsl_files("src/CORE/WORMS/expansion")
    results["expansion_kali"] = win_exp == wsl_files("src/CORE/WORMS/expansion", distro="kali")

    # 2. Docs: Windows vs G: source-of-truth. Fail closed: an unmounted G: is an
    #    *unknown* archive, not a passing one (D-3). `if g_docs else True` turned
    #    a missing drive into a green result.
    win_docs_path = win_root / "docs"
    win_docs = set(f.name for f in win_docs_path.glob("*.md")) if win_docs_path.exists() else set()
    g_docs_path = g_source / "docs"
    g_docs = set(f.name for f in g_docs_path.glob("*.md")) if g_docs_path.exists() else set()
    results["docs_g"] = bool(g_docs) and win_docs.issubset(g_docs)

    # 3. Tests: Windows vs each distro (subset check)
    win_tests_path = win_root / "tests"
    win_tests = set(f.name for f in win_tests_path.glob("*.py")) if win_tests_path.exists() else set()
    wsl_tests = wsl_files("tests")
    results["tests"] = win_tests.issubset(wsl_tests) if wsl_tests else False
    kali_tests = wsl_files("tests", distro="kali")
    results["tests_kali"] = win_tests.issubset(kali_tests) if kali_tests else False

    # 4. Scripts: Windows vs each distro (subset check)
    win_scripts_path = win_root / "scripts"
    win_scripts = set(f.name for f in win_scripts_path.glob("*.py")) if win_scripts_path.exists() else set()
    wsl_scripts = wsl_files("scripts")
    results["scripts"] = win_scripts.issubset(wsl_scripts) if wsl_scripts else False
    kali_scripts = wsl_files("scripts", distro="kali")
    results["scripts_kali"] = win_scripts.issubset(kali_scripts) if kali_scripts else False

    # 5. Desktop release (D-1): the standalone folder is a build artifact
    #    tree, not a mirror, so parity is the wrong check — presence of the
    #    release markers is. Without this, verify could report ALL SYNCED
    #    while the release folder was deleted out from under it.
    desk = ENVIRONMENTS.get("desktop", {}).get("root")
    if desk is not None:
        desk = _resolve_path(desk, ENVIRONMENTS["desktop"].get("type"))
        results["desktop"] = bool(
            desk and desk.is_dir()
            and (desk / "docs").is_dir() and (desk / "skills").is_dir())
    else:
        results["desktop"] = False

    results["all_ok"] = all(results.values())
    return results


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Azazael Environment Sync"
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--sync-all", action="store_true")
    parser.add_argument("--sync-to", choices=["wsl2", "windows", "g", "desktop", "kali"])
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if not (args.status or args.verify or args.sync_all or args.sync_to):
        parser.print_help()
        return

    windows_root = ENVIRONMENTS["windows"]["root"]
    ts = datetime.now().isoformat()

    if args.status:
        print(f"Azazael Sync Status - {ts}")
        print("=" * 60)
        for name, info in ENVIRONMENTS.items():
            s = get_env_status(name, info)
            icon = "OK" if s["available"] else "XX"
            print(f"  [{icon}] {s['label']} ({s['type']})")
            if s["available"]:
                if s.get("py_files"):
                    print(f"      .py: {s['py_files']}")
                if s.get("docs"):
                    print(f"      docs: {s['docs']}")
                if s.get("exe_files"):
                    print(f"      .exe: {s['exe_files']}")
        v = verify_integrity()
        print(f"\n  Integrity: {'ALL SYNCED' if v['all_ok'] else 'OUT OF SYNC'}")
        for k, ok in v.items():
            if k != "all_ok" and not ok:
                print(f"    {k}: FAIL")

    if args.verify:
        v = verify_integrity()
        if v["all_ok"]:
            print("PASS: All environments in sync")
        else:
            print("FAIL: Environments out of sync")
            for k, ok in v.items():
                if k != "all_ok" and not ok:
                    print(f"  {k}: out of sync")

    if args.sync_to == "wsl2" or args.sync_all:
        # Skip Windows→WSL direction when already on WSL. Platform-gated:
        # Path("/root/azazael").exists() misfires on Windows hosts carrying
        # a C:\root\azazael fragment and silently skipped this whole leg.
        if IS_WSL:
            print("Skipping Windows -> WSL2 (already on WSL)")
        else:
            print("Syncing Windows -> WSL2...")
            r = sync_windows_to_wsl2(dry_run=args.dry_run)
            print(f"  {r['count']} files synced")
            for e in r.get("errors", []):
                print(f"  ERROR: {e}")

    if args.sync_to == "windows" or args.sync_all:
        print("Syncing WSL2 -> Windows 11...")
        r = sync_wsl_to_windows(dry_run=args.dry_run)
        print(f"  {r['count']} files synced")
        for e in r.get("errors", []):
            print(f"  ERROR: {e}")

    if args.sync_to == "g" or args.sync_all:
        # Try G: via /mnt/g/ (WSL2) first, then fall back to Windows paths
        g_wsl = Path("/mnt/g/source-of-truth/azazael")
        if g_wsl.exists() or Path("/mnt/g/AZAZAEL_SOURCE_OF_TRUTH").exists():
            print("Syncing WSL2 -> G: archives...")
            r = sync_wsl_to_g(dry_run=args.dry_run)
            print(f"  {r['count']} files synced")
            for e in r.get("errors", []):
                print(f"  ERROR: {e}")
        else:
            for gk in ["g_source", "g_root"]:
                gi = ENVIRONMENTS[gk]
                if gi["root"].exists():
                    print(f"Syncing docs -> {gi['label']}...")
                    r = sync_docs_to_target(windows_root, gi["root"], dry_run=args.dry_run)
                    print(f"  {r['count']} files synced")

    if args.sync_to == "desktop" or args.sync_all:
        dr = ENVIRONMENTS["desktop"]["root"]
        if dr.exists():
            print("Syncing docs -> Desktop...")
            r = sync_docs_to_target(windows_root, dr, dry_run=args.dry_run)
            print(f"  {r['count']} files synced")

    if args.sync_to == "kali" or args.sync_all:
        print("Syncing -> Kali Linux" + (" via E: bridge..." if IS_WSL else " (Kali pulls from /mnt/c)..."))
        r = sync_wsl_to_kali(dry_run=args.dry_run)
        print(f"  {r['count']} files synced")
        for e in r.get("errors", []):
            print(f"  ERROR: {e}")

    if args.sync_all:
        print(f"\nFull sync complete - {ts}")


if __name__ == "__main__":
    main()
