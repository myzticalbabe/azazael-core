#!/usr/bin/env python3

"""
Azazael Scrolling Video - Interleaved Zipper Double Column

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

DURATION_SECONDS = 100



OUTPUT_DIR = Path.home() / "Desktop" / "CODEX"

FRAME_DIR = os.path.join(OUTPUT_DIR, "frames_zipper")

VIDEO_OUTPUT = os.path.join(OUTPUT_DIR, "Azazael_Reference_Video.mp4")



CONTENT = """



                             🚀 Azazael ECOSYSTEM v2.0 









"""



LEFT_LINES = [

    (

        "",

        "divider",

    ),

    ("                    💜 OVERVIEW 💜", "header"),

    ("        Distributed Computing Framework", "sub"),

    ("    Self-Optimizing | Self-Healing | ML-Driven", "sub"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                       🎯 TIER 1: CORE WORMS 🎯", "header"),

    ("", "empty"),

    ("                    💻 SYSTEMZ", "big"),

    ("                    📊 Resource Monitor", "med"),

    ("                    * CPU * Memory * Disk * Network", "small"),

    ("", "empty"),

    ("                    🔄 PROCESSZ", "big"),

    ("                     Process Manager", "med"),

    ("                    * Tree View * Memory Map", "small"),

    ("                    * SSH * WMI * API", "small"),

    ("", "empty"),

    ("                    💿 DISKZ", "big"),

    ("                    💾 Storage Monitor", "med"),

    ("                    * SMART * I/O Statistics", "small"),

    ("                    * HDD * SSD * NVMe", "small"),

    ("", "empty"),

    ("                     NETWORXZ", "big"),

    ("                    🕸 Network Monitor", "med"),

    ("                    * TCP * UDP * ICMP * HTTP", "small"),

    ("                    * Bandwidth * Latency * QoS", "small"),

    ("", "empty"),

    ("                     CLOCKZ", "big"),

    ("                     Time Service", "med"),

    ("                    * NTP * PTP * Precision: ms-ns", "small"),

    ("", "empty"),

    ("                     WORMZ", "big"),

    ("                     Crawler/Indexer", "med"),

    ("                    * Filesystem * Git * Database", "small"),

    ("                    * All Languages * ElasticSearch", "small"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                    🔗 TIER 2: CLUSTERS 🔗", "header"),

    ("", "empty"),

    ("                    📚 BIBL", "big"),

    ("                     + ✂ TRIMZ", "med"),

    ("", "empty"),

    ("                    🖥 SYSTEM", "big"),

    ("                    💻 + 🔄 + 💿", "med"),

    ("", "empty"),

    ("                     NETWORK", "big"),

    ("                     +  + 💿", "med"),

    ("", "empty"),

    ("                    🔒 SECURITY", "big"),

    ("                    🔄 + ", "med"),

    ("", "empty"),

    ("                    📊 ANALYSIS", "big"),

    ("                    🧬 + 📈 + 🕸", "med"),

    ("", "empty"),

    ("                    [OK] VALIDATION", "big"),

    ("                    [OK] + 👻 + ", "med"),

    ("", "empty"),

    ("                    💚 HEALTH", "big"),

    ("                     + >> + 💻", "med"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                    🧬 TIER 3: METAWORMS 🧬", "header"),

    ("", "empty"),

    ("                    🧬 METAZ", "big"),

    ("                    📦 Metadata Extraction", "med"),

    ("", "empty"),

    ("                    🕸 DEPENDZ", "big"),

    ("                    🕸 Dependency Mapping", "med"),

    ("", "empty"),

    ("                    [OK] VALIDZ", "big"),

    ("                    [OK] Syntax Validation", "med"),

    ("", "empty"),

    ("                    👻 DEADZ", "big"),

    ("                    👻 Dead Code Detection", "med"),

    ("", "empty"),

    ("                    📈 METRICZ", "big"),

    ("                    📊 Code Metrics", "med"),

    ("                    * Cyclomatic * Halstead", "small"),

    ("", "empty"),

    ("                     ERRORZ", "big"),

    ("                     Error Pattern Match", "med"),

    ("", "empty"),

    ("                    📋 LOGZ", "big"),

    ("                    📋 Log Analysis", "med"),

    ("", "empty"),

    ("                    ✂ TRIMZ", "big"),

    ("                    ✂ Content Optimization", "med"),

    ("", "empty"),

    ("                    >> OPTIMIZ", "big"),

    ("                    >> Performance Tuning", "med"),

    ("", "empty"),

    ("                     HEALTHZ", "big"),

    ("                     System Diagnostics", "med"),

]



RIGHT_LINES = [

    (

        "",

        "divider",

    ),

    ("                    >> TIER 5: COORDINATION >>", "header"),

    ("", "empty"),

    ("                    🎯 TERENCE", "big"),

    ("                    🎼 Orchestration", "med"),

    ("                    * 50K+ Patterns * Deep Q-Learning", "small"),

    ("                    * 90% Auto-fix Success", "small"),

    ("", "empty"),

    ("                    📊 HORUS", "big"),

    ("                    🎨 Output Generation", "med"),

    ("                    * 24 Animation Protocols", "small"),

    ("                    * HTML * JSON * GraphML", "small"),

    ("                    * WebSocket Real-time", "small"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                    🛠 SUPPORT SYSTEMS 🛠", "header"),

    ("", "empty"),

    ("                    ⌨ SHORTY", "big"),

    ("                    ⌨ Executable Shortcuts", "med"),

    ("", "empty"),

    ("                    📦 TARBABY", "big"),

    ("                    📦 Binary Builder", "med"),

    ("", "empty"),

    ("                    🌈 PERSEPHONE", "big"),

    ("                    🌈 Cross-Platform Deploy", "med"),

    ("", "empty"),

    ("                    📖 BIBL", "big"),

    ("                    📖 Terminology Crawler", "med"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                    🔄 6-STAGE PIPELINE 🔄", "header"),

    ("", "empty"),

    ("            [ 1. DISCOVERY]   ->   [ 2. ANALYSIS]", "small"),

    ("                Scan & Identify        Metaworm Process", "tiny"),

    ("", "empty"),

    ("            [>> 3. OPTIMIZE]   ->   [[OK] 4. VALIDATE]", "small"),

    ("                Genetic Tuning         Error Checking", "tiny"),

    ("", "empty"),

    ("            [🚀 5. DEPLOY]   ->   [🔄 6. FEEDBACK]", "small"),

    ("                Execution               Persistence", "tiny"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                     ARCHITECTURE ", "header"),

    ("", "empty"),

    ("                    TIER 0:  ⌨ SHORTY  📦 TARBABY", "med"),

    ("", "empty"),

    ("                    TIER 1:  💻 🔄 💿   ", "med"),

    ("", "empty"),

    ("                    TIER 2:  📚 🖥  🔒 📊 [OK] 💚", "med"),

    ("", "empty"),

    ("                    TIER 3:  🧬 🕸 [OK] 👻 📈  📋 ✂ >> ", "small"),

    ("", "empty"),

    ("                    TIER 4:  [-] HYDRAMETA", "med"),

    ("", "empty"),

    ("                    TIER 5:  🎯 TERENCE  📊 HORUS", "med"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                    📡 API REFERENCE 📡", "header"),

    ("", "empty"),

    ("                    🖥 SYSTEM API", "big"),

    ("                    GET /system/status", "small"),

    ("                    GET /system/resources", "small"),

    ("                    GET /system/processes", "small"),

    ("", "empty"),

    ("                    [-] HYDRAMETA API", "big"),

    ("                    POST /hydrameta/optimize", "small"),

    ("                    GET /hydrameta/population", "small"),

    ("                    GET /hydrameta/history", "small"),

    ("", "empty"),

    ("                    🎯 TERENCE API", "big"),

    ("                    POST /terence/analyze", "small"),

    ("                    POST /terence/fix", "small"),

    ("                    GET /terence/suggestions", "small"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                    💻 CLI COMMANDS 💻", "header"),

    ("", "empty"),

    ("                    azazael --help", "med"),

    ("                    azazael scan --target", "med"),

    ("                    azazael analyze --all", "med"),

    ("                    azazael optimize --pop", "med"),

    ("                    azazael monitor --dash", "med"),

    ("                    azazael deploy --all", "med"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                    💾 DATABASE ADAPTERS 💾", "header"),

    ("", "empty"),

    ("                    🟦 SQLite   🟥 Redis   🟨 PostgreSQL", "med"),

    ("                    🟩 ElasticSearch   🟪 InfluxDB", "med"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                    🎮 EXECUTION MODES 🎮", "header"),

    ("", "empty"),

    ("                    🔄 Cotemporaneous   >> Parallel", "med"),

    ("                    🧵 Thread Pool   🤖 ML Optimization", "med"),

    ("                    🧬 Genetic Algorithms   📡 Real-time", "med"),

    ("                    💾 Persistent State    Cross-platform", "med"),

    ("", "empty"),

    (

        "",

        "divider",

    ),

    ("                     Azazael ECOSYSTEM v1.0.0 ", "big"),

    ("", "empty"),

]





def get_color(style, frame_num):

    if style == "divider":

        return f"hsl({(120 + frame_num * 0.1) % 360}, 100%, 50%)"

    elif style == "header":

        return f"hsl({(180 + frame_num * 0.1) % 360}, 100%, 55%)"

    elif style == "big":

        return f"hsl({(280 + frame_num * 0.1) % 360}, 100%, 55%)"

    elif style == "med":

        return "#00ff88"

    elif style == "sub":

        return "#8b949e"

    else:

        return "#e6edf3"





def create_video():

    os.makedirs(FRAME_DIR, exist_ok=True)

    print("Creating zipper-style interleaved video...")



    all_lines = []

    max_len = max(len(LEFT_LINES), len(RIGHT_LINES))



    for i in range(max_len):

        if i < len(LEFT_LINES):

            all_lines.append(("left", LEFT_LINES[i]))

        if i < len(RIGHT_LINES):

            all_lines.append(("right", RIGHT_LINES[i]))



    img = Image.new("RGB", (WIDTH, HEIGHT), color="#0a0a1a")

    draw = ImageDraw.Draw(img)



    try:

        font_big = ImageFont.truetype("arialbd.ttf", 72)

        font_large = ImageFont.truetype("arialbd.ttf", 56)

        font_medium = ImageFont.truetype("arialbd.ttf", 42)

        font_small = ImageFont.truetype("consolas.ttf", 32)

        font_tiny = ImageFont.truetype("consolas.ttf", 26)

    except Exception as exc:

        note_failure("make_video_zipper:L609", exc)
        font_big = ImageFont.load_default()

        font_large = ImageFont.load_default()

        font_medium = ImageFont.load_default()

        font_small = ImageFont.load_default()

        font_tiny = ImageFont.load_default()



    num_frames = DURATION_SECONDS * FPS



    gap_x = 30

    left_x = 50

    right_x = WIDTH // 2 + gap_x



    for frame_num in range(num_frames):

        progress = frame_num / num_frames



        frame = Image.new("RGB", (WIDTH, HEIGHT), color="#0a0a1a")

        draw = ImageDraw.Draw(frame)



        current_y = -progress * (len(all_lines) * 90) + 50



        for side, (text, style) in all_lines:

            if current_y > HEIGHT + 100:

                break

            if current_y < -100:

                current_y += 90

                continue



            color = get_color(style, frame_num)



            if style == "big":

                font = font_big

            elif style == "header":

                font = font_large

            elif style == "med":

                font = font_medium

            elif style == "small":

                font = font_small

            else:

                font = font_tiny



            x = left_x if side == "left" else right_x

            draw.text((x, current_y), text, font=font, fill=color)



            if style == "empty":

                current_y += 30

            else:

                current_y += 90



        frame.save(os.path.join(FRAME_DIR, f"frame_{frame_num:06d}.png"), "PNG")



        if frame_num % 300 == 0:

            print(f"Frame {frame_num}/{num_frames}")



    print("Creating video with ffmpeg...")






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

        print(f"Video created: {VIDEO_OUTPUT}")

    else:

        print(f"FFmpeg error: {result.stderr}")



    import shutil



    shutil.rmtree(FRAME_DIR)

    print("Done!")





if __name__ == "__main__":

    create_video()

# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
