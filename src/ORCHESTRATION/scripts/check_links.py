# coding: utf-8
"""
Check Links

Module for scripts functionality.

Functions:
    - main()
    - check_file()
    - check_all()
    - print_report()

Classes:
    - LinkChecker

Usage:
    >>> import check_links
    >>> check_links.main()

Author: Auto-generated
Date: 2026-02-10
"""

#!/usr/bin/env python3
"""
Documentation Link Checker
==========================

Validates all internal links in markdown documentation.

Usage:
    python scripts/check_links.py
    python scripts/check_links.py --fix
"""

import re
from pathlib import Path
from urllib.parse import urlparse
import sys
import argparse
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

BASE_DIR = Path(__file__).parent.parent
DOCS_DIR = BASE_DIR / "ml_platforms" / "docs"


class LinkChecker:
    """Check links in markdown files."""

    def __init__(self, fix: bool = False):
        self.fix = fix
        self.errors = []
        self.warnings = []

    def check_file(self, md_file: Path) -> list:
        """Check all links in a markdown file."""
        content = md_file.read_text(encoding="utf-8")
        lines = content.split("\n")

        file_errors = []

        # Check internal links
        for i, line in enumerate(lines, 1):
            # Find markdown links: [text](url)
            links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", line)

            for text, url in links:
                parsed = urlparse(url)

                # Skip external URLs
                if parsed.scheme in ["http", "https", "mailto"]:
                    continue

                # Skip anchor links
                if url.startswith("#"):
                    continue

                # Check relative paths
                if parsed.scheme == "" and not url.startswith("#"):
                    target = md_file.parent / url.split("#")[0]

                    if not target.exists():
                        file_errors.append(
                            {
                                "file": str(md_file.relative_to(BASE_DIR)),
                                "line": i,
                                "link": url,
                                "error": "File not found",
                            }
                        )
                    elif target.suffix == ".md":
                        # Check if anchor exists
                        anchor = url.split("#")[-1] if "#" in url else None
                        if anchor:
                            md_content = target.read_text(encoding="utf-8")
                            # Check for anchor (header)
                            anchor_pattern = rf"^#+\s+.*{anchor}"
                            if not re.search(
                                anchor_pattern, md_content, re.MULTILINE | re.IGNORECASE
                            ):
                                file_errors.append(
                                    {
                                        "file": str(md_file.relative_to(BASE_DIR)),
                                        "line": i,
                                        "link": url,
                                        "error": f"Anchor #{anchor} not found",
                                    }
                                )

        return file_errors

    def check_all(self) -> dict:
        """Check all markdown files."""
        all_errors = []

        for md_file in DOCS_DIR.rglob("*.md"):
            errors = self.check_file(md_file)
            all_errors.extend(errors)

        return {"errors": all_errors}

    def print_report(self, results: dict):
        """Print link check report."""
        errors = results["errors"]

        if not errors:
            print("\n[OK] All links are valid!")
            return

        print(f"\n[ERROR] Found {len(errors)} link errors:")
        print("-" * 60)

        for error in errors:
            print(f"  {error['file']}:{error['line']}")
            print(f"    Link: {error['link']}")
            print(f"    Error: {error['error']}")
            print()

        print("-" * 60)
        print(f"Total: {len(errors)} errors")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Check documentation links")
    parser.add_argument("--fix", action="store_true", help="Auto-fix errors")
    args = parser.parse_args()

    print("Documentation Link Checker")
    print("=" * 40)

    checker = LinkChecker(fix=args.fix)
    results = checker.check_all()
    checker.print_report(results)

    # Exit with error code if errors found
    sys.exit(1 if results["errors"] else 0)


if __name__ == "__main__":
    main()
