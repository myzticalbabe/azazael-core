# coding: utf-8
"""
MP4 Video Generator for Azazael Ecosystem Overview
Requires: moviepy, numpy, opencv-python (optional)
Uses ffmpeg directly as fallback when moviepy not available.
"""
import os
import subprocess
import numpy as np
from PIL import Image, ImageDraw
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

AZAZAEL_ROOT = os.environ.get("AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", str(os.path.expanduser("~/azazael"))))
OUTPUT_DIR = os.path.join(AZAZAEL_ROOT, "output", "videos")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def create_frame(duration_sec, resolution=(1920, 1080)):
    """Create a single video frame."""
    img = Image.new("RGB", resolution, color="#0a0a1a")
    draw = ImageDraw.Draw(img)

    # Background gradient simulation
    for y in range(resolution[1]):
        r = int(10 + (y / resolution[1]) * 10)
        g = int(10 + (y / resolution[1]) * 10)
        b = int(26 + (y / resolution[1]) * 10)
        draw.line([(0, y), (resolution[0], y)], fill=(r, g, b))

    return np.array(img)


def create_video():
    """Generate MP4 video of Azazael Ecosystem."""

    print("Creating Azazael Ecosystem Video...")
    print("Resolution: 1920x1080")
    print("Duration: 60 seconds")
    print("Framerate: 30 FPS")

    # Frame configuration
    width, height = 1920, 1080
    duration = 60
    fps = 30
    total_frames = duration * fps

    frames = []

    for i in range(total_frames):
        t = i / fps  # Current time in seconds

        # Create frame
        frame = np.zeros((height, width, 3), dtype=np.uint8)

        # Background (gradient)
        frame[:, :, 0] = 10
        frame[:, :, 1] = 10
        frame[:, :, 2] = 26 + (
            np.linspace(0, height, height)[:, np.newaxis] * 5
        ).astype(np.uint8)

        # Title
        title_y = 80 + int(20 * np.sin(t * 0.5))
        frame[title_y : title_y + 100, 400:1520] = (15, 15, 30)

        # Progress bar
        progress_width = int((i / total_frames) * 1920)
        frame[1070:1080, 0:progress_width, 0] = 0
        frame[1070:1080, 0:progress_width, 1] = 245
        frame[1070:1080, 0:progress_width, 2] = 255

        # Animated elements
        # Grid effect
        for x in range(0, width, 50):
            frame[:, x : x + 1, :] = (frame[:, x : x + 1, :] * 0.95).astype(np.uint8)
        for y in range(0, height, 50):
            frame[y : y + 1, :, :] = (frame[y : y + 1, :, :] * 0.95).astype(np.uint8)

        # Tier blocks (7 tiers)
        tier_colors = [
            (165, 0, 255),  # Tier 5 - Purple
            (165, 0, 255),  # Tier 4 - Purple
            (255, 71, 87),  # Tier 3 - Red
            (255, 71, 87),  # Tier 2 - Red
            (255, 71, 87),  # Tier 1 - Red
            (0, 213, 255),  # Tier 0 - Cyan
            (0, 255, 136),  # Tier -1 - Green
        ]

        tier_width, tier_height = 120, 80
        tier_y = 850
        spacing = 30
        total_tier_width = 7 * tier_width + 6 * spacing
        start_x = (width - total_tier_width) // 2

        for j, color in enumerate(tier_colors):
            tx = start_x + j * (tier_width + spacing)
            # Fade in animation
            alpha = min(1.0, (t - 2) / (j * 0.3 + 0.5)) if t > 2 else 0
            if alpha > 0:
                tier_frame = frame[
                    tier_y : tier_y + tier_height, tx : tx + tier_width
                ].copy()
                tier_frame[:, :, 0] = (
                    tier_frame[:, :, 0] * (1 - alpha) + color[0] * alpha
                ).astype(np.uint8)
                tier_frame[:, :, 1] = (
                    tier_frame[:, :, 1] * (1 - alpha) + color[1] * alpha
                ).astype(np.uint8)
                tier_frame[:, :, 2] = (
                    tier_frame[:, :, 2] * (1 - alpha) + color[2] * alpha
                ).astype(np.uint8)
                frame[tier_y : tier_y + tier_height, tx : tx + tier_width] = tier_frame

        # Info cards
        cards = [
            ("Systems", "14", "Core Components"),
            ("Worms", "17", "Functional Agents"),
            ("Tiers", "7", "Architecture Levels"),
            ("Animations", "24", "Visual Protocols"),
        ]

        card_width, card_height = 280, 180
        card_y = 300
        card_spacing = 40
        total_card_width = 4 * card_width + 3 * card_spacing
        card_start_x = (width - total_card_width) // 2

        for j, (title, value, desc) in enumerate(cards):
            cx = card_start_x + j * (card_width + card_spacing)
            card_y_offset = card_y + int(20 * np.sin(t + j))

            # Card background
            frame[card_y_offset : card_y_offset + card_height, cx : cx + card_width] = (
                20,
                20,
                40,
            )

            # Card border (cyan)
            frame[card_y_offset, cx : cx + card_width, 1] = 245
            frame[card_y_offset, cx : cx + card_width, 2] = 255
            frame[card_y_offset + card_height - 1, cx : cx + card_width, 1] = 245
            frame[card_y_offset + card_height - 1, cx : cx + card_width, 2] = 255

        # Typing effect text area
        if t > 0.5:
            text_start_y = 180
            chars_to_show = int((t - 0.5) * 60)  # 60 chars per second
            text = """The Azazael Ecosystem represents a sophisticated distributed computational framework.
Spanning 14 interconnected components across 7 hierarchical tiers.
Powered by 17 functional worms and metaworms.
Featuring advanced ML orchestration through HYDRA and HYDRAMETA."""

            frame[text_start_y : text_start_y + 150, 200:1720] = (10, 10, 26)

        # Scene indicator
        min(5, int(t / 12) + 1)
        frame[20:60, 1700:1900, 0] = 0
        frame[20:60, 1700:1900, 1] = 100
        frame[20:60, 1700:1900, 2] = 100

        frames.append(frame)

        if i % 30 == 0:
            print(
                f"Progress: {i}/{total_frames} frames ({100 * i / total_frames:.1f}%)"
            )

    print("Creating video clip...")

    # Try moviepy first, fall back to ffmpeg PPM pipeline
    try:
        from moviepy.editor import ImageSequenceClip
        clip = ImageSequenceClip(frames, fps=fps)
        output_path = os.path.join(OUTPUT_DIR, "Azazael_ECOSYSTEM.mp4")
        clip.write_videofile(
            output_path,
            codec="libx264",
            fps=fps,
            quality=8,
            audio=False,
            verbose=False,
            logger=None,
        )
    except ImportError:
        print("moviepy not available, using ffmpeg PPM pipeline...")
        frames_dir = os.path.join(AZAZAEL_ROOT, "output", "ecosystem_frames")
        os.makedirs(frames_dir, exist_ok=True)
        for i, frame in enumerate(frames):
            # Write PPM frame
            ppm_path = os.path.join(frames_dir, f"frame_{i:05d}.ppm")
            with open(ppm_path, "w", encoding="utf-8") as f:
                f.write(f"P3\n{width} {height}\n255\n")
                for y in range(height):
                    for x in range(width):
                        r, g, b = frame[y, x]
                        f.write(f"{int(r)} {int(g)} {int(b)} ")
                    f.write("\n")
            if i % 180 == 0:
                print(f"  PPM frame {i}/{total_frames}")
        output_path = os.path.join(OUTPUT_DIR, "Azazael_ECOSYSTEM.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-framerate", str(fps),
            "-i", f"{frames_dir}/frame_%05d.ppm",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-prese", "medium", "-crf", "20", "-", str(duration),
            output_path,
        ], capture_output=True, timeout=1800)

    print(f"Video saved to: {output_path}")
    print(f"Total frames: {len(frames)}")
    print(f"Duration: {duration} seconds")
    print(f"Resolution: {width}x{height}")
    print(f"Framerate: {fps} FPS")


if __name__ == "__main__":
    try:
        create_video()
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Install with: pip install moviepy numpy opencv-python pillow")
        print(
            "\nAlternatively, open Azazael_VIDEO.html in a browser and screen record."
        )
    except Exception as e:
        print(f"Error: {e}")
