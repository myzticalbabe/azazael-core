#!/usr/bin/env python3.15t
"""
build_all_concurrent.py - Concurrent Nuitka builds across Windows 11 + WSL2.

Builds all 39 standalone .exe targets in parallel on both platforms.
Uses Nuitka 4.1.2 (verified on Windows 11 and WSL2).

Usage:
    python3.15t build_all_concurrent.py --all          Build all 39 targets
    python3.15t build_all_concurrent.py --tier neural  Build neural tier only
    python3.15t build_all_concurrent.py --target metacognition
    python3.15t build_all_concurrent.py --platform windows
    python3.15t build_all_concurrent.py --verify       Verify Nuitka patches
    python3.15t build_all_concurrent.py --list         List targets
    python3.15t build_all_concurrent.py --deploy       Deploy to Desktop

Created: June 4, 2026 - Environment realignment session
"""

import sys
import os
import time
import json
import shlex
import shutil
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DESKTOP_RELEASE = Path("C:/Users/clint/Desktop/AZAZAEL_Standalone")

# ═══════════════════════════════════════════════════════════════════════
# Full 39-Target Registry
# ═══════════════════════════════════════════════════════════════════════

BUILD_TARGETS: Dict[str, Dict] = {
    # Neural Advancement (6)
    "metacognition": {"entry": "src/CORE/WORMS/expansion/meta_cognition.py",
                      "output": "azazael-metacognition.exe", "tier": "neural"},
    "evolution": {"entry": "src/CORE/WORMS/expansion/evolution.py",
                  "output": "azazael-evolution.exe", "tier": "neural"},
    "racing_detector": {"entry": "src/CORE/WORMS/expansion/race_detector.py",
                        "output": "azazael-race-detector.exe", "tier": "neural"},
    "error_recovery": {"entry": "src/CORE/WORMS/expansion/error_recovery.py",
                       "output": "azazael-recovery-orchestrator.exe", "tier": "neural"},
    "dreaming": {"entry": "src/CORE/WORMS/expansion/dreaming.py",
                 "output": "azazael-dreaming.exe", "tier": "neural"},
    "learning": {"entry": "src/CORE/WORMS/expansion/learning.py",
                 "output": "azazael-learning-pipeline.exe", "tier": "neural"},
    # Infrastructure (6)
    "clusterworms": {"entry": "src/CORE/WORMS/CLUSTERWORMS.py",
                     "output": "azazael-clusterworms.exe", "tier": "core"},
    "metacluster": {"entry": "src/CORE/WORMS/METACLUSTER.py",
                    "output": "azazael-metacluster.exe", "tier": "core"},
    "concurrency": {"entry": "src/CORE/WORMS/CONCURRENCY.py",
                     "output": "azazael-concurrency.exe", "tier": "core"},
    # U3: cross_nature target removed with CROSS_NATURE_ROUTER.py (D7).
    "network_diag": {"entry": "src/CORE/WORMS/NETWORK_DIAG.py",
                     "output": "azazael-network-diag.exe", "tier": "monitor"},
    "codex_tools": {"entry": "src/CORE/WORMS/CODEX_TOOLS.py",
                    "output": "azazael-codex-tools.exe", "tier": "core"},
    # Monitoring (8)
    "systemz": {"entry": "src/CORE/WORMS/SYSTEMZ.py",
                "output": "azazael-systemz.exe", "tier": "monitor"},
    "processz": {"entry": "src/CORE/WORMS/PROCESSZ.py",
                 "output": "azazael-processz.exe", "tier": "monitor"},
    "diskz": {"entry": "src/CORE/WORMS/DISKZ.py",
              "output": "azazael-diskz.exe", "tier": "monitor"},
    "networxz": {"entry": "src/CORE/WORMS/NETWORXZ.py",
                 "output": "azazael-networxz.exe", "tier": "monitor"},
    "clockz": {"entry": "src/CORE/WORMS/CLOCKZ.py",
               "output": "azazael-clockz.exe", "tier": "monitor"},
    "metricz": {"entry": "src/META/METRICZ.py",
                "output": "azazael-metricz.exe", "tier": "monitor"},
    "diagnostic": {"entry": "src/CORE/WORMS/DIAGNOSTIC_WORM.py",
                   "output": "azazael-diagnostic.exe", "tier": "monitor"},
    "smartdisk": {"entry": "src/CORE/WORMS/SMARTDISK.py",
                  "output": "azazael-smartdisk.exe", "tier": "monitor"},
    # Security (5)
    "binary_analysis": {"entry": "src/CORE/WORMS/BINARY_ANALYSIS.py",
                        "output": "azazael-binary-analysis.exe", "tier": "security"},
    "error_scan": {"entry": "src/CORE/WORMS/ERROR_SCAN_WORM.py",
                   "output": "azazael-error-scan.exe", "tier": "security"},
    "healing": {"entry": "src/CORE/WORMS/HEALING_WORM.py",
                "output": "azazael-healing.exe", "tier": "security"},
    "morphing": {"entry": "src/CORE/WORMS/MORPHING_WORM_V2.py",
                 "output": "azazael-morphing.exe", "tier": "security"},
    "reverse_tools": {"entry": "src/CORE/WORMS/REVERSE_TOOLS.py",
                      "output": "azazael-reverse-tools.exe", "tier": "security"},
    # ML/AI (5)
    "systemz_ml": {"entry": "src/CORE/WORMS/SYSTEMZ_ML.py",
                   "output": "azazael-systemz-ml.exe", "tier": "ml"},
    "wormz_ml": {"entry": "src/CORE/WORMS/WORMZ_ML.py",
                 "output": "azazael-wormz-ml.exe", "tier": "ml"},
    "cain": {"entry": "src/CORE/WORMS/CAIN_WORM.py",
             "output": "azazael-cain.exe", "tier": "ml"},
    "omni": {"entry": "src/CORE/WORMS/OMNI_WORM_V2.py",
             "output": "azazael-omni.exe", "tier": "ml"},
    "codex_studio": {"entry": "src/CODEX/CODEX_Studio.py",
                     "output": "azazael-codex-studio.exe", "tier": "ml"},
    # Expansion (9)
    "db_monitor": {"entry": "src/CORE/WORMS/expansion/db_monitor.py",
                   "output": "azazael-db-monitor.exe", "tier": "expansion"},
    "backup": {"entry": "src/CORE/WORMS/expansion/backup.py",
               "output": "azazael-backup.exe", "tier": "expansion"},
    # "config_validator": {"entry": "src/CORE/WORMS/expansion/config_validator.py",
    # "output": "azazael-config-validator.exe", "tier": "expansion"},
    # "container_manager": {"entry": "src/CORE/WORMS/expansion/container_manager.py",
    # "output": "azazael-container-manager.exe", "tier": "expansion"},
    # "migration_engine": {"entry": "src/CORE/WORMS/expansion/migration_engine.py",
    # "output": "azazael-migration-engine.exe", "tier": "expansion"},
    # "communication_bus": {"entry": "src/CORE/WORMS/expansion/communication_bus.py",
    # "output": "azazael-communication-bus.exe", "tier": "expansion"},
    # "profiling_engine": {"entry": "src/CORE/WORMS/expansion/profiling_engine.py",
    # "output": "azazael-profiling-engine.exe", "tier": "expansion"},
    # "telemetry_collector": {"entry": "src/CORE/WORMS/expansion/telemetry_collector.py",
    # "output": "azazael-telemetry-collector.exe", "tier": "expansion"},
    # "security_auditor": {"entry": "src/CORE/WORMS/expansion/security_auditor.py",
    # "output": "azazael-security-auditor.exe", "tier": "expansion"},
}


