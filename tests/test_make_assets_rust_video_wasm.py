"""Contract tests for tools/make_assets.py — the blob layout is the API.

The two containers are read by Rust with fixed offsets, so a change here breaks
`frame_count`/`unpack_rgb_frame` silently. These tests build a tiny real clip and
assert the bytes, rather than trusting that ffmpeg did what the code asked.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import struct
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "src", "RUST_VIDEO_WASM", "tools", "make_assets.py")

spec = importlib.util.spec_from_file_location("make_assets", TOOL)
make_assets = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(make_assets)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> str:
    """One second of colour at 30 fps — enough for every frame a blob can hold."""
    path = str(tmp_path_factory.mktemp("clip") / "clip.mp4")
    make_assets.run(
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", path,
    )
    return path


@needs_ffmpeg
def test_frames_container_layout(clip, tmp_path):
    out = str(tmp_path / "frames.bin")
    n, size = make_assets.pack_frames(clip, fps=10, width=64, height=36, out=out)
    assert n >= 9, f"expected ~10 frames in 1 s at 10 fps, got {n}"

    blob = open(out, "rb").read()
    assert len(blob) == size
    assert blob[0:4] == b"AVFB"
    count, w, h, fps = struct.unpack("<4I", blob[4:20])
    assert (count, w, h, fps) == (n, 64, 36, 10)

    offsets = struct.unpack(f"<{n + 1}I", blob[20:20 + 4 * (n + 1)])
    assert offsets[0] == 20 + 4 * (n + 1), "first payload byte follows the offset table"
    assert offsets[-1] == len(blob), "last offset terminates the payload"
    assert all(a < b for a, b in zip(offsets, offsets[1:])), "offsets ascend"
    # every slice must be a JPEG the Rust side will accept the SOI/EOI of
    for i in range(n):
        frame = blob[offsets[i]:offsets[i + 1]]
        assert frame[:2] == b"\xff\xd8", f"frame {i} lacks a JPEG SOI"
        assert frame[-2:] == b"\xff\xd9", f"frame {i} lacks a JPEG EOI"


@needs_ffmpeg
def test_raw_container_layout(clip, tmp_path):
    out = str(tmp_path / "raw.bin")
    n, size = make_assets.pack_raw(clip, fps=6, width=32, height=18, out=out)
    assert n >= 5
    blob = open(out, "rb").read()
    assert blob[0:4] == b"AVRD"
    count, w, h, fps = struct.unpack("<4I", blob[4:20])
    assert (count, w, h, fps) == (n, 32, 18, 6)
    assert len(blob) == 20 + n * 32 * 18 * 3 == size, "no padding: header + count*stride"


def test_run_reports_a_missing_binary_promptly():
    """A wedged child must be a named failure, never a silent hang."""
    with pytest.raises(SystemExit) as err:
        make_assets.run("ffmpeg-does-not-exist-here")
    msg = str(err.value)
    assert "cannot run" in msg and "ffmpeg-does-not-exist-here" in msg, msg
    assert "Traceback" not in msg


def test_timeout_is_bounded():
    assert isinstance(make_assets.FFMPEG_TIMEOUT, int)
    assert 0 < make_assets.FFMPEG_TIMEOUT <= 3600


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
