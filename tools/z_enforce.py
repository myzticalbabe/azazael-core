#!/usr/bin/env python
"""Enforce hard constraints on Z-engineer drafts: ASCII-normalize, append lock
block, verify code points from the FILE (never console), emit Klein workflows
with EMPTY negative (distilled CFG 1.0 ignores negatives; describe-want-only)."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "style_samples"

LOCK_S2 = (
    " Both her arms rest at her sides from shoulder to wrist with clearly defined hands, "
    "her arms outlined in lead and separated from her gown. He has short silver-grey hair "
    "under a black spade crown with NO red hair anywhere on him. Her very long red-orange "
    "hair flows past her shoulders beneath a diamond tiara. His black-and-silver armor shows "
    "only black spade pips, her white gown shows only red diamond pips, suits never mix. "
    "Glyphs that must appear: spade (black, inverted heart with a short stem) ONLY on him; "
    "diamond (red, rotated square) ONLY on her. No letters or text anywhere."
)

LOCK_S5 = (
    " Both her arms rest on the chair arms from shoulder to wrist with clearly defined hands, "
    "separated from her gown by lead lines; his arms rest on his chair arms with defined "
    "gauntlet hands. He has short silver-grey hair under a black spade crown with NO red hair "
    "anywhere on him. Her very long red-orange hair flows past her shoulders beneath a diamond "
    "tiara. His black-and-silver armor shows only black spade pips, her white gown shows only "
    "red diamond pips, suits never mix. Glyphs that must appear: spade (black, inverted heart "
    "with a short stem) ONLY on him; diamond (red, rotated square) ONLY on her. No letters or "
    "text anywhere."
)

JOBS = {
    "S2": (111, "KLEIN4B_S2_Z_glass", LOCK_S2),
    "S5": (113, "KLEIN4B_S5_Z_glass", LOCK_S5),
}

REPLACEMENTS = {
    "\u2014": "-",
    "\u2013": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
}


def normalize(text):
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    text.encode("ascii")  # fail loudly if anything non-ASCII remains
    return " ".join(text.split())


def workflow(prefix, prompt, seed):
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "flux-2-klein-4b-fp8.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "flux2"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "4": {"class_type": "EmptySD3LatentImage",
              "inputs": {"batch_size": 1, "height": 1024, "width": 1024}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": ""}},
        "7": {"class_type": "KSampler",
              "inputs": {"cfg": 1.0, "denoise": 1.0, "latent_image": ["4", 0], "model": ["1", 0],
                         "negative": ["6", 0], "positive": ["5", 0], "sampler_name": "euler",
                         "scheduler": "simple", "seed": seed, "steps": 4}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": prefix, "images": ["8", 0]}},
    }


def main():
    for key, (seed, prefix, lock) in JOBS.items():
        raw = (SAMPLES / f"ZRAW_{key}_draft.txt").read_text(encoding="utf-8")
        final = normalize(raw) + lock
        (SAMPLES / f"ZF_{key}_final.txt").write_text(final, encoding="utf-8")
        (SAMPLES / f"{prefix}.json").write_text(
            json.dumps(workflow(prefix, final, seed)), encoding="utf-8")
        print(f"{key}: {len(final.split())} words, ascii-ok, -> {prefix}.json")


if __name__ == "__main__":
    main()