# ═══════════════════════════════════════════════════════════════════════
# Build Operations
# ═══════════════════════════════════════════════════════════════════════

def verify_windows_nuitka() -> bool:
    """Verify Nuitka patches on Windows."""
    patch = PROJECT_ROOT / "docs" / "scripts" / "patch_nuitka_315t.py"
    if not patch.exists():
        return True
    try:
        r = subprocess.run(
            [sys.executable, str(patch), "--verify"],
            capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT), encoding="utf-8", errors="replace"
        )
        return r.returncode == 0
    except Exception:
        return False


def verify_wsl_nuitka() -> bool:
    """Verify Nuitka patches inside WSL2."""
    try:
        r = subprocess.run(
            ["wsl", "-d", "Ubuntu-26.04", "-e", "bash", "-c",
             "python3.15t /root/azazael/docs/scripts/patch_nuitka_315t.py --verify 2>&1"],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace"
        )
        return r.returncode == 0
    except Exception:
        return False


def build_target_windows(name: str, target: Dict, out_dir: Path) -> Dict:
    """Build on Windows 11."""
    start = time.perf_counter()
    entry = PROJECT_ROOT / target["entry"]
    result = {"target": name, "platform": "windows",
              "output": target["output"], "success": False,
              "duration_ms": 0, "error": None}

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone", "--assume-yes-for-downloads",
        "--output-dir=" + str(out_dir),
        "--output-filename=" + target["output"],
        "--nofollow-import-to=psycopg2",
        "--nofollow-import-to=CORE.postgres_memory",
        "--script=" + str(entry),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=300, cwd=str(PROJECT_ROOT), encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            result["success"] = True
        else:
            result["error"] = proc.stderr[-300:] if proc.stderr else "Unknown error"
    except subprocess.TimeoutExpired:
        result["error"] = "Timed out (300s)"
    except Exception as e:
        result["error"] = str(e)

    result["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return result


def build_target_wsl(name: str, target: Dict, out_dir: Path) -> Dict:
    """Build on WSL2."""
    start = time.perf_counter()
    result = {"target": name, "platform": "wsl2",
              "output": target["output"], "success": False,
              "duration_ms": 0, "error": None}

    entry_wsl = f"/root/azazael/{target['entry']}"
    out_wsl = str(out_dir).replace("\\", "/").replace("C:", "/mnt/c")

    # Build command with absolute paths (no cd needed)
    cmd_parts = [
        "python3.15t", "-m", "nuitka",
        "--standalone", "--assume-yes-for-downloads",
        "--output-dir=" + out_wsl,
        "--output-filename=" + target["output"],
        "--nofollow-import-to=psycopg2",
        "--nofollow-import-to=CORE.postgres_memory",
        "--include-plugin-directory=/root/azazael/src",
        "--script=" + entry_wsl,
    ]
    cmd = shlex.join(cmd_parts)

    try:
        proc = subprocess.run(
            ["wsl", "-d", "Ubuntu-26.04", "-e", "bash", "-c", cmd],
            capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace"
        )
        if proc.returncode == 0:
            result["success"] = True
        else:
            result["error"] = proc.stderr[-300:] if proc.stderr else "Unknown error"
    except subprocess.TimeoutExpired:
        result["error"] = "Timed out (300s)"
    except FileNotFoundError:
        result["error"] = "WSL not available"
    except Exception as e:
        result["error"] = str(e)

    result["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return result


def deploy_to_desktop(build_dir: Path, dry_run: bool = False) -> Dict[str, Any]:
    """Copy built .exe files to Desktop AZAZAEL_Standalone."""
    deployed = []
    for exe in sorted(build_dir.rglob("*.exe")):
        dest = DESKTOP_RELEASE / exe.name
        if not dry_run:
            try:
                shutil.copy2(exe, dest)
                deployed.append(exe.name)
            except OSError as e:
                deployed.append(f"ERROR: {exe.name}: {e}")
        else:
            deployed.append(exe.name)
    return {"deployed": deployed, "count": len(deployed),
            "target": str(DESKTOP_RELEASE)}


# ═══════════════════════════════════════════════════════════════════════
# Concurrent Orchestrator
# ═══════════════════════════════════════════════════════════════════════

def build_concurrent(targets: List[str], platform: str = "both",
                     max_workers: int = 4) -> Dict[str, Any]:
    """Build targets concurrently across Windows + WSL2."""
    ts = datetime.now().isoformat().replace(":", "-")
    build_root = PROJECT_ROOT / "build" / "standalone" / ts

    win_dir = build_root / "windows"
    wsl_dir = build_root / "wsl"

    if platform in ("windows", "both"):
        win_dir.mkdir(parents=True, exist_ok=True)
    if platform in ("wsl", "both"):
        wsl_dir.mkdir(parents=True, exist_ok=True)

    print(f"Build: {build_root}")
    print(f"Targets: {len(targets)} | Platform: {platform} | Workers: {max_workers}")

    # Verify Nuitka
    if platform in ("windows", "both"):
        ok = verify_windows_nuitka()
        print(f"Windows Nuitka: {'OK' if ok else 'ISSUES'}")
    if platform in ("wsl", "both"):
        ok = verify_wsl_nuitka()
        print(f"WSL Nuitka: {'OK' if ok else 'ISSUES'}")

    results = []
    futures = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for name in targets:
            t = BUILD_TARGETS.get(name)
            if not t:
                print(f"  SKIP: {name} not registered")
                continue

            if platform in ("windows", "both"):
                futures.append(pool.submit(build_target_windows, name, t, win_dir))
            if platform in ("wsl", "both"):
                futures.append(pool.submit(build_target_wsl, name, t, wsl_dir))

        for future in as_completed(futures):
            r = future.result()
            results.append(r)
            status = "OK" if r["success"] else "FAIL"
            print(f"  [{status}] {r['target']:25s} {r['platform']:8s} "
                  f"{r['duration_ms']:8.0f}ms {r.get('error') or ''}")

    success = sum(1 for r in results if r["success"])
    summary = {
        "timestamp": ts, "targets": targets,
        "attempted": len(results), "succeeded": success,
        "failed": len(results) - success,
        "output_dir": str(build_root),
        "results": results,
    }

    manifest = build_root / "build_manifest.json"
    manifest.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n{success}/{len(results)} succeeded | Manifest: {manifest}")
    return summary


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Concurrent Nuitka builds for Azazael (39 targets)")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--target", type=str)
    parser.add_argument("--tier", type=str,
                        choices=["neural", "core", "monitor", "security", "ml", "expansion"])
    parser.add_argument("--platform", type=str, default="both",
                        choices=["windows", "wsl", "both"])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.list:
        for tier in ["neural", "core", "monitor", "security", "ml", "expansion"]:
            names = [n for n, t in BUILD_TARGETS.items() if t["tier"] == tier]
            print(f"[{tier}] ({len(names)}): {', '.join(names)}")
        print(f"\nTotal: {len(BUILD_TARGETS)} targets")
        return

    if args.verify:
        print(f"Windows Nuitka: {'OK' if verify_windows_nuitka() else 'ISSUES'}")
        print(f"WSL Nuitka: {'OK' if verify_wsl_nuitka() else 'ISSUES'}")
        return

    if args.deploy:
        # Deploy from latest build
        builds = sorted((PROJECT_ROOT / "build" / "standalone").glob("*"))
        if not builds:
            print("No builds found")
            return
        latest = builds[-1]
        print(f"Deploying from: {latest}")
        r = deploy_to_desktop(latest, dry_run=args.dry_run)
        print(f"  {r['count']} .exe files -> Desktop")
        return

    if not (args.all or args.target or args.tier):
        parser.print_help()
        return

    if args.all:
        targets = list(BUILD_TARGETS.keys())
    elif args.tier:
        targets = [n for n, t in BUILD_TARGETS.items() if t["tier"] == args.tier]
    else:
        if args.target not in BUILD_TARGETS:
            print(f"Unknown target: {args.target}")
            sys.exit(1)
        targets = [args.target]

    print(f"\nAzazael Concurrent Build - {datetime.now().isoformat()}")
    build_concurrent(targets, platform=args.platform, max_workers=args.workers)


if __name__ == "__main__":
    main()
