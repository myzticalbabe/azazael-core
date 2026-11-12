#!/usr/bin/env python3
# coding: utf-8
"""Measure the box this tree is running on, and say what is UNKNOWN.

WHY THIS EXISTS
---------------
Every number in `docs/platforms/` is a property of a machine, not of this
software. The reference build runs on an Intel Core Ultra 9 285H with an Arc
140T iGPU, and several choices in this tree -- Accelerate/XPU for torch,
`ONEAPI` for Cycles, an 8 GB shared-memory VRAM budget -- are wrong or
unavailable on a Mac, an Android phone, or an NVIDIA card. A reader who copies
a command from here onto their own box gets a failure that looks like a bug in
this project.

So the first step of "fit your box" is a measurement, and this tool is it. It
writes a JSON profile that the platform docs are written against and that the
rest of the tree can read instead of hard-coding assumptions.

THE RULE THAT MATTERS
---------------------
THREE states, never two:

    available   -- the backend is importable AND reports a device
    unavailable -- the backend is importable, reports NO device (driver, or a
                   wheel without that backend compiled in)
    unknown     -- the backend's library is not installed at all

"unknown" is not "no GPU". On a machine without torch, the honest reading is
that this tool cannot tell you whether the hardware is there, and the remedy is
to install the library named in `remedy`. Collapsing unknown into "no
accelerator" is how a box gets documented as CPU-only for a year.

VRAM is reported only when the backend reports it. `Win32_VideoController`
`AdapterRAM` is deliberately NOT used: on this box it returns a static 4.00 GiB
aperture while torch reports 7.80 GB usable, so the WMI figure is wrong twice
over (static, and not the aperture that is actually allocatable).

Usage:
  python tools/box_profile.py                 # human table
  python tools/box_profile.py --json
  python tools/box_profile.py --write box_profile.json
  python tools/box_profile.py --check box_profile.json   # drift vs a stored profile
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

#: Preference order, and it is a statement about this tree's tuning rather than
#: about the world: XPU first because the reference box has one, then CUDA, then
#: Apple's MPS, then plain CPU. A CUDA-only box ranks CUDA first, and that
#: difference is exactly what the platform docs have to describe.
PREFERENCE = ("xpu", "cuda", "mps")


#: Why a value could not be measured. A profile that prints `memory_gb: null`
#: and nothing else sends the reader looking for a bug in this tool; one that
#: prints the reason sends them to the right platform page. Every swallow below
#: records here, which is also why none of them is an empty `except`.
UNMEASURED: List[str] = []


def _note_missing(what: str, exc: BaseException) -> None:
    """Record a measurement this box would not give up, and why."""
    UNMEASURED.append("%s: %s: %s" % (what, exc.__class__.__name__, exc))


def _run(cmd: Sequence[str], timeout: float = 5.0) -> Optional[str]:
    """Run a small system command, returning stdout or None. Never raises.

    None means "could not tell", which every caller must pass through to the
    profile as unknown rather than turning into a negative.
    """
    output: Optional[str] = None
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=timeout)
        if result.returncode == 0:
            output = result.stdout.strip()
        else:
            _note_missing(" ".join(cmd[:2]), OSError("exit %d" % result.returncode))
    except (OSError, subprocess.SubprocessError) as exc:
        _note_missing(" ".join(cmd[:2]), exc)
    return output


def cpu_model() -> str:
    """Best available CPU name, without inventing one."""
    if sys.platform.startswith("win"):
        # PROCESSOR_IDENTIFIER is present on every Windows and needs no WMI.
        value = os.environ.get("PROCESSOR_IDENTIFIER")
        if value:
            return value
        return platform.processor() or "unknown"
    if sys.platform == "darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "unknown"
    named: Optional[str] = None
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                named = line.split(":", 1)[1].strip()
                break
    except OSError as exc:
        _note_missing("/proc/cpuinfo", exc)
    return named or platform.processor() or "unknown"


def _windows_memory_gb() -> Optional[float]:
    """GlobalMemoryStatusEx, which reports usable physical RAM (unlike WMI)."""
    total: Optional[float] = None
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong),
                        ("avail_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong),
                        ("avail_virtual", ctypes.c_ulonglong),
                        ("avail_extended", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            total = round(status.total / (1024 ** 3), 1)
        else:
            _note_missing("GlobalMemoryStatusEx", OSError("returned 0"))
    except (OSError, AttributeError, ValueError) as exc:
        _note_missing("Win32 memory", exc)
    return total


def total_memory_gb() -> Optional[float]:
    """Physical RAM in GiB, or None when this platform's route is unavailable."""
    if sys.platform.startswith("win"):
        return _windows_memory_gb()
    if sys.platform == "darwin":
        raw = _run(["sysctl", "-n", "hw.memsize"])
        return round(int(raw) / (1024 ** 3), 1) if raw and raw.isdigit() else None
    total: Optional[float] = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                total = round(int(line.split()[1]) / (1024 ** 2), 1)
                break
    except (OSError, ValueError, IndexError) as exc:
        _note_missing("/proc/meminfo", exc)
    return total


