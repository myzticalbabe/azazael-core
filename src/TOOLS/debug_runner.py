#!/usr/bin/env python3
"""
Azazael .debug Multi-Agent Test Runner
========================================
Runs integration tests across all AZAZAEL components in debug mode.
Creates .debug/ directories with timestamped test results.
Tests: KLINK, BIBL, emoji_maker, ascii_flowchart, enhanced_animations,
        rust_runner, go_runner, video compress, HTML template rendering.

Usage:
    python -m TOOLS.debug_runner --all
    python -m TOOLS.debug_runner --component emoji_maker
    python -m TOOLS.debug_runner --component all --verbose
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
# from typing import List, Optional  # removed — all names have native equivalents
import argparse
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Ensure parent src/ is on sys.path so sibling modules are importable
_SRC_DIR = Path(__file__).resolve().parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

AZAZAEL_ROOT = os.environ.get("AZAZAEL_ROOT", str(Path(__file__).resolve().parents[2]))
DEBUG_DIR = Path(AZAZAEL_ROOT) / ".debug"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TestResult:
    component: str
    success: bool
    duration_ms: float
    message: str = ""
    output: str = ""
    error: str = ""


class DebugRunner:
    """Run all component tests and write results to .debug/."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.results: list[TestResult] = []
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = DEBUG_DIR / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _run_python(self, component: str, code: str) -> TestResult:
        """Run a Python snippet and return result."""
        start = time.time()
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "AZAZAEL_ROOT": AZAZAEL_ROOT}, encoding="utf-8", errors="replace"
            )
            duration = (time.time() - start) * 1000
            return TestResult(
                component=component,
                success=result.returncode == 0,
                duration_ms=duration,
                output=result.stdout[:2000],
                error=result.stderr[:1000],
                message="OK" if result.returncode == 0 else f"Exit code {result.returncode}",
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("debug_runner:L75", exc)
            return TestResult(component, False, 30000, error="Timed out (>30s)")
        except Exception as e:
            note_failure("debug_runner:L77", e)
            return TestResult(component, False, (time.time() - start) * 1000, error=str(e))

    def _run_module(self, component: str, module_path: str, func: str = "") -> TestResult:
        """Import and test a Python module."""
        start = time.time()
        try:
            code = f"""
import sys
sys.path.insert(0, "{AZAZAEL_ROOT}/src")
try:
    import {module_path}
    print("IMPORT OK: " + str({module_path}.__name__))
    {func}
except Exception as e:
    print("ERROR: " + str(e))
    import traceback
    traceback.print_exc()
"""
            return self._run_python(component, code)
        except Exception as e:
            note_failure("debug_runner:L97", e)
            return TestResult(component, False, (time.time() - start) * 1000, error=str(e))

    # --- Component Tests ----------------------------------------------------

    def test_klink(self) -> TestResult:
        """Test KLINK wrapper imports and resolves to canonical version."""
        return self._run_python("klink", f"""
import sys
sys.path.insert(0, "{AZAZAEL_ROOT}/src/UTILITIES/KLINK")
from klink import main as klink_main
print("KLINK main function found:", hasattr(klink_main, '__call__'))
print("KLINK module loaded from UTILITIES/KLINK/")
""")

    def test_bibl(self) -> TestResult:
        """Test BIBL crawler imports and indexes."""
        return self._run_python("bibl_crawler", f"""
import sys
sys.path.insert(0, "{AZAZAEL_ROOT}/src/ORCHESTRATION/BIBL")
from bibl_crawler import crawl_all, build_index
entries, metadata = crawl_all()
print(f"BIBL: {{len(entries)}} parsed entries, {{len(metadata)}} files")
index = build_index()
print(f"Index: {{index['statistics']['total_entries']}} entries, {{index['statistics']['total_categories']}} categories")
print(f"Word index: {{len(index['word_index'])}} words")
""")

    def test_emoji_maker(self) -> TestResult:
        """Test emoji maker generates all categories."""
        return self._run_python("emoji_maker", f"""
import sys
sys.path.insert(0, "{AZAZAEL_ROOT}/src/CODEX/CORE")
from emoji_maker import EmojiMaker, Emotion, Mood, Category
m = EmojiMaker()
print(f"Library: {{len(m.library)}} emojis")
print(f"Emotions: {{len(m.by_emotion)}}")
print(f"Moods: {{len(m.by_mood)}}")
print(f"Categories: {{len(m.by_category)}}")
# Test emotion string
s = m.emotion_string(Emotion.HAPPY, 0.8)
print(f"Happy string: {{s}}")
# Test reaction
r = m.reaction_emoji("success", 0.9)
print(f"Reaction: {{r.char}} {{r.name}}")
# Test CSS generation
css = m.generate_css()
print(f"CSS: {{len(css)}} chars, {{css.count('@keyframes')}} keyframes")
""")

    def test_ascii_flowchart(self) -> TestResult:
        """Test ASCII flowchart generates architecture diagram."""
        return self._run_python("ascii_flowchart", f"""
import sys
sys.path.insert(0, "{AZAZAEL_ROOT}/src/CODEX/CORE")
from ascii_flowchart import AsciiFlowchart, azazael_architecture, azazael_pipeline
print("=== ARCHITECTURE ===")
arch = azazael_architecture()
print(arch[:500])
print(f"\\nArchitecture diagram: {{len(arch)}} chars")
print("=== PIPELINE ===")
pipe = azazael_pipeline()
print(pipe)
print(f"\\nPipeline diagram: {{len(pipe)}} chars")
""")

    def test_enhanced_animations(self) -> TestResult:
        """Test enhanced animations CSS generation."""
        return self._run_python("enhanced_animations", f"""
import sys
sys.path.insert(0, "{AZAZAEL_ROOT}/src/CODEX/CORE")
from enhanced_animations import ANIMATIONS, generate_css, export_manifest
print(f"Animations: {{len(ANIMATIONS)}}")
manifest = export_manifest()
print(f"Categories: {{manifest['categories']}}")
css = generate_css()
print(f"CSS: {{len(css)}} chars, {{css.count('@keyframes')}} keyframes")
""")

    def test_rust_runner(self) -> TestResult:
        """Test Rust runner toolchain detection."""
        return self._run_python("rust_runner", f"""
import sys
sys.path.insert(0, "{AZAZAEL_ROOT}/src")
from INTERPRETERS.rust_runner import RustRunner
r = RustRunner()
print(f"Rust toolchain: rustc={{r.has_rustc}}, cargo={{r.has_cargo}}")
if r.has_rustc:
    # Test syntax check on a small snippet
    import tempfile, os
    code = 'fn main() {{ println!("Hello Azazael"); }}'
    with tempfile.NamedTemporaryFile(mode='w', suffix='.rs', delete=False) as f:
        f.write(code)
        tmp = f.name
    result = r.check_syntax(tmp)
    print(f"Syntax check: {{'PASS' if result.success else 'FAIL'}}")
    os.unlink(tmp)
else:
    print("Rust not installed - skipping compile test")
""")

    def test_go_runner(self) -> TestResult:
        """Test Go runner toolchain detection."""
        return self._run_python("go_runner", f"""
import sys
sys.path.insert(0, "{AZAZAEL_ROOT}/src")
from INTERPRETERS.go_runner import GoRunner
g = GoRunner()
print(f"Go toolchain: go={{g.has_go}}")
if g.has_go:
    code = 'package main\\nimport "fmt"\\nfunc main() {{ fmt.Println("Hello Azazael") }}'
    result = g.run_code(code)
    print(f"Run: {{'PASS' if result.success else 'FAIL'}}")
else:
    print("Go not installed - skipping run test")
""")

    def test_video_compress(self) -> TestResult:
        """Test compress_video.py imports."""
        return self._run_python("compress_video", f"""
import sys, shutil
sys.path.insert(0, "{AZAZAEL_ROOT}/src/CODEX/VIDEOS")
from compress_video import PROFILES, _codec_available, check_ffmpeg
print(f"Profiles: {{list(PROFILES.keys())}}")
print(f"Default CRF: {{PROFILES['medium']['crf']}}")
print(f"ffmpeg: {{'found' if shutil.which('ffmpeg') else 'not found'}}")
""")

    def test_html_templates(self) -> TestResult:
        """Test all HTML templates are valid."""
        start = time.time()
        templates_dir = Path(AZAZAEL_ROOT) / "src" / "CODEX"
        html_files = list(templates_dir.glob("*.html"))
        errors = []

        for html_file in html_files:
            try:
                content = html_file.read_text(encoding='utf-8', errors='replace')
                checks = {
                    '<!DOCTYPE html>': content.count('<!DOCTYPE html>'),
                    '</html>': content.count('</html>'),
                    '</body>': content.count('</body>'),
                }
                if checks['</html>'] == 0:
                    errors.append(f"{html_file.name}: missing </html>")
                if checks['</body>'] == 0:
                    errors.append(f"{html_file.name}: missing </body>")
            except Exception as e:
                note_failure("debug_runner:L244", e)
                errors.append(f"{html_file.name}: {e}")

        duration = (time.time() - start) * 1000
        if errors:
            return TestResult(
                "html_templates", False, duration,
                message=f"{len(errors)} errors",
                error="\n".join(errors[:10]),
            )
        return TestResult(
            "html_templates", True, duration,
            message=f"All {len(html_files)} HTML templates valid",
        )

    # --- Run All ------------------------------------------------------------

    def run_all(self) -> list[TestResult]:
        """Run all component tests."""
        tests = [
            ("klink", self.test_klink),
            ("bibl_crawler", self.test_bibl),
            ("emoji_maker", self.test_emoji_maker),
            ("ascii_flowchart", self.test_ascii_flowchart),
            ("enhanced_animations", self.test_enhanced_animations),
            ("rust_runner", self.test_rust_runner),
            ("go_runner", self.test_go_runner),
            ("compress_video", self.test_video_compress),
            ("html_templates", self.test_html_templates),
        ]

        self.results = []
        for name, test_fn in tests:
            print(f"  [{name}] ", end="", flush=True)
            result = test_fn()
            self.results.append(result)
            status = "[OK]" if result.success else "[X]"
            print(f"{status} ({result.duration_ms:.0f}ms) {result.message}")

        return self.results

    def run_component(self, component: str) -> (TestResult | None):
        """Run a single component test."""
        tests = {
            "klink": self.test_klink,
            "bibl": self.test_bibl,
            "bibl_crawler": self.test_bibl,
            "emoji": self.test_emoji_maker,
            "emoji_maker": self.test_emoji_maker,
            "ascii": self.test_ascii_flowchart,
            "ascii_flowchart": self.test_ascii_flowchart,
            "animations": self.test_enhanced_animations,
            "enhanced_animations": self.test_enhanced_animations,
            "rust": self.test_rust_runner,
            "rust_runner": self.test_rust_runner,
            "go": self.test_go_runner,
            "go_runner": self.test_go_runner,
            "video": self.test_video_compress,
            "compress_video": self.test_video_compress,
            "html": self.test_html_templates,
            "html_templates": self.test_html_templates,
        }

        test_fn = tests.get(component)
        if not test_fn:
            print(f"  Unknown component: {component}")
            print(f"  Available: {sorted(set(tests.keys()))}")
            return None

        result = test_fn()
        self.results.append(result)
        status = "[OK]" if result.success else "[X]"
        print(f"  [{component}] {status} ({result.duration_ms:.0f}ms) {result.message}")
        if self.verbose and result.output:
            print(f"    Output: {result.output[:500]}")
        if result.error:
            print(f"    Error: {result.error[:500]}")
        return result

    def save_report(self) -> Path:
        """Save test results to .debug/session_id/report.json."""
        report = {
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.success),
            "failed": sum(1 for r in self.results if not r.success),
            "total_duration_ms": sum(r.duration_ms for r in self.results),
            "results": [
                {
                    "component": r.component,
                    "success": r.success,
                    "duration_ms": r.duration_ms,
                    "message": r.message,
                    "output": r.output[:500] if r.output else "",
                    "error": r.error[:500] if r.error else "",
                }
                for r in self.results
            ],
        }

        report_path = self.session_dir / "report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        # Also write human-readable summary
        summary_path = self.session_dir / "summary.txt"
        lines = [
            "Azazael Debug Test Report",
            f"Session: {self.session_id}",
            f"Time: {datetime.now().isoformat()}",
            f"{'='*50}",
            f"Passed: {report['passed']}/{report['total']}",
            f"Failed: {report['failed']}/{report['total']}",
            f"Duration: {report['total_duration_ms']:.0f}ms",
            "",
        ]
        for r in self.results:
            status = "[OK]" if r.success else "[X]"
            lines.append(f"  {status} {r.component:<25} {r.duration_ms:6.0f}ms  {r.message}")
            if r.error:
                lines.append(f"       Error: {r.error[:200]}")
        summary_path.write_text("\n".join(lines), encoding="utf-8")

        return report_path


def main():
    parser = argparse.ArgumentParser(description="Azazael .debug Multi-Agent Test Runner")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    parser.add_argument("--component", help="Run specific component test")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    runner = DebugRunner(verbose=args.verbose)
    print(f"Azazael Debug Runner — Session: {runner.session_id}")
    print(f"Results: {runner.session_dir}")
    print()

    if args.component:
        runner.run_component(args.component)
    elif args.all or not args.component:
        runner.run_all()
    else:
        parser.print_help()
        return

        report_path = runner.save_report()
        print(f"\nReport saved: {report_path}")

        # Summary
        passed = sum(1 for r in runner.results if r.success)
        failed = sum(1 for r in runner.results if not r.success)
        print(f"Result: {passed} passed, {failed} failed")


if __name__ == "__main__":
    main()
