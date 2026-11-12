#!/usr/bin/env python
"""One-command picture loop: brief -> Z-engineer draft -> enforce lock ->
stand down GPU tenant -> Klein render. Usage:
  python z_auto.py --prefix NAME --seed N --brief "text..." [--lock fairy|couple]
Everything else is automatic: server start/load, draft, normalize, lock,
unload, server stop, RAM/port gates, render, deliver.
"""
import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "style_samples"
ACTIVE = HERE / "flux_probe_api.json"
HEADLESS = HERE / "comfyui_flux_headless.py"
VENV_PY = Path(r"C:\Users\clint\AppData\Local\Programs\AI Playground\resources\ComfyUI\.venv\Scripts\python.exe")
MODEL = "qwen3-4b-z-image-engineer-v4"
PORT = 8188
MIN_RAM_GB = 4.0

LOCKS = {
    "fairy": (" Red diamond marks ONLY on her; no letters or text anywhere. "
              "Both her arms graceful and fully visible with delicate hands."),
    "couple": (" Black spade marks ONLY on him; red diamond marks ONLY on her; "
               "no letters or text anywhere. Both her arms fully visible with defined hands, "
               "separated from her gown; his arms visible with defined hands. He has short "
               "silver-grey hair with NO red hair anywhere on him; her very long red-orange "
               "hair flows past her shoulders."),
}

REPLACEMENTS = {"\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
                "\u201c": '"', "\u201d": '"', "\u2026": "..."}


def run(cmd, timeout=300):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out.strip()[-500:]


def lms(*args, timeout=300):
    return run(["lms", *args], timeout=timeout)


def complete(brief):
    body = json.dumps(
        {"model": MODEL,
         "messages": [{"role": "system", "content": (
             "You write image prompts for a FLUX image model. "
             "Reply with ONLY the prompt paragraph.")},
                      {"role": "user", "content": brief}],
         "temperature": 0.8, "max_tokens": 800}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:1234/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"].strip()


def free_ram_gb():
    import ctypes

    class MSTAT(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MSTAT()
    st.dwLength = ctypes.sizeof(MSTAT)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullAvailPhys / 1073741824


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port)) != 0


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--seed", required=True, type=int)
    ap.add_argument("--brief", required=True)
    ap.add_argument("--lock", default="fairy", choices=list(LOCKS))
    a = ap.parse_args()

    print("[1/6] lms server + model ...")
    print(lms("server", "start")[1][-200:])
    print(lms("load", MODEL, "--ttl", "900", timeout=300)[1][-200:])

    print("[2/6] Z draft ...")
    raw = complete(a.brief)
    (SAMPLES / f"ZRAW_{a.prefix}_draft.txt").write_text(raw, encoding="utf-8")

    print("[3/6] enforce ...")
    text = raw
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    text.encode("ascii")
    final = " ".join(text.split()) + LOCKS[a.lock]
    (SAMPLES / f"ZF_{a.prefix}_final.txt").write_text(final, encoding="utf-8")
    wf = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "flux-2-klein-4b-fp8.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "flux2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "4": {"class_type": "EmptySD3LatentImage",
              "inputs": {"batch_size": 1, "height": 1024, "width": 1024}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": final}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
        "7": {"class_type": "KSampler",
              "inputs": {"cfg": 1.0, "denoise": 1.0, "latent_image": ["4", 0], "model": ["1", 0],
                         "negative": ["6", 0], "positive": ["5", 0], "sampler_name": "euler",
                         "scheduler": "simple", "seed": a.seed, "steps": 4}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": a.prefix, "images": ["8", 0]}},
    }
    (SAMPLES / f"{a.prefix}.json").write_text(json.dumps(wf), encoding="utf-8")
    print(f"  {len(final.split())} words, ascii-ok")

    print("[4/6] stand down GPU tenant ...")
    print(lms("unload", MODEL)[1][-200:])
    print(lms("server", "stop")[1][-200:])
    time.sleep(5)

    print("[5/6] gates ...")
    ram = free_ram_gb()
    print(f"  free RAM {ram:.2f} GB (gate >= {MIN_RAM_GB})")
    if ram < MIN_RAM_GB or not port_free(PORT):
        print("  ABORT: gate failed")
        return 1

    print("[6/6] render ...")
    shutil.copy2(SAMPLES / f"{a.prefix}.json", ACTIVE)
    r = subprocess.run([str(VENV_PY), str(HEADLESS)], timeout=1200)
    print("  headless rc =", r.returncode)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
