"""Headless FLUX.1-schnell on Intel Arc XPU, via the AI Playground ComfyUI.

WHY HEADLESS RATHER THAN THE ELECTRON UI. The app is an Electron shell; the
image backend underneath is a complete ComfyUI checkout with a real main.py
and its own .venv, so it serves the standard /prompt API. Driving that directly
means no UI, no focus stealing, and a machine-readable result path -- which is
what lets this be an instrument rather than a screenshot.

MEASURED ENVIRONMENT (2026-09-30, quoted because it is the thing that made the
model choice possible at all):
    python  3.12.12   (ComfyUI/.venv)
    torch   2.11.0.dev20260317+xpu
    xpu available  True
    device  Intel(R) Arc(TM) 140T GPU (8GB)
    total_memory 7.80 GB        <- from torch.xpu, NOT from WMI AdapterRAM
The UNet is 11.89 GB on a 7.80 GB card, so ComfyUI WILL offload to system RAM.
Free RAM is therefore the binding constraint, not VRAM -- which is why a
restart is worth ~10 GB here and the run is scheduled after one.

FLUX.1-schnell specifics, so these are not arbitrary: 4 steps and cfg 1.0,
because schnell is a guidance-distilled model where cfg>1 and long step counts
degrade output rather than refine it. EmptySD3LatentImage because FLUX uses
SD3-sized latents. 'flux' type on DualCLIPLoader to pair T5-XXL with CLIP-L.

The negative prompt deliberately names the failure modes this session actually
produced -- cartoon, mannequin, plastic skin, block robot -- so the first probe
either clears that bar or tells us plainly that it does not.
"""
import argparse, json, os, shutil, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

APP = Path(r"C:\Users\clint\AppData\Local\Programs\AI Playground\resources\ComfyUI")
VENV_PY = APP / ".venv" / "Scripts" / "python.exe"
WORKFLOW = Path(__file__).with_name("flux_probe_api.json")
OUT = Path(r"C:\Azazael\azazael\renders\comfyui")
PORT = 8188
HOST = "127.0.0.1"


def _get(path, timeout=10):
    with urllib.request.urlopen(f"http://{HOST}:{PORT}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _post(path, payload, timeout=60):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}{path}", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def preflight():
    """Fail loudly and specifically BEFORE starting a server that would take
    minutes to boot and then die on a missing file."""
    ok = True
    if not VENV_PY.exists():
        print("FATAL: no venv python at", VENV_PY); return False
    print("venv python :", VENV_PY)

    code = ("import torch;print(torch.__version__);"
            "print('xpu', torch.xpu.is_available());"
            "p=torch.xpu.get_device_properties(0);"
            "print('device', p.name);"
            "print('total_gb', round(p.total_memory/1073741824,2))")
    try:
        r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True,
                           text=True, timeout=180, encoding="utf-8", errors="replace")
        for line in r.stdout.strip().splitlines():
            print("   ", line)
        if r.returncode != 0:
            print("    torch probe failed:", r.stderr.strip()[:200]); ok = False
    except Exception as e:
        print("    torch probe failed:", e); ok = False

    mdir = APP.parent / "models" / "ComfyUI"
    for rel, expect in (("diffusion_models/flux1-schnell-fp8-e4m3fn.safetensors", 11891329784),
                        ("text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors", 5157348688),
                        ("clip/clip_l.safetensors", 246144152),
                        ("vae/flux-vae-bf16.safetensors", 167664710)):
        p = mdir / rel
        if not p.exists():
            print(f"    MISSING {rel}"); ok = False
        else:
            got = p.stat().st_size
            flag = "ok" if got == expect else f"INCOMPLETE ({got}/{expect})"
            print(f"    {flag}  {rel}")
            if got != expect:
                ok = False
    return ok


