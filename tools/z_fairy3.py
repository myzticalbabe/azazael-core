#!/usr/bin/env python
"""Z-engineer maximum prompt for fairy v3: mushrooms galore + forest creatures."""
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = "qwen3-4b-z-image-engineer-v4"

BRIEF = (
    "Write a single-paragraph maximally rich ornate image prompt (200-230 words, ASCII only, "
    "natural language, no weight syntax) for a fanciful fairy portrait: a diamond queen fairy "
    "with very long flowing red-orange hair, diamond tiara, diamond necklace and ruby bracelets, "
    "translucent butterfly wings veined with ruby diamonds, white gown with gold filigree and red "
    "diamond gems, both arms graceful with delicate hands, one hand lifted, standing in a moonlit "
    "fairy glade crowded with MANY giant glowing mushrooms in varied color combinations (teal "
    "caps, magenta caps, amber caps, violet caps, all with white spots and glowing gills), "
    "fantastical forest creatures around her: a baby moon-dragon curled asleep on a mushroom cap, "
    "a starry-feathered owl watching from a branch, a tiny frog herald holding a lantern, "
    "plus swirling luminous butterflies, drifting spores, ferns, lily bells, fireflies, mist, "
    "hyper-detailed fantasy illustration, radiant glow, deep teal night with golden accents."
)

LOCK = (
    " Red diamond marks ONLY on her; no letters or text anywhere. Both her arms graceful and "
    "fully visible with delicate hands."
)

REPLACEMENTS = {"\u2014": "-", "\u2013": "-", "\u2018": "'",
                "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "..."}


def complete(user_text):
    body = json.dumps(
        {"model": MODEL,
         "messages": [{"role": "system",
                       "content": "You write image prompts for a FLUX image model. Reply with ONLY the prompt paragraph."},
                      {"role": "user", "content": user_text}],
         "temperature": 0.8, "max_tokens": 800}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:1234/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"].strip()


def main():
    raw = complete(BRIEF)
    (HERE / "style_samples" / "ZRAW_Fairy3_draft.txt").write_text(raw, encoding="utf-8")
    text = raw
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    text.encode("ascii")
    final = " ".join(text.split()) + LOCK
    (HERE / "style_samples" / "ZF_Fairy3_final.txt").write_text(final, encoding="utf-8")
    wf = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "flux-2-klein-4b-fp8.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "flux2"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "4": {"class_type": "EmptySD3LatentImage",
              "inputs": {"batch_size": 1, "height": 1024, "width": 1024}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": final}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
        "7": {"class_type": "KSampler",
              "inputs": {"cfg": 1.0, "denoise": 1.0, "latent_image": ["4", 0], "model": ["1", 0],
                         "negative": ["6", 0], "positive": ["5", 0], "sampler_name": "euler",
                         "scheduler": "simple", "seed": 120, "steps": 4}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "KLEIN4B_Fairy_Z3", "images": ["8", 0]}},
    }
    (HERE / "style_samples" / "KLEIN4B_Fairy_Z3.json").write_text(
        json.dumps(wf), encoding="utf-8")
    print(f"{len(final.split())} words, ascii-ok")


if __name__ == "__main__":
    main()
