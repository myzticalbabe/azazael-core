#!/usr/bin/env python3

"""
Azazael 4-Quadrant Scrolling Video - Vibrant & Whimsical

"""



import os

from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from typing import List
import subprocess

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here




WIDTH = 1920

HEIGHT = 1080

FPS = 30

DURATION = 90



OUTPUT_DIR = Path.home() / "Desktop" / "CODEX"

FRAME_DIR = os.path.join(OUTPUT_DIR, "frames_quad")

VIDEO_OUTPUT = os.path.join(OUTPUT_DIR, "Azazael_Reference_Video.mp4")



QUAD1 = [

    ("🚀", "Azazael", ""),

    (">>", "DISTRIBUTED", "💻"),

    ("[-]", "SELF-OPTIMIZING", "🛠"),

    ("💚", "SELF-HEALING", "🔧"),

    ("🌟", "ECOSYSTEM", "✨"),

    ("🎯", "TIER 1", "🎯"),

    ("💻", "SYSTEMZ", "📊"),

    ("🔄", "PROCESSZ", ""),

    ("💿", "DISKZ", "💾"),

    ("", "NETWORXZ", "🕸"),

    ("", "CLOCKZ", ""),

    ("", "WORMZ", ""),

]



QUAD2 = [

    ("🎨", "TIER 2", "🔗"),

    ("📚", "BIBL", "📖"),

    ("🖥", "SYSTEM", "🛡"),

    ("", "NETWORK", "🔒"),

    ("📊", "ANALYSIS", "🔎"),

    ("[OK]", "VALIDATION", "🧪"),

    ("💚", "HEALTH", ""),

    ("🎭", "TIER 3", "🧬"),

    ("🧬", "METAZ", "📦"),

    ("🕸", "DEPENDZ", "🗺"),

    ("[OK]", "VALIDZ", ""),

    ("👻", "DEADZ", "💨"),

]



QUAD3 = [

    ("📈", "METRICZ", "🧮"),

    ("", "ERRORZ", "🎯"),

    ("📋", "LOGZ", "📜"),

    ("", "CHANGEZ", ""),

    ("✂", "TRIMZ", "🧹"),

    (">>", "OPTIMIZ", "🚀"),

    ("", "HEALTHZ", "🩺"),

    ("[-]", "TIER 4", "💜"),

    ("🤖", "ML OPTIM", "🧬"),

    ("💾", "PERSISTENCE", "🗄"),

    ("🎲", "EXECUTION", "🎮"),

    ("🔄", "STRATEGIES", "🎯"),

]



QUAD4 = [

    ("🥇", "ROUND ROBIN", ""),

    (">>", "PERFORMANCE", "🥇"),

    ("⚖", "RESOURCE", "⚖"),

    ("🌪", "RANDOMIZED", "🎲"),

    ("🔮", "ML-OPTIMIZED", "[-]"),

    ("🎯", "TIER 5", ">>"),

    ("🎼", "TERENCE", "🎹"),

    ("📚", "50K+ PATTERNS", "[-]"),

    ("🎨", "HORUS", "🎭"),

    ("🎬", "24 PROTOCOLS", "🌈"),

    ("🛠", "SUPPORT", "🛠"),

    ("⌨", "SHORTY", "📌"),

]



QUAD5 = [

    ("📦", "TARBABY", ""),

    ("🌈", "PERSEPHONE", ""),

    ("🧮", "POINDEXTER", "🧮"),

    ("📖", "BIBL", "📚"),

    ("🔄", "PIPELINE", "🔄"),

    ("", "DISCOVERY", "🔎"),

    ("", "ANALYSIS", "🔬"),

    (">>", "OPTIMIZE", "🧬"),

    ("[OK]", "VALIDATE", "🧪"),

    ("🚀", "DEPLOY", "💨"),

    ("🔄", "FEEDBACK", ""),

    ("📡", "API", "🔌"),

]



