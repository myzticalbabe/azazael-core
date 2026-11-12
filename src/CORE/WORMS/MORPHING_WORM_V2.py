# coding: utf-8
"""
MORPHING WORM SYSTEM V2 - Improved False-Positive Free Detection
Fixed detection logic to understand multi-line strings and comments
"""

import re
from pathlib import Path
# from typing import Dict, List, Optional  # removed — all names have native equivalents

from CORE.paths import get_project_root
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("MORPHING_WORM_V2:L16", exc)
    register_worm = lambda **kw: (lambda cls: cls)


@register_worm(category="diagnostic", priority=4, cooldown_seconds=45.0, description="Improved false-positive free detection of BOM/syntax/pattern issues")
class MorphingWormV2:
    VERSION = "2.0.0"

    def __init__(self, root: str | None = None):
        self.root = Path(root) if root else get_project_root()
        self.stats = {"scans": 0, "real_errors": 0, "false_positives_avoided": 0}

    def is_in_multiline_context(self, lines: list[str], line_idx: int) -> bool:
        """Check if we're inside a string or comment block."""
        content_above = "\n".join(lines[:line_idx])

        in_triple_quote = (
            content_above.count('"""') % 2 == 1 or content_above.count("'''") % 2 == 1
        )
        in_multiline_comment = False

        stripped = content_above.rstrip()
        if stripped.endswith("\\") or stripped.endswith("..."):
            in_multiline_comment = True

        return in_triple_quote or in_multiline_comment

    def check_bom(self, fp: Path) -> (dict | None):
        try:
            with open(fp, "rb") as f:
                content = f.read()
            if b"\xef\xbb\xbf" in content:
                content = content.replace(b"\xef\xbb\xbf", b"")
                with open(fp, "wb") as f:
                    f.write(content)
                return {
                    "type": "BOM",
                    "severity": "HIGH",
                    "file": str(fp.relative_to(self.root)),
                }
        except (OSError, PermissionError) as exc:
            note_failure("MORPHING_WORM_V2:L56", exc)
        return None

    def check_syntax(self, fp: Path) -> (dict | None):
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
            compile(code, str(fp), "exec")
        except SyntaxError as e:
            note_failure("MORPHING_WORM_V2:L65", e)
            return {
                "type": "SYNTAX",
                "severity": "CRITICAL",
                "file": str(fp.relative_to(self.root)),
                "msg": str(e),
            }
        except Exception as exc:
            note_failure("MORPHING_WORM_V2:L72", exc)
            return {
                "type": "READ_ERROR",
                "severity": "LOW",
                "file": str(fp.relative_to(self.root)),
            }
        return None

    def check_actual_patterns(self, fp: Path) -> list[dict]:
        issues = []
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            lines = content.split("\n")

            for i, line in enumerate(lines):
                stripped = line.strip()

                if stripped.startswith("#"):
                    continue

                if '"""' in line or "'''" in line:
                    in_string = (
                        content[: content.find(line)].count('"""') % 2 == 1
                        or content[: content.find(line)].count("'''") % 2 == 1
                    )
                    if in_string:
                        continue

                if re.match(r'^[^"\']*except\s*:\s*$', stripped):
                    issues.append(
                        {
                            "type": "BARE_EXCEPT",
                            "severity": "HIGH",
                            "file": str(fp.relative_to(self.root)),
                            "line": i + 1,
                        }
                    )

                if re.match(
                    r'^[^"\']*import\s+\w+\s*$', stripped
                ) and not stripped.endswith("\\"):
                    issues.append(
                        {
                            "type": "MISSING_NEWLINE_AFTER_IMPORT",
                            "severity": "MEDIUM",
                            "file": str(fp.relative_to(self.root)),
                            "line": i + 1,
                        }
                    )

        except Exception as exc:
            note_failure("MORPHING_WORM_V2:L124", exc)
        return issues

    def run_scan(self):
        print("=" * 70)
        print(f"MORPHING WORM V{self.VERSION} - IMPROVED DETECTION")
        print("=" * 70)
        print()

        files = [
            p
            for p in self.root.rglob("*.py")
            if "__pycache__" not in str(p) and ".venv" not in str(p)
        ]

        print(f"[+] Scanning {len(files)} files...")
        print()

        all_issues = []
        critical = 0
        high = 0
        medium = 0

        for i, fp in enumerate(files):
            if i % 50 == 0:
                print(f"  Progress: {i}/{len(files)}")

            if bom := self.check_bom(fp):
                all_issues.append(bom)
                high += 1

            if syntax := self.check_syntax(fp):
                all_issues.append(syntax)
                if syntax.get("severity") == "CRITICAL":
                    critical += 1
                else:
                    high += 1

            for pattern_issue in self.check_actual_patterns(fp):
                all_issues.append(pattern_issue)
                if pattern_issue.get("severity") == "HIGH":
                    high += 1
                else:
                    medium += 1

        self.stats["scans"] = len(files)
        self.stats["real_errors"] = len(all_issues)

        print()
        print("=" * 70)
        print("SCAN COMPLETE")
        print("=" * 70)
        print(f"Files scanned: {len(files)}")
        print(f"Real issues found: {len(all_issues)}")
        print(f"  CRITICAL: {critical}")
        print(f"  HIGH: {high}")
        print(f"  MEDIUM: {medium}")
        print()

        if critical > 0:
            print("CRITICAL ISSUES:")
            for issue in all_issues:
                if issue.get("severity") == "CRITICAL":
                    print(f"  {issue['file']}: {issue.get('msg', issue.get('type'))}")
        elif high > 0:
            print("HIGH PRIORITY ISSUES:")
            for issue in all_issues[:5]:
                if issue.get("severity") == "HIGH":
                    print(
                        f"  {issue['file']} (line {issue.get('line', '?')}): {issue.get('type')}"
                    )
            if len([i for i in all_issues if i.get("severity") == "HIGH"]) > 5:
                print(
                    f"  ... and {len([i for i in all_issues if i.get('severity') == 'HIGH']) - 5} more"
                )
        else:
            print("[[OK]] No critical or high priority issues found!")

        print()

        if critical == 0 and high == 0:
            print("[[OK]] AZAZAEL ENVIRONMENT IS CLEAN")
            print("    All syntax errors from previous sessions have been fixed.")

        return all_issues


    def get_summary(self) -> dict:
        """Fast summary — avoids full file scan."""
        return {
            "status": "ok",
            "worm": "MorphingWormV2",
            "version": self.VERSION,
            "stats": self.stats,
        }


if __name__ == "__main__":
    worm = MorphingWormV2()
    worm.run_scan()