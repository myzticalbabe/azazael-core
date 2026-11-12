#!/usr/bin/env python
# coding: utf-8
"""dev_stack_census -- what deep-learning stack is installed, per environment.

WHY
`domain_trees.py` counts WHAT IS IN A DIRECTORY. This asks a different question:
which libraries are installed, at what version, in which environment. The two
answer different things and neither substitutes for the other -- a tree built
from a directory listing shows `tree/site_packages/torch` without saying that
Windows has torch 2.x+xpu while Ubuntu has a different build.

HOW IT READS A STACK
No interpreter is executed in the target environment. A package's identity and
version are read from its `*.dist-info/METADATA` (or `*.egg-info/PKG-INFO`),
which is what pip itself consults. That makes the census work on a STOPPED WSL
distro and on a venv built for a different Python version -- executing
`import torch` in each environment would be slower, need the distro running,
and would import broken stacks to find out they are broken.

OUTPUT
  ~/yggdrasil_forest/dev_stack_census.json   -- full record
  stdout                                     -- one table per environment

USAGE
  python tools/dev_stack_census.py
  python tools/dev_stack_census.py --json-only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

HOME = Path.home()
OUT = HOME / "yggdrasil_forest" / "dev_stack_census.json"

#: Package families that make an environment a *deep-learning* environment.
#: Names are matched case-insensitively against the distribution name, so
#: "intel-extension-for-pytorch" and "intel_extension_for_pytorch" both hit.
DL_FAMILIES: Dict[str, List[str]] = {
    "xpu/oneapi": ["intel-extension-for-pytorch", "intel-extension-for-tensorflow",
                   "ipex", "intel-openmp", "mkl", "mkl-dpcpp", "dpctl", "dpnp",
                   "oneccl-bindings", "intel-cmplr", "intel-sycl-rt", "impi-rt",
                   "intel-extension-for-openxla", "xpu"],
    "openvino": ["openvino", "openvino-genai", "openvino-tokenizers",
                 "openvino-telemetry", "nncf", "optimum-intel", "optimum"],
    "torch": ["torch", "torchvision", "torchaudio", "pytorch-triton", "triton",
              "torch-xpu", "pytorch-lightning", "lightning", "torchmetrics"],
    "tf/keras": ["tensorflow", "keras", "tf-keras", "tensorboard", "tensorflow-text"],
    "jax": ["jax", "jaxlib", "flax", "optax"],
    "onnx": ["onnx", "onnxruntime", "onnxruntime-openvino", "onnxruntime-gpu",
             "onnxruntime-directml", "onnxsim", "onnxconverter-common"],
    "hf": ["transformers", "tokenizers", "datasets", "accelerate", "diffusers",
           "peft", "trl", "safetensors", "huggingface-hub", "sentence-transformers",
           "sentencepiece", "bitsandbytes", "deepspeed", "timm", "einops",
           "hqq", "awq", "autoawq", "gptqmodel", "exllamav2"],
    "serving": ["vllm", "sglang", "llama-cpp-python", "llama-cpp", "text-generation",
                "mlflow", "wandb", "ray", "bentoml", "tritonclient"],
    "classic-ml": ["scikit-learn", "scipy", "xgboost", "lightgbm", "catboost",
                   "statsmodels", "shap", "dalex", "imbalanced-learn"],
    "rl": ["gymnasium", "stable-baselines3", "pettingzoo", "gym", "torchrl"],
    "audio/vision": ["librosa", "soundfile", "opencv-python", "opencv-python-headless",
                     "pillow", "scikit-image", "ffmpeg-python", "av"],
    "numerics": ["numpy", "numba", "pyarrow", "pandas", "polars", "cupy",
                 "pyopencl", "llvmlite"],
}

#: Where an environment keeps its stacks. Absolute, environment-prefixed.
ENV_ROOTS: Dict[str, List[str]] = {
    "windows": [
        r"C:/Users/clint/AppData/Local/Programs/Python/Python314/Lib/site-packages",
        r"C:/Azazael/.venvs/py315t/Lib/site-packages",
        r"C:/Azazael/.venvs/py315/Lib/site-packages",
        r"C:/Azazael/.venvs/py314m/Lib/site-packages",
        r"C:/Azazael/.venvs/build315t/Lib/site-packages",
        r"C:/Azazael/.venvs/bccpipe/Lib/site-packages",
        r"C:/Users/clint/AppData/Local/Programs/AI Playground/resources/ComfyUI/.venv/Lib/site-packages",
    ],
    "ubuntu": [
        r"//wsl.localhost/Ubuntu-26.04/root/py315t/lib/python3.15t/site-packages",
        r"//wsl.localhost/Ubuntu-26.04/root/venv-interpreter/lib/python3.14t/site-packages",
        r"//wsl.localhost/Ubuntu-26.04/root/abox-venv/lib/python3.14t/site-packages",
        r"//wsl.localhost/Ubuntu-26.04/root/.local/lib/python3.14/site-packages",
        r"//wsl.localhost/Ubuntu-26.04/root/venv-314-gil/lib/python3.14/site-packages",
        r"//wsl.localhost/Ubuntu-26.04/root/open-interpreter/venv/lib/python3.14/site-packages",
        r"//wsl.localhost/Ubuntu-26.04/usr/lib/python3/dist-packages",
    ],
    "kali": [
        r"//wsl.localhost/kali-linux/root/.local/lib/python3.14t/site-packages",
        r"//wsl.localhost/kali-linux/root/osint-tools/.venv/lib/python3.13/site-packages",
        r"//wsl.localhost/kali-linux/root/osint_venv/lib/python3.13/site-packages",
        r"//wsl.localhost/kali-linux/root/openwebui-venv/lib/python3.12/site-packages",
        r"//wsl.localhost/kali-linux/usr/lib/python3/dist-packages",
    ],
}

_NAME_RE = re.compile(r"^Name:\s*(.+)$", re.M)
_VER_RE = re.compile(r"^Version:\s*(.+)$", re.M)


def root_label(root: str) -> str:
    """Human label for a site-packages dir.

    `Path(root).name` is "site-packages" for every root, which made the first
    census merge three Windows venvs under one name and report torch's family
    without saying WHICH venv held it. The venv is the parent of `lib/`.
    """
    p = Path(root)
    parts = list(p.parts)
    for marker in ("lib", "Lib"):
        if marker in parts:
            i = parts.index(marker)
            if i > 0:
                return f"{parts[i - 1]}/{marker}"
    if p.name in ("site-packages", "dist-packages") and len(parts) > 2:
        return f"{parts[-3]}/{parts[-2]}"
    return p.name or root


#: Distribution name -> import name, where the two differ in a way no rule
#: derives. Only names whose guess would be WRONG belong here.
IMPORT_ALIASES = {
    "scikit-learn": "sklearn", "pillow": "PIL", "opencv-python": "cv2",
    "opencv-python-headless": "cv2", "pyyaml": "yaml", "python-dateutil": "dateutil",
    "beautifulsoup4": "bs4", "protobuf": "google", "sentencepiece": "sentencepiece",
    "intel-extension-for-pytorch": "intel_extension_for_pytorch",
    "intel-extension-for-tensorflow": "intel_extension_for_tensorflow",
    "onnxruntime-openvino": "onnxruntime", "torch-xpu": "torch",
    "mlflow-skinny": "mlflow", "triton-xpu": "triton", "ipex-llm": "ipex_llm",
    "openvino-genai": "openvino_genai", "openvino-tokenizers": "openvino_tokenizers",
    "imbalanced-learn": "imblearn", "ffmpeg-python": "ffmpeg",
    "stable-baselines3": "stable_baselines3", "huggingface-hub": "huggingface_hub",
    "sentence-transformers": "sentence_transformers", "scikit-image": "skimage",
}


def top_levels(dist_info: Path, dist_name: str = "") -> List[str]:
    """Import dirs a distribution provides.

    Read from `top_level.txt` when the wheel shipped one. Many wheels do not
    (measured 2026-10-31: torch and scikit-learn in three of these venvs had no
    top_level.txt), and returning [] there made whole limbs report 0 modules
    while listing their packages -- a silent zero, which reads as "installed but
    empty". The fallback is the normalised distribution name plus a small alias
    table for the cases where that guess is wrong (scikit-learn -> sklearn,
    Pillow -> PIL, opencv-python -> cv2).
    """
    f = dist_info / "top_level.txt"
    if f.exists():
        try:
            names = [n.strip() for n in
                     f.read_text(encoding="utf-8", errors="replace").splitlines()]
        except OSError:
            names = []
        names = [n for n in names if n and not n.startswith("_")]
        if names:
            return names
    low = (dist_name or dist_info.name).lower()
    for suffix in (".dist-info", ".egg-info"):
        if low.endswith(suffix):
            low = low[: -len(suffix)]
    if low in IMPORT_ALIASES:
        return [IMPORT_ALIASES[low]]
    base = low.rsplit("-", 1)[0] if re.search(r"-\d", low) else low
    return [base.replace("-", "_")] if base else []


def _meta_of(dist_info: Path) -> tuple[str, str]:
    meta = dist_info / "METADATA"
    if not meta.exists():
        meta = dist_info / "PKG-INFO"
    try:
        text = meta.read_text(encoding="utf-8", errors="replace")[:8000]
    except OSError:
        return dist_info.name, ""
    name = _NAME_RE.search(text)
    ver = _VER_RE.search(text)
    if name:
        return name.group(1).strip(), (ver.group(1).strip() if ver else "")
    # No readable METADATA: parse the directory name, which is
    # "<name>-<version>.dist-info". Splitting on the LAST hyphen alone turns
    # "scipy-1.18.0.dist-info" into name "scipy-1.18.0.dist-info" when the
    # suffix strip is missed, which then showed up as a package named
    # "scipy-1.18.0.dist-info" in the census.
    stem = dist_info.name
    for suffix in (".dist-info", ".egg-info"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    m = re.match(r"^(.+?)-(\d[\w.+!]*)$", stem, re.ASCII)  # raw: \d in a plain string warns on 3.12+
    if m:
        return m.group(1), m.group(2)
    return stem, ""


def census_root(root: str) -> Dict[str, Any]:
    p = Path(root)
    label = root_label(root)
    rec: Dict[str, Any] = {"path": root, "label": label, "exists": p.is_dir(),
                           "packages": {}, "count": 0, "families": {}}
    if not rec["exists"]:
        return rec
    try:
        entries = list(p.iterdir())
    except OSError as exc:
        rec["error"] = str(exc)
        return rec
    for entry in entries:
        if not entry.name.endswith((".dist-info", ".egg-info")):
            continue
        name, ver = _meta_of(entry)
        if not name:
            continue
        rec["packages"][name] = {"version": ver, "root": label,
                                 "top_level": top_levels(entry, name)}
    rec["count"] = len(rec["packages"])
    low = {k.lower(): v for k, v in rec["packages"].items()}
    for fam, needles in DL_FAMILIES.items():
        hits = {n: low[n] for n in needles if n in low}
        # prefix match catches build variants: "torch-xpu-ops", "openvino-dev"
        for pkg in low:
            for n in needles:
                if pkg != n and (pkg.startswith(n + "-") or pkg.startswith(n + "_")):
                    hits.setdefault(pkg, low[pkg])
        if hits:
            rec["families"][fam] = dict(sorted(hits.items()))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json-only", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    result: Dict[str, Any] = {"schema": "azazael.dev_stack_census/1",
                              "environments": {}}
    for env, roots in ENV_ROOTS.items():
        per = {"roots": {}, "families": {}, "packages": {}}
        for root in roots:
            r = census_root(root)
            per["roots"][root] = {"exists": r["exists"], "count": r["count"],
                                  "label": r["label"]}
            if r.get("error"):
                per["roots"][root]["error"] = r["error"]
            for fam, pkgs in r["families"].items():
                bucket = per["families"].setdefault(fam, {})
                for name, meta in pkgs.items():
                    bucket[f"{name}@{r['label']}"] = meta["version"]
            for name, meta in r["packages"].items():
                # Keyed by distribution AND root: the same distribution appears in
                # several environments at different versions (measured: torch is
                # 2.11.0+xpu in one Windows venv and 2.16.0+cpu in another), so a
                # bare-name key silently keeps whichever root was read first and
                # reports the wrong build for the environment.
                per["packages"][f"{name}@{meta['root']}"] = dict(meta, root_path=root)
        per["package_total"] = len({k.split("@", 1)[0] for k in per["packages"]})
        result["environments"][env] = per

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    if args.json_only:
        print(f"wrote {out}")
        return 0

    for env, per in result["environments"].items():
        live = [f"{v.get('label', Path(p).name)}={v['count']}" for p, v in per["roots"].items() if v["exists"]]
        print(f"\n=== {env}   {per['package_total']} distributions   "
              f"({len(live)} roots: {', '.join(live) if live else 'none reachable'})")
        if not per["families"]:
            print("  no deep-learning family found")
            continue
        for fam, pkgs in sorted(per["families"].items()):
            shown = ", ".join(f"{k.split('@')[0]} {v}" for k, v in sorted(pkgs.items()))
            print(f"  {fam:12} {shown[:300]}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