def _torch_accelerators() -> Dict[str, Dict]:
    """What torch says about its backends, or a single 'library absent' record.

    Imported INSIDE the function and behind a broad guard: this tool has to run
    on a phone where torch cannot be installed at all, and a profile tool that
    crashes on the box it is meant to describe is useless.
    """
    try:
        import torch  # noqa: F401  (only to probe, never to compute)
    except Exception as exc:  # ImportError, or a broken binary wheel
        return {"_library": {"torch": "not importable: %s" % exc.__class__.__name__}}

    out: Dict[str, Dict] = {}
    try:
        import torch

        out["_library"] = {"torch": torch.__version__,
                           "compiled_cuda": bool(getattr(torch.version, "cuda", None)),
                           "built_with_xpu": bool(getattr(torch.version, "xpu", None))}

        xpu_available = False
        try:
            xpu_available = bool(torch.xpu.is_available())
        except (AttributeError, RuntimeError):
            xpu_available = False
        record: Dict[str, object] = {"available": xpu_available}
        if xpu_available:
            try:
                props = torch.xpu.get_device_properties(0)
                record.update({
                    "name": getattr(props, "name", "unknown"),
                    # total_memory is the allocatable aperture. This is the only
                    # VRAM figure quoted anywhere in this tree, on purpose.
                    "total_memory_gb": round(props.total_memory / (1024 ** 3), 2),
                    "device_count": torch.xpu.device_count(),
                })
            except (RuntimeError, AttributeError) as exc:
                record["error"] = "%s: %s" % (exc.__class__.__name__, exc)
        out["xpu"] = record

        cuda_available = False
        try:
            cuda_available = bool(torch.cuda.is_available())
        except (AttributeError, RuntimeError):
            cuda_available = False
        record = {"available": cuda_available}
        if cuda_available:
            try:
                record.update({
                    "name": torch.cuda.get_device_name(0),
                    "total_memory_gb": round(
                        torch.cuda.get_device_properties(0).total_memory / (1024 ** 3), 2),
                    "device_count": torch.cuda.device_count(),
                })
            except (RuntimeError, AttributeError) as exc:
                record["error"] = "%s: %s" % (exc.__class__.__name__, exc)
        out["cuda"] = record

        mps_available = False
        try:
            mps = getattr(torch.backends, "mps", None)
            mps_available = bool(mps and mps.is_available())
            # MPS reports availability but no per-device memory API, so VRAM on
            # Apple silicon is UNIFIED memory: the RAM figure, stated as such.
            out["mps"] = {"available": mps_available,
                          "built": bool(mps and mps.is_built())}
        except (AttributeError, RuntimeError):
            out["mps"] = {"available": False}
    except Exception as exc:
        out.setdefault("_library", {})["error"] = "%s: %s" % (exc.__class__.__name__, exc)
    return out


def npu_present() -> Dict[str, object]:
    """Intel NPU presence, without claiming torch can drive it (it cannot)."""
    if sys.platform.startswith("linux"):
        devices = sorted(str(p) for p in Path("/dev").glob("accel*")) \
            if Path("/dev").exists() else []
        return {"present": bool(devices), "devices": devices,
                "note": "torch does not drive the NPU; OpenVINO does"}
    if sys.platform.startswith("win"):
        # No stdlib route to an Intel NPU on Windows; say so rather than guess.
        return {"present": None, "note": "not detectable with stdlib on Windows; "
                                         "check `Get-PnpDevice -Class ComputeAccelerator`"}
    return {"present": None, "note": "no stdlib route on this platform"}


def profile() -> Dict[str, object]:
    """The whole box, with three-state accelerator records and no guesses."""
    # Cleared per call: this list is module-level so every swallow can reach it,
    # and a second profile() in one process must not inherit the first one's
    # reasons -- duplicated notes read as two separate failures.
    del UNMEASURED[:]
    python_bits = {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
        "free_threaded": None,
    }
    free_threaded = getattr(sys, "_is_gil_enabled", None)
    if free_threaded is not None:
        try:
            python_bits["free_threaded"] = not free_threaded()
        except Exception:
            python_bits["free_threaded"] = None

    torch_info = _torch_accelerators()
    library = torch_info.pop("_library", {})

    accelerators: List[Dict] = []
    for backend in ("xpu", "cuda", "mps"):
        record = torch_info.get(backend)
        if record is None:
            accelerators.append({
                "backend": backend, "status": "unknown",
                "reason": "torch is not installed, so this backend cannot be probed",
                "remedy": "install torch for this platform (docs/platforms/README.md)"})
            continue
        status = "available" if record.get("available") else "unavailable"
        accelerators.append(dict(record, backend=backend, status=status,
                                 remedy=None if status == "available" else
                                 "installable but reports no device: check driver "
                                 "and the wheel's compiled backends"))

    npu = npu_present()
    accelerators.append({
        "backend": "npu",
        "status": ("available" if npu.get("present") else
                   "unknown" if npu.get("present") is None else "unavailable"),
        "note": npu.get("note"), "devices": npu.get("devices"),
        "remedy": "OpenVINO path only; torch cannot target this device"})

    accelerators.append({"backend": "cpu", "status": "available",
                         "note": "always present; the floor every backend falls back to"})

    return {
        "unmeasured": list(UNMEASURED),
        "measured_at_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "os": {"system": platform.system(), "release": platform.release(),
               "version": platform.version(), "machine": platform.machine(),
               "is_wsl": "microsoft" in platform.release().lower()},
        "cpu": {"model": cpu_model(), "logical_count": os.cpu_count()},
        "memory_gb": total_memory_gb(),
        "python": python_bits,
        "torch_library": library,
        "accelerators": accelerators,
        "preferred_accelerator": next(
            (a["backend"] for a in accelerators
             if a["backend"] in PREFERENCE and a["status"] == "available"),
            "cpu"),
        "tooling": {
            "git": shutil.which("git"),
            "ffmpeg": shutil.which("ffmpeg"),
            "node": shutil.which("node"),
            "blender": shutil.which("blender"),
            "chrome": shutil.which("chrome") or shutil.which("google-chrome"),
        },
    }


