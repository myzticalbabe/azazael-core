# SESSION_TEMPLATE - Visual Documentation System

A comprehensive HTML report generation system for documenting AI assistance sessions with multiple themed visual templates.

## Quick Start

### Requirements
- Python 3.8+
- No external dependencies (uses only stdlib)

### Installation
1. Copy the `SESSION_TEMPLATE` folder to your desired location
2. No installation needed - just run the scripts

### Usage

#### 1. Generate a Report
```bash
cd SESSION_TEMPLATE/generators
python unified_generator.py [style]
```

**Available Styles:**
- `ultimate` - Neon rainbow with animated effects
- `neon` - Neon glass effects with pink (#ff69b4)
- `cosmic` - Cosmic space theme
- `glass` - Glassmorphism
- `report` - Professional report
- `mystical` - Mystical runes and purple glow
- `occult` - Dark occult theme
- `ethereal` - Aurora spirits
- `grimoire` - Old book/parchment
- `firehorse` - Fire horse theme
- `cain` - Full accountability, causal chains, financial/ethical ledger

**Example:**
```bash
python unified_generator.py cain
```

#### 2. Update Session Data
Edit `SESSION_TEMPLATE/data/session_data.json` with your session information:

```json
{
  "sessionReport": {
    "title": "Your Session Title",
    "date": "2026-02-18",
    "orderOfOperations": [
      {"step": 1, "command": "your command", "description": "what happened"}
    ],
    "documentedFailures": [
      {"id": "F1", "issue": "failure description"}
    ],
    "phases": [
      {"id": 1, "name": "Phase Name", "steps": [1,2,3], "color": "red"}
    ]
  }
}
```

#### 3. Output
Generated HTML files are saved to:
- `SESSION_TEMPLATE/data/output_[style].html`

#### 4. Batch Generate All Styles
```bash
python batch_generator.py
```

## File Structure

```
SESSION_TEMPLATE/
├── generators/
│   ├── unified_generator.py    # Main generator (all styles)
│   ├── batch_generator.py      # Generate all styles at once
│   ├── batch_recorder.py      # Record HTML to MP4 video
│   └── generate_session_report.py
├── data/
│   ├── session_data.json      # EDIT THIS - your session data
│   └── output_*.html         # Generated reports
├── templates/
│   └── *.html                 # Template samples
├── recorders/
│   └── *.js                   # Browser automation scripts
├── videos/
│   └── *.mp4                  # Recorded videos
└── documentation/
    └── README.md
```

## Cain Template Features

The `cain` style is the most comprehensive with:
- Complete Request vs Delivery Comparison
- Stakeholder Impact Matrix (12 parties)
- Financial Money Snake (serpent through every pocket)
- Cost Accounting (direct, hidden, opportunity, externalized)
- Legal Exposure Analysis (FTC, GDPR, DSA)
- Before/After Metrics
- Action Item Audit
- Methodological Transparency
- DSM-5 Spectrum Disorder Analysis
- Psychological Frameworks

## Recording to Video

To record an HTML report as an MP4:

1. Open HTML in browser
2. Use browser's "Record" feature or:
   ```bash
   python batch_recorder.py
   ```

## Customization

### Add New Styles
Edit `unified_generator.py`:
1. Add style name to `STYLES` dict
2. Add CSS to `get_style_css()` function
3. Add content to `generate_body_content()` function

### Change Colors
Search for color codes in CSS strings:
- `#ff69b4` - Hot pink
- `#ff1493` - Deep pink
- `#00ffff` - Cyan
- `#ff0000` - Red

## Dependencies

**Required (stdlib only):**
- json
- sys
- pathlib
- datetime

**Optional (for video recording):**
- Browser with screen recording capability

## License

This is a custom documentation tool. Use as needed.

## Author

Created for AI session documentation and accountability tracking.
