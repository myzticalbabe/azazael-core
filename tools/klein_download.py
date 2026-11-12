#!/usr/bin/env python
"""Download FLUX.2 Klein files into the AI Playground ComfyUI models tree."""
import sys
from pathlib import Path
from huggingface_hub import hf_hub_download, list_repo_files

MODELS = Path(r"C:\Users\clint\AppData\Local\Programs\AI Playground\resources\models\ComfyUI")
WANT = [
    ("black-forest-labs/FLUX.2-klein-4b-fp8", None, "diffusion_models"),
    ("Comfy-Org/vae-text-encorder-for-flux-klein-4b", "split_files/text_encoders/qwen_3_4b.safetensors", "text_encoders"),
    ("Comfy-Org/vae-text-encorder-for-flux-klein-4b", "split_files/vae/flux2-vae.safetensors", "vae"),
]

for repo, fname, subdir in WANT:
    if fname is None:
        files = list_repo_files(repo)
        cands = [f for f in files if f.endswith(".safetensors")]
        print(repo, "->", cands)
        fname = next(f for f in cands if "4b-fp8" in f.lower() or "klein-4b" in f.lower().replace("_", "-"))
    dest = MODELS / subdir / Path(fname).name
    try:
        from huggingface_hub import get_hf_file_metadata
        exp = get_hf_file_metadata(f"https://huggingface.co/{repo}/resolve/main/{fname}").size
    except Exception:
        exp = None
    if dest.exists() and (exp is None or dest.stat().st_size == exp):
        print(f"SKIP verified {dest} ({dest.stat().st_size / 1073741824:.2f} GB)")
        continue
    print(f"GET {repo}/{fname} ...")
    p = hf_hub_download(repo, fname, local_dir=str(MODELS / subdir), local_dir_use_symlinks=False)
    src = Path(p)
    final = MODELS / subdir / src.name
    if src != final:
        final.parent.mkdir(parents=True, exist_ok=True)
        src.replace(final)
    print("saved", final, f"({final.stat().st_size / 1073741824:.2f} GB)")
print("DONE")