def render(box: Dict) -> str:
    lines: List[str] = []
    add = lines.append
    add("=" * 78)
    add("BOX PROFILE  --  %s / %s" % (box["os"]["system"], box["os"]["machine"]))
    add("=" * 78)
    add("cpu      %s" % box["cpu"]["model"])
    add("         %s logical processor(s), %.1f GiB RAM"
        % (box["cpu"]["logical_count"], box["memory_gb"] or float("nan")))
    add("python   %s (%s)%s" % (box["python"]["version"], box["python"]["implementation"],
                                "" if box["python"]["free_threaded"] is None else
                                "  free-threaded=%s" % box["python"]["free_threaded"]))
    add("")
    add("   %-6s %-12s %-28s %s" % ("device", "status", "detail", "meaning"))
    for record in box["accelerators"]:
        detail = record.get("name") or record.get("note") or record.get("reason") or ""
        if record.get("total_memory_gb") is not None:
            detail = "%s (%s GiB)" % (detail, record["total_memory_gb"])
        meaning = {True: "usable",
                   False: "no (this is a measurement, not a fault)"}.get(
                       record.get("available"), "")
        if record["status"] == "unknown":
            meaning = "NOT MEASURED -- not the same as absent"
        add("   %-6s %-12s %-28s %s"
            % (record["backend"], record["status"], detail[:28], meaning))
    add("")
    add("preferred accelerator: %s" % box["preferred_accelerator"])
    add("torch: %s" % (box["torch_library"].get("torch") or "not importable"))
    missing = [name for name, path in box["tooling"].items() if not path]
    if missing:
        add("not on PATH: %s" % ", ".join(missing))
    for line in box.get("unmeasured", []):
        add("could not measure -- %s" % line)
    add("")
    add("Every 'unknown' above needs the named remedy before this profile can be")
    add("compared with a platform document. See docs/platforms/README.md.")
    return "\n".join(lines)


def drift(stored: Dict, fresh: Dict) -> List[str]:
    """Differences worth a human's attention between a stored and a fresh profile."""
    problems: List[str] = []
    for key in ("system", "machine"):
        if stored.get("os", {}).get(key) != fresh.get("os", {}).get(key):
            problems.append("os.%s: stored %r, now %r"
                            % (key, stored.get("os", {}).get(key), fresh["os"].get(key)))
    old = {r["backend"]: r for r in stored.get("accelerators", [])}
    new = {r["backend"]: r for r in fresh.get("accelerators", [])}
    for backend in sorted(set(old) | set(new)):
        if backend not in old:
            problems.append("%s: not in the stored profile" % backend)
            continue
        if backend not in new:
            problems.append("%s: in the stored profile, absent now" % backend)
            continue
        if old[backend].get("status") != new[backend].get("status"):
            problems.append("%s: stored %s, now %s"
                            % (backend, old[backend].get("status"),
                               new[backend].get("status")))
    if stored.get("preferred_accelerator") != fresh.get("preferred_accelerator"):
        problems.append("preferred_accelerator: stored %r, now %r"
                        % (stored.get("preferred_accelerator"),
                           fresh.get("preferred_accelerator")))
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable profile")
    parser.add_argument("--write", metavar="PATH", help="store this profile as JSON")
    parser.add_argument("--check", metavar="PATH",
                        help="compare a stored profile with this box; 1 on drift")
    args = parser.parse_args(list(argv) if argv is not None else None)

    box = profile()

    if args.check:
        try:
            stored = json.loads(Path(args.check).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("cannot read stored profile: %s" % exc, file=sys.stderr)
            return 2
        problems = drift(stored, box)
        if problems:
            print("DRIFT against %s" % args.check)
            for line in problems:
                print("  %s" % line)
            print("\nA platform document that claims a backend this box does not have "
                  "is a document nobody can follow. Fix the document, not the reader.")
            return 1
        print("no drift against %s" % args.check)
        return 0

    if args.write:
        Path(args.write).write_text(json.dumps(box, indent=1) + "\n", encoding="utf-8")
        print("wrote %s" % args.write)

    print(json.dumps(box, indent=1) if args.json else render(box))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
