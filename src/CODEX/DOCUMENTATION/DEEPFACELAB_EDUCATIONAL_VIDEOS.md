# DeepFaceLab Educational Video Templates

## Overview

This document describes templates for creating educational videos using DeepFaceLab face swapping technology combined with Azazael video generation templates.

---

## Part 1: DeepFaceLab Workflow

### Step 1: Prepare Source Material
```
2) extract images from video data_src.bat    - Extract frames from source video
3) cut video (drop video on me).bat        - Cut video to desired length
3) extract images from video data_dst FULL FPS.bat - Extract destination frames
```

### Step 2: Face Extraction
```
4) data_src faceset extract.bat         - Auto face extraction
4) data_src faceset extract MANUAL.bat  - Manual face extraction (better quality)
```

### Step 3: Training (Optional - Pre-trained models also work)
```
Run DFL GUI for training
Or use: train.bat (varies by version)
```

### Step 4: Merging
```
Merge faces from data_src onto data_dst video
Output: final_video.mp4
```

---

## Part 2: Azazael Educational Video Templates

Located in: `CODEX/VIDEOS/`

### Template Selection Guide

| Template | Use Case | Example |
|----------|-----------|---------|
| `create_dual_pane_video.py` | Side-by-side comparison | Before/After, Original/Swapped |
| `create_scrolling_video.py` | Scrolling content | Code walkthroughs, tutorials |
| `create_reference_video.py` | Reference with annotations | Technical documentation |
| `create_video.py` | Basic video | Simple educational content |
| `generate_matrix_video.py` | Matrix-style effects | Cybersecurity, tech demos |
| `generate_video.py` | General generator | Flexible template |
| `make_video_v2.py` | Enhanced video | Higher quality output |
| `make_video_quad.py` | 4-pane split | Multi-topic comparison |
| `make_video_vibrant.py` | High saturation | Engaging presentations |
| `make_video_docs.py` | Documentation style | Technical docs |

---

## Part 3: FFmpeg Template Structure

### Basic Template
```bash
ffmpeg -y \
    -f lavfi -i "color=c=0x0a0a1a:d=DURATION" \
    -vf "FILTER_CHAIN" \
    -c:v libx264 -pix_fmt yuv420p -t DURATION \
    OUTPUT.mp4
```

### Filter Chain Components

#### Background
```bash
# Grid pattern
drawgrid=w=50:h=50:color=cyan@0.02

# Solid color
drawbox=x=0:y=0:w=1280:h=80:color=0x1a1a3a:t=fill
```

#### Title Bar
```bash
# Colored bar with text
drawbox=x=0:y=0:w=1280:h=80:color=0x1a1a3a:t=fill
drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='TITLE':fontcolor=white:fontsize=36:x=50:y=25
```

#### Sections with Fade-in
```bash
# Fade in effect
fade=t=in:st=0:d=1.5

# Text overlay with fade
drawtext=text='Section 1':fade_in=t=in:st=1:d=0.5
```

#### Animation Effects
```bash
# Zoom effect
zoompan=z='min(zoom+0.0015,1.5)':d=5:s=1280x720

# Scroll
scroll=vertical=1:h=720

# Crossfade
xfade=transition=fade:duration=1:offset=5
```

---

## Part 4: Complete Educational Video Template

### Combining DeepFaceLab + Azazael Templates

```python
"""
Educational Video Generator
Combines DFL face-swap with Azazael video templates
"""

import subprocess
import os
from pathlib import Path

class EducationalVideoGenerator:
    def __init__(self):
        self.dfl_path = "C:/Users/clint/Downloads/DeepFaceLab/DeepFaceLab_DirectX12"
        self.output_path = Path("C:/Users/clint/Desktop/Azazael_UNIFIED/OUTPUT/videos")

    def create_educational_video(
        self,
        source_video: str,
        destination_video: str,
        template: str = "dual_pane",
        title: str = "Educational Content",
        sections: list = None
    ):
        """Create educational video with face swap."""

        # Step 1: Extract frames
        self._extract_frames(source_video, "data_src")
        self._extract_frames(destination_video, "data_dst")

        # Step 2: Extract faces
        self._extract_faces("data_src")

        # Step 3: Run face swap
        self._merge_faces()

        # Step 4: Apply Azazael template
        self._apply_template(
            template=template,
            title=title,
            sections=sections or []
        )

        return self.output_path / f"{title.replace(' ', '_')}.mp4"

    def _extract_frames(self, video: str, folder: str):
        """Extract frames using DFL batch file."""
        batch = self.dfl_path / f"2) extract images from video {folder}.bat"
        if batch.exists():
            subprocess.run([str(batch), video])

    def _extract_faces(self, folder: str):
        """Extract faces from frames."""
        batch = self.dfl_path / f"4) {folder} faceset extract.bat"
        if batch.exists():
            subprocess.run([str(batch)])

    def _merge_faces(self):
        """Merge faces onto destination."""
        # Run DFL merge process
        pass

    def _apply_template(self, template: str, title: str, sections: list):
        """Apply Azazael video template."""

        # Import template
        if template == "dual_pane":
            from CODEX.VIDEOS.create_dual_pane_video import create_html
            create_html(title=title, sections=sections)

        elif template == "scrolling":
            from CODEX.VIDEOS.create_scrolling_video import generate_html
            generate_html(title=title, sections=sections)

        elif template == "docs":
            from CODEX.VIDEOS.make_video_docs import create_docs_video
            create_docs_video(title=title, sections=sections)
```

---

## Part 5: Quick Reference

### DeepFaceLab Batch Files
```
1) clear workspace.bat          - Clean workspace
2) extract images from video     - Frame extraction
3) cut video                     - Trim video
4) faceset extract               - Face extraction
5) training                     - Train model (GUI)
6) merging                       - Face swap merging
7) result video                  - Finalize output
```

### Azazael Video Templates
```
# Python templates
python create_dual_pane_video.py   # HTML dual-pane preview
python create_scrolling_video.py   # Scrolling animation
python make_video_docs.py          # Documentation style

# Batch templates
generate_comprehensive.bat        # Full pipeline
make_azazael_video.bat           # Azazael-specific
make_video.bat                    # Basic generation
```

---

## Tips for Educational Videos

1. **Use dual-pane for comparisons**: Show original side-by-side with modified
2. **Add text overlays**: Use FFmpeg drawtext filter for annotations
3. **Fade transitions**: Smooth section changes with fade effects
4. **Consistent styling**: Use same colors/fonts throughout
5. **Preview first**: Use HTML previews before rendering final video

---

## Dependencies

- DeepFaceLab (DirectX12 version recommended)
- FFmpeg (for video encoding)
- Python 3.x (for Azazael templates)
- PIL, OpenCV (image processing)

---

Generated: 2026-02-13
Azazael Ecosystem v1.0
