#!/usr/bin/env python3
# coding: utf-8
"""accelerator_preflight.py — a device without its interpreter is not a capability.

THE INCIDENT THIS EXISTS FOR
----------------------------
`CAPABILITIES.md` states, as a measured fact:

    | XPU available | `True` | `torch.xpu.is_available()` |
    | usable GPU memory | 7.80 GB | get_device_properties(0).total_memory |

Re-measured on 2026-10-09, the production interpreter disagrees:

    C:/Azazael/.venvs/py315t/Scripts/python.exe   3.15.0rc3   torch 2.16.0.dev+cpu   xpu False
    .../Programs/Python/Python314/python.exe      3.14.7      torch 2.14.0+cpu       xpu False

BOTH READINGS WERE CORRECT. They describe DIFFERENT INTERPRETERS. The `+xpu`
torch that really does bind the Arc lives in a third venv -- AI Playground's
ComfyUI environment, on Python 3.12 -- and nothing in the capability file said
so. Read from inside py315t, "XPU available: True" is simply false, and a session
that trusts it plans work that cannot run. The same mistake hides capability in
the other direction: an interpreter that CAN bind the Arc looks unreachable if
you only ever probe your own.

THE RULE
--------
A device is only a capability together with the interpreter that bound it.
Report them as a pair or not at all. `CAPABILITIES.md` §2 already enforces this
for memory probes -- "a time without a device is not a measurement" -- after WMI's
4 GiB aperture was mistaken for bindable VRAM. This is the same rule with the
missing half being the interpreter rather than the device, and it is worth a
tool because the failure is invisible from inside the environment it misleads.

WHAT IS PROBED
--------------
Candidate interpreters, each spawned as a subprocess that prints one JSON blob:

    py315t                    the production interpreter
    system-3.15               launcher-ladder fallback
    system-3.14               carries the working OpenVINO
    ai-playground-comfyui     the only `+xpu` torch on this box

plus every llama.cpp binary found by globbing ~/llama-server/bin/*/llama-server.exe,
each run with `--list-devices`. Native binaries carry no interpreter, which is
exactly why they are worth probing: they are the accelerator path that does not
need a wheel to exist for the production interpreter.

EACH DEVICE IS LABELLED WITH ITS ORIGIN
---------------------------------------
    production-in-process  bound by an interpreter in the production env
    native-binary          bound by a native binary (interpreter-independent)
    other-interpreter      bound by a local interpreter outside the production env

VERDICTS
--------
    ACCELERATOR_AVAILABLE  reachable in production, in-process or via a binary
    SIDECAR_ONLY           only reachable from another local interpreter --
                           which means reach it out-of-process, over HTTP
    NONE                   nothing anywhere bound a device

HONESTY RULES
-------------
  * A missing interpreter, an ImportError, a timeout and unparseable output are
    each a REPORTED STATE, never a crash.
  * A probe that could not run is never allowed to read as a passing probe.
    That is what exit 3 is for. `MISSING` is a fact about the machine, not a
    failed probe, so a candidate that is simply absent does not trip it.
  * The verdict never hides which interpreter bound the device it is reporting.

Usage:
    python tools/accelerator_preflight.py
    python tools/accelerator_preflight.py --json --timeout 90
Exit: 0 probes ran / 2 verdict NONE / 3 a probe could not run (3 outranks 2)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)                       # .../azazael
AZ = os.path.dirname(ROOT)                          # the project root, C:/Azazael
OUT = os.path.join(ROOT, "state", "accelerator_preflight.json")

DEFAULT_TIMEOUT = 60.0
SENTINEL = "@@PREFLIGHT@@"

#: Interpreters that count as "in the production env". Kept as labels rather than
#: paths so a moved venv is reported as MISSING instead of silently skipped.
PRODUCTION_LABELS = ("py315t", "system-3.15")

#: Substrings that mark a real accelerator. Deliberately excludes "cpu": an
#: OpenVINO device list always contains CPU, and counting it would make every
#: box look accelerated.
ACCEL_TOKENS = ("arc", "ai boost", "npu", "gpu")

#: Printed by each candidate interpreter. Kept as one blob behind a sentinel so
#: stray warnings on stdout cannot be mistaken for the reading.
PROBE_SRC = r'''
import json, sys
r = {"python": sys.version.split()[0], "gil_enabled": None, "torch": None,
     "torch_error": None, "xpu_available": None, "xpu_error": None,
     "xpu_devices": [], "openvino": None, "openvino_error": None,
     "openvino_devices": []}
try:
    r["gil_enabled"] = bool(sys._is_gil_enabled()) if hasattr(sys, "_is_gil_enabled") else None
except Exception:
    r["gil_enabled"] = None
try:
    import torch
    r["torch"] = torch.__version__
    try:
        r["xpu_available"] = bool(torch.xpu.is_available())
        if r["xpu_available"]:
            for i in range(torch.xpu.device_count()):
                p = torch.xpu.get_device_properties(i)
                r["xpu_devices"].append({"index": i, "name": p.name,
                                         "total_gib": round(p.total_memory / 2**30, 2)})
    except Exception as e:
        r["xpu_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
except Exception as e:
    r["torch_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
try:
    import openvino as ov
    r["openvino"] = ov.__version__
    c = ov.Core()
    for d in c.available_devices:
        try:
            r["openvino_devices"].append({"device": d, "name": c.get_property(d, "FULL_DEVICE_NAME")})
        except Exception:
            r["openvino_devices"].append({"device": d, "name": None})
except Exception as e:
    r["openvino_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
print("@@PREFLIGHT@@" + json.dumps(r))
'''


def _candidates() -> list[tuple[str, str]]:
    """(label, path) for every interpreter worth asking. Absence is reported."""
    local = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs")
    return [
        ("py315t", os.path.join(AZ, ".venvs", "py315t", "Scripts", "python.exe")),
        ("system-3.15", os.path.join(local, "Python", "Python315", "python.exe")),
        ("system-3.14", os.path.join(local, "Python", "Python314", "python.exe")),
        ("ai-playground-comfyui",
         os.path.join(local, "AI Playground", "resources", "ComfyUI", ".venv",
                      "Scripts", "python.exe")),
    ]


def _probe_interpreter(py: str, timeout: float) -> dict:
    """Ask one interpreter. Every failure mode is a returned state, never a raise."""
    rec: dict = {"interpreter": py, "status": "OK", "detail": "", "probe": None}
    if not os.path.exists(py):
        rec["status"] = "MISSING"
        rec["detail"] = "interpreter not present"
        return rec
    try:
        p = subprocess.run([py, "-c", PROBE_SRC], capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        rec["status"] = "TIMEOUT"
        rec["detail"] = "no output within %.0fs" % timeout
        return rec
    except Exception as e:
        rec["status"] = "ERROR"
        rec["detail"] = ("%s: %s" % (type(e).__name__, e))[:160]
        return rec
    raw = p.stdout or ""
    for line in raw.splitlines():
        if line.startswith(SENTINEL):
            try:
                rec["probe"] = json.loads(line[len(SENTINEL):])
            except Exception as e:
                rec["status"] = "UNPARSEABLE"
                rec["detail"] = "sentinel line is not JSON (%s)" % type(e).__name__
            return rec
    err = (p.stderr or "").strip().splitlines()
    rec["status"] = "UNPARSEABLE"
    rec["detail"] = ("rc=%d %s" % (p.returncode, err[-1][:140] if err else
                                   (raw.strip()[-140:] or "no output")))
    return rec


def _probe_binaries(timeout: float) -> list[dict]:
    """Run --list-devices on every llama.cpp server binary present."""
    pattern = os.path.join(os.path.expanduser("~"), "llama-server", "bin", "*",
                           "llama-server.exe")
    out = []
    for exe in sorted(glob.glob(pattern)):
        rec: dict = {"binary": exe, "label": os.path.basename(os.path.dirname(exe)),
                     "status": "OK", "detail": "", "devices": []}
        try:
            p = subprocess.run([exe, "--list-devices"], capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            rec["status"] = "TIMEOUT"
            rec["detail"] = "no output within %.0fs" % timeout
            out.append(rec)
            continue
        except Exception as e:
            rec["status"] = "ERROR"
            rec["detail"] = ("%s: %s" % (type(e).__name__, e))[:160]
            out.append(rec)
            continue
        text = (p.stdout or "") + "\n" + (p.stderr or "")
        for line in text.splitlines():
            s = line.strip().replace("\r", "")
            if not s or s.lower().startswith("available devices"):
                continue
            if s == "(none)":
                rec["detail"] = "reports (none)"
            elif ":" in s and any(t in s for t in ("GPU", "Vulkan", "SYCL", "Intel")):
                rec["devices"].append(s)
        out.append(rec)
    return out


def _probe_runtime() -> dict:
    """Level Zero is the runtime every Intel path sits on. Its absence is evidence."""
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    loader = os.path.join(sys32, "ze_loader.dll")
    compute = os.path.join(sys32, "ze_intel_gpu.dll")
    return {
        "ze_loader_present": os.path.exists(loader),
        "ze_intel_gpu_present": os.path.exists(compute),
        "note": ("ze_intel_gpu.dll is the GPU compute runtime; its absence does not "
                 "block the probing paths below, which each bind the device through "
                 "their own runtime, but it is the prerequisite for in-process XPU."),
    }


def _is_accelerator(text: str) -> bool:
    t = (text or "").lower()
    return any(tok in t for tok in ACCEL_TOKENS)


def _bind(probes: list[tuple[str, dict]], bins: list[dict]) -> list[dict]:
    """Every device, paired with whatever bound it. This pairing IS the deliverable."""
    bound: list[dict] = []
    for label, rec in probes:
        pr = rec.get("probe") or {}
        origin = "production-in-process" if label in PRODUCTION_LABELS else "other-interpreter"
        if pr.get("xpu_available"):
            for d in pr.get("xpu_devices") or []:
                bound.append({"via": "torch.xpu", "source": label,
                              "interpreter": rec["interpreter"], "origin": origin,
                              "device": d.get("name"), "index": d.get("index"),
                              "total_gib": d.get("total_gib")})
        for d in pr.get("openvino_devices") or []:
            key, name = d.get("device") or "", d.get("name") or ""
            if _is_accelerator(key) or _is_accelerator(name):
                bound.append({"via": "openvino", "api_device": key, "source": label,
                              "interpreter": rec["interpreter"], "origin": origin,
                              "device": name, "total_gib": None})
    for rec in bins:
        for dev in rec.get("devices") or []:
            if _is_accelerator(dev):
                bound.append({"via": "llama.cpp", "source": rec["label"],
                              "interpreter": None, "binary": rec["binary"],
                              "origin": "native-binary", "device": dev,
                              "total_gib": None})
    return bound


def _verdict(bound: list[dict]) -> str:
    origins = {b["origin"] for b in bound}
    if origins & {"production-in-process", "native-binary"}:
        return "ACCELERATOR_AVAILABLE"
    if origins:
        return "SIDECAR_ONLY"
    return "NONE"


def _table(bound: list[dict], probes: list[tuple[str, dict]], bins: list[dict],
           runtime: dict) -> str:
    lines = ["interpreter probes",
             "  %-24s %-12s %-10s %-6s %-22s %-6s %s" %
             ("label", "status", "python", "gil", "torch", "xpu", "openvino")]
    for label, rec in probes:
        pr = rec.get("probe") or {}
        gil = pr.get("gil_enabled")
        lines.append("  %-24s %-12s %-10s %-6s %-22s %-6s %s" % (
            label, rec["status"], pr.get("python") or "-",
            "-" if gil is None else str(gil),
            (pr.get("torch") or "-")[:22],
            "-" if pr.get("xpu_available") is None else str(pr.get("xpu_available")),
            (pr.get("openvino") or "-")[:10]))
        if rec["status"] != "OK" and rec["detail"]:
            lines.append("  %-24s   ^ %s" % ("", rec["detail"]))

    lines.append("")
    lines.append("llama.cpp binaries")
    if not bins:
        lines.append("  (none found under ~/llama-server/bin/*/llama-server.exe)")
    for rec in bins:
        shown = rec["devices"] or ([rec["detail"]] if rec["detail"] else [])
        lines.append("  %-24s %-12s %s" % (rec["label"], rec["status"],
                                           "; ".join(shown) if shown else "-"))

    lines.append("")
    lines.append("devices, each with whatever bound it")
    if not bound:
        lines.append("  (no accelerator device bound by any probe)")
    for b in bound:
        who = b.get("interpreter") or b.get("binary") or "?"
        gib = (" %.2f GiB" % b["total_gib"]) if b.get("total_gib") else ""
        lines.append("  [%-22s] %-11s %-24s %s%s" %
                     (b["origin"], b["via"], b["source"], b["device"], gib))
        lines.append("      bound by: %s" % who)

    lines.append("")
    lines.append("level zero: ze_loader=%s ze_intel_gpu=%s" %
                 (runtime["ze_loader_present"], runtime["ze_intel_gpu_present"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report every accelerator device together with the interpreter "
                    "or binary that bound it.")
    ap.add_argument("--json", action="store_true",
                    help="print the machine-readable result and nothing else")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="per-probe timeout in seconds (default: %(default)s)")
    ap.add_argument("--quiet", action="store_true", help="print only the verdict")
    args = ap.parse_args(argv)

    probes = [(label, _probe_interpreter(py, args.timeout))
              for label, py in _candidates()]
    bins = _probe_binaries(args.timeout)
    runtime = _probe_runtime()
    bound = _bind(probes, bins)
    verdict = _verdict(bound)

    prod = [b for b in bound if b["origin"] in ("production-in-process", "native-binary")]
    result = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host_interpreter": {"path": sys.executable, "python": sys.version.split()[0]},
        "timeout_s": args.timeout,
        "production_labels": list(PRODUCTION_LABELS),
        "verdict": verdict,
        "verdict_basis": ([b["device"] for b in prod] or
                          ["no production-reachable device; see bound_devices"]),
        "interpreters": [{"label": lb, "status": r["status"], "detail": r["detail"],
                          "probe": r["probe"]} for lb, r in probes],
        "llama_cpp": bins,
        "runtime": runtime,
        "bound_devices": bound,
    }

    write_error = ""
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    except Exception as e:
        write_error = ("%s: %s" % (type(e).__name__, e))[:160]
        result["state_write_error"] = write_error

    # A probe that could not run is not a probe that passed. MISSING is a fact
    # about the machine, so it is excluded from this test on purpose.
    could_not_run = [lb for lb, r in probes
                     if r["status"] in ("ERROR", "TIMEOUT", "UNPARSEABLE")]
    could_not_run += [b["label"] for b in bins
                      if b["status"] in ("ERROR", "TIMEOUT", "UNPARSEABLE")]

    if args.json:
        print(json.dumps(result, indent=2))
    elif args.quiet:
        print("accelerator preflight: %s" % verdict)
    else:
        print("accelerator preflight — %s" % result["at"])
        print("verdict: %s" % verdict)
        for b in prod:
            print("  reachable in production: %s via %s" % (b["device"], b["via"]))
        if verdict == "SIDECAR_ONLY":
            print("  no production interpreter binds a device -- reach one "
                  "out-of-process rather than waiting on a wheel")
        print()
        print(_table(bound, probes, bins, runtime))
        print()
        print("wrote %s" % OUT if not write_error else "STATE WRITE FAILED: %s" % write_error)

    if could_not_run:
        print("probe could not run: %s" % ", ".join(could_not_run), file=sys.stderr)
        return 3
    if write_error:
        return 3
    if verdict == "NONE":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