QUAD6 = [

    ("🖥", "SYSTEM", ""),

    ("🧬", "METAZ", ""),

    ("[-]", "HYDRAMETA", ">>"),

    ("🎯", "TERENCE", "🎯"),

    ("📊", "HORUS", "📈"),

    ("💾", "DATABASES", "💾"),

    ("🟦", "SQLITE", "🟥"),

    ("🟨", "POSTGRES", "🟩"),

    ("🟪", "INFLUXDB", "📊"),

    ("🎮", "EXECUTION", "🎮"),

    (">>", "PARALLEL", "🧵"),

    ("", "CROSS-PLATFORM", "📈"),

]





def get_vibrant_color(hue, frame, s=100, l=60):

    return f"hsl({(hue + frame * 0.5) % 360}, {s}%, {l}%)"





def create_video():

    os.makedirs(FRAME_DIR, exist_ok=True)

    print("Creating 4-quadrant whimsical video...")



    quads = [QUAD1, QUAD2, QUAD3, QUAD4, QUAD5, QUAD6]

    quad_hues = [0, 60, 120, 180, 240, 300]



    panel_w = WIDTH // 2 - 8

    panel_h = HEIGHT // 2 - 8



    for frame_num in range(DURATION * FPS):

        frame = Image.new("RGB", (WIDTH, HEIGHT), color="#050510")

        draw = ImageDraw.Draw(frame)



        try:

            font_huge = ImageFont.truetype("arialbd.ttf", 88)

            font_large = ImageFont.truetype("arialbd.ttf", 64)

            font_medium = ImageFont.truetype("arialbd.ttf", 48)

        except Exception as exc:

            note_failure("make_video_quad:L263", exc)
            font_huge = ImageFont.load_default()

            font_large = ImageFont.load_default()

            font_medium = ImageFont.load_default()



        for q_idx, quad in enumerate(quads):

            if q_idx >= 4:

                continue



            hue = quad_hues[q_idx]



            qx = q_idx % 2

            qy = q_idx // 2



            px = qx * (panel_w + 16)

            py = qy * (panel_h + 16)



            draw.rectangle(

                [(px, py), (px + panel_w, py + panel_h)],

                fill=get_vibrant_color(hue, frame_num, 25, 8),

                outline=get_vibrant_color(hue, frame_num, 90, 55),

                width=4,

            )



            progress = (frame_num * 0.15 + q_idx * 0.25) % 1

            total_items = len(quad)

            item_h = panel_h // 3

            scroll_offset = progress * (total_items * item_h + panel_h) - panel_h



            for i, (emoji, word1, word2) in enumerate(quad):

                y = py + (i * item_h) - scroll_offset



                if y < py - item_h or y > py + panel_h + item_h:

                    continue



                emoji_color = get_vibrant_color(hue + 30, frame_num, 100, 70)

                text_color = get_vibrant_color(hue, frame_num, 95, 65)



                bbox = draw.textbbox((0, 0), emoji, font=font_huge)

                draw.text((px + 30, y + 5), emoji, font=font_huge, fill=emoji_color)



                bbox = draw.textbbox((0, 0), word1, font=font_large)

                draw.text((px + 120, y), word1, font=font_large, fill=text_color)



                if word2:

                    bbox = draw.textbbox((0, 0), word2, font=font_medium)

                    draw.text(

                        (px + 130, y + 55), word2, font=font_medium, fill=text_color

                    )



        if frame_num % 300 == 0:

            print(f"Frame {frame_num}/{DURATION * FPS}")



        frame.save(os.path.join(FRAME_DIR, f"frame_{frame_num:06d}.png"), "PNG")



    print("Encoding video...")






    ffmpeg_cmd = [

        "ffmpeg",

        "-y",

        "-framerate",

        str(FPS),

        "-i",

        os.path.join(FRAME_DIR, "frame_%06d.png"),

        "-c:v",

        "libx264",

        "-preset",

        "fast",

        "-crf",

        "18",

        "-pix_fmt",

        "yuv420p",

        VIDEO_OUTPUT,

    ]



    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)

    if result.returncode == 0:

        print(f"Video: {VIDEO_OUTPUT}")

    else:

        print(f"Error: {result.stderr}")



    import shutil



    shutil.rmtree(FRAME_DIR)

    print("Done!")





if __name__ == "__main__":

    create_video()

# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
