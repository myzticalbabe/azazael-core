#!/usr/bin/env python3
"""
CODEX Progress Bar — ffmpeg subprocess wrapper with real-time progress.

Parses ffmpeg stderr output (time=00:00:05.00 patterns) to display
a progress bar during video encoding. Works with any ffmpeg command.

Usage:
    from progress_bar import ffmpeg_with_progress
    success = ffmpeg_with_progress(cmd, duration=15.0, label="KINETIC")

If duration is unknown, shows a spinner with elapsed time instead.
"""

__all__ = ["ffmpeg_with_progress", "parse_ffmpeg_time", "format_duration"]

import re
import sys
import time
import shutil
import subprocess
import threading
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Time parsing ────────────────────────────────────────────────────────────

_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+)(?:\.(\d+))?")
_SPEED_RE = re.compile(r"speed=\s*([\d.]+)x")


def parse_ffmpeg_time(line: str) -> float | None:
    """Parse ffmpeg stderr line for time=HH:MM:SS.ms, return seconds."""
    m = _TIME_RE.search(line)
    if m:
        h, mi, s, ms = m.groups()
        return int(h) * 3600 + int(mi) * 60 + int(s) + (int(ms) / (10 ** len(ms)) if ms else 0)
    return None


def parse_ffmpeg_speed(line: str) -> float | None:
    """Parse ffmpeg stderr line for speed=XXx."""
    m = _SPEED_RE.search(line)
    if m:
        return float(m.group(1))
    return None


def format_duration(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS."""
    if seconds >= 3600:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h}:{m:02d}:{s:02d}"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


def _draw_bar(pct: float, width: int = 40, elapsed: float = 0,
              current_t: float = 0, duration: float = 0, speed: float = 0) -> str:
    """Draw a progress bar string for stdout."""
    filled = int(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    pct_str = f"{pct * 100:5.1f}%"
    time_str = f"{format_duration(current_t)}/{format_duration(duration)}" if duration > 0 else f"t={format_duration(current_t)}"
    eta_str = ""
    if speed > 0 and duration > 0:
        remaining = duration - current_t
        eta_s = remaining / speed if speed > 0 else 0
        eta_str = f" ETA {format_duration(eta_s)}"
    return f"\r  [{bar}] {pct_str}  {time_str}{eta_str}"


# ── ffmpeg progress wrapper ─────────────────────────────────────────────────

def ffmpeg_with_progress(cmd: list[str], duration: float = 0,
                         label: str = "", check: bool = True) -> bool:
    """Run ffmpeg with a real-time progress bar.

    Args:
        cmd: ffmpeg command as list of strings (e.g., ['ffmpeg', '-y', '-i', ...])
        duration: Expected output duration in seconds (for progress bar).
                  If 0, shows spinner with elapsed time.
        label: Optional label prefix (e.g., "KINETIC", "Pass 1").
        check: If True, print [OK]/[FAIL] result line.

    Returns:
        True if ffmpeg returned 0, False otherwise.
    """
    label_prefix = f"[{label}] " if label else ""

    # Check ffmpeg exists
    if not shutil.which("ffmpeg"):
        print(f"  {label_prefix}ERROR: ffmpeg not found")
        return False

    print(f"  {label_prefix}Encoding...")

    # Inject -nostdin to prevent ffmpeg from hanging on stdin
    if "-nostdin" not in cmd:
        cmd = cmd[:1] + ["-nostdin"] + cmd[1:]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1,
        )

        # Collector thread for stderr lines (prevents pipe deadlock)
        stderr_lines: list[str] = []

        def _collect_stderr():
            if proc.stderr:
                for line in proc.stderr:
                    stderr_lines.append(line.rstrip())

        collector = threading.Thread(target=_collect_stderr, daemon=True)
        collector.start()

        start = time.monotonic()
        last_update = 0.0
        last_line_len = 0

        # Wait for ffmpeg to finish, polling stderr for progress
        while proc.poll() is None:
            time.sleep(0.1)
            now = time.monotonic()

            # Parse latest stderr lines for time/speed
            current_t = 0.0
            speed = 0.0
            for line in reversed(stderr_lines):
                t = parse_ffmpeg_time(line)
                if t is not None:
                    current_t = t
                    break
            for line in reversed(stderr_lines):
                s = parse_ffmpeg_speed(line)
                if s is not None:
                    speed = s
                    break

            if duration > 0:
                pct = min(current_t / duration, 1.0) if current_t > 0 else 0.0
                # Only print if enough has changed (avoids flicker)
                elapsed = now - start
                if now - last_update > 0.15:
                    bar = _draw_bar(pct, width=30, elapsed=elapsed,
                                    current_t=current_t, duration=duration, speed=speed)
                    sys.stdout.write("\r" + " " * last_line_len + "\r")
                    sys.stdout.write(f"  {label_prefix}{bar}")
                    sys.stdout.flush()
                    last_line_len = len(f"  {label_prefix}{bar}")
                    last_update = now
            else:
                # Spinner for unknown duration
                elapsed = now - start
                if now - last_update > 0.5:
                    spinners = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
                    spin = spinners[int(elapsed * 4) % len(spinners)]
                    bar = f"{spin} {format_duration(elapsed)} elapsed"
                    sys.stdout.write("\r" + " " * last_line_len + "\r")
                    sys.stdout.write(f"  {label_prefix}{bar}")
                    sys.stdout.flush()
                    last_line_len = len(f"  {label_prefix}{bar}")
                    last_update = now

        # Final join & collect
        collector.join(timeout=2)
        returncode = proc.wait(timeout=1800)

        # Clear progress line
        sys.stdout.write("\r" + " " * last_line_len + "\r")
        sys.stdout.flush()

        elapsed = time.monotonic() - start
        if returncode == 0:
            if check:
                print(f"  {label_prefix}✅ Done ({format_duration(elapsed)})")
            return True
        else:
            if check:
                print(f"  {label_prefix}❌ Failed (code {returncode}, {format_duration(elapsed)})")
            return False

    except KeyboardInterrupt:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception as exc:
            note_failure("progress_bar:L194", exc)
        print(f"\n  {label_prefix}⚠️  Cancelled")
        return False
    except Exception as e:
        print(f"  {label_prefix}❌ Error: {e}")
        return False


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    import os

    print("=== CODEX Progress Bar — Self-Test ===\n")

    # Generate a short test video
    out = tempfile.mktemp(suffix=".mp4")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=0x111111:s=320x240:d=3",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-t", "3",
        out,
    ]

    print("Test 1: Known duration (3s)")
    success = ffmpeg_with_progress(cmd, duration=3.0, label="test-3s")

    if Path(out).exists():
        print(f"  Output: {out} ({os.path.getsize(out)/1024:.0f} KB)")
        Path(out).unlink()

    print("\nTest 2: Unknown duration (spinner)")
    cmd2 = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=0x222222:s=160x120:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-t", "1",
        tempfile.mktemp(suffix=".mp4"),
    ]

    success2 = ffmpeg_with_progress(cmd2, duration=0, label="test-spinner")
    print(f"\n  {'PASS' if success and success2 else 'FAIL'} — Progress bar self-test")