def start_server(wait_s=420, lowvram=False, min_vram_free_gb=5.0):
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = [str(VENV_PY), str(APP / "main.py"),
           "--listen", HOST, "--port", str(PORT),
           "--output-directory", str(OUT),
           "--disable-auto-launch"]
    if lowvram:
        cmd.append("--lowvram")
        print("lowvram mode: weights stream instead of full-load")
    print("launching:", " ".join(cmd[:3]), "... --listen", HOST, "--port", PORT)
    log = open(APP.parent / "comfyui_headless.log", "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(APP), stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print(f"pid {proc.pid}; waiting up to {wait_s}s for the API")
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if proc.poll() is not None:
            print("FATAL: server exited rc=", proc.returncode)
            print("---- tail of comfyui_headless.log ----")
            try:
                tail = (APP.parent / "comfyui_headless.log").read_text(
                    encoding="utf-8", errors="replace").splitlines()[-25:]
                for l in tail:
                    print("   ", l)
            except Exception as e:
                print("    could not read log:", e)
            return None
        try:
            st = _get("/system_stats", timeout=4)
            dev = (st.get("devices") or [{}])[0]
            free_gb = (dev.get("vram_free") or 0) / 1073741824
            print(f"  API up after {int(time.time()-t0)}s  device={dev.get('name')}  "
                  f"vram_free={dev.get('vram_free')}  vram_total={dev.get('vram_total')}")
            if free_gb < min_vram_free_gb:
                print(f"ABORT: XPU free {free_gb:.2f} GB < {min_vram_free_gb} GB gate -- "
                      f"a tenant is squatting (LM Studio model? ollama? browser?). "
                      f"Stopping server instead of OOMing.")
                proc.terminate()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return None
            return proc
        except Exception:
            time.sleep(4)
    print("FATAL: API did not come up in", wait_s, "s")
    return None


def queue(prompt, timeout=3600):
    res = _post("/prompt", {"prompt": prompt}, timeout=120)
    if "error" in res:
        print("QUEUE ERROR:", json.dumps(res["error"])[:500]); return None
    pid = res.get("prompt_id")
    print("queued prompt_id =", pid)
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = _get("/history/" + pid, timeout=15)
        if pid in h:
            out = h[pid].get("outputs", {})
            for node, o in out.items():
                for img in o.get("images", []):
                    print(f"  image node {node}: {img.get('filename')} "
                          f"({img.get('width')}x{img.get('height')}, "
                          f"{img.get('type')})")
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                print("  EXECUTION ERROR:", json.dumps(st)[:400])
                return None
            return h[pid]
        time.sleep(5)
    print("TIMEOUT waiting for", pid)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preflight-only", action="store_true")
    ap.add_argument("--lowvram", action="store_true",
                    help="stream weights (slower, fits squatted/fragmented XPU)")
    ap.add_argument("--no-start", action="store_true",
                    help="assume a server is already listening")
    a = ap.parse_args()

    print("=" * 70)
    print("PREFLIGHT")
    print("=" * 70)
    if not preflight():
        print("\npreflight FAILED -- not starting a server that would die later")
        return 1
    if a.preflight_only:
        return 0
    if a.no_start:
        proc = None
    else:
        proc = start_server(lowvram=a.lowvram)
        if proc is None:
            return 2

    try:
        prompt = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        latent = next((n.get("inputs", {}) for n in prompt.values()
                       if n.get("class_type") == "EmptySD3LatentImage"), {})
        sampler = next((n.get("inputs", {}) for n in prompt.values()
                        if n.get("class_type") == "KSampler"), {})
        prefix = next((n.get("inputs", {}).get("filename_prefix", "?") for n in prompt.values()
                       if n.get("class_type") == "SaveImage"), "?")
        print("\n" + "=" * 70)
        print(f"RUN  prefix={prefix}  steps={sampler.get('steps')} cfg={sampler.get('cfg')} "
              f"{latent.get('width')}x{latent.get('height')} seed={sampler.get('seed')}  (FLUX FP8 on XPU)")
        print("=" * 70)
        t0 = time.time()
        h = queue(prompt)
        if h is None:
            return 3
        print(f"\nelapsed {time.time()-t0:.1f}s")
        print("output dir:", OUT)
        # Auto-deliver: copy fresh outputs to flux archive + Desktop
        delivered = 0
        for dest in (Path(r"C:\Azazael\azazael\renders\flux"),
                     Path.home() / "Desktop"):
            dest.mkdir(parents=True, exist_ok=True)
            for png in OUT.glob("*.png"):
                try:
                    if png.stat().st_mtime >= t0:
                        shutil.copy2(png, dest / png.name)
                        delivered += 1
                except OSError as e:
                    print("deliver failed:", png.name, e)
        print(f"delivered {delivered} file-copies (flux archive + Desktop)")
    finally:
        if proc is not None:
            print("stopping server pid", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
