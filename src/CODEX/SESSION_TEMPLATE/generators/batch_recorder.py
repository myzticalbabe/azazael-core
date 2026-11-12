#!/usr/bin/env python3
"""
Batch Recorder - Record templates to MP4 individually, in multiples, or all sequentially
Usage:
  python batch_recorder.py              # Record all templates
  python batch_recorder.py ultimate     # Record single template
  python batch_recorder.py neon cosmic  # Record multiple templates
  python batch_recorder.py -l           # List available templates
"""

import subprocess
import sys
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

TEMPLATES = {
    "ultimate": {
        "html": "consequences_ultimate.html",
        "output": "consequences_ultimate_scrolling.mp4",
    },
    "neon": {"html": "output_neon.html", "output": "output_neon_scrolling.mp4"},
    "cosmic": {"html": "output_cosmic.html", "output": "output_cosmic_scrolling.mp4"},
    "glass": {"html": "output_glass.html", "output": "output_glass_scrolling.mp4"},
    "report": {"html": "output_report.html", "output": "output_report_scrolling.mp4"},
    "mystical": {
        "html": "output_mystical.html",
        "output": "output_mystical_scrolling.mp4",
    },
    "occult": {"html": "output_occult.html", "output": "output_occult_scrolling.mp4"},
    "ethereal": {
        "html": "output_ethereal.html",
        "output": "output_ethereal_scrolling.mp4",
    },
    "grimoire": {
        "html": "output_grimoire.html",
        "output": "output_grimoire_scrolling.mp4",
    },
    "failure": {
        "html": "failure_report.html",
        "output": "failure_report_scrolling.mp4",
    },
    "session": {
        "html": "session_report.html",
        "output": "session_report_scrolling.mp4",
    },
}

SCRIPT_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = SCRIPT_DIR / "templates"
RECORDERS_DIR = SCRIPT_DIR / "recorders"
VIDEOS_DIR = SCRIPT_DIR / "videos"


def list_templates():
    print("Available templates to record:")
    for i, (key, info) in enumerate(TEMPLATES.items(), 1):
        print(f"  {i}. {key:12} -> {info['output']}")


def record_single(template_key):
    if template_key not in TEMPLATES:
        print(f"Unknown template: {template_key}")
        return False

    template = TEMPLATES[template_key]
    html_file = TEMPLATES_DIR / template["html"]
    output_file = VIDEOS_DIR / template["output"]

    if not html_file.exists():
        print(f"  X HTML file not found: {html_file}")
        return False

    recorder_script = None
    if template_key == "ultimate":
        recorder_script = RECORDERS_DIR / "record_ultimate.js"
    elif template_key == "failure":
        recorder_script = RECORDERS_DIR / "record_failure.js"
    else:
        recorder_script = RECORDERS_DIR / "record_generic.js"

    if not recorder_script.exists():
        print(f"  X Recorder script not found: {recorder_script}")
        return False

    print(f"Recording: {template_key} -> {template['output']}")

    # Pre-extract values to avoid f-string backslash (anti-pattern #3)
    html_path = html_file.as_posix().replace("C:", "")
    backslash = "\\"
    output_path = output_file.as_posix().replace("C:", "").replace("/", backslash)
    template_output = template["output"]

    with open(recorder_script, "w", encoding="utf-8") as f:
        f.write(f"""const puppeteer = require('puppeteer');
const {{ PuppeteerScreenRecorder }} = require('puppeteer-screen-recorder');

(async () => {{
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  
  await page.setViewport({{ width: 1920, height: 1080 }});
  
  // CAIN Standard: H.264 High profile, CRF 23, medium preset, 4 Mbps
  const recorder = new PuppeteerScreenRecorder(page, {{
    videoCrf: 23,
    videoPreset: 'medium',
    videoPixelFormat: 'yuv420p',
    videoBitrate: 4000,
  }});
  
  await page.goto('file:///{html_path}');
  await recorder.start('{output_path}');
  
  let scrollComplete = false;
  
  await page.evaluate(() => {{
    const scrollStep = 30;
    const scrollDelay = 600;
    
    const scrollInterval = setInterval(() => {{
      if (window.scrollY + window.innerHeight >= document.body.scrollHeight - 50) {{
        scrollComplete = true;
        clearInterval(scrollInterval);
        return;
      }}
      window.scrollBy(0, scrollStep);
    }}, scrollDelay);
  }});
  
  while (!scrollComplete) {{
    await new Promise(r => setTimeout(r, 500));
    const isComplete = await page.evaluate(() => {{
      return window.scrollY + window.innerHeight >= document.body.scrollHeight - 50;
    }});
    if (isComplete) break;
  }}
  
  await new Promise(r => setTimeout(r, 2000));
  
  await recorder.stop();
  await browser.close();
  
  console.log('Video saved: {template_output}');
}})();
""")

    result = subprocess.run(
        ["node", str(recorder_script)],
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        cwd=str(RECORDERS_DIR),
    timeout=1800)

    if result.returncode == 0:
        print(f"  [OK] Recorded: {template['output']}")
        return True
    else:
        print(f"  [FAIL] Error: {result.stderr}")
        return False


def record_all():
    print(f"Recording all {len(TEMPLATES)} templates...\n")
    success = 0
    failed = []

    for key in TEMPLATES:
        if record_single(key):
            success += 1
        else:
            failed.append(key)

    print(f"\n{'=' * 50}")
    print(f"Results: {success}/{len(TEMPLATES)} templates recorded")

    if failed:
        print(f"Failed: {', '.join(failed)}")
    else:
        print("All recordings complete!")

    return success, failed


def main():
    if len(sys.argv) == 1:
        record_all()
        return

        if sys.argv[1] == "-l" or sys.argv[1] == "--list":
            list_templates()
            return

            args = sys.argv[1:]

            if args[0] == "all":
                record_all()
                return

                invalid = [a for a in args if a not in TEMPLATES]
                if invalid:
                    print(f"Unknown templates: {', '.join(invalid)}")
                    print(f"Valid: {', '.join(TEMPLATES.keys())}")
                    return

                    print(f"Recording {len(args)} templates: {', '.join(args)}\n")
                    success = 0
                    for template_key in args:
                        if record_single(template_key):
                            success += 1

                            print(f"\nResults: {success}/{len(args)} templates recorded")


if __name__ == "__main__":
    main()
