#!/usr/bin/env python
"""Successive FLUX renders with OOM-safe server management.

Each item runs in a FRESH ComfyUI server (started + terminated by
comfyui_flux_headless.py). Between runs this manager enforces:
  1. port 8188 free (previous server really dead, up to --port-wait s)
  2. free system RAM >= --min-ram-gb (shared-memory iGPU needs headroom)
  3. --cooldown s settle sleep for the XPU allocator
Any gate failure aborts the batch instead of OOMing into a broken run.

Usage:
    python batch_render_flux.py                       # whole style_samples dir
    python batch_render_flux.py --pattern "STYLE0[5-8]*"
    python batch_render_flux.py --dry-run             # list queue only
    python batch_render_flux.py --cooldown 60 --min-ram-gb 5
"""
import argparse
import ctypes
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = Path(r"C:\Users\clint\AppData\Local\Programs\AI Playground\resources\ComfyUI\.venv\Scripts\python.exe")
HEADLESS = HERE / "comfyui_flux_headless.py"
ACTIVE = HERE / "flux_probe_api.json"
QUEUE = HERE / "style_samples"
PORT = 8188


def free_ram_gb():
    k32 = ctypes.windll.kernel32
    mem = ctypes.c_ulonglong(64), ctypes.c_ulonglong(0), ctypes.c_ulonglong(0)
    class MSTAT(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MSTAT()
    st.dwLength = ctypes.sizeof(MSTAT)
    k32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullAvailPhys / 1073741824


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_port_free(timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if port_free(PORT):
            return True
        time.sleep(3)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(QUEUE))
    ap.add_argument("--pattern", default="*.json")
    ap.add_argument("--cooldown", type=int, default=30)
    ap.add_argument("--min-ram-gb", type=float, default=4.0)
    ap.add_argument("--port-wait", type=int, default=90)
    ap.add_argument("--run-timeout", type=int, default=1200)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-retry", action="store_true",
                    help="skip the automatic --lowvram retry pass for failures")
    a = ap.parse_args()

    items = sorted(Path(a.dir).glob(a.pattern))
    print(f"queue: {len(items)} item(s)")
    for p in items:
        print("  -", p.name)
    if a.dry_run or not items:
        return 0

    if not VENV_PY.exists():
        print("FATAL: no venv python at", VENV_PY)
        return 1

    ok, failed, skipped = [], [], []
    for i, wf in enumerate(items, 1):
        print(f"\n{'=' * 70}\n[{i}/{len(items)}] {wf.name}\n{'=' * 70}")
        ram = free_ram_gb()
        print(f"free RAM: {ram:.2f} GB (gate >= {a.min_ram_gb})")
        if ram < a.min_ram_gb:
            print("ABORT: below RAM gate -- reboot before continuing the queue")
            skipped.extend(p.name for p in items[i - 1:])
            break
        if not wait_port_free(a.port_wait):
            print(f"ABORT: port {PORT} still held -- stale server, kill it first")
            skipped.extend(p.name for p in items[i - 1:])
            break
        shutil.copy2(wf, ACTIVE)
        try:
            r = subprocess.run([str(VENV_PY), str(HEADLESS)], timeout=a.run_timeout)
            (ok if r.returncode == 0 else failed).append(wf.name)
            print(("PASS " if r.returncode == 0 else "FAIL ") + wf.name)
        except subprocess.TimeoutExpired:
            print("FAIL (timeout) " + wf.name)
            failed.append(wf.name)
        if i < len(items):
            print(f"cooldown {a.cooldown}s ...")
            time.sleep(a.cooldown)

    print(f"\n{'=' * 70}\nBATCH SUMMARY: {len(ok)} ok, {len(failed)} failed, {len(skipped)} skipped")
    if failed and not a.no_retry:
        print(f"\nRETRY PASS with --lowvram after {a.cooldown * 2}s settle ...")
        time.sleep(a.cooldown * 2)
        still_failed = []
        for wf_name in failed:
            wf = Path(a.dir) / wf_name
            ram = free_ram_gb()
            print(f"retry {wf_name} (free RAM {ram:.2f} GB)")
            if ram < a.min_ram_gb or not wait_port_free(a.port_wait):
                print("  SKIP: gate failed on retry")
                still_failed.append(wf_name)
                continue
            shutil.copy2(wf, ACTIVE)
            try:
                r = subprocess.run([str(VENV_PY), str(HEADLESS), "--lowvram"],
                                   timeout=a.run_timeout)
                if r.returncode == 0:
                    ok.append(wf_name)
                    print("  PASS (lowvram) " + wf_name)
                else:
                    still_failed.append(wf_name)
                    print("  FAIL (lowvram) " + wf_name)
            except subprocess.TimeoutExpired:
                print("  FAIL (timeout, lowvram) " + wf_name)
                still_failed.append(wf_name)
        failed = still_failed
        print(f"\nFINAL: {len(ok)} ok, {len(failed)} failed, {len(skipped)} skipped")
    for n in failed:
        print("  FAILED:", n)
    for n in skipped:
        print("  SKIPPED:", n)
    return 1 if (failed or skipped) else 0


if __name__ == "__main__":
    sys.exit(main())
