"""accelerator_bench.py -- measure, then claim. Never claim, then hope.

TODO_315T_BUILD.md G-4 is a gate, not a nicety. This device is an Intel Arc 140T
**integrated** GPU on a **shared** 8 GB aperture. Three probes of that aperture
disagree on purpose (torch.xpu says 7.80 GiB, Vulkan says 8972 MiB with 8267 free,
WMI says 4 GiB) because they measure different things, and an iGPU's decode step is
memory-bandwidth bound in a way a discrete CUDA card's is not. So the honest prior
is: **prefill may win on the GPU and decode may lose to a 16-thread free-threaded
CPU.** Which one actually happens is not knowable from the vendor stack; it is
knowable from this file's output, so this file is what settles it.

The four things it records next to every number, because a number without them is
not admissible (TODO_315T_BUILD.md 5.4):
  1. the device string the backend actually bound;
  2. prefill and decode reported SEPARATELY -- they are different bottlenecks and a
     blended tokens/sec hides the answer;
  3. the same model and the same prompt on the CPU baseline, in the same run;
  4. the free-memory state at start, since the aperture is shared and a benchmark
     taken next to a loaded ComfyUI is not a benchmark.

Token counts come from llama.cpp's own `timings` block. If a backend does not
supply real counts, the row is reported as NO_TIMINGS rather than estimated by
splitting on whitespace -- a fabricated tokens/sec silently wins arguments, which
is the one failure this tool exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

PROMPT = ("List the numbers from 1 to 40 separated by commas, and nothing else.")
DEFAULT_BASE = "http://127.0.0.1:8199"


def _get(url: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print("accelerator_bench:_get failed: %s" % type(e).__name__, file=sys.stderr)
        return None


def free_memory_note() -> str:
    """Free RAM is the binding constraint for a shared aperture; record it, not VRAM.

    Returns the raw number so the caller can format it: an earlier version asked
    PowerShell to do the arithmetic and got an empty string, which printed as
    'GiB RAM free' -- a machine-readable field silently reporting nothing while
    looking filled in.
    """
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        kb = int((p.stdout or "").strip())
        return "%.1f GiB RAM free" % (kb / 1048576.0)
    except Exception as e:
        return "unknown (probe failed: %s)" % type(e).__name__


def one_run(base: str, max_tokens: int, temperature: float) -> Dict[str, Any]:
    body = json.dumps({"messages": [{"role": "user", "content": PROMPT}],
                       "max_tokens": max_tokens, "temperature": temperature}).encode()
    req = urllib.request.Request(base + "/v1/chat/completions", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            payload = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP %s: %s" % (e.code, e.read()[:200])}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    wall = time.perf_counter() - t0

    t = payload.get("timings") or {}
    row: Dict[str, Any] = {
        "ok": True, "wall_s": round(wall, 4),
        "device": payload.get("device"),
        "content_head": (payload.get("choices") or [{}])[0].get("message", {}).get("content", "")[:60],
        "usage": payload.get("usage") or {},
    }
    # llama.cpp: prompt_per_second = prefill, predicted_per_second = decode.
    if "prompt_per_second" in t or "predicted_per_second" in t:
        row["prefill_tok_s"] = round(float(t.get("prompt_per_second") or 0), 2)
        row["decode_tok_s"] = round(float(t.get("predicted_per_second") or 0), 2)
        row["prefill_tokens"] = t.get("prompt_n")
        row["decode_tokens"] = t.get("predicted_n")
    else:
        # No real token counts -> say so, do not invent them.
        row["timings_status"] = "NO_TIMINGS"
    if "load_time" in t:
        row["load_s"] = round(float(t["load_time"]), 2)
    return row


def summarise(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    good = [r for r in runs if r.get("ok")]
    out: Dict[str, Any] = {"n_ok": len(good), "n_failed": len(runs) - len(good)}
    for key in ("prefill_tok_s", "decode_tok_s", "wall_s", "load_s"):
        vals = [r[key] for r in good if isinstance(r.get(key), (int, float))]
        if vals:
            out[key] = {"median": round(statistics.median(vals), 2),
                        "min": round(min(vals), 2), "max": round(max(vals), 2),
                        "n": len(vals)}
    devices = {r.get("device") for r in good if r.get("device")}
    out["devices"] = sorted(d for d in devices if d)
    return out


def device_fallback(summary: Dict[str, Any], health: Dict[str, Any]) -> List[str]:
    """llama.cpp does not echo a device in the completion body, so a number measured
    through it would lose its device and become inadmissible (5.4 rule 1). Fall back
    to the device /health bound, never to the one that was requested."""
    if summary.get("devices"):
        return summary["devices"]
    bound = health.get("device") or health.get("bound_device_name")
    return [str(bound)] if bound else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=DEFAULT_BASE, help="sidecar base URL")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--label", default=None, help="e.g. 'Arc 140T Vulkan' or 'CPU baseline 16T'")
    ap.add_argument("--out", default=None, help="append the result JSON here")
    args = ap.parse_args()

    health = _get(args.base + "/health")
    if not health:
        print(json.dumps({"ok": False, "why": "sidecar /health unreachable at %s" % args.base},
                         indent=2))
        return 2

    label = args.label or str(health.get("backend"))
    print("=== %s ===" % label)
    print("  backend  : %s" % health.get("backend"))
    print("  device   : %s" % health.get("device"))
    print("  bound by : %s" % health.get("bound_by"))
    print("  model    : %s" % health.get("model"))
    print("  memory   : %s" % free_memory_note())
    print("  runs     : %d x %d tokens (identical prompt)" % (args.runs, args.max_tokens))
    print()

    runs = []
    for i in range(args.runs):
        r = one_run(args.base, args.max_tokens, args.temperature)
        runs.append(r)
        if not r.get("ok"):
            print("  run %d: FAILED %s" % (i + 1, r.get("error")))
            continue
        print("  run %d: prefill %-8s tok/s | decode %-8s tok/s | wall %ss%s"
              % (i + 1, r.get("prefill_tok_s", "-"), r.get("decode_tok_s", "-"),
                 r.get("wall_s"), "  [NO_TIMINGS]" if r.get("timings_status") else ""))

    summary = summarise(runs)
    print()
    print("  --- %s ---" % label)
    for key in ("prefill_tok_s", "decode_tok_s", "wall_s"):
        if key in summary:
            s = summary[key]
            print("  %-14s median %8s   (min %s, max %s, n=%d)"
                  % (key, s["median"], s["min"], s["max"], s["n"]))
    summary["devices"] = device_fallback(summary, health)
    print("  device(s)     %s" % (", ".join(summary["devices"])
                                 or "UNREPORTED - do not quote a number"))

    record = {"label": label, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "health": health, "memory": free_memory_note(),
              "max_tokens": args.max_tokens, "prompt": PROMPT,
              "runs": runs, "summary": summary}
    if args.out:
        with open(args.out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        print("  appended -> %s" % args.out)
    return 0 if summary["n_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
