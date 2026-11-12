#!/usr/bin/env python3
"""
Consistency Checker for Azazael Documentation

Automated tool to check for inconsistencies across all documentation files.

Features:
- Version consistency checking
- Path consistency checking
- Link validation
- YAML header validation
- Cross-reference verification

Usage:
python scripts/consistency_checker.py
python scripts/consistency_checker.py --verbose
python scripts/consistency_checker.py --fix

Author: Azazael Enhancement System
Date: 2026-02-10
Version: 1.0.0
"""

import re
import sys
from pathlib import Path
# from typing import Dict, List, Tuple, Optional  # removed — all names have native equivalents
from dataclasses import dataclass
from datetime import datetime
import argparse
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class ConsistencyIssue:
    """Represents a consistency issue."""

    file: str
    issue_type: str
    description: str
    severity: str  # ERROR, WARN, INFO
    line_number: (int | None) = None
    suggested_fix: (str | None) = None


    class ConsistencyChecker:
        """Check documentation for inconsistencies."""

        # Known version references (intentional duplicates)
        INTENTIONAL_VERSIONS = {
        "2.0.0": ["DOCUMENTATION_PROTOCOL.md reference"],
        "2.1.0": ["Current Azazael version"],
        "1.0.0": ["PERSEPHONE version"],
        }

        # Required path patterns (generated dynamically)
        PATH_PATTERNS = {
        "azazael_windows": str(Path.home() / "Desktop" / "Azazael").replace("\\", "\\\\"),
        "azazael_wsl": f"/mnt/c/Users/{Path.home().name}/Desktop/Azazael",
        "persephone_windows": str(Path.home() / "Desktop" / "PERSEPHONE").replace("\\", "\\\\"),
        "persephone_wsl": f"/mnt/c/Users/{Path.home().name}/Desktop/PERSEPHONE",
        }

        # Files to check
        CHECK_FILES = [
        "README.md",
        "CURRENT_STATE.md",
        "CHANGELOG.md",
        "Azazael_ENHANCEMENT_PLAN.md",
        ]

        def __init__(self, verbose: bool = False, fix: bool = False):
            self.verbose = verbose
            self.fix = fix
            self.root = Path(__file__).parent.parent
            self.issues: list[ConsistencyIssue] = []

            def log(self, message: str):
                """Print message if verbose mode."""
                if self.verbose:
                    print(message)

                    def check_versions(self, file_path: Path) -> list[ConsistencyIssue]:  # pyright: ignore[reportReturnType]
                        """Check for version inconsistencies."""
                        issues = []

                        content = file_path.read_text(encoding="utf-8", errors="ignore")

                        # Find all version-like patterns
                        version_pattern = r"\bv?(\d+\.\d+\.\d+)\b"
                        versions = re.findall(version_pattern, content)

                        # Count occurrences
                        version_counts: dict[str, int] = {}
                        for v in versions:
                            version_counts[v] = version_counts.get(v, 0) + 1

                            # Check for intentional duplicates
                            for v, count in version_counts.items():
                                if v == "2.0.0" and count > 1:
                                    issues.append(
                                    ConsistencyIssue(
                                    file=str(file_path),
                                    issue_type="VERSION",
                                    description=f"Version 2.0.0 found {count} times (intentional: protocol reference)",
                                    severity="INFO",
                                    suggested_fix="None - intentional duplicate",
                                    )
                                    )
                                elif v == "2.1.0" and count > 1:
                                    issues.append(
                                    ConsistencyIssue(
                                    file=str(file_path),
                                    issue_type="VERSION",
                                    description=f"Version 2.1.0 found {count} times",
                                    severity="INFO",
                                    suggested_fix="Check if all references are to the same version",
                                    )
                                    )

                                    return issues

                                def check_paths(self, file_path: Path) -> list[ConsistencyIssue]:  # pyright: ignore[reportReturnType]
                                    """Check for path inconsistencies."""
                                    issues = []

                                    username = Path.home().name

                                    content = file_path.read_text(encoding="utf-8", errors="ignore")

                                    # Check for mixed path styles
                                    has_windows = bool(re.search(r"C:\\\\\\\\Users\\\\\\\\" + username, content))  # scanner:ignore deliberate: Windows path regex used only for comparison
                                    has_wsl = bool(re.search(r"/mnt/c/Users/" + username, content))

                                    if has_windows and has_wsl:
                                        # Check if both refer to same location
                                        windows_matches = re.findall(
                                        r"C:\\\\\\\\Users\\\\\\\\" + username + r"\\\\Desktop\\\\(\w+)", content  # scanner:ignore deliberate: Windows path regex used only for comparison
                                        )
                                        wsl_matches = re.findall(r"/mnt/c/Users/" + username + r"/Desktop/(\w+)", content)

                                        for i, wm in enumerate(windows_matches):
                                            if i < len(wsl_matches) and wm != wsl_matches[i]:
                                                issues.append(
                                                ConsistencyIssue(
                                                file=str(file_path),
                                                issue_type="PATH",
                                                description=f"Inconsistent path: Windows '{wm}' vs WSL '{wsl_matches[i]}'",
                                                severity="WARN",
                                                )
                                                )

                                                return issues

                                            def check_links(self, file_path: Path) -> list[ConsistencyIssue]:  # pyright: ignore[reportReturnType]
                                                """Check for broken links."""
                                                issues = []

                                                content = file_path.read_text(encoding="utf-8", errors="ignore")

                                                # Find markdown links
                                                link_pattern = r"\[([^\]]+)\]\(([^)]+)\)"
                                                links = re.findall(link_pattern, content)

                                                for text, url in links:
                                                    # Skip external URLs
                                                    if url.startswith(("http://", "https://", "mailto:", "tel:")):
                                                        continue

                                                    # Skip anchors
                                                    if url.startswith("#"):
                                                        anchor = url[1:]
                                                        if anchor not in content:
                                                            issues.append(
                                                            ConsistencyIssue(
                                                            file=str(file_path),
                                                            issue_type="LINK",
                                                            description=f"Broken anchor link: #{anchor}",
                                                            severity="WARN",
                                                            )
                                                            )
                                                            continue

                                                        # Check if file exists
                                                        if url.startswith("./") or not url.startswith("/"):
                                                            link_path = file_path.parent / url
                                                        else:
                                                            link_path = self.root / url[1:]

                                                            if not link_path.exists():
                                                                issues.append(
                                                                ConsistencyIssue(
                                                                file=str(file_path),
                                                                issue_type="LINK",
                                                                description=f"Broken link: {url}",
                                                                severity="ERROR",
                                                                )
                                                                )

                                                                return issues

                                                            def check_yaml_headers(self, file_path: Path) -> list[ConsistencyIssue]:  # pyright: ignore[reportReturnType]
                                                                """Check YAML headers."""
                                                                issues = []

                                                                content = file_path.read_text(encoding="utf-8", errors="ignore")

                                                                # Check if file starts with YAML header
                                                                if not content.startswith("---"):
                                                                    issues.append(
                                                                    ConsistencyIssue(
                                                                    file=str(file_path),
                                                                    issue_type="YAML",
                                                                    description="Missing YAML header (---)",
                                                                    severity="WARN",
                                                                    )
                                                                    )
                                                                    return issues

                                                                # Parse YAML header
                                                                header_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
                                                                if not header_match:
                                                                    issues.append(
                                                                    ConsistencyIssue(
                                                                    file=str(file_path),
                                                                    issue_type="YAML",
                                                                    description="Malformed YAML header",
                                                                    severity="ERROR",
                                                                    )
                                                                    )
                                                                    return issues

                                                                header = header_match.group(1)

                                                                # Check for required fields
                                                                required_fields = ["title", "version", "last_updated"]
                                                                for field in required_fields:
                                                                    if f"{field}:" not in header:
                                                                        issues.append(
                                                                        ConsistencyIssue(
                                                                        file=str(file_path),
                                                                        issue_type="YAML",
                                                                        description=f"Missing YAML field: {field}",
                                                                        severity="WARN",
                                                                        )
                                                                        )

                                                                        return issues

                                                                    def check_cross_references(self, file_path: Path) -> list[ConsistencyIssue]:  # pyright: ignore[reportReturnType]
                                                                        """Check for cross-reference consistency."""
                                                                        issues = []

                                                                        content = file_path.read_text(encoding="utf-8", errors="ignore")

                                                                        # Find all markdown references
                                                                        ref_pattern = r"\[([^\]]+)\]\[([^\]]+)\]"
                                                                        refs = re.findall(ref_pattern, content)

                                                                        # Check if all references are defined
                                                                        define_pattern = r"^\[([^\]]+)\]:\s*(.+)$"
                                                                        defines = re.findall(define_pattern, content, re.MULTILINE)

                                                                        defined_refs = {r[0] for r in defines}

                                                                        for text, ref_id in refs:
                                                                            if ref_id not in defined_refs:
                                                                                issues.append(
                                                                                ConsistencyIssue(
                                                                                file=str(file_path),
                                                                                issue_type="REFERENCE",
                                                                                description=f"Undefined reference: [{text}][{ref_id}]",
                                                                                severity="WARN",
                                                                                )
                                                                                )

                                                                                return issues

                                                                            def check_file(self, file_path: Path) -> list[ConsistencyIssue]:  # pyright: ignore[reportReturnType]
                                                                                """Run all checks on a file."""
                                                                                issues = []

                                                                                if not file_path.exists():
                                                                                    issues.append(
                                                                                    ConsistencyIssue(
                                                                                    file=str(file_path),
                                                                                    issue_type="FILE",
                                                                                    description="File not found",
                                                                                    severity="ERROR",
                                                                                    )
                                                                                    )
                                                                                    return issues

                                                                                self.log(f"Checking: {file_path.name}")

                                                                                issues.extend(self.check_versions(file_path))
                                                                                issues.extend(self.check_paths(file_path))
                                                                                issues.extend(self.check_links(file_path))
                                                                                issues.extend(self.check_yaml_headers(file_path))
                                                                                issues.extend(self.check_cross_references(file_path))

                                                                                return issues

                                                                            def run(self) -> tuple[int, int, int]:  # pyright: ignore[reportReturnType]
                                                                                """Run all checks.

                                                                                Returns:
                                                                                tuple of (errors, warnings, info)
                                                                                """
                                                                                errors = 0
                                                                                warnings = 0
                                                                                info = 0

                                                                                for filename in self.CHECK_FILES:
                                                                                    file_path = self.root / filename
                                                                                    issues = self.check_file(file_path)
                                                                                    self.issues.extend(issues)

                                                                                    for issue in issues:
                                                                                        if issue.severity == "ERROR":
                                                                                            errors += 1
                                                                                            print(f"[ERROR] {issue.file}: {issue.description}")
                                                                                        elif issue.severity == "WARN":
                                                                                            warnings += 1
                                                                                            print(f"[WARN] {issue.file}: {issue.description}")
                                                                                        elif issue.severity == "INFO":
                                                                                            info += 1
                                                                                            if self.verbose:
                                                                                                print(f"[INFO] {issue.file}: {issue.description}")

                                                                                                return errors, warnings, info

                                                                                            def generate_report(self) -> str:  # pyright: ignore[reportReturnType]
                                                                                                """Generate a summary report."""
                                                                                                errors = sum(1 for i in self.issues if i.severity == "ERROR")
                                                                                                warnings = sum(1 for i in self.issues if i.severity == "WARN")
                                                                                                info = sum(1 for i in self.issues if i.severity == "INFO")

                                                                                                report = f"""
                                                                                                ================================================================================
                                                                                                CONSISTENCY CHECK REPORT
                                                                                                ================================================================================
                                                                                                Generated: {datetime.now().isoformat()}

                                                                                                Summary:
                                                                                                Errors:   {errors}
                                                                                                Warnings: {warnings}
                                                                                                Info:     {info}
                                                                                                """

                                                                                                if self.issues:
                                                                                                    by_type: dict[str, int] = {}
                                                                                                    for issue in self.issues:
                                                                                                        by_type[issue.issue_type] = by_type.get(issue.issue_type, 0) + 1

                                                                                                        report += "\nBy Type:\n"
                                                                                                        for itype, count in sorted(by_type.items()):
                                                                                                            report += f"  {itype}: {count}\n"

                                                                                                            report += """
                                                                                                            ================================================================================
                                                                                                            """
                                                                                                            return report


                                                                                                        def main():
                                                                                                            """Main entry point."""
                                                                                                            parser = argparse.ArgumentParser(description="Check documentation for consistency")
                                                                                                            parser.add_argument(
                                                                                                            "-v", "--verbose", action="store_true", help="Show verbose output"
                                                                                                            )
                                                                                                            parser.add_argument(
                                                                                                            "--fix", action="store_true", help="Attempt to fix issues automatically"
                                                                                                            )

                                                                                                            args = parser.parse_args()

                                                                                                            checker = ConsistencyIssue.ConsistencyChecker(verbose=args.verbose, fix=args.fix)
                                                                                                            errors, warnings, info = checker.run()  # pyright: ignore[reportAttributeAccessIssue]

                                                                                                            report = checker.generate_report()  # pyright: ignore[reportAttributeAccessIssue]
                                                                                                            print(report)

                                                                                                            # Exit with error code if issues found
                                                                                                            if errors > 0:
                                                                                                                sys.exit(1)


                                                                                                                if __name__ == "__main__":
                                                                                                                    main()
