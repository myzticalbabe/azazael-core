#!/usr/bin/env python
"""Build 8 single-style sample workflows (same throne scene, seed 114)."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTDIR = HERE / "style_samples"
OUTDIR.mkdir(exist_ok=True)

SUBJECT = ("a queen of diamonds with red-orange strawberry-blonde hair, diamond tiara, "
           "white gown with ONLY red diamond pips and letter Q, sitting on the lap of "
           "a king of spades with short silver-grey hair, black spade crown, "
           "black-and-silver armor with ONLY black spade pips and letter K and NO red hair on him, "
           "grand throne, her gown only diamonds, his armor only spades, "
           "suits never mix, chandeliers")

NEG_BASE = ("photorealistic, real human, photograph, realistic skin, live action, mannequin, "
            "plastic skin, mixed suits, hearts, clubs, garbled text, wrong letters, extra pips")
NEG_NOPIXAR = NEG_BASE + ", 3d render, pixar style"

STYLES = [
    ("STYLE01_pixar",
     "3D animated film still, Pixar Disney style, subsurface scattering, big expressive eyes, warm golden-hour light, rich textures",
     NEG_BASE + ", flat paper card, 2D playing card"),
    ("STYLE02_disney2d",
     "classic 2D hand-drawn Disney animation cel, clean ink outlines, cel shading, gouache background, warm light",
     NEG_NOPIXAR),
    ("STYLE03_clay",
     "stop-motion claymation, visible thumbprint texture, matte finish, squat charming proportions, miniature set lighting",
     NEG_NOPIXAR),
    ("STYLE04_watercolor",
     "classic watercolor storybook illustration, wet-on-wet washes, visible pencil lines, muted pastel palette",
     NEG_NOPIXAR),
    ("STYLE05_crochet",
     "crochet amigurumi plush characters, visible yarn stitches, soft chunky rounded shapes",
     NEG_NOPIXAR),
    ("STYLE06_stainedglass",
     "stained glass window art, lead came lines, glowing jewel tones, backlit",
     NEG_NOPIXAR),
    ("STYLE07_ghibli",
     "ghibli style anime film still, painterly detailed background, soft light",
     NEG_NOPIXAR + ", 2D playing card"),
    ("STYLE08_diorama",
     "layered paper cutout diorama, visible paper layers, soft shadows between layers",
     NEG_NOPIXAR),
]


def workflow(prefix, style, negative):
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "flux1-schnell-fp8-e4m3fn.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader",
              "inputs": {"clip_name1": "t5xxl_fp8_e4m3fn_scaled.safetensors",
                         "clip_name2": "clip_l.safetensors", "type": "flux"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "flux-vae-bf16.safetensors"}},
        "4": {"class_type": "EmptySD3LatentImage",
              "inputs": {"batch_size": 1, "height": 1024, "width": 1024}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": f"{style}: {SUBJECT}"}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": negative}},
        "7": {"class_type": "KSampler",
              "inputs": {"cfg": 1.0, "denoise": 1.0, "latent_image": ["4", 0], "model": ["1", 0],
                         "negative": ["6", 0], "positive": ["5", 0], "sampler_name": "euler",
                         "scheduler": "simple", "seed": 114, "steps": 4}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": prefix, "images": ["8", 0]}},
    }


for prefix, style, neg in STYLES:
    (OUTDIR / f"{prefix}.json").write_text(json.dumps(workflow(prefix, style, neg)), encoding="utf-8")
print(f"wrote {len(STYLES)} workflows to {OUTDIR}")
