#!/usr/bin/env python3
# coding: utf-8
"""
Azazael Educational Video Generator v1.0
Technical, Dynamic, Vibrant, Cinematic Educational Video
Uses FFmpeg for video generation with cinematic effects
"""

import subprocess
from pathlib import Path
from datetime import datetime
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import List, Optional  # removed — all names have native equivalents


class AzazaelEducationalVideo:
    """Generate technical educational video about Azazael ecosystem."""

    def __init__(self):
        self.root = Path(__file__).parent.parent
        self.output_dir = self.root / "OUTPUT" / "videos"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def generate_intro_section(self) -> Path:
        """Generate intro section with cinematic lighting."""
        output = self.output_dir / f"azazael_intro_{self.timestamp}.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x0a0a1a:d=10",
            "-vf",
            (
                "drawgrid=w=50:h=50:color=cyan@0.02,"
                "drawbox=x=0:y=0:w=1920:h=120:color=0x00d5ff:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='AZAZAEL"
                " ECOSYSTEM':fontcolor=white:fontsize=64:x=100:y=35,"
                "fade=t=in:st=0:d=2,"
                "fade=t=out:st=8:d=2"
            ),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-",
            "10",
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}")
        elif output.exists():
            print(f"  OK: {output.name} ({output.stat().st_size / 1024:.1f} KB)")
        return output

    def generate_architecture_section(self) -> Path:
        """Generate architecture diagram section."""
        output = self.output_dir / f"azazael_architecture_{self.timestamp}.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x1a1a2e:d=15",
            "-vf",
            (
                "drawgrid=w=200:h=100:color=magenta@0.02,"
                "drawbox=x=100:y=100:w=400:h=150:color=0x6a0dad:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='CORE"
                " LAYER':fontcolor=white:fontsize=36:x=150:y=150,"
                "drawbox=x=550:y=100:w=400:h=150:color=0x00d5ff:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='HYDRA"
                " ML':fontcolor=black:fontsize=36:x=600:y=150,"
                "drawbox=x=1000:y=100:w=400:h=150:color=0x00ff88:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='ORCHESTRATION"
                "':fontcolor=black:fontsize=36:x=1010:y=150,"
                "fade=t=in:st=0:d=3,"
                "fade=t=out:st=12:d=3"
            ),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-",
            "15",
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}")
        elif output.exists():
            print(f"  OK: {output.name} ({output.stat().st_size / 1024:.1f} KB)")
        return output

    def generate_python_section(self) -> Path:
        """Generate Python installations section."""
        output = self.output_dir / f"azazael_python_{self.timestamp}.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x0d1a2d:d=12",
            "-vf",
            (
                "drawgrid=w=100:h=50:color=cyan@0.015,"
                "drawbox=x=50:y=50:w=500:h=80:color=0x00bfff:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='PYTHON"
                " 3.14.3':fontcolor=white:fontsize=48:x=80:y=75,"
                "drawbox=x=50:y=180:w=500:h=80:color=0x4a90d9:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='PYTHON"
                " 3.11.9':fontcolor=white:fontsize=48:x=80:y=205,"
                "drawbox=x=50:y=310:w=500:h=80:color=0x2ecc71:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='18 TOTAL"
                " INSTALLATIONS':fontcolor=black:fontsize=40:x=80:y=335,"
                "fade=t=in:st=0:d=2,fade=t=out:st=10:d=2"
            ),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-",
            "12",
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}")
        elif output.exists():
            print(f"  OK: {output.name} ({output.stat().st_size / 1024:.1f} KB)")
        return output

    def generate_worms_section(self) -> Path:
        """Generate worms/scanners section."""
        output = self.output_dir / f"azazael_worms_{self.timestamp}.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x1a0a2e:d=15",
            "-vf",
            (
                "drawgrid=w=80:h=80:color=ff00ff@0.02,"
                "drawbox=x=100:y=100:w=350:h=60:color=0x9b59b6:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='OMNI"
                " WORM':fontcolor=white:fontsize=32:x=120:y=125,"
                "drawbox=x=100:y=200:w=350:h=60:color=0xe74c3c:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='MORPHING"
                " WORM':fontcolor=white:fontsize=32:x=120:y=225,"
                "drawbox=x=100:y=300:w=350:h=60:color=0x3498db:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='CLUSTER"
                " INSPECTOR':fontcolor=white:fontsize=32:x=120:y=325,"
                "drawbox=x=100:y=400:w=350:h=60:color=0x2ecc71:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='ERROR"
                " SCAN':fontcolor=black:fontsize=32:x=120:y=425,"
                "fade=t=in:st=0:d=3,fade=t=out:st=12:d=3"
            ),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-",
            "15",
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}")
        elif output.exists():
            print(f"  OK: {output.name} ({output.stat().st_size / 1024:.1f} KB)")
        return output

    def generate_ml_section(self) -> Path:
        """Generate ML frameworks section."""
        output = self.output_dir / f"azazael_ml_{self.timestamp}.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x0a2e1a:d=12",
            "-vf",
            (
                "drawgrid=w=150:h=60:color=00ff88@0.02,"
                "drawbox=x=50:y=80:w=280:h=70:color=0xff6b6b:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='TENSORFLOW"
                " 2.20':fontcolor=white:fontsize=28:x=70:y=105,"
                "drawbox=x=350:y=80:w=280:h=70:color=0xfeca57:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='PYTORCH"
                " 2.10':fontcolor=black:fontsize=28:x=370:y=105,"
                "drawbox=x=650:y=80:w=280:h=70:color=0x5f27cd:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='SCIKIT"
                " 1.8':fontcolor=white:fontsize=28:x=670:y=105,"
                "drawbox=x=950:y=80:w=280:h=70:color=0x00d2d3:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='KERAS"
                " 3.13':fontcolor=black:fontsize=28:x=970:y=105,"
                "fade=t=in:st=0:d=2,fade=t=out:st=10:d=2"
            ),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-",
            "12",
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}")
        elif output.exists():
            print(f"  OK: {output.name} ({output.stat().st_size / 1024:.1f} KB)")
        return output

    def generate_llm_section(self) -> Path:
        """Generate LLM integration section."""
        output = self.output_dir / f"azazael_llm_{self.timestamp}.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x2e1a2e:d=12",
            "-vf",
            (
                "drawgrid=w=120:h=60:color=ff00ff@0.02,"
                "drawbox=x=200:y=100:w=500:h=80:color=0x6c5ce7:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='KILO-LOCAL"
                " CLI':fontcolor=white:fontsize=40:x=220:y=125,"
                "drawbox=x=200:y=220:w=500:h=60:color=0x00cec9:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='OLLAMA"
                " INTEGRATION':fontcolor=black:fontsize=28:x=220:y=245,"
                "drawbox=x=200:y=320:w=500:h=60:color=0xfd79a8:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='OPEN"
                " INTERPRETER':fontcolor=black:fontsize=28:x=220:y=345,"
                "fade=t=in:st=0:d=2,fade=t=out:st=10:d=2"
            ),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-",
            "12",
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}")
        elif output.exists():
            print(f"  OK: {output.name} ({output.stat().st_size / 1024:.1f} KB)")
        return output

    def generate_outro_section(self) -> Path:
        """Generate outro section."""
        output = self.output_dir / f"azazael_outro_{self.timestamp}.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x0a0a1a:d=8",
            "-vf",
            (
                "drawgrid=w=60:h=60:color=cyan@0.03,"
                "drawbox=x=0:y=0:w=1920:h=1080:color=0x000000@0.5:t=fill,"
                "drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='AZAZAEL"
                " ECOSYSTEM':fontcolor=0x00d5ff:fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2,"
                "fade=t=in:st=0:d=2,"
                "fade=t=out:st=6:d=2"
            ),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-",
            "8",
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}")
        elif output.exists():
            print(f"  OK: {output.name} ({output.stat().st_size / 1024:.1f} KB)")
        return output

    def generate_complete_video(self, sections: (list[str] | None) = None):
        """Generate complete educational video with all sections."""
        print("=" * 60)
        print("AZAZAEL EDUCATIONAL VIDEO GENERATOR")
        print("=" * 60)
        print()

        section_methods = {
            "intro": self.generate_intro_section,
            "architecture": self.generate_architecture_section,
            "python": self.generate_python_section,
            "worms": self.generate_worms_section,
            "ml": self.generate_ml_section,
            "llm": self.generate_llm_section,
            "outro": self.generate_outro_section,
        }

        if sections is None:
            sections = list(section_methods.keys())

        generated_files = []

        print("Generating video sections...")
        print()

        for section in sections:
            if section in section_methods:
                print(f"[{section.upper()}] Generating...")
                try:
                    output_file = section_methods[section]()
                    generated_files.append(output_file)
                    print(f"  -> {str(output_file)}")
                except Exception as e:
                    print(f"  -> ERROR: {e}")
            else:
                print(f"[{section}] SKIPPED (unknown)")

        print()
        print("=" * 60)
        print("SECTION GENERATION COMPLETE")
        print("=" * 60)
        print()
        print("Generated files:")
        for f in generated_files:
            print(f"  {f}")

        print()
        print("To concatenate all sections:")
        print("  ffmpeg -f concat -i list.txt -c copy final_video.mp4")

        return generated_files

    def create_concat_file(self, files: list[Path]):
        """Create concat file for FFmpeg."""
        list_file = self.output_dir / f"video_list_{self.timestamp}.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for file in files:
                if file.exists():
                    f.write(f"file '{file}'\n")
        return list_file


def main():
    """Main entry point."""
    video = AzazaelEducationalVideo()

    # Generate all sections
    files = video.generate_complete_video()

    # Create concat file
    concat_file = video.create_concat_file(files)
    print(f"\nConcat file: {concat_file}")

    # Create final video
    final_output = video.output_dir / f"azazael_educational_video_{video.timestamp}.mp4"

    print("\nCreating final video...")
    cmd = [
    "ffmpeg",
    "-y",
    "-f",
    "concat",
    "-safe",
    "0",
    "-i",
    str(concat_file),
    "-c:v",
    "libx264",
    "-pix_fmt",
    "yuv420p",
    "-crf",
    "23",
    "-preset",
    "medium",
    str(final_output),
    ]

    subprocess.run(cmd, capture_output=True, timeout=1800)

    if final_output.exists():
        print(f"Final video: {final_output}")
        print(f"Size: {final_output.stat().st_size / (1024 * 1024):.2f} MB")
    else:
        print("Error creating final video")


if __name__ == "__main__":
    main()
