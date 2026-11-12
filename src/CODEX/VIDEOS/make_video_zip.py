#!/usr/bin/env python3

"""
Azazael Video - Big Keywords Zipper Pattern

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

FRAME_DIR = os.path.join(OUTPUT_DIR, "frames_zip")

VIDEO_OUTPUT = os.path.join(OUTPUT_DIR, "Azazael_Reference_Video.mp4")



PAIRS = [

    ("Azazael", "ECOSYSTEM"),

    ("DISTRIBUTED", "COMPUTING"),

    ("SELF-OPTIMIZING", "SELF-HEALING"),

    ("TIER 1", "CORE WORMS"),

    ("SYSTEMZ", "PROCESSZ"),

    ("DISKZ", "NETWORXZ"),

    ("CLOCKZ", "WORMZ"),

    ("", ""),

    ("TIER 2", "CLUSTERS"),

    ("BIBL", "SYSTEM"),

    ("NETWORK", "SECURITY"),

    ("ANALYSIS", "VALIDATION"),

    ("HEALTH", ""),

    ("", ""),

    ("TIER 3", "METAWORMS"),

    ("METAZ", "DEPENDZ"),

    ("VALIDZ", "DEADZ"),

    ("METRICZ", "ERRORZ"),

    ("LOGZ", "CHANGEZ"),

    ("TRIMZ", "OPTIMIZ"),

    ("HEALTHZ", ""),

    ("", ""),

    ("TIER 4", "HYDRAMETA"),

    ("ML OPTIMIZATION", "GENETIC"),

    ("PERSISTENCE", ""),

    ("ROUND ROBIN", "PERFORMANCE"),

    ("RESOURCE-AWARE", "RANDOMIZED"),

    ("ML-OPTIMIZED", ""),

    ("", ""),

    ("TIER 5", "COORDINATION"),

    ("TERENCE", "HORUS"),

    ("PATTERN DB", "50K+"),

    ("DEEP Q-LEARNING", "ANIMATION"),

    ("PROTOCOLS", "WEBSOCKET"),

    ("", ""),

    ("SUPPORT", "SYSTEMS"),

    ("SHORTY", "TARBABY"),

    ("PERSEPHONE", "POINDEXTER"),

    ("BIBL", ""),

    ("", ""),

    ("6-STAGE", "PIPELINE"),

    ("DISCOVERY", "ANALYSIS"),

    ("OPTIMIZE", "VALIDATE"),

    ("DEPLOY", "FEEDBACK"),

    ("", ""),

    ("API", "REFERENCE"),

    ("SYSTEM", "METAZ"),

    ("HYDRAMETA", "TERENCE"),

    ("HORUS", ""),

    ("", ""),

    ("DATABASE", "ADAPTERS"),

    ("SQLITE", "REDIS"),

    ("POSTGRESQL", "ELASTIC"),

    ("INFLUXDB", ""),

    ("", ""),

    ("EXECUTION", "MODES"),

    ("PARALLEL", "THREAD POOL"),

    ("GENETIC", "ALGORITHMS"),

    ("CROSS-PLATFORM", "AUTO-SCALING"),

    ("", ""),

    ("Azazael", "v1.0.0"),

    ("DISTRIBUTED", "COMPUTING"),

    ("SELF-OPTIMIZING", "SELF-HEALING"),

]





def get_pulse_color(base_hue, frame, lightness=55):

    hue = (base_hue + frame * 0.3) % 360

    return f"hsl({hue}, 100%, {lightness}%)"





def create_video():

    os.makedirs(FRAME_DIR, exist_ok=True)

    print("Creating big keyword zipper video...")






    for frame_num in range(DURATION * FPS):

        frame = Image.new("RGB", (WIDTH, HEIGHT), color="#0a0a1a")

        draw = ImageDraw.Draw(frame)



        try:

            font_large = ImageFont.truetype("arialbd.ttf", 96)

            ImageFont.truetype("arialbd.ttf", 72)

            ImageFont.truetype("arialbd.ttf", 56)

        except Exception as exc:

            note_failure("make_video_zip:L208", exc)
            font_large = ImageFont.load_default()

            ImageFont.load_default()

            ImageFont.load_default()



        progress = frame_num / (DURATION * FPS)



        item_height = HEIGHT // 5

        total_height = len(PAIRS) * item_height

        offset = int(progress * (total_height + HEIGHT)) - HEIGHT // 2



        center_y = HEIGHT // 2

        gap_x = 15



        for i, (left_word, right_word) in enumerate(PAIRS):

            y = center_y + (i * item_height) - offset



            if y < -100 or y > HEIGHT + 100:

                continue



            if left_word:

                bbox = draw.textbbox((0, 0), left_word, font=font_large)

                w = bbox[2] - bbox[0]

                x = WIDTH // 2 - gap_x - w



                hue = (i * 20) % 360

                color = get_pulse_color(hue, frame_num)

                draw.text((x, y), left_word, font=font_large, fill=color)



            if right_word:

                x = WIDTH // 2 + gap_x

                bbox = draw.textbbox((0, 0), right_word, font=font_large)

                w = bbox[2] - bbox[0]



                hue = ((i * 20) + 180) % 360

                color = get_pulse_color(hue, frame_num)

                draw.text((x, y), right_word, font=font_large, fill=color)



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

