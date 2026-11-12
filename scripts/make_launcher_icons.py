#!/usr/bin/env python3
"""Generate distinct launcher icons for the project shortcuts.

Each project gets a colored rounded-square icon with a white glyph so the
10 launchers on the Desktop are easy to tell apart at a glance.

Output: azazael/scripts/icons/<Name>.ico (multi-size: 256/64/48/32/16)
"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "icons")
os.makedirs(OUT, exist_ok=True)

# name -> (background RGB, glyph text, accent RGB for a small dot/bar)
PROJECTS = [
    ("EveUnleashed_Selector", (124, 58, 237), "E", (167, 139, 250)),
    ("LlamaServer",           (13, 148, 136), "L", (45, 212, 191)),
    ("PAMS",                  (59, 91, 219), "P", (96, 165, 250)),
    ("PerformanceMonitor",    (22, 163, 74),  "M", (74, 222, 128)),
    ("Ultimate_Agent_Selector", (234, 88, 12), "A", (251, 146, 60)),
    ("WSL_CrossExplorer",     (8, 145, 178),  "W", (34, 211, 238)),
    ("Azazael_MetaCluster",   (225, 29, 72),  "M", (251, 113, 133)),
    ("Tigger_CLI",            (217, 119, 6),  "T", (252, 211, 77)),
    ("G_EveUnleashed",        (147, 51, 234), "G", (192, 132, 252)),
    ("AOA_Selector",          (180, 83, 9),   "A", (251, 191, 36)),
]

SIZE = 256


def _font(size):
    for cand in (
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if os.path.exists(cand):
            try:
                return ImageFont.truetype(cand, size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_icon(bg, glyph, accent):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded square background with a subtle vertical gradient
    for y in range(SIZE):
        t = y / SIZE
        r = int(bg[0] + (bg[0] * 0.25 - bg[0]) * t)
        g = int(bg[1] + (bg[1] * 0.25 - bg[1]) * t)
        b = int(bg[2] + (bg[2] * 0.25 - bg[2]) * t)
        d.line([(16, y), (SIZE - 16, y)], fill=(r, g, b, 255))
    # rounded mask
    mask = Image.new("L", (SIZE, SIZE), 0)
    dm = ImageDraw.Draw(mask)
    dm.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=48, fill=255)
    img.putalpha(mask)
    # accent bar at the bottom
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([48, SIZE - 64, SIZE - 48, SIZE - 40], radius=12, fill=accent)
    # glyph (centered)
    font = _font(150)
    d = ImageDraw.Draw(img)
    bbox = d.textbbox((0, 0), glyph, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (SIZE - tw) / 2 - bbox[0]
    y = (SIZE - th) / 2 - bbox[1] - 20
    d.text((x, y), glyph, font=font, fill=(255, 255, 255, 255))
    return img


def main():
    for name, bg, glyph, accent in PROJECTS:
        img = make_icon(bg, glyph, accent)
        out = os.path.join(OUT, name + ".ico")
        # multi-size icon
        sizes = []
        for s in (256, 64, 48, 32, 16):
            sizes.append(img.resize((s, s), Image.LANCZOS))
        sizes[0].save(out, format="ICO", sizes=[(s, s) for s in (256, 64, 48, 32, 16)],
                      append_images=sizes[1:])
        print(f"wrote {out}")
    print(f"done: {len(PROJECTS)} icons in {OUT}")


if __name__ == "__main__":
    main()
