# CAIN Session Changes — June 6, 2026

## Pipeline Fixes

### inject_session_cain.py
- **6th incident added** (June 6): "AI Re-Interpreted Instructions Instead of Following Pipeline"
  - Banner: "6 Incidents, ~6 Hours Lost"
  - Web nodes: Re-Interpreted Instructions, Built Wrong Pipeline Video
  - Financial ledger: +$270 June 6 line, total: $590
  - Consequence: SYSTEM INSTRUCTIONS Rule 0 Created
  - Obfuscation: Added re-interpretation pattern note
- **Fix**: May→June replacement sequencing corrected (May creates intermediate strings, June overwrites)
- **Fix**: `$5.25+` → `<strong>$5.25+</strong>` — template has `<strong>` tags (pre-existing bug)
- **Fix**: `sys.stdout.reconfigure('utf-8')` for Windows emoji safety
- **Result**: 50/50 replacements applied, zero warnings

### azazael-cain-pipeline.md
- **720p standard removed**, 1080p HEVC set as default
- All encoding commands updated:
  - HEVC: `libx265 -crf 28 -preset medium -tune grain -tag:v hvc1`
  - H.264: `libx264 -crf 23 -preset medium -tune grain`
  - WMV: `wmv2 -b:v 4000k`
- Anti-patterns table: "Use 720p as standard" → NEVER, "Use 1080p" → ALWAYS

### record_session_cain.js
- **Removed**: `--disable-software-rasterizer` (conflicted with `--disable-gpu` on Windows)
- **Removed**: `--disable-gpu` (modern headless Chrome doesn't need it, GPU compositing helps recording)
- **Added**: Retry logic (MAX_RETRIES=3, crashed once → succeeded on retry)
- **Removed**: Unused `stableFrames` dead code
- **Added**: Progress logging every 1000 frames
- **Added**: `page.on('console')` listener to forward browser logs to terminal

### SYSTEM_INSTRUCTIONS.md
- **Added**: Directive 0 — FOLLOW INSTRUCTIONS LITERALLY, DO NOT RE-INTERPRET

---

## Deliverables

| File | Size | Format | Duration |
|------|------|--------|----------|
| `Desktop/CAIN_1080p_hevc.mp4` | 345 MB | HEVC 1080p, CRF 28 | ~47 min |
| `Desktop/CAIN_1080p.wmv` | 1.4 GB | WMV2 4000k | ~47 min |
| `Desktop/CAIN_2.5x_1080p.mp4` | 205 MB | HEVC 1080p, 2.5x speed | ~18:45 |

## Removed

| File | Reason |
|------|--------|
| `CAIN_session_final.mp4` (raw 265 MB) | Encoded versions exist |
| `CAIN_2.5x.mp4` (old 720p 120 MB) | Superseded by 1080p version |

## Files Modified

- `src/CODEX/inject_session_cain.py`
- `skills/azazael-cain-pipeline.md`
- `src/CODEX/record_session_cain.js`
- `SYSTEM_INSTRUCTIONS.md`
