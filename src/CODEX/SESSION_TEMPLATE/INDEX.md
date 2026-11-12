# SESSION_TEMPLATE Index

## Folder Structure
```
SESSION_TEMPLATE/
├── templates/      # HTML visualization templates
├── data/          # JSON data files
├── generators/    # Python generators
├── recorders/     # Node.js recording scripts
├── videos/        # Generated MP4 videos
└── documentation/ # README and template info
```

---

## TEMPLATES (templates/)

| File | Description | Theme |
|------|-------------|-------|
| `consequences_ultimate.html` | Main consequences report with all visual effects | Cosmic/Nebula |
| `consequences_report.html` | Original themed version | Video Production |
| `consequences_neon.html` | Cyan/Magenta neon version | Neon |
| `consequences_cosmic.html` | Purple with stars | Cosmic |
| `consequences_glass.html` | Frosted glass effect | Glassmorphism |
| `session_report.html` | Session flow visualization | Rainbow |
| `session_steampunk.html` | Steampunk themed | Gears/Brass |
| `session_web.html` | Basic web style | Simple |
| `session_flow.html` | Flow diagram | Flow |
| `session_explicit_web.html` | Explicit details | Detailed |
| `session_complete_web.html` | Complete overview | Full |
| `session_static_details.html` | Static details | Minimal |
| `session_twinkle_web.html` | Twinkle animation | Stars |
| `failure_report.html` | Failure hierarchy | Neon Red |
| `env_hierarchy.html` | Environment hierarchy | Grid |
| `output_ultimate.html` | **NEW** Generated - Ultimate style | All effects |
| `output_neon.html` | **NEW** Generated - Neon style | Cyan/Magenta |
| `output_cosmic.html` | **NEW** Generated - Cosmic style | Purple/stars |
| `output_glass.html` | **NEW** Generated - Glass style | Frosted glass |
| `output_report.html` | **NEW** Generated - Report style | Terminal green |
| `output_mystical.html` | **NEW** Generated - Mystical style | Runes, smoke, purple glow |
| `output_occult.html` | **NEW** Generated - Occult style | Dark, candles, pentagrams |
| `output_ethereal.html` | **NEW** Generated - Ethereal style | Aurora, spirits, floating orbs |
| `output_grimoire.html` | **NEW** Generated - Grimoire style | Old book, parchment, sepia |

---

## DATA (data/)

| File | Description |
|------|-------------|
| `session_data.json` | Session report data (33 steps) |
| `failure_data.json` | Failure report data (hierarchical) |

---

## GENERATORS (generators/)

| File | Description | Usage |
|------|-------------|-------|
| `generate_session_report.py` | Generates session_report.html from JSON | `python generate_session_report.py [json_file]` |
| `generate_failure_report.py` | Generates failure_report.html from JSON | `python generate_failure_report.py [json_file]` |
| `unified_generator.py` | Generates HTML with 9 consequence styles | `python unified_generator.py [style] [json_file]` |
| `batch_generator.py` | **NEW** - Generate single, multiple, or all styles | See below |
| `batch_recorder.py` | **NEW** - Record single, multiple, or all to MP4 | See below |

### Batch Generator Usage
```bash
cd generators
python batch_generator.py              # Generate all 9 styles
python batch_generator.py -l           # List available styles
python batch_generator.py ultimate     # Generate single style
python batch_generator.py neon cosmic mystical  # Generate multiple
python batch_generator.py all         # Generate all
```

### Batch Recorder Usage
```bash
cd generators
python batch_recorder.py              # Record all to MP4
python batch_recorder.py -l           # List available templates
python batch_recorder.py ultimate     # Record single template
python batch_recorder.py neon cosmic # Record multiple
python batch_recorder.py all         # Record all
```

---

## RECORDERS (recorders/)

| File | Records | Output |
|------|---------|--------|
| `record_ultimate.js` | consequences_ultimate.html | consequences_ultimate_scrolling.mp4 |
| `record_failure.js` | failure_report.html | failure_report_scrolling.mp4 |
| `record_steampunk.js` | session_steampunk.html | session_steampunk_scrolling.mp4 |

**Usage:**
```bash
node record_ultimate.js
```

---

## VIDEOS (videos/)

| File | Source | Size |
|------|--------|------|
| `consequences_ultimate_scrolling.mp4` | consequences_ultimate.html | ~27MB |
| `session_report_scrolling.mp4` | session_report.html | ~14MB |
| `failure_report_scrolling.mp4` | failure_report.html | ~8MB |
| `session_steampunk_scrolling.mp4` | session_steampunk.html | ~232KB |

---

## ARTIFACTS (../ARTIFACTS/)

| Artifact | Description | Source |
|----------|-------------|--------|
| [`native_intelligence_3_14t.html`](../ARTIFACTS/native_intelligence_3_14t.html) | Curative Psychology for Python 3.14t — 15 prioritized actions from CAIN framework analysis of 75 panel screenshots | CAIN Accountability System · `output_cain.html` |
| [`panel_dataset.json`](../ARTIFACTS/panel_dataset.json) | Structured OCR dataset of all 75 CAIN panel screenshots with extracted text | 75 panel screenshots → Tesseract OCR |
| [`INDEX.md`](../ARTIFACTS/INDEX.md) | Artifact directory index and pipeline reference | — |

### Pipeline Reference
```
MONITOR → LEARN 3.14t → INFER → ARTIFACT
         ↑                    ↑
  MetaWormSpawner        llama-server
           ↓                    ↓
      Free-Threaded       Local LLM
      Concurrent Pool    Inferencing
                              ↓
                         HORUS · BIBL · POINDEXTER · PAMS
```

---

## DOCUMENTATION (documentation/)

| File | Description |
|------|-------------|
| `README.md` | Full documentation with instructions |
| `SESSION_TEMPLATE.txt` | Original template text |

---

## Quick Start

### Generate HTML from JSON
```bash
cd Desktop/SESSION_TEMPLATE
python generators/generate_session_report.py data/session_data.json
python generators/generate_failure_report.py data/failure_data.json
```

### Record HTML to MP4
```bash
cd Desktop/SESSION_TEMPLATE
npm install puppeteer puppeteer-screen-recorder
node recorders/record_ultimate.js
```

### View Templates
Open any HTML file in `templates/` folder in a browser.

---

## Visual Themes

1. **Ultimate** (consequences_ultimate.html)
   - Nebula, stars, cosmic dust, shooting stars, orbiting particles
   - Rainbow animations, panel vibrate, lightning effects
   - Badge tags, result indicators, category tags

2. **Steampunk** (session_steampunk.html)
   - Gears, brass, copper colors
   - Steam animations, mechanical elements

3. **Neon** (consequences_neon.html)
   - Cyan/Magenta color scheme
   - Glow effects

4. **Glass** (consequences_glass.html)
   - Frosted glass panels
   - Subtle gradients

5. **Cosmic** (consequences_cosmic.html)
   - Purple theme with stars
   - Deep space atmosphere

---

## Order of Operations (CRITICAL)

When creating deliverables:

1. **Read/Review** - Understand requirements first
2. **Document** - List failures/explicit issues
3. **Show First** - Display HTML before action
4. **Wait** - Get user feedback
5. **Ask** - "Generate MP4?"
6. **Execute** - Only after confirmation

---

Generated: 2026-02-18
