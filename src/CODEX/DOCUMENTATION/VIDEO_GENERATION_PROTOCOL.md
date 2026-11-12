# Azazael Video Generation Protocol v1.0

## Overview
FFmpeg-based video generation for Azazael Ecosystem visualization using lavfi (libavfilter virtual input).

## Protocol

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
```
drawgrid=w=50:h=50:color=cyan@0.02
```

#### Title Bar
```
drawbox=x=0:y=0:w=1280:h=80:color=0x1a1a3a:t=fill
drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='TITLE':fontsize=36:x=(w-text_w)/2:y=22:fontcolor=white
```

#### Tier Sections (with fade-in)
```
drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='TIER NAME':fontsize=24:x=50:y=150:fontcolor=yellow:alpha='if(lt(t,N),t/N,1)'
drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='COMPONENTS':fontsize=18:x=50:y=200:fontcolor=0x00ff00:alpha='if(lt(t,N+1),t/(N+1),1)'
```

### Color Constants
| Color | Hex | Usage |
|-------|-----|-------|
| White | 0xffffff | Main titles |
| Yellow | 0xffff00 | Tier headers |
| Cyan | 0x00ffff | System components |
| Lime | 0x00ff00 | ML/Optimization |
| Magenta | 0xff00ff | Analysis tools |
| Dark blue | 0x1a1a3a | Header bar |

### Fonts
- Primary: Consolas (`/c/Windows/Fonts/consola.ttf`)
- Monospace for technical displays

### Encoding Settings
```
-c:v libx264          # H.264 encoder
-pix_fmt yuv420p      # Compatible pixel format
-preset medium        # Encoding speed/quality balance
-crf 23               # Quality (lower = better)
```

### Common Commands

#### Technical Overview (20s)
```bash
ffmpeg -y -f lavfi -i "color=c=0x0a0a1a:d=20" -vf "
fps=30,
drawgrid=w=50:h=50:color=cyan@0.02,
drawbox=x=0:y=0:w=1280:h=80:color=0x1a1a3a:t=fill,
drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='Azazael ECOSYSTEM':fontsize=36:x=(w-text_w)/2:y=22:fontcolor=white,
drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='Tier 1 - WORMS':fontsize=24:x=50:y=150:fontcolor=yellow,
drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='SYSTEMZ PROCESSZ DISKZ NETWORXZ CLOCKZ':fontsize=18:x=50:y=200:fontcolor=0x00ff00,
...
" -c:v libx264 -pix_fmt yuv420p -t 20 OUTPUT.mp4
```

#### Simple Popup (5-10s)
```bash
ffmpeg -y -f lavfi -i "color=c=0x0a0a1a:d=5" -vf "
fps=25,
drawtext=fontfile=/c/Windows/Fonts/consola.ttf:text='Azazael':fontsize=64:x=(w-text_w)/2:y=(h-text_h)/2
" -c:v libx264 -pix_fmt yuv420p -t 5 OUTPUT.mp4
```

### Known Limitations
- Colons (:) in drawtext break filter parsing - avoid or use text file input
- `alpha` expression syntax varies by FFmpeg version
- textfile input requires proper path formatting

## Files Generated
| File | Description |
|------|-------------|
| `Azazael_TECHNICAL.mp4` | Full 20s technical overview |
| `Azazael_POPUP.mp4` | Short 8s popup summary |
| `Azazael_ECOSYSTEM.mp4` | Basic test video |

## Usage
```bash
# Generate video
ffmpeg -y -f lavfi -i "color=c=0x0a0a1a:d=10" -vf "..." -c:v libx264 -pix_fmt yuv420p -t 10 video.mp4

# Play in VLC
vlc C:\Users\clint\Desktop\CODEX\Azazael_TECHNICAL.mp4

# Or double-click in Windows Explorer
```
